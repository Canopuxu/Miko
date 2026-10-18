"""Discover running QQ main processes without reading old account directories."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import psutil
import httpx

from load_qq_probe import load_payload
from miko.adapters.qq.loading import LoadingBusy
from miko.adapters.qq.managed import bridge_processes


def connected_client(row, config):
    """Read a running bridge before installing another native probe."""
    try:
        if not config or not config.get('port') or not config.get('token'):
            return None
        with httpx.Client(trust_env=False, timeout=2) as client:
            url = f"http://127.0.0.1:{config['port']}/rpc"
            headers = {'Authorization': 'Bearer ' + config['token']}
            response = client.post(url, headers=headers, json={'action': 'get_login_info'})
            response.raise_for_status()
            info = response.json()['result']
            if info.get('pid') != row['pid'] or str(info.get('user_id')) != str(config.get('account')):
                return None
            friends = client.post(url, headers=headers, json={'action': 'get_friend_list'})
            friends.raise_for_status()
            if isinstance(friends.json().get('result'), list):
                return {**row, 'account': str(info['user_id']), 'nickname': info.get('nickname', ''),
                        'version': info.get('version', ''), 'state': 'ready', 'note': '已登录，可连接',
                        'capabilities': info.get('capabilities', {})}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        pass
    return None


def other_connected_client(row):
    for process in bridge_processes():
        try:
            command = process.cmdline()
            path = Path(command[command.index('--config') + 1])
            config = json.loads(path.read_text('utf-8-sig'))
        except (psutil.Error, OSError, ValueError, IndexError):
            continue
        connected = connected_client(row, config)
        if connected:
            return connected
    return None


def main_processes(executable=None):
    expected = str(Path(executable).resolve()).casefold() if executable else None
    rows = []
    for process in psutil.process_iter(['pid', 'name', 'exe', 'cmdline', 'create_time']):
        try:
            info = process.info
            if ((info['name'] or '').casefold() != 'qq.exe' or not info['exe']
                    or any(arg.startswith('--type=') for arg in (info['cmdline'] or []))):
                continue
            if expected and str(Path(info['exe']).resolve()).casefold() != expected:
                continue
            rows.append({'pid': info['pid'], 'executable': info['exe'], 'started_at': info['create_time']})
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return sorted(rows, key=lambda row: row['pid'])


def inspect_client(row, local_root, frida_path=None):
    result = {**row, 'account': '', 'nickname': '', 'version': ''}
    try:
        process = psutil.Process(row['pid'])
        if process.create_time() != row['started_at']:
            raise psutil.NoSuchProcess(row['pid'])
        connected = other_connected_client(row)
        if connected:
            return connected
        source = (Path(__file__).resolve().parents[1] / 'apps/qq-bridge/discover.js').read_text('utf-8')
        folder = Path(local_root) / 'cache/qq-discovery'
        folder.mkdir(parents=True, exist_ok=True)
        value = load_payload(row['pid'], source, folder, frida_path)
        if not isinstance(value, dict) or value.get('pid') != row['pid']:
            raise ValueError('invalid client identity')
        return {**result, **value, 'started_at': row['started_at']}
    except psutil.NoSuchProcess:
        return {**result, 'state': 'closed', 'note': '此 QQ 客户端已退出，请重新检测'}
    except (PermissionError, psutil.AccessDenied):
        return {**result, 'state': 'permission', 'note': '无法访问此 QQ，请以相同的普通用户权限运行 QQ 和 Miko'}
    except ImportError:
        return {**result, 'state': 'component', 'note': 'QQ 连接组件未安装完整，请重新运行程序的环境准备步骤'}
    except LoadingBusy as error:
        return {**result, 'state': 'loading', 'stage': 'loader', 'note': str(error)}
    except TimeoutError:
        return {**result, 'state': 'loading', 'note': 'QQ 未响应检测，请打开 QQ 主界面并完成登录后重试'}
    except Exception as error:
        if 'MIKO_NAPI_UNAVAILABLE' in str(error):
            return {**result, 'state': 'unsupported', 'stage': 'loader',
                    'note': '此客户端未提供所需的 QQ NT 加载接口，当前 Miko 版本无法连接'}
        return {**result, 'state': 'error', 'stage': 'inspection',
                'note': '暂时无法读取 QQ 接口，请等待 QQ 启动完成或打开主界面后重新检测'}


def discover(local_root, frida_path=None, executable=None, active=None, pid=None):
    rows = [row for row in main_processes(executable) if pid is None or row['pid'] == pid]
    connected = next((value for row in rows if (value := connected_client(row, active))), None)
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(lambda row: connected if connected and row['pid'] == connected['pid']
                             else inspect_client(row, local_root, frida_path), rows))


def select_client(rows, account, preferred_pid=None):
    matches = [row for row in rows if row.get('state') == 'ready' and row.get('account') == str(account)]
    preferred = next((row for row in matches if row['pid'] == preferred_pid), None)
    if preferred:
        return preferred
    if len(matches) == 1:
        return matches[0]
    return None
