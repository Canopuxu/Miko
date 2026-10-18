"""Miko scheduling and UI adapter for Tulpa's scoped language library."""
import asyncio
import json
import logging
from pathlib import Path
import time
import uuid

from miko.settings import REPO_ROOT


class GroupLanguage:
    def __init__(self, qq, root: Path, *, clock=time.time):
        from miko.adapters.qq.tulpa_language.mcp_language_store import LanguageStore
        self.qq, self.chat, self.clock = qq, qq.chat, clock
        self.store = LanguageStore(root / 'data/qq/language.sqlite3', clock=clock)
        self.epoch = uuid.uuid4().hex
        self.bindings, self.tasks = {}, {}
        self.loop_task = None
        with self.store.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS miko_language_sources(
                scope TEXT,source_id TEXT,message_id INTEGER,sent_at REAL,
                PRIMARY KEY(scope,source_id))''')

    def binding(self, key):
        cfg = self.qq.store.friend(key)
        if not cfg or cfg['chat_type'] != 2:
            raise ValueError('表达学习仅用于已添加的群聊')
        if key not in self.bindings:
            scope = cfg['conversation_id']
            b = dict(scope=scope, gid='miko:' + scope, sid=uuid.uuid4().hex, epoch=self.epoch)
            new = self.store.pin(b['gid']) is None
            self.store.attach(b)
            if new:
                self.store.configure(b, enabled=False)
            self.bindings[key] = b
        return self.bindings[key]

    def start(self):
        self.loop_task = asyncio.create_task(self.loop())

    async def reset(self):
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
        self.store.cancel()
        self.bindings.clear()
        self.epoch = uuid.uuid4().hex

    async def close(self):
        if self.loop_task:
            self.loop_task.cancel()
            await asyncio.gather(self.loop_task, return_exceptions=True)
        await self.reset()

    async def pause(self, key):
        if key in self.bindings:
            self.store.cancel(self.bindings.pop(key))
        if key in self.tasks:
            self.tasks[key].cancel()
            await asyncio.gather(self.tasks.pop(key), return_exceptions=True)

    async def configure(self, key, enabled):
        self.store.configure(self.binding(key), enabled=enabled)
        if key in self.tasks:
            self.tasks[key].cancel()
            await asyncio.gather(self.tasks.pop(key), return_exceptions=True)
        return self.status(key)

    def observe(self, key, mid, stamp, segments):
        from miko.adapters.qq.tulpa_language.mcp_language_store import digest
        b = self.binding(key)
        text = ''.join(s.get('text', '') for s in segments if s['type'] == 'text').strip()
        if not text:
            return
        self.store.observe(b, dict(peer=True, source_id=str(mid), sent_at=stamp, at=stamp, text=text))
        source = digest([b['scope'], b['epoch'], digest([b['scope'], str(mid), stamp])])
        with self.store.db() as db:
            if db.execute('SELECT 1 FROM observed_ids WHERE id=?',
                          (digest([b['scope'], str(mid), stamp]),)).fetchone():
                db.execute('INSERT OR IGNORE INTO miko_language_sources VALUES(?,?,?,?)',
                           (b['scope'], source, mid, stamp))

    def status(self, key):
        b = self.binding(key)
        result = self.store.status(b, records=True)
        with self.store.db() as db:
            for kind in ('expressions', 'jargon'):
                for item in result[kind]:
                    if kind == 'expressions':
                        row = db.execute('SELECT batch_id,source_id FROM expression_grounding WHERE scope=? AND item_id=?',
                                         (b['scope'], item['id'])).fetchone()
                    else:
                        row = db.execute('SELECT batch_id,body FROM jargon_evidence WHERE scope=? AND item_id=? ORDER BY expires DESC LIMIT 1',
                                         (b['scope'], item['id'])).fetchone()
                    item['source'] = None
                    if row:
                        source_id = row['source_id'] if kind == 'expressions' else json.loads(row['body'])['source_id']
                        source = db.execute('SELECT message_id,sent_at FROM miko_language_sources WHERE scope=? AND source_id=?',
                                            (b['scope'], source_id)).fetchone()
                        if source:
                            event = self.qq.store.get_event(key, source['message_id'])
                            if event:
                                item['source'] = dict(message_id=f"{b['scope']}:{source['message_id']}",
                                    text='这条消息已撤回' if event['recalled'] else event['text'],
                                    recalled=bool(event['recalled']), sent_at=source['sent_at'])
        return result

    def manage(self, key, kind, item_id, *, enabled=None, meaning=None, delete=False):
        b = self.binding(key)
        self.store.manage(b, kind, item_id, enabled=enabled, meaning=meaning)
        if delete:
            with self.store.db() as db:
                db.execute(f'DELETE FROM {kind} WHERE scope=? AND id=?', (b['scope'], item_id))
                tables = ('expression_hits', 'expression_grounding') if kind == 'expressions' else ('jargon_hits', 'jargon_evidence')
                for table in tables:
                    db.execute(f'DELETE FROM {table} WHERE scope=? AND item_id=?', (b['scope'], item_id))
                db.execute("UPDATE jobs SET state='cancelled',lease='' WHERE scope=? AND item_id=? AND state IN ('pending','leased')",
                           (b['scope'], item_id))
        return self.status(key)

    async def ask(self, prompt, payload, *, selection=False):
        settings = self.chat.settings
        provider = (self.chat.qq_provider or self.chat.provider) if selection else settings.load_task_provider('memory')
        model = None
        if not selection and provider is None:
            path = settings.local_root / 'config/memory.local.json'
            if path.exists():
                model = json.loads(path.read_text(encoding='utf-8-sig')).get('model') or None
        result = await self.chat.complete_memory(prompt, json.dumps(payload, ensure_ascii=False),
            model=model, provider=provider, json_output=True, allow_reasoning=not selection)
        return json.loads(result['text'])

    async def learn(self, key):
        from miko.adapters.qq.tulpa_language.mcp_language_store import LearningError
        b = self.binding(key)
        task = self.store.claim(b, uuid.uuid4().hex, reply_pending=self.busy(key))
        if not task:
            return
        invocation = uuid.uuid4().hex
        failure = None
        try:
            result = await self.ask(task['prompt'], dict(material=task['material'], json_output_format=task['result_schema']))
            self.store.submit(b, task['job_id'], task['lease'], invocation, result=result)
        except asyncio.CancelledError:
            raise
        except (ValueError, TimeoutError) as exc:
            if isinstance(exc, LearningError) and exc.code in ('task_unavailable', 'learning_disabled', 'lease_unavailable'):
                return
            failure = 'invalid_json' if isinstance(exc, json.JSONDecodeError) else 'timeout' if isinstance(exc, TimeoutError) else 'model_error'
            logging.getLogger(__name__).warning('Group language stage failed: %s', getattr(exc, 'code', type(exc).__name__))
        if failure:
            try:
                self.store.submit(b, task['job_id'], task['lease'], invocation, failure=failure)
            except LearningError:
                pass

    def busy(self, key):
        cfg = self.qq.store.friend(key)
        return key in self.qq.workers or cfg['due_at'] is not None

    async def tick(self):
        for key, task in list(self.tasks.items()):
            if task.done():
                if not task.cancelled() and task.exception():
                    logging.getLogger(__name__).error('Group language worker failed: %s', type(task.exception()).__name__)
                self.tasks.pop(key)
        for cfg in self.qq.store.friends():
            if cfg['chat_type'] != 2 or not cfg['enabled'] or cfg['blocked']:
                continue
            key = (cfg['account'], cfg['friend'])
            if key not in self.tasks and self.store.readiness(self.binding(key), reply_pending=self.busy(key))['ready']:
                self.tasks[key] = asyncio.create_task(self.learn(key))

    async def loop(self):
        while True:
            try:
                await self.tick()
            except Exception:
                logging.getLogger(__name__).exception('Unable to schedule group language learning')
            await asyncio.sleep(1)

    async def reference(self, key, records, plan_id):
        b = self.binding(key)
        if not self.store.status(b)['enabled']:
            return ''
        texts = [r['text'] for r in records[-8:] if r['author']['kind'] == 'contact']
        choice = self.store.context(b, plan_id, texts, '\n'.join(texts), include_expressions=False)
        if not self.store.status(b)['enabled']:
            return ''
        labels = (REPO_ROOT / 'config/defaults/qq-language-context.txt').read_text(encoding='utf-8').splitlines()
        if not choice['jargon']:
            return ''
        return labels[1] + '\n' + '\n'.join(f"- {r['term']}：{r['meaning']}" for r in choice['jargon'])
