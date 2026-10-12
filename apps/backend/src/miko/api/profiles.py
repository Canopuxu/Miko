import asyncio
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from miko.api.local import local_request


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    subject: Literal['miko', 'owner']
    kind: Literal['overview', 'fact', 'recent', 'excerpt']
    category: str = Field(default='', max_length=80)
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=6000)
    keywords: str = Field(default='', max_length=300)
    account: str = ''
    friend: str = ''
    as_of: date | None = None
    enabled: StrictBool = True

    @model_validator(mode='after')
    def dated_and_scoped(self):
        if bool(self.account) != bool(self.friend):
            raise ValueError('请同时选择账号和好友')
        if self.kind == 'recent' and self.as_of is None:
            raise ValueError('近况需要填写发生日期')
        return self

    def record(self):
        return {**self.model_dump(), 'as_of': self.as_of.isoformat() if self.as_of else None}


class RelationshipInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    owner_relationship: str = Field(default='', max_length=3000)
    miko_relationship: str = Field(default='', max_length=3000)
    miko_introduced: StrictBool | None = None


def profiles_router(chat, qq):
    router = APIRouter(prefix='/api/profiles', dependencies=[Depends(local_request)])
    store = chat.profiles

    @router.get('')
    async def listing(q: str = '', subject: str = '', kind: str = '', category: str = ''):
        return {'entries': store.list(q, subject, kind, category)}

    @router.post('')
    async def add(body: ProfileInput):
        try:
            return store.save(body.record())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.put('/entries/{entry_id}')
    async def edit(entry_id: str, body: ProfileInput):
        try:
            return store.save(body.record(), entry_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.delete('/entries/{entry_id}')
    async def delete(entry_id: str):
        try:
            store.delete(entry_id)
            return {'deleted': True}
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None

    @router.get('/context')
    async def context(q: str = '', account: str = '', friend: str = ''):
        if bool(account) != bool(friend):
            raise HTTPException(400, '请同时选择账号和好友')
        key = (account, friend) if account else None
        if key and not qq.store.friend(key):
            raise HTTPException(404, '找不到这位好友')
        if getattr(chat, 'memory', None):
            return await chat.memory.context(q, key)
        return store.context(q, key)

    @router.put('/relationships/{account}/{friend}')
    async def relationship(account: str, friend: str, body: RelationshipInput):
        key = (account, friend)
        row = qq.store.friend(key)
        if not row:
            raise HTTPException(404, '找不到这位好友')
        fields = body.model_dump()
        if body.miko_introduced is None:
            fields.pop('miko_introduced')
        elif bool(row['miko_introduced']) != body.miko_introduced:
            fields['intro_receipt_id'] = None
        qq.store.update_friend(key, **fields)
        if getattr(chat, 'memory', None):
            await asyncio.to_thread(chat.memory.social.store.sync)
            for subject in ('owner', 'miko'):
                cid = subject + '|' + row['conversation_id']
                with chat.memory.social.store.connect() as db:
                    db.execute("INSERT OR IGNORE INTO tulpa_scopes(platform,conversation_id,kind) VALUES('qq',?,'direct')", (cid,))
                    scope = db.execute('SELECT id,revision,manual FROM tulpa_scopes WHERE conversation_id=?', (cid,)).fetchone()
                text = fields[subject + '_relationship']
                if scope['manual'] != text:
                    chat.memory.social.edit(scope['id'], text, scope['revision'])
            await chat.memory.sync()
        return qq.store.friend(key)

    return router
