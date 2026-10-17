"""Portable desktop entry point; the backend stays an ordinary Python process."""
import asyncio
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import winreg

from miko.cli import recorded_process, request, start, stop
from miko.settings import REPO_ROOT, Settings, write_json
from miko.storage.backups import create_backup


def message(text, title='Miko', *, question=False):
    return ctypes.windll.user32.MessageBoxW(None, text, title, 0x24 if question else 0x10) == 6


class SingleWindow:
    """A kernel-owned lock and wake event, scoped to this portable folder."""
    def __init__(self, root):
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CreateEventW.restype = wintypes.HANDLE
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        for name in ('CloseHandle', 'SetEvent'):
            getattr(self.kernel, name).argtypes = [wintypes.HANDLE]
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        key = hashlib.sha256(str(root.resolve()).casefold().encode()).hexdigest()[:24]
        self.mutex = self.kernel.CreateMutexW(None, False, 'Local\\Miko-' + key)
        if not self.mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        self.primary = ctypes.get_last_error() != 183
        self.event = self.kernel.CreateEventW(None, False, False, 'Local\\Miko-wake-' + key)
        if not self.event:
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.primary:
            self.kernel.SetEvent(self.event)

    def close(self):
        self.kernel.CloseHandle(self.event)
        self.kernel.CloseHandle(self.mutex)


class ChildJob:
    """Windows closes the backend's process tree if the desktop crashes."""
    def __init__(self):
        class BASIC(ctypes.Structure):
            _fields_ = [('ProcessTime', ctypes.c_int64), ('JobTime', ctypes.c_int64),
                ('Flags', wintypes.DWORD), ('MinWorking', ctypes.c_size_t), ('MaxWorking', ctypes.c_size_t),
                ('ActiveLimit', wintypes.DWORD), ('Affinity', ctypes.c_size_t),
                ('Priority', wintypes.DWORD), ('Scheduling', wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ('ReadOps','WriteOps','OtherOps','ReadBytes','WriteBytes','OtherBytes')]
        class EXTENDED(ctypes.Structure):
            _fields_ = [('Basic', BASIC), ('IO', IO), ('ProcessMemory', ctypes.c_size_t),
                ('JobMemory', ctypes.c_size_t), ('PeakProcessMemory', ctypes.c_size_t), ('PeakJobMemory', ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        info = EXTENDED()
        info.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())

    def attach(self, process):
        handle = self.kernel.OpenProcess(0x101 | 0x1000, False, process.pid)
        try:
            member = wintypes.BOOL()
            if not handle or not self.kernel.IsProcessInJob(handle, self.handle, ctypes.byref(member)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not member.value and not self.kernel.AssignProcessToJobObject(self.handle, handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            if handle:
                self.kernel.CloseHandle(handle)

    def close(self):
        self.kernel.CloseHandle(self.handle)


def webview_installed():
    path = r'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for access in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | access) as key:
                    if winreg.QueryValueEx(key, 'pv')[0] not in ('', '0.0.0.0'):
                        return True
            except OSError:
                pass
    return False


def ensure_webview(root):
    if webview_installed():
        return
    if not message('首次打开需要补齐 Microsoft WebView2。现在下载并安装吗？', question=True):
        raise RuntimeError('尚未安装 WebView2。再次打开 Miko 时可以继续安装。')
    target = root / 'cache/tmp/MicrosoftEdgeWebview2Setup.exe'
    urllib.request.urlretrieve('https://go.microsoft.com/fwlink/p/?LinkId=2124703', target)
    # Trust the signed Microsoft bootstrapper before executing downloaded code.
    literal = str(target).replace("'", "''")
    script = "$s=Get-AuthenticodeSignature -LiteralPath '" + literal + "'; if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {exit 1}"
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    check = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
        creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True)
    if check.returncode:
        raise RuntimeError('WebView2 安装程序校验未通过，请稍后重试。')
    result = subprocess.run([str(target), '/silent', '/install'], creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode or not webview_installed():
        raise RuntimeError('WebView2 尚未安装成功，请检查网络后重新打开 Miko。')
    target.unlink(missing_ok=True)


def prepare_data(root, build):
    (root / 'config').mkdir(parents=True, exist_ok=True)
    (root / 'cache/tmp').mkdir(parents=True, exist_ok=True)
    settings_file = root / 'config/miko.local.json'
    if not settings_file.exists():
        write_json(settings_file, {'port': 7870})
    memory_file = root / 'config/memory.local.json'
    if not memory_file.exists():
        write_json(memory_file, {'engine': 'memobase'})
    settings = Settings.load(root)
    settings.prepare()
    marker = root / 'runtime/desktop-build.json'
    previous = json.loads(marker.read_text('utf-8')) if marker.exists() else None
    if (root / 'data/database/miko.sqlite3').exists() and (not previous or previous.get('build') != build['build']):
        create_backup(root, reason='before-upgrade', name='升级前备份-' + time.strftime('%Y%m%d-%H%M%S'))
    if previous and previous.get('local_root') and previous['local_root'] != str(root):
        from miko.storage.backups import relocate_settings
        relocate_settings(root, previous['local_root'])
    # Bind to an available local port without asking users to resolve a conflict.
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', settings.port))
        except OSError:
            probe.bind(('127.0.0.1', 0))
            write_json(settings_file, {'port': probe.getsockname()[1]})
    return Settings.load(root)


def main():
    root = REPO_ROOT.parent / 'MikoLocal'
    instance = SingleWindow(root)
    if not instance.primary:
        instance.close()
        return
    job = None
    settings = None
    window = None
    cleanup_lock = threading.Lock()
    cleaned = False

    def cleanup():
        nonlocal cleaned
        with cleanup_lock:
            if cleaned or settings is None:
                return
            try:
                stop(settings)
            finally:
                from miko.adapters.qq.managed import ManagedQq
                asyncio.run(ManagedQq(root).disconnect())
            cleaned = True

    try:
        (root / 'logs').mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = (root / 'logs/desktop.log').open('a', encoding='utf-8', buffering=1)
        logging.basicConfig(stream=sys.stderr, level=logging.INFO)
        build = json.loads((REPO_ROOT / 'portable.json').read_text('utf-8'))
        # A previous crash can leave the recorded server briefly shutting down.
        initial = root / 'config/miko.local.json'
        if initial.exists():
            old = Settings.load(root)
            if recorded_process(old):
                stop(old)
        settings = prepare_data(root, build)
        os.environ['TEMP'] = os.environ['TMP'] = str(root / 'cache/tmp')
        ensure_webview(root)
        import webview
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
        webview.settings['ALLOW_DOWNLOADS'] = True
        window = webview.create_window('Miko', html='''<!doctype html><html><meta charset="utf-8"><style>
            body{margin:0;background:#f8f7f3;color:#6c8066;font:16px "Microsoft YaHei",sans-serif;display:grid;place-items:center;height:100vh}
            div{text-align:center}i{display:block;width:26px;height:26px;margin:0 auto 18px;border:2px solid #dfe4d9;border-top-color:#6c8066;border-radius:50%;animation:spin 1s linear infinite}
            @keyframes spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){i{animation:none}}</style><div><i></i>正在打开 Miko</div></html>''',
            width=1180, height=820, min_size=(900, 600), background_color='#f8f7f3')
        closing = threading.Event()
        stopped = threading.Event()
        boot_done = threading.Event()

        def finish():
            if stopped.is_set():
                return True
            if closing.is_set():
                return False
            closing.set()

            def shutdown():
                boot_done.wait()
                try:
                    restore_file = root / 'runtime/restore-status.json'
                    restore = json.loads(restore_file.read_text('utf-8')) if restore_file.exists() else {}
                    if restore.get('state') in ('starting', 'restoring'):
                        message('正在恢复数据，请完成后再退出。')
                        closing.clear()
                        return
                    state = request(settings.url + '/api/status')
                    qq = request(settings.url + '/api/qq/status')
                    busy = state['chat']['active_turns'] or state['life']['generating'] or any(
                        f['state'] in ('generating', 'sending') for f in qq['friends'])
                    if busy and not message('有回复或生成任务正在进行，仍要退出 Miko 吗？', question=True):
                        closing.clear()
                        return
                except Exception:
                    logging.exception('Desktop closing status check')
                try:
                    cleanup()
                except Exception:
                    logging.exception('Desktop shutdown')
                    message('退出未完成，请稍后重试；详情见 MikoLocal\\logs。')
                    closing.clear()
                    return
                stopped.set()
                window.destroy()
            threading.Thread(target=shutdown, daemon=True).start()
            return False

        def boot():
            nonlocal job
            try:
                start(settings)
                job = ChildJob()
                backend = recorded_process(settings)[1]
                job.attach(backend)
                for child in backend.children(recursive=True):
                    job.attach(child)
                write_json(root / 'runtime/desktop-build.json', {**build, 'local_root': str(root)})
                inventory = REPO_ROOT / 'portable-files.txt'
                if inventory.is_file():
                    (root / 'runtime/desktop-files.txt').write_text(inventory.read_text('utf-8'), encoding='utf-8')
                threading.Thread(target=wake, daemon=True).start()
                if not closing.is_set():
                    window.load_url(settings.url)
            except Exception as error:
                logging.exception('Desktop startup')
                message(str(error) + '\n\n可在 MikoLocal\\logs 中查看日志。', 'Miko 启动失败')
                stopped.set()
                window.destroy()
            finally:
                boot_done.set()

        def wake():
            while not stopped.is_set():
                if instance.kernel.WaitForSingleObject(instance.event, 500) == 0:
                    window.restore()
                    window.show()

        window.events.closing += finish
        webview.start(boot, gui='edgechromium', private_mode=False, storage_path=str(root / 'cache/webview'),
            icon=str(REPO_ROOT / 'Miko.ico'), localization={'global.quitConfirmation': '退出 Miko？'})
        cleanup()
    except Exception as error:
        logging.exception('Desktop failed')
        message(str(error) + '\n\n可在 MikoLocal\\logs 中查看日志。', 'Miko 启动失败')
    finally:
        try:
            cleanup()
        except Exception:
            logging.exception('Desktop final cleanup')
        if job:
            job.close()
        instance.close()


if __name__ == '__main__':
    main()
