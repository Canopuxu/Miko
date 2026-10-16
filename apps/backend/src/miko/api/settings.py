"""Local configuration UI over the existing runtime configuration files."""
import hashlib
import json
import os
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from miko.api.local import local_request
from miko import __version__
from miko.settings import ProviderConfig, RerankConfig, legacy_rerank, write_json
from miko.adapters.memory.rerank import rank, infer_format
from miko.adapters.qq.onebot import QqConfig, OneBot, EventReceiver, SendError
from miko.adapters.qq.avatar import ReplyAvatar
from miko.adapters.qq.managed import resolve_connection, ManagedQq
from miko.adapters.qq.native import NativeQq, create_client, create_receiver
from miko.api.model_catalog import model_kind, model_list_base, rerank_endpoint


class ModelInput(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    api_base: str
    api_key: str = ''
    model: str


class ModelsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: ModelInput
    qq_provider: ModelInput | None = None
    life_provider: ModelInput | None = None
    memory_provider: ModelInput | None = None
    memory_model: str = ''
    life_model: str = ''
    embedding_model: str = ''
    use_shared_model: bool = False
    embedding_provider: ModelInput | None = None
    rerank_provider: 'RerankInput | None' = None
    rerank_enabled: bool | None = None
    rerank_shared: bool = False


class RerankInput(ModelInput):
    format: Literal['dashscope', 'standard'] | None = None


ModelsInput.model_rebuild()


class FolderInput(BaseModel):
    folder: Literal['data', 'logs']


class QqInput(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    account: str
    managed: bool = True
    client_pid: int | None = Field(default=None, gt=0)


class FriendSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    friend_ids: list[str] = Field(min_length=1)


def settings_router(chat, qq):
    router = APIRouter(prefix='/api/settings', dependencies=[Depends(local_request)])
    root = chat.settings.local_root / 'config'
    managed_qq = ManagedQq(chat.settings.local_root)

    def read(name):
        path = root / name
        return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}

    def public_provider(name):
        value = read(name)
        return {'api_base': value.get('api_base', ''), 'model': value.get('model', ''),
                'has_key': bool(value.get('api_key')), 'api_key': ''}

    def provider(body, name, fallback=None):
        value = body.model_dump(exclude={'format'})
        if not value['api_key'].strip():
            old = read(name)
            if not old.get('api_key') and fallback and fallback.api_base == value['api_base'].rstrip('/'):
                old = {'api_base': fallback.api_base, 'api_key': fallback.api_key.get_secret_value()}
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

    def rerank_format(body):
        if body.format:
            return body.format
        previous = read('rerank.local.json')
        if previous.get('api_base', '').rstrip('/') == body.api_base.rstrip('/') and previous.get('format'):
            return previous['format']
        return infer_format(body.api_base)

    @router.get('')
    async def get_settings():
        config = read('qq.local.json')
        return {'provider': public_provider('provider.local.json'),
                'qq_provider': public_provider('qq-provider.local.json') if read('qq-provider.local.json') else None,
                'life_provider': public_provider('life-provider.local.json') if read('life-provider.local.json') else None,
                'memory_provider': public_provider('memory-provider.local.json') if read('memory-provider.local.json') else None,
                'memory_model': read('memory.local.json').get('model', ''),
                'life_model': read('life.local.json').get('model', ''),
                'embedding_model': read('embedding.local.json').get('model', ''),
                'embedding_provider': public_provider('embedding.local.json') if read('embedding.local.json').get('api_base') else None,
                'rerank_provider': public_rerank(),
                'rerank_enabled': read('rerank.local.json').get('enabled', bool(chat.provider)),
                'rerank_shared': read('rerank.local.json').get('shared', not bool(read('rerank.local.json').get('api_base'))),
                'qq': {'account': config.get('account', '') if config.get('enabled', True) else ''},
                'configured': bool(chat.provider)}

    def public_rerank():
        value = read('rerank.local.json')
        if value.get('api_base'):
            return {**public_provider('rerank.local.json'), 'format': value.get('format', 'dashscope')}
        config = legacy_rerank(chat.provider)
        return {**config.model_dump(exclude={'api_key'}), 'api_key': '', 'has_key': True} if config else None

    @router.get('/application')
    async def application():
        return {'version': __version__, 'data_folder': str(chat.settings.local_root / 'data'),
                'logs_folder': str(chat.settings.local_root / 'logs')}

    @router.post('/open-folder')
    async def open_folder(body: FolderInput):
        path = chat.settings.local_root / body.folder
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(path)
        except OSError:
            raise HTTPException(400, '文件夹未能打开，请按页面显示的路径手动打开') from None
        return {'message': '文件夹已打开'}

    @router.post('/models/test')
    async def test_model(body: RerankInput, target: Literal['provider', 'qq', 'life', 'memory', 'embedding', 'rerank'] = 'provider', shared: bool = False):
        name = {'provider': 'provider.local.json', 'qq': 'qq-provider.local.json',
                'life': 'life-provider.local.json', 'memory': 'memory-provider.local.json',
                'embedding': 'embedding.local.json', 'rerank': 'rerank.local.json'}[target]
        fallback = legacy_rerank(chat.provider) if target == 'rerank' else chat.provider
        config = provider(body, 'provider.local.json' if shared else name, chat.provider if shared else fallback)
        if target == 'rerank' and shared:
            config = config.model_copy(update={'api_base': rerank_endpoint(config.api_base)})
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                if target == 'rerank':
                    rows = await rank(client, RerankConfig(**config.model_dump(), format=infer_format(config.api_base) if shared else rerank_format(body)), '你好', ['你好'], 1)
                    if not rows:
                        raise ValueError('missing ranks')
                else:
                    payload = {'model': config.model, 'input': ['你好']} if target == 'embedding' else {
                        'model': config.model, 'messages': [{'role': 'user', 'content': '你好'}], 'max_tokens': 16}
                    result = await client.post(config.api_base + ('/embeddings' if target == 'embedding' else '/chat/completions'),
                        headers={'Authorization': 'Bearer ' + config.api_key.get_secret_value()}, json=payload)
                    result.raise_for_status()
                    if target == 'embedding':
                        vector = result.json()['data'][0]['embedding']
                        if not vector or not all(isinstance(x, (int,float)) for x in vector):
                            raise ValueError('invalid vector')
                    elif not result.json().get('choices'):
                        raise ValueError('missing choices')
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise HTTPException(400, '模型调用失败，请检查地址、密钥、模型权限及网络') from None
        if target == 'embedding':
            return {'message': f'连接成功，已返回 {len(vector)} 维向量；测试不会修改记忆'}
        if target == 'rerank':
            return {'message': '连接成功，已返回相关性排序结果'}
        return {'message': '连接成功，已收到模型回复'}

    @router.post('/models/list')
    async def list_models(body: ModelInput, target: Literal['provider', 'qq', 'life', 'memory', 'embedding', 'rerank'] = 'provider'):
        name = 'provider.local.json' if target == 'provider' else f'{target}-provider.local.json'
        if target in ('embedding', 'rerank'):
            name = f'{target}.local.json'
        config = provider(body.model_copy(update={'model': 'catalog'}), name,
            legacy_rerank(chat.provider) if target == 'rerank' else chat.provider)
        try:
            async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
                response = await client.get(model_list_base(config.api_base) + '/models',
                    headers={'Authorization': 'Bearer ' + config.api_key.get_secret_value()})
                response.raise_for_status()
                value = response.json()
                rows = value if isinstance(value, list) else value.get('data', value.get('models', []))
                if not isinstance(rows, list):
                    raise ValueError('invalid model list')
                models = {}
                for row in rows:
                    if isinstance(row, dict) and isinstance(row.get('id'), str) and row['id'].strip():
                        models[row['id']] = {'id': row['id'], 'kind': model_kind(row)}
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            raise HTTPException(400, '未能获取模型列表，请检查地址和密钥；也可以直接填写模型名称') from None
        return {'models': sorted(models.values(), key=lambda row: row['id'].lower())}

    @router.put('/models')
    async def save_models(body: ModelsInput):
        idle()
        if body.use_shared_model:
            body.qq_provider = None
            body.life_provider = None
            body.memory_provider = None
            body.memory_model = ''
            body.life_model = ''
        primary = provider(body.provider, 'provider.local.json')
        secondary = None
        # A new per-feature connection may deliberately reuse the main API key.
        if body.qq_provider:
            secondary = provider(body.qq_provider, 'qq-provider.local.json', primary)
        task_providers = {task: provider(value, f'{task}-provider.local.json', primary) if value else None
            for task, value in [('life', body.life_provider), ('memory', body.memory_provider)]}
        embedding = read('embedding.local.json')
        old_base = embedding.get('api_base') or read('provider.local.json').get('api_base', '')
        if 'embedding_provider' in body.model_fields_set:
            if body.embedding_provider:
                config = provider(body.embedding_provider, 'embedding.local.json', primary)
                embedding.update(**config.model_dump(exclude={'api_key'}), api_key=config.api_key.get_secret_value())
            else:
                embedding.pop('api_base', None)
                embedding.pop('api_key', None)
        embedding_model = body.embedding_provider.model.strip() if body.embedding_provider else body.embedding_model.strip()
        old_model = read('embedding.local.json').get('model', '')
        effective_base = embedding.get('api_base') or primary.api_base
        if old_model != embedding_model or old_base.rstrip('/') != effective_base:
            identity = effective_base + '|' + embedding_model
            embedding['index_file'] = 'memu-' + hashlib.sha256(identity.encode()).hexdigest()[:12] + '.sqlite3'
        embedding['model'] = embedding_model
        reranker = None
        if body.rerank_enabled:
            if not body.rerank_provider:
                raise HTTPException(400, '请填写记忆筛选服务的地址、密钥和模型')
            if body.rerank_shared:
                config = primary.model_copy(update={'api_base': rerank_endpoint(primary.api_base), 'model': body.rerank_provider.model.strip()})
                if not config.model:
                    raise HTTPException(400, '请填写重排模型名称')
                reranker = RerankConfig(**config.model_dump(), format=infer_format(config.api_base))
            else:
                config = provider(body.rerank_provider, 'rerank.local.json', legacy_rerank(chat.provider or primary))
                reranker = RerankConfig(**config.model_dump(), format=rerank_format(body.rerank_provider))
        async with chat.memory.lock:
            idle()
            root.mkdir(parents=True, exist_ok=True)
            for name, config in [('provider.local.json', primary), ('qq-provider.local.json', secondary),
                    *[(f'{task}-provider.local.json', value) for task, value in task_providers.items()]]:
                if config:
                    write_json(root / name, {**config.model_dump(exclude={'api_key'}), 'api_key': config.api_key.get_secret_value()})
                else:
                    (root / name).unlink(missing_ok=True)
            for name, value in [('memory.local.json', body.memory_model), ('life.local.json', body.life_model)]:
                write_json(root / name, {**read(name), 'model': value.strip()})
            write_json(root / 'embedding.local.json', embedding)
            if body.rerank_enabled is not None:
                value = {**reranker.model_dump(exclude={'api_key'}), 'api_key': reranker.api_key.get_secret_value()} if reranker else read('rerank.local.json')
                write_json(root / 'rerank.local.json', {**value, 'enabled': body.rerank_enabled, 'shared': body.rerank_shared})
            chat.provider, chat.qq_provider = primary, secondary
            if chat.memory.social.pipeline:
                chat.memory.social.pipeline.provider = task_providers['memory']
                chat.memory.social.pipeline.model = (task_providers['memory'].model if task_providers['memory'] else body.memory_model.strip()) or None
            chat.life.provider = task_providers['life']
            chat.life.model = (task_providers['life'].model if task_providers['life'] else body.life_model.strip()) or None
            chat.memory.configure_index()
        return {'message': '模型设置已生效；语义索引将在后台更新'}

    def qq_config(body):
        old = read('qq.local.json')
        try:
            return resolve_connection(chat.settings.local_root, body.account.strip(), old)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    async def check_qq(config):
        client = qq.client if isinstance(qq.client, OneBot) and qq.client.config == config else create_client(config, chat.settings.local_root)
        try:
            if await client.login() != config.account:
                raise HTTPException(400, 'QQ 登录账号已改变，请重新检测并选择账号')
        except SendError:
            raise HTTPException(400, 'QQ 尚未连接，请完成登录后重新检测；若 QQ 刚更新，请检查兼容性提示') from None
        finally:
            if isinstance(client, NativeQq) and client is not qq.client:
                await client.close()

    @router.get('/qq/accounts')
    async def qq_accounts():
        async with managed_qq.lock:
            try:
                clients = await managed_qq.discover()
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
        return {'clients': clients, 'bridge': managed_qq.status()}

    @router.get('/qq/connection')
    async def qq_connection():
        return {'bridge': managed_qq.status()}

    @router.put('/qq')
    async def save_qq(body: QqInput):
        async with managed_qq.lock:
            return await connect_qq(body)

    async def connect_qq(body):
        idle()
        # Reject a different account before starting or changing any bridge.
        # An explicit disconnect keeps account changes visible to the user.
        if qq.client and qq.client.config.account != body.account.strip():
            raise HTTPException(409, '请先断开当前 QQ，再连接所选账号；已有记录会保留')
        if body.managed and body.client_pid is not None:
            try:
                config = await managed_qq.connect(body.client_pid, body.account.strip(), read('qq.local.json'))
            except (ValueError, OSError) as exc:
                message = str(exc) if isinstance(exc, ValueError) else 'QQ 连接组件未能启动，请检查程序文件和本地写入权限'
                raise HTTPException(400, message) from None
        else:
            config = qq_config(body)
        await check_qq(config)
        idle()
        if (body.managed and qq.client and qq.client.config == config
                and qq.receiver and qq.receiver.status()['state'] == 'connected'):
            return {'message': 'QQ 已连接'}
        await qq.close()
        qq.client = create_client(config, chat.settings.local_root)
        qq.receiver = create_receiver(qq.client, qq.receive, qq.source_changed)
        qq.avatar = ReplyAvatar(qq.client, config.miko_avatar_file, config.owner_avatar_file) if config.miko_avatar_file and config.owner_avatar_file else None
        root.mkdir(parents=True, exist_ok=True)
        write_json(root / 'qq.local.json', {**config.model_dump(exclude={'http_token', 'ws_token'}),
            'http_token': config.http_token.get_secret_value(), 'ws_token': config.ws_token.get_secret_value()})
        qq.start()
        return {'message': 'QQ 账号已核对，正在连接消息通道；连接后可选择参与聊天的好友'}

    @router.post('/qq/disconnect')
    async def disconnect_qq():
        async with managed_qq.lock:
            idle()
            await qq.close()
            qq.client = None
            qq.receiver = None
            qq.avatar = None
            previous = read('qq.local.json')
            if previous:
                write_json(root / 'qq.local.json', {**previous, 'enabled': False})
            await managed_qq.disconnect()
        return {'message': 'QQ 已断开，聊天记录和记忆已保留；可以选择其他账号连接'}

    async def friend_list():
        if not qq.client:
            raise HTTPException(400, '请先配置 QQ 连接')
        try:
            config = qq.client.config
            await check_qq(config)
            rows = await qq.client.call('get_friend_list', {})
            return config.account, [{'friend': str(row['user_id']), 'nickname': str(row.get('nickname') or ''),
                'remark': str(row.get('remark') or '')} for row in rows if str(row['user_id']) != config.account]
        except (SendError, KeyError, TypeError):
            raise HTTPException(400, '读取好友失败，请检查 QQ 连接') from None
    @router.get('/qq/friends')
    async def available_friends():
        account, rows = await friend_list()
        added = {r['friend'] for r in qq.store.friends() if r['account'] == account}
        return {'account': account, 'friends': [{**r, 'added': r['friend'] in added} for r in rows]}

    @router.post('/qq/friends')
    async def add_friends(body: FriendSelection):
        account, rows = await friend_list()
        selected = set(body.friend_ids)
        available = {r['friend']: r for r in rows}
        if selected - available.keys():
            raise HTTPException(400, '所选好友不在当前 QQ 好友列表中，请刷新后重试')
        existing = {r['friend'] for r in qq.store.friends() if r['account'] == account}
        added = selected - existing
        for uid in body.friend_ids:
            if uid in added:
                row = available[uid]
                qq.store.add_friend(account, uid, row['remark'] or row['nickname'] or uid)
                added.remove(uid)
        count = len(selected - existing)
        return {'message': f'已添加 {count} 位好友，可到 QQ 好友页开启接话', 'added': count}

    return router
