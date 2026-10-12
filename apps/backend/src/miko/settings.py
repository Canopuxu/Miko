from dataclasses import dataclass
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class LocalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    port: int = Field(default=7870, ge=1024, le=65535, strict=True)


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


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
