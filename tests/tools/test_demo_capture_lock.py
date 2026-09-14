"""Periodic account snapshots may finish before the finite cycle reads."""
import threading
import pytest
from tools import binance_demo_user_stream as tool


def test_capture_waits_for_real_lock_release_without_parallel_ownership(tmp_path):
    path=tmp_path/'capture.lock';owned=threading.Event();release=threading.Event()
    def owner():
        with tool.writer_lock(path):
            owned.set();release.wait(2)
    thread=threading.Thread(target=owner);thread.start();assert owned.wait(2)
    timer=threading.Timer(.1,release.set);timer.start()
    try:
        with tool.writer_lock(path,wait_seconds=1):
            assert release.is_set()
    finally:release.set();timer.cancel();thread.join(2)


def test_capture_wait_has_fixed_deadline_and_leaves_owner_untouched(tmp_path,monkeypatch):
    path=tmp_path/'capture.lock'
    with tool.writer_lock(path):
        clock=iter([0,0,35]);sleeps=[]
        monkeypatch.setattr(tool.time,'monotonic',lambda:next(clock))
        monkeypatch.setattr(tool.time,'sleep',sleeps.append)
        with pytest.raises(ValueError,match='STREAM_WRITER_ALREADY_RUNNING'):
            with tool.writer_lock(path,wait_seconds=35):pytest.fail('parallel capture')
        assert sleeps and path.exists()


@pytest.mark.parametrize('budget',[-1,36,True,float('inf'),float('nan')])
def test_capture_wait_rejects_unbounded_or_invalid_budget_before_open(tmp_path,budget):
    path=tmp_path/'absent.lock'
    with pytest.raises(ValueError,match='STREAM_LOCK_WAIT_BUDGET'):
        with tool.writer_lock(path,wait_seconds=budget):pytest.fail('invalid budget')
    assert not path.exists()
