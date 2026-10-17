"""Avatar preferences over the existing QQ reply-avatar coordinator."""
import asyncio
import base64
import binascii
import json
from pathlib import Path
import shutil
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from miko.adapters.qq.avatar import ReplyAvatar
from miko.adapters.qq.media import image_info, MAX_BYTES
from miko.api.local import local_request
from miko.settings import REPO_ROOT, write_json


class AvatarSwitch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool


class AvatarImage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    image_base64: str = Field(max_length=8 * 1024 * 1024)


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
        raise HTTPException(400, '未能保存当前 QQ 头像，请先选择一张恢复头像，再开启切换') from None


def qq_avatar_router(chat, qq):
    router = APIRouter(prefix='/api/settings/qq/avatar', dependencies=[Depends(local_request)])
    root = chat.settings.local_root
    config_file = root / 'config/qq.local.json'
    default = REPO_ROOT / 'config/defaults/qq-avatar.png'
    lock = asyncio.Lock()

    def config():
        return json.loads(config_file.read_text('utf-8-sig')) if config_file.is_file() else {}

    def account(saved):
        value = str(saved.get('account', ''))
        if not value.isascii() or not value.isdigit() or int(value) <= 0:
            raise HTTPException(400, '请先连接 QQ 账号')
        return value

    def folder(saved):
        return root / 'data/qq/avatars' / account(saved)

    def files(saved):
        if not saved.get('account'):
            return default, None
        directory = folder(saved)
        role = next((p for p in [Path(saved.get('miko_avatar_file') or directory / 'role.image'), default] if p.is_file()), default)
        owner = Path(saved.get('owner_avatar_file') or directory / 'owner.image')
        return role, owner if owner.is_file() else None

    def connected(saved):
        return bool(qq.client and qq.client.config.account == saved.get('account')
                    and qq.receiver and qq.receiver.status()['state'] == 'connected')

    def enabled(saved):
        return bool(saved.get('miko_avatar_file') and saved.get('owner_avatar_file'))

    def public():
        saved = config()
        role, owner = files(saved)
        return {'account':saved.get('account',''), 'enabled':enabled(saved), 'connected':connected(saved),
                'has_owner':owner is not None, 'using_default':role.read_bytes() == default.read_bytes()}

    def idle():
        if qq.workers:
            raise HTTPException(409, '正在接话，请回复结束后再修改头像')

    async def restore():
        if qq.avatar:
            await qq.avatar.close()
            if qq.avatar.showing_miko:
                raise HTTPException(400, '原头像未能恢复，请检查 QQ 连接后重试')

    def persist(saved, role, owner, active):
        directory = folder(saved)
        directory.mkdir(parents=True, exist_ok=True)
        # Keep both pictures when switching off, so enabling again needs no setup.
        for source, name in [(role, 'role.image'), (owner, 'owner.image')]:
            target = directory / name
            if source and source.resolve() != target.resolve():
                shutil.copyfile(source, target)
        role = directory / 'role.image'
        owner = directory / 'owner.image' if owner else None
        update = {'miko_avatar_file':str(role) if active else '', 'owner_avatar_file':str(owner) if active else ''}
        write_json(config_file, {**saved, **update})
        if qq.client and qq.client.config.account == saved['account']:
            qq.client.config = qq.client.config.model_copy(update=update)
        qq.avatar = ReplyAvatar(qq.client, str(role), str(owner)) if active and qq.client else None

    @router.get('')
    async def status():
        return public()

    @router.get('/image/{kind}')
    async def image(kind: Literal['role', 'owner']):
        role, owner = files(config())
        path = role if kind == 'role' else owner
        if not path:
            raise HTTPException(404, '尚未保存恢复头像')
        info = image_info(path.read_bytes())
        mime = {'PNG':'image/png','JPEG':'image/jpeg','GIF':'image/gif','WEBP':'image/webp'}[info['format']]
        return FileResponse(path, media_type=mime, headers={'Cache-Control':'private, no-store'})

    @router.put('')
    async def toggle(body: AvatarSwitch):
        async with lock:
            idle()
            saved = config()
            uid = account(saved)
            role, owner = files(saved)
            if body.enabled:
                if not connected(saved):
                    raise HTTPException(400, '请先连接这个 QQ 账号')
                if not owner:
                    raw = await current_qq_avatar(uid)
                    idle()
                    if config().get('account') != uid or not connected(saved):
                        raise HTTPException(409, 'QQ 账号已改变，请重新打开头像设置')
                    owner = folder(saved) / 'owner.image'
                    owner.parent.mkdir(parents=True, exist_ok=True)
                    owner.write_bytes(raw)
            await restore()
            persist(saved, role, owner, body.enabled)
            return public()

    @router.put('/image/{kind}')
    async def upload(kind: Literal['role', 'owner'], body: AvatarImage):
        try:
            raw = base64.b64decode(body.image_base64, validate=True)
            image_info(raw)
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(400, str(exc)) from None
        async with lock:
            idle()
            saved = config()
            directory = folder(saved)
            role, owner = files(saved)
            await restore()
            path = directory / ('role.image' if kind == 'role' else 'owner.image')
            directory.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            if kind == 'role':
                role = path
            else:
                owner = path
            persist(saved, role, owner, enabled(saved))
            return public()

    @router.post('/default')
    async def reset_role():
        async with lock:
            idle()
            saved = config()
            _, owner = files(saved)
            await restore()
            persist(saved, default, owner, enabled(saved))
            return public()

    return router
