import datetime
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Union

# =========================
# 类型定义
# =========================

ScheduleStatus = Literal["ok", "failed"]

DateLike = Union[  # noqa: UP007
    datetime.datetime,
    datetime.date,
    int,  # timestamp
    float,  # timestamp
]


# =========================
# 工具函数（时间归一化）
# =========================


def to_date_str(value: DateLike) -> str:
    """统一将时间输入转为 yyyy-mm-dd 字符串"""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, int | float):
        return datetime.datetime.fromtimestamp(value).date().isoformat()
    raise TypeError(f"Unsupported date type: {type(value)}")


# =========================
# 数据结构
# =========================


@dataclass(slots=True)
class ScheduleData:
    """单日数据（date 只作为内部 key，不对外暴露格式责任）"""

    date: str  # yyyy-mm-dd
    entries: list[dict] = field(default_factory=list)
    revision: int = 1
    source: str = "generated"
    progress: list[dict] = field(default_factory=list)

    def __post_init__(self):
        # Older snapshots stored both kinds together; retain every original text.
        if any(row.get('goal_id') for row in self.entries):
            self.entries = [dict(row, order=row.get('order', index)) for index, row in enumerate(self.entries)]
        linked = [row for row in self.entries if row.get('goal_id')]
        self.entries = [row for row in self.entries if not row.get('goal_id')]
        self.progress = linked + self.progress

    @property
    def activities(self):
        rows = [*self.entries, *self.progress]
        return [row for _, row in sorted(enumerate(rows), key=lambda pair: pair[1].get('order', pair[0]))]

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduleData":
        """允许未来字段扩展"""
        return cls(
            date=data["date"],
            entries=[{**{key: row[key] for key in ('goal_id', 'result', 'order') if key in row},
                      "text": (row["topic"] + "：" + row["text"]
                               if row.get("topic") and row["topic"] != "活动或兴趣"
                               and row["topic"] not in row["text"] else row["text"])}
                     for row in data.get("entries", [])],
            revision=data.get("revision", 1),
            source=data.get("source", "generated"),
            progress=[dict(row) for row in data.get('progress', [])],
        )


# =========================
# 数据管理器（纯存取）
# =========================


class ScheduleDataManager:
    """
    纯数据层：
    - 内存存取
    - JSON 持久化
    """

    def __init__(self, json_path: Path):
        self._path = json_path
        self._data: dict[str, ScheduleData] = {}
        self.goals: dict[str, dict] = {}
        self.revision = 0

        self.load()

    # ---------- 基础 CRUD ----------

    def has(self, date: DateLike) -> bool:
        return to_date_str(date) in self._data

    def get(self, date: DateLike) -> ScheduleData | None:
        return self._data.get(to_date_str(date))

    def set(self, data: ScheduleData) -> None:
        self._data[data.date] = data
        self.save()

    def remove(self, date: DateLike) -> None:
        if self._data.pop(to_date_str(date), None):
            self.save()

    def all(self) -> dict[str, ScheduleData]:
        """返回副本，防止外部污染"""
        return dict(self._data)

    # ---------- JSON 持久化 ----------

    def load(self) -> None:
        """从 JSON 加载（文件不存在则视为空）"""
        if not self._path.exists():
            self._data.clear()
            return

        raw = json.loads(self._path.read_text(encoding="utf-8"))

        data: dict[str, ScheduleData] = {}
        if 'days' in raw:
            self.goals = raw.get('goals', {})
            self.revision = raw.get('revision', 0)
            raw = raw['days']
        for date_str, item in raw.items():
            if not isinstance(item, dict):
                continue
            try:
                data[date_str] = ScheduleData.from_dict(item)
            except Exception:
                continue

        self._data = data

    def save(self) -> None:
        """保存为 JSON（原子写）"""
        self._path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = self._path.with_suffix(".tmp")
        payload = {'days': {date: asdict(data) for date, data in self._data.items()},
                   'goals': self.goals, 'revision': self.revision + 1}

        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self._path)
        self.revision += 1

    def commit(self, data: ScheduleData | None, goals: dict[str, dict], revision: int) -> None:
        """Publish one generation's goals and activities in the same atomic file."""
        if self.revision != revision:
            raise ValueError('生活记录或目标已更新，请刷新后再生成')
        previous_data, previous_goals = dict(self._data), self.goals
        if data:
            self._data[data.date] = data
        self.goals = goals
        try:
            self.save()
        except Exception:
            self._data, self.goals = previous_data, previous_goals
            raise

    # ---------- 工具方法 ----------

    def clear(self, *, save: bool = True) -> None:
        """清空所有数据"""
        self._data.clear()
        self.goals.clear()
        if save:
            self.save()
