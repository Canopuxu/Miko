import ctypes
from ctypes import wintypes


class ChildJob:
    """Windows owns the attached process trees until this handle closes."""
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

    def attach(self, pid):
        handle = self.kernel.OpenProcess(0x101 | 0x1000, False, pid)
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
