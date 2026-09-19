from decimal import localcontext

import pytest

from services.data import kraken_quotes as quotes
from tests.tools.test_kraken_public_capture import capture

NOW=1800000000000


def source():
    return {'error':[],'result':{'AAPLxUSD':{'a':['102','1','1.25'],'b':['100','1','2.5']}}}


def test_quote_exact_midpoint_and_explicit_time_and_unit_limits():
    spec=capture()[0]['instrument']
    with localcontext() as context:
        context.prec=3
        value=source();value['result']['AAPLxUSD']['a'][0]='100.12345678'
        report=quotes.parse(value,spec=spec,received_at_ms=NOW,as_of_ms=NOW+1)
    assert report['mid']=='100.06172839'
    assert report['source_event_time_ms'] is None
    assert report['price_quantity_basis']=='UNDERLYING_EQUITY'
    assert report['basis_evidence']=='INFERRED_FROM_KRAKEN_XSTOCKS_FAQ'
    assert report['execution_eligible'] is False


@pytest.mark.parametrize('case',['crossed','zero','identity','stale','future','empty'])
def test_bad_or_old_quotes_are_rejected(case):
    p=source();at=NOW
    if case=='crossed':p['result']['AAPLxUSD']['b'][0]='103'
    if case=='zero':p['result']['AAPLxUSD']['a'][0]='0'
    if case=='identity':p['result']['NVDAxUSD']=p['result'].pop('AAPLxUSD')
    if case=='stale':at=NOW+120001
    if case=='future':at=NOW-1
    if case=='empty':p['result']['AAPLxUSD']['a']=[]
    with pytest.raises(ValueError):quotes.parse(p,spec=capture()[0]['instrument'],received_at_ms=NOW,as_of_ms=at)
