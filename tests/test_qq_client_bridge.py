"""Transport checks only; the fake driver is not a QQ integration test."""
import unittest

import httpx
from fastapi.testclient import TestClient

from miko.adapters.qq.bridge import ClientBridge
from miko.adapters.qq.onebot import OneBot, QqConfig, SendUncertain


class Driver:
    def __init__(self):
        self.account = 12345
        self.sends = 0
        self.receipt = {'message_id': 901}

    async def call(self, action, payload):
        if action == 'get_login_info':
            return {'user_id': self.account}
        if action == 'get_friend_list':
            return [{'user_id': 67890, 'nickname': 'test'}]
        if action == 'send_private_msg':
            self.sends += 1
            return self.receipt
        raise NotImplementedError(action)


class BridgeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.driver = Driver()
        self.bridge = ClientBridge(self.driver, '12345')
        self.client = OneBot(QqConfig(account='12345', http_url='http://127.0.0.1',
            ws_url='ws://127.0.0.1', http_token=self.bridge.token),
            transport=httpx.ASGITransport(app=self.bridge.app))

    async def test_existing_sender_uses_receipt(self):
        result = await self.client.send('12345', '67890', 'hello', lambda: None)
        self.assertEqual(result['message_id'], 901)
        self.assertEqual(self.driver.sends, 1)

    async def test_uncertain_send_is_not_repeated(self):
        self.driver.receipt = {}
        with self.assertRaises(SendUncertain):
            await self.client.send('12345', '67890', 'hello', lambda: None)
        self.assertEqual(self.driver.sends, 1)

    async def test_auth_and_account_guard(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.bridge.app),
                                    base_url='http://127.0.0.1') as client:
            self.assertEqual((await client.post('/get_login_info', json={})).status_code, 401)
            self.driver.account = 11111
            response = await client.post('/send_private_msg', json={'user_id': 67890},
                headers={'Authorization': 'Bearer ' + self.bridge.token})
            self.assertEqual(response.status_code, 502)
            self.assertEqual(self.driver.sends, 0)

    def test_friend_and_owner_events_and_disconnect_cleanup(self):
        with TestClient(self.bridge.app) as client:
            with client.websocket_connect('/', headers={'Authorization': 'Bearer ' + self.bridge.token}) as ws:
                self.assertEqual(ws.receive_json()['self_id'], 12345)
                for author in (67890, 12345):
                    event = {'self_id': 12345, 'post_type': 'message',
                             'message_type': 'private', 'user_id': author, 'message_id': author}
                    client.portal.call(self.bridge.publish, event)
                    received = ws.receive_json()
                    while received['post_type'] == 'meta_event':
                        received = ws.receive_json()
                    self.assertEqual(received, event)
        self.assertFalse(self.bridge.clients)


if __name__ == '__main__':
    unittest.main()
