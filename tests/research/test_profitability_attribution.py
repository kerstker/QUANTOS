from copy import deepcopy
from decimal import Decimal as D

import pytest

from services.research.perpetual_alpha import replay
from services.research.profitability_attribution import attribute_replay
from tests.research.test_perpetual_alpha import market


def test_flat_market_explains_loss_without_double_counting_slippage():
    data = market()
    result = replay(**data, decision=lambda at, held: .25 if not held else 0., slip_bps=5, stop=True)
    report = attribute_replay(result)
    assert report['status'] == 'RECONCILED_TECHNICAL_ONLY'
    assert D(report['gross_reference_pnl']) == 0
    assert D(report['net_pnl']) == -D(report['embedded_slippage']) - D(report['fees'])
    assert report['diagnosis'] == 'NO_POSITIVE_GROSS_EDGE'
    assert report['actual_venue_fees'] is None
    assert report['spread_component'] is None
    assert D(report['turnover_notional']) > 0
    assert not report['economic_eligible']


def test_hold_direction_contribution_matches_gross_reference_gain():
    data = market()
    data['prices'][1440:] = 101.
    data['marks'][1440:] = 101.
    result = replay(**data, decision=lambda at, held: .25 if not held else None, slip_bps=5, stop=True)
    report = attribute_replay(result)
    assert D(report['position_contribution']['long']) == D(report['gross_reference_pnl']) > 0
    assert D(report['position_contribution']['short']) == 0
    assert report['prediction_edge'] == 'UNKNOWN'


@pytest.mark.parametrize('mutation', ['fill_fee', 'residual', 'missing_fills'])
def test_incomplete_or_conflicting_receipts_are_unknown(mutation):
    data = market()
    result = replay(**data, decision=lambda at, held: .25, slip_bps=5, stop=True)
    before = deepcopy(result)
    if mutation == 'fill_fee': result['fills'][0]['fee'] = '999'
    elif mutation == 'residual': result['reconciliation']['residual'] = '1'
    else: result.pop('fills')
    report = attribute_replay(result)
    assert report['status'] == 'UNKNOWN' and report['net_pnl'] is None
    if mutation == 'missing_fills': assert result['reconciliation'] == before['reconciliation']


def test_legacy_compact_feedback_cannot_invent_full_fill_attribution():
    report = attribute_replay({'cash_model': 'CANONICAL_LINEAR_USDC_PERPETUAL', 'reconciliation': {'residual':'0'}})
    assert report['status'] == 'UNKNOWN'


def test_no_trades_does_not_establish_absence_of_prediction_edge():
    result=replay(**market(),decision=lambda at,held:0.,slip_bps=5,stop=True)
    assert attribute_replay(result)['diagnosis']=='NO_TRADING_EVIDENCE'


def test_usdt_replay_retains_its_own_settlement_currency_and_same_ledger_math():
    from services.research.ai_factory_execution_feedback import execution_feedback
    usdc=replay(**market(),decision=lambda at,held:.25,slip_bps=5,stop=True)
    usdt=replay(**market(),quote='USDT',decision=lambda at,held:.25,slip_bps=5,stop=True)
    assert usdt['cash_model']=='CANONICAL_LINEAR_USDT_PERPETUAL'
    assert usdt['reconciliation']==usdc['reconciliation']
    assert execution_feedback(usdt)['profitability_attribution']['status']=='RECONCILED_TECHNICAL_ONLY'
    with pytest.raises(ValueError,match='currency'):
        replay(**market(),quote='USD',decision=lambda at,held:0.,slip_bps=5,stop=True)
