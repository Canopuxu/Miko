import asyncio
from datetime import date
import re
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from miko.api.local import local_request
from miko.storage.profile_categories import ProfileCategories
from miko.storage.profile_categories import paths
from miko.settings import chat_prompts, save_chat_prompts, write_json


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

class CharacterName(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=36, pattern=r'^[^\x00-\x1f\x7f]+$')
    subject: Literal['miko', 'owner'] = 'miko'


class Subcategory(BaseModel):
    pinned: StrictBool = False
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=36, pattern=r'^[^/]+$')


class Category(Subcategory):
    children: list[Subcategory] = Field(default_factory=list)


class LibraryInput(BaseModel):
    subject: Literal['miko', 'owner']
    categories: list[Category]

    @model_validator(mode='after')
    def unique_names(self):
        ids = [node.id for root in self.categories for node in [root, *root.children]]
        names = [root.name for root in self.categories]
        if len(ids) != len(set(ids)) or len(names) != len(set(names)):
            raise ValueError('分类名称或编号重复')
        for root in self.categories:
            names = [child.name for child in root.children]
            if len(names) != len(set(names)):
                raise ValueError('小分类名称重复')
        return self


class ExpressionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    web: str = Field(min_length=1, max_length=12000)
    qq: str = Field(min_length=1, max_length=12000)
    qq_base: str | None = Field(default=None, min_length=1, max_length=12000)
    life: str | None = Field(default=None, min_length=1, max_length=12000)

    @model_validator(mode='after')
    def nonempty(self):
        if any(value is not None and not value.strip() for value in self.model_dump().values()):
            raise ValueError('表达设置不能为空')
        return self


class PortableEntry(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    kind: Literal['fact', 'overview'] = 'fact'
    category: str = Field(default='', max_length=80)
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=6000)
    keywords: str = Field(default='', max_length=300)


class CharacterFile(LibraryInput):
    model_config = ConfigDict(extra='forbid')
    format: Literal['miko-profile']
    version: Literal[1]
    name: str = Field(min_length=1, max_length=36, pattern=r'^[^\x00-\x1f\x7f]+$')
    entries: list[PortableEntry] = Field(max_length=2000)
    expression: ExpressionInput | None = None

    @model_validator(mode='after')
    def scoped_content(self):
        if not self.name.strip():
            raise ValueError('名称不能为空')
        known = set(paths(self.model_dump()['categories']).values()) | {''}
        if any(entry.category not in known for entry in self.entries):
            raise ValueError('词条的分类不在文件的分类列表中')
        if self.subject == 'owner' and self.expression is not None:
            raise ValueError('我的资料文件不包含角色表达设置')
        return self


def profiles_router(chat, qq):
    router = APIRouter(prefix='/api/profiles', dependencies=[Depends(local_request)])
    store = chat.profiles
    library = ProfileCategories(store.chat)

    def present(value, labels=False):
        from miko.storage.identity import TEXT_FIELDS
        return store.chat.identity().material(value, fields=TEXT_FIELDS | {'name'} if labels else TEXT_FIELDS)

    def expression_value():
        values, defaults = chat_prompts(chat.settings.local_root)
        return {'values': present(values), 'defaults': present(defaults),
                'effective': {key: chat.named_prompt(text) for key, text in values.items()}}

    def idle():
        if chat.turns or (qq and qq.workers) or getattr(getattr(chat, 'life', None), 'generating', False):
            raise HTTPException(409, '当前有回复正在进行，请结束后再应用角色设置')

    @router.get('/expression')
    async def get_expression():
        return expression_value()

    @router.post('/expression/preview')
    async def preview_expression(body: ExpressionInput):
        return {'effective': {key: chat.named_prompt(text) for key, text in body.model_dump().items()}}

    @router.put('/expression')
    async def save_expression(body: ExpressionInput):
        idle()
        save_chat_prompts(chat.settings.local_root, body.model_dump())
        chat.system_prompt = chat_prompts(chat.settings.local_root)[0]['web']
        return {**expression_value(), 'message': '表达设置已保存，将用于下一轮回复'}

    def portable(subject):
        value = dict(format='miko-profile', version=1, subject=subject,
                     name=store.chat.person_name(subject), categories=library.get(subject),
                     entries=store.portable_entries(subject))
        if subject == 'miko':
            value['expression'] = chat_prompts(chat.settings.local_root)[0]
        return present(value, labels=True)

    @router.get('/portable/export')
    async def export_character(subject: Literal['miko', 'owner'] = 'miko'):
        return portable(subject)

    @router.post('/portable/preview')
    async def preview_character(body: CharacterFile):
        return {'file': body.model_dump(), 'current_name': store.chat.person_name(body.subject),
                'current_entries': len(store.portable_entries(body.subject))}

    @router.post('/portable/import')
    async def import_character(body: CharacterFile):
        idle()
        previous = portable(body.subject)
        backup_id = uuid.uuid4().hex
        folder = chat.settings.local_root / 'backups/profiles'
        folder.mkdir(parents=True, exist_ok=True)
        write_json(folder / (backup_id + '.json'), previous)
        old_expression = chat_prompts(chat.settings.local_root)[0]
        if body.expression:
            save_chat_prompts(chat.settings.local_root, body.expression.model_dump())
        try:
            store.replace_portable(body.subject, body.name.strip(),
                                   [r.model_dump() for r in body.categories],
                                   [r.model_dump() for r in body.entries])
        except Exception:
            if body.expression:
                save_chat_prompts(chat.settings.local_root, old_expression)
            raise
        chat.system_prompt = chat_prompts(chat.settings.local_root)[0]['web']
        return {'message': '资料已导入，导入前的资料已自动备份', 'backup_id': backup_id}

    @router.get('/portable/backups/{backup_id}')
    async def download_character_backup(backup_id: str):
        if not re.fullmatch('[a-f0-9]{32}', backup_id):
            raise HTTPException(404, '找不到这份资料备份')
        path = chat.settings.local_root / 'backups/profiles' / (backup_id + '.json')
        if not path.is_file():
            raise HTTPException(404, '找不到这份资料备份')
        return FileResponse(path, media_type='application/json', filename='miko-profile-before-import.json')

    @router.get('/identity')
    async def identity(subject: Literal['miko', 'owner'] = 'miko'):
        return {'name': store.chat.person_name(subject)}

    @router.put('/identity')
    async def rename(body: CharacterName):
        return store.chat.rename_character(body.name, body.subject)

    @router.get('/categories')
    async def categories(subject: Literal['miko', 'owner'] = 'miko'):
        return present({'categories': library.get(subject)}, labels=True)

    @router.put('/categories')
    async def update_categories(body: LibraryInput):
        tree = body.model_dump()['categories']
        for root in tree:
            for node in [root, *root['children']]:
                if not node['pinned'] and node['id'] != body.subject + '-core':
                    node.pop('pinned')
        return present({'categories': library.save(body.subject, tree)}, labels=True)

    @router.get('')
    async def listing(q: str = '', subject: str = '', kind: str = '', category: str = ''):
        return present({'entries': store.list(q, subject, kind, category)})

    @router.post('')
    async def add(body: ProfileInput):
        try:
            return present(store.save(body.record()))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.put('/entries/{entry_id}')
    async def edit(entry_id: str, body: ProfileInput):
        try:
            return present(store.save(body.record(), entry_id))
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
            return present(await chat.memory.context(q, key))
        return present(store.context(q, key))

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
        return present(qq.store.friend(key))

    return router
