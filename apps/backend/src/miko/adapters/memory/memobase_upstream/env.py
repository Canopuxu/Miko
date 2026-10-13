import logging
from dataclasses import dataclass, field
from typing import Literal, Optional
from types import SimpleNamespace

class ContanstTable:
    topic = "topic"
    sub_topic = "sub_topic"
    memo = "memo"
    update_hits = "update_hits"

    roleplay_plot_status = "roleplay_plot_status"


@dataclass
class ProfileConfig:
    language: Literal["en", "zh"] = None
    profile_strict_mode: bool | None = None
    profile_validate_mode: bool | None = None
    additional_user_profiles: list[dict] = field(default_factory=list)
    overwrite_user_profiles: Optional[list[dict]] = None
    event_theme_requirement: Optional[str] = None

    event_tags: list[dict] = None

    def __post_init__(self):
        if self.language not in ["en", "zh"]:
            self.language = None
        if self.additional_user_profiles:
            [UserProfileTopic(**up) for up in self.additional_user_profiles]
        if self.overwrite_user_profiles:
            [UserProfileTopic(**up) for up in self.overwrite_user_profiles]

    @classmethod
    def load_config_string(cls, config_string: str) -> "Config":
        overwrite_config = yaml.safe_load(config_string)
        if overwrite_config is None:
            return cls()
        # Get all field names from the dataclass
        fields = {field.name for field in dataclasses.fields(cls)}
        # Filter out any keys from overwrite_config that aren't in the dataclass
        filtered_config = {k: v for k, v in overwrite_config.items() if k in fields}
        overwrite_config = cls(**filtered_config)
        return overwrite_config


LOG = logging.getLogger("memobase-core-validation")
CONFIG = SimpleNamespace(llm_tab_separator="::", language="zh", system_prompt=None,
    overwrite_user_profiles=[{'topic': '基本信息', 'sub_topics': []}, {'topic': '兴趣', 'sub_topics': []}, {'topic': '学习与工作', 'sub_topics': []}, {'topic': '人物关系', 'sub_topics': []}],
    additional_user_profiles=[], event_tags=[])
