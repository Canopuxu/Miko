import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from miko.adapters.memory.service import Memory
from miko.domain.conversation.chat import ChatService
from miko.settings import Settings
from miko.storage.qq import QqStore


class MemoryMentionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'config').mkdir()
        (self.root/'config/memory.local.json').write_text('{"engine":"memobase"}')
        (self.root/'config/provider.local.json').write_text(json.dumps({
            'api_base':'http://127.0.0.1:1/v1','api_key':'test','model':'test'}))
        self.chat = ChatService(Settings(self.root,7870))
        self.chat.store.rename_character('阿澄','owner')
        self.chat.store.rename_character('阿柚','owner')
        self.qq = QqStore(self.chat.store)
        self.qq.add_friend('123','456','朋友甲')
        self.qq.add_friend('123','789','朋友乙')
        self.memory = Memory(self.chat)
        self.memory.social.loop = asyncio.get_running_loop()
        self.materials = []

    async def asyncTearDown(self):
        await self.memory.close()
        self.temp.cleanup()

    async def test_mentioned_owner_alias_is_available_and_commits_to_owner(self):
        key=('123','456')
        for mid,origin,text in [(1,'friend','阿澄说他以后周二固定去练游泳'),(2,'miko','知道了')]:
            self.qq.event(key,mid,text,time.time(),origin)
            self.qq.record(key,mid,'qq:456' if origin=='friend' else 'miko')
        self.memory.social.store.sync()
        sid=self.memory.social.listing()['scopes'][0]['id']
        async def complete(system,message,model=None,**kwargs):
            self.materials.append(message)
            return {'text':json.dumps({'facts':[{'subject':'owner','topic':'生活习惯','sub_topic':'游泳安排',
                'memo':'朋友转述：周二固定练游泳'}],'events':[]},ensure_ascii=False),'usage':{}}
        self.chat.complete_memory=complete
        result=await asyncio.to_thread(self.memory.social.consolidate,sid,None,True)
        self.assertEqual(result['status'],'updated')
        people=json.loads(self.materials[0].splitlines()[0].removeprefix('人物：'))
        self.assertEqual(people['owner'],'阿柚')
        self.assertIn('qq:456',people)
        self.assertNotIn('qq:789',people)
        self.assertEqual(self.memory.structured.items(subject='owner')[0]['content'],'朋友转述：周二固定练游泳')
        self.assertEqual(self.memory.structured.items(subject='qq:456'),[])
        self.assertEqual(len(self.memory.structured.detail(self.memory.structured.items()[0]['id'])['sources']),2)

    async def test_known_names_are_selected_without_inventing_unknown_or_ambiguous_people(self):
        names=self.chat.store.identity()
        people={'miko':'Miko','owner':'阿柚','qq:456':'小林','qq:789':'小林','qq:100':'Nikolette'}
        self.assertEqual(set(names.mentioned('橙汁、阿澄和阿柚都是同一个称呼',people)),{'owner'})
        self.assertEqual(names.mentioned('小林和未登记的小明聊天',people),[])
        self.assertEqual(names.mentioned('Nikolette在吗',people),['qq:100'])
