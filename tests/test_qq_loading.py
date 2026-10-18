import os
import subprocess
import sys
import unittest

from miko.adapters.qq.loading import loading_lock


class NativeLoadingTests(unittest.TestCase):
    def test_source_and_portable_processes_share_the_same_client_lock(self):
        identity = f'loading-regression-{os.getpid()}'
        code = '''from miko.adapters.qq.loading import loading_lock,LoadingBusy
import sys
try:
    with loading_lock(sys.argv[1],timeout_ms=100): print('acquired')
except LoadingBusy: print('busy')
'''
        def child():
            return subprocess.check_output([sys.executable,'-c',code,identity],text=True).strip()
        with loading_lock(identity):
            self.assertEqual(child(), 'busy')
        self.assertEqual(child(), 'acquired')
