"""Return selectable logged-in QQ clients to the local settings page."""
import argparse
import json
from pathlib import Path

from qq_clients import discover


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--local-root', type=Path, required=True)
    parser.add_argument('--pid', type=int)
    args = parser.parse_args()
    path = args.local_root / 'config/qq-client.local.json'
    config = json.loads(path.read_text('utf-8-sig')) if path.exists() else {}
    clients = discover(args.local_root, config.get('frida_path'), active=config, pid=args.pid)
    print(json.dumps({'clients': clients}, ensure_ascii=True))
