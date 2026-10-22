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
from miko.cli import request, stop
from miko.settings import Settings, write_json


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
                [sys.executable, '-u', '-m', 'miko.cli', 'run', '--local-root', str(self.root)],
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


if __name__ == '__main__':
    unittest.main()
