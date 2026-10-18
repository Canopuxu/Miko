import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from miko.adapters.memory.service import Memory
from miko.api.chat import chat_router
from miko.api.life import life_router
from miko.api.memory import memory_router
from miko.api.profiles import profiles_router, ProfileInput
from miko.domain.conversation.chat import ChatService, Turn
from miko.domain.life import Life, LifeEntry
from miko.settings import Settings, save_chat_prompts
from miko.storage.chat import ChatStore
from miko.storage.qq import QqStore


class IdentityAliasTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'config').mkdir()
        (self.root / 'config/provider.local.json').write_text(json.dumps({
            'api_base':'http://127.0.0.1:1/v1','api_key':'test','model':'test'}))
        self.chat = ChatService(Settings(self.root, 7870))
        self.store = self.chat.store
        self.store.rename_character('Niko')
        self.store.rename_character('阿澄','owner')
        self.chat.life = Life(self.chat)
        self.chat.memory = Memory(self.chat)
        self.memory = self.chat.memory
        self.qq = SimpleNamespace(store=QqStore(self.store), workers={})
        self.qq.store.add_friend('123','456','朋友')
        self.original = 'Niko 和阿澄约好去河边散步'
        self.profile = self.chat.profiles.save(ProfileInput(subject='miko',kind='fact',
            category='Niko的习惯',title='Niko的散步安排',content=self.original).record())
        self.store.begin_turn('example','turn',self.original)
        self.store.finish('turn','Niko 说好','completed',None)
        save_chat_prompts(self.root,{'web':'Niko 和阿澄聊天','qq':'Niko 简短回复阿澄'})
        self.chat.life.save(date.today(),[LifeEntry(text=self.original)],0)
        with self.memory.social.store.connect() as db:
            self.memory.structured.commit(db,1,{'structured':[
                {'subject':'miko','peer':'','category':'日常','topic':'散步','content':self.original,
                 'expected_revision':0,'sources':[]}], 'days':[
                {'subject':'','subjects':['miko','owner'],'peer':'','day':date.today().isoformat(),
                 'text':self.original,'sources':[],'through':'test'}]})
        self.store.rename_character('小夏')
        self.store.rename_character('小橙','owner')
        self.store.rename_character('花梨')
        self.store.rename_character('阿柚','owner')
        self.current = '花梨 和阿柚约好去河边散步'
        app = FastAPI()
        app.include_router(profiles_router(self.chat,self.qq))
        app.include_router(memory_router(self.memory))
        app.include_router(life_router(self.chat.life))
        app.include_router(chat_router(self.chat))
        self.api = TestClient(app)

    async def asyncTearDown(self):
        self.api.close()
        await self.memory.close()
        self.temp.cleanup()

    async def test_material_views_export_and_original_chat(self):
        profiles = self.api.get('/api/profiles').json()['entries']
        self.assertEqual(profiles[0]['content'],self.current)
        self.assertEqual(profiles[0]['category'],'花梨的习惯')
        categories = self.api.get('/api/profiles/categories').json()['categories']
        self.assertIn('花梨的习惯',[c['name'] for c in categories])
        self.assertEqual(self.api.get('/api/profiles/portable/export').json()['entries'][0]['content'],self.current)
        self.assertEqual(self.api.get('/api/profiles/expression').json()['values']['web'],'花梨 和阿柚聊天')
        item = self.api.get('/api/memory/items').json()['items'][0]
        self.assertEqual(item['content'],self.current)
        self.assertEqual(self.api.get('/api/memory/items/'+item['id']).json()['content'],self.current)
        self.assertEqual(self.api.get('/api/memory/days').json()['days'][0]['text'],self.current)
        life = self.api.get('/api/life').json()
        self.assertEqual(life['record']['entries'][0]['text'],self.current)
        self.assertEqual(life['trajectory'][0]['text'],self.current)
        self.assertEqual(self.api.get('/api/conversations/example').json()['messages'][0]['text'],self.original)
        self.assertEqual(self.chat.profiles.get(self.profile['id'])['content'],self.original)
        self.assertEqual(self.chat.life.store.get(date.today()).entries[0]['text'],self.original)

    async def test_new_and_previous_names_share_local_and_semantic_documents(self):
        for name in ('Niko','小夏','花梨','Miko'):
            rows=self.api.get('/api/profiles',params={'q':name,'category':'花梨的习惯'}).json()['entries']
            self.assertEqual([r['id'] for r in rows],[self.profile['id']])
        for name in ('阿澄','小橙','阿柚','橙汁'):
            self.assertTrue(self.api.get('/api/memory/items',params={'q':name}).json()['items'])
        await self.memory.sync()
        documents=self.memory.documents()
        semantic=documents['profile:'+self.profile['id']]['content']
        self.assertIn('Miko',semantic)
        self.assertIn('橙汁',semantic)
        self.assertNotIn('Niko',semantic)
        results=[]
        for question in ('Niko和阿澄去河边散步','小夏和小橙去河边散步','花梨和阿柚去河边散步'):
            value=await self.memory.context(question)
            results.append([r['id'] for r in value.get('role_material',[])])
        self.assertIn(self.profile['id'],results[0])
        self.assertEqual(results[0],results[1])
        self.assertEqual(results[1],results[2])
        (self.root/'config/embedding.local.json').write_text('{"model":"test-embedding"}')
        self.memory.configure_index()
        class Embeddings:
            async def embed(self,texts):
                return [[1.,0.] for _ in texts],{}
        self.memory.index._embedding_pool._cache['embedding']=Embeddings()
        online=[]
        for question in ('Niko和阿澄去河边散步','花梨和阿柚去河边散步'):
            value=await self.memory.context(question)
            self.assertEqual(value['retrieval']['engine'],'memU Embedding + Tulpa FTS')
            online.append([r['id'] for r in value.get('role_material',[])])
        self.assertEqual(online[0],online[1])
        self.assertIn(self.profile['id'],online[0])
        reopened=ChatStore(self.store.path)
        self.assertEqual(reopened.identity().display(self.original),self.current)
        self.assertEqual(reopened.identity().display('niko 和阿澄去散步'),'花梨 和阿柚去散步')
        self.assertEqual(reopened.identity().display('Nikolette 的 introduces_miko，miko/owner'),'Nikolette 的 introduces_miko，miko/owner')

    async def test_model_payload_rebinds_names_without_rewriting_messages(self):
        captured=[]
        async def handler(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200,text='data: {"choices":[{"delta":{"content":"好"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        original_client=httpx.AsyncClient
        def client(**kwargs):
            return original_client(transport=httpx.MockTransport(handler),**kwargs)
        turn=Turn('capture','example',self.original,system_prompt=self.chat.named_prompt('你是 Niko，与阿澄聊天'))
        with patch('miko.domain.conversation.chat.httpx.AsyncClient',client):
            response=await self.chat.proxy(turn)
            async for _ in response.body_iterator:
                pass
        payload=captured[0]['messages']
        self.assertEqual(payload[0]['content'],'你是 花梨，与阿柚聊天')
        self.assertEqual(payload[-1]['content'],self.current)
        self.assertEqual(payload[1]['content'],self.current)
        self.assertEqual(self.store.messages('example')[0]['text'],self.original)

    async def test_life_generation_reads_old_material_using_current_names(self):
        captured=[]
        async def complete(system,message,**kwargs):
            captured.append((system,message))
            material=json.loads(message)
            return {'text':json.dumps({'entries':[{'text':'花梨和阿柚吃了晚饭'}]},ensure_ascii=False)}
        self.chat.complete_memory=complete
        await self.chat.life.generate(date.today())
        self.assertEqual(len(captured),1)
        self.assertIn(self.current,captured[0][1])
        self.assertNotIn('Niko',captured[0][1])
        self.assertNotIn('阿澄',captured[0][1])
        names=self.store.identity().material({'result':self.original,'done_when':self.original,'current_state':self.original})
        self.assertTrue(all(value==self.current for value in names.values()))
        self.assertEqual(self.store.messages('example')[0]['text'],self.original)

    async def test_import_uses_same_alias_history_and_evidence_stays_original(self):
        exported=self.api.get('/api/profiles/portable/export').json()
        exported['name']='小雨'
        self.assertEqual(self.api.post('/api/profiles/portable/import',json=exported).status_code,200)
        self.assertEqual(self.store.identity().display(self.original),'小雨 和阿柚约好去河边散步')
        value={'id':'miko','subject':'owner','text':self.original,
               'sources':[{'content':self.original}], 'history':[{'snapshot':self.original}]}
        view=self.store.identity().material(value)
        self.assertEqual(view['id'],'miko')
        self.assertEqual(view['subject'],'owner')
        self.assertEqual(view['text'],'小雨 和阿柚约好去河边散步')
        self.assertEqual(view['sources'][0]['content'],self.original)
        self.assertEqual(view['history'][0]['snapshot'],self.original)
