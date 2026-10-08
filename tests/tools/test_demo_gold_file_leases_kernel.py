"""Actual full fixed-role metadata and Linux leases, no host state or services.

Research originals are native producer outputs. The three other roots here are
real owned fixture files, not a native Gold four-role restore or a writer barrier.
Public/release boundaries remain the original explicitly synthetic fixtures.
"""
import importlib
import os
from pathlib import Path
import select
import signal
import subprocess
import sys

import pytest

from tests.tools.test_demo_gold_research_capture_peer_kernel import (
    native_roster as native_roster,
    source_attachment as source_attachment,
    registered_case as registered_case,
    native_research as native_research,
    private_files as private_files,
    fixed_public as fixed_public,
    book as book,
    env as env,
)
from tools import demo_gold_namespace_checkpoint as native
from tests.tools.test_demo_gold_research_launcher_kernel import manager_boundary as manager_boundary


@pytest.fixture
def lease_case(native_roster, tmp_path, monkeypatch):
    api = importlib.import_module('tools.demo_gold_file_leases')
    f = native_roster
    roots = {}
    for role in ('executor', 'issuer', 'controller'):
        uid = native._role_uid(f['roster']._public['registry'], role)
        root = tmp_path / ('lease-role-' + role)
        root.mkdir(mode=0o700)
        leaf = root / 'fixture-record.json'
        leaf.write_bytes(b'{"fixture":true}')
        leaf.chmod(0o600)
        os.chown(leaf, uid, uid)
        os.chown(root, uid, uid)
        roots[role] = root
    monkeypatch.setattr(native, 'ROLE_ROOTS', roots)
    return dict(api=api, f=f, roots=roots)


def hold(f):
    return f['api'].hold(verified_layout=f['f']['roster'], verified_public=f['f']['public'])


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated Linux root with CAP_LEASE')
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
def test_all_four_fixed_roles_and_native_research_uids_keep_real_read_leases(lease_case):
    f = lease_case
    with hold(f) as guard:
        report = guard.report()
        assert report['roles'] == ['controller', 'executor', 'issuer', 'research']
        assert report['research_uids'] == [10001, 10002]
        assert report['original_file_leases_verified'] is True
        assert report['writer_barrier_verified'] is report['whole_set_verified'] is False
        assert report['submission_enabled'] is report['economic_authority'] is False
        for path in (f['f']['values'][1]['input_path'], str(f['roots']['controller'] / 'fixture-record.json')):
            # Other open read-only descriptions do not break a read lease.
            with open(path, 'rb') as reader:
                assert reader.read(1)
        guard.recheck()


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated Linux root with CAP_LEASE')
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
@pytest.mark.parametrize('writer_timing', ['existing', 'after'])
def test_real_writer_handle_or_nonblocking_open_cannot_be_reported_as_held(lease_case, writer_timing):
    f = lease_case
    value = f['f']['values'][1]
    target = value['input_path']
    uid = value['layout']['uid']
    if writer_timing == 'existing':
        program = 'import os,sys; fd=os.open(sys.argv[1],os.O_RDWR); print("ready",flush=True); sys.stdin.read(1); os.close(fd)'
        child = subprocess.Popen([sys.executable, '-I', '-c', program, target], user=uid, group=uid,
            extra_groups=[], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert select.select([child.stdout], [], [], 5)[0]
            assert child.stdout.readline().strip() == 'ready'
            with pytest.raises(ValueError, match='LEASE_UNAVAILABLE'):
                with hold(f):
                    pytest.fail('an existing writer must prevent capture')
        finally:
            child.stdin.write('x')
            child.stdin.close()
            child.wait(timeout=10)
            assert child.returncode == 0
    else:
        program = '''
import errno,os,sys
try:
    fd=os.open(sys.argv[1],os.O_WRONLY|os.O_NONBLOCK)
except OSError as error:
    raise SystemExit(0 if error.errno in (errno.EAGAIN,errno.EWOULDBLOCK) else 3)
os.close(fd)
raise SystemExit(4)
'''
        with pytest.raises(ValueError, match='LEASE_BROKEN'):
            with hold(f) as guard:
                result = subprocess.run([sys.executable, '-I', '-c', program, target], user=uid, group=uid,
                    extra_groups=[], stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
                assert result.returncode == 0, result.stderr
                guard.recheck()


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated Linux root with CAP_LEASE')
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
@pytest.mark.parametrize('change', ['new_member', 'replacement'])
def test_original_directory_population_and_path_identity_are_rechecked(lease_case, change):
    f = lease_case
    root = f['roots']['controller']
    with pytest.raises(ValueError, match='ORIGINAL_CHANGED'):
        with hold(f) as guard:
            if change == 'new_member':
                (root / 'new.json').write_bytes(b'{}')
            else:
                (root / 'replacement.json').write_bytes((root / 'fixture-record.json').read_bytes())
                os.replace(root / 'replacement.json', root / 'fixture-record.json')
            guard.recheck()


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated Linux root with CAP_LEASE and CAP_SETPCAP')
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
def test_native_archives_and_controlled_exits_retain_the_original_file_leases(lease_case, manager_boundary):
    from tools import demo_gold_research_launcher as launcher
    f = lease_case
    native_case = f['f']
    assert manager_boundary['f'] is native_case
    with hold(f) as guard:
        result = launcher.capture_all(verified_layout=native_case['roster'], verified_public=native_case['public'],
            source_attachments={value['layout']['uid']: value['source'] for value in native_case['values']},
            file_leases=guard)
        assert result['original_file_leases_verified'] is result['worker_exit_verified'] is True
        assert result['original_file_lease_scope_sha256'] == guard.report()['scope_sha256']
        assert result['writer_barrier_verified'] is result['whole_set_verified'] is False
        assert result['submission_enabled'] is result['economic_authority'] is False
    with pytest.raises(ValueError, match='CLOSED'):
        guard.report()


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated Linux root with CAP_LEASE')
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
def test_unsupported_filesystem_reclaims_partial_handles_and_signal_mask(lease_case, monkeypatch):
    f = lease_case
    before = set(Path('/proc/self/fd').iterdir())
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    original = f['api'].fcntl.fcntl
    def unsupported(descriptor, operation, *args):
        if operation == f['api'].fcntl.F_SETLEASE and args == (f['api'].fcntl.F_RDLCK,):
            raise OSError(22, 'synthetic unsupported lease filesystem')
        return original(descriptor, operation, *args)
    monkeypatch.setattr(f['api'].fcntl, 'fcntl', unsupported)
    with pytest.raises(ValueError, match='LEASE_UNAVAILABLE'):
        with hold(f):
            pytest.fail('unsupported leases cannot become a success')
    assert set(Path('/proc/self/fd').iterdir()) == before
    assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == mask


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated Linux root')
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
def test_existing_signal_scope_is_preserved_on_rejection(lease_case):
    old = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGIO})
    try:
        os.kill(os.getpid(), signal.SIGIO)
        with pytest.raises(ValueError, match='SIGNAL_SCOPE'):
            with hold(lease_case):
                pytest.fail('cannot take another signal owner scope')
        assert signal.SIGIO in signal.sigpending()
        assert signal.sigtimedwait({signal.SIGIO}, 0).si_signo == signal.SIGIO
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)
