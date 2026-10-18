"""Generate one whole day, including its records and goal changes."""
import json
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from miko.settings import life_generation_prompt


class Activity(BaseModel):
    model_config = ConfigDict(extra='ignore', str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=240)
    goal_id: str | None = Field(default=None, min_length=1)
    result: str | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def progress_has_result(self):
        if self.goal_id and not self.result:
            self.result = self.text
        return self


class NewGoal(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    ref: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=240)
    done_when: str = Field(min_length=1, max_length=500)
    target_date: date


class GoalUpdate(BaseModel):
    model_config = ConfigDict(extra='ignore')
    id: str
    status: str | None = None
    target_date: date | None = None

    @model_validator(mode='after')
    def valid_status(self):
        if self.status not in (None, 'active', 'completed', 'paused'):
            raise ValueError('目标状态无效')
        return self


class GeneratedDay(BaseModel):
    model_config = ConfigDict(extra='forbid')
    entries: list[Activity] = Field(default_factory=list)
    new_goals: list[NewGoal] = Field(default_factory=list)
    goal_updates: list[GoalUpdate] = Field(default_factory=list)

    @property
    def progress(self):
        return [row for row in self.entries if row.goal_id]

    @model_validator(mode='after')
    def unique_events(self):
        # The timeline already folds same-day progress into one node. Persist
        # that same unit: preparation and execution finish at the last position,
        # and the last result describes the goal's resulting state.
        entries = []
        for row in self.entries:
            previous = next((old for old in entries if row.goal_id and old.goal_id == row.goal_id), None)
            if previous:
                entries.remove(previous)
                row = Activity(text=previous.text + '；' + row.text, goal_id=row.goal_id, result=row.result)
            entries.append(row)
        self.entries = entries
        updates = [row.id for row in self.goal_updates]
        if len(updates) != len(set(updates)):
            raise ValueError('目标状态变更重复')
        return self


async def generate_day(chat, material, *, model=None, provider=None):
    options = dict(model=model, json_output=True, allow_reasoning=True,
                   **({'provider': provider} if provider else {}))
    response = await chat.complete_memory(chat.named_prompt(life_generation_prompt(chat.settings.local_root)),
        chat.named_prompt(json.dumps(material, ensure_ascii=False)), **options)
    return GeneratedDay.model_validate_json(response['text'])
