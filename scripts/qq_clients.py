"""Discover running QQ main processes without reading old account directories."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import httpx

from load_qq_probe import load_payload


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
    except TimeoutError:
        return {**result, 'state': 'loading', 'note': 'QQ 未响应检测，请打开 QQ 主界面并完成登录后重试'}
    except Exception as error:
        if 'MIKO_NAPI_UNAVAILABLE' in str(error):
            return {**result, 'state': 'unsupported', 'stage': 'loader',
                    'note': '此客户端未提供所需的 QQ NT 加载接口，当前 Miko 版本无法连接'}
        return {**result, 'state': 'unsupported', 'note': '无法读取此 QQ 的登录和消息接口，当前适配仅支持 QQ NT；请检查客户端版本'}


def discover(local_root, frida_path=None, executable=None, active=None, pid=None):
    rows = [row for row in main_processes(executable) if pid is None or row['pid'] == pid]
    connected = None
    if active and active.get('port') and active.get('token'):
        try:
            with httpx.Client(trust_env=False, timeout=2) as client:
                url = f"http://127.0.0.1:{active['port']}/rpc"
                headers = {'Authorization': 'Bearer ' + active['token']}
                response = client.post(url, headers=headers, json={'action': 'get_login_info'})
                response.raise_for_status()
                info = response.json()['result']
                row = next((row for row in rows if row['pid'] == info.get('pid')), None)
                if row and str(info.get('user_id')) == str(active.get('account')):
                    friends = client.post(url, headers=headers, json={'action': 'get_friend_list'})
                    friends.raise_for_status()
                    if isinstance(friends.json().get('result'), list):
                        connected = {**row, 'account': str(info['user_id']), 'nickname': info.get('nickname', ''),
                                     'version': info.get('version', ''), 'state': 'ready', 'note': '已登录，可连接',
                                     'capabilities': info.get('capabilities', {})}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            pass
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
