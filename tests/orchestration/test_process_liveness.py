from __future__ import annotations

import os

from services.orchestration.process_liveness import process_is_alive


def test_process_is_alive_rejects_invalid_ids_and_accepts_current_process():
    assert process_is_alive(True) is False
    assert process_is_alive(0) is False
    assert process_is_alive(-1) is False
    assert process_is_alive(os.getpid()) is True
