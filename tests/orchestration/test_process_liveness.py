from __future__ import annotations

import os
import subprocess
import sys

import pytest

from services.orchestration.process_liveness import process_is_alive


def test_process_is_alive_rejects_invalid_ids_and_accepts_current_process():
    assert process_is_alive(True) is False
    assert process_is_alive(0) is False
    assert process_is_alive(-1) is False
    assert process_is_alive(os.getpid()) is True


@pytest.mark.skipif(os.name != "nt", reason="Windows retained-handle semantics")
def test_terminated_windows_process_is_not_alive_while_handle_is_retained():
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    open_process.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_int

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    retained_handle = open_process(0x1000, 0, child.pid)
    assert retained_handle
    try:
        assert process_is_alive(child.pid) is True
        child.terminate()
        child.wait(timeout=10)

        assert process_is_alive(child.pid) is False
    finally:
        close_handle(retained_handle)
