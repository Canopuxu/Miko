"""Load Miko's client adapter and reconnect only the selected QQ account."""
import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
import time

import httpx
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/backend/src'))
from miko.settings import write_json
from miko.adapters.qq.managed import adapter_signature, runner_signature
from miko.adapters.qq.loading import LoadingBusy
from load_qq_probe import load_payload
from qq_clients import main_processes, inspect_client, select_client


async def identity(config):
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
            response = await client.post(f"http://127.0.0.1:{config['port']}/rpc",
                headers={'Authorization': 'Bearer ' + config['token']}, json={'action': 'get_login_info'})
            response.raise_for_status()
            result = response.json()['result']
            return result if str(result['user_id']) == str(config['account']) else None
    except (httpx.HTTPError, ValueError, KeyError):
        return None


async def disconnect(config):
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
            await client.post(f"http://127.0.0.1:{config['port']}/rpc",
                headers={'Authorization': 'Bearer ' + config['token']}, json={'action': 'disconnect'})
    except httpx.HTTPError:
        pass


def read_config(path):
    return json.loads(path.read_text('utf-8-sig')) if path.exists() else None


async def run(args):
    local_root = args.config.resolve().parent.parent
    source = (Path(__file__).resolve().parents[1] / 'apps/qq-bridge/client.js').read_text('utf-8')
    runner = psutil.Process()
    signature = adapter_signature()
    last_state = None
    inspected = {}

    def inspect(row, config):
        key = (row['pid'], row.get('started_at'))
        previous = inspected.get(key)
        if previous and previous[0] > time.monotonic():
            return previous[1]
        value = inspect_client(row, local_root, config.get('frida_path') or args.frida_path)
        # An unrelated logged-in account is not a reason to load/unload Frida
        # every three seconds. Manual detection remains an immediate fresh read.
        if value.get('state') == 'ready' and value.get('account') != str(config['account']):
            inspected[key] = (time.monotonic() + 30, value)
        return value

    def status(state, note, config=None, version=''):
        nonlocal last_state
        value = {'state': state, 'note': note, 'account': str((config or {}).get('account', '')),
                 'version': version, 'adapter_hash': signature, 'runner_hash': runner_signature(),
                 'runner_pid': runner.pid, 'runner_started_at': runner.create_time()}
        if value != last_state:
            path = local_root / 'runtime/qq-bridge-status.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json(path, value)
            logging.getLogger('qq-bridge').warning(note)
            last_state = value

    while True:
        config = read_config(args.config)
        if not config:
            status('unconfigured', '请在设置中选择已登录的 QQ 账号')
            await asyncio.sleep(3)
            continue
        logged_in = await identity(config)
        if logged_in and logged_in.get('adapter_hash') != signature:
            logged_in = None
        selected_pid = config.get('preferred_pid')
        if logged_in and selected_pid and logged_in.get('pid') not in (None, selected_pid):
            # If the originally selected process has exited, reconnect by QQ
            # account; a restarted process is allowed to have a new PID.
            if any(row['pid'] == selected_pid for row in main_processes()):
                await disconnect(config)
                logged_in = None
        if not logged_in:
            status('detecting', '正在查找所选 QQ 账号', config)
            candidates = main_processes()
            if not candidates:
                status('waiting', 'QQ 尚未运行，请启动 QQ 并登录所选账号', config)
                await asyncio.sleep(3)
                continue
            preferred = next((row for row in candidates if row['pid'] == selected_pid
                              and config.get('preferred_started_at') is not None
                              and row.get('started_at') == config['preferred_started_at']), None)
            rows = ([{**preferred, 'account': str(config['account']), 'state': 'ready'}] if preferred else
                    await asyncio.gather(*(asyncio.to_thread(inspect, row, config) for row in candidates)))
            if read_config(args.config) != config:
                continue
            row = select_client(rows, config['account'], selected_pid)
            if not row:
                fault = next((row for row in rows if row['state'] in ('unsupported', 'permission', 'component', 'error')), None)
                pending = next((row for row in rows if row['state'] == 'loading'), None)
                current = fault or pending
                status(current['state'] if current else 'waiting', current['note'] if current else
                       f"请在 QQ 登录账号 {config['account']}，其他已登录账号不会自动接入", config)
                await asyncio.sleep(3)
                continue
            try:
                payload = source.replace('__MIKO_CONFIG__', json.dumps({**config, 'adapter_hash': signature}))
                await asyncio.to_thread(load_payload, row['pid'], payload, args.config.parent,
                                        config.get('frida_path') or args.frida_path)
                logged_in = await identity(config)
                if not logged_in:
                    raise ValueError('QQ account changed during connection')
            except LoadingBusy as error:
                status('loading', str(error), config)
                await asyncio.sleep(3)
                continue
            except Exception:
                logging.getLogger('qq-bridge').exception('QQ adapter loading failed for PID %s', row['pid'])
                status('error', 'QQ 连接未完成，请打开主界面后重试；若刚更新 QQ，请重新检测兼容性', config)
                await asyncio.sleep(3)
                continue
        status('ready', 'QQ 客户端已连接', config, logged_in.get('version', ''))
        while True:
            await asyncio.sleep(1)
            if read_config(args.config) != config:
                await disconnect(config)
                await asyncio.sleep(.15)
                break
            if not await identity(config):
                break


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--frida-path', type=Path)
    asyncio.run(run(parser.parse_args()))
