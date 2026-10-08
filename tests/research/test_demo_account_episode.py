"""Episode cash identity must not imply observed daily equity or new risk capital."""
from copy import deepcopy

import pytest

from services.research.demo_account_recovery import reconcile_episode
from tests.research.test_demo_account_days import later_close
from tests.research.test_gold_reported_short import short_inputs


@pytest.mark.parametrize('damage', [None, 'window', 'wallet', 'second_sweep', 'missing_fee'])
def test_episode_keeps_original_cash_checks_and_initial_equity(tmp_path, damage):
    value = later_close(short_inputs(tmp_path), days=7 if damage == 'window' else 2)
    if damage == 'wallet':
        for read in value['closing']['reads']:
            if read['kind'].startswith('account'):
                for asset in read['data']['assets']:
                    if asset['asset'] == 'USDT':
                        asset['walletBalance'] = '5000'
    elif damage == 'second_sweep':
        value['statements'][1] = deepcopy(value['statements'][0])
    elif damage == 'missing_fee':
        for statement in value['statements']:
            statement['income'] = [r for r in statement['income'] if r['incomeType'] != 'COMMISSION']
            for read in statement['reads']:
                if read['kind'] == 'income':
                    read['data'] = deepcopy(statement['income'])
    original = deepcopy(value)
    report, ledger = reconcile_episode(**value)
    if damage:
        assert report['status'] == 'UNRESOLVED' and ledger is None
    else:
        assert report['canonical_wallet'] == '5000.0801'
        assert ledger.reconciliation_snapshot().as_canonical_dict()['initial_equity'] == '5000'
        assert report['daily_valuation_status'] == 'NOT_RECONSTRUCTED'
        assert report['risk_budget_reset'] is False
        assert 'daily_cash' not in report
    assert value == original
