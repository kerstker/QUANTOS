from copy import deepcopy
from decimal import Decimal as D, localcontext

import pytest

from services.research.gold_trade_windows import build_window, WindowError
from services.data.gold_execution_tape import new_gold_trade_cursor
from services.research.gold_signal import GoldSignal, Rule, Zone


def packet(identifier, at, price, quantity='1', maker=True, received=None):
    return {'received_at_ms':at+10 if received is None else received,
        'payload':{'e':'aggTrade','s':'XAUUSDT','E':at+1,'T':at,'a':identifier,
            'f':identifier*2,'l':identifier*2+1,'p':price,'q':quantity,'m':maker}}


def tape():
    return [packet(1,900,'101'),packet(2,1100,'99.5','12'),
            packet(3,1800,'100.5','3',False),packet(4,2000,'100.6'),
            packet(5,2900,'101.5'),packet(6,3000,'102')]


def build(rows=None,now=2010):
    return build_window(tape() if rows is None else rows,start_ms=1000,end_ms=2000,
                        as_of_ms=now,max_age_ms=100,cursor_factory=new_gold_trade_cursor)


def test_bracketed_contiguous_aggregates_construct_real_signal_inputs():
    first=build()
    assert first.low==D('99.5') and first.close==D('100.5')
    assert first.sell_quantity==D('12') and first.buy_quantity==D('3')
    assert first.received_at_ms==2010 and first.complete is True
    signal=GoldSignal(Rule(1000,100,D('10'),2),Zone(D('99'),D('100'),500,10000))
    assert signal.observe(first,decision_ms=2010)['state']=='CONFIRMING'
    second=build_window(tape(),start_ms=2000,end_ms=3000,as_of_ms=3010,max_age_ms=100,cursor_factory=new_gold_trade_cursor)
    assert signal.observe(second,decision_ms=3010)['intent']=='LONG_RESEARCH_SIGNAL'


def test_no_future_watermark_or_incomplete_prefix_may_claim_complete():
    for rows in (tape()[:3],tape()[1:]):
        with pytest.raises(WindowError): build(rows)
    with pytest.raises(WindowError): build(now=2009)


def test_gap_and_conflicting_duplicate_fail_closed():
    with pytest.raises(WindowError): build([tape()[0],*tape()[2:]])
    rows=tape(); conflict=deepcopy(rows[1]); conflict['payload']['q']='99'
    with pytest.raises(WindowError): build([rows[0],rows[1],conflict,*rows[2:]])


def test_duplicate_does_not_double_count_or_retime_availability():
    rows=tape()
    result=build([rows[0],rows[1],deepcopy(rows[1]),*rows[2:]])
    assert result.sell_quantity==D('12')
    assert result.received_at_ms==build().received_at_ms


def test_future_payload_never_changes_a_sealed_prefix():
    rows=tape(); baseline=build(rows)
    rows[-1]['payload']={'unavailable':'not parsed'}
    assert build(rows)==baseline


def test_timestamp_reorder_stale_and_empty_windows_rejected():
    rows=tape(); rows[2]['received_at_ms']=1000
    with pytest.raises(WindowError): build(rows)
    with pytest.raises(WindowError): build(now=2200)
    with pytest.raises(WindowError,match='NO_WINDOW_TRADES'):
        build([packet(1,900,'100'),packet(2,2000,'101')])


def test_quantity_accumulation_has_exact_context_independent_precision():
    rows=tape(); rows[2]['payload']['m']=True
    rows[2]['payload']['q']='0.000000000000000001'
    with localcontext() as ctx:
        ctx.prec=3
        result=build(rows)
    assert result.sell_quantity==D('12.000000000000000001')


def test_window_uses_a_fresh_injected_cursor_without_reading_future_packets():
    seen = []
    cursors = []

    def factory():
        cursor = new_gold_trade_cursor()
        cursors.append(cursor)

        class Port:
            def apply(self, payload, *, received_at_ms):
                seen.append(received_at_ms)
                return cursor.apply(payload, received_at_ms=received_at_ms)

        return Port()

    for _ in range(2):
        result = build_window(tape(), start_ms=1000, end_ms=2000,
                              as_of_ms=2010, max_age_ms=100, cursor_factory=factory)
        assert result.sell_quantity == D('12')
    assert len(cursors) == 2 and cursors[0] is not cursors[1]
    assert max(seen) == 2010
