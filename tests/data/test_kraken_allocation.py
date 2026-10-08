from copy import deepcopy

import pytest

from services.portfolio import kraken_allocation as allocation
from services.portfolio.kraken_valuation import value
from tests.data.test_kraken_valuation import fixture
from tests.data.test_kraken_inventory import NOW

KEY='KRAKEN:tokenized_asset:AAPLxUSD'


def inputs():
    inventory,quotes=fixture()
    inventory['open_orders']=[]
    inventory['planning_quotes']=quotes
    inventory['indicative_valuation']=value(inventory,quotes,quote_asset='ZUSD',as_of_ms=NOW+700)
    policy={'schema':'KRAKEN_ALLOCATION_POLICY_V1','account_ref_sha256':'a'*64,'quote_asset':'ZUSD',
        'capital_limit':'500','cash_reserve':'100','max_asset_weight':'0.5','target_weights':{KEY:'0.4'}}
    return inventory,policy


def test_explicit_budget_caps_targets_and_does_not_spend_locked_cash():
    inventory,policy=inputs()
    result=allocation.prepare(inventory,policy,as_of_ms=NOW+800)
    assert result['status']=='REVIEW_ONLY'
    assert result['allocation_budget']=='500'
    assert result['cash_available_after_reserve']=='650.00000001'
    assert result['targets'][0]['target_value']=='200'
    assert result['targets'][0]['side']=='REDUCE'
    assert result['submission_enabled'] is False


@pytest.mark.parametrize('change',['account','weight','sum','missing','unknown'])
def test_invalid_policy_has_no_implicit_defaults(change):
    inventory,policy=inputs()
    if change=='account':policy['account_ref_sha256']='b'*64
    if change=='weight':policy['target_weights'][KEY]='0.6'
    if change=='sum':policy['max_asset_weight']='1';policy['target_weights'].update({KEY:'0.8','KRAKEN:currency:XAUTUSD':'0.8'})
    if change=='missing':policy.pop('capital_limit')
    if change=='unknown':policy['live_enabled']=True
    with pytest.raises(ValueError):allocation.prepare(inventory,policy,as_of_ms=NOW+800)


def test_orders_staleness_or_unknown_valuation_block_targets():
    inventory,policy=inputs()
    for change in ('orders','stale','valuation'):
        item=deepcopy(inventory);at=NOW+800
        if change=='orders':item['open_orders']=[{'order_id':'pending'}]
        if change=='stale':at=NOW+130000
        if change=='valuation':item['indicative_valuation']['status']='INCOMPLETE'
        result=allocation.prepare(item,policy,as_of_ms=at)
        assert result['status']=='BLOCKED' and result['targets']==[]


def test_credit_is_not_owned_cash_and_unknown_target_not_assumed_empty():
    inventory,policy=inputs()
    cash=next(r for r in inventory['balances'] if r['role']=='QUOTE_BALANCE')
    cash.update(credit='10000',available='10750.00000001')
    report=allocation.prepare(inventory,policy,as_of_ms=NOW+800)
    assert report['cash_available_after_reserve']=='650.00000001'
    policy['target_weights']={'KRAKEN:currency:UNKNOWNUSD':'0.4'}
    result=allocation.prepare(inventory,policy,as_of_ms=NOW+800)
    assert result['status']=='BLOCKED' and result['targets']==[]


def test_new_holding_requires_fresh_product_quote_and_reduction_respects_locked_units():
    inventory,policy=inputs()
    new='KRAKEN:currency:XAUTUSD'
    quote=deepcopy(inventory['planning_quotes'][0]);quote['instrument_id']=new;quote['base']='XAUT'
    inventory['planning_quotes'].append(quote)
    policy['target_weights']={new:'0.4'}
    report=allocation.prepare(inventory,policy,as_of_ms=NOW+800)
    assert report['status']=='REVIEW_ONLY' and report['targets'][0]['current_value']=='0'
    inventory,policy=inputs();policy['target_weights'][KEY]='0'
    report=allocation.prepare(inventory,policy,as_of_ms=NOW+800)
    assert report['status']=='BLOCKED' and report['targets']==[]
    assert 'REDUCTION_EXCEEDS_AVAILABLE_HOLDING:'+KEY in report['issues']
