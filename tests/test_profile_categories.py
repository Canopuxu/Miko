from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from miko.api.profiles import profiles_router, ProfileInput
from miko.storage.chat import ChatStore
from miko.storage.profiles import ProfileStore


class CategoryTests(unittest.TestCase):
    def test_category_rename_delete_preserve_entries_and_search(self):
        with tempfile.TemporaryDirectory() as folder:
            chat = ChatStore(Path(folder) / 'profiles.sqlite3')
            profiles = ProfileStore(chat)
            app = FastAPI()
            app.include_router(profiles_router(SimpleNamespace(profiles=profiles), None))
            with TestClient(app) as client:
                tree = client.get('/api/profiles/categories').json()['categories']
                preferences = next(c for c in tree if c['name'] == '喜好与厌恶')
                food = preferences['children'][0]
                original = profiles.save(ProfileInput(subject='miko', kind='fact',
                    category='喜好与厌恶/喜欢的食物', title='喜欢嚼橄榄', content='喜欢嚼橄榄', enabled=False).record())
                edited = deepcopy(tree)
                group = next(c for c in edited if c['id'] == preferences['id'])
                group['name'] = '偏好'
                group['children'][0]['name'] = '零食'
                result = client.put('/api/profiles/categories', json={'subject': 'miko', 'categories': edited})
                self.assertEqual(result.status_code, 200, result.text)
                row = profiles.get(original['id'])
                self.assertEqual((row['category'], row['content'], row['enabled']), ('偏好/零食', '喜欢嚼橄榄', 1))
                self.assertEqual(profiles.list('零食')[0]['id'], original['id'])
                group['children'] = [c for c in group['children'] if c['id'] != food['id']]
                self.assertEqual(client.put('/api/profiles/categories', json={'subject': 'miko', 'categories': edited}).status_code, 200)
                self.assertEqual(profiles.get(original['id'])['category'], '')
                self.assertEqual(profiles.list('橄榄')[0]['content'], '喜欢嚼橄榄')
                self.assertEqual(client.get('/api/profiles/categories').json()['categories'], edited)
                # Independent subject trees survive reopening, including empty categories.
                reopened = ProfileStore(ChatStore(chat.path))
                from miko.storage.profile_categories import ProfileCategories
                self.assertEqual(ProfileCategories(reopened.chat).get('miko'), edited)
                self.assertEqual(ProfileCategories(reopened.chat).get('owner')[0]['name'], '个人概况')


class PinnedCategoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_core_defaults_apply_to_existing_library_and_can_be_unpinned(self):
        import json
        from miko.storage.profile_categories import ProfileCategories
        from retrieval_support import select
        with tempfile.TemporaryDirectory() as folder:
            profiles = ProfileStore(ChatStore(Path(folder) / 'profiles.sqlite3'))
            library = ProfileCategories(profiles.chat)
            for subject, gender in [('miko', '女生'), ('owner', '男生')]:
                tree = library.get(subject)
                self.assertTrue(tree[0]['pinned'])
                profiles.save(ProfileInput(subject=subject, kind='overview', category=tree[0]['name'], title=gender, content=gender).record())
                tree[0].pop('pinned')
                with profiles.chat.connect() as db:
                    db.execute('UPDATE profile_library SET categories=? WHERE subject=?', (json.dumps(tree), subject))
                self.assertTrue(library.get(subject)[0]['pinned'])
            context = profiles.context('今天吃了什么')
            core, _, _ = await select(None, '今天吃了什么', context)
            self.assertEqual(set(core), {'Miko：女生', '橙汁：男生'})
            app = FastAPI()
            app.include_router(profiles_router(SimpleNamespace(profiles=profiles), None))
            with TestClient(app) as client:
                tree = library.get('owner')
                tree[0]['pinned'] = False
                self.assertEqual(client.put('/api/profiles/categories', json={'subject':'owner','categories':tree}).status_code, 200)
            self.assertFalse(library.get('owner')[0]['pinned'])
            self.assertNotIn('owner', [r['subject'] for r in profiles.context('今天吃了什么')['role_material']])

    async def test_pin_is_persistent_background_and_unpin_restores_retrieval(self):
        from miko.storage.profile_categories import ProfileCategories
        from retrieval_support import select
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'profiles.sqlite3'
            profiles = ProfileStore(ChatStore(path))
            library = ProfileCategories(profiles.chat)
            tree = library.get('owner')
            root = next(c for c in tree if c['name'] == '生活习惯')
            sleep = next(c for c in root['children'] if c['name'] == '作息')
            entry = profiles.save(ProfileInput(subject='owner', kind='fact', category='生活习惯/作息', title='睡眠', content='晚上12点睡觉').record())
            profiles.save(ProfileInput(subject='owner', kind='fact', category='兴趣', title='兴趣', content='跑步').record())
            profiles.save(ProfileInput(subject='owner', kind='overview', title='概况', content='橙汁').record())
            sleep['pinned'] = True
            app = FastAPI()
            app.include_router(profiles_router(SimpleNamespace(profiles=profiles), None))
            with TestClient(app) as client:
                self.assertEqual(client.put('/api/profiles/categories', json={'subject':'owner','categories':tree}).status_code, 200)
            profiles = ProfileStore(ChatStore(path))
            context = profiles.context('Miko喜欢听谁的歌')
            self.assertIn(entry['id'], [r['id'] for r in context['role_material']])
            # QQ includes all facts as candidates. The owner pin must survive Miko-only scope.
            context['role_material'] = profiles.list(active=True)
            provider = SimpleNamespace(api_base='https://example.com/v1')
            core, selected, _ = await select(provider, 'Miko喜欢听谁的歌', context)
            self.assertIn('橙汁：晚上12点睡觉', core)
            self.assertNotIn('橙汁：橙汁', core)
            self.assertFalse(selected)
            sleep['name'] = '睡眠时间'
            library.save('owner', tree)
            self.assertEqual(profiles.get(entry['id'])['category'], '生活习惯/睡眠时间')
            self.assertIn(entry['id'], [r['id'] for r in profiles.context('无关话题')['role_material']])
            sleep['pinned'] = False
            library.save('owner', tree)
            self.assertNotIn(entry['id'], [r['id'] for r in profiles.context('无关话题')['role_material']])
