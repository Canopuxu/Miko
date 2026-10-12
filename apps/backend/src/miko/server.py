import argparse
import os
from pathlib import Path

import uvicorn

from miko.api.app import create_app
from miko.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-root", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings.load(args.local_root)
    settings.prepare()
    control_token = os.environ.pop("MIKO_CONTROL_TOKEN")
    server: uvicorn.Server

    def request_stop() -> None:
        server.should_exit = True

    app = create_app(settings, control_token, request_stop)
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=settings.port, access_log=False, log_level="info"
    ))
    server.run()


if __name__ == "__main__":
    main()
