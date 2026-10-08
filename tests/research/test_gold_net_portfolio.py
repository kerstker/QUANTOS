from decimal import Decimal as D

import pytest

from services.research.gold_net_portfolio import compare_portfolio
from services.execution.perpetual_public_replay import prepare_public_replay
from tests.execution.test_perpetual_public_replay import gold_capture_fixture, START


def run(monkeypatch, sides, *, collateral='1000', cap='100'):
    def signal(bars, *, candidate, as_of_ms):
        return dict(status='READY', entry_side=sides.get(candidate), exit_long=False,
            exit_short=False, stop_distance='5', max_holding_bars=16,
            decision_id=candidate+'-fixed')
    monkeypatch.setattr('services.research.gold_net_portfolio.evaluate', signal)
    return compare_portfolio(bars=(), prepared=prepare_public_replay(gold_capture_fixture()),
        release_identity={'fixture': True}, quantities={n:D('.1') for n in ('channel','trend','reversion')},
        initial_collateral=D(collateral), fee_rate=D('.001'), max_notional=D(cap))


def test_opposing_targets_cancel_before_fees_or_positions(monkeypatch):
    result = run(monkeypatch, {'channel':'BUY', 'reversion':'SELL'})
    assert result['simulation']['orders'] == []
    assert result['simulation']['reconciliation']['fee'] == '0'
    assert result['simulation']['ending_state']['positions'] == {}
    assert result['construction']['separate_strategy_pnl_computed'] is False
    assert result['economic_eligible'] is False


def test_shared_account_fills_one_net_order_and_charges_once(monkeypatch):
    result = run(monkeypatch, {'channel':'BUY','trend':'BUY'})
    orders = result['simulation']['orders']
    assert len(orders) == 1 and orders[0]['filled_at'] == START+1002
    assert result['simulation']['ending_state']['positions']['XAUUSDT']['quantity'] == '0.2'
    assert result['simulation']['reconciliation']['residual'] == '0'
    assert D(result['simulation']['reconciliation']['fee']) > 0


def test_notional_limit_rejects_combined_target(monkeypatch):
    result = run(monkeypatch, {'channel':'BUY','trend':'BUY'}, cap='10')
    assert result['simulation']['ending_state']['positions'] == {}
    assert all(o['filled_at'] is None for o in result['simulation']['orders'])


def test_shared_equity_rejects_excess_combined_exposure(monkeypatch):
    result = run(monkeypatch, {'channel':'BUY','trend':'BUY'}, collateral='15')
    assert result['simulation']['ending_state']['positions'] == {}
    assert all(o['filled_at'] is None for o in result['simulation']['orders'])



@pytest.mark.parametrize('incremental', [False, True])
def test_funding_and_mark_loss_triggers_shared_five_percent_stop(monkeypatch, incremental):
    from copy import deepcopy
    from tests.execution.test_public_observed_funding import inputs
    from tests.execution.test_perpetual_public_replay import sealed
    document, record, nominal = inputs()
    row = deepcopy(document['samples'][-1])
    for kind, delay in [('mark', 1), ('depth', 2)]:
        row[kind].update(requested_at=START+4000, received_at=START+4000+delay)
    row['mark']['payload']['time'] = START+4000
    row['depth']['payload'].update(T=START+4000, E=START+4000, lastUpdateId=5)
    document['samples'].append(row)
    document['exchange_after'].update(requested_at=START+5000, received_at=START+5001)
    record['payload'][0]['fundingRate'] = '0.1'
    # Synthetic settlement-mark stress; preserve the existing single-name gate.
    record['payload'][0]['markPrice'] = '500'
    record = sealed({'record':record})['record']
    prepared = prepare_public_replay(sealed(document), observed_funding_records=[record], settlement_window_ms=1000)
    def signal(bars, *, candidate, as_of_ms):
        return dict(status='READY', entry_side='BUY' if candidate != 'reversion' else None,
            exit_long=False, exit_short=False, stop_distance='5', max_holding_bars=16,
            decision_id=candidate+'-fixed')
    monkeypatch.setattr('services.research.gold_net_portfolio.evaluate', signal)
    from services.research.gold_bar_candidates import Bar, INTERVAL_MS
    close = START // INTERVAL_MS * INTERVAL_MS
    bars = tuple(Bar(close+(i-2)*INTERVAL_MS, close+(i-1)*INTERVAL_MS,
        START+delay, D('100'), D('101'), D('99'), D('100'))
        for i, delay in enumerate((500, 2000)))
    result = compare_portfolio(bars=bars, prepared=prepared, release_identity={'fixture':True},
        quantities={n:D('.1') for n in ('channel','trend','reversion')},
        initial_collateral=D('150'), fee_rate=D('.001'), max_notional=D('100'),
        incremental_bars=incremental)
    assert result['loss_control']['status'] == 'STOPPED_FLAT'
    assert result['loss_control']['trigger']['timestamp'] == START+2002
    assert result['simulation']['ending_state']['positions'] == {}
    assert result['simulation']['reconciliation']['funding'] == '-10'
    assert result['simulation']['reconciliation']['residual'] == '0'



def test_real_signal_families_use_causal_observation_journal(tmp_path, monkeypatch):
    from tests.tools.test_gold_bar_observed_replay import continuous_inputs
    from tools.gold_bar_observed_replay import compare_observed_portfolio
    journal, capture, _ = continuous_inputs(tmp_path, monkeypatch)
    result = compare_observed_portfolio(journal=journal, capture=capture,
        release_identity={'fixture':True}, quantities={n:D('.1') for n in ('channel','trend','reversion')},
        initial_collateral=D('1000'), fee_rate=D('.001'), max_notional=D('100'))
    assert result['simulation']['reconciliation']['residual'] == '0'
    assert result['submission_enabled'] is False
    assert all(row['net_target'] == '0' for row in result['target_trace'] if row['timestamp'] < START+1500)
    assert result['construction']['opposing_targets'] == 'NET_BEFORE_EXECUTION'


def test_reversal_reduces_actual_inventory_before_new_opening(monkeypatch):
    from copy import deepcopy
    from tests.execution.test_perpetual_public_replay import sealed
    document = gold_capture_fixture()
    for index in (4, 5):
        row = deepcopy(document['samples'][-1])
        for kind, delay in [('mark',1), ('depth',2)]:
            row[kind].update(requested_at=START+index*1000, received_at=START+index*1000+delay)
        row['mark']['payload']['time'] = START+index*1000
        row['depth']['payload'].update(T=START+index*1000,E=START+index*1000,lastUpdateId=index+1)
        document['samples'].append(row)
    document['exchange_after'].update(requested_at=START+6000,received_at=START+6001)
    def signal(bars, *, candidate, as_of_ms):
        phase = 0 if as_of_ms < START+2000 else 1 if as_of_ms < START+4000 else 2
        return dict(status='READY', entry_side=None if candidate != 'channel' else 'BUY' if phase==0 else 'SELL',
            exit_long=phase>0, exit_short=False, stop_distance='5', max_holding_bars=16,
            decision_id=str(phase))
    monkeypatch.setattr('services.research.gold_net_portfolio.evaluate',signal)
    result=compare_portfolio(bars=(), prepared=prepare_public_replay(sealed(document)),
        release_identity={'fixture':True}, quantities={n:D('.1') for n in ('channel','trend','reversion')},
        initial_collateral=D('1000'),fee_rate=D('.001'),max_notional=D('100'))
    orders=result['simulation']['orders']
    assert len(orders)==3
    assert orders[1]['filled_at'] < orders[2]['created_at']
    assert result['simulation']['ending_state']['positions']['XAUUSDT']['quantity']=='-0.1'
    assert result['simulation']['reconciliation']['residual']=='0'


def test_observed_portfolio_advances_received_bars_without_resetting_account(tmp_path, monkeypatch):
    from services.research.gold_net_portfolio import GoldNetTargetPortfolio
    from tools.gold_bar_observed_replay import compare_observed_portfolio
    from tests.tools.test_gold_bar_observed_replay import continuous_inputs
    appended = []
    original = GoldNetTargetPortfolio.append_bars
    def append(self, bars):
        appended.append((self.last_time, dict(self.targets), tuple(self.orders), bars))
        return original(self, bars)
    monkeypatch.setattr(GoldNetTargetPortfolio, 'append_bars', append)
    journal, capture, received = continuous_inputs(tmp_path, monkeypatch)
    kwargs = dict(journal=journal, capture=capture, release_identity={'fixture': True},
        quantities={n:D('.1') for n in ('channel','trend','reversion')},
        initial_collateral=D('1000'), fee_rate=D('.001'), max_notional=D('100'))
    result = compare_observed_portfolio(**kwargs)
    assert len(appended) == 2
    assert all(last < bars[0].available_ms for last, _, _, bars in appended)
    assert not appended[0][2] and appended[1][2]
    assert any(appended[1][1].values())
    orders = result['simulation']['orders']
    assert any(o['filled_at'] is not None and o['filled_at'] < received for o in orders)
    assert result['simulation']['reconciliation']['residual'] == '0'
    from services.research.gold_net_portfolio import compare_portfolio
    def whole_tape(**args):
        args['incremental_bars'] = False
        return compare_portfolio(**args)
    monkeypatch.setattr('services.research.gold_net_portfolio.compare_portfolio', whole_tape)
    assert compare_observed_portfolio(**kwargs) == result


def test_portfolio_bar_append_rejects_late_or_duplicate_receipts():
    from services.research.gold_net_portfolio import GoldNetTargetPortfolio
    from services.research.gold_bar_candidates import Bar, INTERVAL_MS
    bar = Bar(0, INTERVAL_MS, INTERVAL_MS+1, D('100'), D('101'), D('99'), D('100'))
    portfolio = GoldNetTargetPortfolio((), quantities={n:D('.1') for n in ('channel','trend','reversion')},
                                      max_notional=D('100'))
    portfolio.last_time = bar.available_ms
    with pytest.raises(ValueError, match='ALREADY_PROCESSED'):
        portfolio.append_bars((bar,))
    assert portfolio.bars == ()
    portfolio.last_time = None
    portfolio.append_bars((bar,))
    with pytest.raises(ValueError, match='DUPLICATE'):
        portfolio.append_bars((bar,))
    assert portfolio.bars == (bar,)
