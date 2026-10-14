"""One account avatar shared by overlapping reply batches."""
import asyncio
import logging


class ReplyAvatar:
    def __init__(self, client, miko_file, owner_file):
        self.client = client
        self.miko_file, self.owner_file = miko_file, owner_file
        self.active = set()
        self.lock = asyncio.Lock()

    async def acquire(self, reply_id):
        async with self.lock:
            if not self.active:
                await self.client.avatar(self.miko_file)
            self.active.add(reply_id)

    async def release(self, reply_id):
        async with self.lock:
            if reply_id not in self.active:
                return
            self.active.remove(reply_id)
            if not self.active:
                try:
                    await self.client.avatar(self.owner_file)
                except Exception:
                    logging.getLogger(__name__).error('QQ original avatar restore failed')
