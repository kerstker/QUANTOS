import pytest
from tools.profitability_forward_market import market_from_receipts,DAY,MINUTE


def receipts():
    start=2*DAY+300000;end=3*DAY+300000
    def rows(lo):return [[at,'100000','100000','100000','100000','1',at+59999,'100000'] for at in range(lo,end+MINUTE,MINUTE)]
    records=[{'kind':'klines','received_ms':end+MINUTE+1,'payload':rows(DAY)},
             {'kind':'markPriceKlines','received_ms':end+MINUTE+1,'payload':rows(start)},
             {'kind':'fundingRate','received_ms':end+MINUTE+1,'payload':[
                 {'symbol':'BTCUSDC','fundingTime':at,'fundingRate':'0.0001','markPrice':'100000'} for at in range(DAY,3*DAY+1,8*3600000)]}]
    return start,end,records


def test_trade_marks_funding_and_previous_day_volume_are_aligned():
    start,end,rows=receipts();market=market_from_receipts(rows,start=start,end=end)
    assert len(market['times'])==1441
    assert market['daily_volume'][2]==144000000


@pytest.mark.parametrize('fault',['missing','duplicate','unclosed','funding_gap'])
def test_market_gaps_are_not_replaced_by_zero_cost_or_flat_prices(fault):
    start,end,rows=receipts()
    if fault=='missing':rows[0]['payload'].pop(10)
    if fault=='duplicate':rows[1]['payload'].append(rows[1]['payload'][0])
    if fault=='unclosed':rows[1]['received_ms']=end
    if fault=='funding_gap':rows[2]['payload']=[]
    with pytest.raises(ValueError):market_from_receipts(rows,start=start,end=end)


def test_venue_settlement_millisecond_offsets_preserve_actual_event_times():
    start,end,rows=receipts()
    for i,row in enumerate(rows[2]['payload']):row['fundingTime']+=i%6
    expected=[r['fundingTime'] for r in rows[2]['payload']]
    market=market_from_receipts(rows,start=start,end=end)
    assert [r['time'] for r in market['funding']]==expected


def test_settlement_clock_tolerance_does_not_hide_missing_settlement():
    start,end,rows=receipts()
    rows[2]['payload'].pop(2)
    with pytest.raises(ValueError,match='funding coverage'):market_from_receipts(rows,start=start,end=end)


def test_settlement_clock_tolerance_is_bounded_to_one_second():
    start,end,rows=receipts()
    rows[2]['payload'][2]['fundingTime']+=1001
    with pytest.raises(ValueError,match='funding coverage'):market_from_receipts(rows,start=start,end=end)
