import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StrictBool

from miko.api.local import local_request


class Control(BaseModel):
    enabled: StrictBool


class Correction(BaseModel):
    text: str
    revision: int

class ItemCorrection(Correction):
    enabled: StrictBool = True

class ItemEdit(ItemCorrection):
    category: str | None = Field(default=None, min_length=1, max_length=80)
    topic: str | None = Field(default=None, min_length=1, max_length=100)

class Annotation(ItemCorrection):
    scope: str = 'example'

class Restore(BaseModel):
    history_id: int
    revision: int

class Organization(BaseModel):
    subject: str
    peer: str = ''
    category: str

class OrganizationApply(Organization):
    items: list[dict[str,str]]
    revisions: dict[str,int]


class SearchRebuild(BaseModel):
    rebuild_all: StrictBool = False


def memory_router(memory):
    router = APIRouter(prefix='/api/memory', dependencies=[Depends(local_request)])

    @router.get('/search-index')
    async def search_index():
        return {'status':memory.expansion.status(),'items':[
            {'id':r['source_id'],'state':r['state'],'expressions':r['expressions'],
             'subject':r['row']['source'].get('subject','miko'),
             'text':r['row']['source'].get('content',r['row']['source'].get('text',
                    r['row']['source'].get('manual_corrections','')))}
            for r in memory.local.expansion_items()]}

    @router.post('/search-index/control')
    async def search_control(body: Control):
        memory.expansion.control(body.enabled)
        return memory.expansion.status()

    @router.post('/search-index/rebuild')
    async def search_rebuild(body: SearchRebuild):
        await memory.sync()
        memory.expansion.retry(body.rebuild_all)
        return memory.expansion.status()

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

    @router.post('/scopes/{sid}/consolidate')
    async def consolidate(sid: int):
        if memory.social.engine!='memobase':
            raise HTTPException(409,'新版记忆整理尚未配置')
        import time
        with memory.social.store.connect() as db:
            cur=db.execute("UPDATE tulpa_scopes SET status='updating',retry_at=? WHERE id=? AND retry_at<=?",
                           (time.time()+600,sid,time.time()))
            if not cur.rowcount:
                raise HTTPException(409,'该会话正在整理或等待重试')
        try:
            result=await asyncio.to_thread(memory.social.consolidate,sid,None,True)
            await memory.sync()
            return result
        except Exception:
            with memory.social.store.connect() as db:
                db.execute("UPDATE tulpa_scopes SET status='update_failed',retry_at=? WHERE id=?",(time.time()+900,sid))
            raise HTTPException(502,'整理未完成，本批消息已保留') from None
        finally:
            with memory.social.store.connect() as db:
                db.execute("UPDATE tulpa_scopes SET retry_at=0,status='collecting' WHERE id=? AND status='updating'",(sid,))

    @router.get('/items')
    async def items(q: str='', subject: str='', category: str='', peer: str | None=None):
        return {'items': memory.structured.items(q,subject=subject,category=category,peer=peer)}

    @router.get('/objects')
    async def objects():
        with memory.chat.store.connect() as db:
            return {'people':[dict(r) for r in db.execute('SELECT * FROM people ORDER BY kind,id')]}

    @router.post('/organize')
    async def organize(body: Organization):
        if not memory.social.pipeline:
            raise HTTPException(409,'新版记忆整理尚未配置')
        return await memory.social.pipeline.organize_category(body.subject,body.peer,body.category)

    @router.post('/organize/apply')
    async def organize_apply(body: OrganizationApply):
        try:
            result=memory.structured.organize(body.subject,body.peer,body.category,body.items,body.revisions)
            await memory.sync()
            return {'items':result}
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from None

    @router.get('/items/{item_id}')
    async def item(item_id: str):
        try:
            return memory.structured.detail(item_id)
        except ValueError as exc:
            raise HTTPException(404,str(exc)) from None

    @router.patch('/items/{item_id}')
    async def edit_item(item_id: str, body: ItemEdit):
        try:
            result=memory.structured.edit(item_id,body.text,body.revision,body.enabled,body.category,body.topic)
            await memory.sync()
            return result
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from None

    @router.delete('/items/{item_id}')
    async def delete_item(item_id: str):
        try:
            result = memory.structured.delete(item_id)
            await memory.sync()
            return result
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None

    @router.post('/items/{item_id}/restore')
    async def restore(item_id: str, body: Restore):
        try:
            result=memory.structured.restore(item_id,body.history_id,body.revision)
            await memory.sync()
            return result
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from None

    @router.get('/days')
    async def days(day: str='', subject: str=''):
        return {'days': memory.structured.days(day,subject)}

    @router.delete('/days/{event_id}')
    async def delete_day(event_id: str):
        try:
            result = memory.structured.delete(event_id, 'day')
            await memory.sync()
            return result
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None

    @router.get('/review')
    async def review(day: str=''):
        try:
            return memory.structured.review(day)
        except ValueError:
            raise HTTPException(422,'日期格式需要为 YYYY-MM-DD') from None

    @router.delete('/review/{message_id}')
    async def delete_review(message_id: str):
        try:
            return memory.structured.delete_review(message_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None

    @router.put('/review/{message_id}')
    async def annotate(message_id: str, body: Annotation):
        try:
            return memory.structured.annotate(message_id,body.text,body.scope,body.revision,body.enabled)
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from None

    return router
