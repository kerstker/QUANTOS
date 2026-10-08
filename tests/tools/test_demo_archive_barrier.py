"""An archive barrier uses actual writer lock inodes, never substitute locks."""
import fcntl
import os
import pytest
from tools import demo_archive_barrier as barrier

@pytest.fixture
def source(tmp_path):
    root=tmp_path/'executor';root.mkdir(mode=0o700)
    (root/'resident-recovery').mkdir(mode=0o700)
    for name in ('observation.lock','origin-capture.lock','resident-recovery/cycle.lock'):
        (root/name).touch(mode=0o600)
    return root


def test_every_writer_lock_is_held_and_released(source):
    with barrier.executor_barrier(source):
        for name in barrier.LOCKS:
            with (source/name).open('rb') as lock:
                with pytest.raises(BlockingIOError):fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    for name in barrier.LOCKS:
        with (source/name).open('rb') as lock:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)


def test_busy_later_lock_releases_earlier_locks(source):
    with (source/barrier.LOCKS[-1]).open('rb') as occupied:
        fcntl.flock(occupied,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError,match='ARCHIVE_WRITER_BUSY'):
            with barrier.executor_barrier(source):pytest.fail('entered')
        with (source/barrier.LOCKS[0]).open('rb') as earlier:
            fcntl.flock(earlier,fcntl.LOCK_EX|fcntl.LOCK_NB)


@pytest.mark.parametrize('damage',['missing','symlink','hardlink','permissions'])
def test_untrusted_lock_never_created_or_accepted(source,damage):
    target=source/barrier.LOCKS[0]
    if damage in ('missing','symlink','hardlink'):target.unlink()
    if damage=='symlink':target.symlink_to(source/barrier.LOCKS[1])
    if damage=='hardlink':os.link(source/barrier.LOCKS[1],target)
    if damage=='permissions':target.chmod(0o666)
    with pytest.raises((ValueError,FileNotFoundError)):
        with barrier.executor_barrier(source):pytest.fail('entered')
    if damage=='missing':assert not target.exists()


def test_replaced_lock_is_detected_before_success_exit(source):
    with pytest.raises(ValueError,match='ARCHIVE_LOCK_CHANGED'):
        with barrier.executor_barrier(source):
            lock=source/barrier.LOCKS[0];lock.rename(source/'old.lock');lock.touch(mode=0o600)


def test_body_exception_releases_all_locks(source):
    with pytest.raises(RuntimeError):
        with barrier.executor_barrier(source):raise RuntimeError('operation failed')
    with barrier.executor_barrier(source):pass
