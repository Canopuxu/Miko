import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from miko.api.settings import settings_router
from miko.settings import ProviderConfig, write_json


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'config').mkdir()
        self.memory = SimpleNamespace(lock=asyncio.Lock(), configure_index=Mock(),
            social=SimpleNamespace(active_jobs=set(), configured=False, pipeline=SimpleNamespace(model=None)))
        self.chat = SimpleNamespace(settings=SimpleNamespace(local_root=self.root), provider=None, qq_provider=None,
            turns={}, life=SimpleNamespace(generating=False, model=None), memory=self.memory)
        self.qq = SimpleNamespace(workers={}, client=None, store=SimpleNamespace(add_friend=Mock(), friends=Mock(return_value=[])))
        app = FastAPI(); app.include_router(settings_router(self.chat, self.qq))
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close(); self.tmp.cleanup()

    def payload(self):
        return {'provider': {'api_base':'https://models.example/v1','api_key':'private-test-key','model':'chat-model'},
            'memory_model':'memory-model','life_model':'life-model','embedding_model':'embed-model'}

    def test_fresh_setup_save_and_secrets_never_returned(self):
        self.assertFalse(self.client.get('/api/settings').json()['configured'])
        self.assertEqual(self.client.put('/api/settings/models', json=self.payload()).status_code, 200)
        value = self.client.get('/api/settings')
        self.assertNotIn('private-test-key', value.text)
        self.assertTrue(value.json()['provider']['has_key'])
        self.assertTrue(value.json()['configured'])
        self.assertEqual(self.chat.provider.model, 'chat-model')
        self.assertEqual(self.chat.life.model, 'life-model')
        self.assertEqual(self.memory.social.pipeline.model, 'memory-model')
        self.assertTrue(self.memory.social.configured)
        self.memory.configure_index.assert_called_once()

    def test_blank_key_preserved_and_changed_host_requires_new_key(self):
        data=self.payload();self.client.put('/api/settings/models',json=data)
        data['provider']['api_key']=''
        self.assertEqual(self.client.put('/api/settings/models',json=data).status_code,200)
        self.assertEqual(self.chat.provider.api_key.get_secret_value(),'private-test-key')
        data['provider']['api_base']='https://another.example/v1'
        self.assertEqual(self.client.put('/api/settings/models',json=data).status_code,400)
        self.assertEqual(self.chat.provider.api_base,'https://models.example/v1')

    def test_active_reply_cannot_be_reconfigured(self):
        self.qq.workers['friend']=object()
        self.assertEqual(self.client.put('/api/settings/models',json=self.payload()).status_code,409)
        self.assertFalse((self.root/'config/provider.local.json').exists())

    def test_basic_model_save_shares_one_model_across_generation_tasks(self):
        data = self.payload()
        data['qq_provider'] = {'api_base':'https://qq.example/v1','api_key':'qq-test','model':'other'}
        data['use_shared_model'] = True
        self.assertEqual(self.client.put('/api/settings/models',json=data).status_code,200)
        self.assertIsNone(self.chat.qq_provider)
        self.assertIsNone(self.chat.life.model)
        self.assertIsNone(self.memory.social.pipeline.model)
        self.assertFalse((self.root/'config/qq-provider.local.json').exists())

    def test_independent_generation_connections_save_reload_and_reset(self):
        from miko.settings import Settings
        data=self.payload()
        for task in ('life','memory'):
            data[task+'_provider']={'api_base':f'https://{task}.example/v1','api_key':task+'-secret','model':task+'-custom'}
        response=self.client.put('/api/settings/models',json=data)
        self.assertEqual(response.status_code,200,response.text)
        settings=Settings(self.root,7870)
        self.assertEqual(settings.load_task_provider('life').model,'life-custom')
        self.assertEqual(self.chat.life.provider.api_base,'https://life.example/v1')
        self.assertEqual(self.memory.social.pipeline.provider.api_key.get_secret_value(),'memory-secret')
        self.assertEqual(self.memory.social.pipeline.model,'memory-custom')
        for task in ('life','memory'):
            data[task+'_provider']['api_key']=''
        self.assertEqual(self.client.put('/api/settings/models',json=data).status_code,200)
        public=self.client.get('/api/settings')
        self.assertNotIn('memory-secret',public.text)
        self.assertNotIn('life-secret',public.text)
        data['use_shared_model']=True
        self.assertEqual(self.client.put('/api/settings/models',json=data).status_code,200)
        self.assertIsNone(settings.load_task_provider('life'))
        self.assertIsNone(self.memory.social.pipeline.provider)
        self.assertIsNone(self.chat.life.provider)

    def test_model_catalog_uses_saved_key_and_filters_across_providers_without_saving(self):
        import httpx
        self.client.put('/api/settings/models',json=self.payload())
        rows=[{'id':name} for name in ('qwen3.7-plus','text-embedding-3-small','mistral-embed',
            'voyage-4-large','BAAI/bge-m3','BAAI/bge-reranker-v2-m3','intfloat/multilingual-e5-large-instruct',
            'cross-encoder/ms-marco-MiniLM-L6-v2')]
        rows.extend([{'id':'custom-vectors','type':'embedding'},{'id':'custom-ranks','capabilities':{'rerank':True}},
            {'id':'custom-chat','type':'llm'},{'id':'missing-data'}, {'id':123}])
        requests=[]
        def respond(request):
            requests.append(request)
            return httpx.Response(200,json={'data':rows})
        original=httpx.AsyncClient
        with patch('miko.api.settings.httpx.AsyncClient',side_effect=lambda **kwargs:original(**kwargs,transport=httpx.MockTransport(respond))):
            response=self.client.post('/api/settings/models/list',json={'api_base':'https://models.example/v1','api_key':'','model':''})
        self.assertEqual(response.status_code,200,response.text)
        classified={row['id']:row['kind'] for row in response.json()['models']}
        self.assertEqual(classified['BAAI/bge-m3'],'embedding')
        self.assertEqual(classified['BAAI/bge-reranker-v2-m3'],'rerank')
        self.assertEqual(classified['voyage-4-large'],'embedding')
        self.assertEqual(classified['custom-vectors'],'embedding')
        self.assertEqual(classified['custom-ranks'],'rerank')
        self.assertEqual(classified['cross-encoder/ms-marco-MiniLM-L6-v2'],'rerank')
        self.assertEqual(classified['qwen3.7-plus'],'chat')
        self.assertEqual(requests[0].url.path,'/v1/models')
        self.assertEqual(requests[0].headers['Authorization'],'Bearer private-test-key')
        self.assertNotIn('private-test-key',response.text)

    def test_catalog_failure_keeps_existing_configuration(self):
        import httpx
        self.client.put('/api/settings/models',json=self.payload())
        before=(self.root/'config/provider.local.json').read_bytes()
        original=httpx.AsyncClient
        with patch('miko.api.settings.httpx.AsyncClient',side_effect=lambda **kwargs:original(**kwargs,
                transport=httpx.MockTransport(lambda request:httpx.Response(404)))):
            response=self.client.post('/api/settings/models/list',json={'api_base':'https://models.example/v1','api_key':'','model':''})
        self.assertEqual(response.status_code,400)
        self.assertIn('直接填写',response.json()['detail'])
        self.assertEqual((self.root/'config/provider.local.json').read_bytes(),before)

    def test_shared_reranker_uses_updated_main_key_and_loads_without_extra_fields(self):
        from miko.settings import Settings
        data=self.payload()
        data.update(rerank_shared=True,rerank_enabled=True,
            rerank_provider={'api_base':'','api_key':'','model':'bge-reranker-v2-m3'})
        response=self.client.put('/api/settings/models',json=data)
        self.assertEqual(response.status_code,200,response.text)
        config=Settings(self.root,7870).load_rerank()
        self.assertEqual(config.api_base,'https://models.example/v1/rerank')
        self.assertEqual(config.model,'bge-reranker-v2-m3')
        self.assertEqual(config.format,'standard')
        data['provider']['api_key']='changed-main-key'
        self.assertEqual(self.client.put('/api/settings/models',json=data).status_code,200)
        self.assertEqual(Settings(self.root,7870).load_rerank().api_key.get_secret_value(),'changed-main-key')

    def test_basic_qq_only_requires_account_and_uses_native_credentials(self):
        from miko.adapters.qq.onebot import QqConfig
        config = {'account':'12345','http_url':'http://127.0.0.1:3000','ws_url':'ws://127.0.0.1:3001/',
                  'http_token':'http-test-secret','ws_token':'ws-test-secret'}
        write_json(self.root/'config/qq.local.json',config)
        write_json(self.root/'config/qq-client.local.json',{'account':'12345','token':'native-test-secret'})
        self.qq.client = SimpleNamespace(config=QqConfig.model_validate(config))
        self.qq.receiver = SimpleNamespace(status=lambda:{'state':'connected'})
        self.qq.close = AsyncMock()
        self.qq.start = Mock()
        self.qq.receive = AsyncMock()
        self.qq.source_changed = AsyncMock()
        with patch('miko.api.settings.OneBot.login',new=AsyncMock(return_value='12345')):
            response=self.client.put('/api/settings/qq',json={'account':'12345','managed':True})
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn('test-secret',response.text)
        saved = json.loads((self.root/'config/qq.local.json').read_text())
        self.assertEqual(saved['http_token'],'native-test-secret')
        self.assertEqual(saved['http_url'],'http://127.0.0.1:17971')

    def test_account_picker_and_first_connection_save_without_user_credentials(self):
        from miko.adapters.qq.onebot import QqConfig
        self.qq.close=AsyncMock();self.qq.start=Mock()
        self.qq.receive=AsyncMock();self.qq.source_changed=AsyncMock()
        row={'pid':42,'started_at':123,'account':'12345','nickname':'测试','state':'ready','version':'fixture'}
        with patch('miko.api.settings.ManagedQq.discover',new=AsyncMock(return_value=[row])):
            response=self.client.get('/api/settings/qq/accounts')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['clients'][0]['account'],'12345')
        connection=QqConfig(account='12345',http_url='http://127.0.0.1:45001',ws_url='ws://127.0.0.1:45001/',
                            http_token='generated-private-key',ws_token='generated-private-key',
                            miko_avatar_file='miko.png',owner_avatar_file='owner.png')
        with patch('miko.api.settings.ManagedQq.connect',new=AsyncMock(return_value=connection)) as prepare, \
                patch('miko.api.settings.OneBot.login',new=AsyncMock(return_value='12345')):
            response=self.client.put('/api/settings/qq',json={'managed':True,'account':'12345','client_pid':42})
        self.assertEqual(response.status_code,200,response.text)
        prepare.assert_awaited_once_with(42,'12345',{})
        self.qq.start.assert_called_once()
        public=self.client.get('/api/settings')
        self.assertNotIn('generated-private-key',public.text)
        self.assertEqual(public.json()['qq']['account'],'12345')
        with patch('miko.api.settings.ManagedQq.disconnect',new=AsyncMock()) as stop:
            response=self.client.post('/api/settings/qq/disconnect')
        self.assertEqual(response.status_code,200,response.text)
        stop.assert_awaited_once()
        self.assertIsNone(self.qq.client)
        retained=json.loads((self.root/'config/qq.local.json').read_text('utf-8'))
        self.assertFalse(retained['enabled'])
        self.assertEqual(retained['owner_avatar_file'],'owner.png')
        from miko.settings import Settings
        self.assertIsNone(Settings(self.root,7870).load_qq())
        self.assertEqual(self.client.get('/api/settings').json()['qq']['account'],'')

    def test_account_switch_requires_disconnect_before_preparing_bridge(self):
        self.qq.client=SimpleNamespace(config=SimpleNamespace(account='12345'))
        with patch('miko.api.settings.ManagedQq.connect',new=AsyncMock()) as connect:
            response=self.client.put('/api/settings/qq',json={'managed':True,'account':'99999','client_pid':42})
        self.assertEqual(response.status_code,409,response.text)
        self.assertIn('先断开',response.json()['detail'])
        connect.assert_not_awaited()

    def test_embedding_switch_preserves_old_index_and_other_settings(self):
        write_json(self.root/'config/life.local.json',{'enabled':False})
        write_json(self.root/'config/memory.local.json',{'engine':'memobase'})
        self.client.put('/api/settings/models',json=self.payload())
        first=json.loads((self.root/'config/embedding.local.json').read_text())['index_file']
        data=self.payload();data['embedding_model']='new-embedding'
        self.client.put('/api/settings/models',json=data)
        second=json.loads((self.root/'config/embedding.local.json').read_text())['index_file']
        self.assertNotEqual(first,second)
        self.assertFalse(json.loads((self.root/'config/life.local.json').read_text())['enabled'])
        self.assertEqual(json.loads((self.root/'config/memory.local.json').read_text())['engine'],'memobase')

    def test_cross_site_configuration_rejected(self):
        response=self.client.put('/api/settings/models',json=self.payload(),headers={'Origin':'https://unrelated.example'})
        self.assertEqual(response.status_code,403)

    def test_independent_search_services_keep_their_keys_and_indexes(self):
        data = self.payload()
        data['embedding_provider'] = {'api_base':'https://vectors.example/v1', 'api_key':'vector-secret', 'model':'vectors'}
        data['rerank_enabled'] = True
        data['rerank_provider'] = {'api_base':'https://ranks.example/v1/rerank', 'api_key':'rank-secret', 'model':'ranker', 'format':'standard'}
        response = self.client.put('/api/settings/models',json=data)
        self.assertEqual(response.status_code,200,response.text)
        first = json.loads((self.root/'config/embedding.local.json').read_text())
        self.assertEqual(first['api_base'],'https://vectors.example/v1')
        public = self.client.get('/api/settings')
        self.assertNotIn('vector-secret',public.text)
        self.assertNotIn('rank-secret',public.text)
        self.assertTrue(public.json()['embedding_provider']['has_key'])
        data['provider'].update(api_base='https://other-chat.example/v1',api_key='new-chat-secret')
        data['embedding_provider']['api_key']=''
        data['rerank_provider']['api_key']=''
        self.assertEqual(self.client.put('/api/settings/models',json=data).status_code,200)
        second = json.loads((self.root/'config/embedding.local.json').read_text())
        self.assertEqual(second['api_key'],'vector-secret')
        self.assertEqual(second['index_file'],first['index_file'])
        data['embedding_provider'].update(api_base='https://new-vectors.example/v1',api_key='new-vector-secret')
        self.assertEqual(self.client.put('/api/settings/models',json=data).status_code,200)
        third = json.loads((self.root/'config/embedding.local.json').read_text())
        self.assertNotEqual(third['index_file'],first['index_file'])
        data.update(rerank_enabled=False,embedding_provider=None,embedding_model='')
        self.assertEqual(self.client.put('/api/settings/models',json=data).status_code,200)
        self.assertFalse(json.loads((self.root/'config/rerank.local.json').read_text())['enabled'])

    def test_feature_connection_tests_use_the_correct_endpoints_without_saving(self):
        import httpx
        requests=[]
        responses=[{'data':[{'embedding':[.1,.2]}]}, {'results':[{'index':0,'relevance_score':.9}]}]
        def respond(request):
            requests.append(request)
            return httpx.Response(200,json=responses.pop(0))
        original=httpx.AsyncClient
        def client(**kwargs):
            return original(**kwargs,transport=httpx.MockTransport(respond))
        with patch('miko.api.settings.httpx.AsyncClient',side_effect=client):
            embedding={'api_base':'https://vectors.example/v1','api_key':'vector-key','model':'embedding-test'}
            self.assertEqual(self.client.post('/api/settings/models/test?target=embedding',json=embedding).status_code,200)
            rerank={'api_base':'https://ranks.example/v1/rerank','api_key':'rank-key','model':'rank-test'}
            result=self.client.post('/api/settings/models/test?target=rerank',json=rerank)
            self.assertEqual(result.status_code,200)
            self.assertIn('排序结果',result.json()['message'])
        self.assertEqual(requests[0].url.path,'/v1/embeddings')
        self.assertEqual(json.loads(requests[0].content)['model'],'embedding-test')
        self.assertEqual(requests[1].url.path,'/v1/rerank')
        self.assertEqual(json.loads(requests[1].content)['model'],'rank-test')
        self.assertFalse((self.root/'config/embedding.local.json').exists())

    def test_bailian_format_is_inferred_without_a_service_type(self):
        import httpx
        requests=[]
        def respond(request):
            requests.append(request)
            return httpx.Response(200,json={'output':{'results':[{'index':0,'relevance_score':.9}]}})
        original=httpx.AsyncClient
        with patch('miko.api.settings.httpx.AsyncClient',side_effect=lambda **kwargs:original(**kwargs,transport=httpx.MockTransport(respond))):
            body={'api_base':'https://ranks.example/api/v1/services/rerank/text-rerank/text-rerank','api_key':'key','model':'chosen'}
            response=self.client.post('/api/settings/models/test?target=rerank',json=body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('input',json.loads(requests[0].content))

    def test_data_folder_opening_is_limited_to_known_locations(self):
        with patch('miko.api.settings.os.startfile') as open_file:
            self.assertEqual(self.client.post('/api/settings/open-folder',json={'folder':'logs'}).status_code,200)
            open_file.assert_called_once_with(self.root/'logs')
            self.assertEqual(self.client.post('/api/settings/open-folder',json={'folder':'../config'}).status_code,422)
        self.assertEqual(self.client.get('/api/settings/application').status_code,200)

    def test_friend_manager_reads_without_importing_and_adds_only_selected(self):
        config=SimpleNamespace(account='12345')
        self.qq.client=SimpleNamespace(config=config,call=AsyncMock(return_value=[
            {'user_id':67890,'nickname':'测试好友','remark':'备注'},
            {'user_id':98765,'nickname':'另一位'},
            {'user_id':11111,'nickname':'已有好友'}]))
        self.qq.store.friends.return_value=[{'account':'12345','friend':'11111'}]
        with patch('miko.api.settings.OneBot.login',new=AsyncMock(return_value='12345')):
            listing=self.client.get('/api/settings/qq/friends')
            self.assertEqual(listing.status_code,200)
            self.assertEqual(listing.json()['friends'][0],{'friend':'67890','nickname':'测试好友','remark':'备注','added':False})
            self.assertTrue(listing.json()['friends'][2]['added'])
            self.qq.store.add_friend.assert_not_called()
            self.assertEqual(self.client.post('/api/settings/qq/friends').status_code,422)
            self.assertEqual(self.client.post('/api/settings/qq/friends',json={'friend_ids':['67890','unknown']}).status_code,400)
            self.qq.store.add_friend.assert_not_called()
            response=self.client.post('/api/settings/qq/friends',json={'friend_ids':['67890','67890','11111']})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['added'],1)
        self.qq.store.add_friend.assert_called_once_with('12345','67890','备注')


class SettingsIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_qq_model_keeps_the_current_context_pipeline(self):
        from miko.settings import Settings
        from miko.domain.conversation.chat import ChatService
        with TemporaryDirectory() as folder:
            root=Path(folder); (root/'config').mkdir()
            write_json(root/'config/provider.local.json',{'api_base':'https://chat.example/v1','api_key':'test','model':'shared'})
            chat=ChatService(Settings(root,7874))
            chat.run=AsyncMock()
            records=[{'author':{'kind':'contact','name':'好友'},'message_id':'1','text':'今天怎么样'}]
            turn=chat.begin_qq('qq:12345:67890',records)
            await turn.task
            self.assertIsNone(chat.qq_provider)
            self.assertEqual(turn.qq_records,records)
            self.assertIn('最近对话：',turn.context[0]['content'])
            self.assertEqual(chat.provider_for(turn).model,'shared')
            await chat.close()

    async def test_parallel_generation_requests_use_their_own_connections(self):
        import httpx
        from miko.settings import Settings
        from miko.domain.conversation.chat import ChatService
        with TemporaryDirectory() as folder:
            root=Path(folder); (root/'config').mkdir()
            write_json(root/'config/provider.local.json',{'api_base':'https://chat.example/v1','api_key':'chat-secret','model':'chat'})
            chat=ChatService(Settings(root,7874))
            requests=[]
            def respond(request):
                requests.append(request)
                return httpx.Response(200,text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            original=httpx.AsyncClient
            async def run(turn):
                try:
                    response=await chat.proxy(turn)
                    async for _ in response.body_iterator: pass
                    turn.status='completed'
                finally:
                    chat.turns.pop(turn.id,None)
            chat.run=run
            life=ProviderConfig(api_base='https://life.example/v1',api_key='life-secret',model='life')
            memory=ProviderConfig(api_base='https://memory.example/v1',api_key='memory-secret',model='memory')
            with patch('miko.domain.conversation.chat.httpx.AsyncClient',side_effect=lambda **kwargs:original(**kwargs,transport=httpx.MockTransport(respond))):
                results=await asyncio.gather(chat.complete_memory('system','life',provider=life),chat.complete_memory('system','memory',provider=memory))
            self.assertEqual([r['text'] for r in results],['ok','ok'])
            sent={r.url.host:(json.loads(r.content)['model'],r.headers['Authorization']) for r in requests}
            self.assertEqual(sent,{'life.example':('life','Bearer life-secret'),'memory.example':('memory','Bearer memory-secret')})
            self.assertEqual(chat.provider.model,'chat')
            await chat.close()

    async def test_real_runtime_reloads_provider_and_embedding_without_losing_memory(self):
        import httpx
        from miko.settings import Settings
        from miko.domain.conversation.chat import ChatService
        from miko.domain.life import Life
        from miko.adapters.memory.service import Memory
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'config').mkdir()
            write_json(root/'config/memory.local.json', {'engine':'memobase'})
            chat = ChatService(Settings(root, 7874))
            chat.life = Life(chat)
            chat.memory = Memory(chat)
            qq = SimpleNamespace(workers={})
            app = FastAPI(); app.include_router(settings_router(chat, qq))
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://testserver') as client:
                    data={'provider':{'api_base':'https://models.example/v1','api_key':'test','model':'chat'},'embedding_model':'embedding-a','memory_model':'memory'}
                    response=await client.put('/api/settings/models',json=data)
                    self.assertEqual(response.status_code,200,response.text)
                    self.assertEqual(chat.memory.model,'embedding-a')
                    self.assertIsNotNone(chat.memory.index)
                    first=chat.memory.index
                    data['embedding_model']='embedding-b'
                    response=await client.put('/api/settings/models',json=data)
                    self.assertEqual(response.status_code,200,response.text)
                    self.assertIsNot(chat.memory.index,first)
                    self.assertEqual(chat.memory.model,'embedding-b')
                    self.assertEqual(chat.memory.social.pipeline.model,'memory')
                    self.assertTrue(chat.settings.database_path.exists())
                    data['embedding_provider']={'api_base':'https://vectors.example/v1','api_key':'independent-vector-key','model':'vectors'}
                    data['rerank_enabled']=True
                    data['rerank_provider']={'api_base':'https://ranks.example/v1/rerank','api_key':'independent-rank-key','model':'ranks','format':'standard'}
                    response=await client.put('/api/settings/models',json=data)
                    self.assertEqual(response.status_code,200,response.text)
                    profile=chat.memory.index.embedding_profiles['default']
                    self.assertEqual(profile.base_url,'https://vectors.example/v1')
                    self.assertEqual(profile.api_key,'independent-vector-key')
                    self.assertEqual(chat.memory.reranker.api_base,'https://ranks.example/v1/rerank')
                    self.assertEqual(chat.memory.reranker.model,'ranks')
            finally:
                await chat.memory.close()
                await chat.life.close()
                await chat.close()


if __name__ == '__main__': unittest.main()
