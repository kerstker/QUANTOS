import pytest
from services.research.ai_factory_execution_feedback import execution_feedback


def test_feedback_preserves_canonical_cashflows_and_never_claims_venue_receipts():
    r={'cash_model':'CANONICAL_LINEAR_USDC_PERPETUAL','reconciliation':{'residual':'0','fee':'3','funding':'-2','price_pnl':'5'},
       'fills':[{'time':i,'quantity':'1','price':'2'} for i in range(40)],'orders':[{'status':'REJECTED_CAPACITY'}]}
    f=execution_feedback(r)
    assert f['reconciliation']==r['reconciliation'] and f['reconciliation'] is not r['reconciliation']
    assert f['fill_count']==40 and len(f['recent_fills'])==32
    assert not f['venue_receipts_verified'] and f['order_counts']=={'REJECTED_CAPACITY':1}
    with pytest.raises(ValueError):execution_feedback({**r,'reconciliation':{'residual':'1'}})
