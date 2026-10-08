"""V2 is bounded published evidence, with a separate external runtime binding."""
from copy import deepcopy
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.tools.test_gold_feedback_delivery import BOOTSTRAP
from tests.tools.test_gold_reported_publication import reported
from tests.live.test_gold_feedback_recovery_fixture import case as case
from tests.live.test_gold_feedback_recovery_fixture import original_evidence as original_evidence
from tests.live.test_gold_feedback_recovery_fixture import completed_bar_feedback as completed_bar_feedback
from tools.demo_operator_setup import canonical, digest


CYCLE = 'a'*64


def document(tmp_path):
    value = reported(tmp_path)
    value.update(schema='GOLD_BAR_REPORTED_ACCOUNT_FEEDBACK_V2',
        opening_record_sha256='b'*64, bundle_sha256=CYCLE)
    value['result_hash'] = digest({k:v for k,v in value.items() if k != 'result_hash'})
    return value


@pytest.mark.parametrize('damage', [None, 'missing_opening', 'missing_bundle', 'extra',
    'opening_type', 'opening_hash', 'bundle_type', 'bundle_hash', 'wrong_cycle',
    'missing_cycle', 'v1_extra', 'unknown_schema', 'authority', 'repaired_gap',
    'nested_hash', 'source', 'future', 'stale_hash'])
def test_v2_exact_fields_hash_and_external_runtime(tmp_path, damage):
    from tools import demo_feedback_publish as publisher
    value = document(tmp_path)
    cycle = CYCLE
    now = value['available_at_ms']
    if damage == 'missing_opening': del value['opening_record_sha256']
    elif damage == 'missing_bundle': del value['bundle_sha256']
    elif damage == 'extra': value['can_trade'] = False
    elif damage == 'opening_type': value['opening_record_sha256'] = 1
    elif damage == 'opening_hash': value['opening_record_sha256'] = 'z'*64
    elif damage == 'bundle_type': value['bundle_sha256'] = None
    elif damage == 'bundle_hash': value['bundle_sha256'] = 'a'*63
    elif damage == 'wrong_cycle': cycle = 'c'*64
    elif damage == 'missing_cycle': cycle = None
    elif damage == 'v1_extra': value['schema'] = 'GOLD_BAR_REPORTED_ACCOUNT_FEEDBACK_V1'
    elif damage == 'unknown_schema': value['schema'] = 'GOLD_BAR_REPORTED_ACCOUNT_FEEDBACK_V3'
    elif damage == 'authority': value['economic_eligible'] = True
    elif damage == 'repaired_gap': value['stream_coverage_repaired'] = True
    elif damage == 'nested_hash': value['account_report_sha256'] = '0'*64
    elif damage == 'source': value['source_commit'] = 'd'*40
    elif damage == 'future': now -= 1
    elif damage == 'stale_hash': value['opening_record_sha256'] = 'd'*64
    if damage != 'stale_hash':
        value['result_hash'] = digest({k:v for k,v in value.items() if k != 'result_hash'})
    original = deepcopy(value)
    kwargs = dict(source_commit='c'*40, feedback_sha256=value['result_hash'], now=now, cycle=cycle)
    if damage:
        with pytest.raises(ValueError): publisher.validate_bar_export(value, **kwargs)
    else:
        assert publisher.validate_bar_export(value, **kwargs) == original
    assert value == original


@pytest.mark.parametrize('version', [1, 2])
@pytest.mark.parametrize('error', [None, errno.ENOSPC])
@pytest.mark.parametrize('wrong_cycle', [False, True])
def test_real_isolated_cli_publishes_or_classifies_v2_storage_failure(tmp_path, version, error, wrong_cycle):
    value = document(tmp_path) if version == 2 else reported(tmp_path)
    executor, research = tmp_path/'executor', tmp_path/'research'
    cycle = 'd'*64 if wrong_cycle else CYCLE
    directory = executor/'strategy-bar-feedback'/cycle/'research-feedback'
    directory.mkdir(parents=True)
    artifact = digest(value)
    original = canonical(value)+b'\n'
    (directory/(artifact+'.json')).write_bytes(original)
    for path in (executor, *executor.rglob('*')):
        path.chmod(0o700 if path.is_dir() else 0o600)
    settings = dict(executor=str(executor), research=str(research), source='c'*40,
        cycle=cycle, artifact=artifact, feedback=value['result_hash'], phase='publish', error=error,
        now=value['available_at_ms'])
    # Original reported-account fixtures intentionally use a future synthetic day.
    bootstrap = BOOTSTRAP.replace("raise SystemExit(p.main(args))",
        "p.time.time_ns=lambda:settings['now']*1000000\nraise SystemExit(p.main(args))")
    child = subprocess.run([sys.executable, '-B', '-c', bootstrap, json.dumps(settings)],
        cwd=Path(__file__).resolve().parents[2], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False)
    assert child.stderr == b''
    receipt = json.loads(child.stdout)
    if version == 2 and wrong_cycle:
        assert child.returncode == 3
        assert receipt == dict(status='RESEARCH_FEEDBACK_DELIVERY_FAILED', submission_enabled=False)
        assert not research.exists()
        return
    assert child.returncode == (4 if error else 0), receipt
    assert receipt['cycle_sha256'] == cycle and receipt['feedback_sha256'] == value['result_hash']
    assert all(receipt[k] is False for k in ('source_verified','submission_enabled','economic_authority'))
    if error:
        assert receipt['schema'] == 'DEMO_RESEARCH_FEEDBACK_RETRYABLE_FAILURE_V1'
        assert receipt['artifact_sha256'] == artifact
        assert not research.exists()
    else:
        target = research/'data/cache/ai_factory/bar-execution-feedback'/(value['result_hash']+'.json')
        assert target.read_bytes() == original and target.stat().st_nlink == 1


def test_wrong_runtime_rejected_before_privilege_drop_or_destination(tmp_path, monkeypatch):
    from tools import demo_feedback_publish as publisher
    value = document(tmp_path)
    events = []
    monkeypatch.setattr(publisher, 'verify_source', lambda _: None)
    monkeypatch.setattr(publisher.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(publisher, 'configuration', lambda _: dict(executor_uid=os.getuid(),
        research_uid=os.getuid(), research_gid=os.getgid(), research_state_root=str(tmp_path/'research')))
    monkeypatch.setattr(publisher, 'read_export', lambda **kw: value)
    monkeypatch.setattr(publisher.custody, 'drop_identity', lambda *args: events.append('drop'))
    monkeypatch.setattr(publisher, 'publish', lambda *args, **kwargs: events.append('publish'))
    with pytest.raises(ValueError, match='RUNTIME_BINDING'):
        publisher.run(source_commit='c'*40,cycle='d'*64,artifact_sha256=digest(value),
            feedback_sha256=value['result_hash'],bar=True)
    assert events == []


def test_published_v2_research_keeps_provenance_and_never_claims_authority(tmp_path, monkeypatch):
    from tools import demo_feedback_publish as publisher, gold_bar_feedback_research as research
    value = document(tmp_path)
    root = tmp_path/'research'
    publisher.publish(root, value, bar=True)
    monkeypatch.setattr(research, 'runtime_state_root', lambda: root)
    monkeypatch.setattr(research, 'source_identity', lambda: {'commit':'c'*40})
    monkeypatch.setattr(research.time, 'time_ns', lambda: value['available_at_ms']*1000000)
    calls = []
    def model(objective, remaining, history, **kwargs):
        calls.append(objective)
        assert 'REPORTED_FLAT_ROUNDTRIP_WITH_RETAINED_GAPS' in objective
        assert 'opening_record_sha256='+value['opening_record_sha256'] in objective
        assert 'bundle_sha256='+CYCLE in objective
        assert value['account_report']['canonical_wallet'] not in objective
        assert remaining == [0, 1, 2] and history == []
        return {'candidate_id':2, 'model':'fixture'}
    monkeypatch.setattr(research, 'propose', model)
    result = research.run(value['result_hash'])
    assert research.run(value['result_hash']) == result and len(calls) == 1
    assert result['submission_enabled'] is False and result['economic_eligible'] is False
    assert result['experiment_started'] is False
    saved = root/'data/cache/ai_factory/bar-feedback-research'/value['result_hash']/'feedback.json'
    assert json.loads(saved.read_bytes()) == value


def test_native_auto_cash_artifact_validates_and_reaches_research(completed_bar_feedback, tmp_path, monkeypatch):
    from tools import demo_feedback_publish as publisher, gold_bar_feedback_research as research
    f = completed_bar_feedback
    value = f.artifact
    assert value['schema'] == 'GOLD_BAR_REPORTED_ACCOUNT_FEEDBACK_V2'
    assert value['opening_record_sha256'] == digest(f.episode.account_opening)
    assert value['bundle_sha256'] == digest(f.base_runtime)
    assert publisher.validate_bar_export(value, source_commit=f.source_commit,
        feedback_sha256=f.exported['feedback_sha256'], now=f.clock[0], cycle=digest(f.base_runtime)) == value
    assert Path(f.exported['path']).read_bytes() == canonical(value)+b'\n'
    assert f.exported['artifact_sha256'] == digest(value)
    with pytest.raises(ValueError, match='RUNTIME_BINDING'):
        publisher.validate_bar_export(value, source_commit=f.source_commit,
            feedback_sha256=f.exported['feedback_sha256'], now=f.clock[0], cycle='0'*64)
    # The native execution fixture virtualizes executor identity; publication is
    # performed under this temporary research root and the actual local UID.
    monkeypatch.setattr(publisher, 'os', SimpleNamespace(**dict(vars(os), geteuid=os.getuid)))
    research_root = tmp_path/'research-native'
    publisher.publish(research_root, value, bar=True)
    monkeypatch.setattr(research, 'runtime_state_root', lambda:research_root)
    monkeypatch.setattr(research, 'source_identity', lambda:{'commit':f.source_commit})
    calls = []
    def model(objective, *args, **kwargs):
        calls.append(objective)
        assert value['opening_record_sha256'] in objective
        return {'candidate_id':1, 'model':'fixture'}
    monkeypatch.setattr(research, 'propose', model)
    result = research.run(value['result_hash'])
    assert research.run(value['result_hash']) == result and len(calls) == 1
    assert result['economic_eligible'] is False and result['submission_enabled'] is False
