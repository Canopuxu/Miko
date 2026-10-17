"""Miko's short dated activities, using the embedded Life Scheduler store."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime
from zoneinfo import ZoneInfo
import json
import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from miko.adapters.life.store import ScheduleData, ScheduleDataManager
from miko.domain.life_material import generation_material
from miko.settings import REPO_ROOT, life_generation_prompt
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


class ConversationActivity(BaseModel):
    kind: Literal['experience', 'plan', 'cancelled']
    date: date | None
    period: Literal['清晨', '上午', '中午', '下午', '傍晚', '晚上', '深夜'] | None
    people: list[str]
    text: str = Field(min_length=1, max_length=240)
    source_ids: list[int] = Field(min_length=1)


class ConversationActivities(BaseModel):
    events: list[ConversationActivity] = Field(max_length=20)


class Life:
    def __init__(self, chat):
        self.chat = chat
        self.provider = chat.settings.load_task_provider('life')
        self.store = ScheduleDataManager(chat.settings.local_root / 'data/life/entries.json')
        self.config_path = chat.settings.local_root / 'config/life.local.json'
        self.enabled = json.loads(self.config_path.read_text(encoding='utf-8-sig')).get('enabled', True) if self.config_path.exists() else True
        self.model = json.loads(self.config_path.read_text(encoding='utf-8-sig')).get('model') if self.config_path.exists() else None
        if self.provider:
            self.model = self.provider.model
        self.generating = False
        self.error = None
        self.task = None
        self.attempted_day = None

    def records(self, query=''):
        selected = terms(query) if query.strip() else set()
        result = []
        names = self.chat.store.identity()
        for day, row in sorted(self.store.all().items(), reverse=True):
            for entry in row.entries:
                entry = names.material(entry)
                if not selected or selected & terms(entry['text']):
                    result.append({'day': day, **entry})
        return result

    def goals_context(self, query=''):
        selected = terms(query) if query.strip() else set()
        associated = {r.get('goal_id') for r in self.context(query)}
        return [g for g in self.chat.store.identity().material(list(self.store.goals.values()))
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
        return self.chat.store.identity().material({'status': self.status(), 'day': day.isoformat(),
                'record': asdict(value) if value else {'date': day.isoformat(), 'entries': [], 'revision': 0, 'source': ''},
                'dates': sorted(self.store.all(), reverse=True),
                'goals': list(self.store.goals.values()), 'revision': self.store.revision,
                'trajectory': self.records()})

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

    async def conversation_activities(self, material, day):
        background = material.pop('对话提炼记录', {})
        quotes = []
        for rows in background.values():
            for row in rows:
                for quote in row.get('相关原话', []):
                    if quote not in quotes:
                        quotes.append(quote)
        if not quotes:
            return []
        quotes.sort(key=lambda q: q.get('发言时间', ''))
        names = self.chat.store.identity()
        quotes = [{**quote, '原话': names.display(quote['原话'])} for quote in quotes]
        prompt = (REPO_ROOT / 'config/defaults/life-conversation.txt').read_text(encoding='utf-8').strip()
        message = json.dumps({'生活日期': day.isoformat(),
                              '原话': [{'id': i + 1, **q} for i, q in enumerate(quotes)]}, ensure_ascii=False)
        result = await self.chat.complete_memory(self.chat.named_prompt(prompt), message,
            model=self.model or None, json_output=True, **({'provider': self.provider} if self.provider else {}))
        activities = ConversationActivities.model_validate_json(result['text']).events
        character = self.chat.store.character_name()
        kept = []
        for activity in activities:
            if any(i < 1 or i > len(quotes) for i in activity.source_ids):
                raise ValueError('生活背景引用了不存在的原话')
            # A fictional reply on its own does not establish another person's
            # real-world arrangements. Undated plans also stay in chat memory.
            human_source = any(quotes[i - 1]['发言人'] in activity.people
                               and quotes[i - 1]['发言人'] != character
                               for i in activity.source_ids)
            if (character in activity.people and human_source and activity.date
                    and activity.date <= day and activity.kind != 'cancelled'):
                kept.append(activity)
        today = [a for a in kept if a.date == day][:2]
        past = [a.text for a in kept if a.date < day and a.kind == 'experience'][-1:]
        if past:
            material['过去的共同经历'] = past
        if today:
            # The writer only fills unoccupied time. It never rewrites these
            # facts, so a friend's name cannot turn into the owner's name.
            material['已占用时段'] = [a.period or '部分时间' for a in today]
            material['本次补充片段数'] = max(2, 4 - len(today))
        return today

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
            memory = self.chat.memory
            events = memory.structured.days(subject='miko') if memory and memory.social.control()['enabled'] else []
            material = generation_material(self.chat.profiles, self.records(), self.store.goals, day, memory_events=events)
            confirmed = await self.conversation_activities(material, day)
            # A manual addition to this day is already a fact to continue from.
            if current:
                material['今天已有的记录'] = current.entries
            prompt = life_generation_prompt(self.chat.settings.local_root)
            message = self.chat.named_prompt(json.dumps(material, ensure_ascii=False))
            value = await self.chat.complete_memory(self.chat.named_prompt(prompt), message, model=self.model or None,
                json_output=True, **({'provider': self.provider} if self.provider else {}))
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
            generated = output.entries
            if confirmed:
                # Keep ordinary prose generation unchanged; requesting a full
                # time grid encourages filling every slot with extra activity.
                hours = {'清晨': 6, '早上': 8, '早晨': 8, '上午': 10, '中午': 12, '午后': 14,
                         '下午': 15, '傍晚': 18, '晚上': 20, '晚间': 20, '夜里': 22, '深夜': 23, '凌晨': 0}
                ordered = []
                for i, entry in enumerate(generated):
                    cue = re.search('|'.join(hours), entry.text[:16])
                    hour = hours[cue[0]] if cue else 8 + 14 * i / max(1, len(generated) - 1)
                    ordered.append((hour, entry))
                ordered.extend((hours.get(a.period, 12), LifeEntry(text=a.text)) for a in confirmed)
                generated = [entry for _, entry in sorted(ordered, key=lambda pair: pair[0])]
            for entry in generated:
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
