"""Bounded child receipts and real isolated CLI classification, without real roles."""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools.demo_operator_setup import canonical, digest
from tests.tools.test_gold_bar_feedback_publish import document


SOURCE = 'c'*40
CYCLE = 'a'*64
FAILURE_SCHEMA = 'DEMO_RESEARCH_FEEDBACK_RETRYABLE_FAILURE_V1'


def exported(bar=True):
    return dict(status='BAR_STRATEGY_FEEDBACK_RETAINED' if bar else 'STRATEGY_FEEDBACK_RETAINED',
        feedback_sha256='1'*64, artifact_sha256='2'*64, path='/untrusted/ignored',
        submission_enabled=False, economic_authority=False)


def pending(value):
    return dict(schema=FAILURE_SCHEMA, status='RESEARCH_FEEDBACK_DELIVERY_PENDING',
        reason='FEEDBACK_PUBLICATION_DESTINATION_UNAVAILABLE', source_commit=SOURCE,
        cycle_sha256=CYCLE, artifact_sha256=value['artifact_sha256'], feedback_sha256=value['feedback_sha256'],
        source_verified=False, submission_enabled=False, economic_authority=False)


BOOTSTRAP = r'''
import errno,json,os,sys
from pathlib import Path
from tools import demo_feedback_publish as p
settings=json.loads(sys.argv[1]); real_uid=os.geteuid(); dropped=False
p.EXECUTOR=Path(settings['executor'])
# Only installation/accepted-source/virtual-role boundaries are replaced. The
# original artifact reader, validator, destination publisher and main execute.
p.custody._require_protected=lambda *a,**kw:None
def verify(source):
    assert source==settings['source']
    if settings['phase']=='source':raise OSError(errno.ENOSPC,'synthetic private detail')
p.verify_source=verify
p.os.geteuid=lambda:real_uid if dropped else 0
p.configuration=lambda source:dict(executor_uid=real_uid,research_uid=real_uid,
    research_gid=os.getgid(),research_state_root=settings['research'])
def drop(*args):
    global dropped
    if settings['phase']=='drop':raise OSError(errno.ENOSPC,'synthetic private detail')
    dropped=True
p.custody.drop_identity=drop
if settings['phase']=='read':
    def bad_read(**kw):raise ValueError('FEEDBACK_PUBLICATION_DESTINATION_UNAVAILABLE')
    p.read_export=bad_read
original_mkdir=p.os.mkdir
def mkdir(name,*args,**kwargs):
    if name=='research' and settings['error'] is not None:
        assert dropped
        raise OSError(settings['error'],'synthetic private detail')
    return original_mkdir(name,*args,**kwargs)
p.os.mkdir=mkdir
args=['--bar','--source-commit',settings['source'],'--cycle',settings['cycle'],
    '--artifact-sha256',settings['artifact'],'--feedback-sha256',settings['feedback']]
raise SystemExit(p.main(args))
'''


def cli(tmp_path, *, error=None, phase='publish', damage=None, invoke=None):
    value = document(); artifact = digest(value)
    executor = tmp_path/'executor'; research = tmp_path/'research'
    directory = executor/'strategy-bar-feedback'/CYCLE/'research-feedback'
    directory.mkdir(parents=True)
    raw = canonical(value)+b'\n'
    path = directory/(artifact+'.json'); path.write_bytes(raw)
    for item in (executor, *executor.rglob('*')):
        item.chmod(0o700 if item.is_dir() else 0o600)
    if damage == 'artifact': path.write_bytes(b'{}\n')
    if damage == 'custody': research.mkdir(mode=0o700); research.chmod(0o777)
    settings = dict(executor=str(executor), research=str(research), source=SOURCE,
        cycle=CYCLE, artifact=artifact, feedback=value['result_hash'], phase=phase, error=error)
    if invoke is not None:return invoke(settings)
    child = subprocess.run([sys.executable, '-B', '-c', BOOTSTRAP, json.dumps(settings)],
        cwd=Path(__file__).resolve().parents[2], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False)
    assert child.stderr == b''
    assert b'synthetic private detail' not in child.stdout and str(tmp_path).encode() not in child.stdout
    return child, json.loads(child.stdout), settings


@pytest.mark.parametrize('error', [errno.ENOSPC, errno.EDQUOT, errno.EIO, errno.EROFS, errno.EAGAIN, errno.ETIMEDOUT])
def test_real_cli_only_post_drop_storage_failure_has_bound_retry_receipt(tmp_path, error):
    child, receipt, settings = cli(tmp_path, error=error)
    expected = pending(dict(artifact_sha256=settings['artifact'], feedback_sha256=settings['feedback']))
    assert (child.returncode, receipt) == (4, expected)


@pytest.mark.parametrize('error', [errno.EACCES, errno.EPERM, errno.ELOOP, errno.ENOTDIR,
    errno.ENOENT, errno.ENOSYS, errno.EEXIST, errno.EINVAL])
def test_real_cli_path_and_permission_errors_remain_unclassified(tmp_path, error):
    child, receipt, _ = cli(tmp_path, error=error)
    assert child.returncode == 3
    assert receipt == dict(status='RESEARCH_FEEDBACK_DELIVERY_FAILED', submission_enabled=False)


@pytest.mark.parametrize('phase,damage', [('source', None), ('read', None), ('drop', None),
    ('publish', 'artifact'), ('publish', 'custody')])
def test_real_cli_validation_and_role_failures_never_advertise_retry(tmp_path, phase, damage):
    child, receipt, _ = cli(tmp_path, phase=phase, damage=damage)
    assert child.returncode == 3
    assert receipt == dict(status='RESEARCH_FEEDBACK_DELIVERY_FAILED', submission_enabled=False)


def test_real_cli_success_keeps_original_receipt(tmp_path):
    child, receipt, settings = cli(tmp_path)
    assert child.returncode == 0
    assert receipt == dict(status='RESEARCH_FEEDBACK_DELIVERED',
        feedback_sha256=settings['feedback'], cycle_sha256=CYCLE, source_verified=False,
        submission_enabled=False, economic_authority=False)


@pytest.mark.parametrize('error', [None, errno.ENOSPC])
def test_shared_handoff_consumes_real_isolated_cli_child(tmp_path, monkeypatch, error):
    from tools import gold_feedback_delivery as delivery
    original_run = subprocess.run
    def invoke(settings):
        def child(command, **kwargs):
            assert command[1:5] == ['-P','-B','-m','tools.demo_feedback_publish']
            assert kwargs['timeout'] == 30 and kwargs['cwd'] == Path('/opt/quantos/releases')/SOURCE
            # Map only the accepted release and test roles. A real separate
            # process still executes main/read/validation/drop/publish/output.
            return original_run([sys.executable,'-B','-c',BOOTSTRAP,json.dumps(settings)],
                cwd=Path(__file__).resolve().parents[2],stdin=kwargs['stdin'],stdout=kwargs['stdout'],
                stderr=kwargs['stderr'],timeout=kwargs['timeout'],check=kwargs['check'])
        monkeypatch.setattr(delivery.subprocess,'run',child)
        value = exported();value.update(artifact_sha256=settings['artifact'],feedback_sha256=settings['feedback'])
        if error is None:
            result = delivery.deliver_feedback(source_commit=SOURCE,cycle=CYCLE,exported=value)
            assert result['status'] == 'RESEARCH_FEEDBACK_DELIVERED'
        else:
            with pytest.raises(delivery.FeedbackDeliveryPending) as caught:
                delivery.deliver_feedback(source_commit=SOURCE,cycle=CYCLE,exported=value)
            assert caught.value.receipt == pending(value)
    cli(tmp_path,error=error,invoke=invoke)


def runner(monkeypatch, delivery, *, receipt, code=0, bar=True):
    def child(command, **kwargs):
        assert command == [sys.executable, '-P', '-B', '-m', 'tools.demo_feedback_publish',
            '--bar' if bar else '--strategy', '--source-commit', SOURCE, '--cycle', CYCLE,
            '--artifact-sha256', '2'*64, '--feedback-sha256', '1'*64]
        assert kwargs == dict(cwd=Path('/opt/quantos/releases')/SOURCE,
            env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONPATH':'/opt/quantos/releases/'+SOURCE,
                'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1'},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=30, check=False)
        return SimpleNamespace(returncode=code, stdout=json.dumps(receipt).encode())
    monkeypatch.setattr(delivery.subprocess, 'run', child)


@pytest.mark.parametrize('bar', [False, True])
def test_original_success_uses_same_fixed_separate_child(monkeypatch, bar):
    from tools import gold_feedback_delivery as delivery
    receipt = dict(status='RESEARCH_FEEDBACK_DELIVERED', cycle_sha256=CYCLE,
        feedback_sha256='1'*64, source_verified=False, submission_enabled=False, economic_authority=False)
    runner(monkeypatch, delivery, receipt=receipt, bar=bar)
    assert delivery.deliver_feedback(source_commit=SOURCE, cycle=CYCLE, exported=exported(bar)) == receipt


@pytest.mark.parametrize('bar', [False, True])
def test_only_exact_pending_receipt_raises_typed_pending(monkeypatch, bar):
    from tools import gold_feedback_delivery as delivery
    receipt = pending(exported(bar)); runner(monkeypatch, delivery, receipt=receipt, code=4, bar=bar)
    with pytest.raises(delivery.FeedbackDeliveryPending) as caught:
        delivery.deliver_feedback(source_commit=SOURCE, cycle=CYCLE, exported=exported(bar))
    assert isinstance(caught.value, ValueError) and caught.value.receipt == receipt


@pytest.mark.parametrize('damage', ['schema','status','reason','source_commit','cycle_sha256',
    'artifact_sha256','feedback_sha256','source_verified','submission_enabled','economic_authority','extra','missing','code'])
def test_wrong_pending_bindings_do_not_become_retryable(monkeypatch, damage):
    from tools import gold_feedback_delivery as delivery
    receipt = pending(exported())
    if damage == 'extra':receipt['path']='/untrusted/ignored'
    elif damage == 'missing':del receipt['reason']
    elif damage != 'code':receipt[damage] = True if damage.endswith('enabled') or damage in ('source_verified','economic_authority') else 'incorrect'
    runner(monkeypatch, delivery, receipt=receipt, code=3 if damage=='code' else 4)
    with pytest.raises(ValueError) as caught:
        delivery.deliver_feedback(source_commit=SOURCE, cycle=CYCLE, exported=exported())
    assert not isinstance(caught.value, delivery.FeedbackDeliveryPending)


@pytest.mark.parametrize('kind', ['generic','timeout','oserror','garbage','duplicate','oversized','success_nonzero'])
def test_unknown_child_outcome_is_not_retryable(monkeypatch, kind):
    from tools import gold_feedback_delivery as delivery
    def child(*args, **kwargs):
        if kind == 'timeout':raise subprocess.TimeoutExpired('isolated-child',30)
        if kind == 'oserror':raise OSError(errno.EIO,'isolated-child')
        raw = dict(status='RESEARCH_FEEDBACK_DELIVERY_FAILED', submission_enabled=False)
        if kind == 'success_nonzero':raw = dict(status='RESEARCH_FEEDBACK_DELIVERED',cycle_sha256=CYCLE,
            feedback_sha256='1'*64,source_verified=False,submission_enabled=False,economic_authority=False)
        body = json.dumps(raw).encode()
        if kind == 'garbage':body=b'not json'
        if kind == 'oversized':body=b' '*65537
        if kind == 'duplicate':body=json.dumps(pending(exported())).encode()[:-1]+b',"schema":"DEMO_RESEARCH_FEEDBACK_RETRYABLE_FAILURE_V1"}'
        return SimpleNamespace(returncode=4 if kind in ('garbage','duplicate','oversized') else 3,stdout=body)
    monkeypatch.setattr(delivery.subprocess,'run',child)
    with pytest.raises((ValueError,OSError,subprocess.TimeoutExpired)) as caught:
        delivery.deliver_feedback(source_commit=SOURCE,cycle=CYCLE,exported=exported())
    assert not isinstance(caught.value,delivery.FeedbackDeliveryPending)
