"""Cross-platform process-liveness probe for single-host orchestration locks."""
from __future__ import annotations

import ctypes
import os


def process_is_alive(pid: int) -> bool:
    """Return whether *pid* currently names a live process on this host."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        open_process.restype = ctypes.c_void_p
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_int
        handle = open_process(0x1000, 0, pid)
        if handle:
            close_handle(handle)
            return True
        # Access denied also proves that the process object exists.
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OverflowError):
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
