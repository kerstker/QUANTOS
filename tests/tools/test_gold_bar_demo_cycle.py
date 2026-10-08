from types import SimpleNamespace
from copy import deepcopy

import pytest

from tests.tools.test_gold_bar_demo_entry import prepared, pilot_state as pilot_state
from tests.tools.test_gold_bar_demo_exit import case as case
from tests.tools.test_gold_strategy_entry import origin_at


@pytest.fixture(autouse=True)
def local_partition_records(tmp_path, monkeypatch):
    from live.execution import demo_market_partition_runtime as runtime
    monkeypatch.setattr(runtime, 'EXECUTOR_RECORD', tmp_path/'partition-executor.json')
    monkeypatch.setattr(runtime, 'ISSUER_RECORD', tmp_path/'partition-issuer.json')
    monkeypatch.setattr(runtime, 'ACTIVATION', tmp_path/'partition-activation.json')


def stores(*, pending=False):
    return dict(dispatch=SimpleNamespace(snapshot=lambda: [], wire_snapshot=lambda: []),
        recovery=SimpleNamespace(latest_records=lambda: [SimpleNamespace(state=SimpleNamespace(value='UNKNOWN'))] if pending else []),
        oms=SimpleNamespace(orders=lambda: []))


@pytest.mark.parametrize('side,action', [('BUY', 'OPEN_LONG'), ('SELL', 'OPEN_SHORT')])
def test_fresh_flat_cycle_reaches_exact_approval_request_only(tmp_path, side, action):
    from tools.gold_bar_demo_cycle import prepare
    engine, _, at = prepared(tmp_path, side)
    result = prepare(engine=engine, entry_decision_ms=at-1, origin=origin_at(at), now_ms=at, **stores())
    assert result['status'] == 'DECISION_REQUIRES_APPROVAL'
    assert result['action'] == action
    assert result['execution_constraints']['decision_sha256'] == result['draft']['result_hash']
    assert result['submission_enabled'] is False


def test_unknown_send_blocks_before_any_new_draft(tmp_path):
    from tools.gold_bar_demo_cycle import prepare
    engine, _, at = prepared(tmp_path, 'BUY')
    result = prepare(engine=engine, entry_decision_ms=at-1, origin=origin_at(at), now_ms=at,
                     **stores(pending=True))
    assert result['status'] == 'RECOVERY_REQUIRED'
    assert result['draft'] is None


def test_owned_short_reduces_then_settles_without_reopening(case, monkeypatch):
    from tools import gold_bar_demo_cycle as cycle
    exit_tool, args, context = case('SELL')
    monkeypatch.setattr(cycle, '_state', exit_tool._state)
    # This focused episode fixture substitutes its small context, while the
    # native roundtrip suite exercises the resolver and original V2 replay.
    monkeypatch.setattr(cycle.position, '_load_context_unpartitioned',
        lambda *a, **kw: deepcopy(context))
    result = cycle.prepare(**args)
    assert result['status'] == 'DECISION_REQUIRES_APPROVAL'
    assert result['action'] == 'REDUCE_SHORT'
    context['quantity'] = '0'
    context['position_episode'] = None
    result = cycle.prepare(**args)
    assert result['status'] == 'STRATEGY_EPISODE_SETTLED'
    assert result['draft'] is None
