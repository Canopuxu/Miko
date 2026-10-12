import asyncio
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
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
from miko.storage.chat import ChatStore
from miko.storage.qq import QqStore
from miko.api.profiles import ProfileInput


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
        self.temporary = tempfile.TemporaryDirectory()
        self.store = ChatStore(Path(self.temporary.name) / 'chat.sqlite3')
        config = ProviderConfig(api_base=settings.url + "/fake", api_key="fake-private-key", model="fake-model")
        with patch.object(Settings, "load_provider", return_value=config):
            self.service = ChatService(settings, self.store)
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
        self.temporary.cleanup()

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
        self.assertTrue(self.requests[-1]['messages'][0]['content'].startswith('你是 Miko，用中文自然地与用户交谈。\n\n'))
        self.assertEqual(self.requests[-1]["messages"][1:], [
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
        self.assertEqual(self.store.history(first["conversation_id"]), [])
        saved = self.store.messages(first['conversation_id'])
        self.assertEqual(saved[-1]['status'], 'cancelled')
        self.assertEqual(saved[-1]['text'], '你好')
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

    async def test_history_api_and_continuation_after_service_restart(self):
        first = await self.collect('saved first conversation')
        first_id = first[0]['conversation_id']
        second = await self.collect('another conversation')
        listing = (await self.client.get('/api/conversations')).json()['conversations']
        self.assertEqual([row['id'] for row in listing], [second[0]['conversation_id'], first_id])
        detail = (await self.client.get(f'/api/conversations/{first_id}')).json()
        self.assertEqual([m['author']['kind'] for m in detail['messages']], ['owner', 'miko'])
        self.assertTrue(all(m['source'] == 'web' for m in detail['messages']))
        self.assertIsNone(detail['active_turn_id'])
        await self.service.close()
        with patch.object(Settings, 'load_provider', return_value=self.service.provider):
            restarted = ChatService(self.service.settings, ChatStore(self.store.path))
        # Keep the running test HTTP listener; replace its service state as a process restart would.
        self.service.__dict__.update(restarted.__dict__)
        continued = await self.collect('continue after restart', first_id)
        self.assertEqual(continued[-1]['status'], 'completed', continued)
        self.assertEqual([m['content'] for m in self.requests[-1]['messages'][1:]],
                         ['saved first conversation', '你好，Miko', 'continue after restart'])
        self.assertEqual((await self.client.get('/api/conversations/not-found')).status_code, 404)

    async def test_failure_is_kept_in_history_with_error(self):
        failed = await self.collect('fail')
        detail = (await self.client.get(f'/api/conversations/{failed[0]["conversation_id"]}')).json()
        self.assertEqual(detail['messages'][-1]['status'], 'error')
        self.assertIn('503', detail['messages'][-1]['error'])
        self.assertNotIn('fake-private-key', json.dumps(detail))

    async def test_failed_database_write_ends_stream_and_releases_turn(self):
        with patch.object(self.store, 'finish', side_effect=sqlite3.OperationalError('database is read-only')):
            with self.assertLogs('miko.domain.conversation.chat', level='ERROR'):
                events = await self.collect('hello')
        self.assertEqual(events[-1]['status'], 'error')
        self.assertIn('保存', events[-1]['error'])
        self.assertFalse(self.service.turns)
        self.assertEqual(self.store.history(events[0]['conversation_id']), [])

    async def test_qq_uses_approved_identity_and_author_records(self):
        records = [{'author': {'kind': 'owner', 'name': '你'}, 'text': '我稍后回来'},
                   {'author': {'kind': 'contact', 'name': '测试好友'}, 'text': '你好'}]
        turn = self.service.begin_qq('qq:123:456', records)
        await asyncio.wait_for(turn.task, 10)
        self.assertEqual(turn.status, 'completed')
        payload = self.requests[-1]
        self.assertTrue(payload['messages'][0]['content'].startswith(
            '你是Miko，是号主形影不离的朋友，请你以自己的身份帮号主回应好友消息。\n\n'))
        self.assertTrue(payload['messages'][0]['content'].endswith(
            '如果某个文字气泡介绍了你是 Miko，请在该气泡中添加 introduces_miko=true。'))
        self.assertEqual(json.loads(payload['messages'][1]['content'])['chat_records'], records)
        self.assertNotIn('tools', payload)
        self.assertEqual(payload['response_format'], {'type': 'json_object'})

    async def test_web_and_qq_share_reviewed_material_and_keep_relationships_separate(self):
        self.service.profiles.save(ProfileInput(subject='miko', kind='overview', title='Miko', content='喜欢平面设计').record())
        self.service.profiles.save(ProfileInput(subject='owner', kind='recent', title='近况', content='最近学习前端设计', as_of='2026-10-12').record())
        qq = QqStore(self.store)
        qq.add_friend('123','456','朋友甲')
        qq.add_friend('123','789','朋友乙')
        qq.update_friend(('123','456'), owner_relationship='多年同学', miko_relationship='刚认识', miko_introduced=1)
        qq.update_friend(('123','789'), miko_relationship='另一段交往')
        await self.collect('和朋友甲聊设计')
        web_prompt = self.requests[-1]['messages'][0]['content']
        web = json.loads(web_prompt.split('\n\n', 2)[2])
        records = [{'author': {'kind':'contact','name':'朋友甲'}, 'text':'你们最近在学什么设计？'}]
        turn = self.service.begin_qq('qq:123:456', records)
        await asyncio.wait_for(turn.task, 10)
        qq_prompt = self.requests[-1]['messages'][0]['content']
        data = json.loads(qq_prompt.split('\n\n', 2)[2].rsplit('\n\n', 1)[0])
        self.assertEqual(data['role_material'], web['role_material'])
        relation = data['friend_relationships'][0]
        self.assertEqual((relation['owner_relationship'], relation['miko_relationship']), ('多年同学', '刚认识'))
        self.assertEqual(len(data['friend_relationships']), 1)
        self.assertTrue(relation['miko_introduced'])


if __name__ == "__main__":
    unittest.main()
