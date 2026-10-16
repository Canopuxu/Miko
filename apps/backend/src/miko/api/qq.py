import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, StrictBool

from miko.domain.conversation.qq import QqService
from miko.api.local import local_request


class FriendSwitch(BaseModel):
    enabled: StrictBool

class ReplyOptions(BaseModel):
    auto_reply_idle: StrictBool


class StickerUpload(BaseModel):
    image_base64: str
    description: str


class StickerEdit(BaseModel):
    description: str
    enabled: StrictBool


def qq_router(service: QqService):
    router = APIRouter(prefix='/api/qq', dependencies=[Depends(local_request)])

    @router.get('/status')
    async def status():
        return service.status()

    @router.put('/friends/{account}/{friend}')
    async def configure(account: str, friend: str, body: FriendSwitch):
        try:
            return await service.configure((account, friend), body.enabled)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get('/friends/{account}/{friend}')
    async def detail(account: str, friend: str):
        key = (account, friend)
        cfg = service.store.friend(key)
        if not cfg:
            raise HTTPException(404, '找不到这位好友')
        return {'friend': cfg, 'messages': service.store.messages(key),
                'runs': service.store.runs(key)}

    @router.patch('/friends/{account}/{friend}/options')
    async def options(account: str, friend: str, body: ReplyOptions):
        key = (account, friend)
        cfg = service.store.friend(key)
        if not cfg:
            raise HTTPException(404, '找不到这位好友')
        fields = {'auto_reply_idle': int(body.auto_reply_idle)}
        if not body.auto_reply_idle and cfg['mode'] == 'owner':
            fields.update(pending_since=None, pending_last=None, due_at=None)
        service.store.update_friend(key, **fields)
        return service.status()

    def known_account(account):
        if not any(f['account'] == account for f in service.store.friends()):
            raise HTTPException(404, '找不到这个 QQ 账号')

    @router.get('/stickers/{account}')
    async def stickers(account: str):
        known_account(account)
        return service.stickers.list(account)

    @router.post('/stickers/{account}')
    async def upload(account: str, body: StickerUpload):
        known_account(account)
        try:
            if len(body.image_base64) > 8 * 1024 * 1024:
                raise ValueError('图片超过 6 MiB 上限')
            raw = base64.b64decode(body.image_base64, validate=True)
            return {'sticker_id': service.stickers.add(account, raw, body.description)}
        except (ValueError, binascii.Error) as exc:
            raise HTTPException(400, str(exc)) from None

    @router.put('/stickers/{account}/{digest}')
    async def edit(account: str, digest: str, body: StickerEdit):
        known_account(account)
        try:
            service.stickers.edit(account, digest, body.description, body.enabled)
            return {'saved': True}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get('/stickers/{account}/{digest}/image')
    async def image(account: str, digest: str):
        known_account(account)
        try:
            raw, info = service.stickers.read(account, digest, require_enabled=False)
            mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'GIF': 'image/gif', 'WEBP': 'image/webp'}[info['format']]
            return Response(raw, media_type=mime, headers={'Cache-Control': 'private, no-cache'})
        except (ValueError, OSError):
            raise HTTPException(404, '表情原文件不可用') from None

    return router
