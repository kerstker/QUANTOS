"""tools/binance_readonly_smoke.py(2026-07 Binance mainnet 接线)——假 transport 全注入,
零真网(镜像 tests/tools/test_mexc_readonly_smoke.py)。

钉死:四个只读检查的形状校验逻辑(server_time/exchange_info/premium_index/account)+
verdict 汇总 + 证据文件形状(schema_version/provenance/order_response_shape=
UNVERIFIED_UNTIL_FIRST_FILL)+ **绝不触碰下单端点**(记录全部请求,断言全 GET、
URL 不含 /order)。本工具绝不实例化 gated 适配器、绝不下单。
"""
import json

from tools.binance_readonly_smoke import run_smoke

_NOW = 1_700_000_000_000
_CLOCK = lambda: _NOW / 1000.0  # noqa: E731

_EXCHANGE_INFO = {"symbols": [{
    "symbol": "BTCUSDT",
    "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
        {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"},
    ],
}]}
_PREMIUM = {"symbol": "BTCUSDT", "markPrice": "60000.5", "indexPrice": "60000.1",
            "lastFundingRate": "0.0001", "nextFundingTime": _NOW + 28_800_000, "time": _NOW}
_ACCOUNT = {"availableBalance": "1000.0", "totalMarginBalance": "1200.0",
            "totalMaintMargin": "0.0",
            "positions": [{"symbol": "BTCUSDT", "positionAmt": "0.001", "entryPrice": "59000.0"},
                          {"symbol": "ETHUSDT", "positionAmt": "0", "entryPrice": "0"}]}


def _http_get(mapping, record):
    def http_get(url, headers):
        record.append(("GET", url))
        for prefix, payload in mapping.items():
            if prefix in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected url {url}")
    return http_get


def _http_account(payload, record, status=200):
    def http(method, url, *, body=None, headers=None):
        record.append((method, url))
        return status, json.dumps(payload).encode()
    return http


def _public_mapping(**over):
    mapping = {"/fapi/v1/time": {"serverTime": _NOW},
               "/fapi/v1/exchangeInfo": _EXCHANGE_INFO,
               "/fapi/v1/premiumIndex": _PREMIUM}
    mapping.update(over)
    return mapping


def _run(record=None, **over):
    rec = record if record is not None else []
    kwargs = dict(
        http_get=_http_get(_public_mapping(), rec),
        http_request=_http_account(_ACCOUNT, rec),
        clock=_CLOCK, sleep=lambda _s: None, now_ms=_NOW,
    )
    kwargs.update(over)
    return run_smoke("k", "s", **kwargs), rec


def test_all_pass_verdict_and_evidence_shape():
    result, _rec = _run()
    assert result["verdict"] == "PASS"
    assert [c["status"] for c in result["checks"]] == ["PASS"] * 4
    assert result["provenance"] == "REAL_BINANCE_MAINNET_READONLY_SMOKE"
    assert result["schema_version"] == 1
    # 下单响应形状只有真实首单能验证——诚实标注,绝不冒充已验
    assert result["order_response_shape"] == "UNVERIFIED_UNTIL_FIRST_FILL"
    names = [c["name"] for c in result["checks"]]
    assert names == ["server_time", "exchange_info", "premium_index", "account"]


def test_never_touches_order_endpoints():
    # 全部请求必须是 GET,且任何 URL 都不含下单/撤单端点——只读首验的结构性保证
    _result, rec = _run(record=[])
    assert rec, "expected recorded calls"
    for method, url in rec:
        assert method == "GET"
        assert "/fapi/v1/order" not in url
        assert "/order?" not in url


def test_bad_exchange_info_fails():
    broken = {"symbols": [{"symbol": "BTCUSDT",
                           "filters": [{"filterType": "LOT_SIZE", "minQty": "x"}]}]}
    result, _rec = _run(http_get=_http_get(_public_mapping(**{"/fapi/v1/exchangeInfo": broken}),
                                           []))
    assert result["verdict"] == "FAIL"
    exch = [c for c in result["checks"] if c["name"] == "exchange_info"][0]
    assert exch["status"] == "FAIL"


def test_server_time_shape_enforced():
    result, _rec = _run(http_get=_http_get(
        _public_mapping(**{"/fapi/v1/time": {"serverTime": "not-an-int"}}), []))
    assert result["verdict"] == "FAIL"
    st = [c for c in result["checks"] if c["name"] == "server_time"][0]
    assert st["status"] == "FAIL"


def test_premium_index_missing_fields_fails():
    bad = {"symbol": "BTCUSDT", "markPrice": "60000.5"}   # 缺 indexPrice/funding/时间字段
    result, _rec = _run(http_get=_http_get(_public_mapping(**{"/fapi/v1/premiumIndex": bad}),
                                           []))
    assert result["verdict"] == "FAIL"
    pi = [c for c in result["checks"] if c["name"] == "premium_index"][0]
    assert pi["status"] == "FAIL"


def test_account_missing_balance_fields_fails():
    result, _rec = _run(http_request=_http_account({"positions": []}, []))
    assert result["verdict"] == "FAIL"
    account = [c for c in result["checks"] if c["name"] == "account"][0]
    assert account["status"] == "FAIL"


def test_account_bad_position_row_fails():
    bad = dict(_ACCOUNT, positions=[{"symbol": "BTCUSDT", "positionAmt": "not-a-number"}])
    result, _rec = _run(http_request=_http_account(bad, []))
    assert result["verdict"] == "FAIL"


def test_account_non_2xx_degrades_to_fail_not_crash():
    result, _rec = _run(http_request=_http_account({}, [], status=401))
    assert result["verdict"] == "FAIL"
    account = [c for c in result["checks"] if c["name"] == "account"][0]
    assert account["status"] == "FAIL"


def test_network_failure_degrades_to_fail_not_crash():
    result, _rec = _run(http_get=_http_get({"/fapi/v1/time": ConnectionError("offline")}, []))
    assert result["verdict"] == "FAIL"
    assert all(c["status"] == "FAIL" for c in result["checks"][:3])


def test_signature_is_present_and_secret_never_in_plaintext():
    captured = {}

    def http(method, url, *, body=None, headers=None):
        captured["url"] = url
        captured["headers"] = headers
        return 200, json.dumps(_ACCOUNT).encode()
    run_smoke("k", "topsecretvalue",
              http_get=_http_get(_public_mapping(), []),
              http_request=http, clock=_CLOCK, sleep=lambda _s: None, now_ms=_NOW)
    assert "signature=" in captured["url"]
    assert "timestamp=" in captured["url"]
    assert captured["headers"]["X-MBX-APIKEY"] == "k"
    assert "topsecretvalue" not in captured["url"]    # secret 只进 HMAC,绝不进明文
