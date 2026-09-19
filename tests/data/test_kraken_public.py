from copy import deepcopy

import pytest

from services.data.kraken_public import instrument, closed_rows

NOW = 1800000000000


def pair():
    return {'error': [], 'result': {'AAPLXUSD': {'altname': 'AAPLXUSD', 'wsname': 'AAPLx/USD',
        'base': 'AAPLX', 'quote': 'ZUSD', 'aclass_base': 'tokenized_asset', 'aclass_quote': 'currency',
        'pair_decimals': 4, 'lot_decimals': 8, 'lot_multiplier': 1, 'ordermin': '0.01',
        'costmin': '0.5', 'tick_size': '0.0001', 'status': 'online'}}}


def market():
    return {'error': [], 'result': {'AAPLXUSD': [[NOW//1000-120, '100','102','99','101','100','2',4],
        [NOW//1000-60, '101','103','100','102','101','3',5],
        [NOW//1000,'102','105','101','104','103','1',2]], 'last':NOW//1000-60}}


def test_tokenized_identity_rules_and_only_committed_observed_rows():
    spec = instrument(pair(), pair='AAPLx/USD', asset_class='tokenized_asset')
    assert spec['instrument_id']=='KRAKEN:tokenized_asset:AAPLXUSD'
    rows = closed_rows(market(), spec=spec, interval=1, received_at_ms=NOW+1)
    assert len(rows)==2 and rows[-1]['close']==102
    assert all(r['knowledge_time']==NOW+1 for r in rows)
    assert all(r['instrument_id']==spec['instrument_id'] for r in rows)


@pytest.mark.parametrize('change', ['wrong_class','missing_rule','ambiguous','error'])
def test_rejects_wrong_product_or_incomplete_rules(change):
    p=pair()
    if change=='wrong_class': p['result']['AAPLXUSD']['aclass_base']='currency'
    if change=='missing_rule': del p['result']['AAPLXUSD']['tick_size']
    if change=='ambiguous': p['result']['OTHER']=deepcopy(p['result']['AAPLXUSD'])
    if change=='error': p['error']=['EQuery:Unknown asset pair']
    with pytest.raises(ValueError): instrument(p,pair='AAPLx/USD',asset_class='tokenized_asset')


@pytest.mark.parametrize('change',['future','duplicate','ohlc','nonfinite','wrong_pair'])
def test_market_rejects_bad_evidence(change):
    p=market();bars=p['result']['AAPLXUSD']
    if change=='future': bars[1][0]+=120
    if change=='duplicate': bars[1][0]=bars[0][0]
    if change=='ohlc': bars[0][2]='98'
    if change=='nonfinite': bars[0][1]='NaN'
    if change=='wrong_pair': p['result']['OTHER']=p['result'].pop('AAPLXUSD')
    with pytest.raises(ValueError): closed_rows(p,spec=instrument(pair(),pair='AAPLx/USD',asset_class='tokenized_asset'),interval=1,received_at_ms=NOW+1)


def test_retains_zero_activity_bars_and_accepts_720_committed_plus_trailing():
    p=market();p['result']['AAPLXUSD']=[
        [NOW//1000-(720-i)*60,'100','100','100','100','0.00','0.000000',0]
        for i in range(721)]
    spec=instrument(pair(),pair='AAPLx/USD',asset_class='tokenized_asset')
    rows=closed_rows(p,spec=spec,interval=1,received_at_ms=NOW+1)
    assert len(rows)==720 and all(row['volume']==0 for row in rows)
    p['result']['AAPLXUSD'][0][6]='1'
    with pytest.raises(ValueError):closed_rows(p,spec=spec,interval=1,received_at_ms=NOW+1)
