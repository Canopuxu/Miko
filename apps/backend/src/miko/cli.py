import argparse
from importlib.metadata import version
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, build_opener, ProxyHandler

import psutil

from miko.adapters.dsh.runtime import DshRuntime
from miko.settings import Settings, write_json


# Local control requests must not follow the user's HTTP proxy configuration.
HTTP = build_opener(ProxyHandler({}))


def request(url: str, token: str | None = None) -> dict:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    req = Request(url, data=None if token is None else b"", headers=headers)
    with HTTP.open(req, timeout=2) as response:
        return json.load(response)


def recorded_process(settings: Settings) -> tuple[dict, psutil.Process] | None:
    if not settings.process_file.exists():
        return None
    record = json.loads(settings.process_file.read_text(encoding="utf-8"))
    try:
        process = psutil.Process(record["pid"])
        command = process.cmdline()
        expected_python = settings.python_executable
        if (process.create_time() != record["created"]
                or Path(process.exe()).resolve() != expected_python.resolve()
                or "miko.server" not in command
                or str(settings.local_root) not in command):
            # Migration and PID reuse can leave an unrelated live process here.
            # Forget the record; never send that process a control request or signal.
            settings.process_file.unlink(missing_ok=True)
            return None
        return record, process
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        settings.process_file.unlink(missing_ok=True)
        return None


def start(settings: Settings) -> None:
    from miko.restore import recover_interrupted_restore
    recover_interrupted_restore(settings)
    previous = recorded_process(settings)
    if previous:
        record, _ = previous
        status = request(f"http://127.0.0.1:{record['port']}/health")
        print(json.dumps({"already_running": True, "url": f"http://127.0.0.1:{record['port']}", **status}))
        return
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", settings.port))
    token = secrets.token_urlsafe(32)
    env = os.environ.copy()
    env["MIKO_CONTROL_TOKEN"] = token
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["TEMP"] = str(settings.local_root / "cache/tmp")
    env["TMP"] = str(settings.local_root / "cache/tmp")
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with (settings.local_root / "logs/backend.log").open("ab") as log:
        child = subprocess.Popen(
            [str(settings.python_executable), "-X", "utf8", "-m", "miko.server", "--local-root", str(settings.local_root)],
            cwd=settings.workspace, env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, **kwargs,
        )
    record = {"pid": child.pid, "created": psutil.Process(child.pid).create_time(),
              "port": settings.port, "control_token": token}
    write_json(settings.process_file, record)
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError("Backend exited during startup; see MikoLocal/logs/backend.log")
            try:
                status = request(settings.url + "/health")
                if status.get("service") == "miko" and status.get("ready"):
                    print(json.dumps({"url": settings.url, "pid": child.pid, **status}))
                    return
            except (URLError, TimeoutError, ConnectionError):
                pass
            time.sleep(0.2)
        raise RuntimeError("Backend startup timed out; see MikoLocal/logs/backend.log")
    except BaseException:
        stop(settings)
        raise


def stop(settings: Settings) -> None:
    found = recorded_process(settings)
    if found is None:
        print("Miko is stopped.")
        return
    record, process = found
    try:
        request(f"http://127.0.0.1:{record['port']}/_runtime/stop", record["control_token"])
        process.wait(timeout=15)
    except (URLError, TimeoutError, ConnectionError, psutil.TimeoutExpired):
        # A restore worker may be a child of the backend it is stopping.
        children = [child for child in process.children(recursive=True) if child.pid != os.getpid()]
        for child in reversed(children):
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        try:
            process.terminate()
            process.wait(timeout=5)
        except psutil.NoSuchProcess:
            pass
        _, alive = psutil.wait_procs(children, timeout=5)
        for child in alive:
            child.kill()
    settings.process_file.unlink(missing_ok=True)
    print("Miko is stopped.")


def check(settings: Settings) -> None:
    runtime = DshRuntime(settings)
    try:
        runtime.start()
        result = {
            "python": sys.executable,
            "fastapi_version": version("fastapi"),
            "paths": settings.paths(),
            "dsh": runtime.status(),
            "model_requests": 0,
        }
    finally:
        runtime.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "stop", "check", "status"))
    parser.add_argument("--local-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        settings = Settings.load(args.local_root)
        settings.prepare()
        if args.action == "status":
            print(json.dumps(request(settings.url + "/api/status"), indent=2))
        else:
            {"start": start, "stop": stop, "check": check}[args.action](settings)
    except Exception as error:
        print(f"Miko {args.action} failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
