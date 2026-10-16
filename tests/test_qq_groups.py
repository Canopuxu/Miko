import asyncio
import json
import time
import base64
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import httpx
from fastapi import FastAPI

from test_qq_replies import FakeChat, FakeClient
from miko.storage.chat import ChatStore
from miko.domain.conversation.qq import QqService
from miko.adapters.qq.client_driver import NativeClientDriver
from miko.adapters.memory.tulpa import Projection
from miko.adapters.memory.structured import StructuredMemory
from miko.adapters.memory.service import Memory
from miko.settings import Settings
from miko.api.qq import qq_router
from miko.adapters.qq.images import attach_images
from miko.adapters.qq.native import NativeQq
from miko.adapters.qq.onebot import QqConfig
from PIL import Image


class GroupRepliesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.chat = FakeChat(ChatStore(self.root / 'chat.sqlite3'))
        self.client = FakeClient()
        self.now = 1000
        self.service = QqService(self.chat, None, client=self.client, clock=lambda: self.now)
        self.service.receiver.state.update(state='connected', account='123')
        self.key = ('123', 'group-900')
        self.service.store.add_friend(*self.key, '朋友群', chat_type=2)
        await self.service.configure(self.key, True)

    async def asyncTearDown(self):
        await self.service.close()
        self.tmp.cleanup()

    def event(self, mid, text='下班了', sender=456, group=900, extra=None):
        return {'post_type':'message_sent' if sender==123 else 'message', 'message_type':'group',
            'self_id':123,'group_id':group,'user_id':sender,'message_id':mid,'time':self.now,
            'sender':{'user_id':sender,'nickname':'同名','card':'群名片'+str(sender)},
            'message':(extra or [])+[{'type':'text','data':{'text':text}}]}

    async def reply(self):
        self.now += 2
        await self.service.tick()
        await asyncio.wait_for(asyncio.gather(*(w.task for w in self.service.workers.values())), 3)

    async def test_direct_mention_combines_people_and_uses_native_at(self):
        await self.service.receive(self.event(1, '今天好累'))
        self.assertIsNone(self.service.store.friend(self.key)['due_at'])
        await self.service.receive(self.event(2, '你晚上吃啥', sender=789,
            extra=[{'type':'at','data':{'qq':'123'}}]))
        await self.reply()
        records=self.chat.calls[-1]
        self.assertEqual([r['author']['id'] for r in records], ['qq:456','qq:789'])
        self.assertEqual(records[-1]['author']['name'],'群名片789')
        self.assertEqual(self.client.sent[-1][0],'group-900')
        self.assertEqual(self.client.payloads[-1][0]['type'],'at')
        self.assertEqual(self.client.payloads[-1][0]['data']['qq'],'789')
        self.assertEqual(self.service.store.messages(self.key)[-1]['author']['kind'],'miko')

    async def test_name_quote_silence_and_natural_chat_cooldown(self):
        self.chat.bubbles=[]
        await self.service.receive(self.event(1,'miko，刚刚在聊电影'))
        await self.reply()
        self.assertEqual(self.service.store.runs(self.key)[0]['state'],'skipped')
        self.assertEqual(self.client.sent,[])
        self.service.store.update_friend(self.key,natural_chat=1)
        await self.service.receive(self.event(2,'周末看电影去不去'))
        self.assertGreater(self.service.store.friend(self.key)['due_at'],self.now+20)
        self.service.store.confirm_event(self.key,800,'我也去',self.now,{'segments':[]})
        self.service.store.record(self.key,800,'miko')
        await self.service.receive(self.event(3,'几点',extra=[{'type':'reply','data':{'id':'800'}}]))
        self.assertLessEqual(self.service.store.friend(self.key)['due_at'],self.now+2)

    async def test_withdrawal_cancels_pending_reply_and_excludes_recalled_context(self):
        await self.service.receive(self.event(1,'Miko，撤回这句'))
        self.chat.gate=asyncio.Event()
        self.now+=2
        await self.service.tick()
        while not self.chat.calls:await asyncio.sleep(0)
        worker=self.service.workers[self.key]
        await self.service.receive({'post_type':'notice','notice_type':'group_recall',
            'self_id':123,'group_id':900,'user_id':456,'message_id':1})
        await worker.task
        self.assertEqual(self.client.sent,[])
        self.assertIsNone(self.service.store.friend(self.key)['due_at'])
        self.assertEqual(self.service.store.context(self.key),[])
        self.assertTrue(self.service.store.messages(self.key)[0]['recalled'])
        self.chat.gate=None
        await self.service.receive(self.event(2,'Miko，还在吗'))
        await self.reply()
        self.assertEqual(len(self.client.sent),1)

    async def test_unknown_disabled_groups_and_other_conversations_are_independent(self):
        await self.service.receive(self.event(1,'miko',group=901))
        self.assertEqual(self.service.store.context(self.key),[])
        other=('123','group-902')
        self.service.store.add_friend(*other,'另一个群',chat_type=2)
        await self.service.configure(other,True)
        await self.service.receive(self.event(2,'miko',group=902))
        await self.service.receive(self.event(3,'miko'))
        before=self.service.versions.get(other,0)
        await self.service.receive(self.event(4,'我来回复',sender=123))
        self.assertEqual(self.service.versions.get(other,0),before)
        self.assertIsNotNone(self.service.store.friend(other)['due_at'])
        self.assertIsNone(self.service.store.friend(self.key)['due_at'])
        await self.service.configure(self.key,False)
        await self.service.receive(self.event(5,'miko'))
        self.assertIsNone(self.service.store.get_event(self.key,5))

    async def test_group_projection_preserves_people_and_retrieval_scope(self):
        await self.service.receive(self.event(1,'我喜欢爵士乐'))
        await self.service.receive(self.event(2,'我比较喜欢摇滚',sender=789))
        projection=Projection(self.root/'memory.sqlite3',self.chat.store,structured=True)
        memory=StructuredMemory(projection)
        with projection.connect() as db:
            people=[r['sender_id'] for r in db.execute('SELECT * FROM messages ORDER BY id')]
            self.assertEqual(people,['qq:456','qq:789'])
            self.assertEqual(db.execute('SELECT kind FROM tulpa_scopes').fetchone()[0],'group')
            for peer,text in [('', '私聊资料'),('qq:123:group-900','群内资料'),('qq:123:group-902','其他群资料')]:
                db.execute('''INSERT INTO memory_items(id,subject,peer,category,topic,automatic,valid,enabled,updated_at,scope_id)
                    VALUES(?, 'qq:456',?,'兴趣','音乐',?,1,1,1,1)''',(text,peer,text))
        rows=memory.items(peer='qq:123:group-900',active=True)
        self.assertEqual([r['content'] for r in rows],['群内资料'])

    async def test_group_selection_api_starts_disabled_and_has_own_options(self):
        self.client.config=SimpleNamespace(account='123')
        async def call(action,payload):
            self.assertEqual(action,'get_group_list')
            return [{'group_id':'900','group_name':'朋友群'},{'group_id':'901','group_name':'测试群'}]
        self.client.call=call
        app=FastAPI();app.include_router(qq_router(self.service))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://127.0.0.1') as client:
            listing=await client.get('/api/qq/groups')
            self.assertEqual(listing.status_code,200)
            self.assertTrue(listing.json()['groups'][0]['added'])
            response=await client.post('/api/qq/groups',json={'group_ids':['901']})
            self.assertEqual(response.status_code,200)
            self.assertFalse(self.service.store.friend(('123','group-901'))['enabled'])
            response=await client.patch('/api/qq/friends/123/group-901/options',json={'natural_chat':True})
            self.assertEqual(response.status_code,200)
            self.assertTrue(self.service.store.friend(('123','group-901'))['natural_chat'])

    async def test_unified_memory_extracts_members_from_participating_group_context(self):
        self.now=time.time()
        original_send=self.client.send
        async def send(*args):
            result=await original_send(*args)
            return {**result,'timestamp':self.now}
        self.client.send=send
        (self.root/'config').mkdir()
        (self.root/'config/memory.local.json').write_text('{"engine":"memobase"}',encoding='utf-8')
        self.chat.settings=Settings(self.root,7870)
        self.chat.provider=SimpleNamespace(model='test')
        calls=[]
        async def complete(system,message,model=None,json_output=False):
            calls.append(message)
            return {'text':json.dumps({'facts':[
                {'subject':'qq:456','topic':'喜好','sub_topic':'音乐','memo':'爵士乐'},
                {'subject':'qq:789','topic':'喜好','sub_topic':'音乐','memo':'摇滚'}], 'events':[]}), 'usage':{}}
        self.chat.complete_memory=complete
        memory=Memory(self.chat);memory.social.loop=asyncio.get_running_loop()
        with memory.social.store.connect() as db:
            db.execute('UPDATE tulpa_control SET since=0')
        try:
            await self.service.receive(self.event(1,'我喜欢爵士乐'))
            await self.service.receive(self.event(2,'我更喜欢摇滚，miko呢',sender=789))
            await self.reply()
            memory.social.store.sync()
            sid=memory.social.listing()['scopes'][0]['id']
            result=await asyncio.to_thread(memory.social.consolidate,sid,None,True)
            self.assertEqual(result['status'],'updated')
            self.assertIn('qq:456',calls[0]);self.assertIn('qq:789',calls[0])
            rows=memory.structured.items(peer='qq:123:group-900',active=True)
            self.assertEqual({r['subject'] for r in rows},{'qq:456','qq:789'})
            self.assertTrue(all(r['peer']=='qq:123:group-900' for r in rows))
            self.assertEqual(memory.structured.items(peer='qq:123:group-901',active=True),[])
        finally:
            await memory.close()


class GroupNativeTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_image_uses_group_identity_and_reaches_model_input(self):
        with TemporaryDirectory() as folder:
            config=QqConfig(account='123',http_url='http://127.0.0.1:1',ws_url='ws://127.0.0.1:1')
            client=NativeQq(config,{'account':'123','port':1,'token':'test'},Path(folder)/'native.sqlite3')
            buffer=io.BytesIO();Image.new('RGB',(8,8),'green').save(buffer,format='PNG')
            encoded=base64.b64encode(buffer.getvalue()).decode()
            native=dict(chatType=2,peerUin='900',msgId='image',senderUin='456',msgTime='1000',
                elements=[{'elementId':'1','picElement':{'fileName':'pic.png'}}])
            async def rpc(action,payload=None):
                if action=='get_login_info':return {'user_id':123}
                self.assertEqual(payload['group_id'],'900')
                if action=='get_native_message':return native
                self.assertEqual(action,'read_image');return {'base64':encoded}
            client.driver.rpc=rpc
            try:
                mid=client.driver.remember(native)
                rows=[{'message_id':mid,'segments':[{'type':'image'}]}]
                await attach_images(client,('123','group-900'),rows)
                self.assertEqual(rows[0]['segments'][0]['content_status'],'attached')
                self.assertTrue(rows[0]['_image_inputs'][1]['image_url']['url'].startswith('data:image/jpeg;base64,'))
            finally:
                await client.close()

    async def test_group_ids_mentions_and_member_quotes_do_not_collide_with_private(self):
        with TemporaryDirectory() as folder:
            driver=NativeClientDriver({'account':'123','port':1,'token':'test'},Path(folder)/'native.sqlite3')
            record=dict(chatType=2,peerUin='900',msgId='same-native-id',senderUin='456',senderUid='u_member',
                msgTime='1000',msgSeq='10',sendNickName='昵称',sendMemberName='名片',elements=[
                    {'textElement':{'content':'@Miko','atType':2,'atUid':'123'}}])
            try:
                event=await driver.event(record)
                private=driver.remember({**record,'chatType':1,'peerUin':'456'})
                self.assertNotEqual(event['message_id'],private)
                self.assertEqual(event['message_type'],'group')
                self.assertEqual(event['group_id'],900)
                self.assertEqual(event['sender']['card'],'名片')
                self.assertEqual(event['message'][0]['type'],'at')
                quoted={**record,'msgId':'quote-id','msgSeq':'11','elements':[{'elementId':'7','replyElement':{
                    'senderUid':'u_member','replayMsgSeq':'10','replyMsgTime':'1000'}}]}
                reply=await driver.event(quoted)
                self.assertEqual(reply['message'][0]['data']['id'],str(event['message_id']))
                quoted['elements'][0]['replyElement'].update(senderUid='0',senderUidStr='u_member')
                reply=await driver.event(quoted)
                self.assertEqual(reply['message'][0]['data']['id'],str(event['message_id']))
                delivered=[]
                await driver._publish_native({'name':'message','payload':{'msgList':[record]}},lambda e:delivered.append(e))
                self.assertFalse(delivered)
            finally:
                await driver.close()
