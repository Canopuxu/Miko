import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image

from miko.adapters.qq.client_driver import NativeClientDriver
from miko.adapters.qq.images import attach_images


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


if __name__ == '__main__':
    unittest.main()
