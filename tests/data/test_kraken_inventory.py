import pytest

from services.data import kraken_inventory as inventory
from services.data.kraken_account import assess
from tests.data.test_kraken_account import observations, NOW
from tests.data.test_kraken_units import source, SPEC


def inputs():
    account=assess(observations(),as_of_ms=NOW+500)
    spec=dict(SPEC,quote='ZUSD',altname='AAPLxUSD',pair='AAPLx/USD')
    return account,[{'spec':spec,'payload':source(),'received_at_ms':NOW+400}]


def test_existing_holdings_cash_and_orders_remain_separate_and_exact():
    account,markets=inputs()
    report=inventory.reconcile(account,markets,as_of_ms=NOW+600)
    rows={r['account_asset']:r for r in report['balances']}
    assert rows['AAPLx.T']['base_asset']=='AAPLx'
    assert rows['AAPLx.T']['base_quantity']=='2.123456789012345678'
    # Independent integer product of both decimal coefficients (34 fractional places).
    coefficient=str(2123456789012345678 * 10032690125398187)
    expected=coefficient[:-34]+'.'+coefficient[-34:]
    assert rows['AAPLx.T']['display_quantity']==expected
    assert rows['ZUSD']['role']=='QUOTE_BALANCE'
    assert rows['ZUSD']['available']=='750.00000001'
    assert report['open_orders'][0]['remaining']=='1'
    assert report['status']=='INVENTORY_MAPPED'
    assert report['net_asset_value'] is None and report['portfolio_management_ready'] is False


def test_unknown_assets_are_retained_and_ambiguous_aliases_do_not_double_count():
    account,markets=inputs()
    account['balances']['AAPLx']=dict(account['balances']['AAPLx.T'])
    account['balances']['XAUT.F']=dict(account['balances']['ZUSD'])
    report=inventory.reconcile(account,markets,as_of_ms=NOW+600)
    assert report['status']=='UNRESOLVED'
    assert len(report['balances'])==4
    assert 'AMBIGUOUS_BALANCE_ALIAS:AAPLx' in report['issues']
    assert 'UNMAPPED_BALANCE:XAUT.F' in report['issues']


def test_fresh_report_cannot_hide_old_account_sweep_or_units():
    account,markets=inputs()
    report=inventory.reconcile(account,markets,as_of_ms=NOW+120001)
    assert 'ACCOUNT_STALE' in report['issues']
    report=inventory.reconcile(account,markets,as_of_ms=NOW+121000)
    assert 'UNITS_UNAVAILABLE:AAPLx' in report['issues']


def test_rebased_or_authorized_input_cannot_be_accepted_as_base_inventory():
    account,markets=inputs();account['quantity_basis']='rebased'
    with pytest.raises(ValueError):inventory.reconcile(account,markets,as_of_ms=NOW+600)
