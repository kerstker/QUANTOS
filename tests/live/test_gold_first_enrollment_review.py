"""First Gold review preserves the original book and requires protected seals."""
from copy import deepcopy
from decimal import Decimal

import pytest

from live.execution import demo_authority
from live.execution import demo_market_partition_runtime as runtime
from tests.live.test_demo_market_partition import append_target, NOW, book as book
from tests.live.test_demo_market_partition_runtime import installed as installed


@pytest.fixture
def first(installed, monkeypatch):
    value = installed
    value['registered'] = deepcopy(value['registry'])
    value['registry'] = deepcopy(value['legacy'])
    monkeypatch.setattr(demo_authority, 'load_registry_for_executor', lambda: deepcopy(value['registry']))
    return value


def test_first_gold_review_has_no_fabricated_target_context_or_permissions(first):
    before = deepcopy(first['state']), first['events'].row_count()
    report = runtime.first_gold_enrollment_review(state=first['state'], origin=first['origin'],
        as_of_ms=NOW + 140000)
    assert report['status'] == 'FIRST_GOLD_ENROLLMENT_REVIEW_REQUIRED'
    assert report['observed_usdt_wallet'] == report['sealed_usdt_wallet'] == '4200'
    assert report['gold_position_context'] is None
    risk = report['legacy_risk']
    assert risk['canonical_wallet'] == '4999.6801' and risk['initial_equity'] == '5000'
    assert sum(map(Decimal, risk['capacity_consumed'].values())) == Decimal('239.8')
    assert report['collateral_conversion'] == 'NOT_PERFORMED'
    assert report['partition_installed'] is True and report['gold_enrollment_installed'] is False
    assert all(report[key] is False for key in ('approval_granted', 'source_verified',
        'submission_enabled', 'economic_authority'))
    assert before == (first['state'], first['events'].row_count())


@pytest.mark.parametrize('damage', ['activation_missing', 'executor_missing', 'source',
    'old_registry', 'already_registered', 'gold_history', 'legacy_prefix', 'legacy_wallet',
    'gold_wallet', 'account', 'symbol', 'stale', 'time_bool'])
def test_first_gold_review_never_hides_a_missing_seal_new_authority_or_changed_account(
        first, monkeypatch, damage):
    state = deepcopy(first['state']);origin = deepcopy(first['origin']);at = NOW + 140000
    if damage == 'activation_missing': first['activation'].unlink()
    elif damage == 'executor_missing': first['executor'].unlink()
    elif damage == 'source': monkeypatch.setattr(runtime, 'executing_source_commit', lambda: 'd' * 40)
    elif damage == 'old_registry': first['registry']['keys'][0]['status'] = 'REVOKED'
    elif damage == 'already_registered': first['registry'] = deepcopy(first['registered'])
    elif damage == 'gold_history': state = append_target(state)
    elif damage == 'legacy_prefix': state['reservations'][0]['record_hash'] = 'f' * 64
    elif damage in ('legacy_wallet', 'gold_wallet'):
        asset = 'USDC' if damage == 'legacy_wallet' else 'USDT'
        for read in origin['reads']:
            if read['kind'].startswith('account'):
                row = next(x for x in read['data']['assets'] if x['asset'] == asset)
                for key in ('walletBalance', 'crossWalletBalance', 'availableBalance', 'marginBalance'):
                    row[key] = str(Decimal(row[key]) + 1)
    elif damage == 'account': origin['account_ref_sha256'] = 'e' * 64
    elif damage == 'symbol': origin['symbol'] = 'BTCUSDC'
    elif damage == 'stale': at += 31000
    else: at = True
    before = deepcopy(first['state']), first['events'].row_count()
    with pytest.raises(ValueError):
        runtime.first_gold_enrollment_review(state=state, origin=origin, as_of_ms=at)
    assert before == (first['state'], first['events'].row_count())
