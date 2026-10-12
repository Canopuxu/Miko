import asyncio
from dataclasses import dataclass, field
import json
import secrets
import threading
import uuid

from deepseek_harness.api import Session
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
import httpx

from miko.adapters.dsh.runtime import DshRuntime
from miko.settings import REPO_ROOT, Settings


@dataclass
class Turn:
    id: str
    conversation_id: str
    message: str
    events: asyncio.Queue = field(default_factory=asyncio.Queue)
    cancelled: bool = False
    runtime: DshRuntime | None = None
    remote: httpx.Response | None = None
    provider_task: asyncio.Task | None = None
    lifecycle: threading.Lock = field(default_factory=threading.Lock)
    task: asyncio.Task | None = None
    text: str = ""
    error: str | None = None
    provider_requests: int = 0

    def close_runtime(self):
        with self.lifecycle:
            if self.runtime is not None:
                self.runtime.close()


class ChatService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = settings.load_provider()
        self.system_prompt = (REPO_ROOT / "config/defaults/system-prompt.txt").read_text(encoding="utf-8").strip()
        self.bridge_token = secrets.token_urlsafe(32)
        self.conversations: dict[str, list[dict]] = {}
        self.turns: dict[str, Turn] = {}
        # SDK carrier includes coding tools by default. Chat has no tool calls in this stage.
        self.patch = settings.local_root / "runtime/dsh/chat.patch.json"
        self.patch.parent.mkdir(parents=True, exist_ok=True)
        self.patch.write_text(json.dumps([
            {"id": name, "disabled": True} for name in (
                "persistent-bash", "persistent-pwsh", "plugin-package-inventory-deepseek",
                "llm-retry", "session-title")
        ] + [{"id": "session-log-deepseek", "config": {"enabled": False}}]), encoding="utf-8")

    def status(self) -> dict:
        return {"configured": self.provider is not None,
                "model": self.provider.model if self.provider else None,
                "active_turns": len(self.turns)}

    def begin(self, message: str, conversation_id: str | None) -> Turn:
        if self.provider is None:
            raise HTTPException(503, "尚未配置模型，请填写 MikoLocal/config/provider.local.json")
        if not message.strip():
            raise HTTPException(422, "消息不能为空")
        if conversation_id is not None and conversation_id not in self.conversations:
            raise HTTPException(404, "对话已结束，请开启新对话")
        conversation_id = conversation_id or uuid.uuid4().hex
        if any(t.conversation_id == conversation_id for t in self.turns.values()):
            raise HTTPException(409, "当前对话仍在生成，请先停止或等待结束")
        self.conversations.setdefault(conversation_id, [])
        turn = Turn(uuid.uuid4().hex, conversation_id, message.strip())
        self.turns[turn.id] = turn
        turn.task = asyncio.create_task(self.run(turn))
        return turn

    async def run(self, turn: Turn):
        status = "error"
        try:
            turn.runtime = DshRuntime(
                self.settings, base_url=f"{self.settings.url}/_runtime/llm/{turn.id}",
                token=self.bridge_token, model=self.provider.model,
                patches=(str(self.patch),), system_prompt=self.system_prompt,
            )

            def generate():
                with turn.lifecycle:
                    if turn.cancelled:
                        return None
                    turn.runtime.start()
                if turn.cancelled:
                    return None
                return Session(turn.runtime, f"chat-{turn.id}").run(turn.message)

            result = await asyncio.to_thread(generate)
            if turn.cancelled:
                status = "cancelled"
            elif turn.error:
                status = "error"
            elif result is not None and result.finish_reason == "completed":
                turn.text = result.final_response
                self.conversations[turn.conversation_id].extend([
                    {"role": "user", "content": turn.message},
                    {"role": "assistant", "content": turn.text},
                ])
                status = "completed"
            else:
                turn.error = "生成未正常完成，请重试"
        except Exception:
            status = "cancelled" if turn.cancelled else "error"
            if status == "error" and not turn.error:
                turn.error = "生成连接中断，请重试"
        finally:
            await asyncio.to_thread(turn.close_runtime)
            self.turns.pop(turn.id, None)
            turn.events.put_nowait({"type": "done", "status": status,
                                    "text": turn.text, "error": turn.error})

    async def cancel(self, turn: Turn):
        turn.cancelled = True
        if turn.provider_task is not None:
            turn.provider_task.cancel()
        if turn.remote is not None:
            await turn.remote.aclose()
        await asyncio.to_thread(turn.close_runtime)

    async def close(self):
        turns = list(self.turns.values())
        await asyncio.gather(*(self.cancel(t) for t in turns))
        await asyncio.gather(*(t.task for t in turns), return_exceptions=True)

    async def proxy(self, turn: Turn) -> StreamingResponse:
        if turn.cancelled:
            raise HTTPException(409, "Turn cancelled")
        turn.provider_requests += 1
        if turn.provider_requests != 1:
            turn.error = "本轮模型请求未正常结束，请重试"
            raise HTTPException(502, "Unexpected additional model request")
        # Miko owns the cloud context. No default coding instructions or tools are forwarded.
        payload = {
            "model": self.provider.model,
            "messages": [{"role": "system", "content": self.system_prompt}]
                        + self.conversations[turn.conversation_id]
                        + [{"role": "user", "content": turn.message}],
            "stream": True, "max_tokens": 2048,
        }
        client = httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15), trust_env=False)
        try:
            turn.provider_task = asyncio.create_task(client.send(client.build_request(
                "POST", self.provider.api_base + "/chat/completions", json=payload,
                headers={"Authorization": "Bearer " + self.provider.api_key.get_secret_value()},
            ), stream=True))
            remote = await turn.provider_task
        except asyncio.CancelledError:
            await client.aclose()
            raise HTTPException(409, "Turn cancelled") from None
        except httpx.HTTPError:
            await client.aclose()
            turn.error = "无法连接模型接口，请稍后重试"
            raise HTTPException(502, "Provider connection failed") from None
        finally:
            turn.provider_task = None
        if remote.status_code != 200:
            turn.error = f"模型接口返回 HTTP {remote.status_code}，请检查模型配置或稍后重试"
            await remote.aclose()
            await client.aclose()
            raise HTTPException(502, "Provider request failed")
        turn.remote = remote

        async def stream():
            try:
                async for line in remote.aiter_lines():
                    if turn.cancelled:
                        break
                    if line.startswith("data:") and line[5:].strip() != "[DONE]":
                        chunk = json.loads(line[5:].strip())
                        for choice in chunk.get("choices", []):
                            text = (choice.get("delta") or {}).get("content")
                            if choice.get("index", 0) == 0 and isinstance(text, str) and text:
                                turn.text += text
                                turn.events.put_nowait({"type": "delta", "text": text})
                    yield (line + "\n").encode("utf-8")
            except (httpx.HTTPError, ValueError):
                if not turn.cancelled:
                    turn.error = "模型输出中断，请重试"
            finally:
                await remote.aclose()
                await client.aclose()
                turn.remote = None

        return StreamingResponse(stream(), media_type="text/event-stream")
