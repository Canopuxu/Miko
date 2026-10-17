"""OneBot transport adapted from the MIT Tulpa QQ baseline.

Keep identity checks, literal text segments and uncertain-send semantics.
"""
import asyncio
import json
import time
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr, model_validator
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus


class SendError(ValueError):
    pass


class SendUncertain(SendError):
    pass


class QqConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    http_url: str
    http_token: SecretStr = SecretStr('')
    ws_url: str
    ws_token: SecretStr = SecretStr('')
    account: str
    friends: list[dict[str, str]] = []
    miko_avatar_file: str = ''
    owner_avatar_file: str = ''

    @model_validator(mode='after')
    def local_endpoints(self):
        if bool(self.miko_avatar_file) != bool(self.owner_avatar_file):
            raise ValueError('头像切换需要同时配置角色头像和原头像')
        if not self.account.isascii() or not self.account.isdigit() or int(self.account) <= 0:
            raise ValueError('需要有效的本人 QQ 号')
        for friend in self.friends:
            uid = friend.get('id', '')
            if not uid.isascii() or not uid.isdigit() or int(uid) <= 0 or uid == self.account:
                raise ValueError('需要有效且与本人不同的好友 QQ 号')
        for url, schemes in ((self.http_url, ('http', 'https')), (self.ws_url, ('ws', 'wss'))):
            p = urlparse(url)
            if (p.scheme not in schemes or p.hostname not in ('127.0.0.1', 'localhost', '::1')
                    or p.username or p.password or p.query or p.fragment):
                raise ValueError('QQ 接口需要无凭据参数的本机地址')
        self.http_url = self.http_url.rstrip('/')
        return self


class OneBot:
    def __init__(self, config: QqConfig, transport=None):
        self.config = config
        self.transport = transport

    async def call(self, action, payload, *, sending=False):
        allowed = {'get_login_info', 'get_friend_list', 'get_friend_msg_history', 'get_msg', 'set_qq_avatar', 'fetch_sys_faces'}
        if action not in allowed | ({'send_private_msg'} if sending else set()):
            raise SendError('接口不在 QQ 适配器允许范围内')
        try:
            token = self.config.http_token.get_secret_value()
            async with httpx.AsyncClient(timeout=httpx.Timeout(30 if sending or action in ('get_msg', 'set_qq_avatar') else 4, connect=3),
                                         trust_env=False, transport=self.transport) as client:
                response = await client.post(self.config.http_url + '/' + action, json=payload,
                    headers={'Authorization': 'Bearer ' + token} if token else {})
                response.raise_for_status()
                value = response.json()
                if not isinstance(value, dict) or value.get('status') != 'ok' or value.get('retcode') != 0:
                    raise ValueError('invalid receipt')
                return value.get('data')
        except (httpx.HTTPError, ValueError, TypeError):
            if sending:
                raise SendUncertain('发送结果未知，请到 QQ 核对；不会自动重发') from None
            raise SendError('无法核对 QQ 接口，请检查连接、Token 和登录状态') from None

    async def login(self):
        value = await self.call('get_login_info', {})
        account = str(value.get('user_id', '')) if isinstance(value, dict) else ''
        if not account.isascii() or not account.isdigit() or int(account) <= 0:
            raise SendError('QQ 接口未返回有效登录账号')
        return account

    async def avatar(self, path):
        if await self.login() != self.config.account:
            raise SendError('QQ 登录账号发生变化，未更换头像')
        await self.call('set_qq_avatar', {'file': path})

    async def faces(self):
        return await self.call('fetch_sys_faces', {'refresh': False})

    async def target(self, account, friend):
        if await self.login() != account:
            raise SendError('QQ 登录账号发生变化，未发送')
        rows = await self.call('get_friend_list', {})
        match = next((r for r in rows if isinstance(r, dict) and str(r.get('user_id')) == friend), None) if isinstance(rows, list) else None
        if match is None:
            raise SendError('目标不是当前账号已核对的好友，未发送')
        return str(match.get('remark') or match.get('nickname') or friend)

    async def message(self, account, friend, mid, *, known_own=False):
        """Adapt Tulpa's native quote identity check to private messages."""
        value = await self.call('get_msg', {'message_id': mid})
        sender = value.get('sender') if isinstance(value, dict) else None
        uid = str(sender.get('user_id', '')) if isinstance(sender, dict) else ''
        if (not isinstance(value, dict) or str(value.get('message_id')) != str(mid)
            or value.get('message_type') != 'private' or uid not in (account, friend)
            or ('self_id' in value and str(value['self_id']) != account)
            or any(value.get(k) for k in ('recalled', 'is_recalled', 'deleted', 'is_deleted'))
            or (uid == account and not known_own and str(value.get('target_id', value.get('user_id', ''))) != friend)
            or (uid == account and 'target_id' in value and str(value['target_id']) != friend)):
            raise SendError('无法核对引用消息属于当前私聊，或消息已撤回')
        return value

    async def quote(self, account, friend, event):
        value = await self.message(account, friend, event['message_id'], known_own=event['origin'] != 'friend')
        uid = str(value['sender']['user_id'])
        expected = friend if event['origin'] == 'friend' else account
        if uid != expected or value.get('time') != int(event['timestamp']):
            raise SendError('引用消息的发送者或时间发生变化，未发送')

    async def send(self, account, friend, text, guard):
        await self.target(account, friend)
        guard()  # Last synchronous state check immediately before dispatch.
        value = await self.call('send_private_msg', {
            'user_id': int(friend), 'message': text if isinstance(text, list) else [{'type': 'text', 'data': {'text': text}}],
        }, sending=True)
        if not isinstance(value, dict) or type(value.get('message_id')) is not int:
            raise SendUncertain('未取得可靠消息编号，请到 QQ 核对；不会自动重发')
        return {'message_id': value['message_id'], 'timestamp': time.time()}


class EventReceiver:
    """Async adaptation of Tulpa's verified HTTP + WS identity/health checks."""
    def __init__(self, client, on_event, on_change):
        self.client, self.on_event, self.on_change = client, on_event, on_change
        self.state = {'state': 'stopped', 'account': '', 'note': 'QQ 接收尚未启动', 'connected_at': 0}
        self.task = None

    def status(self):
        return dict(self.state)

    async def update(self, state, note, **fields):
        changed = state != self.state['state']
        self.state.update(state=state, note=note, **fields)
        if changed:
            await self.on_change(state)

    def start(self):
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self.run())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.update('stopped', 'QQ 接收已停止', account='')

    async def run(self):
        delay = 1
        while True:
            try:
                await self.update('connecting', '正在连接 QQ 桥接…', account='')
                account = await self.client.login()
                if account != self.client.config.account:
                    await self.update('account_mismatch', '登录账号与 Miko 配置不一致，已停止接话', account='')
                    raise SendError('account mismatch')
                token = self.client.config.ws_token.get_secret_value()
                async with connect(self.client.config.ws_url,
                    additional_headers={'Authorization': 'Bearer ' + token} if token else {},
                    proxy=None, open_timeout=3, close_timeout=1, max_size=1024*1024,
                    ping_interval=20, ping_timeout=20) as ws:
                    await self.update('verifying', '正在核对事件账号与接收状态', account=account)
                    identity_at = packet_at = time.monotonic()
                    verified = False
                    while True:
                        if time.monotonic() - identity_at > 15:
                            if await self.client.login() != account:
                                raise SendError('account changed')
                            identity_at = time.monotonic()
                        if time.monotonic() - packet_at > 90:
                            raise SendError('event timeout')
                        try:
                            raw = await asyncio.wait_for(ws.recv(), .5)
                        except TimeoutError:
                            continue
                        try:
                            event = json.loads(raw)
                        except (ValueError, UnicodeError):
                            continue
                        if not isinstance(event, dict) or 'post_type' not in event:
                            continue
                        if str(event.get('self_id', '')) != account:
                            await self.update('account_mismatch', 'HTTP 与事件账号不一致，已停止接话', account='')
                            raise SendError('account mismatch')
                        packet_at = time.monotonic()
                        health = event.get('status') or {}
                        healthy = not (event.get('meta_event_type') == 'heartbeat' and
                                       (health.get('online') is False or health.get('good') is False))
                        if event.get('notice_type') == 'bot_status':
                            healthy = event.get('sub_type') == 'online'
                        if not verified:
                            self.state['connected_at'] = time.time()
                            verified = True
                            delay = 1
                        await self.update('connected' if healthy else 'upstream_offline',
                            'QQ 桥接已连接' if healthy else 'QQ 接收链路不健康，已停止接话')
                        if healthy:
                            await self.on_event(event)
            except InvalidStatus as exc:
                await self.update('auth_error' if exc.response.status_code in (401, 403) else 'disconnected',
                                 'QQ 事件连接失败，请检查节点与 Token', account='')
            except Exception:
                # Don't expose exception strings containing handshake credentials.
                if self.state['state'] != 'account_mismatch':
                    await self.update('disconnected', 'QQ 连接已断开，正在重连', account='')
            await asyncio.sleep(delay)
            delay = min(15, delay * 2)
