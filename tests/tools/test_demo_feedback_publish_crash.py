"""Real process exits and competing publishers; no account or service operation."""
import os
import signal
import stat
import time

import pytest

from tools import demo_feedback_publish as publisher
from tools.demo_operator_setup import canonical
from tests.tools.test_gold_bar_feedback_publish import document


def target(root, value, bar):
    return root/'data/cache/ai_factory'/('bar-execution-feedback' if bar else 'execution-feedback')/(value['result_hash']+'.json')


def wait_child(pid):
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            return os.waitstatus_to_exitcode(status)
        time.sleep(0.01)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    pytest.fail('Isolated publisher child exceeded 5 seconds')


def crash_publish(root, value, *, bar, point):
    destination = target(root, value, bar)
    pid = os.fork()
    if pid == 0:
        original_unlink, original_fsync = os.unlink, os.fsync
        def unlink(path, *args, **kwargs):
            # Original hard-link implementation reaches this with final nlink=2.
            if point == 'visible' and destination.exists(): os._exit(87)
            return original_unlink(path, *args, **kwargs)
        def fsync(fd):
            info = os.fstat(fd)
            if point == 'staged' and stat.S_ISREG(info.st_mode):
                original_fsync(fd)
                os._exit(86)
            # Also works for atomic rename: visible, before directory fsync and
            # publisher return. Fault injection does not replace publication.
            if point == 'visible' and stat.S_ISDIR(info.st_mode) and destination.exists(): os._exit(87)
            return original_fsync(fd)
        publisher.os.unlink, publisher.os.fsync = unlink, fsync
        try:
            publisher.publish(root, value, bar=bar)
        except BaseException:
            os._exit(98)
        os._exit(99)
    return wait_child(pid)


@pytest.mark.parametrize('bar', [False, True])
def test_visible_publication_survives_hard_exit_and_same_artifact_retry(tmp_path, bar):
    value = document()
    # The bar document passes the original validator. The False case exercises
    # the other immutable storage namespace, not a different account contract.
    publisher.validate_bar_export(value, source_commit='c'*40, feedback_sha256=value['result_hash'], now=3000)
    root = tmp_path/'research'
    assert crash_publish(root, value, bar=bar, point='visible') == 87
    path = target(root, value, bar)
    original = path.read_bytes()
    links = path.stat().st_nlink
    retry_error = None
    try: publisher.publish(root, value, bar=bar)
    except ValueError as error: retry_error = str(error)
    assert links == 1 and retry_error is None, {'published_nlink': links, 'same_artifact_retry_error': retry_error}
    assert path.read_bytes() == original == canonical(value)+b'\n'
    assert path.stat().st_nlink == 1


@pytest.mark.parametrize('bar', [False, True])
def test_hard_exit_before_publication_retries_without_removing_unknown_staging(tmp_path, bar):
    value = document(); root = tmp_path/'research'
    assert crash_publish(root, value, bar=bar, point='staged') == 86
    path = target(root, value, bar)
    assert not path.exists()
    originals = {p.name: (p.read_bytes(), p.stat().st_ino) for p in path.parent.iterdir()}
    assert len(originals) == 1
    publisher.publish(root, value, bar=bar)
    assert path.read_bytes() == canonical(value)+b'\n' and path.stat().st_nlink == 1
    assert all((path.parent/name).read_bytes() == body and (path.parent/name).stat().st_ino == ino
               for name, (body, ino) in originals.items())


@pytest.mark.parametrize('bar', [False, True])
def test_competing_identical_publishers_keep_one_immutable_single_link(tmp_path, bar):
    value = document(); root = tmp_path/'research'
    read_fd, write_fd = os.pipe()
    pids = []
    try:
        for _ in range(2):
            pid = os.fork()
            if pid == 0:
                os.close(write_fd)
                if os.read(read_fd, 1) != b'G': os._exit(96)
                os.close(read_fd)
                try: publisher.publish(root, value, bar=bar)
                except BaseException: os._exit(97)
                os._exit(0)
            pids.append(pid)
        os.close(read_fd); read_fd = None
        os.write(write_fd, b'GG')
        os.close(write_fd); write_fd = None
        assert [wait_child(pid) for pid in pids] == [0, 0]
        pids.clear()
    finally:
        for fd in (read_fd, write_fd):
            if fd is not None: os.close(fd)
        for pid in pids:
            try:
                if os.waitpid(pid, os.WNOHANG)[0] == 0:
                    os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
            except ChildProcessError: pass
    path = target(root, value, bar)
    assert path.read_bytes() == canonical(value)+b'\n' and path.stat().st_nlink == 1
    assert list(path.parent.iterdir()) == [path]
    before = path.stat()
    publisher.publish(root, value, bar=bar)
    after = path.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


@pytest.mark.parametrize('damage', ['conflict', 'legacy_hardlink', 'symlink', 'writable_mode'])
def test_existing_untrusted_destination_is_not_repaired_or_overwritten(tmp_path, damage):
    value = document(); root = tmp_path/'research'
    publisher.publish(root, value, bar=True)
    path = target(root, value, True)
    if damage == 'conflict': path.write_bytes(b'conflicting original\n')
    elif damage == 'legacy_hardlink': os.link(path, path.parent/'.feedback-old-unknown.tmp')
    elif damage == 'symlink':
        outside = tmp_path/'outside'; outside.write_bytes(path.read_bytes())
        path.unlink(); path.symlink_to(outside)
    else: path.chmod(0o666)
    original = path.read_bytes(); info = path.lstat()
    names = {p.name for p in path.parent.iterdir()}
    with pytest.raises(ValueError): publisher.publish(root, value, bar=True)
    assert path.read_bytes() == original and path.lstat().st_ino == info.st_ino
    assert {p.name for p in path.parent.iterdir()} == names


def test_preexisting_staging_name_collision_never_deletes_unknown_file(tmp_path, monkeypatch):
    value = document(); root = tmp_path/'research'
    path = target(root, value, True)
    path.parent.mkdir(parents=True, mode=0o700)
    for directory in (root, *root.rglob('*')):
        directory.chmod(0o700)
    nonce = b'c'*16
    unknown = path.parent/('.feedback-'+nonce.hex()+'.tmp')
    unknown.write_bytes(b'original interrupted or unrelated staging record\n')
    unknown.chmod(0o600)
    before = unknown.stat(); body = unknown.read_bytes()
    monkeypatch.setattr(publisher.os, 'urandom', lambda count: nonce)
    with pytest.raises(ValueError, match='DESTINATION_UNAVAILABLE'):
        publisher.publish(root, value, bar=True)
    assert unknown.exists(), 'failed exclusive creation must not grant cleanup ownership'
    after = unknown.stat()
    assert unknown.read_bytes() == body
    assert (after.st_ino, after.st_mtime_ns, after.st_ctime_ns) == (before.st_ino, before.st_mtime_ns, before.st_ctime_ns)
    assert not path.exists()
