import io
import copy
import zipfile
from decimal import Decimal as D

import numpy as np
import pytest

from services.research.spot_alpha import DAY, MINUTE, SpotRules
from tools.public_spot_alpha_research import decode_archive, replay, timestamp
from tools.public_spot_alpha_analysis import reconcile
from fractions import Fraction


def fixture():
    start = timestamp('2025-01-01')+5*MINUTE
    times = np.arange(start-5*MINUTE, start+2*DAY+MINUTE, MINUTE)
    minute = np.column_stack([times, np.full((len(times), 4), 100.), np.ones(len(times)), times+MINUTE-1, np.full(len(times), 100.)])
    days = np.array([start-5*MINUTE-DAY, start-5*MINUTE])
    daily = np.full((2, 5), 100.)
    rules = SpotRules('BTCUSDT','USDT', D('.001'),D('.001'),D('1000'),D('5'),D('10000000'),D('.01'),5)
    return minute, days, daily, np.zeros(2), rules, start


def test_loss_observed_then_exits_next_minute_and_keeps_unhalted_comparison():
    minute, days, daily, pred, rules, start = fixture()
    minute[15:, 1:5] = 50
    halted = replay(minute, days, daily, pred, rules, 'passive25', 5, True, start=start,end=start+2*DAY)
    fills = [r for r in halted['orders'] if r['status']=='FILLED']
    assert fills[0]['at'] == start
    assert halted['metrics']['halted_at'] == int(minute[15,0])
    assert fills[1]['at'] == int(minute[16,0])
    assert fills[1]['side'] == 'SELL'
    assert len(fills) == 2
    assert halted['metrics']['net_pnl'] < -150
    unlimited = replay(minute, days, daily, pred, rules, 'passive25', 5, False, start=start,end=start+2*DAY)
    assert unlimited['metrics']['halted_at'] is None
    assert D(unlimited['metrics']['remaining_quantity']) > 0


def test_missing_quote_rejects_entire_case_and_future_bars_do_not_change_past():
    minute, days, daily, pred, rules, start = fixture()
    with pytest.raises(ValueError, match='coverage'):
        replay(np.delete(minute,20,axis=0), days,daily,pred,rules,'passive25',5,True,start=start,end=start+2*DAY)
    partial = minute.copy()
    partial[20, 6] -= 1000
    with pytest.raises(ValueError, match='coverage'):
        replay(partial, days,daily,pred,rules,'passive25',5,True,start=start,end=start+2*DAY)
    one = replay(minute,days,daily,pred,rules,'passive25',5,True,start=start,end=start+DAY)
    minute[1450:, 1:5] = 1e6
    two = replay(minute,days,daily,pred,rules,'passive25',5,True,start=start,end=start+DAY)
    assert one == two


def test_subminimum_exit_retains_position_and_marked_equity():
    minute,days,daily,pred,rules,start=fixture()
    minute[15:,1:5] = .01
    minute[15:,7] = .01
    result=replay(minute,days,daily,pred,rules,'passive25',5,True,start=start,end=start+2*DAY)
    assert D(result['metrics']['remaining_quantity'])>0
    assert result['metrics']['halted_at'] is not None
    assert result['metrics']['skipped']>0
    last=result['equity'][-1]
    assert D(last['equity'])==D(last['cash'])+D(last['quantity'])*D('.01')


def test_2025_microseconds_are_not_milliseconds():
    t=1735689600000000
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:
        z.writestr('sample.csv',f'{t},100,101,99,100,1,{t+60000000-1},100,1,0,0,0\n')
    row=decode_archive(buf.getvalue())[0]
    assert row[0]==1735689600000
    assert row[6]==1735689659999


def test_independent_cash_reconciliation_detects_corrupted_fee_and_equity():
    minute,days,daily,pred,rules,start=fixture()
    result=replay(minute,days,daily,pred,rules,'passive25',5,True,start=start,end=start+2*DAY)
    prices={int(row[0]):Fraction(str(row[1])) for row in minute}
    assert reconcile(result,prices)==(1,2)
    broken=copy.deepcopy(result)
    broken['orders'][0]['fee']='0'
    with pytest.raises(AssertionError):
        reconcile(broken,prices)
    broken=copy.deepcopy(result)
    broken['equity'][-1]['equity']='1500'
    with pytest.raises(AssertionError):
        reconcile(broken,prices)
