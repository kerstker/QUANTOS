from copy import deepcopy
import json

import pytest

from tools import binance_demo_market_inputs as tool
from services.research import demo_market_inputs as model
from tests.tools.test_binance_demo_market_inputs import material, NOW


def capture():
    document = material()
    document['ticker']['symbol'] = 'XAUUSDT'
    ticks = iter(range(NOW, NOW+100))
    calls = []
    def get(kind, *, symbol, end_ms=None):
        calls.append((kind, symbol))
        value = {'serverTime': NOW+(6 if kind == 'clock_after' else 0)} if kind.startswith('clock') else document[kind]
        return json.dumps(value, separators=(',', ':')).encode()
    return tool.collect(symbol='XAUUSDT', get=get, clock_ms=lambda: next(ticks)), calls


def test_gold_demo_observation_uses_explicit_scope_and_original_storage(tmp_path):
    raw, calls = capture()
    assert raw['schema'] == 'DEMO_GOLD_MARKET_INPUTS_V1'
    assert raw['status'] == 'CAPTURED'
    assert [symbol for _, symbol in calls] == ['XAUUSDT']*4
    assessment = model.assess(raw, as_of_ms=NOW+10)
    assert assessment['daily_volume_base'] == '123.456'
    assert assessment['closed_bars'] == 61
    assert assessment['schema'] == raw['schema']
    assert assessment['source_verified'] is assessment['submission_enabled'] is False
    assert model.display(raw)['schema'] == 'DEMO_GOLD_MARKET_INPUTS_VIEW_V1'
    model.save(tmp_path/'gold', raw)
    assert model.load_latest(tmp_path/'gold') == raw


def test_gold_cannot_be_relabelled_as_btc_protocol_or_ticker():
    raw, _ = capture()
    changed = deepcopy(raw)
    changed['schema'] = model.SCHEMA
    with pytest.raises(ValueError, match='SCOPE'):
        model.validate(changed)
    changed = deepcopy(raw)
    changed['symbol'] = 'BTCUSDT'
    with pytest.raises(ValueError, match='SCOPE'):
        model.validate(changed)
    assert model.assess(raw, as_of_ms=NOW+40000)['status'] == 'STALE'


def test_gold_transport_remains_fixed_public_demo_get():
    requests = []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, maximum): return b'{}'
    class Opener:
        def open(self, request, *, timeout):
            requests.append(request)
            return Response()
    tool.get_response('ticker', symbol='XAUUSDT', opener=Opener())
    assert requests[0].full_url == 'https://demo-fapi.binance.com/fapi/v1/ticker/24hr?symbol=XAUUSDT'
    assert requests[0].method == 'GET' and not requests[0].headers
    with pytest.raises(ValueError):
        tool.get_response('orders', symbol='XAUUSDT', opener=Opener())


def test_observation_support_does_not_expand_v1_submission_scope():
    from services.validation.demo_submission import validate_orders, DemoSubmissionRejected
    order = {'symbol': 'XAUUSDT', 'side': 'BUY', 'type': 'LIMIT', 'timeInForce': 'GTX',
             'quantity': '0.01', 'price': '4000', 'reduceOnly': False, 'positionSide': 'BOTH',
             'newClientOrderId': 'QDtestgold0001', 'newOrderRespType': 'ACK'}
    with pytest.raises(DemoSubmissionRejected, match='ORDER_SCOPE'):
        validate_orders([order])
