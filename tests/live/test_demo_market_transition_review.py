"""Cross-collateral review must keep the old loss and independent cash amounts."""
from copy import deepcopy
from decimal import Decimal
import pytest

from live.execution.demo_batch_risk import _digest

from tests.tools.test_demo_successor_setup import fixture
from tests.tools.test_gold_demo_execution_origin import capture, NOW


def materials(tmp_path):
    legacy, registry, _, context = fixture(tmp_path)
    gold, _ = capture()
    delta = 65000
    def shift(value):
        if isinstance(value, dict): return {k: shift(v) for k, v in value.items()}
        if isinstance(value, list): return [shift(v) for v in value]
        return value + delta if type(value) is int and value >= NOW else value
    gold = shift(gold)
    gold['account_ref_sha256'] = legacy['account_ref_sha256']
    for origin in (legacy, gold):
        for row in origin['reads']:
            if row['account_ref_sha256'] is not None:
                row['account_ref_sha256'] = legacy['account_ref_sha256']
            if row['kind'].startswith('account'):
                row['data']['positions'] = []
                row['data']['assets'] = [dict(asset=asset, walletBalance=amount,
                    crossWalletBalance=amount, availableBalance=amount,
                    marginBalance=amount, unrealizedProfit='0')
                    for asset, amount in [('USDC', '4999.6801'), ('USDT', '4200')]]
            elif row['kind'] == 'positions': row['data'] = []
    return dict(legacy_origin=legacy, gold_origin=gold, legacy_context=context,
                registry=registry, now_ms=NOW+70000)


def review(values):
    from live.execution.demo_market_transition_review import review as inspect
    return inspect(**values)


def test_review_preserves_original_book_and_does_not_convert_collateral(tmp_path):
    values = materials(tmp_path); before = deepcopy(values)
    result = review(values)
    assert result['status'] == 'NEW_GOLD_ENROLLMENT_REQUIRED'
    assert result['legacy_book']['canonical_wallet'] == '4999.6801'
    assert result['legacy_book']['initial_equity'] == '5000'
    assert result['legacy_book']['observed_day_pnl'] == '-0.3199'
    assert result['legacy_book']['capacity_consumed'] == {'original': '239.8'}
    assert result['gold_observation']['wallet'] == '4200'
    assert result['collateral_conversion'] == 'NOT_PERFORMED'
    assert result['migration_installed'] is False and result['approval_granted'] is False
    assert result['source_verified'] is False and result['submission_enabled'] is False
    assert result['atomic_snapshot_verified'] is False
    assert values == before


@pytest.mark.parametrize('damage', ['active_key', 'empty_registry', 'held', 'wrong_market',
    'cash_difference', 'cross_capture_cash', 'account', 'source', 'stale', 'backwards',
    'day', 'orders', 'multi_asset'])
def test_transition_cannot_hide_old_cash_or_reuse_unrelated_account(tmp_path, damage):
    values = materials(tmp_path)
    if damage == 'active_key': values['registry']['keys'][0]['valid_until_ms'] = NOW+80000
    elif damage == 'empty_registry': values['registry']['keys'] = []
    elif damage == 'held': values['legacy_context']['quantity'] = '0.002'
    elif damage == 'wrong_market': values['legacy_context']['symbol'] = 'XAUUSDT'
    elif damage == 'cash_difference': values['legacy_context']['canonical_wallet'] = '5000'
    elif damage == 'cross_capture_cash':
        for row in values['gold_origin']['reads']:
            if row['kind'].startswith('account'):
                asset = next(a for a in row['data']['assets'] if a['asset']=='USDC')
                for key in ('walletBalance', 'crossWalletBalance', 'availableBalance', 'marginBalance'):
                    asset[key] = '5000'
    elif damage == 'account': values['gold_origin']['account_ref_sha256'] = 'a'*64
    elif damage == 'source': values['gold_origin']['source_sha256'] = 'a'*64
    elif damage == 'stale': values['now_ms'] += 40000
    elif damage == 'backwards': values['legacy_origin'], values['gold_origin'] = values['gold_origin'], values['legacy_origin']
    elif damage == 'day': values['legacy_context']['day_id'] -= 1
    elif damage == 'orders': values['gold_origin']['reads'] = [r for r in values['gold_origin']['reads'] if r['kind'] != 'algo_orders']
    else:
        for row in values['gold_origin']['reads']:
            if row['kind'].startswith('config'): row['data']['multiAssetsMargin'] = True
    with pytest.raises(ValueError): review(values)


def test_any_other_collateral_position_requires_resolution(tmp_path):
    values = materials(tmp_path)
    for row in values['gold_origin']['reads']:
        if row['kind'].startswith('account'):
            row['data']['positions'] = [dict(symbol='ETHUSDC', positionSide='BOTH', positionAmt='1')]
        elif row['kind']=='positions':
            row['data'] = [dict(symbol='ETHUSDC', positionSide='BOTH', positionAmt='1',
                entryPrice='100', markPrice='100', unRealizedProfit='0', isolatedWallet='0',
                marginAsset='USDC', updateTime=NOW+64000)]
    with pytest.raises(ValueError): review(values)


@pytest.mark.parametrize('field', ['observed', 'negative'])
def test_unexplained_third_collateral_is_not_ignored(tmp_path, field):
    values = materials(tmp_path)
    for origin in (values['legacy_origin'], values['gold_origin']):
        for row in origin['reads']:
            if row['kind'].startswith('account'):
                amount = '-1' if field=='negative' else '10'
                row['data']['assets'].append(dict(asset='BNB', walletBalance=amount,
                    crossWalletBalance=amount, availableBalance=amount,
                    marginBalance=amount if field=='negative' else '11',
                    unrealizedProfit='0' if field=='negative' else '1'))
    with pytest.raises(ValueError): review(values)


def test_original_receipt_replay_survives_transition_review_without_store_changes(tmp_path, monkeypatch):
    from live.execution.demo_pre_submit import _state
    from tests.live.test_demo_day_checkpoint import recovered_context
    from tests.live.test_demo_reported_recovery import close
    (tmp_path/'book').mkdir()
    (tmp_path/'observations').mkdir()
    batch, stores, events, _, state, context = recovered_context(tmp_path/'book', monkeypatch)
    try:
        values = materials(tmp_path/'observations')
        def shift(value):
            if isinstance(value, dict): return {k: shift(v) for k, v in value.items()}
            if isinstance(value, list): return [shift(v) for v in value]
            return value+60000 if type(value) is int and value>=NOW else value
        for name in ('legacy_origin', 'gold_origin'):
            values[name] = shift(values[name])
            values[name]['account_ref_sha256'] = context['account_ref_sha256']
            for row in values[name]['reads']:
                if row['account_ref_sha256'] is not None:
                    row['account_ref_sha256'] = context['account_ref_sha256']
        values.update(legacy_context=context, now_ms=NOW+130000)
        rows = events.row_count()
        result = review(values)
        assert result['legacy_book']['context_sha256'] == _digest(context)
        assert result['legacy_book']['ledger_state_hash'] == context['ledger_state_hash']
        assert result['legacy_book']['observed_day_pnl'] == '-0.3199'
        assert sum(map(Decimal, result['legacy_book']['capacity_consumed'].values())) == Decimal('239.8')
        assert _state(stores['dispatch'], stores['recovery'], stores['oms']) == state
        assert events.row_count() == rows and not result['migration_installed']
        assert batch['reservation_sha256'] == state['reservations'][0]['record_hash']
    finally:
        events.close(); close(stores)
