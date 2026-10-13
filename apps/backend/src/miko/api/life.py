from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictBool

from miko.api.local import local_request
from miko.domain.life import LifeEntries


class LifeSave(LifeEntries):
    day: date
    revision: int


class LifeControl(BaseModel):
    enabled: StrictBool


def life_router(life):
    router = APIRouter(prefix='/api/life', dependencies=[Depends(local_request)])

    @router.get('')
    async def listing(day: date | None = None):
        return life.listing(day)

    @router.put('')
    async def save(body: LifeSave):
        try:
            return life.save(body.day, body.entries, body.revision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post('/generate')
    async def generate():
        try:
            return await life.generate()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        except Exception:
            raise HTTPException(502, '生成未完成，原生活条目已保留') from None

    @router.put('/control')
    async def control(body: LifeControl):
        return life.toggle(body.enabled)

    return router
