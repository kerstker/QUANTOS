from decimal import Decimal as D
import pytest
from services.execution.perpetual_results import fixed_frequency_equity


def row(t,e,sequence=0):
    return {'timestamp':t,'sequence':sequence,'equity':e}


def test_extra_events_and_same_time_updates_do_not_change_fixed_returns():
    base=[row(0,'100'),row(10,'110'),row(20,'99')]
    extra=[row(0,'100'),row(5,'103'),row(10,'105'),row(10,'110',1),row(15,'104'),row(20,'99')]
    a=fixed_frequency_equity(base,start_ms=0,end_ms=25,frequency_ms=10)
    b=fixed_frequency_equity(extra,start_ms=0,end_ms=25,frequency_ms=10)
    assert a==b
    assert [p['net_return'] for p in a['points']]==[None,'0.1','-0.1']
    assert a['excluded_tail_ms']==5
    assert a['annualized_sharpe'] is None


def test_empty_periods_carry_last_observed_equity_forward():
    r=fixed_frequency_equity([row(0,'100'),row(30,'110')],start_ms=0,end_ms=30,frequency_ms=10)
    assert [p['equity'] for p in r['points']]==['100','100','100','110']


def test_nonpositive_equity_cannot_create_a_sharpe():
    r=fixed_frequency_equity([row(0,'100'),row(10,'-1'),row(20,'2')],start_ms=0,end_ms=20,frequency_ms=10)
    assert r['points'][-1]['net_return'] is None
    assert r['statistics_status']=='NONPOSITIVE_EQUITY'


@pytest.mark.parametrize('rows,frequency', [([row(1,'100')],10),([row(0,'100'),row(0,'99')],10),([row(0,'NaN')],10),([row(0,'100')],True)])
def test_malformed_ordering_equity_and_grid_are_rejected(rows,frequency):
    with pytest.raises(ValueError): fixed_frequency_equity(rows,start_ms=0,end_ms=20,frequency_ms=frequency)
