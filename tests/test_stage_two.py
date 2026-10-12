import asyncio
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import httpx
import psutil
import uvicorn

from miko.api.chat import chat_router
from miko.domain.conversation.chat import ChatService
from miko.settings import ProviderConfig, Settings


LOCAL_ROOT = Path(__file__).resolve().parents[2] / "MikoLocal"


class StageTwoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        settings = Settings(LOCAL_ROOT, port)
        self.requests = []
        self.remote_closed = asyncio.Event()
        self.waiting_headers = asyncio.Event()
        config = ProviderConfig(api_base=settings.url + "/fake", api_key="fake-private-key", model="fake-model")
        with patch.object(Settings, "load_provider", return_value=config):
            self.service = ChatService(settings)
        app = FastAPI()
        app.include_router(chat_router(self.service))

        @app.post("/fake/chat/completions")
        async def provider(request: Request):
            payload = await request.json()
            self.requests.append(payload)
            message = payload["messages"][-1]["content"]
            if message == "fail":
                return JSONResponse({"error": "fake-private-key"}, status_code=503)
            if message == "headers":
                self.waiting_headers.set()
                for _ in range(600):
                    if await request.is_disconnected():
                        self.remote_closed.set()
                        return JSONResponse({})
                    await asyncio.sleep(.05)

            async def stream():
                try:
                    for word in ("你好", "，", "Miko"):
                        yield self.chunk(word)
                        if message == "slow":
                            await asyncio.sleep(30)
                    yield self.chunk("", "stop")
                    yield "data: [DONE]\n\n"
                finally:
                    self.remote_closed.set()
            return StreamingResponse(stream(), media_type="text/event-stream")

        self.server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
        self.server_task = asyncio.create_task(self.server.serve(sockets=[sock]))
        for _ in range(100):
            if self.server.started:
                break
            await asyncio.sleep(.02)
        self.assertTrue(self.server.started)
        self.client = httpx.AsyncClient(base_url=settings.url, timeout=45, trust_env=False)

    async def asyncTearDown(self):
        await self.service.close()
        await self.client.aclose()
        self.server.should_exit = True
        await asyncio.wait_for(self.server_task, 10)

    @staticmethod
    def chunk(text, finish=None):
        return "data: " + json.dumps({
            "id": "fake", "object": "chat.completion.chunk", "created": 0, "model": "fake-model",
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": finish}],
        }, ensure_ascii=False) + "\n\n"

    async def events(self, response):
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                yield json.loads(line[5:])

    async def collect(self, message, conversation_id=None):
        async with self.client.stream("POST", "/api/chat", json={
            "message": message, "conversation_id": conversation_id,
        }) as response:
            self.assertEqual(response.status_code, 200)
            return [event async for event in self.events(response)]

    async def test_stream_and_context_without_coding_instructions(self):
        first = await self.collect("hello")
        self.assertEqual(first[-1]["status"], "completed", first)
        self.assertEqual("".join(e["text"] for e in first if e["type"] == "delta"), "你好，Miko")
        conversation = first[0]["conversation_id"]
        second = await self.collect("continue", conversation)
        self.assertEqual(second[-1]["status"], "completed", second)
        self.assertEqual(self.requests[-1]["messages"], [
            {"role": "system", "content": "你是 Miko，用中文自然地与用户交谈。"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "你好，Miko"},
            {"role": "user", "content": "continue"},
        ])
        self.assertNotIn("tools", self.requests[-1])
        self.assertFalse(self.service.turns)

    async def test_cancel_stream_then_continue(self):
        async with self.client.stream("POST", "/api/chat", json={"message": "slow"}) as response:
            iterator = self.events(response)
            first = await anext(iterator)
            delta = await anext(iterator)
            self.assertEqual(delta["type"], "delta")
            turn = self.service.turns[first["turn_id"]]
            pid = turn.runtime.process.pid
            environment = turn.runtime.process.environ()
            self.assertEqual(environment["DSH_HOME"], str(LOCAL_ROOT / "dsh/home"))
            self.assertNotIn("fake-private-key", json.dumps(environment))
            cancelled = await self.client.post(f'/api/chat/{turn.id}/cancel')
            self.assertEqual(cancelled.status_code, 200)
            remainder = [event async for event in iterator]
            self.assertEqual(remainder[-1]["status"], "cancelled", remainder)
        await asyncio.wait_for(self.remote_closed.wait(), 3)
        self.assertFalse(psutil.pid_exists(pid))
        self.assertEqual(self.service.conversations[first["conversation_id"]], [])
        followup = await self.collect("after stop", first["conversation_id"])
        self.assertEqual(followup[-1]["status"], "completed", followup)
        self.assertEqual(len(self.requests[-1]["messages"]), 2)

    async def test_provider_failure_no_retry_and_recovery(self):
        failed = await self.collect("fail")
        self.assertEqual(failed[-1]["status"], "error", failed)
        self.assertIn("503", failed[-1]["error"])
        self.assertNotIn("fake-private-key", json.dumps(failed))
        self.assertEqual(len(self.requests), 1)
        recovered = await self.collect("hello", failed[0]["conversation_id"])
        self.assertEqual(recovered[-1]["status"], "completed", recovered)
        self.assertEqual(len(self.requests[-1]["messages"]), 2)

    async def test_cancel_before_runtime_start(self):
        turn = self.service.begin("hello", None)
        await self.service.cancel(turn)
        await asyncio.wait_for(turn.task, 5)
        self.assertTrue(turn.cancelled)
        self.assertEqual(self.requests, [])
        self.assertFalse(self.service.turns)

    async def test_client_disconnect_releases_runtime(self):
        async with self.client.stream("POST", "/api/chat", json={"message": "slow"}) as response:
            iterator = self.events(response)
            first = await anext(iterator)
            await anext(iterator)
            pid = self.service.turns[first["turn_id"]].runtime.process.pid
        for _ in range(100):
            if not self.service.turns:
                break
            await asyncio.sleep(.05)
        self.assertFalse(self.service.turns)
        self.assertFalse(psutil.pid_exists(pid))

    async def test_cancel_while_provider_has_not_sent_headers(self):
        async with self.client.stream("POST", "/api/chat", json={"message": "headers"}) as response:
            iterator = self.events(response)
            first = await anext(iterator)
            await asyncio.wait_for(self.waiting_headers.wait(), 5)
            cancelled = await self.client.post(f'/api/chat/{first["turn_id"]}/cancel')
            self.assertEqual(cancelled.status_code, 200)
            remainder = [event async for event in iterator]
            self.assertEqual(remainder[-1]["status"], "cancelled", remainder)
        await asyncio.wait_for(self.remote_closed.wait(), 3)
        self.assertFalse(self.service.turns)


if __name__ == "__main__":
    unittest.main()
