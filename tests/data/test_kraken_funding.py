"""Whole-batch cash constraints, independent of execution or venue fee sizing."""
from copy import deepcopy
from decimal import localcontext
import pytest

from services.data.kraken_account import assess
from services.portfolio.kraken_funding import review
from tests.data.test_kraken_account import observations, NOW


def inputs():
    account = assess(observations(), as_of_ms=NOW+500)
    account['open_orders'] = []
    plan = {'schema': 'KRAKEN_FUNDING_REQUIREMENTS_V1', 'account_ref_sha256': 'a'*64,
            'quantity_basis': 'base', 'cash_reserves': {'ZUSD': '100'},
            'pending_debits': {}, 'orders': [
                {'client_order_id': 'a'*32, 'draft_sha256': 'b'*64,
                 'principal_debits': {'ZUSD': '400'}, 'fee_debits': {'ZUSD': '1'}}]}
    return account, plan


def run(account, plan, now=NOW+600):
    return review(account, plan, as_of_ms=now)


def test_whole_batch_cannot_spend_the_same_cash_twice():
    account, plan = inputs()
    second = deepcopy(plan['orders'][0]); second['client_order_id'] = 'c'*32
    plan['orders'].append(second)
    result = run(account, plan)
    assert result['status'] == 'BLOCKED'
    usd = result['assets']['ZUSD']
    assert usd['new_debits'] == '802'
    assert usd['shortfall'] == '151.99999999'
    assert result['submission_enabled'] is False


def test_locked_cash_reserves_pending_and_fees_all_reduce_owned_cash():
    account, plan = inputs()
    account['balances']['ZUSD'].update(credit='10000', available='10750.00000001')
    plan['pending_debits'] = {'ZUSD': '200'}
    with localcontext() as context:
        context.prec = 6
        result = run(account, plan)
    assert result['status'] == 'CONDITIONAL_BUDGET_FIT'
    assert result['assets']['ZUSD']['remaining'] == '49.00000001'
    assert result['reservation_created'] is False


def test_stock_fees_consume_stock_units_and_no_sale_proceeds_are_credited():
    account, plan = inputs()
    plan['orders'][0].update(principal_debits={'AAPLx.T': '2'}, fee_debits={'AAPLx.T': '0.03'})
    result = run(account, plan)
    assert result['status'] == 'BLOCKED'
    assert result['assets']['AAPLx.T']['shortfall'] == '0.006543210987654322'
    plan['orders'][0]['expected_proceeds'] = {'ZUSD': '9999'}
    with pytest.raises(ValueError): run(account, plan)


@pytest.mark.parametrize('change', ['unknown_fee', 'missing_asset', 'stale', 'open', 'credit_used', 'changed'])
def test_unresolved_inputs_never_fit(change):
    account, plan = inputs(); now = NOW+600
    if change == 'unknown_fee': plan['orders'][0]['fee_debits'] = None
    if change == 'missing_asset': plan['orders'][0]['fee_debits'] = {'UNKNOWN': '0.1'}
    if change == 'stale': now += 120001
    if change == 'open': account['open_orders'] = [{'order_id': 'unresolved'}]
    if change == 'credit_used': account['balances']['ZUSD']['credit_used'] = '1'
    if change == 'changed': account['status'] = 'UNRESOLVED'
    assert run(account, plan, now)['status'] == 'BLOCKED'


@pytest.mark.parametrize('change', ['duplicate', 'negative', 'float', 'basis', 'account', 'empty_fees'])
def test_malformed_requirements_rejected(change):
    account, plan = inputs()
    if change == 'duplicate': plan['orders'].append(deepcopy(plan['orders'][0]))
    if change == 'negative': plan['pending_debits'] = {'ZUSD': '-1'}
    if change == 'float': plan['orders'][0]['fee_debits'] = {'ZUSD': 0.1}
    if change == 'basis': plan['quantity_basis'] = 'rebased'
    if change == 'account': plan['account_ref_sha256'] = 'b'*64
    if change == 'empty_fees': plan['orders'][0]['fee_debits'] = {}
    with pytest.raises(ValueError): run(account, plan)


def test_negative_available_in_an_unspent_asset_blocks_the_account():
    account, plan = inputs()
    account['balances']['AAPLx.T']['available'] = '-0.1'
    assert run(account, plan)['status'] == 'BLOCKED'


def test_fresh_receipt_does_not_hide_stale_start_of_account_sweep():
    account, plan = inputs()
    assert run(account, plan, NOW+120100)['status'] == 'BLOCKED'


def test_exact_boundary_is_fit_but_one_fraction_more_is_not():
    account, plan = inputs()
    plan['orders'][0]['principal_debits'] = {'ZUSD': '649.00000001'}
    assert run(account, plan)['status'] == 'CONDITIONAL_BUDGET_FIT'
    plan['orders'][0]['fee_debits']['ZUSD'] = '1.000000000000000001'
    result = run(account, plan)
    assert result['status'] == 'BLOCKED'
    assert result['assets']['ZUSD']['shortfall'] == '0.000000000000000001'
