import hashlib
import json

import pytest

from tests.research.test_gold_cost_review import completed
from tools import gold_cost_receipt as tool

NOW = 1700000000000


def configured(tmp_path, monkeypatch):
    from tests.governance import test_artifact_receipt_auth as fixture
    dest = completed(tmp_path, monkeypatch)
    digest = hashlib.sha256((dest/'independent-request.json').read_bytes()).hexdigest()
    receipt = fixture._receipt().model_copy(update={'artifact_sha256': digest})
    monkeypatch.setattr(fixture, '_receipt', lambda: receipt)
    envelope, key = fixture._signed_envelope()
    registry = tmp_path/'public-registry.json'
    registry.write_text(json.dumps({'schema_version': 2, 'keys': {'test': key.model_dump(mode='json')}}))
    monkeypatch.setattr(tool, 'REGISTRY', registry)
    (dest/'independent-envelope.json').write_text(envelope.model_dump_json())
    return dest, registry


def test_registered_signature_is_engineering_only_and_check_is_retained(tmp_path, monkeypatch):
    dest, _ = configured(tmp_path, monkeypatch)
    result = tool.check_receipt(dest, now_ms=NOW)
    assert result['verification']['status'] == 'VERIFIED_SIGNATURE_ENGINEERING_ONLY'
    assert result['economic_eligible'] is result['authoritative'] is False
    assert list((dest/'receipt-checks').glob('*.json'))


def test_missing_registered_authority_does_not_accept_envelope_key(tmp_path, monkeypatch):
    dest, registry = configured(tmp_path, monkeypatch)
    registry.write_text(json.dumps({'schema_version': 2, 'keys': {}}))
    result = tool.check_receipt(dest, now_ms=NOW)
    assert result['verification']['status'] == 'INDEPENDENT_AUTHORITY_UNCONFIGURED'


@pytest.mark.parametrize('change', ['expired', 'revoked', 'wrong-artifact', 'duplicate-key'])
def test_invalid_receipts_and_ambiguous_registry_never_admit(tmp_path, monkeypatch, change):
    dest, registry = configured(tmp_path, monkeypatch)
    now = NOW
    if change == 'expired':
        now += 86400001
    elif change == 'wrong-artifact':
        path = dest/'independent-envelope.json'
        envelope = json.loads(path.read_bytes())
        envelope['receipt']['artifact_sha256'] = 'e'*64
        path.write_text(json.dumps(envelope))
    else:
        document = json.loads(registry.read_bytes())
        if change == 'revoked':
            document['keys']['test']['status'] = 'REVOKED'
        else:
            document['keys']['duplicate'] = document['keys']['test']
        registry.write_text(json.dumps(document))
    result = tool.check_receipt(dest, now_ms=now)
    assert result['verification']['status'] == 'REJECTED'
    assert result['economic_eligible'] is False


def test_queue_rechecks_late_receipt_without_replaying_or_rewriting_trial(tmp_path, monkeypatch):
    from services.research.run_registry.store import ResearchRunStore
    from services.research.run_registry.registry import RunRegistry
    from services.research_runner.runner import JobQueue
    from tools import gold_cost_research
    dest, _ = configured(tmp_path, monkeypatch)
    original = {name: (dest/name).read_bytes() for name in
                ('input.json', 'report.json', 'manifest.json', 'trials.db', 'independent-request.json')}
    monkeypatch.setattr(tool.time, 'time_ns', lambda: NOW * 1000000)
    def forbidden(**kwargs):
        pytest.fail('receipt recheck must not rerun the experiment')
    monkeypatch.setattr(gold_cost_research, 'replay_costs', forbidden)
    registry = RunRegistry(ResearchRunStore(str(tmp_path/'receipt-runs.db')))
    queue = JobQueue(registry)
    uid = queue.submit('gold_cost_receipt', {'attempt': 1}, NOW, invocation_id='receipt-one')
    queue.join()
    run = registry.get(uid)
    assert run.status == 'done', run.error
    assert run.metrics['verification_status'] == 'VERIFIED_SIGNATURE_ENGINEERING_ONLY'
    assert run.verdict == 'TECHNICAL_VALIDATION_ONLY'
    monkeypatch.setattr(tool.time, 'time_ns', lambda: (NOW + 86400001) * 1000000)
    second = queue.submit('gold_cost_receipt', {'attempt': 1}, NOW+86400001,
                          invocation_id='receipt-expired')
    queue.join()
    assert registry.get(second).metrics['verification_status'] == 'REJECTED'
    assert registry.get(uid).metrics['verification_status'] == 'VERIFIED_SIGNATURE_ENGINEERING_ONLY'
    assert all((dest/name).read_bytes() == raw for name, raw in original.items())


@pytest.mark.parametrize('params', [{}, {'attempt': True}, {'attempt': 0},
                                  {'attempt': 1001}, {'attempt': 1, 'path': '/tmp/x'}])
def test_receipt_job_rejects_unbounded_parameters(params):
    with pytest.raises(ValueError):
        tool.run_check(params)
