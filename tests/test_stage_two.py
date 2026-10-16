import asyncio
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
import httpx
import psutil
import uvicorn

from miko.api.chat import chat_router
from miko.domain.conversation.chat import ChatService
from miko.adapters.dsh.runtime import DshRuntime
from miko.settings import ProviderConfig, Settings, REPO_ROOT
from miko.storage.chat import ChatStore
from miko.storage.qq import QqStore
from miko.api.profiles import ProfileInput


LOCAL_ROOT = Path(__file__).resolve().parents[2] / "MikoLocal"


class StageTwoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        self.requests = []
        self.remote_closed = asyncio.Event()
        self.waiting_headers = asyncio.Event()
        self.temporary = tempfile.TemporaryDirectory()
        settings = Settings(Path(self.temporary.name), port)
        settings.prepare()
        def isolated_runtime(settings, **kwargs):
            # Validate the installed runtime, keeping all test state in its temporary root.
            runtime = DshRuntime(Settings(LOCAL_ROOT, settings.port), **kwargs)
            runtime.settings = settings
            return runtime
        self.runtime_patch = patch('miko.domain.conversation.chat.DshRuntime', side_effect=isolated_runtime)
        self.runtime_patch.start()
        self.store = ChatStore(Path(self.temporary.name) / 'chat.sqlite3')
        config = ProviderConfig(api_base=settings.url + "/fake", api_key="fake-private-key", model="fake-model")
        with patch.object(Settings, "load_provider", return_value=config), patch.object(Settings, "load_qq_provider", return_value=None):
            self.service = ChatService(settings, self.store)
        async def context(query, key=None, conversation_id=None):
            material = self.service.profiles.context(query, key)
            if self.service.life:
                material['life_entries'] = self.service.life.context(query)
                material['life_goals'] = self.service.life.goals_context(query)
            return material
        self.service.memory = SimpleNamespace(context=context, reranker=None)
        app = FastAPI()
        app.include_router(chat_router(self.service))

        @app.post("/fake/chat/completions")
        async def provider(request: Request):
            payload = await request.json()
            self.requests.append(payload)
            if getattr(self, 'reject_images', False) and isinstance(payload['messages'][-1]['content'], list):
                return JSONResponse({'error': {'message': 'image_url is not supported; fake-private-key'}}, status_code=400)
            if getattr(self, 'reject_text_fallback', False):
                return JSONResponse({'error': {'message': 'invalid request; fake-private-key'}}, status_code=400)
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
                    if message != 'truncated':
                        yield self.chunk("", "stop")
                    if message not in ('no-marker','truncated'):
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
        self.runtime_patch.stop()
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

    async def test_complete_stream_without_done_and_real_truncation(self):
        normal=await self.collect('no-marker')
        self.assertEqual(normal[-1]['status'],'completed')
        broken=await self.collect('truncated')
        self.assertEqual(broken[-1]['status'],'error')
        self.assertEqual(self.store.history(broken[0]['conversation_id']),[])

    async def test_cancel_stream_then_continue(self):
        async with self.client.stream("POST", "/api/chat", json={"message": "slow"}) as response:
            iterator = self.events(response)
            first = await anext(iterator)
            delta = await anext(iterator)
            self.assertEqual(delta["type"], "delta")
            turn = self.service.turns[first["turn_id"]]
            pid = turn.runtime.process.pid
            environment = turn.runtime.process.environ()
            self.assertEqual(environment["DSH_HOME"], str(self.service.settings.dsh_home))
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
        self.assertEqual(turn.status, 'completed', turn.error)
        payload = self.requests[-1]
        self.assertTrue(payload['messages'][0]['content'].startswith(
            (REPO_ROOT / 'config/defaults/qq-system-prompt.txt').read_text(encoding='utf-8').strip()))
        self.assertIn('如果某个文字气泡介绍了你是 Miko，请在该气泡中添加 introduces_miko=true。', payload['messages'][0]['content'])
        self.assertTrue(payload['messages'][0]['content'].endswith(
            (REPO_ROOT / 'config/defaults/qq-output-format.txt').read_text(encoding='utf-8').strip()))
        sent = payload['messages'][1]['content']
        self.assertIn('橙汁：我稍后回来', sent)
        self.assertIn('测试好友：你好', sent)
        self.assertIn('本次回应：\n[] 测试好友：你好', sent)
        self.assertEqual(records[0]['author']['name'], '你')
        self.assertNotIn('tools', payload)
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertEqual(payload['max_tokens'], 2048)

    async def test_qq_image_parts_reach_provider_request(self):
        image_url = 'data:image/jpeg;base64,aW1hZ2U='
        records = [{'author': {'kind': 'contact', 'name': '测试好友'}, 'message_id': 42,
                    'text': '[图片]', '_image_inputs': [
                        {'type': 'text', 'text': '消息 42 · 图片 1'},
                        {'type': 'image_url', 'image_url': {'url': image_url}}]}]
        turn = self.service.begin_qq('qq:123:456', records)
        await asyncio.wait_for(turn.task, 10)
        self.assertEqual(turn.status, 'completed', turn.error)
        content = self.requests[-1]['messages'][1]['content']
        self.assertIsInstance(content, list)
        self.assertIn('测试好友：[图片]', content[0]['text'])
        self.assertEqual(content[-1], records[0]['_image_inputs'][-1])

    async def test_image_rejection_retries_text_once_with_warning(self):
        self.reject_images = True
        records = [{'author': {'kind': 'contact', 'name': '测试好友'}, 'message_id': 42,
                    'text': '[图片]', 'segments': [{'type': 'image', 'content_status': 'attached'}], '_image_inputs': [
                        {'type': 'text', 'text': '消息 42 · 图片 1'},
                        {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,aW1hZ2U='}}]}]
        turn = self.service.begin_qq('qq:123:456', records)
        await asyncio.wait_for(turn.task, 10)
        self.assertEqual(turn.status, 'completed', turn.error)
        self.assertIn('自动改用文字处理', turn.warning)
        self.assertNotIn('fake-private-key', turn.warning)
        self.assertEqual(len(self.requests), 2)
        self.assertIsInstance(self.requests[0]['messages'][-1]['content'], list)
        fallback = self.requests[1]['messages'][-1]['content']
        self.assertIsInstance(fallback, str)
        self.assertIn('"content_status": "unavailable"', fallback)
        self.assertNotIn('image_url', fallback)
        self.assertNotIn('消息 42 · 图片 1', fallback)
        self.assertEqual(self.requests[0]['messages'][0], self.requests[1]['messages'][0])
        self.assertEqual(records[0]['segments'][0]['content_status'], 'attached')

    async def test_rejected_text_fallback_stops_after_two_requests(self):
        self.reject_images = self.reject_text_fallback = True
        records = [{'author': {'kind': 'contact', 'name': '测试好友'}, 'message_id': 42,
                    'text': '[图片]', '_image_inputs': [
                        {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,aW1hZ2U='}}]}]
        turn = self.service.begin_qq('qq:123:456', records)
        await asyncio.wait_for(turn.task, 10)
        self.assertEqual(turn.status, 'error')
        self.assertIn('改用文字后仍失败', turn.error)
        self.assertNotIn('fake-private-key', turn.error)
        self.assertEqual(len(self.requests), 2)

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
        records = [{'author': {'kind':'contact','name':'朋友甲'}, 'text':'你和橙汁最近在学什么设计？'}]
        turn = self.service.begin_qq('qq:123:456', records)
        await asyncio.wait_for(turn.task, 10)
        self.assertEqual(turn.status, 'completed', turn.error)
        qq_prompt = self.requests[-1]['messages'][0]['content']
        self.assertIn('喜欢平面设计', qq_prompt)
        self.assertIn('最近学习前端设计', qq_prompt)
        self.assertIn('橙汁与朋友甲：多年同学', qq_prompt)
        self.assertIn('Miko与朋友甲：刚认识', qq_prompt)
        self.assertIn('Miko 已向朋友甲介绍过自己', qq_prompt)
        self.assertNotIn('另一段交往', qq_prompt)
        self.assertEqual(len(web['role_material']), 2)

    async def test_web_and_qq_read_the_same_dated_life_entries(self):
        entries = [{'day': '2026-10-13', 'text': '摄影：练构图'}]
        goals = [{'id': 'private-goal-id', 'text': '拍一组窗边照片', 'start_date': '2026-10-13',
                  'target_date': '2026-10-20', 'status': 'active'}]
        self.service.life = SimpleNamespace(context=lambda query: entries, goals_context=lambda query: goals,
                                            records=lambda: entries)
        await self.collect('你最近在做什么')
        web_prompt = self.requests[-1]['messages'][0]['content']
        web = json.loads(web_prompt.split('\n\n', 2)[2])
        turn = self.service.begin_qq('qq:123:456', [{'author': {'kind': 'contact', 'name': '朋友甲'}, 'text': '你在做什么'}])
        await asyncio.wait_for(turn.task, 10)
        self.assertEqual(turn.status, 'completed', turn.error)
        qq_prompt = self.requests[-1]['messages'][0]['content']
        self.assertEqual(web['life_entries'], [{**entries[0], 'subject': 'miko', 'subject_name': 'Miko'}])
        self.assertIn('Miko，2026-10-13：摄影：练构图', qq_prompt)
        self.assertIn('拍一组窗边照片', qq_prompt)
        self.assertEqual(web['life_goals'][0]['status'], 'active')
        self.assertEqual(web['life_goals'][0]['subject_name'], 'Miko')
        self.assertNotIn('id', web['life_goals'][0])
        self.assertIn('日期表示记录时间', web_prompt)
        self.assertNotIn('private-goal-id', qq_prompt)

    async def test_reply_uses_compact_memory_and_preserves_review_data(self):
        from copy import deepcopy
        material = self.service.profiles.context('摄影')
        material.update({
            'social_memory': [{'subject': 'miko', 'peer': 'qq:123:456', 'automatic': '',
                'manual_corrections': '和朋友甲刚认识', 'sources': [{'content': '证据原文保留供回顾'}],
                'episodes': [[{'content': '旧聊天片段'}]]}],
            'people_memory': [{'id': 'internal-memory-id', 'subject': 'miko', 'peer': '',
                'category': '兴趣', 'topic': '摄影', 'content': '开始学摄影', 'updated_at': 123}],
            'dated_experiences': [{'id': 'internal-day-id', 'subject': 'miko', 'day': '2026-10-12',
                'text': '开始学摄影；计划明天练构图。',
                'sources': [{'content': '事件依据'}], 'scope_id': 2}],
            'retrieval': {'engine': 'memU', 'hits': [{'name': 'internal-memory-id', 'score': .9}]},
        })
        original = deepcopy(material)

        async def context(*args):
            return material
        self.service.memory = SimpleNamespace(context=context, reranker=None)
        await self.collect('最近在做什么')
        web_prompt = self.requests[-1]['messages'][0]['content']
        web = json.loads(web_prompt.split('\n\n', 2)[2])
        turn = self.service.begin_qq('qq:123:456', [{'author': {'kind': 'contact', 'name': '朋友甲'},
                                                'text': '最近在做什么'}])
        await asyncio.wait_for(turn.task, 10)
        self.assertEqual(turn.status, 'completed', turn.error)
        prompt = self.requests[-1]['messages'][0]['content']
        self.assertEqual(web['people_memory'][0]['content'], '开始学摄影')
        self.assertIn('和朋友甲刚认识', prompt)
        self.assertIn('2026-10-12：开始学摄影；计划明天练构图。', prompt)
        self.assertNotIn('// event', prompt)
        self.assertNotIn('// schedule', prompt)
        for omitted in ('证据原文保留供回顾', '旧聊天片段', '事件依据', 'internal-memory-id', 'retrieval'):
            self.assertNotIn(omitted, web_prompt)
            self.assertNotIn(omitted, prompt)
        self.assertEqual(material, original)


if __name__ == "__main__":
    unittest.main()
