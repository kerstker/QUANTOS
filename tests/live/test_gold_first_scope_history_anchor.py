"""Native old BTC signatures and unsigned charges must survive Scope admission."""
from contextlib import closing
from copy import deepcopy

import pytest

from live.execution.demo_gold_first_scope_history import history_anchor, validate_anchor, verify_prefix
from live.execution.demo_issuer_store import IssuerStore
from tests.live.test_gold_first_scope_original_history import (
    build_original_btc_history, original_btc_history as original_btc_history,
)


def test_first_scope_anchor_preserves_the_actual_signed_btc_prefix(original_btc_history):
    f = original_btc_history
    anchor = history_anchor(f['issuer_prefix'])
    with closing(IssuerStore(f['issuer_path'], read_only=True)) as original:
        assert verify_prefix(anchor, original.snapshot()) == anchor
    assert anchor['records'] == 2
    with pytest.raises(ValueError, match='PREFIX_MISSING'):
        verify_prefix(anchor, [])


def test_initial_group_reducer_rejects_a_replaced_empty_issuer(original_btc_history):
    """Exercise the reducer after upstream certificate validation, before append."""
    from live.execution.demo_issuer_group_continuity import _anchor, _group_transition
    rows = original_btc_history['issuer_prefix']
    previous = dict(schema='DEMO_GOLD_STRATEGY_BUDGET_SCOPE_V2',
        budget_group={'group_id':'first-native-group'},
        scope_enrollment={'original_issuer_history':history_anchor(rows)})
    template = dict(budget_group=dict(group_id='first-native-group', max_grants=3,
        max_orders=3, max_total_notional='750', exit_reserve=dict(grants=1, orders=1, notional='250')))
    # Even a newly self-consistent candidate for a replacement empty DB cannot
    # discard the earlier independently signed Scope's original issuer prefix.
    certificate = dict(previous_authorization=previous, future_template=template,
        operation={'subject':dict(action='INIT_SCOPE', from_group_binding_sha256='a'*64,
            expected_head=_anchor([], previous, {}))})
    with pytest.raises(ValueError, match='FIRST_SCOPE_ISSUER_PREFIX_MISSING'):
        _group_transition({}, certificate, [])
    certificate['operation']['subject']['expected_head'] = _anchor(rows, previous, {})
    state = _group_transition({}, certificate, rows)
    assert state['submission'][:2] == [0, 0]


def test_first_scope_anchor_keeps_unsigned_btc_budget_as_well(tmp_path, monkeypatch):
    f = build_original_btc_history(tmp_path, monkeypatch, include_unsigned=True)
    try:
        rows = f['issuer_prefix']
        anchor = history_anchor(rows)
        assert anchor['records'] == 3 and rows[-1]['kind'] == 'RESERVED'
        with pytest.raises(ValueError, match='PREFIX_MISSING'):
            verify_prefix(anchor, rows[:-1])
        assert verify_prefix(history_anchor(rows[:-1]), rows)['records'] == 2
        changed = deepcopy(rows)
        changed[-1]['payload']['plan']['notional'] = '0'
        with pytest.raises(ValueError, match='PREFIX_CHAIN'):
            verify_prefix(anchor, changed)
    finally:
        f['close']()


@pytest.mark.parametrize('count', [True, 0, 1, 20001])
def test_first_scope_anchor_cannot_describe_an_empty_or_unknown_original(count):
    with pytest.raises(ValueError):
        validate_anchor(dict(schema='DEMO_GOLD_FIRST_SCOPE_ISSUER_PREFIX_V1', records=count,
            head_sha256='a'*64, history_sha256='b'*64))
