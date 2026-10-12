import asyncio
import json
import secrets
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from miko.domain.conversation.chat import ChatService


class ChatInput(BaseModel):
    message: str
    conversation_id: str | None = None


class Author(BaseModel):
    id: str
    kind: Literal['owner', 'miko', 'contact']
    name: str


class MessageRecord(BaseModel):
    id: str
    conversation_id: str
    turn_id: str
    author: Author
    source: Literal['web', 'qq']
    text: str
    status: Literal['running', 'completed', 'cancelled', 'error', 'interrupted']
    error: str | None
    created_at: int
    updated_at: int


class ConversationRecord(BaseModel):
    id: str
    title: str
    source: Literal['web', 'qq']
    created_at: int
    updated_at: int


class ConversationSummary(ConversationRecord):
    preview: str | None


class ConversationList(BaseModel):
    conversations: list[ConversationSummary]


class ConversationDetail(ConversationRecord):
    messages: list[MessageRecord]
    active_turn_id: str | None


def chat_router(service: ChatService) -> APIRouter:
    router = APIRouter()

    @router.get("/api/conversations", response_model=ConversationList)
    async def conversations():
        return {"conversations": service.store.list_conversations()}

    @router.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
    async def conversation(conversation_id: str):
        return service.conversation_detail(conversation_id)

    @router.post("/api/chat")
    async def chat(body: ChatInput, request: Request):
        turn = service.begin(body.message, body.conversation_id)

        async def events():
            try:
                yield encode({"type": "started", "turn_id": turn.id,
                              "conversation_id": turn.conversation_id,
                              "user_message_id": turn.user_message_id})
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(turn.events.get(), timeout=1)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield encode(event)
                    if event["type"] == "done":
                        return
            finally:
                if turn.id in service.turns:
                    await asyncio.shield(service.cancel(turn))

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.post("/api/chat/{turn_id}/cancel")
    async def cancel(turn_id: str):
        turn = service.turns.get(turn_id)
        if turn is not None:
            await service.cancel(turn)
        return {"cancelled": True}

    @router.post("/_runtime/llm/{turn_id}/chat/completions", include_in_schema=False)
    async def provider(turn_id: str, authorization: str = Header(default="")):
        if not secrets.compare_digest(authorization, "Bearer " + service.bridge_token):
            raise HTTPException(403, "Invalid local bridge token")
        turn = service.turns.get(turn_id)
        if turn is None:
            raise HTTPException(404, "Unknown turn")
        return await service.proxy(turn)

    return router


def encode(event: dict) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
