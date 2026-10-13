import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from miko.adapters.memory.service import Memory
from miko.domain.conversation.chat import ChatService
from miko.settings import Settings


class StructuredTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.local=Path(self.tmp.name)
        (self.local/'config').mkdir()
        (self.local/'config/memory.local.json').write_text('{"engine":"memobase"}',encoding='utf-8')
        (self.local/'config/provider.local.json').write_text(json.dumps({
            'api_base':'http://127.0.0.1:1/v1','api_key':'test','model':'test'}),encoding='utf-8')
        self.chat=ChatService(Settings(self.local,7870))
        self.memory=Memory(self.chat)
        self.social=self.memory.social
        self.social.loop=asyncio.get_running_loop()
        self.calls=[]

        async def complete(system,message,model=None):
            self.calls.append(message)
            if '### 输入对话' in message:
                if 'Miko(user)' in message:
                    text='- Miko在练键盘。[提及于 2026/10/13] // info'
                else:
                    text=('- 主人在练贝斯。' if '改练贝斯' in message else '- 主人在练吉他。')+'[提及于 2026/10/13] // info\n- 主人今天练了半小时。[提及于 2026/10/13] // event'
            elif '#### 备忘录' in message:
                text='- 兴趣::乐器::'+('键盘' if 'Miko在练键盘' in message else '贝斯' if '主人在练贝斯' in message else '吉他')
            else:
                text='1. UPDATE::'+('贝斯' if '贝斯' in message else '键盘')
            return {'text':text,'usage':{}}
        self.chat.complete_memory=complete

    async def asyncTearDown(self):
        await self.memory.close()
        self.tmp.cleanup()

    def add_turn(self,text):
        self.chat.store.begin_turn('example','turn-'+str(len(self.calls))+text,text)
        self.chat.store.finish('turn-'+str(len(self.calls))+text,'我在练键盘','completed',None)
        self.social.store.sync()
        return self.social.listing()['scopes'][0]['id']

    async def test_update_recall_manual_restore_and_source_recall(self):
        sid=self.add_turn('我在练吉他')
        result=await asyncio.to_thread(self.social.consolidate,sid,None,True)
        self.assertEqual(result['status'],'updated')
        owner=self.memory.structured.items(subject='owner')[0]
        miko=self.memory.structured.items(subject='miko')[0]
        self.assertEqual((owner['content'],miko['content']),('吉他','键盘'))
        self.assertTrue(self.memory.structured.days(subject='owner'))
        self.add_turn('我暂停吉他，现在改练贝斯')
        await asyncio.to_thread(self.social.consolidate,sid,None,True)
        row=self.memory.structured.items(subject='owner')[0]
        self.assertEqual(row['id'],owner['id'])
        self.assertEqual(row['content'],'贝斯')
        self.assertEqual((await self.memory.context('贝斯'))['people_memory'][0]['content'],'贝斯')
        edited=self.memory.structured.edit(row['id'],'主要练贝斯，吉他偶尔练',row['revision'])
        self.add_turn('我暂停吉他，现在改练贝斯')
        await asyncio.to_thread(self.social.consolidate,sid,None,True)
        fresh=self.memory.structured.detail(row['id'])
        self.assertEqual(fresh['content'],edited['content'])
        old=next(h for h in fresh['history'] if json.loads(h['snapshot'])['automatic']=='吉他')
        restored=self.memory.structured.restore(row['id'],old['id'],fresh['revision'])
        self.assertEqual(restored['content'],'吉他')
        self.memory.structured.edit(row['id'],'',restored['revision'])
        with self.social.store.connect() as db:
            source=db.execute('SELECT message_id FROM memory_sources WHERE item_id=? LIMIT 1',(row['id'],)).fetchone()[0]
            db.execute('DELETE FROM messages WHERE id=?',(source,))
        self.assertFalse(self.memory.structured.items('贝斯',subject='owner',active=True))

    async def test_batch_invalidated_by_manual_edit_and_feedback_withdrawal(self):
        sid=self.add_turn('我在练吉他')
        await asyncio.to_thread(self.social.consolidate,sid,None,True)
        owner=self.memory.structured.items(subject='owner')[0]
        self.add_turn('我暂停吉他，现在改练贝斯')
        captured=self.chat.complete_memory
        touched=False
        async def concurrent_edit(system,message,model=None):
            nonlocal touched
            if not touched:
                touched=True
                self.memory.structured.edit(owner['id'],'用户确认仍练吉他',owner['revision'])
            return await captured(system,message,model)
        self.chat.complete_memory=concurrent_edit
        result=await asyncio.to_thread(self.social.consolidate,sid,None,True)
        self.assertEqual(result['status'],'superseded')
        self.assertEqual(self.memory.structured.detail(owner['id'])['content'],'用户确认仍练吉他')
        reply=self.chat.store.messages('example')[-1]
        note=self.memory.structured.annotate(reply['id'],'语气稍微轻松一些','global',0)
        self.assertTrue((await self.memory.context('你好'))['owner_feedback'])
        self.memory.structured.annotate(reply['id'],note['note'],'global',note['revision'],False)
        self.assertFalse((await self.memory.context('你好'))['owner_feedback'])
        reviewed=self.memory.structured.review()['replies']
        self.assertTrue(reviewed)
        self.assertEqual(reviewed[0]['conversation_id'],'example')
        self.assertEqual(reviewed[0]['source'],'web')
        self.assertIn('贝斯',reviewed[0]['context'][-1]['text'])
        self.assertLessEqual(len(reviewed[0]['context']),2)

    async def test_changed_subtopic_updates_named_item_and_category_history(self):
        import ast
        original=self.chat.complete_memory
        async def classify(system,message,model=None):
            answer=await original(system,message,model)
            if '#### 备忘录' in message and '主人在练吉他' in message:
                answer['text']='- 兴趣::吉他::正在练吉他'
            elif '#### 备忘录' in message and '主人在练贝斯' in message:
                answer['text']='- 兴趣::乐器::吉他暂时停练，现在主要练贝斯'
            elif message.lstrip().startswith('[{'):
                changes=ast.literal_eval(message.strip())
                answer['text']='\n'.join(str(c['memo_id'])+'. UPDATE::'+c['new_info'] for c in changes)
            return answer
        self.chat.complete_memory=classify
        sid=self.add_turn('我在练吉他')
        await asyncio.to_thread(self.social.consolidate,sid,None,True)
        self.add_turn('我暂停吉他，现在改练贝斯')
        await asyncio.to_thread(self.social.consolidate,sid,None,True)
        rows=self.memory.structured.items(subject='owner',category='兴趣',active=True)
        self.assertEqual(len(rows),2)
        self.assertTrue(all('停练' in r['content'] for r in rows))
        revisions={r['id']:r['revision'] for r in rows}
        self.memory.structured.organize('owner','','兴趣',[{'sub_topic':'乐器','memo':'主要练贝斯，吉他暂时停练'}],revisions)
        active=self.memory.structured.items(subject='owner',category='兴趣',active=True)
        self.assertEqual(len(active),1)
        self.assertTrue(self.memory.structured.detail(rows[0]['id'])['history'])


if __name__=='__main__':
    unittest.main()
