import os
import select
import subprocess
import sys

import pytest

from services.research_runner.ownership import runner_ownership


def test_process_exit_releases_os_lock_without_deleting_record(tmp_path):
    path = tmp_path/'runner.lock'
    script = '''
import os,sys
from services.research_runner.ownership import runner_ownership
with runner_ownership(sys.argv[1]):
    print('OWNED',flush=True)
    sys.stdin.readline()
    os._exit(77)
'''
    child = subprocess.Popen([sys.executable, '-c', script, str(path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert select.select([child.stdout], [], [], 10)[0]
        assert child.stdout.readline() == b'OWNED\n'
        inode = path.stat().st_ino
        with pytest.raises(RuntimeError, match='RUNNER_ALREADY_OWNED'):
            with runner_ownership(path):
                pass
        _, error = child.communicate(b'exit\n', timeout=10)
        assert child.returncode == 77, error
        with runner_ownership(path):
            assert path.stat().st_ino == inode
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_shared_or_linked_lock_is_rejected_without_rewriting(tmp_path):
    path = tmp_path/'runner.lock'
    path.write_bytes(b'preserve')
    os.chmod(path, 0o644)
    with pytest.raises(RuntimeError, match='UNSAFE_FILE'):
        with runner_ownership(path):
            pass
    link = tmp_path/'linked.lock'
    link.symlink_to(path)
    with pytest.raises(RuntimeError, match='UNSAFE_PATH'):
        with runner_ownership(link):
            pass
    assert path.read_bytes() == b'preserve'
