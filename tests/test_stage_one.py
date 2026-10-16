import json
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from urllib.error import HTTPError

import psutil

from miko.adapters.dsh.runtime import DshRuntime
from miko.cli import recorded_process, request
from miko.settings import Settings, write_json


LOCAL_ROOT = Path(__file__).resolve().parents[2] / "MikoLocal"


class StageOneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = Settings.load(LOCAL_ROOT)
        cls.settings.prepare()
        if recorded_process(cls.settings) is not None:
            raise unittest.SkipTest("Stop Miko before running the stage-one integration tests")

    def cli(self, action, *, success=True):
        result = subprocess.run(
            [sys.executable, "-m", "miko.cli", action, "--local-root", str(LOCAL_ROOT)],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(result.returncode, 0 if success else 1, result.stdout + result.stderr)
        return result

    def tearDown(self):
        self.cli("stop")

    def test_isolated_runtime_and_profile(self):
        settings = self.settings
        runtime = DshRuntime(settings)
        try:
            runtime.start()
            self.assertTrue(runtime.ready)
            environment = runtime.process.environ()
            self.assertEqual(environment["DSH_HOME"], str(settings.dsh_home))
            self.assertEqual(environment["DSH_AGENTS_HOME"], str(settings.agents_home))
            self.assertEqual(environment["DEEPSEEK_BASE_URL"], "http://127.0.0.1:9")
            runtime_pid = runtime.process.pid
        finally:
            runtime.close()
        self.assertFalse(psutil.pid_exists(runtime_pid))
        self.assertFalse(runtime.ready)
        dump = subprocess.run(
            [str(runtime.executable), "--profile", "sdk-minimal", "--dump-config"],
            cwd=settings.workspace, env=environment, capture_output=True,
            text=True, encoding="utf-8", timeout=30, check=True,
        ).stdout
        self.assertNotIn("dsh-agent-instructions", dump)
        self.assertNotIn("dsh-skill-filesystem", dump)

    def test_start_status_stop_and_restart(self):
        for _ in range(2):
            self.cli("start")
            record, process = recorded_process(self.settings)
            descendants = process.children(recursive=True)
            self.assertTrue(request(self.settings.url + "/health")["ready"])
            self.assertTrue(request(self.settings.url + "/api/status")["dsh"]["ready"])
            self.assertTrue(json.loads(self.cli("start").stdout)["already_running"])
            self.assertEqual(recorded_process(self.settings)[1].pid, process.pid)
            with self.assertRaises(HTTPError) as refused:
                request(self.settings.url + "/_runtime/stop", "invalid")
            self.assertEqual(refused.exception.code, 403)
            self.assertTrue(request(self.settings.url + "/health")["ready"])
            self.cli("stop")
            self.assertFalse(self.settings.process_file.exists())
            _, alive = psutil.wait_procs(descendants, timeout=5)
            self.assertEqual(alive, [])
            self.assertFalse(process.is_running())

    def test_occupied_port_does_not_create_process_record(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", self.settings.port))
            listener.listen()
            result = self.cli("start", success=False)
            self.assertIn("failed", result.stderr)
            self.assertFalse(self.settings.process_file.exists())

    def test_unrelated_process_is_not_stopped(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        process = psutil.Process(child.pid)
        try:
            write_json(self.settings.process_file, {
                "pid": child.pid, "created": process.create_time(),
                "port": self.settings.port, "control_token": "test",
            })
            result = self.cli("stop", success=False)
            self.assertIn("another process", result.stderr)
            self.assertIsNone(child.poll())
        finally:
            self.settings.process_file.unlink(missing_ok=True)
            child.terminate()
            child.wait(timeout=5)
        write_json(self.settings.process_file, {
            "pid": child.pid, "created": process.create_time(),
            "port": self.settings.port, "control_token": "test",
        })
        self.cli("stop")
        self.assertFalse(self.settings.process_file.exists())

    def test_runtime_exit_is_visible_in_health(self):
        self.cli("start")
        _, backend = recorded_process(self.settings)
        executable = DshRuntime(self.settings).executable
        runtime = next(p for p in backend.children(recursive=True)
                       if Path(p.exe()).resolve() == executable)
        runtime.terminate()
        runtime.wait(timeout=5)
        self.assertFalse(request(self.settings.url + "/api/status")["dsh"]["ready"])
        with self.assertRaises(HTTPError) as unhealthy:
            request(self.settings.url + "/health")
        self.assertEqual(unhealthy.exception.code, 503)


if __name__ == "__main__":
    unittest.main()
