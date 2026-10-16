from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from miko.api.local import local_request
from miko.storage.history import History


def history_router(store):
    router = APIRouter(prefix='/api/history', dependencies=[Depends(local_request)])
    history = History(store)

    @router.get('/search')
    def search(source: Literal['web', 'qq'], query: str = Query(default='', max_length=200),
               day: date | None = None, conversation_id: str = '', offset: int = Query(default=0, ge=0),
               snapshot: int | None = Query(default=None, ge=0)):
        if not query.strip() and day is None:
            raise HTTPException(400, '请输入关键词或选择日期')
        return history.search(source, query, day, conversation_id, offset, snapshot=snapshot)

    @router.get('/context')
    def context(source: Literal['web', 'qq'], conversation_id: str, message_id: str,
                before: int = Query(default=8, ge=0, le=30), after: int = Query(default=8, ge=0, le=30)):
        try:
            return history.context(source, conversation_id, message_id, before, after)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None

    return router
