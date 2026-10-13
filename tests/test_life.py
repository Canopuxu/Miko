import asyncio
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from miko.api.life import life_router
from miko.api.profiles import ProfileInput
from miko.domain.conversation.chat import ChatService, reply_context
from miko.domain.life import Life
from miko.settings import Settings
from miko.adapters.memory.service import Memory


class SimulatedDay(date):
    current = date(2026, 10, 6)

    @classmethod
    def today(cls):
        return cls.current


class Embeddings:
    async def embed(self, texts):
        return [[1., 0.] if '摄影' in text or '相机' in text else [0., 1.] for text in texts], {}


class LifeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.local = Path(self.tmp.name)
        (self.local / 'config').mkdir()
        (self.local / 'config/provider.local.json').write_text(json.dumps({
            'api_base': 'http://127.0.0.1:1/v1', 'api_key': 'test', 'model': 'test'}), encoding='utf-8')
        (self.local / 'config/embedding.local.json').write_text('{"model":"test"}', encoding='utf-8')
        self.chat = ChatService(Settings(self.local, 7870))
        self.chat.life = self.life = Life(self.chat)
        self.memory = Memory(self.chat)
        self.memory.index._embedding_pool._cache['embedding'] = Embeddings()
        self.chat.profiles.save(ProfileInput(subject='miko', kind='overview', title='Miko',
                                            content='喜欢平面设计和键盘').record())
        self.chat.profiles.save(ProfileInput(subject='owner', kind='recent', title='近况',
                                            content='最近在学习前端配色', as_of='2026-10-06').record())

    async def asyncTearDown(self):
        await self.life.close()
        await self.chat.close()
        await self.memory.close()
        self.tmp.cleanup()

    async def test_week_continuity_persistence_retrieval_and_manual_api(self):
        inputs = []

        async def generate(system, message, **kwargs):
            material = json.loads(message)
            inputs.append(material)
            if len(inputs) > 1:
                self.assertIn('摄影', json.dumps(material['生活条目'], ensure_ascii=False))
            self.assertIn('前端配色', json.dumps(material['role_material'], ensure_ascii=False))
            return {'text': json.dumps({'entries': [{'topic': '摄影', 'text': '开始学摄影' if len(inputs) == 1 else '练构图'}]}, ensure_ascii=False)}

        self.chat.complete_memory = generate
        with patch('miko.domain.life.date', SimulatedDay):
            for day in range(6, 13):
                SimulatedDay.current = date(2026, 10, day)
                await self.life.generate()
            self.assertEqual(len(self.life.store.all()), 7)
            self.assertEqual(self.life.context('摄影')[0]['day'], '2026-10-12')
        restarted = Life(self.chat)
        self.assertEqual(restarted.records(), self.life.records())
        context = await self.memory.context('摄影')
        self.assertEqual(context['life_entries'][0]['text'], '练构图')
        compact = reply_context(context)
        self.assertNotIn('revision', json.dumps(compact['life_entries']))
        self.assertIn('life:2026-10-12', self.memory.documents())
        # A related question with no matching keyword recalls an earlier day.
        semantic = await self.memory.context('相机')
        self.assertEqual(semantic['retrieval']['engine'], 'memU Embedding + Tulpa FTS')
        self.assertTrue(any(r['day'] == '2026-10-06' for r in semantic['life_entries']))
        app = FastAPI()
        app.include_router(life_router(self.life))
        with TestClient(app) as client:
            row = client.get('/api/life?day=2026-10-12').json()['record']
            changed = client.put('/api/life', json={'day': '2026-10-12', 'revision': row['revision'],
                'entries': [{'topic': '摄影', 'text': '练光影；构图暂停'}]})
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertIn('光影', self.life.context('摄影')[0]['text'])
            self.assertEqual(client.put('/api/life/control', json={'enabled': False}).status_code, 200)
            self.assertFalse(Life(self.chat).enabled)
            self.assertTrue(self.life.context('摄影'))
            self.assertEqual(client.get('/api/life?day=2026-10-06').json()['record']['entries'][0]['text'], '开始学摄影')

    async def test_failed_or_superseded_generation_preserves_owner_edits(self):
        day = date.today()
        self.life.save(day, [{'topic': '键盘', 'text': '练和弦'}], 0)
        async def fail(*args, **kwargs):
            raise ValueError('模拟生成失败')
        self.chat.complete_memory = fail
        with self.assertRaises(ValueError):
            await self.life.generate()
        self.assertEqual(self.life.store.get(day).entries[0]['text'], '练和弦')
        async def concurrent(*args, **kwargs):
            self.life.save(day, [{'topic': '键盘', 'text': '号主修正：今天休息'}], 1)
            return {'text': '{"entries":[{"topic":"键盘","text":"练新曲"}]}'}
        self.chat.complete_memory = concurrent
        with self.assertRaises(ValueError):
            await self.life.generate()
        self.assertEqual(Life(self.chat).store.get(day).entries[0]['text'], '号主修正：今天休息')

    async def test_background_survives_page_close_and_pause_stops_new_days(self):
        calls = []
        async def complete(*args, **kwargs):
            calls.append(1)
            return {'text': '{"entries":[{"topic":"设计","text":"练配色"}]}'}
        self.chat.complete_memory = complete
        self.life.start()
        for _ in range(30):
            if self.life.store.has(date.today()):
                break
            await asyncio.sleep(.01)
        self.assertEqual(len(calls), 1)
        await self.life.close()
        self.life.toggle(False)
        self.life.start()
        await asyncio.sleep(.03)
        self.assertEqual(len(calls), 1)
