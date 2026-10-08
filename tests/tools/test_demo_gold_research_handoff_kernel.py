"""Real Linux root/UID/SCM_RIGHTS/pidfd checks in the isolated offline container.

The existing fixture supplies synthetic accepted-release/public authorities.
Research-directory ownership, request custody, credentials and process identities
are actual kernel facts. Declared source bundles are not native archive evidence.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.tools.test_demo_gold_research_layout import configured as configured, capture
from tests.tools.test_demo_gold_checkpoint_public import fixed_public as fixed_public, public_cut
from tests.tools.test_demo_market_partition_install import book as book, env as env
from tools import demo_gold_research_handoff as api
from tools.gold_pilot_retention_store import canonical

pytestmark = pytest.mark.skipif(os.getuid() != 0, reason='requires isolated Linux root/UID container')
_CHOWN, _FCHOWN = os.chown, os.fchown

CHILD = '''
import json, os, socket, sys
sys.path.insert(0, '/code')
from tools import demo_gold_research_handoff as api
channel = socket.socket(fileno=int(sys.argv[1]))
fd = None
try:
    fd = api.receive_request(channel)
    with api.admitted(fd) as lease:
        print(json.dumps(dict(status='ADMITTED', uid=os.getresuid(), gid=os.getresgid(),
            groups=os.getgroups(), pid=os.getpid(), request_sha256=lease.request_sha256,
            owner=os.fstat(fd).st_uid)), flush=True)
        if len(sys.argv) > 2:
            assert lease.poller.poll(10000), 'root controller did not exit'
        else:
            channel.recv(1)
        lease.check()
        print(json.dumps(dict(status='RECHECKED')), flush=True)
except Exception as error:
    print(json.dumps(dict(status='REJECTED', error=str(error))), flush=True)
    sys.exit(2)
finally:
    if fd is not None:
        os.close(fd)
    channel.close()
'''


@pytest.fixture
def kernel_case(configured, tmp_path, monkeypatch):
    f = configured
    assert os.getresuid() == os.getresgid() == (0, 0, 0)
    monkeypatch.setattr(os, 'chown', _CHOWN)
    monkeypatch.setattr(os, 'fchown', _FCHOWN)
    # Only the dedicated container's scratch directories become traversable.
    for path in (tmp_path, *tmp_path.parents):
        if path == Path('/work') or path.is_relative_to('/work'):
            path.chmod(0o755)
    for row in f['roots']:
        root = Path(row['root'])
        uid = row['layout']['uid']
        for path in (root, *root.rglob('*')):
            os.chown(path, uid, uid)
    # Original fixed-roster reads now inspect actual, rather than virtual, owners.
    monkeypatch.setattr(f['api'], '_identity', lambda info: {key: getattr(info, key) for key in f['api']._STAT})
    public_source = f['f']
    snapshot = public_source.module.capture(**public_source.args)
    public, cut = public_cut(public_source, snapshot)
    verified = public_source.module.bind_captured(snapshot, public_bundle=public,
        cut_request=cut, guard=public_source.args['guard'])
    f.update(handle=verified, public=public, cut=cut)
    captured = capture(f)
    source = dict(schema='GOLD_CHECKPOINT_VERIFIED_SOURCE_V1',
        source_root=str(tmp_path / 'declared-source'), bundle_path=str(tmp_path / 'declared.bundle'),
        bundle_sha256='b' * 64, identity=dict(commit=public['source_commit'],
            python_source_sha256='c' * 64, dependency_lock_sha256='d' * 64,
            python_version='3.12.3', architecture='aarch64'))
    return dict(f=f, captured=captured, source=source)


def launch(channel, uid, *, groups=(), stdout=subprocess.PIPE, wait_for_controller=False):
    command = [sys.executable, '-I', '-B', '-c', CHILD, str(channel.fileno())]
    if wait_for_controller:
        command.append('WAIT_FOR_CONTROLLER_EXIT')
    return subprocess.Popen(command,
        pass_fds=(channel.fileno(),), user=uid, group=uid, extra_groups=groups,
        env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'}, cwd='/work',
        stdin=subprocess.DEVNULL, stdout=stdout, stderr=subprocess.PIPE, text=True)


def stop(child):
    # The caller closes its socket first; EOF must make the role exit itself.
    child.communicate(timeout=10)


@pytest.mark.parametrize('configured', ['multiple'], indirect=True)
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
def test_root_to_every_original_role_with_actual_kernel_identity(kernel_case):
    f = kernel_case
    uids = sorted(row['layout']['uid'] for row in f['f']['roots'])
    assert len(uids) == 2
    for uid in uids:
        with api.issue(verified_layout=f['captured'], verified_public=f['f']['handle'],
                uid=uid, source_attachment=f['source']) as request:
            parent, inherited = api.channel_pair()
            child = launch(inherited, uid)
            inherited.close()
            try:
                pid, ticks = api.process_identity(Path('/proc') / str(child.pid))
                api.send_request(parent, request, worker_pid=pid, worker_start_ticks=ticks)
                prepared = json.loads(child.stdout.readline())
                assert prepared == dict(status='ADMITTED', uid=[uid] * 3, gid=[uid] * 3,
                    groups=[], pid=pid, request_sha256=request.request_sha256, owner=0)
                parent.send(b'C')
                tail, error = child.communicate(timeout=10)
                assert child.returncode == 0, error
                assert json.loads(tail) == {'status': 'RECHECKED'}
                assert request.document()['writer_barrier_verified'] is False
            finally:
                parent.close()
                stop(child)


def test_actual_root_controller_exit_invalidates_original_role_lease(kernel_case):
    f = kernel_case
    uid = f['f']['roots'][0]['layout']['uid']
    report_read, report_write = os.pipe()
    role_read, role_write = os.pipe()
    root_pid = os.fork()
    if root_pid == 0:
        os.close(report_read)
        try:
            with api.issue(verified_layout=f['captured'], verified_public=f['f']['handle'],
                    uid=uid, source_attachment=f['source']) as request:
                parent, inherited = api.channel_pair()
                child = launch(inherited, uid, stdout=role_write, wait_for_controller=True)
                inherited.close()
                os.close(role_write)
                pid, ticks = api.process_identity(Path('/proc') / str(child.pid))
                api.send_request(parent, request, worker_pid=pid, worker_start_ticks=ticks)
                with os.fdopen(role_read) as stream:
                    prepared = json.loads(stream.readline())
                assert prepared['status'] == 'ADMITTED'
                assert request.document()['controller_pid'] == os.getpid()
                os.write(report_write, canonical(dict(worker_pid=pid, root_pid=os.getpid(), status='ADMITTED')))
                parent.close()
            os._exit(0)
        except BaseException as error:
            os.write(report_write, canonical(dict(status='FAILED', error=str(error))))
            os._exit(2)
    os.close(report_write)
    os.close(role_write)
    try:
        raw_report = os.read(report_read, 65536)
        waited, result = os.waitpid(root_pid, 0)
        report = json.loads(raw_report)
        assert waited == root_pid and os.waitstatus_to_exitcode(result) == 0, report
        assert report['root_pid'] == root_pid and report['status'] == 'ADMITTED'
        with os.fdopen(os.dup(role_read)) as stream:
            rejected = json.loads(stream.readline())
        assert rejected == dict(status='REJECTED', error='GOLD_RESEARCH_HANDOFF_CONTROLLER_EXITED')
        # The container's pytest is PID 1 and reaps this now-orphaned role.
        if os.getpid() == 1:
            waited, result = os.waitpid(report['worker_pid'], 0)
            assert waited == report['worker_pid'] and os.waitstatus_to_exitcode(result) == 2
    finally:
        os.close(report_read)
        os.close(role_read)


@pytest.mark.parametrize('damage', ['wrong_uid', 'extra_group'])
def test_real_foreign_role_and_supplementary_privilege_reject(kernel_case, damage):
    f = kernel_case
    uid = f['f']['roots'][0]['layout']['uid']
    with api.issue(verified_layout=f['captured'], verified_public=f['f']['handle'],
            uid=uid, source_attachment=f['source']) as request:
        parent, inherited = api.channel_pair()
        child_uid = uid + 100 if damage == 'wrong_uid' else uid
        child = launch(inherited, child_uid, groups=[0] if damage == 'extra_group' else [])
        inherited.close()
        try:
            pid, ticks = api.process_identity(Path('/proc') / str(child.pid))
            with pytest.raises(ValueError, match='WORKER_IDENTITY|PACKET'):
                api.send_request(parent, request, worker_pid=pid, worker_start_ticks=ticks)
            if damage == 'extra_group':
                output, error = child.communicate(timeout=10)
                assert child.returncode == 2, error
                assert json.loads(output)['error'].endswith('ROLE_IDENTITY')
        finally:
            parent.close()
            stop(child)


def test_same_path_new_research_root_rejects_live_role_recheck(kernel_case):
    f = kernel_case
    uid = f['f']['roots'][0]['layout']['uid']
    with pytest.raises(ValueError, match='CHANGED|PATH_CUSTODY'):
        with api.issue(verified_layout=f['captured'], verified_public=f['f']['handle'],
                uid=uid, source_attachment=f['source']) as request:
            parent, inherited = api.channel_pair()
            child = launch(inherited, uid)
            inherited.close()
            try:
                pid, ticks = api.process_identity(Path('/proc') / str(child.pid))
                api.send_request(parent, request, worker_pid=pid, worker_start_ticks=ticks)
                assert json.loads(child.stdout.readline())['status'] == 'ADMITTED'
                root = Path(request.document()['research_root'])
                root.rename(root.with_name('original-retained'))
                root.mkdir(mode=0o700)
                os.chown(root, uid, uid)
                parent.send(b'C')
                output, error = child.communicate(timeout=10)
                assert child.returncode == 2, error
                assert json.loads(output)['error'].endswith('ROOT_CHANGED')
            finally:
                parent.close()
                stop(child)
