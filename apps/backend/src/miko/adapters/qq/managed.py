"""Resolve a local QQ bridge without asking users to copy ports or tokens."""
import json
from pathlib import Path

import psutil

from miko.adapters.qq.onebot import QqConfig
from miko.adapters.qq.snowluma_config import adapters, read_json, SetupError
from miko.settings import write_json


def bridge_folders(local_root):
    folders = [local_root / 'runtime/qq-bridge']
    registration = local_root / 'config/qq-bridge.local.json'
    if registration.exists():
        value = json.loads(registration.read_text(encoding='utf-8-sig'))
        if value.get('folder'):
            folders.append(Path(value['folder']))
    for process in psutil.process_iter(['exe']):
        try:
            executable = process.info.get('exe')
            if executable and Path(executable).name.lower() == 'node.exe':
                folders.append(Path(executable).parent)
        except (psutil.Error, OSError):
            continue
    return list(dict.fromkeys(folders))


def resolve_connection(local_root, account, saved):
    if not account.isascii() or not account.isdigit() or int(account) <= 0:
        raise ValueError('请输入有效的 QQ 号')
    if saved.get('account') == account and saved.get('http_url') and saved.get('ws_url'):
        return QqConfig.model_validate(saved)
    candidates = []
    for folder in bridge_folders(local_root):
        package_path = folder / 'package.json'
        config_path = folder / 'config' / f'onebot_{account}.json'
        if not package_path.exists() or not config_path.exists():
            continue
        try:
            package, _ = read_json(package_path)
            if package.get('name') != '@snowluma/runtime' or package.get('version') not in ('1.14.19', '1.14.20'):
                continue
            own, _ = read_json(config_path)
            sources = []
            global_path = folder / 'config/onebot.json'
            if own.get('mode') != 'snapshot' and global_path.exists():
                sources.append(read_json(global_path)[0])
            sources.append(own)
            nodes, private = adapters(sources)
            http = [node for node in nodes['http'] if node['usable']]
            ws = [node for node in nodes['ws'] if node['usable']]
            if len(http) != 1 or len(ws) != 1:
                continue
            connection = QqConfig(account=account,
                http_url=private[http[0]['id']]['url'], http_token=private[http[0]['id']]['token'],
                ws_url=private[ws[0]['id']]['url'], ws_token=private[ws[0]['id']]['token'])
            candidates.append((folder, connection))
        except (SetupError, ValueError, OSError):
            continue
    if len(candidates) != 1:
        raise ValueError('尚未找到此 QQ 号的唯一可用连接，请确认 QQ 桥接组件已运行并登录此账号')
    folder, connection = candidates[0]
    registration = local_root / 'config/qq-bridge.local.json'
    registration.parent.mkdir(parents=True, exist_ok=True)
    write_json(registration, {'folder': str(folder), 'provider': 'snowluma'})
    return connection
