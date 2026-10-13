"""Miko's short dated activities, using the embedded Life Scheduler store."""
import asyncio
from dataclasses import asdict
from datetime import date
import json

from pydantic import BaseModel, ConfigDict, Field

from miko.adapters.life.store import ScheduleData, ScheduleDataManager
from miko.domain.conversation.chat import reply_context
from miko.settings import REPO_ROOT
from miko.storage.search import terms


class LifeEntry(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    topic: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=240)


class LifeEntries(BaseModel):
    entries: list[LifeEntry] = Field(max_length=20)


class Life:
    def __init__(self, chat):
        self.chat = chat
        self.store = ScheduleDataManager(chat.settings.local_root / 'data/life/entries.json')
        self.config_path = chat.settings.local_root / 'config/life.local.json'
        self.enabled = json.loads(self.config_path.read_text(encoding='utf-8-sig')).get('enabled', True) if self.config_path.exists() else True
        self.generating = False
        self.error = None
        self.task = None
        self.attempted_day = None

    def records(self, query=''):
        selected = terms(query) if query.strip() else set()
        result = []
        for day, row in sorted(self.store.all().items(), reverse=True):
            for entry in row.entries:
                if not selected or selected & terms(entry['topic'] + ' ' + entry['text']):
                    result.append({'day': day, **entry})
        return result

    def context(self, query=''):
        today = date.today().isoformat()
        records = [r for r in self.records() if r['day'] <= today]
        latest = max((d for d in self.store.all() if d <= today), default='')
        current = [r for r in records if r['day'] == latest]
        matches = [r for r in self.records(query) if r['day'] <= today and r not in current] if query.strip() else []
        return (current + matches[:6])[:20]

    def status(self):
        return {'enabled': self.enabled, 'generating': self.generating, 'error': self.error,
                'days': len(self.store.all()), 'today_recorded': self.store.has(date.today())}

    def listing(self, day=None):
        day = day or date.today()
        value = self.store.get(day)
        return {'status': self.status(), 'day': day.isoformat(),
                'record': asdict(value) if value else {'date': day.isoformat(), 'entries': [], 'revision': 0, 'source': ''},
                'dates': sorted(self.store.all(), reverse=True)}

    def save(self, day, entries, revision, source='manual'):
        current = self.store.get(day)
        if (current.revision if current else 0) != revision:
            raise ValueError('这一天的记录已更新，请刷新后再保存')
        entries = [r.model_dump() for r in LifeEntries(entries=entries).entries]
        entries = [r for index, r in enumerate(entries) if r not in entries[:index]]
        self.store.set(ScheduleData(day.isoformat(), entries, revision + 1, source))
        return self.listing(day)

    def toggle(self, enabled):
        self.enabled = enabled
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps({'enabled': enabled}), encoding='utf-8')
        return self.status()

    async def generate(self):
        if self.generating:
            raise ValueError('生活条目正在生成')
        self.generating = True
        day = date.today()
        self.attempted_day = day
        current = self.store.get(day)
        revision = current.revision if current else 0
        try:
            material = reply_context(self.chat.profiles.context('兴趣 生活 学习 乐器 设计 运动'))
            # Stable Miko facts inform her activities; owner input stays recent.
            material['role_material'] = [r for r in material.get('role_material', [])
                                         if r['subject'] == 'miko' or r['kind'] == 'recent']
            material.pop('friend_relationships', None)
            material['生活条目'] = [
                {k: v for k, v in row.items() if not (k == 'topic' and v == '活动或兴趣')}
                for row in self.records()[:40] if row['day'] <= day.isoformat()
            ]
            material['日期'] = day.isoformat()
            prompt = (REPO_ROOT / 'config/defaults/life-generation.txt').read_text(encoding='utf-8').strip()
            value = await self.chat.complete_memory(prompt, json.dumps(material, ensure_ascii=False), json_output=True)
            output = LifeEntries.model_validate_json(value['text'])
            self.save(day, output.entries, revision, 'generated')
            self.error = None
        except Exception as exc:
            self.error = str(exc) if isinstance(exc, ValueError) else '生活生成未完成（' + type(exc).__name__ + '）'
            raise
        finally:
            self.generating = False
        return self.listing(day)

    def start(self):
        self.task = asyncio.create_task(self.background())

    async def background(self):
        # TODO: 按实际使用效果再调整生成频率与触发方式。
        while True:
            if self.enabled and self.chat.provider and not self.store.has(date.today()) and self.attempted_day != date.today():
                try:
                    await self.generate()
                except Exception:
                    pass  # Keep the previous entries; the page offers an explicit retry.
            await asyncio.sleep(60)

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
