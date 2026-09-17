"""Cross-day accounting must retain the original ledger and execution block."""
from copy import deepcopy
from decimal import Decimal
import json

import pytest

from services.research import demo_account_recovery as recovery
from tests.research.test_demo_account_recovery import inputs
from tests.tools.test_binance_demo_execution_origin import NOW
from tests.research.test_binance_order_receipts import receipt

DAY = 86_400_000


def later_close(value, days=1):
    # The round trip remains on day zero; the closing and two complete REST
    # observations are genuinely later, with matching request coverage.
    def shift(item):
        if type(item) is int and item >= NOW + 30000:
            return item + days * DAY
        if isinstance(item, dict):
            return {k: shift(v) for k, v in item.items()}
        if isinstance(item, list):
            return [shift(v) for v in item]
        return item
    result = shift(value)
    result['opening'] = deepcopy(value['opening'])
    return result


def test_cross_day_replay_preserves_losses_fees_capacity_and_empty_days(tmp_path):
    value = later_close(inputs(tmp_path), days=3)
    original = deepcopy(value)
    report, ledger = recovery.reconcile_days(**value)
    assert report['status'] == 'REPORTED_ACCOUNT_MATCHED', report['issues']
    assert report['schema'] == 'DEMO_REPORTED_ACCOUNT_DAYS_V1'
    assert report['canonical_wallet'] == '4999.6801'
    days = report['daily_cash']
    assert len(days) == 4 and days[0]['net_after_costs'] == '-0.3199'
    assert [d['net_after_costs'] for d in days[1:]] == ['0', '0', '0']
    assert all(d['opening_wallet'] == '4999.6801' for d in days[1:])
    assert sum(Decimal(d['fee']) for d in days) == Decimal('0.1199')
    assert sum(map(Decimal, report['ledger_state']['capacity_consumed'].values())) == Decimal('239.8')
    assert ledger.reconciliation_snapshot().as_canonical_dict()['initial_equity'] == '5000'
    assert not report['risk_budget_reset'] and not report['submission_enabled']
    assert not report['automatic_cycle_completed'] and not report['stream_coverage_repaired']
    assert value == original
    # Existing protected recovery/continuation still uses the reviewed one-day
    # function. A diagnostic day report is never a new execution permission.
    old, old_ledger = recovery.reconcile(**value)
    assert old['status'] == 'UNRESOLVED' and old_ledger is None


@pytest.mark.parametrize('damage', ['window', 'wallet', 'sweep', 'fee', 'origin_boundary'])
def test_cross_day_does_not_fill_missing_evidence_or_reset_wallet(tmp_path, damage):
    value = later_close(inputs(tmp_path), days=7 if damage == 'window' else 1)
    if damage == 'wallet':
        for read in value['closing']['reads']:
            if read['kind'].startswith('account'):
                for asset in read['data']['assets']:
                    if asset['asset'] == 'USDC':
                        for key in ('walletBalance', 'crossWalletBalance', 'availableBalance', 'marginBalance'):
                            asset[key] = '5000'
    elif damage == 'sweep':
        value['statements'][1] = deepcopy(value['statements'][0])
    elif damage == 'fee':
        for statement in value['statements']:
            statement['income'].pop()
    elif damage == 'origin_boundary':
        value['opening']['observed_at_ms'] = (NOW // DAY) * DAY - 1
    try:
        report, ledger = recovery.reconcile_days(**value)
    except ValueError:
        return
    assert report['status'] == 'UNRESOLVED' and ledger is None
    assert report['daily_cash'] == []


def test_midnight_position_requires_valuation_instead_of_fabricating_daily_equity(tmp_path):
    value = later_close(inputs(tmp_path))
    for statement in value['statements']:
        for trade in statement['trades']:
            if trade['id'] == '2': trade['time'] += DAY
        for flow in statement['income']:
            if flow['tradeId'] == '2': flow['time'] += DAY
        for read in statement['reads']:
            if read['kind'] == 'trades': read['data'] = deepcopy(statement['trades'])
            if read['kind'] == 'income': read['data'] = deepcopy(statement['income'])
    window = value['windows'][1]
    window['start_ms'] += DAY; window['end_ms'] += DAY
    message = json.loads(window['messages'][0]['raw_utf8'])
    message['E'] += DAY; message['T'] += DAY; message['o']['T'] += DAY
    window['messages'] = [receipt(message, NOW + 20005 + DAY)]
    for gap in window['gaps']: gap['at_ms'] += DAY
    report, ledger = recovery.reconcile_days(**value)
    assert report['issues'] == ['ACCOUNT_DAYS_MIDNIGHT_POSITION_UNVALUED']
    assert ledger is None and report['daily_cash'] == []


def test_daily_cash_flows_are_not_trading_profit_or_new_initial_collateral(tmp_path):
    value = later_close(inputs(tmp_path), days=2)
    for statement in value['statements']:
        statement['income'].extend([
            dict(symbol='', incomeType='TRANSFER', income='1', asset='USDC',
                 time=NOW+30000, tranId='40', tradeId=''),
            dict(symbol='', incomeType='TRANSFER', income='-1', asset='USDC',
                 time=NOW+DAY+30000, tranId='41', tradeId=''),
        ])
        for read in statement['reads']:
            if read['kind'] == 'income': read['data'] = deepcopy(statement['income'])
    report, ledger = recovery.reconcile_days(**value)
    assert report['status'] == 'REPORTED_ACCOUNT_MATCHED', report['issues']
    assert [d['external_cash_flow'] for d in report['daily_cash']] == ['1', '-1', '0']
    assert [d['net_after_costs'] for d in report['daily_cash']] == ['-0.3199', '0', '0']
    assert report['daily_cash'][0]['closing_wallet'] == '5000.6801'
    assert report['daily_cash'][1]['opening_wallet'] == '5000.6801'
    assert [d['complete_utc_day'] for d in report['daily_cash']] == [False, True, False]
    assert ledger.reconciliation_snapshot().initial_equity == Decimal('5000')


def test_day_view_and_original_same_day_replay_have_identical_ledger(tmp_path):
    value = inputs(tmp_path)
    original, old = recovery.reconcile(**value)
    daily, new = recovery.reconcile_days(**value)
    assert daily['ledger_state'] == original['ledger_state']
    assert old.state_as_canonical_dict() == new.state_as_canonical_dict()
    assert daily['daily_cash'][0]['net_after_costs'] == original['net_after_fees']
