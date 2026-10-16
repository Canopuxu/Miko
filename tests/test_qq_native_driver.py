import base64
import io
import json
import socket
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image

from miko.adapters.qq.client_driver import NativeClientDriver
from miko.adapters.qq.images import attach_images, public_addresses
from miko.adapters.qq.messages import parse_message


def record(sender='67890', status=2):
    return dict(chatType=1, msgId='7696526253391844439', peerUin='67890',
                senderUin=sender, msgTime='1791950400', sendStatus=status,
                elements=[{'textElement': {'content': 'hello'}}])


class NativeDriverTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'messages.sqlite3'
        self.config = dict(account='12345', port=17971, token='test')
        self.driver = NativeClientDriver(self.config, self.path)

    async def asyncTearDown(self):
        await self.driver.close()
        self.directory.cleanup()

    async def test_native_id_survives_restart_without_precision_loss(self):
        mid = self.driver.remember(record())
        self.assertGreater(mid, 2147483647)
        await self.driver.close()
        self.driver = NativeClientDriver(self.config, self.path)
        self.assertEqual(self.driver.remember(record()), mid)
        self.assertEqual(self.driver.lookup(mid)[0], '7696526253391844439')

    async def test_send_receipt_and_duplicate_owner_events(self):
        received = []
        events = [record('12345', 1), record('12345'), record('12345')]
        content = '\n'.join(json.dumps(dict(sequence=i+1,name='message', payload={'msgList': [r]})) for i,r in enumerate(events))
        requests = []
        def transport(request):
            if request.method == 'POST':
                return httpx.Response(200, json={'result': {'session': 'session-a', 'sequence': 0}})
            requests.append(request)
            return httpx.Response(200, text=content) if len(requests) == 1 else httpx.Response(409)
        await self.driver.http.aclose()
        self.driver.http = httpx.AsyncClient(base_url='http://localhost',
            transport=httpx.MockTransport(transport))
        async def publish(event):
            received.append(event)
        async def reconnect_delay(seconds):
            with self.assertRaises(ConnectionError):
                await self.driver.call('send_private_msg', {})
        with patch('miko.adapters.qq.client_driver.asyncio.sleep', reconnect_delay):
            with self.assertRaises(httpx.HTTPStatusError):
                await self.driver.listen(publish)
        self.assertEqual(requests[1].url.params['after'], '3')
        self.assertEqual(requests[1].url.params['session'], 'session-a')
        self.assertFalse(self.driver.receiving)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]['post_type'], 'message_sent')
        self.assertEqual(received[0]['target_id'], 67890)

    async def test_local_image_reaches_model_input(self):
        out = io.BytesIO()
        Image.new('RGB', (8, 8), 'green').save(out, format='PNG')
        encoded = base64.b64encode(out.getvalue()).decode()
        class Client:
            async def message(self, *args, **kwargs):
                return {'message': [{'type': 'image', 'data': {'file': 'base64://' + encoded}}]}
        rows = [{'message_id': 1, 'segments': [{'type': 'image'}]}]
        await attach_images(Client(), ('12345', '67890'), rows)
        self.assertEqual(rows[0]['segments'][0]['content_status'], 'attached')
        self.assertTrue(rows[0]['_image_inputs'][1]['image_url']['url'].startswith('data:image/jpeg;base64,'))

    async def test_quote_cannot_cross_friend_conversations(self):
        mid = self.driver.remember(record())
        self.driver.rpc = AsyncMock()
        with self.assertRaisesRegex(ValueError, 'another friend'):
            await self.driver.call('send_private_msg', {'user_id': '99999',
                'message': [{'type': 'reply', 'data': {'id': str(mid)}}]})
        self.driver.rpc.assert_not_awaited()

    async def test_received_quote_resolves_original_or_preview(self):
        original = record()
        original['msgSeq'] = '174'
        mid = self.driver.remember(original)
        quoted = record()
        quoted['msgId'] = 'new-message'
        element = {'elementId': 'quote-element', 'replyElement': {
            'replayMsgId': '0', 'replayMsgSeq': '174',
            'senderUid': '67890', 'replyMsgTime': original['msgTime'],
            'sourceMsgTextElems': [{'textElemContent': 'hello'}]}}
        quoted['elements'] = [element]
        event = await self.driver.event(quoted)
        self.assertEqual(event['message'][0]['data']['id'], str(mid))
        # An older original may not exist in our database. Retain QQ's preview.
        element['replyElement']['replayMsgSeq'] = '100'
        event = await self.driver.event(quoted)
        self.driver.rpc = AsyncMock()
        preview = await self.driver.call('get_msg', {
            'message_id': event['message'][0]['data']['id']})
        self.assertEqual(preview['message'][0]['data']['text'], 'hello')
        self.assertEqual(preview['sender']['user_id'], 67890)
        self.driver.rpc.assert_not_awaited()

    async def test_revoked_quote_does_not_resolve_cached_original(self):
        original = record()
        original['msgSeq'] = '174'
        mid = self.driver.remember(original)
        quoted = record()
        quoted['msgId'] = 'new-message'
        quoted['elements'] = [{'elementId': 'quote-element', 'replyElement': {
            'replayMsgSeq': '174', 'senderUid': '67890',
            'replyMsgTime': original['msgTime'], 'replyMsgRevokeType': 1,
            'sourceMsgText': 'hello'}}]
        event = await self.driver.event(quoted)
        reference = event['message'][0]['data']['id']
        self.assertNotEqual(reference, str(mid))
        preview = await self.driver.call('get_msg', {'message_id': reference})
        self.assertTrue(preview['recalled'])

    async def test_private_quote_resolves_current_opaque_author_id(self):
        original={**record(), 'senderUid':'u_friend', 'msgSeq':'174'}
        mid=self.driver.remember(original)
        quoted={**record(), 'msgId':'quote', 'elements':[{'elementId':'7','replyElement':{
            'senderUid':'0','senderUidStr':'u_friend','replayMsgSeq':'174',
            'replyMsgTime':original['msgTime']}}]}
        result=await self.driver.event(quoted)
        self.assertEqual(result['message'][0]['data']['id'],str(mid))

    async def test_group_mentions_use_nt_uid_or_current_account_flag(self):
        self.driver.self_uid='u_me'
        for at in ({'atType':2,'atUid':'0','atNtUid':'u_me'}, {'atType':4,'atUid':'0'}):
            result=await self.driver.event({**record(),'chatType':2,'peerUin':'900',
                'elements':[{'textElement':{'content':'@我',**at}}]})
            self.assertEqual(result['message'],[{'type':'at','data':{'qq':'12345'}}])

    async def test_withdrawal_updates_emitted_message_once_in_private_and_group(self):
        received=[]
        async def publish(event):received.append(event)
        for chat_type,peer in ((1,'67890'),(2,'900')):
            self.driver.group_ids={'900'}
            original={**record(),'chatType':chat_type,'peerUin':peer}
            async def deliver(r):
                await self.driver._publish_native({'name':'onMsgInfoListUpdate','payload':{'msgList':[r]}},publish)
            await deliver(original)
            withdrawn={**original,'msgType':5,'elements':[{'grayTipElement':{
                'subElementType':1,'revokeElement':{'operatorUid':'u_friend'}}}]}
            await deliver(withdrawn)
            await deliver(withdrawn)
            self.assertEqual(received[-1]['notice_type'],'group_recall' if chat_type==2 else 'friend_recall')
            self.assertEqual(received[-1]['message_id'],received[-2]['message_id'])
            self.assertTrue((await self.driver.event(withdrawn))['recalled'])
        self.assertEqual(len(received),4)

    async def test_unread_media_is_preserved_instead_of_being_dropped(self):
        result=await self.driver.event({**record(),'elements':[
            {'marketFaceElement':{'faceName':'[笑哭]'}},{'pttElement':{'duration':2}},
            {'fileElement':{'fileName':'note.pdf'}},{'multiForwardMsgElement':{'resId':'1'}},
            {'arkElement':{'bytesData':'{}'}}]})
        parsed=parse_message(result['message'])
        self.assertIn('笑哭',parsed['text'])
        self.assertEqual([r['type'] for r in parsed['segments']],['image','record','file','forward','share'])

    async def test_market_face_reaches_image_input_when_qq_cache_is_not_ready(self):
        face={**record(),'elements':[{'elementId':'11','marketFaceElement':{
            'emojiId':'38319f9443394264a1f44801fe2a84f0','faceName':'[不错呦]'}}]}
        request=httpx.Request('POST','http://localhost/rpc')
        self.driver.rpc=AsyncMock(side_effect=httpx.HTTPStatusError('cache missing',request=request,
            response=httpx.Response(502,request=request)))
        received=await self.driver.event(face,images=True)
        self.assertEqual(received['message'][0]['data']['url'],
            'https://gxh.vip.qq.com/club/item/parcel/item/38/38319f9443394264a1f44801fe2a84f0/300x300.png')
        out=io.BytesIO();Image.new('RGB',(8,8),'green').save(out,'PNG')
        client=type('Client',(),{'message':AsyncMock(return_value=received)})()
        parsed=parse_message(received['message'])
        self.assertEqual(parsed['text'],'[表情包：不错呦]')
        rows=[{'message_id':received['message_id'],**parsed}]
        with patch('miko.adapters.qq.images.download',return_value=out.getvalue()):
            await attach_images(client,('12345','67890'),rows)
        self.assertEqual(rows[0]['segments'][0]['content_status'],'attached')
        self.assertEqual(rows[0]['_image_inputs'][1]['type'],'image_url')

    def test_qq_image_dns_handles_mixed_proxy_benchmark_addresses(self):
        fake=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('198.18.0.9',443)),
              (socket.AF_INET6,socket.SOCK_STREAM,6,'',('2001:2::c',443,0,0))]
        def respond(request):
            self.assertEqual(request.url.host,'dns.alidns.com')
            self.assertEqual(request.url.params['name'],'gxh.vip.qq.com')
            return httpx.Response(200,json={'Answer':[{'type':1,'data':'1.2.3.4'}]})
        client=httpx.Client(transport=httpx.MockTransport(respond))
        with patch('miko.adapters.qq.images.socket.getaddrinfo',return_value=fake), \
                patch('miko.adapters.qq.images.httpx.Client',return_value=client):
            self.assertEqual(public_addresses('gxh.vip.qq.com',443)[0][4],('1.2.3.4',443))
        with patch('miko.adapters.qq.images.socket.getaddrinfo',return_value=[
                (socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError):public_addresses('gxh.vip.qq.com',443)


if __name__ == '__main__':
    unittest.main()
