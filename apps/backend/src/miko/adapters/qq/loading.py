"""Serialize native QQ loading across source and portable Miko processes."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib


class LoadingBusy(TimeoutError):
    pass


@contextmanager
def loading_lock(identity, timeout_ms=2000):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    name = 'Local\\MikoQQ-' + hashlib.sha256(str(identity).encode()).hexdigest()
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        result = kernel.WaitForSingleObject(handle, timeout_ms)
        if result == 0x102:
            raise LoadingBusy('另一个 Miko 进程正在检测或连接此 QQ，请稍候')
        if result not in (0, 0x80):
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = True
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)
