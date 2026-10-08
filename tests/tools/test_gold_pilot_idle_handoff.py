"""Only the exact executor classification can resume normal observation.

Worker transport and executor proof are explicitly isolated here. Actual
worker/capture/state boundaries are exercised by the closure integration tests.
"""
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_pilot_retention import enrolled as enrolled, bundle, packet, NOW, SOURCE


@pytest.mark.parametrize('damage', [None, 'source', 'bundle', 'request', 'window', 'reason', 'extra', 'bool', 'status'])
def test_only_exact_not_applicable_can_resume_existing_observation(enrolled, monkeypatch, damage):
    from tools import gold_pilot_closure as closure, gold_pilot_closure_controller as terminal
    from tools import gold_strategy_controller as controller
    from tools.gold_pilot_retention_store import canonical
    f = enrolled
    window = packet(f, NOW)['original_pilot_window']
    req = closure.idle_request(bundle(), window)
    value = dict(schema='GOLD_PILOT_IDLE_CLOSURE_RESULT_V1', status='NOT_APPLICABLE',
        reason='EXECUTION_SINCE_ENROLLMENT', source_commit=SOURCE, bundle_sha256=_digest(bundle()),
        request_sha256=_digest(req), original_pilot_window=deepcopy(window),
        submission_enabled=False, economic_authority=False)
    if damage == 'source': value['source_commit'] = 'f'*40
    elif damage == 'bundle': value['bundle_sha256'] = 'f'*64
    elif damage == 'request': value['request_sha256'] = 'f'*64
    elif damage == 'window': value['original_pilot_window']['ends_at_ms'] += 1
    elif damage == 'reason': value['reason'] = 'UNKNOWN'
    elif damage == 'extra': value['may_continue'] = True
    elif damage == 'bool': value['submission_enabled'] = 0
    elif damage == 'status': value['status'] = 'INPUTS_UNRESOLVED'
    sent, finished, released = [], [], []
    process = SimpleNamespace(stdin=BytesIO(), stdout=SimpleNamespace(fileno=lambda: 77, close=lambda: None),
        poll=lambda: 0)
    monkeypatch.setattr(controller.demo_task, 'preparation_command',
        lambda **kw: ['tools.demo_request_prepare', 'RuntimeMaxSec=155s'])
    monkeypatch.setattr(terminal.subprocess, 'Popen', lambda *a, **kw: process)
    monkeypatch.setattr(terminal.subprocess, 'run', lambda *a, **kw: None)
    monkeypatch.setattr(controller.supervisor, '_send', lambda channel, raw, deadline: sent.append(raw))
    monkeypatch.setattr(controller.supervisor, 'read_frame', lambda *a, **kw: canonical(value))
    monkeypatch.setattr(controller.supervisor, '_finish', lambda *a: finished.append(True))
    owner = SimpleNamespace(release=lambda: released.append(True), inspect_reobservation=lambda **kw: None)
    args = dict(source_commit=SOURCE, registry={}, policy={'account_ref_sha256': f.account},
        owner=owner, current_runtime=bundle(), original_window=window)
    if damage is None:
        assert terminal.finish_idle(**args) is None
        assert finished == [True]
    else:
        with pytest.raises(ValueError): terminal.finish_idle(**args)
        assert finished == []
    assert len(sent) == 1 and len(released) == 2
