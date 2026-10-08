from decimal import localcontext

import pytest

from services.data import kraken_units as units

NOW=1800000000000
SPEC={'instrument_id':'KRAKEN:tokenized_asset:AAPLxUSD','base':'AAPLx','asset_class':'tokenized_asset'}


def source():
    return {'error':[],'result':{'AAPLx':{'aclass':'tokenized_asset','altname':'AAPLx','decimals':8,
        'display_decimals':8,'status':'enabled','token_multiplier':'1.0032690125398187'}}}


def test_rebased_quantity_preserves_tokens_and_exact_multiplier_without_context_rounding():
    with localcontext() as ctx:
        ctx.prec=6
        result=units.convert_quantity('2',source(),spec=SPEC,received_at_ms=NOW,as_of_ms=NOW+1)
    assert result['base_quantity']=='2' and result['display_quantity']=='2.0065380250796374'
    assert result['display_basis']=='UNDERLYING_EQUITY' and result['submission_enabled'] is False


@pytest.mark.parametrize('bad',['missing','wrong_class','wrong_asset','zero','future','stale'])
def test_unknown_or_unavailable_multiplier_never_defaults_to_one(bad):
    p=source();now=NOW+1;row=p['result']['AAPLx']
    if bad=='missing':del row['token_multiplier']
    if bad=='wrong_class':row['aclass']='currency'
    if bad=='wrong_asset':p['result']['NVDAx']=p['result'].pop('AAPLx')
    if bad=='zero':row['token_multiplier']='0'
    if bad=='future':now=NOW-1
    if bad=='stale':now=NOW+120001
    with pytest.raises(ValueError):units.convert_quantity('2',p,spec=SPEC,received_at_ms=NOW,as_of_ms=now)


def test_native_xaut_quantity_is_not_equity_or_rebased():
    spec={'instrument_id':'KRAKEN:currency:XAUTUSD','base':'XAUT','asset_class':'currency'}
    p={'error':[],'result':{'XAUT':{'aclass':'currency','altname':'XAUT','decimals':10,'display_decimals':6,'status':'enabled'}}}
    result=units.convert_quantity('0.125',p,spec=spec,received_at_ms=NOW,as_of_ms=NOW)
    assert result['display_quantity']=='0.125' and result['display_basis']=='NATIVE_ASSET'
