"""Observed finite brackets and configured leverage must survive Demo import."""
from copy import deepcopy
from decimal import Decimal, localcontext

import pytest

from services.research.demo_origin_contract import contract_from_origin
from services.research.demo_opening_projection import contract_spec
from tests.tools.test_binance_demo_execution_origin import collect


def test_technical_contract_keeps_finite_table_and_selected_leverage(tmp_path):
    origin,_=collect(tmp_path)
    for r in origin['reads']:
        if r['kind'] in ('symbols_before','symbols_after'):
            next(s for s in r['data'] if s['symbol']=='BTCUSDC')['leverage']=3
    before=deepcopy(origin)
    with localcontext() as c:
        c.prec=4
        raw,terms=contract_from_origin(origin)
    spec=contract_spec(raw)
    assert spec.margin_brackets[0].initial_margin_rate==Decimal('0.333333333333333333333334')
    assert spec.new_risk_notional_cap==Decimal('10000') and spec.margin_brackets[-1].notional_cap==Decimal('10000')
    assert spec.settlement_quantum==Decimal('0.00000001') and spec.contract_multiplier==1
    assert terms['settlement_rounding_verified'] is False and terms['symbol_config_cap_verified'] is False
    assert terms['settlement_policy']=='EXPLICIT_DEMO_QUOTE_QUANTUM_1E8_V1'
    assert spec.as_canonical_dict()==raw and origin==before


@pytest.mark.parametrize('failure',['coefficient','leverage','unknown_mode','nonuniform_grid','bracket_gap'])
def test_unproven_or_unrepresentable_terms_are_not_replaced_by_default_brackets(tmp_path, failure):
    origin,_=collect(tmp_path)
    if failure=='coefficient':next(r for r in origin['reads'] if r['kind']=='brackets')['data'][0]['notionalCoef']='2'
    elif failure=='leverage':
        for r in origin['reads']:
            if r['kind'] in ('symbols_before','symbols_after'):next(s for s in r['data'] if s['symbol']=='BTCUSDC')['leverage']=100
    elif failure=='unknown_mode':
        for r in origin['reads']:
            if r['kind'] in ('symbols_before','symbols_after'):next(s for s in r['data'] if s['symbol']=='BTCUSDC')['marginType']='ISOLATED'
    elif failure=='bracket_gap':next(r for r in origin['reads'] if r['kind']=='brackets')['data'][0]['brackets'][0]['notionalFloor']='1'
    else:
        rules=next(r for r in origin['reads'] if r['kind']=='rules')['data']
        for key in ('venue_filters',):
            next(r for r in rules[key] if r['filterType']=='LOT_SIZE').update(minQty='0.002',stepSize='0.003')
        next(r for r in rules['terms']['filters'] if r['filterType']=='LOT_SIZE').update(minQty='0.002',stepSize='0.003')
    with pytest.raises(ValueError):contract_from_origin(origin)
