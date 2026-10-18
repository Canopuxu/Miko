from dataclasses import dataclass
import json
from pathlib import Path

from urllib.parse import urlparse
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

REPO_ROOT = Path(__file__).resolve().parents[4]
CHAT_PROMPT_FILES = {'web': 'system-prompt.txt', 'qq': 'qq-expression.txt',
                     'qq_base': 'qq-system-prompt.txt', 'life': 'life-generation.txt'}


def chat_prompts(local_root):
    path = Path(local_root) / 'config/expression.local.json'
    saved = json.loads(path.read_text('utf-8-sig')) if path.exists() else {}
    defaults = {key: (REPO_ROOT / 'config/defaults' / name).read_text('utf-8').strip()
                for key, name in CHAT_PROMPT_FILES.items()}
    defaults['life'] = defaults['life'].rsplit('\n\n', 1)[0]
    return {key: saved.get(key, value) for key, value in defaults.items()}, defaults


def save_chat_prompts(local_root, values):
    current, defaults = chat_prompts(local_root)
    values = {**current, **{key: value for key, value in values.items() if value is not None}}
    root = Path(local_root) / 'config'
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / 'expression.local.json',
               {key: text for key, text in values.items() if text != defaults[key]})


def life_generation_prompt(local_root):
    preferences = chat_prompts(local_root)[0]['life']
    output = (REPO_ROOT / 'config/defaults/life-generation.txt').read_text('utf-8').strip().rsplit('\n\n', 1)[1]
    return preferences + '\n\n' + output


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    api_base: str
    api_key: SecretStr
    model: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_endpoint(self):
        parsed = urlparse(self.api_base)
        if (parsed.username or parsed.password or parsed.query or parsed.fragment
                or not parsed.hostname or (parsed.scheme != "https" and not (
                    parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")))):
            raise ValueError("模型地址需要无凭据参数的 HTTPS，或本机 HTTP 地址")
        if not self.api_key.get_secret_value().strip():
            raise ValueError("模型密钥为空")
        self.api_base = self.api_base.rstrip("/")
        return self


class LocalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    port: int = Field(default=7870, ge=1024, le=65535, strict=True)


class RerankConfig(ProviderConfig):
    format: Literal['dashscope', 'standard'] = 'dashscope'


def legacy_rerank(provider):
    if not provider:
        return None
    url = urlparse(provider.api_base)
    return RerankConfig(api_base=url.scheme + '://' + url.netloc +
        '/api/v1/services/rerank/text-rerank/text-rerank',
        api_key=provider.api_key, model='qwen3.7-text-rerank')


@dataclass(frozen=True)
class Settings:
    local_root: Path
    port: int

    @classmethod
    def load(cls, local_root: Path) -> "Settings":
        root = local_root.resolve()
        config = LocalConfig.model_validate_json(
            (root / "config/miko.local.json").read_text(encoding="utf-8-sig")
        )
        return cls(root, config.port)

    @property
    def dsh_home(self) -> Path:
        return self.local_root / "dsh/home"

    @property
    def agents_home(self) -> Path:
        return self.local_root / "dsh/agents"

    @property
    def workspace(self) -> Path:
        return self.local_root / "dsh/workspace"

    @property
    def process_file(self) -> Path:
        return self.local_root / "runtime/pids/backend.json"

    @property
    def database_path(self) -> Path:
        return self.local_root / "data/database/miko.sqlite3"

    @property
    def python_executable(self) -> Path:
        if (REPO_ROOT / 'portable.json').is_file():
            return REPO_ROOT / 'python/python.exe'
        return self.local_root / 'envs/python/Scripts/python.exe'

    @property
    def python_root(self) -> Path:
        if (REPO_ROOT / 'portable.json').is_file():
            return REPO_ROOT / 'python'
        return self.local_root / 'envs/python'

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def prepare(self) -> None:
        for path in (self.dsh_home, self.agents_home, self.workspace,
                     self.process_file.parent, self.local_root / "logs",
                     self.local_root / "cache/dsh-resources", self.local_root / "cache/tmp"):
            path.mkdir(parents=True, exist_ok=True)

    def paths(self) -> dict[str, str]:
        return {
            "local_root": str(self.local_root),
            "dsh_home": str(self.dsh_home),
            "agents_home": str(self.agents_home),
            "workspace": str(self.workspace),
        }

    def load_provider(self) -> ProviderConfig | None:
        path = self.local_root / "config/provider.local.json"
        if not path.exists():
            return None
        return ProviderConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))

    def load_qq(self):
        from miko.adapters.qq.onebot import QqConfig
        path = self.local_root / 'config/qq.local.json'
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        enabled = value.pop('enabled', True)
        return QqConfig.model_validate(value) if enabled else None

    def load_qq_provider(self) -> ProviderConfig | None:
        path = self.local_root / 'config/qq-provider.local.json'
        return ProviderConfig.model_validate_json(path.read_text(encoding='utf-8-sig')) if path.exists() else None

    def load_task_provider(self, task: Literal['life', 'memory']) -> ProviderConfig | None:
        path = self.local_root / f'config/{task}-provider.local.json'
        return ProviderConfig.model_validate_json(path.read_text(encoding='utf-8-sig')) if path.exists() else None

    def load_rerank(self) -> RerankConfig | None:
        path = self.local_root / 'config/rerank.local.json'
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        value.pop('shared', None)
        if not value.pop('enabled', True):
            return None
        return RerankConfig.model_validate(value)


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
