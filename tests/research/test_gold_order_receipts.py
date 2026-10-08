"""Explicit gold native versions preserve exact receipt and clock semantics."""
from copy import deepcopy
import json
import pytest
from services.research.binance_order_receipts import normalize_order_receipts
from tests.research.test_binance_order_receipts import v2_spec,ACTIONS,HASH,receipt
from tests.research.test_binance_order_receipt_clocks import native_v3,ACTIONS_V3


@pytest.mark.parametrize('clocked',[False,True])
def test_gold_version_binds_market_options_costs_and_time(clocked):
    raw=native_v3() if clocked else v2_spec()['raw_observed']
    old_schema=raw['schema']
    raw['schema']='BINANCE_USDM_GOLD_ORDER_RECEIPTS_V3' if clocked else 'BINANCE_USDM_GOLD_ORDER_RECEIPTS_V2'
    raw['symbol']='XAUUSDT'
    for i,row in enumerate(raw['messages']):
        payload=json.loads(row['raw_utf8']);payload['o']['s']='XAUUSDT'
        if 'N' in payload['o']:payload['o']['N']='USDT'
        raw['messages'][i]=receipt(payload,row['received_at_ms'])
    actions=ACTIONS_V3 if clocked else ACTIONS
    before=deepcopy(raw)
    observed,evidence=normalize_order_receipts(raw,actions,'XAUUSDT',HASH)
    from services.research.passive_receipt_calibration import validate_observed_trace,validate_calibration
    from types import SimpleNamespace
    validate_observed_trace(observed,actions,'XAUUSDT',HASH)
    with pytest.raises(ValueError):
        validate_calibration({'schema':'PASSIVE_BINANCE_ORDER_RECEIPTS_V1','raw_observed':raw,'cutoff_ms':150},
            SimpleNamespace(symbol='XAUUSDT'),actions,HASH)
    assert raw==before and evidence['trades_count']==2
    assert evidence['reported_quote_commission']=='0.0198'
    assert not evidence['source_verified'] and evidence['actual_paid_fees'] is None
    if clocked:
        assert evidence['clock']['exact_latency_ms'] is None
        assert evidence['clock']['available_at_ms']==10320
    for mutation in ('old_schema','btc','missing_option','wrong_client','clock'):
        value=deepcopy(raw)
        if mutation=='old_schema':value['schema']=old_schema
        elif mutation=='btc':value['symbol']='BTCUSDT'
        elif mutation=='missing_option':del value['bindings'][0]['reduce_only']
        elif mutation=='wrong_client':value['bindings'][0]['client_order_id']='other-client'
        elif clocked:del value['clock_bracket']
        else:value['clock_bracket']={}
        with pytest.raises(ValueError):normalize_order_receipts(value,actions,value['symbol'],HASH)
