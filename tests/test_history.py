from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

from miko.api.history import history_router
from miko.storage.chat import ChatStore
from miko.storage.qq import QqStore


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = ChatStore(Path(self.temp.name) / 'chat.sqlite3')
        self.qq = QqStore(self.store)
        self.stamp = datetime(2026, 10, 16, tzinfo=ZoneInfo('Asia/Shanghai'))
        app = FastAPI(); app.include_router(history_router(self.store))
        self.api = TestClient(app)

    def tearDown(self):
        self.api.close(); self.temp.cleanup()

    def add(self, mid, text, minutes=600, account='123', friend='group-900', sender='456'):
        key = (account, friend)
        self.qq.add_friend(*key, '测试对话', chat_type=2 if friend.startswith('group-') else 1)
        self.qq.member(sender, '全局昵称')
        self.qq.event(key, mid, text, (self.stamp + timedelta(minutes=minutes)).timestamp(), 'friend',
                      {'sender_name': '本群名片' if sender == '456' else '另一位群友'})
        self.qq.record(key, mid, 'qq:' + sender)
        return f'qq:{account}:{friend}:{mid}'

    def test_search_respects_conversation_dates_recall_and_group_authors(self):
        wanted = self.add(8, '最近听歌，还是夜鹿听得多', minutes=1439)
        self.add(9, '夜鹿的新歌', minutes=600)
        recalled = self.add(10, '听歌时又放了夜鹿', minutes=601)
        self.qq.recall(('123', 'group-900'), 10)
        self.add(11, '听歌，夜鹿', minutes=1440)
        self.add(12, '听歌，夜鹿', account='999')
        self.add(13, '听歌，夜鹿', friend='group-901')
        self.store.begin_turn('web-one', 'web-reply', '听歌，夜鹿')
        self.store.finish('web-reply', '网页回复', 'completed', None)
        value = self.api.get('/api/history/search', params=dict(source='qq', conversation_id='qq:123:group-900',
                            query='听歌 夜鹿', day='2026-10-16')).json()
        self.assertEqual([row['id'] for row in value['messages']], [wanted])
        self.assertEqual(value['messages'][0]['author']['name'], '本群名片')
        web = self.api.get('/api/history/search', params=dict(source='web', query='夜鹿')).json()
        self.assertEqual(len(web['messages']), 1)
        self.assertEqual(web['messages'][0]['conversation_id'], 'web-one')
        context = self.api.get('/api/history/context', params=dict(source='qq', conversation_id='qq:123:group-900', message_id=recalled)).json()
        self.assertEqual(next(r for r in context['messages'] if r['id'] == recalled)['text'], '这条消息已撤回')

    def test_date_pagination_stays_stable_while_new_messages_arrive(self):
        ids = [self.add(i + 1, f'当天消息 {i}', minutes=i) for i in range(33)]
        params = dict(source='qq', conversation_id='qq:123:group-900', day='2026-10-16')
        first = self.api.get('/api/history/search', params=params).json()
        self.assertEqual([r['id'] for r in first['messages']], ids[:30])
        self.assertTrue(first['has_more'])
        self.add(99, '稍后才收到的旧时间消息', minutes=10)
        last = self.api.get('/api/history/search', params={**params, 'offset': 30, 'snapshot': first['snapshot']}).json()
        self.assertEqual([r['id'] for r in last['messages']], ids[30:])
        self.assertFalse(last['has_more'])

    def test_context_uses_message_time_and_cannot_cross_conversations(self):
        late = self.add(1, '后一条', minutes=603, sender='789')
        early = self.add(99, '前一条', minutes=601)
        focus = self.add(5, '要找的原消息', minutes=602)
        self.add(6, '另一个群的消息', minutes=602, friend='group-901')
        params = dict(source='qq', conversation_id='qq:123:group-900', message_id=focus, before=1, after=1)
        value = self.api.get('/api/history/context', params=params).json()
        self.assertEqual([r['id'] for r in value['messages']], [early, focus, late])
        self.assertEqual(value['messages'][-1]['author']['name'], '另一位群友')
        self.assertEqual(self.api.get('/api/history/context', params={**params, 'conversation_id': 'qq:123:group-901'}).status_code, 404)
