import pytest
import numpy as np
from services.research.profitability_information import basis_panel, basis_specs


def observation(slot,quote='USDC'):
    def prices(event,spot,perp):
        return [{'market':market,'base':'BTC','quote':quote,'event_ms':event,
                 'available_ms':event+1,'price':price} for market,price in
                [('spot',spot),('perpetual',perp),('index',spot)]]
    return {'slot_ms':slot,'source_hash':'a'*64,'previous':prices(slot-120000,100,101),
            'current':prices(slot-60000,102,104)}


def test_basis_panel_uses_synchronized_causal_prices_and_flow_baseline():
    panel=basis_panel([3600000],np.array([[.01,0,0,.02,.3]]),[observation(3600000)],quote='USDC')
    assert panel[0,:2].tolist()==[.01,.3]
    assert panel[0,2]==pytest.approx(104/102-1)
    assert panel[0,3]==pytest.approx(104/102-1-.01)
    assert panel[0,4]==pytest.approx(104/101-102/100)
    assert basis_specs()[0]['features']==['price','flow']
    assert len(basis_specs())==4


@pytest.mark.parametrize('change',['future','currency','stale','missing','duplicate'])
def test_invalid_basis_information_is_not_imputed(change):
    row=observation(3600000);rows=[row]
    if change=='future':row['current'][0]['available_ms']=3600001
    if change=='currency':row['current'][0]['quote']='USDT'
    if change=='stale':
        for r in row['previous']:r['event_ms']=1;r['available_ms']=2
    if change=='missing':rows=[]
    if change=='duplicate':rows=[row,row]
    with pytest.raises(ValueError):basis_panel([3600000],np.zeros((1,5)),rows,quote='USDC')
