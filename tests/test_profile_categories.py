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
