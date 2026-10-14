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
            social=SimpleNamespace(active_jobs=set(), pipeline=SimpleNamespace(model=None)))
        self.chat = SimpleNamespace(settings=SimpleNamespace(local_root=self.root), provider=None, qq_provider=None,
            turns={}, life=SimpleNamespace(generating=False, model=None), memory=self.memory)
        self.qq = SimpleNamespace(workers={}, client=None, store=SimpleNamespace(add_friend=Mock()))
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

    def test_basic_qq_only_requires_account_and_preserves_existing_tokens(self):
        from miko.adapters.qq.onebot import QqConfig
        config = {'account':'12345','http_url':'http://127.0.0.1:3000','ws_url':'ws://127.0.0.1:3001/',
                  'http_token':'http-test-secret','ws_token':'ws-test-secret'}
        write_json(self.root/'config/qq.local.json',config)
        self.qq.client = SimpleNamespace(config=QqConfig.model_validate(config))
        self.qq.receiver = SimpleNamespace(status=lambda:{'state':'connected'})
        with patch('miko.api.settings.OneBot.login',new=AsyncMock(return_value='12345')):
            response=self.client.put('/api/settings/qq',json={'account':'12345','managed':True})
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn('test-secret',response.text)
        self.assertEqual(json.loads((self.root/'config/qq.local.json').read_text())['http_token'],'http-test-secret')

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

    def test_friend_import_only_adds_records(self):
        config=SimpleNamespace(account='12345')
        self.qq.client=SimpleNamespace(config=config,call=AsyncMock(return_value=[{'user_id':67890,'nickname':'测试好友'}]))
        with patch('miko.api.settings.OneBot.login',new=AsyncMock(return_value='12345')):
            response=self.client.post('/api/settings/qq/friends')
        self.assertEqual(response.status_code,200)
        self.qq.store.add_friend.assert_called_once_with('12345','67890','测试好友')


class SettingsIntegrationTests(unittest.IsolatedAsyncioTestCase):
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
            finally:
                await chat.memory.close()
                await chat.life.close()
                await chat.close()


if __name__ == '__main__': unittest.main()
