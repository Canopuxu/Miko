from importlib.metadata import version
from pathlib import Path

from deepseek_harness import HarnessClient, HarnessConfig
from deepseek_harness_runtime import bundled_runtime_path
import psutil

from miko.settings import Settings


class DshRuntime:
    def __init__(self, settings: Settings, *, base_url: str = "http://127.0.0.1:9",
                 token: str = "miko-local-handshake-only", model: str = "deepseek-chat",
                 patches: tuple[str, ...] = (), system_prompt: str | None = None, max_tokens: int = 2048):
        self.settings = settings
        self.base_url, self.token, self.model = base_url, token, model
        self.patches, self.system_prompt = patches, system_prompt
        self.max_tokens = max_tokens
        self.client: HarnessClient | None = None
        self.process: psutil.Process | None = None
        self.server_info: dict = {}
        self.executable = Path(bundled_runtime_path()).resolve()
        # Use the runtime installed in Miko's environment, never an executable from PATH.
        if not self.executable.is_relative_to(settings.python_root.resolve()):
            raise RuntimeError("DSH runtime must be installed in Miko's bundled Python environment")
        if version("deepseek-harness-sdk") != version("deepseek-harness-runtime-bin"):
            raise RuntimeError("DSH SDK and runtime versions differ")

    def start(self) -> None:
        settings = self.settings
        environment = {
            "DSH_HOME": str(settings.dsh_home),
            "DSH_AGENTS_HOME": str(settings.agents_home),
            "DSH_RESOURCE_CACHE": str(settings.local_root / "cache/dsh-resources"),
            "DSH_PRIMARY_RUNTIME": "", "DSH_OFFICE_SIDECAR": "", "DSH_RUNTIME_MODE": "exe",
            "TEMP": str(settings.local_root / "cache/tmp"),
            "TMP": str(settings.local_root / "cache/tmp"),
            "DEEPSEEK_BASE_URL": self.base_url, "DEEPSEEK_API_KEY": self.token,
            "API_KEY": "", "OPENAI_API_KEY": "",
        }
        if self.system_prompt is not None:
            environment["DSH_SYSTEM_PROMPT"] = self.system_prompt
        client = HarnessClient(HarnessConfig(
            dsh_bin=str(self.executable),
            profile="sdk-minimal",
            dsh_home=str(settings.dsh_home),
            cwd=str(settings.workspace),
            env=environment,
            patches=self.patches,
            initialize_timeout_seconds=30,
            request_timeout_seconds=15,
            shutdown_timeout_seconds=5,
        ))
        self.client = client
        try:
            client.start()
            result = client.initialize(
                cwd=str(settings.workspace), provider="deepseek-official", model=self.model,
                reasoning_effort="high", max_tokens=self.max_tokens,
            )
            self.server_info = result.model_dump(exclude_none=True)
        except BaseException:
            self.close()
            raise
        # The pinned SDK has no public process-status accessor.
        self.process = psutil.Process(client._proc.pid)

    @property
    def ready(self) -> bool:
        return self.client is not None and self.process is not None and self.process.is_running()

    def close(self) -> None:
        if self.client is not None:
            process = self.client._proc
            try:
                self.client.close()
            finally:
                # SDK 0.1.5rc1 waits for its readers but leaves the output pipes open.
                if process is not None:
                    for pipe in (process.stdout, process.stderr):
                        if pipe is not None:
                            pipe.close()
                self.client = None
                self.process = None

    def status(self) -> dict:
        return {
            "ready": self.ready,
            "profile": "sdk-minimal",
            "sdk_version": version("deepseek-harness-sdk"),
            "runtime_version": version("deepseek-harness-runtime-bin"),
            "executable": str(self.executable),
            "server": self.server_info,
        }
