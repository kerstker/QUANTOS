"""Legacy reader's exact terminal-tail exception; executor proof is isolated.

The original enrollment and file readers run. A validated closure view is an
explicit boundary here; native/legacy executor tests verify that proof itself.
"""
from copy import deepcopy

import pytest

from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _retain
from tools.gold_pilot_retention_store import canonical
from tests.tools.test_gold_feedback_legacy import (
    local as local, enrolled as enrolled, frame, delivered, read, files,
)


def terminal(local, enrolled, monkeypatch, stage):
    from tools import demo_operating_bundle as installer, gold_pilot_closure as closure
    local.update(executor_root=enrolled.root, account=enrolled.account)
    monkeypatch.setattr(installer, 'EXECUTOR', enrolled.root)
    original = frame(local, pilot=enrolled)
    receipt = delivered(local, original)
    decision = dict(original['decision'], status='PILOT_STOPPED', closure_of_result_sha256=_digest(original))
    result = {k: deepcopy(v) for k, v in original.items() if k != 'feedback'}
    result.update(decision=decision, decision_sha256=_digest(decision))
    request = dict(schema=closure.SCHEMA, runtime=local['current_runtime'], original_result=original,
        original_result_sha256=_digest(original), original_decision_sha256=original['decision_sha256'])
    intent = dict(request=request, observation_claim=None, original_claim=None, original_checkpoint=None)
    area = enrolled.root/'gold-pilot'
    (area/'closure-intent.json').write_bytes(canonical(intent))
    if stage != 'intent':
        (area/'closure-origin.json').write_bytes(canonical({'isolated_origin': True}))
    if stage in ('decision', 'complete', 'mirrored'):
        _retain(enrolled.root/'strategy-decisions', decision)
    if stage in ('complete', 'mirrored'):
        (area/'closure-result.json').write_bytes(canonical(result))
    if stage == 'mirrored':
        _retain(local['root']/'strategy-decisions', result)
    for name in ('closure-intent.json', 'closure-origin.json', 'closure-result.json'):
        path = area/name
        if path.exists():path.chmod(0o600)
    view = dict(intent=intent, intent_sha256=_digest(intent), claim=None,
        status='INTENT_ONLY' if stage == 'intent' else 'COMPLETE' if stage in ('complete', 'mirrored') else 'PENDING',
        decision=decision if stage in ('decision', 'complete', 'mirrored') else None,
        result=result if stage in ('complete', 'mirrored') else None)
    def verified(root, **kwargs):
        assert root == enrolled.root and kwargs['retention'] is None
        assert kwargs['runtime'] == local['current_runtime'] and kwargs['account'] == enrolled.account
        return deepcopy(view)
    monkeypatch.setattr(closure, 'read_intent', verified)
    return original, receipt, view


@pytest.mark.parametrize('stage', ['intent', 'origin', 'decision', 'complete', 'mirrored'])
def test_only_verified_terminal_tail_is_read_without_inventing_legacy_mirror(local, enrolled, monkeypatch, stage):
    original, receipt, view = terminal(local, enrolled, monkeypatch, stage)
    before = files(local)
    found = read(local)
    assert found['result'] == original and found['delivery'] == receipt
    assert found['closure'] == {k: view[k] for k in ('intent_sha256', 'status', 'claim', 'result')}
    assert files(local) == before


@pytest.mark.parametrize('damage', ['proof', 'other_orphan', 'original', 'fixed_result', 'missing_intent', 'missing_decision'])
def test_terminal_exception_never_hides_unverified_originals_or_other_orphans(local, enrolled, monkeypatch, damage):
    from tools import gold_pilot_closure as closure
    original, _, view = terminal(local, enrolled, monkeypatch, 'complete')
    if damage == 'proof':
        monkeypatch.setattr(closure, 'read_intent', lambda *a, **kw: (_ for _ in ()).throw(ValueError('UNVERIFIED_INTENT')))
    elif damage == 'other_orphan':
        _retain(enrolled.root/'strategy-decisions', dict(original['decision'], reason='OTHER_OLD_DECISION'))
    elif damage == 'original':
        (local['root']/'strategy-decisions'/(_digest(original)+'.json')).unlink()
    elif damage == 'fixed_result':
        (enrolled.root/'gold-pilot/closure-result.json').write_bytes(canonical({'wrong': True}))
    elif damage == 'missing_intent':
        (enrolled.root/'gold-pilot/closure-intent.json').unlink()
    else:
        (enrolled.root/'strategy-decisions'/(_digest(view['decision'])+'.json')).unlink()
    with pytest.raises(ValueError):read(local)
