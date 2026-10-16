"""Miko's short dated activities, using the embedded Life Scheduler store."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime
from zoneinfo import ZoneInfo
import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from miko.adapters.life.store import ScheduleData, ScheduleDataManager
from miko.domain.life_material import generation_material
from miko.settings import REPO_ROOT
from miko.storage.search import terms


class LifeEntry(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=240)
    goal_id: str | None = None


class LifeEntries(BaseModel):
    entries: list[LifeEntry] = Field(max_length=100)


GoalStatus = Literal['active', 'completed', 'paused']


class GoalInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=240)
    target_date: date
    status: GoalStatus = 'active'


class NewGoal(BaseModel):
    ref: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=240)
    target_date: date


class GoalUpdate(BaseModel):
    id: str
    status: GoalStatus | None = None
    target_date: date | None = None


class LifeGeneration(LifeEntries):
    entries: list[LifeEntry] = Field(max_length=20)
    new_goals: list[NewGoal] = Field(default_factory=list, max_length=10)
    goal_updates: list[GoalUpdate] = Field(default_factory=list)


class Life:
    def __init__(self, chat):
        self.chat = chat
        self.store = ScheduleDataManager(chat.settings.local_root / 'data/life/entries.json')
        self.config_path = chat.settings.local_root / 'config/life.local.json'
        self.enabled = json.loads(self.config_path.read_text(encoding='utf-8-sig')).get('enabled', True) if self.config_path.exists() else True
        self.model = json.loads(self.config_path.read_text(encoding='utf-8-sig')).get('model') if self.config_path.exists() else None
        self.generating = False
        self.error = None
        self.task = None
        self.attempted_day = None

    def records(self, query=''):
        selected = terms(query) if query.strip() else set()
        result = []
        for day, row in sorted(self.store.all().items(), reverse=True):
            for entry in row.entries:
                if not selected or selected & terms(entry['text']):
                    result.append({'day': day, **entry})
        return result

    def goals_context(self, query=''):
        selected = terms(query) if query.strip() else set()
        associated = {r.get('goal_id') for r in self.context(query)}
        return [g for g in self.store.goals.values()
                if selected & terms(g['text']) or g['id'] in associated][:3]

    def context(self, query=''):
        if not query.strip():
            return []
        today = date.today().isoformat()
        return [r for r in self.records(query) if r['day'] <= today][:3]

    def status(self):
        return {'enabled': self.enabled, 'generating': self.generating, 'error': self.error,
                'days': len(self.store.all()), 'today_recorded': self.store.has(date.today())}

    def listing(self, day=None):
        day = day or date.today()
        value = self.store.get(day)
        return {'status': self.status(), 'day': day.isoformat(),
                'record': asdict(value) if value else {'date': day.isoformat(), 'entries': [], 'revision': 0, 'source': ''},
                'dates': sorted(self.store.all(), reverse=True),
                'goals': list(self.store.goals.values()), 'revision': self.store.revision,
                'trajectory': self.records()}

    def validate_links(self, entries, goals):
        for entry in entries:
            if entry.get('goal_id') and entry['goal_id'] not in goals:
                raise ValueError('活动关联的目标不存在')

    def save_goal(self, value, revision, goal_id=None):
        goals = deepcopy(self.store.goals)
        if goal_id and goal_id not in goals:
            raise ValueError('目标不存在，请刷新后重试')
        goal_id = goal_id or uuid4().hex
        goal = goals.get(goal_id, {'id': goal_id, 'start_date': date.today().isoformat()})
        goal.update(value.model_dump(mode='json'))
        if goal['status'] == 'completed':
            goal.setdefault('completed_date', date.today().isoformat())
        else:
            goal.pop('completed_date', None)
        goals[goal_id] = goal
        self.store.commit(None, goals, revision)
        return self.listing()

    def save(self, day, entries, revision, source='manual'):
        current = self.store.get(day)
        if (current.revision if current else 0) != revision:
            raise ValueError('这一天的记录已更新，请刷新后再保存')
        entries = [r.model_dump(exclude_none=True) for r in LifeEntries(entries=entries).entries]
        self.validate_links(entries, self.store.goals)
        entries = [r for index, r in enumerate(entries) if r not in entries[:index]]
        self.store.set(ScheduleData(day.isoformat(), entries, revision + 1, source))
        return self.listing(day)

    def toggle(self, enabled):
        self.enabled = enabled
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps({'enabled': enabled, 'model': self.model}), encoding='utf-8')
        return self.status()

    async def generate(self, day=None):
        if self.generating:
            raise ValueError('生活条目正在生成')
        self.generating = True
        day = day or date.today()
        self.attempted_day = day
        current = self.store.get(day)
        revision = current.revision if current else 0
        store_revision = self.store.revision
        try:
            material = generation_material(self.chat.profiles, self.records(), self.store.goals, day)
            # A manual addition to this day is already a fact to continue from.
            if current:
                material['今天已有的记录'] = current.entries
            prompt = (REPO_ROOT / 'config/defaults/life-generation.txt').read_text(encoding='utf-8').strip()
            value = await self.chat.complete_memory(prompt, json.dumps(material, ensure_ascii=False), model=self.model or None, json_output=True)
            output = LifeGeneration.model_validate_json(value['text'])
            goals = deepcopy(self.store.goals)
            for update in output.goal_updates:
                if update.id not in goals:
                    raise ValueError('生成结果引用了不存在的目标')
                goal = goals[update.id]
                goal.update(update.model_dump(mode='json', exclude_none=True, exclude={'id'}))
                if goal['status'] == 'completed':
                    goal.setdefault('completed_date', day.isoformat())
                else:
                    goal.pop('completed_date', None)
            references = {key: key for key in goals}
            for new in output.new_goals:
                if new.ref in references:
                    raise ValueError('新目标的临时编号重复')
                if new.target_date < day:
                    raise ValueError('新目标的预计完成日期早于今天')
                goal_id = uuid4().hex
                references[new.ref] = goal_id
                goals[goal_id] = {'id': goal_id, 'text': new.text, 'start_date': day.isoformat(),
                                  'target_date': new.target_date.isoformat(), 'status': 'active'}
            entries = deepcopy(current.entries) if current else []
            for entry in output.entries:
                row = entry.model_dump(exclude_none=True)
                if row.get('goal_id'):
                    if row['goal_id'] not in references:
                        raise ValueError('活动关联的目标不存在')
                    row['goal_id'] = references[row['goal_id']]
                if row not in entries:
                    entries.append(row)
            self.validate_links(entries, goals)
            self.store.commit(ScheduleData(day.isoformat(), entries, revision + 1, 'generated'),
                              goals, store_revision)
            self.error = None
        except Exception as exc:
            self.error = str(exc) if isinstance(exc, ValueError) else '生活生成未完成（' + type(exc).__name__ + '）'
            raise
        finally:
            self.generating = False
        return self.listing(day)

    def start(self):
        self.task = asyncio.create_task(self.background())

    def automatic_due(self, now=None):
        now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
        return (self.enabled and bool(self.chat.provider) and now.hour >= 22
                and not self.store.has(now.date()) and self.attempted_day != now.date())

    async def background(self):
        while True:
            if self.automatic_due():
                try:
                    await self.generate(datetime.now(ZoneInfo('Asia/Shanghai')).date())
                except Exception:
                    pass  # Keep the previous entries; the page offers an explicit retry.
            await asyncio.sleep(60)

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
