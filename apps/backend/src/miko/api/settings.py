"""Local configuration UI over the existing runtime configuration files."""
import hashlib
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError

from miko.api.local import local_request
from miko.settings import ProviderConfig, write_json
from miko.adapters.qq.onebot import QqConfig, OneBot, EventReceiver, SendError
from miko.adapters.qq.avatar import ReplyAvatar


class ModelInput(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    api_base: str
    api_key: str = ''
    model: str


class ModelsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: ModelInput
    qq_provider: ModelInput | None = None
    memory_model: str = ''
    life_model: str = ''
    embedding_model: str = ''


class QqInput(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    account: str
    http_url: str
    http_token: str = ''
    ws_url: str
    ws_token: str = ''


def settings_router(chat, qq):
    router = APIRouter(prefix='/api/settings', dependencies=[Depends(local_request)])
    root = chat.settings.local_root / 'config'

    def read(name):
        path = root / name
        return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}

    def public_provider(name):
        value = read(name)
        return {'api_base': value.get('api_base', ''), 'model': value.get('model', ''),
                'has_key': bool(value.get('api_key')), 'api_key': ''}

    def provider(body, name):
        value = body.model_dump()
        if not value['api_key'].strip():
            old = read(name)
            if old.get('api_base', '').rstrip('/') != value['api_base'].rstrip('/'):
                raise HTTPException(400, '更换 API 地址时请重新填写密钥')
            value['api_key'] = old.get('api_key', '')
        try:
            return ProviderConfig.model_validate(value)
        except ValidationError:
            raise HTTPException(400, '请检查 API 地址、密钥和模型名称') from None

    def idle():
        if chat.turns or qq.workers or chat.life.generating or chat.memory.social.active_jobs:
            raise HTTPException(409, '当前有任务正在运行，请完成后再保存设置')

    @router.get('')
    async def get_settings():
        config = read('qq.local.json')
        return {'provider': public_provider('provider.local.json'),
                'qq_provider': public_provider('qq-provider.local.json') if read('qq-provider.local.json') else None,
                'memory_model': read('memory.local.json').get('model', ''),
                'life_model': read('life.local.json').get('model', ''),
                'embedding_model': read('embedding.local.json').get('model', ''),
                'rerank_model': 'qwen3.7-text-rerank',
                'qq': {**{key: config.get(key, '') for key in ('account', 'http_url', 'ws_url')},
                       'http_token': '', 'ws_token': '',
                       'has_http_token': bool(config.get('http_token')), 'has_ws_token': bool(config.get('ws_token'))},
                'configured': bool(chat.provider)}

    @router.post('/models/test')
    async def test_model(body: ModelInput, target: str = 'provider'):
        name = 'qq-provider.local.json' if target == 'qq' else 'provider.local.json'
        config = provider(body, name)
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                result = await client.post(config.api_base + '/chat/completions',
                    headers={'Authorization': 'Bearer ' + config.api_key.get_secret_value()},
                    json={'model': config.model, 'messages': [{'role': 'user', 'content': '你好'}], 'max_tokens': 16})
                result.raise_for_status()
                if not result.json().get('choices'):
                    raise ValueError('missing choices')
        except (httpx.HTTPError, ValueError):
            raise HTTPException(400, '模型调用失败，请检查地址、密钥、模型权限及网络') from None
        return {'message': '模型调用成功'}

    @router.put('/models')
    async def save_models(body: ModelsInput):
        idle()
        primary = provider(body.provider, 'provider.local.json')
        secondary = provider(body.qq_provider, 'qq-provider.local.json') if body.qq_provider else None
        embedding = read('embedding.local.json')
        if (embedding.get('model', '') != body.embedding_model.strip()
                or read('provider.local.json').get('api_base', '').rstrip('/') != primary.api_base):
            identity = primary.api_base + '|' + body.embedding_model.strip()
            embedding['index_file'] = 'memu-' + hashlib.sha256(identity.encode()).hexdigest()[:12] + '.sqlite3'
        embedding['model'] = body.embedding_model.strip()
        async with chat.memory.lock:
            idle()
            root.mkdir(parents=True, exist_ok=True)
            for name, config in [('provider.local.json', primary), ('qq-provider.local.json', secondary)]:
                if config:
                    write_json(root / name, {**config.model_dump(exclude={'api_key'}), 'api_key': config.api_key.get_secret_value()})
                else:
                    (root / name).unlink(missing_ok=True)
            for name, value in [('memory.local.json', body.memory_model), ('life.local.json', body.life_model)]:
                write_json(root / name, {**read(name), 'model': value.strip()})
            write_json(root / 'embedding.local.json', embedding)
            chat.provider, chat.qq_provider = primary, secondary
            if chat.memory.social.pipeline:
                chat.memory.social.pipeline.model = body.memory_model.strip() or None
            chat.life.model = body.life_model.strip() or None
            chat.memory.configure_index()
        return {'message': '模型设置已生效；语义索引将在后台更新'}

    def qq_config(body):
        old = read('qq.local.json')
        value = {**old, **body.model_dump()}
        for key, url in [('http_token', 'http_url'), ('ws_token', 'ws_url')]:
            if not value[key]:
                value[key] = old.get(key, '') if old.get(url) == value[url] else ''
        try:
            return QqConfig.model_validate(value)
        except ValidationError:
            raise HTTPException(400, '请检查 QQ 账号及本机 HTTP、WebSocket 地址') from None

    async def check_qq(config):
        try:
            if await OneBot(config).login() != config.account:
                raise HTTPException(400, '接口登录账号与填写的 QQ 号不一致')
        except SendError:
            raise HTTPException(400, 'QQ 接口连接失败，请检查登录状态、地址和 Token') from None

    @router.post('/qq/test')
    async def test_qq(body: QqInput):
        await check_qq(qq_config(body))
        return {'message': 'HTTP 连接与账号核对成功；保存后查看 WebSocket 连接状态'}

    @router.put('/qq')
    async def save_qq(body: QqInput):
        idle()
        config = qq_config(body)
        await check_qq(config)
        idle()
        if qq.client and qq.client.config.account != config.account:
            raise HTTPException(409, '当前已连接其他 QQ 账号，请先停止服务后更换账号')
        await qq.close()
        qq.client = OneBot(config)
        qq.receiver = EventReceiver(qq.client, qq.receive, qq.source_changed)
        qq.avatar = ReplyAvatar(qq.client, config.miko_avatar_file, config.owner_avatar_file) if config.miko_avatar_file and config.owner_avatar_file else None
        root.mkdir(parents=True, exist_ok=True)
        write_json(root / 'qq.local.json', {**config.model_dump(exclude={'http_token', 'ws_token'}),
            'http_token': config.http_token.get_secret_value(), 'ws_token': config.ws_token.get_secret_value()})
        qq.start()
        return {'message': 'QQ 设置已保存，正在重新连接'}

    @router.post('/qq/friends')
    async def import_friends():
        if not qq.client:
            raise HTTPException(400, '请先配置 QQ 连接')
        try:
            config = qq.client.config
            await check_qq(config)
            rows = await qq.client.call('get_friend_list', {})
            for row in rows:
                uid = str(row['user_id'])
                if uid != config.account:
                    qq.store.add_friend(config.account, uid, row.get('remark') or row.get('nickname') or uid)
        except (SendError, KeyError, TypeError):
            raise HTTPException(400, '读取好友失败，请检查 QQ 连接') from None
        return {'message': f'已同步 {len(rows)} 位好友，请到 QQ 好友页选择开启接话'}

    return router
