"""Resolve the bundled QQ bridge connection for the settings page."""
import json
import hashlib
import asyncio
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys

import httpx
import psutil

from miko.adapters.qq.onebot import QqConfig
from miko.settings import REPO_ROOT, write_json
from .loading import LoadingBusy, loading_lock


def bridge_processes(local_root=None):
    target = (str((local_root / 'config/qq-client.local.json').resolve()).replace('/', '\\').casefold()
              if local_root is not None else None)
    result = []
    for process in psutil.process_iter(['cmdline']):
        try:
            command = process.info['cmdline'] or []
            if (any(Path(arg).name == 'run_qq_client_bridge.py' for arg in command)
                    and (target is None or any(arg.replace('/', '\\').casefold() == target for arg in command))):
                result.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result


def start_bridge(local_root):
    """Share the same runner between application startup and settings connect."""
    with loading_lock('runner-' + str(local_root.resolve()).casefold(), timeout_ms=5000):
        _start_bridge(local_root)


def _start_bridge(local_root):
    existing = bridge_processes(local_root)
    if existing:
        path = local_root / 'runtime/qq-bridge-status.json'
        status = json.loads(path.read_text('utf-8')) if path.exists() else {}
        # A just-launched runner may not have published its status yet. An old
        # status file must not make the next caller kill that new runner.
        if (status.get('runner_pid') not in {process.pid for process in existing}
                or status.get('runner_hash') == runner_signature()):
            return
        for process in existing:
            try:
                process.terminate()
                process.wait(5)
            except psutil.NoSuchProcess:
                pass
    logs = local_root / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / 'qq-bridge-out.log').open('ab') as output, (logs / 'qq-bridge-error.log').open('ab') as errors:
        subprocess.Popen([sys.executable, '-X', 'utf8', str(REPO_ROOT / 'scripts/run_qq_client_bridge.py'),
            '--config', str(local_root / 'config/qq-client.local.json')],
            stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


def adapter_signature():
    files = ('apps/qq-bridge/client.js', 'apps/qq-bridge/compat.js')
    return hashlib.sha256(b'\0'.join((REPO_ROOT / name).read_bytes() for name in files)).hexdigest()


def runner_signature():
    files = ('apps/qq-bridge/client.js', 'apps/qq-bridge/compat.js', 'apps/qq-bridge/loader.js',
             'scripts/load_qq_probe.py', 'scripts/run_qq_client_bridge.py', 'scripts/qq_clients.py',
             'apps/backend/src/miko/adapters/qq/managed.py', 'apps/backend/src/miko/adapters/qq/loading.py')
    return hashlib.sha256(b'\0'.join((REPO_ROOT / name).read_bytes() for name in files)).hexdigest()


def free_port():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        return server.getsockname()[1]


class ManagedQq:
    def __init__(self, local_root):
        self.root = Path(local_root)
        self.lock = asyncio.Lock()
        self.clients = {}

    async def discover(self, pid=None):
        command = [sys.executable, '-X', 'utf8', str(REPO_ROOT / 'scripts/discover_qq.py'),
                   '--local-root', str(self.root)]
        if pid is not None:
            command.extend(['--pid', str(pid)])
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 50)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise ValueError('检测 QQ 超时，请打开 QQ 主界面并完成登录后重试') from None
        try:
            if process.returncode:
                raise ValueError()
            rows = json.loads(output)['clients']
            self.clients.update({row['pid']: row for row in rows})
            return rows
        except (ValueError, KeyError, TypeError):
            raise ValueError('QQ 检测未完成，请检查连接组件是否安装完整后重试') from None

    def status(self):
        path = self.root / 'runtime/qq-bridge-status.json'
        try:
            value = json.loads(path.read_text('utf-8'))
            process = psutil.Process(value['runner_pid'])
            if process.create_time() != value['runner_started_at']:
                return None
            return {key: value.get(key) for key in ('state', 'note', 'account', 'version')}
        except (OSError, ValueError, KeyError, psutil.Error):
            return None

    async def connect(self, pid, account, saved):
        rows = [self.clients[pid]] if pid in self.clients else await self.discover(pid)
        row = next((row for row in rows if row['pid'] == pid), None)
        if not row:
            raise ValueError('所选 QQ 已退出，请重新检测并选择账号')
        if row.get('state') != 'ready':
            raise ValueError(row.get('note') or 'QQ 尚未就绪，请重新检测')
        if row['account'] != account:
            raise ValueError('QQ 登录账号已改变，请重新检测并选择账号')
        try:
            process = psutil.Process(pid)
            if process.create_time() != row['started_at']:
                raise psutil.NoSuchProcess(pid)
        except psutil.NoSuchProcess:
            raise ValueError('所选 QQ 已退出，请重新检测并选择账号') from None
        path = self.root / 'config/qq-client.local.json'
        previous = json.loads(path.read_text('utf-8-sig')) if path.exists() else None
        config = dict(previous or {})
        config.update(account=account, qq_exe=row['executable'], preferred_pid=pid,
                      preferred_started_at=row['started_at'])
        if not config.get('token'):
            config['token'] = secrets.token_urlsafe(32)
        if not config.get('port'):
            config['port'] = free_port()
        config.pop('bridge_port', None)
        # Never carry a test-recipient restriction to another user's account.
        if previous and str(previous.get('account')) != account:
            config.pop('test_targets', None)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, config)
        try:
            try:
                await asyncio.to_thread(start_bridge, self.root)
            except LoadingBusy as error:
                raise ValueError(str(error)) from None
            deadline = asyncio.get_running_loop().time() + 30
            async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
                while asyncio.get_running_loop().time() < deadline:
                    try:
                        response = await client.post(f"http://127.0.0.1:{config['port']}/rpc",
                            headers={'Authorization': 'Bearer ' + config['token']}, json={'action': 'get_login_info'})
                        data = response.json()
                        if (response.is_success and str(data.get('result', {}).get('user_id')) == account
                                and data.get('result', {}).get('pid', pid) == pid):
                            return resolve_connection(self.root, account, saved)
                    except (httpx.HTTPError, ValueError, AttributeError):
                        pass
                    status = self.status()
                    if status and status['account'] == account and status['state'] in ('unsupported', 'permission'):
                        raise ValueError(status['note'])
                    await asyncio.sleep(.4)
            raise ValueError('QQ 连接尚未完成，请检查 QQ 是否已登录并打开主界面，然后重试')
        except Exception:
            if previous is not None:
                write_json(path, previous)
            else:
                await self.disconnect()
                path.unlink(missing_ok=True)
            raise

    async def disconnect(self):
        path = self.root / 'config/qq-client.local.json'
        if path.exists():
            config = json.loads(path.read_text('utf-8-sig'))
            try:
                async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
                    await client.post(f"http://127.0.0.1:{config['port']}/rpc",
                        headers={'Authorization': 'Bearer ' + config['token']}, json={'action': 'disconnect'})
            except (httpx.HTTPError, KeyError):
                pass
        for process in bridge_processes(self.root):
            try:
                process.terminate()
                await asyncio.to_thread(process.wait, 5)
            except psutil.NoSuchProcess:
                pass


def resolve_connection(local_root, account, saved):
    if not account.isascii() or not account.isdigit() or int(account) <= 0:
        raise ValueError('请输入有效的 QQ 号')
    path = local_root / 'config/qq-client.local.json'
    try:
        bridge = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        raise ValueError('尚未配置自建 QQ 桥接，请先完成桥接组件安装') from None
    except (OSError, ValueError):
        raise ValueError('无法读取自建 QQ 桥接配置') from None
    if not isinstance(bridge, dict) or str(bridge.get('account')) != account:
        raise ValueError('填写的 QQ 号与自建桥接账号不一致')
    token = bridge.get('token')
    if not isinstance(token, str) or not token.strip():
        raise ValueError('自建 QQ 桥接缺少连接凭据，请修复桥接配置')
    # Preserve preferences, never the previous provider endpoints.
    retained = {key: saved[key] for key in ('friends', 'miko_avatar_file', 'owner_avatar_file', 'avatar_switch_enabled')
                if saved.get('account') == account and key in saved}
    port = bridge.get('port', 17971)
    return QqConfig(account=account, http_url=f'http://127.0.0.1:{port}',
                    ws_url=f'ws://127.0.0.1:{port}/', http_token=token, ws_token=token,
                    **retained)
