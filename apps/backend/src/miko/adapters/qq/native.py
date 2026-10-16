"""Miko's direct QQ driver, reusing the existing send and quote checks."""
import asyncio
import json
import time

import httpx

from .client_driver import NativeClientDriver
from .onebot import OneBot, EventReceiver, SendError, SendUncertain


class NativeQq(OneBot):
    def __init__(self, config, native_config, database):
        super().__init__(config)
        self.driver = NativeClientDriver(native_config, database)
        self.capabilities = {}

    async def call(self, action, payload, *, sending=False):
        allowed = {'get_login_info', 'get_friend_list', 'get_msg', 'set_qq_avatar', 'fetch_sys_faces'}
        if action not in allowed | ({'send_private_msg'} if sending else set()):
            raise SendError('此操作不在 Miko 的 QQ 功能范围内')
        try:
            info = await self.driver.rpc('get_login_info')
            if str(info.get('user_id')) != self.config.account:
                raise ValueError('account changed')
            self.capabilities = info.get('capabilities', {})
            return info if action == 'get_login_info' else await self.driver.call(action, payload)
        except Exception:
            if sending:
                raise SendUncertain('发送结果未知，请到 QQ 核对；不会自动重发') from None
            raise SendError('QQ 尚未连接，请检查 QQ 登录状态，或到设置中重新连接') from None

    async def close(self):
        await self.driver.close()


class NativeReceiver(EventReceiver):
    async def run(self):
        driver = self.client.driver
        delay = 1

        async def connected():
            nonlocal delay
            if await self.client.login() != self.client.config.account:
                raise SendError('account changed')
            delay = 1
            note = 'QQ 已连接' if self.client.capabilities.get('send_images', True) else 'QQ 已连接；此客户端暂不支持图片发送'
            await self.update('connected', note, account=self.client.config.account, connected_at=time.time())

        async def receive(event):
            if str(event.get('self_id')) != self.client.config.account:
                raise SendError('account changed')
            await self.on_event(event)

        driver.on_connected = connected
        while True:
            try:
                await self.update('connecting', '正在连接所选 QQ…', account='')
                if await self.client.login() != self.client.config.account:
                    raise SendError('account changed')
                await driver.listen(receive)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 409:
                    try:
                        current = await driver.rpc('diagnostics')
                    except Exception:
                        current = None
                    if current and current['session'] == driver.event_session:
                        await self.update('blocked', 'QQ 消息接收出现缺失，接话已暂停；请到设置中重新连接', account='')
                        return
                await self.update('disconnected', 'QQ 连接已断开，正在等待原账号重新登录', account='')
            except Exception:
                await self.update('disconnected', 'QQ 连接已断开，正在等待原账号重新登录', account='')
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10)


def create_client(config, local_root):
    path = local_root / 'config/qq-client.local.json'
    if path.exists():
        native = json.loads(path.read_text('utf-8-sig'))
        if (native.get('port') and str(native.get('account')) == config.account
                and native.get('token') == config.http_token.get_secret_value()
                and config.http_url in (f"http://127.0.0.1:{native['port']}",
                                       f"http://127.0.0.1:{native.get('bridge_port', 17972)}")):
            return NativeQq(config, native, local_root / 'data/qq/native-messages.sqlite3')
    return OneBot(config)


def create_receiver(client, on_event, on_change):
    return (NativeReceiver if isinstance(client, NativeQq) else EventReceiver)(client, on_event, on_change)
