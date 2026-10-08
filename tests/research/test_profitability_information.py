import pytest
from services.research.profitability_information import synchronized_basis, controlled_specs


def row(market,price,quote='USDC',available=1000):
    return {'market':market,'base':'BTC','quote':quote,'event_ms':900,'available_ms':available,'price':price}


def test_basis_rejects_mixed_currency_future_or_unsynchronized_evidence():
    a=row('spot',100);b=row('perpetual',101);c=row('index',100)
    result=synchronized_basis([a,b,c],decision_ms=1000)
    assert result['basis']==pytest.approx(.01) and result['quote']=='USDC'
    with pytest.raises(ValueError):synchronized_basis([a,row('perpetual',101,'USDT'),c],decision_ms=1000)
    with pytest.raises(ValueError):synchronized_basis([a,row('perpetual',101,available=1001),c],decision_ms=1000)
    with pytest.raises(ValueError):synchronized_basis([a,{**b,'event_ms':899},c],decision_ms=1000)


def test_oi_contrasts_are_bounded_and_do_not_assign_direction_to_oi():
    specs=controlled_specs()
    assert len(specs)==6
    assert specs[0]['features']==['price']
    assert all(s['outer_feedback_allowed'] is False for s in specs)
    assert all(s['oi_direction_assumption']=='NONE' for s in specs)
