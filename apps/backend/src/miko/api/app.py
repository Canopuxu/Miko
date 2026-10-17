import asyncio
from contextlib import asynccontextmanager
import secrets
from typing import Callable

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from miko import __version__
from miko.adapters.dsh.runtime import DshRuntime
from miko.api.chat import chat_router
from miko.api.qq import qq_router
from miko.api.profiles import profiles_router
from miko.api.memory import memory_router
from miko.api.life import life_router
from miko.api.settings import settings_router
from miko.api.backups import backups_router
from miko.api.history import history_router
from miko.domain.life import Life
from miko.adapters.memory.service import Memory
from miko.domain.conversation.chat import ChatService
from miko.domain.conversation.qq import QqService
from miko.settings import REPO_ROOT, Settings
from miko.adapters.qq.managed import start_bridge
from miko.adapters.qq.native import NativeQq


def create_app(settings: Settings, control_token: str, request_stop: Callable[[], None]) -> FastAPI:
    runtime = DshRuntime(settings)
    chat = ChatService(settings)
    qq = QqService(chat, settings.load_qq())
    chat.life = Life(chat)
    chat.memory = Memory(chat)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await asyncio.to_thread(runtime.start)
        if isinstance(qq.client, NativeQq):
            await asyncio.to_thread(start_bridge, settings.local_root)
        qq.start()
        chat.memory.start()
        chat.life.start()
        try:
            yield
        finally:
            await qq.close()
            await chat.life.close()
            await chat.memory.expansion.close()
            chat.memory.social.stop()
            await chat.close()
            await chat.memory.close()
            await asyncio.to_thread(runtime.close)

    app = FastAPI(title="Miko", version=__version__, lifespan=lifespan)

    @app.get("/health")
    async def health():
        ready = runtime.ready
        return JSONResponse(
            {"service": "miko", "version": __version__, "ready": ready},
            status_code=200 if ready else 503,
        )

    @app.get("/api/status")
    async def status():
        return {"service": "miko", "version": __version__, "dsh": runtime.status(),
                "chat": chat.status(), "memory": chat.memory.status(), "life": chat.life.status()}

    @app.post("/_runtime/stop", include_in_schema=False)
    async def stop(authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization, f"Bearer {control_token}"):
            raise HTTPException(status_code=403, detail="Invalid local control token")
        request_stop()
        return {"stopping": True}

    app.include_router(chat_router(chat))
    app.include_router(qq_router(qq))
    app.include_router(profiles_router(chat, qq))
    app.include_router(memory_router(chat.memory))
    app.include_router(life_router(chat.life))
    app.include_router(settings_router(chat, qq))
    app.include_router(backups_router(chat, qq))
    app.include_router(history_router(chat.store))
    web_root = REPO_ROOT / "apps/web/dist"
    if web_root.exists():
        app.mount("/", StaticFiles(directory=web_root, html=True), name="miko-web")
    return app
