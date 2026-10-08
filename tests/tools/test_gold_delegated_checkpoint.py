"""Retain a real large delegated task without weakening existing reader bounds.

Archive replay, admission and both descriptor readers remain native. Only the
controller UID is mapped onto this test's private temporary directory.
"""
from copy import deepcopy
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from live.execution import demo_authority
from live.execution.demo_batch_risk import _digest
from tests.live.test_demo_gold_delegated_issuer import native_issuer_case
from tests.live.test_gold_delegated_pipeline import task_material
from tests.live.test_gold_strategy_constraints import case as case
from tests.tools.test_gold_runtime_evidence import original_evidence as original_evidence
from tools import demo_portfolio_checkpoint as checkpoint
from tools.demo_operator_setup import canonical
from tools.demo_resident_recovery import _check_path


@pytest.fixture
def retained_task(case, original_evidence, tmp_path, monkeypatch):
    f = native_issuer_case(case, original_evidence, tmp_path, monkeypatch)
    task, _, context = task_material(f)
    body = canonical(task)
    assert 65536 < len(body) <= checkpoint.supervisor.MAX_INPUT_BYTES
    state = tmp_path/'controller'
    state.mkdir(mode=0o700)
    (state/'task-claims').mkdir(mode=0o700)
    uid, gid = os.getuid(), os.getgid()

    def private(path):
        path.relative_to(state)
        _check_path(path, uid, directory=True)

    def root_stat(info, *, directory=False):
        assert info.st_uid in (0, uid) if directory else info.st_uid == uid
        values = {name: getattr(info, name) for name in (
            'st_mode', 'st_uid', 'st_gid', 'st_dev', 'st_ino', 'st_nlink',
            'st_size', 'st_mtime_ns', 'st_ctime_ns')}
        values.update(st_uid=0, st_gid=0)
        # Temporary ancestors stand in for protected installed ancestors. File
        # type, size, links, permissions and all descriptor stability stay real.
        if directory:
            values['st_mode'] &= ~0o022
        return SimpleNamespace(**values)

    def controller_fstat(fd):
        Path(os.readlink('/proc/self/fd/'+str(fd))).relative_to(state)
        return root_stat(os.fstat(fd))

    original_small = checkpoint.supervisor._read_root_owned_json
    protected = demo_authority._require_protected

    def small_reader(path):
        path.relative_to(state)
        with monkeypatch.context() as mapping:
            mapping.setattr(demo_authority, '_require_protected',
                lambda info, *, directory: protected(root_stat(info, directory=directory), directory=directory))
            return original_small(path)

    writer = checkpoint.bundle.write_new

    def write(path, raw):
        path.relative_to(state)
        return writer(path, raw, uid=uid, gid=gid)

    monkeypatch.setattr(checkpoint.supervisor, 'STATE', state)
    monkeypatch.setattr(checkpoint.supervisor, '_root_private_directory', private)
    monkeypatch.setattr(checkpoint.supervisor, '_read_root_owned_json', small_reader)
    monkeypatch.setattr(checkpoint.supervisor, 'os',
        SimpleNamespace(**{**vars(os), 'fstat': controller_fstat}))
    monkeypatch.setattr(checkpoint.bundle, 'write_new', write)
    identity = _digest(task)
    result = dict(schema='GOLD_STRATEGY_DISPATCH_RESULT_V1', task_sha256=identity,
        result_sha256=_digest(f.proof['result']), submission_enabled=False, economic_authority=False)
    args = dict(task=task, result=result, allocation={'source_commit': 'c'*40},
        policy=f.base_policy, now_ms=f.now_ms, delegation_context=context)
    return SimpleNamespace(args=args, state=state, body=body, identity=identity,
        path=state/'tasks'/(identity+'.json'))


def test_native_large_task_materializes_twice_and_claim_prevents_replay(retained_task):
    f = retained_task
    first = checkpoint.materialize(**f.args)
    assert first['status'] == 'TASK_READY' and first['task_sha256'] == f.identity
    before = f.path.stat()
    assert f.path.read_bytes() == f.body
    assert checkpoint.materialize(**f.args) == first
    after = f.path.stat()
    assert (after.st_ino, after.st_mtime_ns, after.st_ctime_ns) == (
        before.st_ino, before.st_mtime_ns, before.st_ctime_ns)
    assert f.path.read_bytes() == f.body
    claim = f.state/'task-claims'/(f.identity+'.json')
    # Even an interrupted claim is permanent evidence against another send.
    claim.write_bytes(b'{')
    assert checkpoint.materialize(**f.args)['status'] == 'QUERY_ONLY'
    assert claim.read_bytes() == b'{' and f.path.read_bytes() == f.body
    assert list((f.state/'tasks').iterdir()) == [f.path]


@pytest.mark.parametrize('damage', ['same_json_wrong_bytes', 'different_task_same_filename'])
def test_existing_large_task_hash_or_bytes_change_is_rejected(retained_task, damage):
    f = retained_task
    checkpoint.materialize(**f.args)
    if damage == 'same_json_wrong_bytes':
        changed = f.body+b' '
    else:
        value = deepcopy(f.args['task'])
        value['orders'][0]['quantity'] = '0.2'
        changed = canonical(value)
    f.path.write_bytes(changed)
    with pytest.raises(ValueError, match='SUPERVISOR_REQUEST_DIGEST'):
        checkpoint.materialize(**f.args)
    assert f.path.read_bytes() == changed
    assert not list((f.state/'task-claims').iterdir())


def test_existing_large_task_reader_preserves_private_mode_requirement(retained_task):
    f = retained_task
    checkpoint.materialize(**f.args)
    f.path.chmod(0o644)
    with pytest.raises(ValueError, match='SUPERVISOR_REQUEST_CUSTODY'):
        checkpoint.materialize(**f.args)
    assert stat.S_IMODE(f.path.stat().st_mode) == 0o644
    assert not list((f.state/'task-claims').iterdir())


def test_generic_root_configuration_reader_keeps_64k_limit(retained_task):
    f = retained_task
    checkpoint.materialize(**f.args)
    with pytest.raises(ValueError, match='DEMO_REGISTRY_FILE'):
        checkpoint.supervisor._read_root_owned_json(f.path)
