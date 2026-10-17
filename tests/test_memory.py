import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest

from miko.adapters.memory.service import Memory
from miko.api.profiles import ProfileInput
from miko.domain.conversation.chat import ChatService
from miko.settings import Settings
from miko.storage.qq import QqStore


class Embeddings:
    async def embed(self, texts):
        return [[1., 0.] if '体恤' in t or '服装' in t else [0., 1.] for t in texts], {}


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.local = Path(self.tmp.name)
        (self.local / 'config').mkdir()
        (self.local / 'config/provider.local.json').write_text(json.dumps({
            'api_base': 'http://127.0.0.1:1/v1', 'api_key': 'test', 'model': 'test'}), encoding='utf-8')
        (self.local / 'config/embedding.local.json').write_text('{"model":"test"}', encoding='utf-8')
        self.chat = ChatService(Settings(self.local, 7870))
        self.qq = QqStore(self.chat.store)
        self.qq.add_friend('123', '456', '甲')
        self.qq.add_friend('123', '789', '乙')
        self.memory = Memory(self.chat)
        self.memory.index._embedding_pool._cache['embedding'] = Embeddings()

    async def asyncTearDown(self):
        await self.memory.close()
        self.tmp.cleanup()

    async def test_memu_retrieval_scope_saved_profiles_and_restart(self):
        first = self.chat.profiles.save(ProfileInput(subject='miko', kind='fact', title='日常穿着',
            content='白色体恤，红白格子长衫').record())
        second = self.chat.profiles.save(ProfileInput(subject='owner', kind='fact', title='乙的资料',
            content='白色体恤', account='123', friend='789').record())
        self.assertEqual(self.chat.profiles.context('服装')['role_material'], [])
        result = await self.memory.context('服装', ('123', '456'))
        self.assertEqual(result['retrieval']['engine'], 'memU Embedding + Tulpa FTS')
        self.assertEqual([r['id'] for r in result['role_material']], [first['id']])
        self.assertNotIn(second['id'], [r['id'] for r in result['role_material']])
        self.memory.index.database.close()
        self.memory = Memory(self.chat)
        self.memory.index._embedding_pool._cache['embedding'] = Embeddings()
        self.assertEqual(len((await self.memory.context('服装', ('123','456')))['role_material']), 1)
        body = ProfileInput(subject='miko', kind='fact', title='日常穿着', content='白色体恤', enabled=False).record()
        self.chat.profiles.save(body, first['id'])
        self.assertEqual([r['id'] for r in (await self.memory.context('服装', ('123','456')))['role_material']], [first['id']])
        self.chat.profiles.delete(first['id'])
        self.assertEqual((await self.memory.context('服装', ('123','456')))['role_material'], [])

    async def test_tulpa_authors_batches_corrections_evidence_and_recall(self):
        stamp = time.time() + .01
        for i in range(100):
            author = f'qq:456' if i % 2 == 0 else 'owner'
            origin = 'friend' if i % 2 == 0 else 'manual'
            self.qq.event(('123','456'), i + 1, '一起跑步' if i % 2 == 0 else '周末去跑步', stamp + i, origin)
            self.qq.record(('123','456'), i + 1, author)
        self.qq.event(('123','456'), 101, '我在练键盘', stamp + 101, 'miko')
        self.qq.record(('123','456'), 101, 'miko')
        self.memory.social.store.sync()
        scopes = {r['conversation_id'].split('|')[0]: r for r in self.memory.social.listing()['scopes']}
        self.assertEqual(scopes['owner']['total'], 100)
        self.assertEqual(scopes['miko']['total'], 51)
        sid = scopes['owner']['id']
        self.memory.social.edit(sid, '主人与甲常一起跑步', 0)
        captured = []

        def summarize(payload):
            captured.append(payload)
            return {'memory': '主人与甲约周末跑步', 'evidence_ids': [r['id'] for r in payload['stage']]}

        result = self.memory.social.consolidate(sid, summarize)
        self.assertEqual(result['status'], 'updated')
        self.assertEqual(captured[0]['manual_corrections'], '主人与甲常一起跑步')
        self.assertTrue(all('键盘' not in r['text'] for r in captured[0]['stage']))
        detail = self.memory.social.detail(sid)
        self.assertEqual(len(detail['sources']), 100)
        self.assertEqual([r['kind'] for r in detail['history']], ['automatic', 'manual'])
        self.qq.recall(('123','456'), 1)
        self.memory.social.store.sync()
        self.assertEqual(self.memory.social.detail(sid)['valid'], 0)

    async def test_embedding_failure_keeps_local_recall_and_model_change_has_own_index(self):
        first=self.chat.profiles.save(ProfileInput(subject='miko',kind='fact',title='乐器',content='电吉他').record())
        await self.memory.sync()
        class Offline:
            async def embed(self,texts):
                raise RuntimeError('offline')
        self.memory.index._embedding_pool._cache['embedding']=Offline()
        result=await self.memory.context('你还在练电吉他吗',('123','456'))
        self.assertEqual([r['id'] for r in result['role_material']],[first['id']])
        self.assertTrue(result['retrieval']['error'])
        (self.local/'config/embedding.local.json').write_text('{"model":"different-model"}',encoding='utf-8')
        self.memory.configure_index()
        self.assertFalse(self.memory.index.database.recall_file_repo.list_recall_files())
        (self.local/'config/embedding.local.json').write_text('{"model":"test"}',encoding='utf-8')
        self.memory.configure_index()
        self.assertTrue(self.memory.index.database.recall_file_repo.list_recall_files())


if __name__ == '__main__':
    unittest.main()
