"""MEXC mainnet 只读首验工具(**人工执行**;docs/MEXC_INTEGRATION.md §6/§10 + console
mexc-readiness 的 UNVERIFIED_MAINNET_RESPONSE 解除步骤)。

三个只读调用,校验真实 mainnet 响应字段形状与 live/exchange/mexc_spot.py 的适配器假设一致:

1. `GET /api/v3/time`(公开,无签名)——校时源:`serverTime` 字段存在且为正整数毫秒;
2. `GET /api/v3/exchangeInfo`(公开)——交易所过滤器:BTCUSDT 的 LOT_SIZE(stepSize/minQty)、
   MIN_NOTIONAL(notional)、PRICE_FILTER(tickSize)可按 `MexcSpotAdapter.exchange_filters`
   的假设解析;
3. `GET /api/v3/account`(签名,**只读**)——`balances[].asset/free/locked` 形状 +
   `canTrade`/`canWithdraw` 权限位(凭证经 tools/mexc_credentials 加载,绝不打印/落盘)。

产出 `evidence/mexc_mainnet_smoke.json`:逐项 PASS/FAIL + 非密钥字段盘点 + 总 verdict。
**不实例化 MexcSpotAdapter**(mainnet 构造闸门不碰——与 tools/verify_key_permissions.py
同一「ungated 纯函数镜像」设计);下单响应形状(orderId/executedQty/cummulativeQuoteQty)
**不在**本工具校验范围——那要等人工复审的 ≤$100 首单,证据里诚实标注
`UNVERIFIED_UNTIL_FIRST_FILL`。

人工运行(需要 MEXC_API_KEY/MEXC_API_SECRET env 或 key/mexc_spot.json;AI 绝不运行本工具,
绝不触碰真实凭证):

    python -m tools.mexc_readonly_smoke
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable, Optional

from tools.cache_io import atomic_write_json
from tools.coingecko_fetch import _http_get_json
from tools.mexc_credentials import (MEXC_MAINNET_BASE, MexcCredentialError,
                                    fetch_mexc_account, load_mexc_credentials)

EVIDENCE_PATH = os.path.join("evidence", "mexc_mainnet_smoke.json")


def _check(name: str, ok: bool, detail: str, **fields) -> dict:
    return {"name": name, "status": "PASS" if ok else "FAIL", "detail": detail, **fields}


def _public_get(path: str, http_get: Callable):
    payload = http_get(MEXC_MAINNET_BASE + path, {"accept": "application/json"})
    if isinstance(payload, dict) and "code" in payload:      # MEXC 错误体
        raise RuntimeError(f"MEXC error {payload.get('code')}: {payload.get('msg')}")
    return payload


def _check_server_time(http_get: Callable, *, clock: Callable[[], float]) -> dict:
    """适配器假设(sync_clock):GET /api/v3/time 返回 {"serverTime": <ms int>}。"""
    try:
        payload = _public_get("/api/v3/time", http_get)
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
    """适配器假设(exchange_filters):symbols[].baseAsset + filters[] 里 LOT_SIZE(stepSize/
    minQty)、MIN_NOTIONAL(notional) 可解析为 float;PRICE_FILTER(tickSize) 可选。"""
    try:
        payload = _public_get("/api/v3/exchangeInfo", http_get)
    except Exception as exc:
        return _check("exchange_info", False, f"request failed: {type(exc).__name__}")
    symbols = payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(symbols, list):
        return _check("exchange_info", False, "symbols missing/not a list")
    row = next((s for s in symbols if isinstance(s, dict) and s.get("symbol") == "BTCUSDT"), None)
    if row is None:
        return _check("exchange_info", False, "BTCUSDT not present in symbols")
    if row.get("baseAsset") != "BTC":
        return _check("exchange_info", False,
                      f"baseAsset {row.get('baseAsset')!r} != 'BTC' — instrument_id mapping broken")
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


def _check_account(api_key: str, api_secret: str, *, http_request: Optional[Callable],
                   clock: Callable[[], float], sleep: Optional[Callable]) -> dict:
    """适配器假设(account/positions):balances[] 每行 asset 字符串 + free/locked 可解析
    float;§10 权限位:canTrade/canWithdraw 为 bool 且 canWithdraw 必须为 False。"""
    try:
        payload = fetch_mexc_account(api_key, api_secret, http_request=http_request,
                                     clock=clock, sleep=sleep)
    except Exception as exc:
        return _check("account", False, f"request failed: {type(exc).__name__}")
    balances = payload.get("balances")
    if not isinstance(balances, list):
        return _check("account", False, "balances missing/not a list")
    bad = 0
    n_nonzero = 0
    for bal in balances:
        if not isinstance(bal, dict) or not isinstance(bal.get("asset"), str):
            bad += 1
            continue
        try:
            free = float(bal["free"])
            float(bal["locked"])
        except (KeyError, TypeError, ValueError):
            bad += 1
            continue
        if free > 0:
            n_nonzero += 1
    if bad:
        return _check("account", False, f"{bad} balance row(s) fail asset/free/locked shape")
    can_trade = payload.get("canTrade")
    can_withdraw = payload.get("canWithdraw")
    if not isinstance(can_trade, bool) or not isinstance(can_withdraw, bool):
        return _check("account", False,
                      "canTrade/canWithdraw missing/non-boolean — §10 permission fields "
                      "unverified; do NOT use this key until resolved",
                      n_balances=len(balances))
    if can_withdraw:
        return _check("account", False,
                      "canWithdraw=true — this key CAN withdraw funds; DISABLE withdrawals "
                      "at the MEXC console before any live use",
                      n_balances=len(balances))
    return _check("account", True,
                  "balances shape ok; canTrade/canWithdraw present; withdrawals disabled",
                  n_balances=len(balances), n_nonzero_balances=n_nonzero,
                  can_trade=can_trade, can_withdraw=can_withdraw)


def run_smoke(api_key: str, api_secret: str, *, http_get: Callable = _http_get_json,
              http_request: Optional[Callable] = None,
              clock: Callable[[], float] = time.time,
              sleep: Optional[Callable] = None, now_ms: Optional[int] = None) -> dict:
    """三个只读检查 → 证据 dict(纯拼装,transport/clock 全注入,测试零真网)。"""
    if now_ms is None:
        now_ms = int(clock() * 1000)
    checks = [
        _check_server_time(http_get, clock=clock),
        _check_exchange_info(http_get),
        _check_account(api_key, api_secret, http_request=http_request,
                       clock=clock, sleep=sleep),
    ]
    verdict = "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL"
    return {
        "schema_version": 1,
        "provenance": "REAL_MEXC_MAINNET_READONLY_SMOKE",
        "ran_at_ms": int(now_ms),
        "ran_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ms / 1000)),
        "checks": checks,
        "verdict": verdict,
        # 下单/成交响应形状(orderId/executedQty/cummulativeQuoteQty)只有真实首单才能验证——
        # 首单是人工复审的 ≤$100 步骤,不本工具范围内,诚实标注。
        "order_response_shape": "UNVERIFIED_UNTIL_FIRST_FILL",
        "notes": [
            "read-only checks only; this tool never places orders and never prints credentials",
            "IP-whitelist binding is not verifiable via MEXC v3 API — confirm at the console",
        ],
    }


def main(argv: Optional[list] = None) -> int:
    _ = argv
    try:
        api_key, api_secret = load_mexc_credentials()
    except MexcCredentialError as exc:
        print(f"[mexc_readonly_smoke] credentials unavailable: {type(exc).__name__} — "
              "place key/mexc_spot.json or set MEXC_API_KEY/MEXC_API_SECRET (human step)", flush=True)
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
