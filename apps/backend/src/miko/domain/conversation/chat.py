import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
import json
import logging
import re
import secrets
import sqlite3
import threading
import time
import uuid

from deepseek_harness.api import Session
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
import httpx

from miko.adapters.dsh.runtime import DshRuntime
from miko.adapters.qq.messages import FACES, OUTPUT_SCHEMA
from miko.settings import REPO_ROOT, Settings, ProviderConfig
from miko.storage.chat import ChatStore
from miko.storage.profiles import ProfileStore
from miko.domain.conversation.context_selection import compose_qq_context, render_dialogue


def reply_context(context):
    """Keep conversational material; evidence and diagnostics stay in the preview."""
    result = {'current_time': context['current_time']}
    fields = {
        'role_material': ('subject', 'kind', 'category', 'title', 'content', 'as_of', 'source'),
        'friend_relationships': ('name', 'owner_relationship', 'miko_relationship', 'miko_introduced'),
        'social_memory': ('subject', 'peer', 'automatic', 'manual_corrections'),
        'people_memory': ('subject', 'peer', 'category', 'topic', 'content'),
        'dated_experiences': ('subject', 'subjects', 'day', 'text'),
        'owner_feedback': ('reply', 'feedback'),
        'life_entries': ('day', 'text'),
        'life_goals': ('text', 'start_date', 'target_date', 'status', 'completed_date'),
    }
    for group, names in fields.items():
        rows = []
        for original in context.get(group, []):
            row = {name: original[name] for name in names if original.get(name) not in (None, '')}
            if group in ('life_entries', 'life_goals'):
                row['subject'] = 'miko'
            if row.get('subject') in ('owner', 'miko'):
                row['subject_name'] = {'owner': '橙汁', 'miko': 'Miko'}[row['subject']]
            if group == 'social_memory' and not (row.get('automatic') or row.get('manual_corrections')):
                continue
            if group == 'dated_experiences':
                row['text'] = re.sub(r'[ \t]*//[ \t]*(event|schedule)[ \t]*$', '', row['text'], flags=re.MULTILINE)
            if row not in rows:
                rows.append(row)
        if rows:
            result[group] = rows
    return result


@dataclass
class Turn:
    id: str
    conversation_id: str
    message: str
    user_message_id: str = ""
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
    last_checkpoint: float = 0
    context: list[dict] | None = None
    system_prompt: str | None = None
    status: str = 'running'
    json_output: bool = False
    memory_query: str | None = None
    memory_key: tuple | None = None
    model: str | None = None
    usage: dict = field(default_factory=dict)
    qq_records: list[dict] | None = None
    qq_input: dict | None = None
    warning: str | None = None
    provider_override: ProviderConfig | None = None

    def close_runtime(self):
        with self.lifecycle:
            if self.runtime is not None:
                self.runtime.close()


class ChatService:
    def __init__(self, settings: Settings, store: ChatStore | None = None):
        self.settings = settings
        self.provider = settings.load_provider()
        self.qq_provider = settings.load_qq_provider()
        self.system_prompt = (REPO_ROOT / "config/defaults/system-prompt.txt").read_text(encoding="utf-8").strip()
        self.bridge_token = secrets.token_urlsafe(32)
        self.store = store or ChatStore(settings.database_path)
        self.profiles = ProfileStore(self.store)
        self.store.recover_interrupted()
        self.turns: dict[str, Turn] = {}
        self.memory = None
        self.life = None
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
                "qq_model": (self.qq_provider or self.provider).model if (self.qq_provider or self.provider) else None,
                "active_turns": len(self.turns)}

    def begin(self, message: str, conversation_id: str | None) -> Turn:
        if self.provider is None:
            raise HTTPException(503, "尚未配置模型，请到设置中填写 API 与模型")
        if not message.strip():
            raise HTTPException(422, "消息不能为空")
        if conversation_id is not None:
            existing = self.store.conversation(conversation_id)
            if existing is None or existing['source'] != 'web':
                raise HTTPException(404, "找不到这条对话，请开启新对话")
        conversation_id = conversation_id or uuid.uuid4().hex
        if any(t.conversation_id == conversation_id for t in self.turns.values()):
            raise HTTPException(409, "当前对话仍在生成，请先停止或等待结束")
        turn = Turn(uuid.uuid4().hex, conversation_id, message.strip())
        query = turn.message + '\n' + '\n'.join(r['content'] for r in self.store.history(conversation_id)[-6:][::-1])
        turn.system_prompt = self.compose_prompt(self.system_prompt, query)
        turn.memory_query = query
        turn.user_message_id = self.store.begin_turn(conversation_id, turn.id, turn.message)
        self.turns[turn.id] = turn
        turn.task = asyncio.create_task(self.run(turn))
        return turn

    def compose_prompt(self, base, query, key=None):
        instructions = (REPO_ROOT / 'config/defaults/role-context.txt').read_text(encoding='utf-8').strip()
        context = self.profiles.context(query, key)
        if self.life:
            context['life_entries'] = self.life.context(query)
            context['life_goals'] = self.life.goals_context(query)
            if context['life_entries'] or context['life_goals']:
                instructions += '\n' + (REPO_ROOT / 'config/defaults/life-context.txt').read_text(encoding='utf-8').strip()
        return self.named_prompt(base + '\n\n' + instructions + '\n\n' + json.dumps(reply_context(context), ensure_ascii=False))

    def named_prompt(self, text):
        names = {'Miko': self.store.character_name(), '橙汁': self.store.person_name('owner')}
        return re.sub(r'(?<![A-Za-z0-9_])Miko(?![A-Za-z0-9_])|橙汁', lambda match: names[match[0]], text)

    def canonical_query(self, query):
        names = {self.store.character_name().casefold(): 'Miko', self.store.person_name('owner').casefold(): '橙汁'}
        pattern = '|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True))
        return re.sub(pattern, lambda match: names[match[0].casefold()], query, flags=re.IGNORECASE)

    async def run(self, turn: Turn):
        status = "error"
        try:
            if turn.qq_records is not None:
                background = await compose_qq_context(self, turn.qq_records, turn.memory_key)
                group = turn.memory_key[1].startswith('group-')
                language = getattr(self, 'qq_language', None)
                if group and language:
                    reference = await language.reference(turn.memory_key, turn.qq_records, turn.id)
                    if reference:
                        background += '\n\n' + reference
                turn.system_prompt = '\n\n'.join([
                    (REPO_ROOT / 'config/defaults' / name).read_text(encoding='utf-8').strip()
                    for name in ('qq-system-prompt.txt', 'qq-group-context.txt' if group else 'qq-role-context.txt')
                ] + [background] + [
                    (REPO_ROOT / 'config/defaults' / name).read_text(encoding='utf-8').strip()
                    for name in ('qq-expression.txt', 'qq-output-format.txt')
                ])
                if group:
                    turn.system_prompt += '\n\n' + (REPO_ROOT / 'config/defaults/qq-group-output.txt').read_text(encoding='utf-8').strip()
            elif self.memory and turn.memory_query is not None:
                context = await self.memory.context(turn.memory_query, turn.memory_key, turn.conversation_id)
                base = self.system_prompt if turn.memory_key is None else (REPO_ROOT / 'config/defaults/qq-system-prompt.txt').read_text(encoding='utf-8').strip()
                instructions = (REPO_ROOT / 'config/defaults/role-context.txt').read_text(encoding='utf-8').strip()
                if context.get('life_entries') or context.get('life_goals'):
                    instructions += '\n' + (REPO_ROOT / 'config/defaults/life-context.txt').read_text(encoding='utf-8').strip()
                turn.system_prompt = base + '\n\n' + instructions + '\n\n' + json.dumps(reply_context(context), ensure_ascii=False)
                if context.get('owner_feedback'):
                    turn.system_prompt += '\n\n' + (REPO_ROOT / 'config/defaults/feedback-context.txt').read_text(encoding='utf-8').strip()
                if turn.memory_key:
                    turn.system_prompt += '\n\n' + (REPO_ROOT / 'config/defaults/qq-output-format.txt').read_text(encoding='utf-8').strip()
            turn.system_prompt = self.named_prompt(turn.system_prompt or self.system_prompt)
            turn.runtime = DshRuntime(
                self.settings, base_url=f"{self.settings.url}/_runtime/llm/{turn.id}",
                token=self.bridge_token, model=turn.model or self.provider_for(turn).model,
                patches=(str(self.patch),), system_prompt=turn.system_prompt or self.system_prompt,
                max_tokens=8192 if turn.conversation_id == 'memory' or turn.memory_key is not None else 2048,
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
                status = "completed"
            else:
                turn.error = "生成未正常完成，请重试"
        except Exception:
            status = "cancelled" if turn.cancelled else "error"
            if status == "error" and not turn.error:
                logging.getLogger(__name__).exception('Unable to generate chat turn')
                turn.error = "生成连接中断，请重试"
        finally:
            await asyncio.to_thread(turn.close_runtime)
            try:
                if turn.user_message_id:
                    self.store.finish(turn.id, turn.text, status, turn.error)
            except sqlite3.Error:
                logging.getLogger(__name__).exception("Unable to save completed chat turn")
                status = 'error'
                turn.error = '回复未能完整保存，请检查本机存储后重试'
            self.turns.pop(turn.id, None)
            turn.status = status
            turn.events.put_nowait({"type": "done", "status": status,
                                    "text": turn.text, "error": turn.error})

    def begin_qq(self, conversation_id: str, records: list[dict], stickers: list[dict] | None = None) -> Turn:
        prompt_path = REPO_ROOT / 'config/defaults/qq-system-prompt.txt'
        if self.provider is None or not prompt_path.exists():
            raise ValueError('QQ 模型或已审核提示词尚未就绪')
        records = [{**r, 'author': {**r['author'], 'name': '橙汁'}}
                   if r.get('author', {}).get('kind') == 'owner' else r for r in records]
        target = next((r for r in reversed(records) if r.get('author', {}).get('kind') == 'contact'), None)
        message = json.dumps({'reply_to': {k: target[k] for k in ('author', 'message_id', 'text') if k in target} if target else None,
                              'chat_records': [{k: v for k, v in r.items() if k != '_image_inputs'} for r in records], 'available_faces': FACES,
                              'available_stickers': stickers or [], 'output_format': {'bubbles': [
                                  bubble for bubble in OUTPUT_SCHEMA['bubbles']
                                  if bubble['kind'] != 'face' or FACES
                                  if bubble['kind'] != 'sticker' or stickers
                              ]}}, ensure_ascii=False)
        output = (REPO_ROOT / 'config/defaults/qq-output-format.txt').read_text(encoding='utf-8').strip()
        _, account, friend = conversation_id.split(':')
        query = '\n'.join(r['text'] for r in records[-8:][::-1])
        turn = Turn(uuid.uuid4().hex, conversation_id, message,
                    context=[{'role': 'user', 'content': message}],
                    system_prompt=self.compose_prompt(prompt_path.read_text(encoding='utf-8').strip(), query, (account, friend)) + '\n\n' + output,
                    json_output=True)
        turn.memory_query = query
        turn.memory_key = (account, friend)
        turn.qq_records = records
        turn.qq_input = json.loads(message)
        if friend.startswith('group-'):
            conversation = self.store.conversation(conversation_id)
            turn.qq_input['group'] = {'id': friend[6:], 'name': conversation['title'] if conversation else friend[6:]}
            mention_ids = list(dict.fromkeys(r['author']['id'] for r in records if r['author']['kind'] == 'contact'))
            for bubble in turn.qq_input['output_format']['bubbles']:
                if bubble['kind'] == 'text':
                    bubble.update(reply_to_message_id='optional integer',
                        mention_user_ids='optional array; available author ids: ' + json.dumps(mention_ids, ensure_ascii=False))
            reactions = getattr(self, 'qq_reactions', None)
            available, targets = reactions.catalog((account, friend), records) if reactions else ([], [])
            if available:
                turn.system_prompt += '\n\n' + (REPO_ROOT / 'config/defaults/qq-reactions.txt').read_text(encoding='utf-8').strip()
                turn.qq_input['available_reactions'] = available
                turn.qq_input['reaction_targets'] = targets
                turn.qq_input['output_format']['bubbles'].append(dict(kind='reaction', message_id='current group message integer',
                    reaction_id='candidate string', operation='add or remove'))
        turn.message = render_dialogue(turn.qq_input)
        image_parts = [part for r in records for part in r.get('_image_inputs', [])]
        content = [{'type': 'text', 'text': turn.message}, *image_parts] if image_parts else turn.message
        turn.context = [{'role': 'user', 'content': content}]
        self.turns[turn.id] = turn
        turn.task = asyncio.create_task(self.run(turn))
        return turn

    async def summarize_memory(self, payload):
        message = json.dumps(payload, ensure_ascii=False)
        prompt = (REPO_ROOT / 'config/defaults/memory-consolidation.txt').read_text(encoding='utf-8').strip()
        turn = Turn(uuid.uuid4().hex, 'memory', message, context=[{'role': 'user', 'content': message}],
                    system_prompt=prompt, json_output=True)
        self.turns[turn.id] = turn
        turn.task = asyncio.create_task(self.run(turn))
        try:
            await asyncio.wait_for(asyncio.shield(turn.task), timeout=140)
            if turn.status != 'completed':
                raise ValueError(turn.error or '记忆整理模型请求未完成')
            return json.loads(turn.text)
        finally:
            if not turn.task.done():
                await self.cancel(turn)
                await turn.task

    async def complete_memory(self, system, message, model=None, json_output=False, provider=None, image_inputs=None):
        content = [{'type': 'text', 'text': message}, *image_inputs] if image_inputs else message
        turn = Turn(uuid.uuid4().hex, 'memory', message,
                    context=[{'role': 'user', 'content': content}], system_prompt=system, model=model,
                    json_output=json_output, provider_override=provider)
        self.turns[turn.id] = turn
        turn.task = asyncio.create_task(self.run(turn))
        try:
            await asyncio.wait_for(asyncio.shield(turn.task), timeout=150)
            if turn.status != 'completed':
                raise ValueError(turn.error or '记忆整理模型请求未完成')
            return {'text': turn.text, 'usage': turn.usage}
        finally:
            if not turn.task.done():
                await self.cancel(turn)
                await turn.task

    def conversation_detail(self, conversation_id: str) -> dict:
        conversation = self.store.conversation(conversation_id)
        if conversation is None or conversation['source'] != 'web':
            raise HTTPException(404, "找不到这条对话")
        active = next((t.id for t in self.turns.values() if t.conversation_id == conversation_id), None)
        return {**conversation, 'messages': self.store.messages(conversation_id), 'active_turn_id': active}

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
        provider = self.provider_for(turn)
        payload = {
            "model": turn.model or provider.model,
            "messages": [{"role": "system", "content": turn.system_prompt or self.system_prompt}]
                        + (turn.context if turn.context is not None else self.store.history(turn.conversation_id)
                           + [{"role": "user", "content": turn.message}]),
            "stream": True, "max_tokens": 8192 if turn.conversation_id == 'memory' or turn.memory_key is not None else 2048,
        }
        if turn.json_output:
            payload['response_format'] = {'type': 'json_object'}
        if turn.qq_records is not None:
            payload['thinking'] = {'type': 'disabled'}
            payload['max_tokens'] = 2048
        if turn.conversation_id == 'memory':
            payload['thinking'] = {'type': 'disabled'}
            payload['stream_options'] = {'include_usage': True}
        client = httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15), trust_env=False)
        try:
            for attempt in range(2):
                if turn.cancelled:
                    raise asyncio.CancelledError
                turn.provider_task = asyncio.create_task(client.send(client.build_request(
                    "POST", provider.api_base + "/chat/completions", json=payload,
                    headers={"Authorization": "Bearer " + provider.api_key.get_secret_value()},
                ), stream=True))
                remote = await turn.provider_task
                has_images = any(isinstance(message.get('content'), list) and
                    any(part.get('type') == 'image_url' for part in message['content'])
                    for message in payload['messages'])
                if attempt == 0 and turn.qq_input is not None and has_images and remote.status_code in (400, 415, 422):
                    await remote.aclose()
                    text_input = deepcopy(turn.qq_input)
                    for record in text_input['chat_records']:
                        for segment in record.get('segments', []):
                            if segment.get('type') == 'image':
                                segment['content_status'] = 'unavailable'
                    turn.message = render_dialogue(text_input)
                    turn.context = [{'role': 'user', 'content': turn.message}]
                    payload['messages'] = [payload['messages'][0], *turn.context]
                    turn.warning = '模型接口未接受图片，本轮已自动改用文字处理。可在模型设置中更换支持图片输入的模型。'
                    continue
                break
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
            if turn.warning:
                turn.error = '图片请求被拒，改用文字后仍失败：' + turn.error
            await remote.aclose()
            await client.aclose()
            raise HTTPException(502, "Provider request failed")
        turn.remote = remote

        async def stream():
            finished, done = False, False
            try:
                async for line in remote.aiter_lines():
                    if turn.cancelled:
                        break
                    if line.startswith('data:') and line[5:].strip() == '[DONE]':
                        done = True
                    elif line.startswith("data:"):
                        chunk = json.loads(line[5:].strip())
                        if chunk.get('usage'):
                            turn.usage = chunk['usage']
                        for choice in chunk.get("choices", []):
                            if choice.get('index',0)==0 and choice.get('finish_reason'):
                                finished = True
                                if choice['finish_reason'] == 'length':
                                    turn.error = '模型输出达到长度上限，未完成本轮生成'
                            text = (choice.get("delta") or {}).get("content")
                            if choice.get("index", 0) == 0 and isinstance(text, str) and text:
                                turn.text += text
                                now = time.monotonic()
                                if turn.user_message_id and now - turn.last_checkpoint >= .5:
                                    self.store.checkpoint(turn.id, turn.text)
                                    turn.last_checkpoint = now
                                turn.events.put_nowait({"type": "delta", "text": text})
                    yield (line + "\n").encode("utf-8")
                if not done and not turn.cancelled:
                    if finished:
                        yield b'data: [DONE]\n\n'
                    else:
                        turn.error = '模型输出未完整结束，请重试'
            except (httpx.HTTPError, ValueError):
                if not turn.cancelled:
                    turn.error = "模型输出中断，请重试"
            finally:
                await remote.aclose()
                await client.aclose()
                turn.remote = None

        return StreamingResponse(stream(), media_type="text/event-stream")

    def provider_for(self, turn):
        return turn.provider_override or ((self.qq_provider or self.provider) if turn.memory_key is not None else self.provider)
