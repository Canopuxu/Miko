import argparse
import asyncio
from copy import deepcopy
import os
from pathlib import Path
import secrets
import signal
import threading

import psutil
import uvicorn

from miko.api.app import create_app
from miko.cli import open_ui, recorded_process, request
from miko.settings import Settings, write_json


def run(settings: Settings, *, open_browser: bool = False) -> None:
    settings.prepare()
    control_token = os.environ.pop("MIKO_CONTROL_TOKEN", None)
    foreground = control_token is None
    if foreground:
        from miko.restore import recover_interrupted_restore
        recover_interrupted_restore(settings)
        previous = recorded_process(settings)
        if previous:
            record, _ = previous
            url = f"http://127.0.0.1:{record['port']}"
            if request(url + '/health').get('ready') and open_browser:
                open_ui(url)
            print(f"Miko is already running at {url}", flush=True)
            return
        control_token = secrets.token_urlsafe(32)
    os.environ['TEMP'] = os.environ['TMP'] = str(settings.local_root / 'cache/tmp')
    server: uvicorn.Server

    def request_stop() -> None:
        server.should_exit = True

    app = create_app(settings, control_token, request_stop)
    log_config = deepcopy(uvicorn.config.LOGGING_CONFIG)
    if foreground:
        log_config['formatters']['file'] = {
            'format': '%(asctime)s %(levelname)s %(name)s: %(message)s',
        }
        log_config['handlers']['file'] = {
            'class': 'logging.FileHandler', 'formatter': 'file',
            'filename': str(settings.local_root / 'logs/backend.log'), 'encoding': 'utf-8',
        }
        log_config['loggers']['uvicorn']['handlers'].append('file')
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=settings.port, access_log=False, log_level="info",
        log_config=log_config,
    ))
    if foreground:
        process = psutil.Process()
        write_json(settings.process_file, {'pid': process.pid, 'created': process.create_time(),
                   'port': settings.port, 'control_token': control_token})
    finished = threading.Event()
    def open_when_ready():
        while not finished.wait(.1):
            if server.started:
                open_ui(settings.url)
                return
    if open_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
    try:
        server.run()
    finally:
        finished.set()
        if foreground:
            from miko.adapters.qq.managed import ManagedQq
            asyncio.run(ManagedQq(settings.local_root).disconnect())
            settings.process_file.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-root", type=Path, required=True)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if os.name == 'nt':
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    try:
        run(Settings.load(args.local_root), open_browser=args.open_browser)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
