"""First Scope binds native old executor reservations to its actual issuer."""
from copy import deepcopy

import pytest

from tests.live.test_gold_first_scope_original_history import original_btc_history as original_btc_history


def test_native_issuer_signed_history_explains_original_executor(original_btc_history):
    from tools import gold_first_scope_enrollment as tool
    value = original_btc_history
    tool.verify_executor_issuer_history(value['issuer_prefix'], state=value['executor_state'],
        account_ref=value['old_policy']['account_ref_sha256'])


@pytest.mark.parametrize('damage', ['empty', 'missing_signature', 'wrong_approval', 'context', 'orders', 'account'])
def test_unexplained_original_execution_is_not_a_first_scope(original_btc_history, damage):
    from tools import gold_first_scope_enrollment as tool
    value = original_btc_history
    rows, state = deepcopy(value['issuer_prefix']), deepcopy(value['executor_state'])
    if damage == 'empty': rows = []
    elif damage == 'missing_signature': rows = [r for r in rows if r['kind'] != 'SIGNED']
    elif damage == 'wrong_approval': state['reservations'][0]['approval_id'] = 'unrelated-original'
    elif damage == 'context': state['reservations'][0]['payload']['context']['source_commit'] = 'f' * 40
    elif damage == 'orders': state['reservations'][0]['payload']['orders'][0]['quantity'] = '0.004'
    else: state['reservations'][0]['account_ref_sha256'] = 'f' * 64
    with pytest.raises(ValueError, match='FIRST_SCOPE_ISSUER_'):
        tool.verify_executor_issuer_history(rows, state=state,
            account_ref=value['old_policy']['account_ref_sha256'])


def test_unsigned_original_issuer_reservation_remains_part_of_retained_history(tmp_path, monkeypatch):
    from tests.live.test_gold_first_scope_original_history import build_original_btc_history
    from tools import gold_first_scope_enrollment as tool
    value = build_original_btc_history(tmp_path, monkeypatch, include_unsigned=True)
    try:
        before = deepcopy(value['issuer_prefix'])
        tool.verify_executor_issuer_history(before, state=value['executor_state'],
            account_ref=value['old_policy']['account_ref_sha256'])
        assert before == value['issuer_store'].snapshot()
        assert [row['kind'] for row in before] == ['RESERVED', 'SIGNED', 'RESERVED']
    finally:
        value['close']()
