import json
import mimetypes
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import psutil
from fastapi.testclient import TestClient

from miko.api.app import create_app
from miko.cli import request, start, stop
from miko.settings import REPO_ROOT, Settings, write_json


@unittest.skipUnless(os.name == 'nt', 'Windows source launcher')
class SourceStartupTests(unittest.TestCase):
    def setUp(self):
        import _winapi
        self.temporary = TemporaryDirectory(prefix='miko-source-start-')
        self.root = Path(self.temporary.name)
        (self.root / 'envs').mkdir()
        _winapi.CreateJunction(sys.prefix, str(self.root / 'envs/python'))
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.settings = Settings(self.root, probe.getsockname()[1])
        self.settings.prepare()
        (self.root / 'config').mkdir()
        write_json(self.root / 'config/miko.local.json', {'port': self.settings.port})
        write_json(self.root / 'config/qq.local.json', {'enabled': False})
        write_json(self.root / 'config/life.local.json', {'enabled': False})
        write_json(self.root / 'config/retrieval.local.json', {'expansion_enabled': False})

    def tearDown(self):
        stop(self.settings)
        self.temporary.cleanup()

    def test_windows_svg_mapping_is_corrected_in_actual_static_response(self):
        with TemporaryDirectory() as assets:
            repo = Path(assets)
            web = repo / 'apps/web/dist'
            web.mkdir(parents=True)
            image = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
            (web / 'logo.svg').write_bytes(image)
            original = mimetypes.guess_type('logo.svg')[0]
            try:
                mimetypes.add_type('image/svg', '.svg')
                with patch('miko.api.app.REPO_ROOT', repo):
                    app = create_app(self.settings, 'test-token', lambda: None)
                client = TestClient(app)
                try:
                    response = client.get('/logo.svg')
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers['content-type'], 'image/svg+xml')
                    self.assertEqual(response.content, image)
                finally:
                    client.close()
            finally:
                mimetypes.add_type(original or 'image/svg+xml', '.svg')

    def test_foreground_interrupt_and_background_start_both_release_their_backend(self):
        with (self.root / 'foreground.log').open('wb') as log:
            launcher = subprocess.Popen(
                [sys.executable, '-u', '-m', 'miko.cli', 'run', '--no-browser', '--local-root', str(self.root)],
                stdout=log, stderr=log, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            try:
                deadline = time.monotonic() + 50
                while time.monotonic() < deadline:
                    if launcher.poll() is not None:
                        self.fail((self.root / 'foreground.log').read_text('utf-8'))
                    try:
                        if request(self.settings.url + '/health').get('ready'):
                            break
                    except (OSError, ValueError):
                        pass
                    time.sleep(.2)
                else:
                    self.fail('Foreground backend did not become ready')
                record = json.loads(self.settings.process_file.read_text('utf-8'))
                backend = psutil.Process(record['pid'])
                children = backend.children(recursive=True)
                launcher.send_signal(signal.CTRL_BREAK_EVENT)
                self.assertEqual(launcher.wait(timeout=30), 0)
                self.assertFalse(self.settings.process_file.exists())
                self.assertFalse(psutil.pid_exists(record['pid']))
                self.assertEqual(psutil.wait_procs(children, timeout=5)[1], [])
                self.assertIn('Application shutdown complete',
                              (self.root / 'logs/backend.log').read_text('utf-8'))
            finally:
                if launcher.poll() is None:
                    stop(self.settings)
                    launcher.wait(timeout=15)
        result = subprocess.run(
            [sys.executable, '-m', 'miko.cli', 'start', '--local-root', str(self.root)],
            capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(request(self.settings.url + '/health')['ready'])
        stop(self.settings)
        self.assertFalse(self.settings.process_file.exists())

    def test_browser_opens_only_when_ready_and_reuses_running_service(self):
        opened = []
        def browser(url):
            self.assertTrue(request(url + '/health')['ready'])
            opened.append(url)
            return True
        with patch('miko.cli.webbrowser.open', side_effect=browser):
            start(self.settings, open_browser=True)
            first = json.loads(self.settings.process_file.read_text('utf-8'))['pid']
            start(self.settings, open_browser=True)
            self.assertEqual(json.loads(self.settings.process_file.read_text('utf-8'))['pid'], first)
        self.assertEqual(opened, [self.settings.url, self.settings.url])

    def test_abrupt_launcher_exit_reaps_backend_and_hidden_children(self):
        workbench = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(90)', str(REPO_ROOT / 'scripts/workbench.mjs')],
            creationflags=subprocess.CREATE_NO_WINDOW)
        with (self.root / 'abrupt.log').open('wb') as log:
            launcher = subprocess.Popen(
                [sys.executable, '-u', '-m', 'miko.cli', 'run', '--no-browser', '--local-root', str(self.root),
                 '--workbench-pid', str(workbench.pid)],
                stdout=log, stderr=log, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            try:
                deadline = time.monotonic() + 50
                while time.monotonic() < deadline:
                    if launcher.poll() is not None:
                        self.fail((self.root / 'abrupt.log').read_text('utf-8'))
                    try:
                        if request(self.settings.url + '/health').get('ready'):
                            break
                    except (OSError, ValueError):
                        pass
                    time.sleep(.2)
                else:
                    self.fail('Foreground backend did not become ready')
                backend = psutil.Process(json.loads(self.settings.process_file.read_text('utf-8'))['pid'])
                children = backend.children(recursive=True)
                self.assertTrue(children)
                workbench_tree = [psutil.Process(workbench.pid),
                                  *psutil.Process(workbench.pid).children(recursive=True)]
                # A venv python.exe may be a redirector; target the actual CLI owner.
                candidates = [psutil.Process(launcher.pid), *psutil.Process(launcher.pid).children(recursive=True)]
                owner = next(p for p in reversed(candidates) if 'miko.cli' in p.cmdline())
                owner.kill()
                launcher.wait(timeout=10)
                self.assertEqual(psutil.wait_procs([backend, *children, *workbench_tree], timeout=10)[1], [])
            finally:
                if launcher.poll() is None:
                    stop(self.settings)
                    launcher.wait(timeout=15)
                if workbench.poll() is None:
                    for process in reversed(psutil.Process(workbench.pid).children(recursive=True)):
                        process.kill()
                    workbench.kill()
                workbench.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
