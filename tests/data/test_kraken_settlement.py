import json
from copy import deepcopy
import pytest
from services.data import kraken_settlement as settlement
from services.data import kraken_cash_reconciliation as cash
from services.data import kraken_order_query as orders
from services.data import kraken_trade_query as trades
from tests.data.test_kraken_cash_reconciliation import samples, NOW
from tests.data.test_kraken_order_query import read, body
from tests.data.test_kraken_trade_query import observation, trade_body
from tests.tools.test_kraken_public_capture import capture


def inputs():
    before,after,pages=samples();bridge=cash.reconcile(before,after,pages,as_of_ms=NOW+63000)
    r=read(body('canceled'));r.update(requested_at_ms=NOW+61000,received_at_ms=NOW+61020)
    order=orders.assess(r,as_of_ms=NOW+63000)
    p=trade_body();p['result']['TRADE-1']['time']=str(NOW//1000+30)
    r=observation(p);r.update(requested_at_ms=NOW+62000,received_at_ms=NOW+62020)
    fills=trades.assess([r],orders=order,as_of_ms=NOW+63000)
    return bridge,order,fills,[capture()[0]['instrument']]


def test_partial_fill_then_cancel_links_both_legs_and_cash_fee():
    args=inputs();report=settlement.link(*args,as_of_ms=NOW+63100)
    assert report['status']=='OBSERVED_SETTLEMENT_MATCHED'
    assert report['trades'][0]['actual_fees']=={'AAPLx.T':'0','ZUSD':'0.04'}
    assert report['submission_enabled'] is False and report['next_cycle_allowed'] is False


@pytest.mark.parametrize('case',['open','missing_leg','unknown_ref','wrong_amount','fee','old_fill','account'])
def test_incomplete_settlement_cannot_pass(case):
    bridge,order,fills,specs=inputs()
    if case=='open':order['orders'][0]['status']='open';order['orders'][0]['canonical_state']='PARTIALLY_FILLED'
    if case=='missing_leg':bridge['ledger_entries'].pop()
    if case=='unknown_ref':bridge['ledger_entries'][0]['refid']='UNKNOWN'
    if case=='wrong_amount':bridge['ledger_entries'][0]['amount']='99'
    if case=='fee':fills['trades'][0]['fee']='1'
    if case=='old_fill':fills['trades'][0]['time_seconds']=str(NOW//1000-1)
    if case=='account':fills['account_ref_sha256']='b'*64
    try:report=settlement.link(bridge,order,fills,specs,as_of_ms=NOW+63100)
    except ValueError:return
    assert report['status']=='UNRESOLVED'
