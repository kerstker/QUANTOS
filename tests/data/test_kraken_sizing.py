from copy import deepcopy
from decimal import Decimal, localcontext
import pytest

from services.portfolio.kraken_sizing import prepare
from services.data.kraken_units import asset_units
from tests.data.test_kraken_allocation import inputs, KEY
from tests.data.test_kraken_units import source, NOW
from tests.tools.test_kraken_public_capture import capture


def fixture():
    inventory,policy=inputs();spec=capture()[0]['instrument']
    inventory['instrument_specs']=[spec]
    inventory['planning_units']=[asset_units(source(),spec=spec,received_at_ms=NOW+400,as_of_ms=NOW+800)]
    return inventory,policy


def test_exact_stock_reduction_keeps_base_and_equity_quantities_separate():
    inventory,policy=fixture()
    with localcontext() as ctx:
        ctx.prec=6
        result=prepare(inventory,policy,as_of_ms=NOW+800)
    row=result['quantities'][0]
    assert result['status']=='INDICATIVE_QUANTITIES'
    assert row['side']=='sell'
    assert Decimal(row['base_quantity'])>0
    assert row['display_quantity']!=row['base_quantity']
    assert Decimal(row['residual_target_value'])<=0
    assert result['submission_enabled'] is False and result['drafts']==[]
    assert 'ORDER_QUANTITY_SEMANTICS_UNVERIFIED' in result['blockers']


def test_missing_or_stale_metadata_never_defaults_multiplier_to_one():
    inventory,policy=fixture()
    for mutate in ('missing','stale','basis'):
        item=deepcopy(inventory)
        if mutate=='missing':item['planning_units']=[]
        if mutate=='stale':item['planning_units'][0]['received_at_ms']=NOW-130000
        if mutate=='basis':item['planning_units'][0]['display_basis']='NATIVE_ASSET'
        result=prepare(item,policy,as_of_ms=NOW+800)
        assert result['status']=='BLOCKED' and result['quantities']==[]


def test_new_position_uses_catalog_units_without_requiring_existing_holding():
    inventory,policy=fixture()
    inventory['balances']=[r for r in inventory['balances'] if r['role']!='BASE_ASSET']
    inventory['indicative_valuation']['rows']=[r for r in inventory['indicative_valuation']['rows'] if r['account_asset']=='ZUSD']
    result=prepare(inventory,policy,as_of_ms=NOW+800)
    row=result['quantities'][0]
    assert row['side']=='buy' and row['base_quantity']!='0'
    assert Decimal(row['residual_target_value'])>=0
    assert result['indicative_buy_cost_before_fees']==row['indicative_quote_notional']


def test_native_asset_sizing_has_no_equity_multiplier():
    inventory,policy=fixture();spec=inventory['instrument_specs'][0]
    spec.update(instrument_id='KRAKEN:currency:XAUTUSD',asset_class='currency',base='XAUT')
    key=spec['instrument_id'];policy['target_weights']={key:'0.4'}
    inventory['balances']=[r for r in inventory['balances'] if r['role']!='BASE_ASSET']
    inventory['indicative_valuation']['rows']=[r for r in inventory['indicative_valuation']['rows'] if r['account_asset']=='ZUSD']
    inventory['planning_quotes'][0].update(instrument_id=key,base='XAUT',price_quantity_basis='NATIVE_ASSET',basis_evidence='SPOT_PAIR_BASE')
    inventory['planning_units']=[asset_units({'error':[],'result':{'XAUT':{'aclass':'currency','status':'enabled',
        'decimals':8,'display_decimals':8}}},spec=spec,received_at_ms=NOW+400,as_of_ms=NOW+800)]
    result=prepare(inventory,policy,as_of_ms=NOW+800)
    assert result['quantities'][0]['base_quantity']==result['quantities'][0]['display_quantity']


def test_locked_reduction_remains_blocked():
    inventory,policy=fixture();policy['target_weights'][KEY]='0'
    result=prepare(inventory,policy,as_of_ms=NOW+800)
    assert result['status']=='BLOCKED' and result['quantities']==[]


@pytest.mark.parametrize('budget,expected',[('1.000000019999999999','0.33333333'),('1.00000002','0.33333334')])
def test_rational_rounding_never_crosses_a_lot_boundary(budget,expected):
    inventory,policy=fixture()
    inventory['balances']=[r for r in inventory['balances'] if r['role']!='BASE_ASSET']
    inventory['indicative_valuation']['rows']=[r for r in inventory['indicative_valuation']['rows'] if r['account_asset']=='ZUSD']
    inventory['planning_quotes'][0].update(bid='1',ask='1',mid='1')
    inventory['planning_units'][0]['token_multiplier']='3'
    policy.update(capital_limit=budget,max_asset_weight='1',target_weights={KEY:'1'})
    with localcontext() as ctx:
        ctx.prec=6
        result=prepare(inventory,policy,as_of_ms=NOW+800)
    assert result['quantities'][0]['base_quantity']==expected


def test_ask_cost_can_exceed_mid_budget_without_crediting_planned_sales():
    inventory,policy=fixture()
    inventory['balances']=[r for r in inventory['balances'] if r['role']!='BASE_ASSET']
    inventory['indicative_valuation']['rows']=[r for r in inventory['indicative_valuation']['rows'] if r['account_asset']=='ZUSD']
    inventory['planning_quotes'][0].update(bid='99',ask='101',mid='100')
    policy.update(capital_limit='1000',max_asset_weight='1',target_weights={KEY:'1'})
    result=prepare(inventory,policy,as_of_ms=NOW+800)
    assert result['allocation_review']['requires_completed_sales_before_buys'] is False
    assert result['requires_settled_cash_before_buys'] is True
    assert Decimal(result['indicative_buy_cost_before_fees'])>Decimal(result['allocation_review']['cash_available_after_reserve'])
