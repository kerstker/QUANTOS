"""Gold account observations reuse account checks without granting execution."""
import json
from copy import deepcopy

import pytest

from services.research import demo_execution_origin as model
from tests.tools.test_binance_demo_execution_origin import NOW, transport
from tools import binance_demo_execution_origin as tool


def capture(**options):
    original, calls = transport(**options)

    def get(path, **kwargs):
        body = original(path, **kwargs).decode()
        body = body.replace('USDC', 'TEMPQUOTE').replace('USDT', 'USDC')
        body = body.replace('TEMPQUOTE', 'USDT').replace('BTCUSDT', 'XAUUSDT')
        data = json.loads(body)
        if path == '/fapi/v1/exchangeInfo':
            data['symbols'][0].update(baseAsset='XAU', contractType='TRADIFI_PERPETUAL')
        return json.dumps(data).encode()

    raw = tool.collect(credential_file=None, symbol='XAUUSDT', operator_read=True,
                       credential_reader=lambda _: ('synthetic-key', 'synthetic-secret'),
                       get=get, clock_ms=lambda: NOW)
    return raw, calls


def test_gold_account_capture_retains_rules_and_account_binding(tmp_path):
    raw, calls = capture()
    assert raw['status'] == 'CAPTURED' and len(calls) == len(model.KINDS)
    assert raw['schema'] == 'DEMO_GOLD_EXECUTION_ORIGIN_V1'
    rules = next(r['data'] for r in raw['reads'] if r['kind'] == 'rules')
    assert rules['base_asset'] == 'XAU'
    assert rules['terms']['contractType'] == 'TRADIFI_PERPETUAL'
    report = model.assess(raw, as_of_ms=NOW)
    assert report['status'] == 'OBSERVED_FLAT_QUOTE'
    assert report['quote_asset'] == 'USDT' and report['wallet'] == '5000'
    assert report['other_quote_positions'] == 1
    assert report['target_configuration']['status'] == 'MATCHED_OBSERVATIONS'
    assert not report['submission_enabled'] and not report['source_verified']
    assert model.load(model.save(tmp_path, raw)) == raw


@pytest.mark.parametrize('options,reason', [
    ({'conditional': True}, 'PREEXISTING_ALGO_ORDERS'),
    ({'isolated': True}, 'TARGET_CROSS_MARGIN_REQUIRED'),
])
def test_gold_keeps_original_account_risk_checks(options, reason):
    raw, _ = capture(**options)
    assert reason in model.assess(raw, as_of_ms=NOW)['issues']


def test_gold_cannot_relabel_btc_schema_or_assume_underlying():
    raw, _ = capture()
    for schema in (model.SCHEMA, model.TERMS_SCHEMA, model.FULL_TERMS_SCHEMA, model.ACCOUNT_TERMS_SCHEMA):
        bad = deepcopy(raw)
        bad['schema'] = schema
        with pytest.raises(ValueError, match='ORIGIN_SCHEMA'):
            model.validate(bad)
    rules = next(r['data'] for r in raw['reads'] if r['kind'] == 'rules')
    rules['base_asset'] = 'BTC'
    assert 'TARGET_RULES_UNAVAILABLE' in model.assess(raw, as_of_ms=NOW)['issues']
    rules['base_asset'] = 'XAU'
    rules['terms']['contractType'] = 'PERPETUAL'
    assert 'TARGET_RULES_UNAVAILABLE' in model.assess(raw, as_of_ms=NOW)['issues']


def test_gold_offline_path_never_reads_credentials_or_network():
    def forbidden(*args, **kwargs):
        pytest.fail('private access or network')
    raw = tool.collect(credential_file=None, symbol='XAUUSDT',
                       credential_reader=forbidden, get=forbidden)
    assert raw['status'] == 'NOT_REQUESTED' and raw['reads'] == []


def test_gold_contract_projection_keeps_gold_identity_and_cap():
    from services.research.demo_origin_contract import contract_from_origin
    raw, _ = capture()
    contract, terms = contract_from_origin(raw)
    assert terms['schema'] == 'DEMO_GOLD_ORIGIN_CONTRACT_PROJECTION_V1'
    assert terms['base_asset'] == 'XAU'
    assert terms['venue_contract_type'] == 'TRADIFI_PERPETUAL'
    assert terms['symbol_config_notional_cap'] == '10000'
    assert contract['instrument_id'] == 'XAUUSDT'
    assert terms['contract_multiplier_verified'] is False
    assert not terms['authoritative'] and not terms['submission_enabled']


def test_gold_public_transport_keeps_fixed_get_and_rejects_private_headers(monkeypatch):
    from tests.tools.test_demo_origin_compression import response
    calls = response(monkeypatch, b'{}', None)
    assert tool.get_response('/fapi/v1/premiumIndex', params='symbol=XAUUSDT', headers={}) == b'{}'
    request = calls[0][0]
    assert request.method == 'GET'
    assert request.full_url == 'https://demo-fapi.binance.com/fapi/v1/premiumIndex?symbol=XAUUSDT'
    with pytest.raises(tool.ProbeFailure):
        tool.get_response('/fapi/v1/premiumIndex', params='symbol=XAUUSDT',
                          headers={'X-MBX-APIKEY': 'synthetic'})
    assert len(calls) == 1
