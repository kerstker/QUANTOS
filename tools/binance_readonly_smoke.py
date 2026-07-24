"""Binance USDT-M 期货 mainnet 只读首验工具(**人工执行**;runbook
docs/runbooks/binance-futures-live-runbook.md 步骤3,镜像 tools/mexc_readonly_smoke.py)。

四个只读调用(全部 GET,**绝不下单**),校验真实 mainnet 响应字段形状与
live/exchange/binance_futures.py 的适配器假设一致:

1. `GET /fapi/v1/time`(公开,无签名)——校时源:`serverTime` 字段存在且为正整数毫秒;
2. `GET /fapi/v1/exchangeInfo`(公开)——交易所过滤器:BTCUSDT 的 LOT_SIZE(stepSize/
   minQty)、MIN_NOTIONAL(notional)、PRICE_FILTER(tickSize)可按
   `BinanceFuturesAdapter.exchange_filters` 的假设解析;
3. `GET /fapi/v1/premiumIndex?symbol=BTCUSDT`(公开)——markPrice/indexPrice/
   lastFundingRate/nextFundingTime/time 可按 `BinanceFuturesAdapter.premium_index`
   的假设解析(资金费信号策略 carry_oi_majors 的实时交叉核对证据源);
4. `GET /fapi/v2/account`(签名,**只读**)——availableBalance/totalMarginBalance/
   totalMaintMargin 可解析 float + positions[] 行形状(symbol/positionAmt/entryPrice)
   可按 `BinanceFuturesAdapter.account/positions` 的假设解析。凭证经
   `tools/verify_key_permissions._load_mainnet_credentials("binance")` 加载
   (BINANCE_MAINNET_API_KEY/SECRET env 或 key/binance_mainnet_key.json),绝不打印/落盘。

产出 `evidence/binance_mainnet_smoke.json`:逐项 PASS/FAIL + 非密钥字段盘点 + 总 verdict。
**不实例化 BinanceFuturesAdapter**(mainnet 构造闸门不碰——与
tools/verify_key_permissions.py 同一「ungated 纯函数镜像」设计:签名复用该模块的
`_binance_sign_query`,transport 复用 binance_futures.py 的模块级 `_default_http_request`)。
权限位(enableWithdrawals/ipRestrict)**不在** /fapi 响应里——那是
`python -m tools.verify_key_permissions --venue binance`(apiRestrictions 端点)的职责,
本工具不重复。下单响应形状(orderId/executedQty/avgPrice)**不在**本工具校验范围——
那要等人工复审的 ≤$100 首单,证据里诚实标注 `UNVERIFIED_UNTIL_FIRST_FILL`。

人工运行(AI 绝不运行本工具,绝不触碰真实凭证):

    python -m tools.binance_readonly_smoke
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable, Optional

from live.exchange.binance_futures import BinanceFuturesAdapter
from live.exchange.binance_futures import _default_http_request
from live.exchange.binance_futures import _is_rate_limited
from live.exchange.retry import call_with_backoff
from tools.cache_io import atomic_write_json
from tools.coingecko_fetch import _http_get_json
from tools.verify_key_permissions import _binance_sign_query, _load_mainnet_credentials

_MAINNET_BASE = BinanceFuturesAdapter.MAINNET_BASE     # 类属性静态访问,不实例化、不碰构造闸门
_ACCOUNT_PATH = "/fapi/v2/account"
EVIDENCE_PATH = os.path.join("evidence", "binance_mainnet_smoke.json")
# 诊断性只读调用,给比生产 5000ms 更宽松的签名窗口(与 verify_key_permissions 同款考量)。
_DEFAULT_RECV_WINDOW_MS = 10_000


def _check(name: str, ok: bool, detail: str, **fields) -> dict:
    return {"name": name, "status": "PASS" if ok else "FAIL", "detail": detail, **fields}


def _public_get(path: str, http_get: Callable):
    payload = http_get(_MAINNET_BASE + path, {"accept": "application/json"})
    if isinstance(payload, dict) and "code" in payload:      # Binance 错误体 {"code":..., "msg":...}
        raise RuntimeError(f"Binance error {payload.get('code')}: {payload.get('msg')}")
    return payload


def fetch_binance_futures_account(api_key: str, api_secret: str, *,
                                  http_request: Optional[Callable] = None,
                                  clock: Optional[Callable[[], float]] = None,
                                  sleep: Optional[Callable[[float], None]] = None,
                                  max_retries: int = 2, retry_base_delay: float = 1.0,
                                  recv_window_ms: int = _DEFAULT_RECV_WINDOW_MS) -> dict:
    """签名 `GET /fapi/v2/account`(只读)——ungated 纯函数镜像,不实例化 gated 适配器
    (与 tools/verify_key_permissions.py 同一设计:签名算法复用 `_binance_sign_query`,
    逐行镜像 `BinanceFuturesAdapter._signed_query`,不重设一套签名)。成功返回原始 dict;
    任何失败抛异常(调用方决定 fail-soft 还是 fail-closed)。transport/clock/sleep 全可注入
    (测试零真网)。绝不记录/打印凭证值本身。"""
    http = http_request or _default_http_request
    clk = clock or time.time
    slp = sleep or time.sleep
    headers = {"X-MBX-APIKEY": api_key}

    def _do():
        # 签名串(含 timestamp)每次重试都重新算——退避可能跨越数秒(与适配器同款考量)。
        params = {"timestamp": int(clk() * 1000), "recvWindow": recv_window_ms}
        url = _MAINNET_BASE + _ACCOUNT_PATH + "?" + _binance_sign_query(params, api_secret)
        status, body = http("GET", url, body=None, headers=headers)
        data = BinanceFuturesAdapter._parse_json(status, body)   # noqa: SLF001 (staticmethod, unbound-safe)
        return status, data

    status, data = call_with_backoff(_do, is_rate_limited=_is_rate_limited,
                                     max_retries=max_retries,
                                     base_delay=retry_base_delay, sleep=slp)
    if not (200 <= status < 300):
        raise RuntimeError(f"account read failed: HTTP {status}")
    if not isinstance(data, dict):
        raise RuntimeError("account response is not a JSON object")
    return data


def _check_server_time(http_get: Callable, *, clock: Callable[[], float]) -> dict:
    """适配器假设(sync_clock):GET /fapi/v1/time 返回 {"serverTime": <ms int>}。"""
    try:
        payload = _public_get("/fapi/v1/time", http_get)
    except Exception as exc:
        return _check("server_time", False, f"request failed: {type(exc).__name__}")
    server_time = payload.get("serverTime") if isinstance(payload, dict) else None
    ok = isinstance(server_time, int) and not isinstance(server_time, bool) and server_time > 0
    if not ok:
        return _check("server_time", False,
                      "serverTime missing/not a positive int — adapter sync_clock assumption broken")
    skew_ms = int(server_time - int(clock() * 1000))
    return _check("server_time", True, "serverTime present (positive int ms)",
                  skew_ms=skew_ms)


def _check_exchange_info(http_get: Callable) -> dict:
    """适配器假设(exchange_filters):symbols[] 里 *USDT 合约的 filters[] 中
    LOT_SIZE(stepSize/minQty)、MIN_NOTIONAL(notional) 可解析为 float;PRICE_FILTER
    (tickSize) 可选。"""
    try:
        payload = _public_get("/fapi/v1/exchangeInfo", http_get)
    except Exception as exc:
        return _check("exchange_info", False, f"request failed: {type(exc).__name__}")
    symbols = payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(symbols, list):
        return _check("exchange_info", False, "symbols missing/not a list")
    row = next((s for s in symbols if isinstance(s, dict) and s.get("symbol") == "BTCUSDT"), None)
    if row is None:
        return _check("exchange_info", False, "BTCUSDT not present in symbols")
    by_type = {f.get("filterType"): f for f in row.get("filters", []) if isinstance(f, dict)}
    problems = []
    parsed = {}
    lot = by_type.get("LOT_SIZE")
    try:
        parsed["step_size"] = float(lot["stepSize"])
        parsed["min_qty"] = float(lot["minQty"])
    except (TypeError, KeyError, ValueError):
        problems.append("LOT_SIZE.stepSize/minQty unparseable")
    notional = by_type.get("MIN_NOTIONAL")
    try:
        parsed["min_notional"] = float(notional["notional"])
    except (TypeError, KeyError, ValueError):
        problems.append("MIN_NOTIONAL.notional unparseable")
    price_filter = by_type.get("PRICE_FILTER")
    if price_filter is None:
        problems.append("PRICE_FILTER missing (adapter tolerates tick_size=None, but verify)")
    else:
        try:
            parsed["tick_size"] = float(price_filter["tickSize"])
        except (KeyError, ValueError):
            problems.append("PRICE_FILTER.tickSize unparseable")
    if problems:
        return _check("exchange_info", False, "; ".join(problems))
    return _check("exchange_info", True, "BTCUSDT filters parse per adapter assumptions",
                  filters=parsed)


def _check_premium_index(http_get: Callable) -> dict:
    """适配器假设(premium_index):GET /fapi/v1/premiumIndex?symbol=BTCUSDT 的
    markPrice/indexPrice/lastFundingRate 可解析 float,nextFundingTime/time 为正整数毫秒
    (资金费信号策略的实时交叉核对证据源——字段形状错 = funding_check 恒 None 恒冻结)。"""
    try:
        payload = _public_get("/fapi/v1/premiumIndex?symbol=BTCUSDT", http_get)
    except Exception as exc:
        return _check("premium_index", False, f"request failed: {type(exc).__name__}")
    if not isinstance(payload, dict):
        return _check("premium_index", False, "response is not a JSON object")
    problems = []
    parsed = {}
    for field in ("markPrice", "indexPrice", "lastFundingRate"):
        try:
            parsed[field] = float(payload[field])
        except (KeyError, TypeError, ValueError):
            problems.append(f"{field} unparseable")
    for field in ("nextFundingTime", "time"):
        raw = payload.get(field)
        if not isinstance(raw, int) or isinstance(raw, bool) or raw <= 0:
            problems.append(f"{field} missing/not a positive int")
    if problems:
        return _check("premium_index", False, "; ".join(problems))
    return _check("premium_index", True,
                  "premiumIndex fields parse per adapter premium_index assumptions",
                  mark_price=parsed["markPrice"], last_funding_rate=parsed["lastFundingRate"])


def _check_account(api_key: str, api_secret: str, *, http_request: Optional[Callable],
                   clock: Callable[[], float], sleep: Optional[Callable]) -> dict:
    """适配器假设(account/positions):availableBalance/totalMarginBalance/totalMaintMargin
    可解析 float(_binance_balances/_binance_margin 的口径);positions[] 每行 symbol 字符串
    (以 USDT 结尾)+ positionAmt/entryPrice 可解析 float(adapter.positions 净额归并逻辑)。
    权限位不在 /fapi 响应里——verify_key_permissions --venue binance 已单独把关,这里不重复。"""
    try:
        payload = fetch_binance_futures_account(api_key, api_secret, http_request=http_request,
                                                clock=clock, sleep=sleep)
    except Exception as exc:
        return _check("account", False, f"request failed: {type(exc).__name__}")
    problems = []
    for field in ("availableBalance", "totalMarginBalance", "totalMaintMargin"):
        try:
            float(payload[field])
        except (KeyError, TypeError, ValueError):
            problems.append(f"{field} unparseable")
    positions = payload.get("positions")
    if not isinstance(positions, list):
        problems.append("positions missing/not a list")
    else:
        bad = 0
        n_open = 0
        for p in positions:
            if not isinstance(p, dict) or not isinstance(p.get("symbol"), str):
                bad += 1
                continue
            if not p["symbol"].endswith("USDT"):
                bad += 1
                continue
            try:
                qty = float(p["positionAmt"])
                float(p.get("entryPrice", 0.0) or 0.0)
            except (KeyError, TypeError, ValueError):
                bad += 1
                continue
            if qty != 0.0:
                n_open += 1
        if bad:
            problems.append(f"{bad} position row(s) fail symbol/positionAmt/entryPrice shape")
    if problems:
        return _check("account", False, "; ".join(problems))
    return _check("account", True,
                  "balance fields parse; position rows parse per adapter assumptions",
                  n_positions=len(positions), n_open_positions=n_open)


def run_smoke(api_key: str, api_secret: str, *, http_get: Callable = _http_get_json,
              http_request: Optional[Callable] = None,
              clock: Callable[[], float] = time.time,
              sleep: Optional[Callable] = None, now_ms: Optional[int] = None) -> dict:
    """四个只读检查 → 证据 dict(纯拼装,transport/clock 全注入,测试零真网)。"""
    if now_ms is None:
        now_ms = int(clock() * 1000)
    checks = [
        _check_server_time(http_get, clock=clock),
        _check_exchange_info(http_get),
        _check_premium_index(http_get),
        _check_account(api_key, api_secret, http_request=http_request,
                       clock=clock, sleep=sleep),
    ]
    verdict = "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL"
    return {
        "schema_version": 1,
        "provenance": "REAL_BINANCE_MAINNET_READONLY_SMOKE",
        "ran_at_ms": int(now_ms),
        "ran_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ms / 1000)),
        "checks": checks,
        "verdict": verdict,
        # 下单/成交响应形状(orderId/executedQty/avgPrice)只有真实首单才能验证——首单是人工
        # 复审的 ≤$100 步骤,不在本工具范围内,诚实标注。
        "order_response_shape": "UNVERIFIED_UNTIL_FIRST_FILL",
        "notes": [
            "read-only checks only (4 GETs); this tool never places orders and never prints "
            "credentials",
            "key permission bits (enableWithdrawals/ipRestrict) are NOT in the /fapi response — "
            "verified separately via python -m tools.verify_key_permissions --venue binance "
            "(/sapi/v1/account/apiRestrictions); that check must report SAFE before any "
            "--arm-live use",
        ],
    }


def main(argv: Optional[list] = None) -> int:
    _ = argv
    try:
        api_key, api_secret = _load_mainnet_credentials("binance")
    except RuntimeError as exc:
        print(f"[binance_readonly_smoke] credentials unavailable: {type(exc).__name__} — "
              "place key/binance_mainnet_key.json or set BINANCE_MAINNET_API_KEY/"
              "BINANCE_MAINNET_API_SECRET (human step)", flush=True)
        return 2
    result = run_smoke(api_key, api_secret)
    atomic_write_json(EVIDENCE_PATH, result, indent=2)
    for check in result["checks"]:
        print(f"[{check['status']}] {check['name']}: {check['detail']}", flush=True)
    print(f"verdict={result['verdict']} · order_response_shape={result['order_response_shape']} "
          f"-> {EVIDENCE_PATH}", flush=True)
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
