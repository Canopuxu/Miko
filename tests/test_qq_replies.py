import asyncio
import base64
import io
import json
from pathlib import Path
import tempfile
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from PIL import Image
from websockets.asyncio.server import serve

from miko.adapters.qq.onebot import EventReceiver, OneBot, QqConfig, SendError, SendUncertain
from miko.domain.conversation.qq import QqService
from miko.storage.chat import ChatStore
from miko.storage.qq import QqStore


class FakeChat:
    def __init__(self, store):
        self.store, self.calls = store, []
        self.gate = None
        self.text = '你好，我是 Miko。'
        self.bubbles = None

    def begin_qq(self, cid, records, stickers=None):
        self.calls.append(records)
        turn = SimpleNamespace(text=json.dumps({'bubbles': self.bubbles if self.bubbles is not None else [{'kind': 'text', 'text': self.text}]}, ensure_ascii=False), status='completed', error=None)
        async def run():
            if self.gate:
                await self.gate.wait()
        turn.task = asyncio.create_task(run())
        return turn

    async def cancel(self, turn):
        turn.status = 'cancelled'
        if not turn.task.done():
            self.gate.set()
            await asyncio.gather(turn.task, return_exceptions=True)


class FakeClient:
    def __init__(self):
        self.sent = []
        self.gate = None
        self.uncertain = False
        self.on_send = None
        self.payloads = []
        self.quoted = []

    async def quote(self, account, friend, event):
        self.quoted.append(event['message_id'])

    async def target(self, account, friend):
        return '测试好友'

    async def send(self, account, friend, text, guard):
        if self.gate:
            await self.gate.wait()
        guard()
        self.payloads.append(text)
        content = ''.join(s['data'].get('text', '') for s in text) if isinstance(text, list) else text
        self.sent.append((friend, content))
        if self.on_send:
            await self.on_send()
        if self.uncertain:
            raise SendUncertain('发送结果未知；不会重发')
        return {'message_id': 899 + len(self.sent), 'timestamp': 1120}


class QqRepliesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ChatStore(Path(self.temp.name)/'chat.sqlite3')
        self.chat, self.client = FakeChat(self.store), FakeClient()
        self.now = 1000
        self.service = QqService(self.chat, None, client=self.client, clock=lambda: self.now)
        self.service.receiver.state.update(state='connected', account='123')
        self.key = ('123','456')
        self.service.store.add_friend(*self.key, '测试好友')
        await self.service.configure(self.key, True)

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    def event(self, mid, text='你好', *, own=False, friend='456', media=False):
        return {'post_type': 'message_sent' if own else 'message', 'message_type': 'private',
            'self_id': 123, 'user_id': 123 if own else int(friend), 'target_id': int(friend),
            'time': self.now, 'message_id': mid,
            'message': [{'type': 'image' if media else 'text', 'data': {'text': text}}]}

    async def receive(self, mid, text='你好', **kwargs):
        await self.service.receive(self.event(mid,text,**kwargs))

    async def due(self):
        await self.service.tick()
        tasks = [w.task for w in self.service.workers.values()]
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks), 2)

    async def test_two_minute_wait_batches_then_continuous_chat(self):
        await self.receive(1, '第一句')
        self.now = 1020
        await self.receive(2, '第二句')
        self.now = 1119
        await self.due()
        self.assertEqual(self.client.sent, [])
        self.now = 1120
        await self.due()
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual([r['text'] for r in self.chat.calls[0]], ['第一句','第二句'])
        self.now = 1121
        await self.receive(3, '继续聊')
        self.now = 1122
        await self.due()
        self.assertEqual(len(self.client.sent), 1)
        self.now = 1123
        await self.due()
        self.assertEqual(len(self.client.sent), 2)
        self.assertIn('miko', [r['author']['kind'] for r in self.chat.calls[-1]])

    async def test_silence_is_success_without_sending_retry_or_taking_over(self):
        self.chat.bubbles = []
        await self.receive(1, '好，那就这样')
        self.now = 1120
        await self.due()
        self.assertEqual(self.client.sent, [])
        self.assertEqual(self.service.store.friend(self.key)['mode'], 'owner')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM qq_runs').fetchone()[0], 'skipped')
        self.now = 1300
        await self.due()
        self.assertEqual(len(self.chat.calls), 1)
        self.chat.bubbles = None
        await self.receive(2, 'Miko，明天有什么安排？')
        self.now += 3
        await self.due()
        self.assertEqual(len(self.client.sent), 1)

    async def test_owner_reply_clears_pending_then_next_message_waits_again(self):
        await self.receive(1)
        self.now = 1005
        await self.receive(2, '我来回复', own=True)
        self.now = 1200
        await self.due()
        self.assertEqual(self.client.sent, [])
        await self.receive(3, '下一条')
        self.now = 1319
        await self.due()
        self.assertEqual(self.client.sent, [])
        self.now = 1320
        await self.due()
        self.assertEqual(len(self.client.sent), 1)

    async def test_named_miko_skips_wait_and_keeps_following_batch(self):
        await self.receive(1, 'MiKo在吗')
        self.assertEqual(self.service.store.friend(self.key)['due_at'], 1002)
        self.now = 1001
        await self.receive(2, '最近听什么歌')
        self.assertEqual(self.service.store.friend(self.key)['due_at'], 1003)
        self.now = 1002
        await self.due()
        self.assertEqual(self.client.sent, [])
        self.now = 1003
        await self.due()
        self.assertEqual(len(self.client.sent), 1)

    async def test_unintroduced_context_excludes_previous_activation_messages(self):
        await self.receive(1, '旧测试对话')
        self.now = 1005
        await self.service.configure(self.key, True)
        await self.receive(2, '睡了吗')
        self.assertEqual([r['text'] for r in self.service.store.context(self.key)], ['睡了吗'])
        self.assertEqual(len(self.service.store.messages(self.key)), 2)

    async def test_normal_reply_drops_latest_quote_before_dispatch(self):
        self.chat.bubbles = [{'kind': 'text', 'text': '准备睡啦', 'quote_message_id': 1}]
        await self.receive(1, '早点睡吧')
        self.now = 1120
        await self.due()
        self.assertEqual(self.client.quoted, [])
        self.assertEqual(self.client.payloads, [[{'type': 'text', 'data': {'text': '准备睡啦'}}]])

    async def test_reply_to_earlier_message_keeps_quote(self):
        self.chat.bubbles = [{'kind': 'text', 'text': '听夜鹿', 'quote_message_id': 1}]
        await self.receive(1, '最近听谁的歌')
        await self.receive(3, '稍等', own=True)
        await self.receive(2, '我刚吃完饭')
        self.now = 1120
        await self.due()
        self.assertEqual(self.client.quoted, [1])

    async def test_name_does_not_override_disabled_blocked_or_manual(self):
        await self.receive(1, 'komiko')
        self.assertEqual(self.service.store.friend(self.key)['due_at'], 1120)
        await self.receive(2, 'Miko')
        await self.receive(3, '我来', own=True)
        self.now = 1003
        await self.due()
        self.assertEqual(self.client.sent, [])
        await self.service.configure(self.key, False)
        await self.receive(4, 'Miko')
        self.assertIsNone(self.service.store.friend(self.key)['due_at'])
        await self.service.configure(self.key, True)
        self.service.store.update_friend(self.key, blocked=1)
        await self.receive(5, 'Miko')
        self.assertIsNone(self.service.store.friend(self.key)['due_at'])

    async def test_new_message_invalidates_generating_reply_and_rebuilds_context(self):
        self.chat.gate = asyncio.Event()
        await self.receive(1, '旧消息')
        self.now = 1120
        await self.service.tick()
        await asyncio.sleep(.01)
        await self.receive(2, '补充消息')
        await asyncio.sleep(.01)
        self.assertEqual(self.client.sent, [])
        self.assertEqual(self.service.store.runs()[0]['state'], 'stale')
        self.chat.gate.set()
        self.now = 1122
        await self.due()
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual([r['text'] for r in self.chat.calls[-1]], ['旧消息','补充消息'])

    async def test_owner_intervention_during_target_check_prevents_send(self):
        self.client.gate = asyncio.Event()
        await self.receive(1)
        self.now = 1120
        await self.service.tick()
        await asyncio.sleep(.01)
        await self.receive(2, '我在', own=True)
        self.client.gate.set()
        await asyncio.gather(*(w.task for w in list(self.service.workers.values())))
        self.assertEqual(self.client.sent, [])

    async def test_echo_before_receipt_is_miko_but_other_own_message_is_owner(self):
        async def echo():
            await self.receive(900, self.chat.text, own=True)
            await self.receive(901, '我也在', own=True)
        self.client.on_send = echo
        await self.receive(1)
        self.now = 1120
        await self.due()
        messages = self.store.messages(self.service.store.friend(self.key)['conversation_id'])
        self.assertEqual([m['author']['kind'] for m in messages], ['contact','miko','owner'])
        self.assertEqual(self.service.store.friend(self.key)['mode'], 'owner')
        await self.receive(900, self.chat.text, own=True)
        self.assertEqual(len(self.store.messages(self.service.store.friend(self.key)['conversation_id'])),3)

    async def test_unknown_send_pauses_without_retry_then_explicit_restore(self):
        self.client.uncertain = True
        await self.receive(1)
        self.now = 1120
        await self.due()
        self.now = 1400
        await self.receive(2)
        await self.due()
        self.assertEqual(len(self.client.sent),1)
        self.assertTrue(self.service.store.friend(self.key)['blocked'])
        await self.service.configure(self.key,True)
        await self.due()
        self.assertEqual(len(self.client.sent),1)

    async def test_length_check_blocks_overlong_reply(self):
        self.chat.text = '字'*401
        await self.receive(1)
        self.now = 1120
        await self.due()
        self.assertEqual(self.client.sent,[])
        self.assertEqual(self.service.store.runs()[0]['state'],'error')

    async def test_format_retry_succeeds_on_third_retry_without_duplicate_send(self):
        original = self.chat.begin_qq
        def begin(*args):
            self.chat.bubbles = [{'text': '缺少类型'}] if len(self.chat.calls) < 3 else None
            return original(*args)
        self.chat.begin_qq = begin
        await self.receive(1, 'Miko，你这是夸我还是骂我')
        self.now += 3
        await self.due()
        self.assertEqual(len(self.chat.calls), 4)
        self.assertEqual(len(self.client.sent), 1)

    async def test_exhausted_format_retries_preserve_miko_mode_for_next_message(self):
        self.chat.bubbles = [{'text': '缺少类型'}]
        await self.receive(1, 'Miko，你这是夸我还是骂我')
        self.now += 3
        await self.due()
        self.assertEqual(len(self.chat.calls), 4)
        self.assertEqual(self.client.sent, [])
        self.assertEqual(self.service.store.friend(self.key)['mode'], 'miko')
        self.chat.bubbles = None
        await self.receive(2, '那你说出我十个优点')
        self.now += 3
        await self.due()
        self.assertEqual(len(self.client.sent), 1)

    async def test_manual_reply_during_format_retry_stops_generation(self):
        original = self.chat.begin_qq
        self.chat.bubbles = [{'text': '缺少类型'}]
        async def cancel(turn):
            turn.status = 'cancelled'
        self.chat.cancel = cancel
        def begin(*args):
            turn = original(*args)
            if len(self.chat.calls) == 2:
                turn.task.cancel()
                turn.task = asyncio.create_task(self.receive(2, '我来回', own=True))
            return turn
        self.chat.begin_qq = begin
        await self.receive(1, 'Miko，在吗')
        self.now += 3
        await self.due()
        self.assertEqual(len(self.chat.calls), 2)
        self.assertEqual(self.client.sent, [])
        self.assertEqual(self.service.store.friend(self.key)['mode'], 'owner')

    async def test_disconnect_and_restart_do_not_send_stale_pending_or_unknown_run(self):
        await self.receive(1)
        await self.service.source_changed('disconnected')
        self.now = 1300
        await self.due()
        self.assertEqual(self.client.sent,[])
        run = self.service.store.new_run(self.key,self.now)
        self.service.store.update_run(run,state='sending',text='尚无回执')
        reopened = QqStore(ChatStore(self.store.path))
        reopened.recover()
        self.assertTrue(reopened.friend(self.key)['blocked'])
        self.assertEqual(reopened.runs()[0]['state'],'unknown')
        self.assertIsNone(reopened.friend(self.key)['due_at'])

    async def test_friends_have_independent_switches_and_contexts(self):
        other = ('123','789')
        self.service.store.add_friend(*other,'另一位好友')
        await self.receive(1,'不接话',friend='789')
        await self.receive(2,'需要接话')
        self.now = 1120
        await self.due()
        self.assertEqual(self.client.sent,[('456',self.chat.text)])
        self.assertNotIn('不接话',[r['text'] for r in self.chat.calls[0]])

    async def test_continuous_messages_are_collected_for_at_most_eight_seconds(self):
        self.service.store.update_friend(self.key,mode='miko')
        for mid in range(1,10):
            self.now = 1000 + mid - 1
            await self.receive(mid,str(mid))
            await self.due()
        self.assertEqual(len(self.client.sent),1)
        self.assertEqual(len(self.chat.calls[0]),9)

    async def test_image_message_is_saved_as_unread_and_does_not_drop_batch(self):
        await self.receive(1)
        await self.receive(2,media=True)
        self.now = 1200
        await self.due()
        self.assertEqual(len(self.client.sent),1)
        messages=self.store.messages(self.service.store.friend(self.key)['conversation_id'])
        self.assertEqual(messages[1]['text'],'[图片]')
        self.assertEqual(self.chat.calls[-1][1]['segments'][0]['content_status'], 'not_read')

    async def test_receipt_storage_failure_blocks_next_send_and_releases_worker(self):
        await self.receive(1)
        self.now = 1120
        original=self.service.store.update_bubble
        def failing(rid,position,**fields):
            if fields.get('state') in ('sent','unknown'):
                raise sqlite3.OperationalError('database read-only')
            return original(rid,position,**fields)
        with patch.object(self.service.store,'update_bubble',side_effect=failing):
            with self.assertLogs('miko.domain.conversation.qq', level='ERROR'):
                await self.due()
        self.assertFalse(self.service.workers)
        self.assertEqual(self.service.status()['friends'][0]['state'],'blocked')
        self.now = 1400
        await self.receive(2)
        await self.due()
        self.assertEqual(len(self.client.sent),1)

    async def test_restart_recovers_confirmed_receipt_without_resending(self):
        run = self.service.store.new_run(self.key,self.now)
        self.service.store.update_run(run,state='sent',text='已发送的回复',receipt_id=750)
        reopened = QqStore(ChatStore(self.store.path))
        reopened.recover()
        messages=self.store.messages(reopened.friend(self.key)['conversation_id'])
        self.assertEqual([(r['author']['kind'],r['text']) for r in messages],[('miko','已发送的回复')])
        reopened.recover()
        self.assertEqual(len(self.store.messages(reopened.friend(self.key)['conversation_id'])),1)
        self.assertEqual(self.client.sent,[])

    async def test_mixed_input_quoted_text_face_and_original_sticker_queue(self):
        picture = io.BytesIO()
        Image.new('RGB', (8, 8), 'blue').save(picture, 'GIF')
        raw = picture.getvalue()
        digest = self.service.stickers.add('123', raw, '表示收到和赞同')
        await self.receive(1, '原话')
        event = self.event(2)
        event['message'] = [{'type':'reply','data':{'id':'1'}},
            {'type':'text','data':{'text':'这件事呢'}}, {'type':'face','data':{'id':'76'}}]
        await self.service.receive(event)
        self.chat.bubbles = [{'kind':'text','text':'这件事我记得','quote_message_id':1},
                             {'kind':'face','face_id':'76'}, {'kind':'sticker','sticker_id':digest}]
        async def no_delay(*args): pass
        with patch.object(self.service, 'pause_between', side_effect=no_delay), \
                patch('miko.adapters.qq.messages.FACES', [{'face_id': '76', 'name': '赞'}]), \
                patch('miko.domain.conversation.qq.FACES', [{'face_id': '76', 'name': '赞'}]):
            self.now = 1120
            await self.due()
        self.assertEqual(self.client.quoted, [])
        self.assertEqual(self.chat.calls[0][1]['quoted_message']['text'], '原话')
        self.assertEqual([p[-1]['type'] for p in self.client.payloads], ['text','face','image'])
        encoded = self.client.payloads[2][0]['data']['file'].removeprefix('base64://')
        self.assertEqual(base64.b64decode(encoded), raw)
        run = self.service.store.runs()[0]
        self.assertEqual([b['state'] for b in run['bubbles']], ['sent']*3)
        self.assertEqual([b['receipt_id'] for b in run['bubbles']], [900,901,902])
        self.assertEqual([r['author']['kind'] for r in self.service.store.context(self.key)][-3:], ['miko']*3)

    async def test_owner_intervention_cancels_unsent_tail(self):
        self.chat.bubbles = [{'kind':'text','text':'第一句'}, {'kind':'text','text':'旧的第二句'}]
        async def intervene(*args):
            await self.receive(2, '我来回复', own=True)
            self.service.check(self.key, self.service.workers[self.key])
        await self.receive(1)
        with patch.object(self.service, 'pause_between', side_effect=intervene):
            self.now = 1120
            await self.due()
        self.assertEqual(self.client.sent, [('456','第一句')])
        self.assertEqual([b['state'] for b in self.service.store.runs()[0]['bubbles']], ['sent','cancelled'])
        self.assertEqual(self.service.store.friend(self.key)['mode'], 'owner')
        # Next friend input still starts a fresh two-minute wait.
        await self.receive(3)
        self.assertEqual(self.service.store.friend(self.key)['due_at'], 1240)

    async def test_friend_new_message_cancels_tail_and_next_turn_keeps_sent_context(self):
        self.chat.bubbles = [{'kind':'text','text':'第一句'}, {'kind':'text','text':'旧的尾部'}]
        async def intervene(*args):
            await self.receive(2, '补充一句')
            self.service.check(self.key, self.service.workers[self.key])
        await self.receive(1)
        with patch.object(self.service, 'pause_between', side_effect=intervene):
            self.now = 1120
            await self.due()
        self.assertEqual(self.client.sent, [('456','第一句')])
        self.assertEqual(self.service.store.friend(self.key)['due_at'], 1122)
        self.chat.bubbles = None
        self.chat.text = '回应补充'
        self.now = 1122
        await self.due()
        self.assertEqual(self.client.sent[-1], ('456','回应补充'))
        texts = [r['text'] for r in self.chat.calls[-1]]
        self.assertIn('第一句', texts)
        self.assertIn('补充一句', texts)
        self.assertNotIn('旧的尾部', texts)

    async def test_partial_queue_restart_recovers_receipts_and_never_resends_tail(self):
        rid = self.service.store.new_run(self.key, self.now)
        bubbles = [{'kind':'text','text':'已经发出'}, {'kind':'text','text':'未知'}, {'kind':'text','text':'尾部'}]
        self.service.store.plan(rid, bubbles, ['已经发出','未知','尾部'])
        self.service.store.update_run(rid, state='sending')
        self.service.store.update_bubble(rid,0,state='sent',receipt_id=800,timestamp=self.now)
        self.service.store.update_bubble(rid,1,state='sending')
        reopened = QqStore(ChatStore(self.store.path))
        reopened.recover()
        reopened.recover()
        self.assertEqual([b['state'] for b in reopened.runs()[0]['bubbles']], ['sent','unknown','cancelled'])
        self.assertTrue(reopened.friend(self.key)['blocked'])
        self.assertEqual([r['text'] for r in reopened.context(self.key)], ['已经发出'])
        self.assertEqual(self.client.sent, [])


class OneBotTests(unittest.IsolatedAsyncioTestCase):
    def config(self,ws='ws://127.0.0.1:3001'):
        return QqConfig(http_url='http://127.0.0.1:3000',ws_url=ws,account='123')

    async def test_native_quote_and_face_use_verified_private_message_and_literal_segments(self):
        calls = []
        def transport(request):
            calls.append((request.url.path, json.loads(request.content)))
            data = {'user_id':123}
            if request.url.path.endswith('get_friend_list'):
                data = [{'user_id':456}]
            elif request.url.path.endswith('get_msg'):
                data = {'message_id':15,'message_type':'private','sender':{'user_id':456},'time':1120}
            elif request.url.path.endswith('send_private_msg'):
                data = {'message_id':16}
            return httpx.Response(200,json={'status':'ok','retcode':0,'data':data})
        client = OneBot(self.config(), httpx.MockTransport(transport))
        await client.quote('123','456',{'message_id':15,'origin':'friend','timestamp':1120})
        segments = [{'type':'reply','data':{'id':'15'}},
                    {'type':'text','data':{'text':'[CQ:image,file=x]'}}, {'type':'face','data':{'id':'76'}}]
        receipt = await client.send('123','456',segments,lambda:None)
        self.assertEqual(receipt['message_id'],16)
        self.assertIn(('/get_msg', {'message_id':15}), calls)
        self.assertEqual(calls[-1], ('/send_private_msg', {'user_id':456,'message':segments}))

    async def test_identity_literal_text_and_uncertain_receipt_without_retry(self):
        calls=[]
        def transport(request):
            import json
            calls.append((request.url.path,json.loads(request.content)))
            data = {'user_id':123} if request.url.path.endswith('get_login_info') else [{'user_id':456}]
            if request.url.path.endswith('send_private_msg'):
                return httpx.Response(200,json={'status':'async','retcode':1})
            return httpx.Response(200,json={'status':'ok','retcode':0,'data':data})
        client=OneBot(self.config(),httpx.MockTransport(transport))
        with self.assertRaises(SendError):
            await client.target('999','456')
        with self.assertRaises(SendUncertain):
            await client.send('123','456','[CQ:image,file=x]',lambda:None)
        sends=[payload for path,payload in calls if path.endswith('send_private_msg')]
        self.assertEqual(sends,[{'user_id':456,'message':[{'type':'text','data':{'text':'[CQ:image,file=x]'}}]}])

    async def test_websocket_verifies_identity_and_rejects_other_account(self):
        import json
        allow_bad = asyncio.Event()
        async def ws_handler(ws):
            await ws.send(json.dumps({'post_type':'meta_event','meta_event_type':'lifecycle','self_id':123}))
            await allow_bad.wait()
            await ws.send(json.dumps({'post_type':'message','self_id':999,'message_type':'private'}))
            await ws.wait_closed()
        def transport(request):
            return httpx.Response(200,json={'status':'ok','retcode':0,'data':{'user_id':123}})
        connected, mismatch = asyncio.Event(), asyncio.Event()
        events=[]
        async def on_event(event): events.append(event)
        async def on_change(state):
            if state=='connected': connected.set()
            if state=='account_mismatch': mismatch.set()
        async with serve(ws_handler,'127.0.0.1',0) as server:
            port=server.sockets[0].getsockname()[1]
            client=OneBot(self.config(f'ws://127.0.0.1:{port}'),httpx.MockTransport(transport))
            receiver=EventReceiver(client,on_event,on_change)
            receiver.start()
            try:
                await asyncio.wait_for(connected.wait(),2)
                allow_bad.set()
                await asyncio.wait_for(mismatch.wait(),2)
                self.assertEqual(len(events),1)
                self.assertEqual(receiver.status()['state'],'account_mismatch')
            finally:
                await receiver.close()


if __name__=='__main__':
    unittest.main()
