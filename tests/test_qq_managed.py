from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch
import json
import sys
from types import SimpleNamespace

import httpx

from miko.adapters.qq.managed import resolve_connection, ManagedQq
from miko.settings import write_json


class ManagedQqTests(unittest.TestCase):
    def test_native_connection_replaces_saved_provider_and_keeps_preferences(self):
        with TemporaryDirectory() as folder:
            local = Path(folder)
            (local/'config').mkdir()
            write_json(local/'config/qq-client.local.json', {
                'account': '12345', 'port': 17971, 'token': 'private-native'})
            saved = {'account': '12345', 'http_url': 'http://127.0.0.1:3000',
                     'ws_url': 'ws://127.0.0.1:3001', 'http_token': 'old',
                     'friends': [{'id': '67890'}],
                     'miko_avatar_file': 'miko.png', 'owner_avatar_file': 'owner.png'}
            result = resolve_connection(local, '12345', saved)
            self.assertEqual(result.http_url, 'http://127.0.0.1:17971')
            self.assertEqual(result.ws_url, 'ws://127.0.0.1:17971/')
            self.assertEqual(result.http_token.get_secret_value(), 'private-native')
            self.assertEqual(result.ws_token.get_secret_value(), 'private-native')
            self.assertEqual(result.friends, saved['friends'])
            self.assertEqual(result.miko_avatar_file, 'miko.png')
            self.assertNotIn('private-native', repr(result))
            self.assertFalse((local/'config/qq-bridge.local.json').exists())

    def test_missing_or_wrong_account_never_falls_back(self):
        with TemporaryDirectory() as folder:
            local = Path(folder)
            (local/'config').mkdir()
            saved = {'account': '12345', 'http_url': 'http://127.0.0.1:3000',
                     'ws_url': 'ws://127.0.0.1:3001'}
            with self.assertRaisesRegex(ValueError, '尚未配置'):
                resolve_connection(local, '12345', saved)
            write_json(local/'config/qq-client.local.json', {'account': '99999', 'token': 'secret'})
            with self.assertRaisesRegex(ValueError, '不一致'):
                resolve_connection(local, '12345', saved)


class ManagedSetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        process=patch('miko.adapters.qq.managed.psutil.Process',return_value=SimpleNamespace(create_time=lambda:123.5))
        process.start();self.addCleanup(process.stop)

    def client_row(self, **changes):
        return {'pid': 42, 'started_at': 123.5, 'executable': 'D:/Different QQ/QQ.exe',
                'account': '12345', 'nickname': '测试账号', 'version': 'fixture', 'state': 'ready', **changes}

    async def test_first_connect_generates_credentials_and_keeps_preferences(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            managed = ManagedQq(root)
            calls = []
            def respond(request):
                calls.append(request)
                return httpx.Response(200, json={'result': {'user_id': 12345, 'pid':42}})
            original = httpx.AsyncClient
            with patch.object(managed, 'discover', new=AsyncMock(return_value=[self.client_row()])), \
                    patch('miko.adapters.qq.managed.start_bridge') as start, \
                    patch('miko.adapters.qq.managed.httpx.AsyncClient', side_effect=lambda **kw: original(**kw, transport=httpx.MockTransport(respond))):
                saved = {'account': '12345', 'friends': [{'id': '67890'}],
                         'miko_avatar_file': 'miko.png', 'owner_avatar_file': 'owner.png'}
                connection = await managed.connect(42, '12345', saved)
                first = json.loads((root / 'config/qq-client.local.json').read_text('utf-8'))
                await managed.connect(42, '12345', saved)
                second = json.loads((root / 'config/qq-client.local.json').read_text('utf-8'))
            self.assertGreaterEqual(len(first['token']), 32)
            self.assertEqual(first['token'], second['token'])
            self.assertEqual(first['qq_exe'], 'D:/Different QQ/QQ.exe')
            self.assertNotIn('bridge_port', first)
            self.assertEqual(connection.friends, saved['friends'])
            self.assertEqual(connection.owner_avatar_file, 'owner.png')
            self.assertEqual(calls[0].headers['Authorization'], 'Bearer ' + first['token'])
            self.assertNotIn(first['token'], repr(connection))
            self.assertEqual(start.call_count, 2)

    async def test_account_changed_or_client_closed_does_not_write_or_launch(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            managed = ManagedQq(root)
            for rows in ([], [self.client_row(account='99999')], [self.client_row(state='unsupported', note='客户端不兼容')]):
                with patch.object(managed, 'discover', new=AsyncMock(return_value=rows)), \
                        patch('miko.adapters.qq.managed.start_bridge') as start:
                    with self.assertRaises(ValueError):
                        await managed.connect(42, '12345', {})
                    start.assert_not_called()
                    self.assertFalse((root / 'config/qq-client.local.json').exists())

    async def test_start_failure_restores_existing_configuration(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'config').mkdir()
            original = {'account': '12345', 'token': 'preserved-secret', 'port': 17971, 'qq_exe': 'old/QQ.exe'}
            write_json(root / 'config/qq-client.local.json', original)
            managed = ManagedQq(root)
            with patch.object(managed, 'discover', new=AsyncMock(return_value=[self.client_row()])), \
                    patch('miko.adapters.qq.managed.start_bridge', side_effect=OSError('fixture failure')):
                with self.assertRaises(OSError):
                    await managed.connect(42, '12345', {})
            self.assertEqual(json.loads((root / 'config/qq-client.local.json').read_text('utf-8')), original)


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import qq_clients


class ClientDiscoveryTests(unittest.TestCase):
    def test_running_main_processes_allow_multiple_paths_and_ignore_renderers(self):
        rows = [SimpleNamespace(info={'pid': pid, 'name': name, 'exe': exe,
                'cmdline': command, 'create_time': 10.0}) for pid, name, exe, command in [
            (1, 'QQ.exe', 'D:/QQ/QQ.exe', ['QQ.exe']),
            (2, 'QQ.exe', 'E:/Other/QQ.exe', ['QQ.exe']),
            (3, 'QQ.exe', 'D:/QQ/QQ.exe', ['QQ.exe', '--type=renderer']),
            (4, 'not-qq.exe', 'D:/old/QQ.exe', ['not-qq.exe'])]]
        with patch.object(qq_clients.psutil, 'process_iter', return_value=rows):
            self.assertEqual([row['pid'] for row in qq_clients.main_processes()], [1, 2])

    def test_reconnection_matches_account_even_after_process_restart(self):
        rows = [{'pid': 8, 'state': 'ready', 'account': '99999'},
                {'pid': 9, 'state': 'ready', 'account': '12345'}]
        self.assertEqual(qq_clients.select_client(rows, '12345', preferred_pid=8)['pid'], 9)
        self.assertIsNone(qq_clients.select_client(rows, '44444', preferred_pid=8))
        rows.append({'pid': 10, 'state': 'ready', 'account': '12345'})
        self.assertIsNone(qq_clients.select_client(rows, '12345'))
        self.assertEqual(qq_clients.select_client(rows, '12345', preferred_pid=10)['pid'], 10)

    def test_incompatible_and_logged_out_clients_are_not_selectable(self):
        self.assertIsNone(qq_clients.select_client([
            {'pid': 1, 'state': 'unsupported', 'account': '12345'},
            {'pid': 2, 'state': 'login', 'account': ''}], '12345'))

    def test_failed_inspection_does_not_claim_version_is_unsupported(self):
        row = {'pid': 42, 'started_at': 10.0, 'executable': 'D:/QQ/QQ.exe'}
        process = SimpleNamespace(create_time=lambda: 10.0)
        with TemporaryDirectory() as folder, patch.object(qq_clients.psutil, 'Process', return_value=process), \
                patch.object(qq_clients, 'load_payload') as load:
            load.side_effect = RuntimeError('QQ is still starting')
            result = qq_clients.inspect_client(row, folder)
            self.assertEqual(result['state'], 'error')
            self.assertIn('重新检测', result['note'])
            load.side_effect = RuntimeError('MIKO_NAPI_UNAVAILABLE')
            self.assertEqual(qq_clients.inspect_client(row, folder)['state'], 'unsupported')


class RunnerReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_restart_reconnects_selected_account_with_new_pid(self):
        import asyncio
        import run_qq_client_bridge as runner
        with TemporaryDirectory() as folder:
            root=Path(folder);(root/'config').mkdir()
            config={'account':'12345','token':'fixture','port':45001,'bridge_port':45002,'preferred_pid':42}
            write_json(root/'config/qq-client.local.json',config)
            state={'live':None,'pid':42};loaded=[];announced=[]
            ready=asyncio.Event()
            async def identity(_):
                if state['live'] and state['live']['pid'] not in announced:
                    announced.append(state['live']['pid']);ready.set()
                return state['live']
            def load(pid,*args):
                loaded.append(pid);state['live']={'user_id':12345,'pid':pid,'version':'fixture'}
            def inspect(row,*args):
                return {**row,'state':'ready','account':'99999' if row['pid']==7 else '12345'}
            args=SimpleNamespace(config=root/'config/qq-client.local.json',database=root/'messages.sqlite3',
                                 qq_exe=None,frida_path=None,port=17972)
            with patch.object(runner,'identity',new=identity),patch.object(runner,'load_payload',side_effect=load), \
                    patch.object(runner,'main_processes',side_effect=lambda:[{'pid':7},{'pid':state['pid']}]), \
                    patch.object(runner,'inspect_client',side_effect=inspect):
                task=asyncio.create_task(runner.run(args))
                try:
                    await asyncio.wait_for(ready.wait(),5)
                    ready.clear();state.update(live=None,pid=43)
                    await asyncio.wait_for(ready.wait(),6)
                finally:
                    task.cancel();await asyncio.gather(task,return_exceptions=True)
            self.assertEqual(loaded,[42,43])


if __name__ == '__main__':
    unittest.main()
