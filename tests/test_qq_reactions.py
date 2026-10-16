import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from test_qq_replies import FakeChat, FakeClient
from miko.adapters.qq.onebot import SendUncertain
from miko.domain.conversation.qq import QqService
from miko.storage.chat import ChatStore
from miko.storage.qq import QqStore


class ReactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = TemporaryDirectory()
        self.chat = FakeChat(ChatStore(Path(self.tmp.name) / 'chat.sqlite3'))
        self.client = FakeClient()
        self.client.capabilities = {'message_reactions': True}
        self.writes = []
        self.uncertain = False
        self.now = 1000
        self.service = QqService(self.chat, None, client=self.client, clock=lambda: self.now)
        self.service.receiver.state.update(state='connected', account='123')
        self.key = ('123', 'group-900')
        self.service.store.add_friend(*self.key, '测试群', chat_type=2)
        await self.service.configure(self.key, True)
        self.service.reactions.core.policy_path.write_text('{"cooldown_seconds":0}', encoding='utf-8')

        async def login(): return '123'
        async def call(action, payload, **kwargs):
            if action == 'get_msg':
                return dict(message_id=payload['message_id'], self_id='123', message_type='group', group_id='900',
                    sender=dict(user_id='456'), time=1000, message_seq=19, sequence_authoritative=True)
            self.assertEqual(action, 'set_msg_emoji_like')
            self.assertTrue(kwargs['sending'])
            self.writes.append(payload)
            if self.uncertain: raise SendUncertain('unknown')
            return {}
        self.client.login = login
        self.client.call = call
        await self.service.receive(dict(post_type='message', message_type='group', self_id=123, group_id=900,
            user_id=456, message_id=2**40, time=1000, sender=dict(nickname='群友'),
            message=[dict(type='text', data=dict(text='Miko，我先走啦'))]))

    async def asyncTearDown(self):
        await self.service.close()
        self.tmp.cleanup()

    def bubble(self, operation='add'):
        return dict(kind='reaction', message_id=2**40, reaction_id='unicode_thumbsup', operation=operation)

    async def reply(self):
        self.chat.bubbles = [self.bubble()]
        self.now += 2
        await self.service.tick()
        await asyncio.wait_for(asyncio.gather(*(w.task for w in self.service.workers.values())), 4)

    async def test_reaction_only_receipt_without_chat_message_and_recovery(self):
        await self.reply()
        run = self.service.store.runs(self.key)[0]
        self.assertEqual(run['state'], 'sent')
        self.assertIsNone(run['receipt_id'])
        self.assertEqual(self.writes, [dict(message_id=2**40, emoji_id='128077', set=True)])
        self.assertEqual(self.client.sent, [])
        self.assertEqual(len(self.service.store.messages(self.key)), 1)
        QqStore(self.chat.store).recover()
        self.assertEqual(len(self.service.store.messages(self.key)), 1)
        self.assertEqual(len(self.service.reactions.states(self.key)), 1)

    async def test_add_remove_and_identical_operation_is_noop(self):
        async def apply(operation, key):
            return await self.service.reactions.apply(self.key, self.bubble(operation), key, lambda: None)
        self.assertEqual((await apply('add', 'one'))['state'], 'SUCCEEDED')
        self.assertEqual((await apply('add', 'two'))['state'], 'NOOP')
        self.assertEqual((await apply('remove', 'three'))['state'], 'SUCCEEDED')
        self.assertEqual([r['set'] for r in self.writes], [True, False])
        self.assertEqual(self.service.reactions.states(self.key), [])

    async def test_unknown_result_blocks_repeat_and_following_text(self):
        self.uncertain = True
        await self.reply()
        self.assertEqual(self.service.store.runs(self.key)[0]['state'], 'unknown')
        self.assertFalse(self.service.store.friend(self.key)['blocked'])
        self.service.store.update_friend(self.key, blocked=0)
        result = await self.service.reactions.apply(self.key, self.bubble(), 'new-key', lambda: None)
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.client.sent, [])

    async def test_recalled_or_previous_connection_target_cannot_dispatch(self):
        self.service.store.recall(self.key, 2**40)
        result = await self.service.reactions.apply(self.key, self.bubble(), 'recalled', lambda: None)
        self.assertEqual(result['state'], 'REJECTED')
        self.service.reactions.reset()
        self.assertEqual(self.service.reactions.catalog(self.key, self.service.store.context(self.key)), ([], []))
        self.assertEqual(self.writes, [])
