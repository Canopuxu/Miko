import asyncio
import unittest
from unittest.mock import AsyncMock

from miko.adapters.qq.avatar import ReplyAvatar
import test_qq_replies as fixtures


class AvatarTests(unittest.IsolatedAsyncioTestCase):
    async def test_overlapping_replies_keep_avatar_until_last_release(self):
        client = AsyncMock()
        avatar = ReplyAvatar(client, 'miko.png', 'owner.png', restore_seconds=0)
        await asyncio.gather(avatar.acquire('a'), avatar.acquire('b'))
        self.assertEqual(client.avatar.await_count, 1)
        await avatar.release('a')
        self.assertEqual(client.avatar.await_count, 1)
        await avatar.release('b')
        self.assertEqual([c.args[0] for c in client.avatar.await_args_list], ['miko.png', 'owner.png'])

    async def test_service_interrupt_only_releases_affected_friend(self):
        fixture = fixtures.QqRepliesTests()
        await fixture.asyncSetUp()
        try:
            service, client = fixture.service, fixture.client
            client.avatar = AsyncMock()
            service.avatar = ReplyAvatar(client, 'miko.png', 'owner.png', restore_seconds=0)
            service.store.add_friend('123', '789', '第二位好友')
            await service.configure(('123', '789'), True)
            client.gate = asyncio.Event()
            await fixture.receive(1)
            await fixture.receive(2, friend='789')
            fixture.now = 1120
            await service.tick()
            tasks = [w.task for w in service.workers.values()]
            for _ in range(100):
                if len(service.avatar.active) == 2:
                    break
                await asyncio.sleep(.001)
            self.assertEqual(len(service.avatar.active), 2)
            await fixture.receive(3, '我来回', own=True)
            client.gate.set()
            await asyncio.gather(*tasks)
            self.assertEqual([friend for friend, _ in client.sent], ['789'])
            self.assertEqual([c.args[0] for c in client.avatar.await_args_list], ['miko.png', 'owner.png'])
        finally:
            await fixture.asyncTearDown()

    async def test_send_failure_restores(self):
        fixture = fixtures.QqRepliesTests()
        await fixture.asyncSetUp()
        try:
            service, client = fixture.service, fixture.client
            client.avatar = AsyncMock()
            service.avatar = ReplyAvatar(client, 'miko.png', 'owner.png', restore_seconds=0)
            client.uncertain = True
            await fixture.receive(2)
            fixture.now = 1120
            await fixture.due()
            self.assertEqual([c.args[0] for c in client.avatar.await_args_list], ['miko.png', 'owner.png'])
        finally:
            await fixture.asyncTearDown()
