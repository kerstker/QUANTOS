import hashlib
import json

import pytest

from tests.tools.test_gold_bar_cost_replay import inputs
from tools import gold_bar_experiment as experiment
from tools.gold_bar_review import review_request

NOW = 1700000000000


def configured(tmp_path, monkeypatch):
    from tools import gold_bar_receipt as tool
    from tests.governance import test_artifact_receipt_auth as fixture
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    args = inputs()
    monkeypatch.setattr(experiment, 'source_identity', lambda: args['release_identity'])
    dest = tmp_path / 'data/cache/gold_bar_research/results/1'
    dest.parent.mkdir(parents=True)
    experiment.archive(args, dest)
    request = review_request(dest)
    digest = hashlib.sha256((dest / 'independent-request.json').read_bytes()).hexdigest()
    receipt = fixture._receipt().model_copy(update={'artifact_sha256': digest})
    monkeypatch.setattr(fixture, '_receipt', lambda: receipt)
    envelope, key = fixture._signed_envelope()
    registry = tmp_path / 'public-registry.json'
    registry.write_text(json.dumps({'schema_version': 2, 'keys': {'fixture': key.model_dump(mode='json')}}))
    monkeypatch.setattr(tool, 'REGISTRY', registry)
    (dest / 'independent-envelope.json').write_text(envelope.model_dump_json())
    # Later receipt processing must bind the archive, not rerun old code under
    # a new source identity or alter a terminal trial.
    monkeypatch.setattr(experiment, 'source_identity', lambda: {'different_release': True})
    monkeypatch.setattr(experiment, '_execute', lambda args: pytest.fail('replayed terminal experiment'))
    return tool, dest, registry, request


def test_late_bar_receipt_uses_original_queue_and_preserves_three_trials(tmp_path, monkeypatch):
    from services.research.run_registry.store import ResearchRunStore
    from services.research.run_registry.registry import RunRegistry
    from services.research_runner.runner import JobQueue
    tool, dest, _, request = configured(tmp_path, monkeypatch)
    names = ('input.json', 'report.json', 'manifest.json', 'trials.db', 'independent-request.json')
    original = {name: (dest / name).read_bytes() for name in names}
    monkeypatch.setattr(tool.time, 'time_ns', lambda: NOW * 1000000)
    registry = RunRegistry(ResearchRunStore(str(tmp_path / 'review-runs.db')))
    queue = JobQueue(registry)
    uid = queue.submit('gold_bar_receipt', {'attempt': 1}, NOW // 1000)
    queue.join()
    run = registry.get(uid)
    assert run.status == 'done', run.error
    assert run.metrics['verification_status'] == 'VERIFIED_SIGNATURE_ENGINEERING_ONLY'
    assert run.metrics['trial_count'] == len(request['trial_ids']) == 3
    assert run.verdict == 'TECHNICAL_VALIDATION_ONLY'
    expired = tool.check_receipt(dest, now_ms=NOW + 86400001)
    assert expired['verification']['status'] == 'REJECTED'
    assert expired['economic_eligible'] is expired['authoritative'] is False
    assert len(list((dest / 'receipt-checks').glob('*.json'))) == 2
    assert all((dest / name).read_bytes() == raw for name, raw in original.items())


@pytest.mark.parametrize('change', ['missing-authority', 'revoked', 'wrong-artifact', 'changed-input'])
def test_bar_receipt_rejects_missing_trust_or_changed_bindings(tmp_path, monkeypatch, change):
    tool, dest, registry, _ = configured(tmp_path, monkeypatch)
    if change == 'changed-input':
        (dest / 'input.json').write_text('{}')
        with pytest.raises(ValueError):
            tool.check_receipt(dest, now_ms=NOW)
        assert not (dest / 'receipt-checks').exists()
        return
    if change == 'wrong-artifact':
        p = dest / 'independent-envelope.json'
        data = json.loads(p.read_bytes())
        data['receipt']['artifact_sha256'] = 'e' * 64
        p.write_text(json.dumps(data))
    else:
        data = json.loads(registry.read_bytes())
        if change == 'missing-authority':
            data['keys'] = {}
        else:
            data['keys']['fixture']['status'] = 'REVOKED'
        registry.write_text(json.dumps(data))
    result = tool.check_receipt(dest, now_ms=NOW)
    assert result['verification']['status'] == (
        'INDEPENDENT_AUTHORITY_UNCONFIGURED' if change == 'missing-authority' else 'REJECTED')
    assert result['economic_eligible'] is False
