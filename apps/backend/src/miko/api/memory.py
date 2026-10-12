import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictBool

from miko.api.local import local_request


class Control(BaseModel):
    enabled: StrictBool


class Correction(BaseModel):
    text: str
    revision: int


def memory_router(memory):
    router = APIRouter(prefix='/api/memory', dependencies=[Depends(local_request)])

    @router.get('')
    async def listing(q: str = ''):
        await asyncio.to_thread(memory.social.store.sync)
        return {**memory.social.listing(q), 'index': memory.status()}

    @router.post('/control')
    async def control(body: Control):
        result = memory.social.toggle(body.enabled)
        await memory.sync()
        return result

    @router.get('/scopes/{sid}')
    async def detail(sid: int):
        try:
            result = memory.social.detail(sid)
            ids = set(result['sources'])
            for episode in result['episodes']:
                for field in ('incoming', 'outgoing', 'context'):
                    ids.update(json.loads(episode[field]))
            if not ids:
                with memory.social.store.connect() as db:
                    ids.update(r[0] for r in db.execute('SELECT id FROM messages WHERE conversation_id=? ORDER BY timestamp DESC,id DESC LIMIT 20', (result['conversation_id'],)))
            result['source_messages'] = memory.social.sources(sid, ids)
            return result
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None

    @router.patch('/scopes/{sid}')
    async def edit(sid: int, body: Correction):
        try:
            result = memory.social.edit(sid, body.text, body.revision)
            subject, cid = result['conversation_id'].split('|', 1)
            if cid.startswith('qq:'):
                _, account, friend = cid.split(':')
                with memory.chat.store.connect() as db:
                    db.execute(f'UPDATE qq_friends SET {subject}_relationship=? WHERE account=? AND friend=?',
                               (body.text.strip(), account, friend))
            await memory.sync()
            return result
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post('/scopes/{sid}/retry')
    async def retry(sid: int):
        with memory.social.store.connect() as db:
            db.execute('UPDATE tulpa_scopes SET retry_at=0 WHERE id=?', (sid,))
        return {'queued': True}

    return router
