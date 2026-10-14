import asyncio
import unittest
from unittest.mock import AsyncMock

from miko.adapters.qq.avatar import ReplyAvatar


class AvatarHoldTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_during_hold_reuses_avatar_then_manual_restores(self):
        client = AsyncMock()
        avatar = ReplyAvatar(client, 'miko.png', 'owner.png')
        await avatar.acquire('a')
        await avatar.release('a')
        self.assertTrue(avatar.showing_miko)
        self.assertIsNotNone(avatar.restore_task)
        await avatar.acquire('b')
        self.assertIsNone(avatar.restore_task)
        await avatar.acquire('c')
        await avatar.interrupt('b')
        self.assertEqual(client.avatar.await_count, 1)
        await avatar.release('c')
        await avatar.interrupt()
        self.assertEqual([c.args[0] for c in client.avatar.await_args_list], ['miko.png', 'owner.png'])
        self.assertIsNone(avatar.restore_task)

    async def test_hold_expiration_and_shutdown_restore(self):
        client = AsyncMock()
        avatar = ReplyAvatar(client, 'miko.png', 'owner.png', restore_seconds=.01)
        await avatar.acquire('a')
        await avatar.release('a')
        await asyncio.wait_for(avatar.restore_task, 1)
        self.assertFalse(avatar.showing_miko)
        await avatar.acquire('b')
        await avatar.close()
        self.assertFalse(avatar.showing_miko)
        self.assertEqual(client.avatar.await_count, 4)
