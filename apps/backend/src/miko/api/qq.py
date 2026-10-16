import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, StrictBool, Field

from miko.domain.conversation.qq import QqService
from miko.api.local import local_request


class FriendSwitch(BaseModel):
    enabled: StrictBool

class ReplyOptions(BaseModel):
    auto_reply_idle: StrictBool | None = None
    natural_chat: StrictBool | None = None

class GroupSelection(BaseModel):
    group_ids: list[str] = Field(min_length=1)


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

    async def group_list():
        if not service.client:
            raise HTTPException(400, '请先连接 QQ')
        try:
            rows = await service.client.call('get_group_list', {})
            account = service.client.config.account
            added = {r['friend'] for r in service.store.friends() if r['account'] == account}
            return account, [{**r, 'added': 'group-' + str(r['group_id']) in added} for r in rows]
        except ValueError:
            raise HTTPException(400, '读取群列表失败，请检查 QQ 连接') from None

    @router.get('/groups')
    async def available_groups():
        account, rows = await group_list()
        return {'account': account, 'groups': rows}

    @router.post('/groups')
    async def add_groups(body: GroupSelection):
        account, rows = await group_list()
        available = {str(r['group_id']): r for r in rows}
        if set(body.group_ids) - available.keys():
            raise HTTPException(400, '所选群不在当前账号的群列表中，请刷新后重试')
        for gid in body.group_ids:
            service.store.add_friend(account, 'group-' + gid, available[gid]['group_name'], chat_type=2)
        return {'message': '群聊已添加，请选择群聊并开启接话'}

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
        fields = {key: int(value) for key, value in body.model_dump(exclude_none=True).items()}
        if not fields:
            return service.status()
        if cfg['chat_type'] == 2 and 'natural_chat' in fields and not fields['natural_chat']:
            await service.invalidate(key)
            fields.update(pending_since=None, pending_last=None, due_at=None)
        if body.auto_reply_idle is False and cfg['mode'] == 'owner':
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
