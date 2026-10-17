import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import httpx

from miko.adapters.memory.history import HistoryTools
from miko.adapters.memory.service import Memory
from miko.adapters.qq.messages import parse_bubbles
from miko.domain.conversation.chat import ChatService, Turn
from miko.settings import Settings
from miko.storage.qq import QqStore


class HistoryToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        (root / 'config').mkdir()
        (root / 'config/memory.local.json').write_text('{"engine":"memobase"}')
        (root / 'config/provider.local.json').write_text(json.dumps({
            'api_base': 'http://127.0.0.1:1/v1', 'api_key': 'private', 'model': 'test'}))
        self.chat = ChatService(Settings(root, 7870))
        self.qq = QqStore(self.chat.store)
        for friend in ('456', '789', 'group-100'):
            self.qq.add_friend('123', friend, friend)
        self.memory = Memory(self.chat)
        self.chat.memory = self.memory
        self.key = ('123', '456')
        self.cid = 'qq:123:456'
        self.chat.store.begin_turn('other-web', 'offset', '编号应与QQ消息编号分开')
        self.chat.store.finish('offset', '测试', 'completed', None)
        self.memory.social.store.sync()
        self.add(1, '周六晚上一起去音乐会吗')
        self.add(2, '行', 'miko')
        for i in range(3, 17):
            self.add(i, f'中间闲聊{i}')
        self.add(17, '音乐会临时取消了，票已退')
        self.add(18, '那晚上就在家吧', 'miko')
        self.add(19, 'SECRET_OTHER_PERSON', key=('123', '789'))
        self.add(20, 'SECRET_GROUP', key=('123', 'group-100'))
        self.memory.social.store.sync()
        self.tools = HistoryTools(self.memory, self.cid)

    def add(self, mid, text, origin='friend', key=None):
        key = key or self.key
        self.qq.event(key, mid, text, time.time() + mid, origin)
        self.qq.record(key, mid, 'qq:' + key[1] if origin == 'friend' else 'miko')

    async def asyncTearDown(self):
        await self.memory.close()
        self.temp.cleanup()

    async def test_search_preserves_reply_and_later_cancellation_without_other_chats(self):
        found = self.tools.execute('search_messages', {'query': '音乐会'})
        self.assertEqual(len(found['hit_ids']), 2, found)
        contents = [row['content'] for row in found['messages']]
        self.assertIn('行', contents)
        self.assertIn('音乐会临时取消了，票已退', contents)
        self.assertIn('那晚上就在家吧', contents)
        self.assertEqual(set(found['hit_ids']), {1, 17})
        self.assertNotIn('SECRET', json.dumps(found))
        with self.memory.social.store.connect() as db:
            other = db.execute("SELECT id FROM messages WHERE content='SECRET_OTHER_PERSON'").fetchone()[0]
        self.assertIn('error', self.tools.execute('get_context', {'message_id': other}))
        self.assertIn('error', self.tools.execute('read_message', {'message_id': other}))
        anchor = next(row['id'] for row in found['messages'] if row['content'] == '音乐会临时取消了，票已退')
        self.qq.recall(self.key, 17)
        # The live-original check hides a recall even before projection sync.
        self.assertIn('error', self.tools.execute('get_context', {'message_id': anchor}))

    async def test_read_history_uses_real_qq_ids_and_only_allows_seen_quote_targets(self):
        self.tools.execute('search_messages', {'query': '音乐会'})
        records = [{'message_id': 50, 'author': {'id':'qq:456','kind':'contact','name':'朋友'}}]
        raw = json.dumps({'bubbles':[{'kind':'text','text':'那场音乐会后来取消了','quote_message_id':17}]})
        self.assertRaises(ValueError, parse_bubbles, raw, records, [])
        bubbles = parse_bubbles(raw, records, [], quote_records=self.tools.quote_records())
        self.assertEqual(bubbles[0]['quote_message_id'], 17)
        forbidden = raw.replace('17', '19')
        self.assertRaises(ValueError, parse_bubbles, forbidden, records, [], quote_records=self.tools.quote_records())

    async def test_snapshot_pagination_and_long_message_read(self):
        first = self.tools.execute('read_conversation', {'order': 'oldest', 'limit': 2})
        self.add(21, '新消息不能挤入旧分页')
        self.memory.social.store.sync()
        second = self.tools.execute('read_conversation', {'order': first['order'], 'limit': 2,
            'snapshot_max_id': first['snapshot_max_id'], 'offset': first['next_offset']})
        self.assertFalse(set(r['id'] for r in first['messages']) & set(r['id'] for r in second['messages']))
        text = '长原文' * 900 + '末尾确认'
        self.add(22, text)
        self.memory.social.store.sync()
        tools = HistoryTools(self.memory, self.cid)
        found = tools.execute('search_messages', {'query': '末尾确认'})
        row = next(r for r in found['messages'] if r['id'] in found['hit_ids'])
        self.assertTrue(row['truncated'])
        rest = tools.execute('read_message', {'message_id': row['id'], 'offset': 2000})
        self.assertEqual(rest['content'], text[2000:])
        self.assertFalse(rest['has_more'])

    async def test_automatic_source_window_and_web_conversation_scope(self):
        with self.memory.social.store.connect() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM messages WHERE conversation_id=?',
                                           ('miko|' + self.cid,))]
            sid = db.execute('SELECT id FROM tulpa_scopes WHERE conversation_id=?',
                             ('miko|' + self.cid,)).fetchone()[0]
            self.memory.structured.commit(db, sid, {'days': [{'subject': '', 'subjects': ['miko','qq:456'],
                'peer': self.cid, 'day': '2026-10-17', 'text': '约好周六去音乐会', 'sources': ids, 'through': 'seed'}]})
        rows = self.memory.structured.days(peer=self.cid)
        evidence = self.tools.seed({'dated_experiences': rows}, '音乐会还去吗')
        self.assertIn('音乐会临时取消了，票已退', [r['content'] for r in evidence])
        self.chat.store.begin_turn('web-a', 'a1', '属于网页A')
        self.chat.store.finish('a1', 'A回复', 'completed', None)
        self.chat.store.begin_turn('web-b', 'b1', '属于网页B')
        self.chat.store.finish('b1', 'B回复', 'completed', None)
        self.memory.social.store.sync()
        web = HistoryTools(self.memory, 'web-a')
        found = web.execute('read_conversation', {})
        self.assertEqual([r['content'] for r in found['messages']], ['属于网页A', 'A回复'])

    @staticmethod
    def response(delta, finish=None, usage=None):
        chunks = [{'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}]
        if usage:
            chunks.append({'choices': [], 'usage': usage})
        return httpx.Response(200, content=(''.join('data: ' + json.dumps(c, ensure_ascii=False) + '\n\n'
                                                  for c in chunks) + 'data: [DONE]\n\n').encode())

    async def test_proxy_completes_multiple_read_rounds_and_only_exposes_final_reply(self):
        requests = []
        def provider(request):
            payload = json.loads(request.content)
            requests.append(payload)
            results = [r for r in payload['messages'] if r['role'] == 'tool']
            if not results:
                return self.response({'content': '我先查原文', 'tool_calls': [{'index': 0, 'id': 's1',
                    'type': 'function', 'function': {'name': 'search_messages', 'arguments': '{"query":"音乐会"}'}}]},
                    'tool_calls', {'prompt_tokens': 10, 'completion_tokens': 4})
            if len(results) == 1:
                found = json.loads(results[-1]['content'])
                self.assertIn('取消', json.dumps(found, ensure_ascii=False))
                return self.response({'tool_calls': [{'index': 0, 'id': 'c1', 'type': 'function',
                    'function': {'name': 'get_context', 'arguments': json.dumps({'message_id': found['hit_ids'][0]})}}]},
                    'tool_calls', {'prompt_tokens': 20, 'completion_tokens': 4})
            return self.response({'content': '原来答应了，后来音乐会取消了。'}, 'stop',
                                 {'prompt_tokens': 30, 'completion_tokens': 9})
        client_type = httpx.AsyncClient
        transport = httpx.MockTransport(provider)
        turn = Turn('lookup', self.cid, '音乐会还去吗', context=[{'role':'user','content':'音乐会还去吗'}],
                    history_tools=self.tools)
        with patch('miko.domain.conversation.chat.httpx.AsyncClient',
                   side_effect=lambda **kw: client_type(transport=transport, **kw)):
            response = await self.chat.proxy(turn)
            output = b''.join([part async for part in response.body_iterator]).decode()
        self.assertEqual(len(requests), 3)
        self.assertEqual(turn.text, '原来答应了，后来音乐会取消了。')
        self.assertNotIn('我先查原文', output)
        self.assertNotIn('tool_calls', output)
        self.assertEqual(turn.usage['prompt_tokens'], 60)
        self.assertIsNone(turn.error)

    async def test_unsupported_tools_fall_back_with_notice(self):
        requests = []
        def provider(request):
            payload = json.loads(request.content)
            requests.append(payload)
            if payload.get('tools'):
                return httpx.Response(400, json={'error': 'tools unsupported'})
            return self.response({'content': '使用现有原文回复'}, 'stop')
        client_type = httpx.AsyncClient
        turn = Turn('fallback', self.cid, '你好', context=[{'role':'user','content':'你好'}], history_tools=self.tools)
        with patch('miko.domain.conversation.chat.httpx.AsyncClient',
                   side_effect=lambda **kw: client_type(transport=httpx.MockTransport(provider), **kw)):
            response = await self.chat.proxy(turn)
            async for _ in response.body_iterator:
                pass
        self.assertEqual(len(requests), 2)
        self.assertIn('未接受原文回查工具', turn.warning)
        self.assertEqual(turn.text, '使用现有原文回复')

    async def test_interruption_cancels_followup_provider_request(self):
        waiting = asyncio.Event()
        async def provider(request):
            payload = json.loads(request.content)
            if not any(row['role']=='tool' for row in payload['messages']):
                return self.response({'tool_calls':[{'index':0,'id':'read1','type':'function',
                    'function':{'name':'search_messages','arguments':'{"query":"音乐会"}'}}]}, 'tool_calls')
            waiting.set()
            await asyncio.Event().wait()
        client_type = httpx.AsyncClient
        turn = Turn('interrupt', self.cid, '音乐会还去吗', context=[{'role':'user','content':'音乐会还去吗'}],
                    history_tools=self.tools)
        with patch('miko.domain.conversation.chat.httpx.AsyncClient',
                   side_effect=lambda **kw: client_type(transport=httpx.MockTransport(provider), **kw)):
            response = await self.chat.proxy(turn)
            async def consume():
                async for _ in response.body_iterator:
                    pass
            running = asyncio.create_task(consume())
            await asyncio.wait_for(waiting.wait(), 3)
            await self.chat.cancel(turn)
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(running, 3)
        self.assertEqual(turn.text, '')
