"""One account avatar shared by overlapping reply batches."""
import asyncio
import logging


class ReplyAvatar:
    def __init__(self, client, miko_file, owner_file, *, restore_seconds=60):
        self.client = client
        self.miko_file, self.owner_file = miko_file, owner_file
        self.active = set()
        self.lock = asyncio.Lock()
        self.restore_seconds = restore_seconds
        self.restore_task = None
        self.showing_miko = False

    def cancel_restore(self):
        if self.restore_task:
            self.restore_task.cancel()
            self.restore_task = None

    async def restore(self):
        if not self.showing_miko:
            return
        try:
            await self.client.avatar(self.owner_file)
            self.showing_miko = False
        except Exception:
            logging.getLogger(__name__).error('QQ original avatar restore failed')

    async def restore_later(self):
        try:
            await asyncio.sleep(self.restore_seconds)
            async with self.lock:
                if not self.active:
                    await self.restore()
        finally:
            if self.restore_task is asyncio.current_task():
                self.restore_task = None

    async def acquire(self, reply_id):
        async with self.lock:
            self.cancel_restore()
            if not self.showing_miko:
                await self.client.avatar(self.miko_file)
                self.showing_miko = True
            self.active.add(reply_id)

    async def release(self, reply_id):
        async with self.lock:
            if reply_id not in self.active:
                return
            self.active.remove(reply_id)
            if not self.active:
                if self.restore_seconds:
                    self.restore_task = asyncio.create_task(self.restore_later())
                else:
                    await self.restore()

    async def interrupt(self, reply_id=None):
        async with self.lock:
            self.active.discard(reply_id)
            if not self.active:
                self.cancel_restore()
                await self.restore()

    async def close(self):
        async with self.lock:
            self.cancel_restore()
            self.active.clear()
            await self.restore()
