import json
from pathlib import Path
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from miko.api.profiles import profiles_router
from miko.api.profiles import ProfileInput
from miko.storage.chat import ChatStore
from miko.storage.profiles import ProfileStore
from miko.storage.qq import QqStore
from types import SimpleNamespace


class ProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'chat.sqlite3'
        self.chat = ChatStore(self.path)
        self.profiles = ProfileStore(self.chat)
        self.qq = QqStore(self.chat)
        self.qq.add_friend('123', '456', '朋友甲')
        self.qq.add_friend('123', '789', '朋友乙')

    def tearDown(self):
        self.temp.cleanup()

    def add(self, **fields):
        return self.profiles.save(ProfileInput(subject='miko', kind='fact', title='测试资料',
                                               content='用于验证检索', **fields).record())

    def test_chinese_search_scope_edit_disable_and_reopen(self):
        value = ProfileInput(subject='miko', kind='fact', category='外貌', title='日常穿着',
            content='白色体恤，红白格子长衫', keywords='衣服 服装').record()
        clothing = self.profiles.save(value)
        scoped = self.add(account='123', friend='789', keywords='衣服')
        self.assertEqual(len(self.profiles.list('衣服')), 2)
        context = self.profiles.context('穿着什么衣服', ('123', '456'))
        self.assertEqual([r['id'] for r in context['role_material']], [clothing['id']])
        updated = self.profiles.save({**value, 'content': '深蓝衬衫', 'keywords': '衬衣'}, clothing['id'])
        reopened = ProfileStore(ChatStore(self.path))
        self.assertEqual(reopened.list('深蓝')[0]['content'], '深蓝衬衫')
        self.assertEqual(reopened.list('红白'), [])
        reopened.save({**value, 'enabled': False}, clothing['id'])
        self.assertEqual(reopened.context('衣服', ('123','456'))['role_material'], [])
        self.assertFalse(updated['id'] == scoped['id'])

    def test_core_dated_activity_excerpt_and_two_relationships(self):
        overview = self.profiles.save(ProfileInput(subject='miko', kind='overview', title='Miko', content='练键盘').record())
        activity = self.profiles.save(ProfileInput(subject='owner', kind='recent', title='近况', content='练吉他', as_of='2026-10-12').record())
        excerpt = self.profiles.save(ProfileInput(subject='owner', kind='excerpt', title='旧对话',
            content='主人和朋友一起跑步', keywords='运动', account='123', friend='456').record())
        self.qq.update_friend(('123','456'), owner_relationship='常一起跑步', miko_relationship='刚认识')
        context = self.profiles.context('运动', ('123','456'))
        self.assertEqual([r['id'] for r in context['role_material']], [overview['id'], activity['id'], excerpt['id']])
        self.assertEqual([r['subject'] for r in context['role_material']], ['miko', 'owner', 'owner'])
        relation = context['friend_relationships'][0]
        self.assertEqual((relation['owner_relationship'], relation['miko_relationship']), ('常一起跑步','刚认识'))
        self.assertEqual(self.profiles.context('朋友甲')['friend_relationships'][0]['friend'], '456')

    def test_introduction_requires_sent_receipt_and_manual_correction_survives_restart(self):
        key = ('123','456')
        rid = self.qq.new_run(key, 1000)
        self.qq.plan(rid, [{'kind': 'text', 'text': '我是 Miko', 'introduces_miko': True}], ['我是 Miko'])
        self.assertFalse(self.qq.friend(key)['miko_introduced'])
        self.qq.update_bubble(rid, 0, state='unknown')
        self.assertFalse(self.qq.friend(key)['miko_introduced'])
        self.qq.update_bubble(rid, 0, state='sent', receipt_id=10, timestamp=1001)
        self.assertEqual(self.qq.friend(key)['intro_receipt_id'], 10)
        self.qq.update_friend(key, miko_introduced=0, intro_receipt_id=None)
        self.qq.recover()
        self.assertFalse(self.qq.friend(key)['miko_introduced'])

    def test_management_api_and_context_use_same_saved_material(self):
        app = FastAPI()
        app.include_router(profiles_router(SimpleNamespace(profiles=self.profiles), SimpleNamespace(store=self.qq)))
        with TestClient(app) as client:
            body = {'subject':'miko','kind':'fact','title':'兴趣','content':'学平面设计','keywords':'设计'}
            created = client.post('/api/profiles', json=body)
            self.assertEqual(created.status_code, 200, created.text)
            entry_id = created.json()['id']
            context = client.get('/api/profiles/context', params={'q':'设计','account':'123','friend':'456'}).json()
            self.assertEqual(context['role_material'][0]['id'], entry_id)
            response = client.put('/api/profiles/relationships/123/456', json={'owner_relationship':'同学', 'miko_relationship':'刚认识'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['owner_relationship'], '同学')
            self.assertEqual(client.post('/api/profiles', json=body, headers={'origin':'https://elsewhere.example'}).status_code, 403)
            self.assertEqual(client.delete(f'/api/profiles/entries/{entry_id}').status_code, 200)
            self.assertEqual(self.profiles.list('设计'), [])


if __name__ == '__main__':
    unittest.main()
