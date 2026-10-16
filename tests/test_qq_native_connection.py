import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock

import httpx

from miko.adapters.qq.native import NativeQq, NativeReceiver, create_client
from miko.adapters.qq.onebot import QqConfig, SendError, SendUncertain
from miko.settings import write_json


class DirectConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=TemporaryDirectory();self.root=Path(self.tmp.name)
        self.config=QqConfig(account='12345',http_url='http://127.0.0.1:45001',ws_url='ws://127.0.0.1:45001/',
                             http_token='fixture-private',ws_token='fixture-private')
        self.client=NativeQq(self.config,{'account':'12345','token':'fixture-private','port':45001},self.root/'native.sqlite3')

    async def asyncTearDown(self):
        await self.client.close();self.tmp.cleanup()

    async def test_send_uses_native_receipt_without_onebot_endpoint(self):
        calls=[]
        def respond(request):
            self.assertEqual(request.url.path,'/rpc')
            body=json.loads(request.content);calls.append(body['action'])
            if body['action']=='get_login_info':result={'user_id':12345}
            elif body['action']=='get_friend_list':result=[{'user_id':67890,'nickname':'好友'}]
            elif body['action']=='send_native':
                result={'chatType':1,'msgId':'native-1','peerUin':'67890','senderUin':'12345',
                        'msgTime':'1791950400','sendStatus':2,'elements':body['payload']['elements']}
            else:self.fail(body['action'])
            return httpx.Response(200,json={'result':result})
        await self.client.driver.http.aclose()
        self.client.driver.http=httpx.AsyncClient(base_url=self.config.http_url,transport=httpx.MockTransport(respond))
        self.client.driver.receiving=True
        receipt=await self.client.send('12345','67890','hello',lambda:None)
        self.assertGreater(receipt['message_id'],2147483647)
        self.assertEqual(calls.count('send_native'),1)
        self.client.driver.receiving=False
        with self.assertRaises(SendUncertain):
            await self.client.send('12345','67890','not sent',lambda:None)
        self.assertEqual(calls.count('send_native'),1)

    async def test_changed_account_and_failed_send_never_resend(self):
        self.client.driver.rpc=AsyncMock(return_value={'user_id':99999})
        self.client.driver.call=AsyncMock()
        with self.assertRaises(SendError):await self.client.login()
        self.client.driver.call.assert_not_awaited()
        self.client.driver.rpc=AsyncMock(return_value={'user_id':12345})
        self.client.driver.call=AsyncMock(side_effect=OSError('fixture'))
        with self.assertRaises(SendUncertain):
            await self.client.call('send_private_msg',{},sending=True)
        self.client.driver.call.assert_awaited_once()

    async def test_same_session_replay_gap_pauses_instead_of_skipping_messages(self):
        receiver=NativeReceiver(self.client,AsyncMock(),AsyncMock())
        self.client.driver.event_session='session-a'
        self.client.driver.rpc=AsyncMock(side_effect=lambda action: {'session':'session-a'} if action=='diagnostics' else {'user_id':12345})
        request=httpx.Request('GET','http://localhost/events')
        error=httpx.HTTPStatusError('gap',request=request,response=httpx.Response(409,request=request))
        self.client.driver.listen=AsyncMock(side_effect=error)
        await asyncio.wait_for(receiver.run(),1)
        self.assertEqual(receiver.status()['state'],'blocked')
        self.client.driver.listen.assert_awaited_once()

    async def test_existing_managed_config_loads_direct_driver(self):
        (self.root/'config').mkdir()
        write_json(self.root/'config/qq-client.local.json',{'account':'12345','token':'fixture-private','port':45001})
        legacy=self.config.model_copy(update={'http_url':'http://127.0.0.1:17972','ws_url':'ws://127.0.0.1:17972/'})
        loaded=create_client(legacy,self.root)
        try:self.assertIsInstance(loaded,NativeQq)
        finally:await loaded.close()


if __name__=='__main__':unittest.main()
