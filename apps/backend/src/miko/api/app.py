import asyncio
from contextlib import asynccontextmanager
import secrets
from typing import Callable

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from miko import __version__
from miko.adapters.dsh.runtime import DshRuntime
from miko.settings import Settings


def create_app(settings: Settings, control_token: str, request_stop: Callable[[], None]) -> FastAPI:
    runtime = DshRuntime(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await asyncio.to_thread(runtime.start)
        try:
            yield
        finally:
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
        return {"service": "miko", "version": __version__, "dsh": runtime.status()}

    @app.post("/_runtime/stop", include_in_schema=False)
    async def stop(authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization, f"Bearer {control_token}"):
            raise HTTPException(status_code=403, detail="Invalid local control token")
        request_stop()
        return {"stopping": True}

    return app
