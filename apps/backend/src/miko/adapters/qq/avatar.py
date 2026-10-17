"""One account avatar shared by overlapping reply batches."""
import asyncio
import logging
import json
from pathlib import Path
import shutil

import httpx

from miko.adapters.qq.media import image_info, MAX_BYTES
from miko.settings import REPO_ROOT, write_json


async def current_qq_avatar(account):
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, trust_env=False) as client:
            async with client.stream('GET', 'https://q1.qlogo.cn/g', params={'b':'qq','nk':account,'s':'640'}) as response:
                response.raise_for_status()
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError('头像图片过大')
                    chunks.append(chunk)
        raw = b''.join(chunks)
        image_info(raw)
        return raw
    except (httpx.HTTPError, ValueError):
        raise ValueError('未能保存当前 QQ 头像，请检查网络后重试') from None


def avatar_files(root, saved):
    default = REPO_ROOT / 'config/defaults/qq-avatar.png'
    custom = root / 'data/qq/avatars/role.image'
    role = next((p for p in [custom, Path(saved.get('miko_avatar_file') or default), default] if p.is_file()), default)
    uid = str(saved.get('account', ''))
    if not uid:
        return role, None
    if not uid.isascii() or not uid.isdigit() or int(uid) <= 0:
        raise ValueError('请先连接 QQ 账号')
    directory = root / 'data/qq/avatars' / uid
    if not custom.is_file() and not saved.get('miko_avatar_file') and (directory / 'role.image').is_file():
        role = directory / 'role.image'
    owner = Path(saved.get('owner_avatar_file') or directory / 'owner.image')
    return role, owner if owner.is_file() else None


async def initialize_reply_avatar(qq, root):
    path = root / 'config/qq.local.json'
    client = qq.client
    if not client or qq.avatar or not path.is_file():
        return
    saved = json.loads(path.read_text('utf-8-sig'))
    if not saved.get('avatar_switch_enabled', True) or saved.get('account') != client.config.account:
        return
    _, owner = avatar_files(root, saved)
    raw = await current_qq_avatar(saved['account']) if not owner else None
    latest = json.loads(path.read_text('utf-8-sig'))
    if qq.client is not client or not latest.get('avatar_switch_enabled', True) or latest.get('account') != saved['account']:
        return
    role, owner = avatar_files(root, latest)
    directory = root / 'data/qq/avatars' / saved['account']
    directory.mkdir(parents=True, exist_ok=True)
    if not owner:
        owner = directory / 'owner.image'
        owner.write_bytes(raw)
    for source, name in [(role, 'role.image'), (owner, 'owner.image')]:
        target = directory / name
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
    update = {'avatar_switch_enabled':True,'miko_avatar_file':str(directory/'role.image'),
              'owner_avatar_file':str(directory/'owner.image')}
    write_json(path, {**latest, **update})
    client.config = client.config.model_copy(update=update)
    qq.avatar = ReplyAvatar(client, update['miko_avatar_file'], update['owner_avatar_file'])


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
