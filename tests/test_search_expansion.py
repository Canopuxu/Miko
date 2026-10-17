import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from miko.adapters.memory.service import Memory
from miko.api.profiles import ProfileInput
from miko.domain.conversation.chat import ChatService
from miko.domain.conversation.context_selection import render,select
from miko.settings import Settings


class SearchExpansionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'config').mkdir()
        (self.root/'config/provider.local.json').write_text(json.dumps({
            'api_base':'https://example.test/v1','api_key':'test','model':'chat'}),encoding='utf-8')
        self.chat=ChatService(Settings(self.root,7899));self.memory=Memory(self.chat);self.chat.memory=self.memory
        self.record=self.chat.profiles.save(ProfileInput(subject='miko',kind='fact',category='职业',
            title='主业',content='绘本插画师').record())
        async def generate(system,message,**kwargs):
            return {'text':json.dumps({'items':[{'id':r['id'],'expressions':['稿费够饭钱吗','靠画画养活自己']}
                for r in json.loads(message)['items']]},ensure_ascii=False)}
        self.chat.complete_memory=AsyncMock(side_effect=generate)
        await self.memory.sync()

    async def asyncTearDown(self):
        await self.memory.close();self.tmp.cleanup()

    async def test_persistent_local_recall_only_exposes_original_and_edits_invalidate(self):
        self.assertFalse((await self.memory.context('稿费够饭钱吗'))['role_material'])
        self.assertTrue(await self.memory.expansion.process())
        self.assertEqual(self.memory.expansion.status()['ready'],1)
        ctx=await self.memory.context('稿费够饭钱吗',raw=True)
        core,rows,_=await select(None,'稿费够饭钱吗',ctx,local=self.memory.local)
        self.assertEqual([r['source']['content'] for r in rows],['绘本插画师'])
        self.assertNotIn('稿费',render(ctx,core,rows))
        self.assertEqual(self.chat.complete_memory.await_count,1)
        await self.memory.close();self.memory=Memory(self.chat);self.chat.memory=self.memory
        self.assertEqual((await self.memory.context('稿费够饭钱吗'))['role_material'][0]['content'],'绘本插画师')
        self.assertEqual(self.chat.complete_memory.await_count,1)
        self.chat.profiles.save(ProfileInput(subject='miko',kind='fact',category='职业',title='主业',content='已转行做翻译').record(),self.record['id'])
        self.assertFalse((await self.memory.context('稿费够饭钱吗'))['role_material'])
        self.assertEqual(self.memory.expansion.status()['pending'],1)
        self.chat.profiles.delete(self.record['id']);await self.memory.sync()
        self.assertEqual(self.memory.expansion.status()['total'],0)

    async def test_in_flight_old_result_is_discarded_after_edit(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def slow(*args,**kwargs):
            entered.set();await release.wait()
            return {'text':'{"items":[{"id":0,"expressions":["稿费够饭钱吗"]}]}'}
        self.chat.complete_memory.side_effect=slow
        task=asyncio.create_task(self.memory.expansion.process());await entered.wait()
        self.chat.profiles.save(ProfileInput(subject='miko',kind='fact',category='职业',title='主业',content='已转行做翻译').record(),self.record['id'])
        release.set();await task
        self.assertEqual(self.memory.expansion.status()['ready'],0)
        self.assertFalse((await self.memory.context('稿费够饭钱吗'))['role_material'])

    async def test_isolated_word_in_generated_expression_does_not_inject_a_fact(self):
        self.chat.complete_memory.side_effect=None
        self.chat.complete_memory.return_value={'text':'{"items":[{"id":0,"expressions":["平时喜欢做什么东西"]}]}'}
        await self.memory.expansion.process()
        self.assertFalse((await self.memory.context('睡不着，想翻点东西，你都看啥'))['role_material'])

    async def test_failure_pause_retry_and_empty_result_do_not_break_base(self):
        self.chat.complete_memory.side_effect=RuntimeError('offline')
        self.assertFalse(await self.memory.expansion.process())
        self.assertEqual(self.memory.expansion.status()['failed'],1)
        self.assertTrue((await self.memory.context('你是绘本插画师吗'))['role_material'])
        self.memory.expansion.control(False);self.memory.expansion.retry()
        self.assertFalse(await self.memory.expansion.process())
        self.assertEqual(self.chat.complete_memory.await_count,1)
        self.memory.expansion.control(True)
        self.chat.complete_memory.side_effect=None
        self.chat.complete_memory.return_value={'text':'{"items":[{"id":0,"expressions":[]}]}'}
        self.assertTrue(await self.memory.expansion.process())
        self.assertFalse(await self.memory.expansion.process())
        self.assertEqual(self.memory.expansion.status()['ready'],1)
