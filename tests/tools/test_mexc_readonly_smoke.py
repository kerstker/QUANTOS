"""tools/mexc_readonly_smoke.py(2026-07 MEXC 对接 1.6)——假 transport 全注入,零真网。

钉死:三个只读检查的形状校验逻辑(server_time/exchangeInfo/account)+ verdict 汇总 +
证据文件形状(schema_version/provenance/order_response_shape=UNVERIFIED_UNTIL_FIRST_FILL)。
本工具绝不实例化 gated 适配器、绝不下单;字段校验是适配器假设的诚实镜像。
"""
import json

from tools.mexc_readonly_smoke import run_smoke

_NOW = 1_700_000_000_000
_CLOCK = lambda: _NOW / 1000.0  # noqa: E731

_EXCHANGE_INFO = {"symbols": [{
    "symbol": "BTCUSDT", "baseAsset": "BTC",
    "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
        {"filterType": "LOT_SIZE", "minQty": "0.00001", "stepSize": "0.00001"},
        {"filterType": "MIN_NOTIONAL", "notional": "5"},
    ],
}]}
_ACCOUNT = {"canTrade": True, "canWithdraw": False, "balances": [
    {"asset": "BTC", "free": "0.001", "locked": "0"},
    {"asset": "USDT", "free": "50.0", "locked": "0"},
]}


def _http_get(mapping):
    def http_get(url, headers):
        for prefix, payload in mapping.items():
            if prefix in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected url {url}")
    return http_get


def _http_account(payload):
    def http(method, url, *, body=None, headers=None):
        return 200, json.dumps(payload).encode()
    return http


def _run(**over):
    kwargs = dict(
        http_get=_http_get({"/api/v3/time": {"serverTime": _NOW},
                            "/api/v3/exchangeInfo": _EXCHANGE_INFO}),
        http_request=_http_account(_ACCOUNT),
        clock=_CLOCK, sleep=lambda _s: None, now_ms=_NOW,
    )
    kwargs.update(over)
    return run_smoke("k", "s", **kwargs)


def test_all_pass_verdict_and_evidence_shape():
    result = _run()
    assert result["verdict"] == "PASS"
    assert [c["status"] for c in result["checks"]] == ["PASS", "PASS", "PASS"]
    assert result["provenance"] == "REAL_MEXC_MAINNET_READONLY_SMOKE"
    assert result["schema_version"] == 1
    # 下单响应形状只有真实首单能验证——诚实标注,绝不冒充已验
    assert result["order_response_shape"] == "UNVERIFIED_UNTIL_FIRST_FILL"
    names = [c["name"] for c in result["checks"]]
    assert names == ["server_time", "exchange_info", "account"]


def test_withdraw_enabled_fails_closed():
    result = _run(http_request=_http_account(dict(_ACCOUNT, canWithdraw=True)))
    assert result["verdict"] == "FAIL"
    account = [c for c in result["checks"] if c["name"] == "account"][0]
    assert account["status"] == "FAIL" and "canWithdraw=true" in account["detail"]


def test_missing_permission_fields_fail():
    result = _run(http_request=_http_account({"balances": []}))
    assert result["verdict"] == "FAIL"


def test_bad_exchange_info_fails():
    broken = {"symbols": [{"symbol": "BTCUSDT", "baseAsset": "BTC",
                           "filters": [{"filterType": "LOT_SIZE", "minQty": "x"}]}]}
    result = _run(http_get=_http_get({"/api/v3/time": {"serverTime": _NOW},
                                      "/api/v3/exchangeInfo": broken}))
    assert result["verdict"] == "FAIL"
    exch = [c for c in result["checks"] if c["name"] == "exchange_info"][0]
    assert exch["status"] == "FAIL"


def test_server_time_shape_enforced():
    result = _run(http_get=_http_get({"/api/v3/time": {"serverTime": "not-an-int"},
                                      "/api/v3/exchangeInfo": _EXCHANGE_INFO}))
    assert result["verdict"] == "FAIL"
    st = [c for c in result["checks"] if c["name"] == "server_time"][0]
    assert st["status"] == "FAIL"


def test_network_failure_degrades_to_fail_not_crash():
    result = _run(http_get=_http_get({"/api/v3/time": ConnectionError("offline")}))
    assert result["verdict"] == "FAIL"
    assert all(c["status"] == "FAIL" for c in result["checks"][:2])
