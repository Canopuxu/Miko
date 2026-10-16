"""Standalone local OneBot surface for the Miko QQ client bridge.

The client driver must use the logged-in QQ session and return native send
receipts. This module is not mounted by Miko's production application.
"""
import asyncio
import secrets
from contextlib import asynccontextmanager, suppress
from typing import Protocol

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse


class ClientDriver(Protocol):
    async def call(self, action: str, payload: dict): ...


class ClientBridge:
    actions = frozenset({'get_login_info', 'get_friend_list', 'get_msg',
                         'send_private_msg', 'set_qq_avatar', 'fetch_sys_faces'})

    def __init__(self, driver: ClientDriver, account: str, token: str | None = None):
        if not account.isascii() or not account.isdigit() or int(account) <= 0:
            raise ValueError('Invalid QQ account')
        self.driver, self.account = driver, int(account)
        self.token = token or secrets.token_urlsafe(32)
        self.clients = set()
        self.app = self._app()

    def authorized(self, headers):
        return secrets.compare_digest(headers.get('authorization', ''), 'Bearer ' + self.token)

    async def identity(self):
        info = await self.driver.call('get_login_info', {})
        if not isinstance(info, dict) or str(info.get('user_id')) != str(self.account):
            raise ValueError('QQ account unavailable or changed')
        return info

    async def publish(self, event):
        """Called by the driver for both friend messages and owner's messages."""
        if str(event.get('self_id')) != str(self.account):
            raise ValueError('Event account mismatch')
        for queue in tuple(self.clients):
            if queue.full():
                # Force reconnection rather than silently lose a manual interruption.
                self.clients.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
            else:
                queue.put_nowait(event)

    async def heartbeat(self):
        while True:
            try:
                await self.identity()
                good = True
            except Exception:
                good = False
            await self.publish({'self_id': self.account, 'post_type': 'meta_event',
                'meta_event_type': 'heartbeat', 'status': {'online': good, 'good': good}})
            await asyncio.sleep(15)

    def _app(self):
        @asynccontextmanager
        async def lifespan(app):
            task = asyncio.create_task(self.heartbeat())
            try:
                yield
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

        app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

        @app.post('/{action}')
        async def call(action: str, request: Request):
            if not self.authorized(request.headers):
                return JSONResponse({'status': 'failed', 'retcode': 1401}, status_code=401)
            if action not in self.actions:
                return JSONResponse({'status': 'failed', 'retcode': 1404}, status_code=404)
            try:
                payload = await request.json()
                if not isinstance(payload, dict):
                    raise ValueError('Expected object')
                identity = await self.identity()
                if action == 'send_private_msg':
                    friends = await self.driver.call('get_friend_list', {})
                    target = str(payload.get('user_id', ''))
                    if not any(str(friend['user_id']) == target for friend in friends):
                        raise ValueError('Not a friend')
                result = identity if action == 'get_login_info' else await self.driver.call(action, payload)
                if action == 'send_private_msg' and (not isinstance(result, dict)
                        or type(result.get('message_id')) is not int):
                    raise ValueError('Missing native send receipt')
                return {'status': 'ok', 'retcode': 0, 'data': result}
            except Exception:
                # No resend: a native call may have sent before its receipt failed.
                return JSONResponse({'status': 'failed', 'retcode': 1500}, status_code=502)

        @app.websocket('/')
        async def events(ws: WebSocket):
            if not self.authorized(ws.headers):
                await ws.close(code=1008)
                return
            try:
                await self.identity()
            except Exception:
                await ws.close(code=1013)
                return
            await ws.accept()
            queue = asyncio.Queue(maxsize=256)
            self.clients.add(queue)
            disconnected = asyncio.create_task(ws.receive())
            pending = None
            try:
                await ws.send_json({'post_type': 'meta_event', 'meta_event_type': 'lifecycle',
                                    'sub_type': 'connect', 'self_id': self.account})
                while True:
                    pending = asyncio.create_task(queue.get())
                    done, _ = await asyncio.wait((pending, disconnected), return_when=asyncio.FIRST_COMPLETED)
                    if disconnected in done:
                        if disconnected.result()['type'] == 'websocket.disconnect':
                            return
                        disconnected = asyncio.create_task(ws.receive())
                    if pending not in done:
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
                        continue
                    event = pending.result()
                    if event is None:
                        await ws.close(code=1013)
                        return
                    await ws.send_json(event)
            except (WebSocketDisconnect, RuntimeError, OSError, asyncio.CancelledError):
                pass
            finally:
                self.clients.discard(queue)
                tasks = [disconnected] + ([pending] if pending else [])
                for task in tasks:
                    task.cancel()
                with suppress(asyncio.CancelledError):
                    await asyncio.gather(*tasks, return_exceptions=True)

        return app
