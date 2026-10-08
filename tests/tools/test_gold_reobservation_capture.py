"""Capture's trusted guard runs after its real lock and before network work."""
from contextlib import contextmanager
import fcntl
import os

import pytest

from tests.tools.test_gold_pilot_closure import (
    case as case, original_evidence as original_evidence,
    completed_bar_feedback as completed_bar_feedback, closure_case as closure_case,
)


@pytest.mark.parametrize('crossing',['lock_wait','witness_fsync'])
def test_capture_guard_refuses_deadline_crossing_before_get(closure_case,monkeypatch,crossing):
    from tools import demo_executor_origin as capture
    from tools.gold_pilot_retention import executor_retention
    f=closure_case;deadline=f.result['original_pilot_window']['ends_at_ms']+900000
    f.clock[0]=deadline-1000;original=capture.writer_lock;seen=[]
    @contextmanager
    def delayed(*args,**kwargs):
        with original(*args,**kwargs):
            if crossing=='lock_wait':f.clock[0]=deadline
            yield
    monkeypatch.setattr(capture,'writer_lock',delayed)
    def guard():
        fd=os.open(f.root/'origin-capture.lock',os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:os.close(fd)
        seen.append(True)
        if crossing=='witness_fsync':f.clock[0]=deadline
        if f.clock[0]>=deadline:raise ValueError('REOBSERVATION_CLOCK')
    before=list(f.read_calls)
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        claim=store.claim(bundle=f.base_runtime,now_ms=f.clock[0])
        with pytest.raises(ValueError,match='REOBSERVATION_CLOCK'):
            capture.capture_for_pilot(symbol='XAUUSDT',source_commit=f.source_commit,
                retention=store,claim=claim,before_capture=guard)
        assert store.read_claim(claim,'origin') is None
    assert seen==[True] and f.read_calls==before
