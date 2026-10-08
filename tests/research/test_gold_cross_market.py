from dataclasses import replace
from decimal import Decimal as D, localcontext, ROUND_UP, Inexact

import pytest

from services.research.gold_cross_market import Quote, compare_at


def quotes():
    return ([Quote('BINANCE','XAUUSDT','USDT_PER_TROY_OUNCE',D('4000'),D('4002'),1000,1010,'a'*64)],
            [Quote('COMEX','GCZ2026','USD_PER_TROY_OUNCE',D('4010'),D('4012'),1001,1011,'b'*64)],
            [Quote('FX_REFERENCE','USDTUSD','USD_PER_USDT',D('0.999'),D('1.001'),1002,1012,'c'*64)])


def compare(data=None,**kwargs):
    local,external,fx=quotes() if data is None else data
    return compare_at(local,external,fx,external_contract='GCZ2026',
        decision_ms=kwargs.get('decision_ms',1020),max_age_ms=100,max_skew_ms=20,
        external_session_open=kwargs.get('external_session_open',True))


def test_comparison_binds_three_sources_and_is_not_a_trade_signal():
    r=compare()
    assert r['status']=='ALIGNED_QUOTES_ONLY'
    assert r['source_hashes']==['a'*64,'b'*64,'c'*64]
    assert r['causal_driver'] is None and r['trade_intent'] is None
    assert r['authoritative'] is False


def test_usdt_is_not_silently_equal_to_usd_and_bounds_use_opposite_fx_sides():
    data=quotes(); data[2][0]=replace(data[2][0],bid=D('2'),ask=D('2'))
    r=compare(data)
    assert D(r['external_bid_usdt'])==D('2005')
    assert D(r['external_ask_usdt'])==D('2006')
    assert D(r['basis_mid_bps'])<0
    with localcontext() as ctx:
        ctx.prec=3
        assert compare(data)==r


def test_missing_external_or_fx_never_falls_back_to_local_confirmation():
    local,external,fx=quotes()
    assert compare((local,[],fx))['status']=='INPUT_MISSING'
    assert compare((local,external,[]))['status']=='INPUT_MISSING'


def test_future_quote_is_not_used_and_cannot_change_prefix():
    data=quotes(); original=compare(data)
    data[1].append(replace(data[1][0],bid=D('9999'),ask=D('10000'),event_ms=1030,received_ms=1040))
    assert compare(data)==original


@pytest.mark.parametrize('change',[{'instrument':'GCG2027'},{'unit':'USDT_PER_TROY_OUNCE'},
    {'bid':D('5000')},{'bid':D('NaN')},{'event_ms':1020},{'event_ms':980}])
def test_wrong_contract_currency_crossed_future_event_or_time_skew_rejected(change):
    data=quotes(); data[1][0]=replace(data[1][0],**change)
    assert compare(data)['status']=='INPUT_INVALID'


def test_stale_or_closed_external_market_does_not_confirm():
    assert compare(decision_ms=1200)['status']=='INPUT_INVALID'
    assert compare(external_session_open=False)['status']=='EXTERNAL_SESSION_CLOSED'


def test_ambiguous_same_receipt_time_is_rejected():
    data=quotes(); data[1].append(replace(data[1][0],ask=D('4013')))
    assert compare(data)['status']=='INPUT_INVALID'


def test_callers_rounding_and_traps_cannot_change_comparison():
    expected=compare()
    with localcontext() as context:
        context.rounding=ROUND_UP
        context.traps[Inexact]=True
        assert compare()==expected
