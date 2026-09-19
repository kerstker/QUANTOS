from services.portfolio import kraken_valuation as valuation
from services.data.kraken_inventory import reconcile
from services.data.kraken_quotes import parse
from tests.data.test_kraken_inventory import inputs, NOW
from tests.tools.test_kraken_public_capture import capture
from tests.data.test_kraken_quotes import source


def fixture():
    account,markets=inputs()
    inventory=reconcile(account,markets,as_of_ms=NOW+600)
    quote=parse(source(),spec=capture()[0]['instrument'],received_at_ms=NOW+600,as_of_ms=NOW+600)
    return inventory,[quote]


def test_cash_holds_and_open_orders_are_not_counted_twice():
    inventory,quotes=fixture()
    result=valuation.value(inventory,quotes,quote_asset='ZUSD',as_of_ms=NOW+700)
    assert result['status']=='INDICATIVE'
    assert result['observed_spot_mid_value']=='1215.1702379942224089395552352487064386'
    assert result['rows'][1]['mid_value']=='1000.00000001'
    assert result['net_asset_value'] is None and result['execution_eligible'] is False
    assert 'XSTOCKS_PRICE_BASIS_INFERRED' in result['limitations']


def test_unknown_inventory_or_missing_price_cannot_produce_complete_total():
    inventory,quotes=fixture()
    inventory['issues'].append('UNMAPPED_BALANCE:UNKNOWN');inventory['status']='UNRESOLVED'
    assert valuation.value(inventory,quotes,quote_asset='ZUSD',as_of_ms=NOW+700)['observed_spot_mid_value'] is None
    inventory,quotes=fixture()
    for q,at,currency in [([],NOW+700,'ZUSD'),(quotes,NOW+130000,'ZUSD'),(quotes,NOW+700,'EUR')]:
        report=valuation.value(inventory,q,quote_asset=currency,as_of_ms=at)
        assert report['status']=='INCOMPLETE' and report['observed_spot_mid_value'] is None
