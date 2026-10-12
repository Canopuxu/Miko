from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, StrictBool

from miko.domain.conversation.qq import QqService


class FriendSwitch(BaseModel):
    enabled: StrictBool


def qq_router(service: QqService):
    def local_request(request: Request):
        base = str(request.base_url).rstrip('/')
        if (urlparse(base).hostname not in ('127.0.0.1', 'localhost', '::1', 'testserver')
            or request.headers.get('origin') not in (None, base)
            or request.headers.get('sec-fetch-site') == 'cross-site'):
            raise HTTPException(403, '请从 Miko 本机页面访问')

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
        return {'friend': cfg, 'messages': service.chat.store.messages(cfg['conversation_id']),
                'runs': service.store.runs(key)}

    return router
