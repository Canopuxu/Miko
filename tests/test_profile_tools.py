import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from miko.api.profiles import profiles_router, ProfileInput, CharacterFile
from miko.domain.conversation.chat import ChatService
from miko.settings import Settings, ProviderConfig, chat_prompts
from miko.storage.chat import ChatStore
from miko.storage.qq import QqStore


class ProfileToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.settings = Settings(self.root, 7870)
        self.chat = ChatService(self.settings, ChatStore(self.root / 'chat.sqlite3'))
        self.qq = SimpleNamespace(store=QqStore(self.chat.store), workers={})
        self.qq.store.add_friend('123', '456', '朋友')
        app = FastAPI()
        app.include_router(profiles_router(self.chat, self.qq))
        self.api = TestClient(app)

    def tearDown(self):
        self.api.close()
        self.tmp.cleanup()

    def entry(self, **values):
        return self.chat.profiles.save(ProfileInput(subject='miko', kind='fact', title='兴趣',
            content='喜欢文学', **values).record())

    def test_expression_edit_preview_persistence_default_and_active_reply(self):
        initial = self.api.get('/api/profiles/expression').json()
        self.assertEqual(initial['values'], initial['defaults'])
        self.assertFalse((self.root / 'config/expression.local.json').exists())
        self.chat.store.rename_character('小米')
        self.chat.store.rename_character('小橙', 'owner')
        draft = dict(web='Miko 和橙汁聊天', qq='Miko 用简短词句和橙汁聊天')
        preview = self.api.post('/api/profiles/expression/preview', json=draft)
        self.assertEqual(preview.json()['effective']['qq'], '小米 用简短词句和小橙聊天')
        self.assertFalse((self.root / 'config/expression.local.json').exists())
        self.assertEqual(self.api.put('/api/profiles/expression', json=draft).status_code, 200)
        self.assertEqual(chat_prompts(self.root)[0], draft)
        self.assertEqual(self.chat.system_prompt, draft['web'])
        reopened = ChatService(self.settings, self.chat.store)
        self.assertEqual(reopened.system_prompt, draft['web'])
        self.chat.turns['active'] = object()
        self.assertEqual(self.api.put('/api/profiles/expression', json=initial['defaults']).status_code, 409)
        self.assertEqual(chat_prompts(self.root)[0], draft)
        self.chat.turns.clear()
        self.assertEqual(self.api.put('/api/profiles/expression', json=initial['defaults']).status_code, 200)
        self.assertEqual(chat_prompts(self.root)[0], initial['defaults'])

    def test_qq_generation_uses_saved_expression_and_replaced_names(self):
        draft = dict(web='Miko 和橙汁聊天', qq='本轮表达验证：Miko 和橙汁')
        self.api.put('/api/profiles/expression', json=draft)
        self.chat.store.rename_character('小米')
        self.chat.store.rename_character('小橙', 'owner')
        self.chat.provider = ProviderConfig(api_base='https://example.test/v1', api_key='test', model='test')
        self.chat.memory = SimpleNamespace(reranker=None,
            context=AsyncMock(side_effect=lambda query, key: self.chat.profiles.context(query, key)))
        records = [dict(author=dict(kind='contact', id='qq:456', name='朋友'), message_id=1,
                        text='嗨', created_at=1000000, segments=[])]
        result = SimpleNamespace(finish_reason='completed', final_response='{"bubbles":[]}')
        async def generate():
            turn = self.chat.begin_qq('qq:123:456', records)
            await turn.task
            self.assertEqual(turn.status, 'completed', turn.error)
        with patch('miko.domain.conversation.chat.DshRuntime') as runtime, \
                patch('miko.domain.conversation.chat.Session') as session:
            session.return_value.run.return_value = result
            asyncio.run(generate())
        self.assertIn('本轮表达验证：小米 和小橙', runtime.call_args.kwargs['system_prompt'])

    def test_export_import_and_backup_preserve_other_subjects_scopes_and_history(self):
        self.entry(category='兴趣与能力')
        scoped = self.entry(account='123', friend='456')
        owner = self.chat.profiles.save(ProfileInput(subject='owner', kind='fact', title='作息', content='九点起床').record())
        recent = self.chat.profiles.save(ProfileInput(subject='miko', kind='recent', title='近况', content='今天散步', as_of='2026-10-16').record())
        turn_id = 'kept-turn'
        self.chat.store.begin_turn('kept-conversation', turn_id, '保留的聊天记录')
        card = self.api.get('/api/profiles/portable/export').json()
        CharacterFile.model_validate(card)
        self.assertEqual(len(card['entries']), 1)
        self.assertNotIn('account', card['entries'][0])
        self.assertNotIn('api_key', json.dumps(card))
        card['name'] = '新角色'
        card['entries'][0]['content'] = '喜欢天文学'
        card['expression']['qq'] = 'Miko 和橙汁自然聊天'
        preview = self.api.post('/api/profiles/portable/preview', json=card)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(self.chat.store.character_name(), 'Miko')
        applied = self.api.post('/api/profiles/portable/import', json=card)
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(self.chat.store.character_name(), '新角色')
        self.assertEqual(self.chat.profiles.list('天文学')[0]['content'], '喜欢天文学')
        for entry in (scoped, owner, recent):
            self.assertIsNotNone(self.chat.profiles.get(entry['id']))
        self.assertEqual(self.chat.store.conversation('kept-conversation')['id'], 'kept-conversation')
        backup = self.api.get('/api/profiles/portable/backups/' + applied.json()['backup_id'])
        self.assertEqual(backup.status_code, 200)
        self.assertEqual(backup.json()['entries'][0]['content'], '喜欢文学')
        self.assertEqual(self.api.post('/api/profiles/portable/import', json=backup.json()).status_code, 200)
        self.assertEqual(self.chat.store.character_name(), 'Miko')
        self.assertEqual(self.chat.profiles.portable_entries('miko')[0]['content'], '喜欢文学')

    def test_export_includes_entries_beyond_ui_page_limit(self):
        for _ in range(205):
            self.entry()
        card = self.api.get('/api/profiles/portable/export').json()
        self.assertEqual(len(card['entries']), 205)

    def test_storage_failure_does_not_partially_apply_import(self):
        self.entry()
        original = self.api.get('/api/profiles/portable/export').json()
        changed = {**original, 'name': '不可写入', 'expression': {'web': '测试网页', 'qq': '测试表达'}}
        write = self.chat.profiles._write
        def fail_after_write(*args):
            write(*args)
            raise RuntimeError('模拟写入失败')
        with patch.object(self.chat.profiles, '_write', side_effect=fail_after_write), \
                TestClient(self.api.app, raise_server_exceptions=False) as client:
            self.assertEqual(client.post('/api/profiles/portable/import', json=changed).status_code, 500)
        self.assertEqual(self.api.get('/api/profiles/portable/export').json(), original)
