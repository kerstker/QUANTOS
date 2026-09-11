"""Binance 期货测试网真实联调烟测(手动运行,一次性验证 live/exchange/binance_futures.py 对真实
测试网响应的假设——`binance_futures.py` 的单元测试全部注入假 transport,这是本仓库第一次真正
打到 demo-fapi.binance.com)。

凭证:env `BINANCE_TESTNET_API_KEY`/`BINANCE_TESTNET_API_SECRET`,或 gitignored
`key/binance_test_key.json`({"api_key": "...", "api_secret": "..."})——绝不打印密钥内容,
只用于构造签名请求。

用法:
  python -m tools.live_testnet_smoke                只读步骤(零风险,验证连接/签名/解析)
  python -m tools.live_testnet_smoke --place-order   额外在只读步骤后下一笔最小 MARKET 单
                                                     (测试网假钱,零真实资本)

网络/墙钟只在此(out-of-gate,与其它 tools/ 驱动器同一惯例);live/exchange/binance_futures.py
本身零网络逻辑改动,这里只是调用它。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any

from live.contracts import LiveOrderRequest
from live.exchange.binance_futures import BinanceFuturesAdapter
from quantos.contracts.trading import OrderSide
from tools.cache_io import atomic_write_json

_KEY_FILE = os.path.join("key", "binance_test_key.json")
DEFAULT_EVIDENCE_PATH = os.path.join("evidence", "binance_testnet_smoke.json")


def _load_credentials():
    api_key = os.environ.get("BINANCE_TESTNET_API_KEY")
    api_secret = os.environ.get("BINANCE_TESTNET_API_SECRET")
    if api_key and api_secret:
        return api_key.strip(), api_secret.strip()
    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        k, s = data.get("api_key"), data.get("api_secret")
        if k and s:
            return k.strip(), s.strip()
    raise RuntimeError(
        "Binance testnet credentials not found "
        "(set $BINANCE_TESTNET_API_KEY/$BINANCE_TESTNET_API_SECRET, or key/binance_test_key.json)")


def _step(
    name: str,
    fn: Callable[[], Any],
    summarize: Callable[[Any], dict[str, Any]],
) -> tuple[dict[str, Any], Any | None]:
    try:
        result = fn()
        summary = summarize(result)
        record = {"name": name, "status": "PASS", **summary}
        print(f"{name}: PASS {json.dumps(summary, ensure_ascii=False, sort_keys=True)}")
        return record, result
    except Exception as exc:
        record = {
            "name": name,
            "status": "FAIL",
            "error_type": type(exc).__name__,
        }
        print(f"{name}: FAIL error_type={type(exc).__name__}", flush=True)
        return record, None


def _filters_summary(filters: Any) -> dict[str, Any]:
    if not isinstance(filters, dict) or "BTC" not in filters:
        raise ValueError("BTC filter is missing")
    btc = filters["BTC"]
    return {
        "symbol_count": len(filters),
        "btc": {
            "step_size": btc.step_size,
            "min_qty": btc.min_qty,
            "min_notional": btc.min_notional,
            "tick_size": btc.tick_size,
        },
    }


def _account_summary(account: Any) -> dict[str, Any]:
    if not isinstance(account, dict):
        raise TypeError("account response must be an object")
    assets = account.get("assets")
    positions = account.get("positions")
    return {
        "field_names": sorted(account),
        "asset_row_count": len(assets) if isinstance(assets, list) else None,
        "position_row_count": len(positions) if isinstance(positions, list) else None,
        "can_trade": account.get("canTrade"),
        "can_withdraw": account.get("canWithdraw"),
    }


def _positions_summary(positions: Any) -> dict[str, Any]:
    if not isinstance(positions, dict):
        raise TypeError("normalized positions must be an object")
    return {
        "nonzero_position_count": len(positions),
        "instrument_ids": sorted(positions),
    }


def _order_summary(order: Any) -> dict[str, Any]:
    if order is None:
        raise ValueError("order response is missing")
    return {
        "venue_order_id": str(order.venue_order_id),
        "client_order_id": order.client_order_id,
        "instrument_id": order.instrument_id,
        "order_status": order.status,
        "filled_qty": order.filled_qty,
        "avg_price": order.avg_price,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--place-order",
        action="store_true",
        help="place one minimum Binance Futures testnet MARKET order",
    )
    parser.add_argument("--output", default=DEFAULT_EVIDENCE_PATH)
    args = parser.parse_args(argv)
    observed_at_ms = int(time.time() * 1000)
    checks: list[dict[str, Any]] = []
    try:
        api_key, api_secret = _load_credentials()
        client = BinanceFuturesAdapter(
            api_key=api_key,
            api_secret=api_secret,
            base_url=BinanceFuturesAdapter.TESTNET_BASE,
        )
        if client.venue_mode != "testnet":
            raise ValueError("adapter did not bind to testnet")
        checks.append({
            "name": "credential_load_and_testnet_adapter",
            "status": "PASS",
            "venue_mode": client.venue_mode,
        })
    except Exception as exc:
        checks.append(
            {
                "name": "credential_load_and_testnet_adapter",
                "status": "FAIL",
                "error_type": type(exc).__name__,
            }
        )
        client = None

    filters = None
    if client is not None:
        record, filters = _step(
            "exchange_filters",
            client.exchange_filters,
            _filters_summary,
        )
        checks.append(record)
        record, _account = _step("signed_account", client.account, _account_summary)
        checks.append(record)
        record, _positions = _step(
            "normalized_positions",
            client.positions,
            _positions_summary,
        )
        checks.append(record)
        record, _ghost = _step(
            "idempotent_missing_order_lookup",
            lambda: client.get_order("BTC", "quantos-smoke-ghost-order"),
            lambda value: {"returned_none": value is None}
            if value is None
            else (_ for _ in ()).throw(ValueError("ghost order unexpectedly exists")),
        )
        checks.append(record)

    if args.place_order:
        if client is None or not isinstance(filters, dict) or "BTC" not in filters:
            checks.append(
                {
                    "name": "testnet_order_and_requery",
                    "status": "FAIL",
                    "error_type": "PrerequisiteFailed",
                }
            )
        else:
            btc = filters["BTC"]
            qty = round(max(btc.min_qty * 20, btc.step_size), 8)
            client_order_id = f"quantos-smoke-{int(time.time())}"
            order = LiveOrderRequest(
                instrument_id="BTC",
                side=OrderSide.BUY,
                quantity=qty,
                client_order_id=client_order_id,
            )
            placed_record, _placed = _step(
                "testnet_order_submit",
                lambda: client.place_order(order),
                _order_summary,
            )
            if (
                placed_record["status"] == "PASS"
                and (
                    _placed is None
                    or _placed.client_order_id != client_order_id
                    or _placed.instrument_id != "BTC"
                )
            ):
                placed_record = {
                    **placed_record,
                    "status": "FAIL",
                    "error_type": "OrderIdentityMismatch",
                }
            checks.append(placed_record)
            queried_record, queried = _step(
                "testnet_order_requery",
                lambda: client.get_order("BTC", client_order_id),
                _order_summary,
            )
            if (
                queried_record["status"] == "PASS"
                and (
                    queried is None
                    or queried.client_order_id != client_order_id
                    or queried.instrument_id != "BTC"
                    or queried.status != "FILLED"
                    or queried.filled_qty != qty
                )
            ):
                queried_record = {
                    **queried_record,
                    "status": "FAIL",
                    "error_type": "OrderFillOrIdentityMismatch",
                }
            checks.append(queried_record)

    passed = all(check["status"] == "PASS" for check in checks)
    evidence = {
        "schema_version": 1,
        "evidence_kind": "BINANCE_FUTURES_TESTNET_SMOKE",
        "observed_at_ms": observed_at_ms,
        "venue": "BINANCE_FUTURES_TESTNET",
        "place_order_requested": args.place_order,
        "checks": checks,
        "verdict": "PASS" if passed else "FAIL",
        "safety": {
            "testnet_only": True,
            "real_capital": False,
            "mainnet_order_submission": False,
            "credentials_persisted": False,
            "secret_values_in_evidence": False,
            "raw_account_response_persisted": False,
        },
    }
    atomic_write_json(args.output, evidence, indent=2)
    print(
        json.dumps(
            {
                "verdict": evidence["verdict"],
                "check_count": len(checks),
                "output": args.output,
                "testnet_only": True,
            },
            sort_keys=True,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
