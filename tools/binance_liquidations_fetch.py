"""Binance USD-M 强平事件只读采集器。

``/fapi/v1/allForceOrders`` 在不同 Binance 环境可能要求额外权限；本模块只
尝试无凭证的公开 GET，遇到 HTTP 400/权限错误就 BLOCK，不读取账户凭证。即使
端点可用，它也不是完整历史归档：交易所对查询窗口和返回条数有限制，因而
只生成已经闭合 UTC 日的聚合，不把缺失历史填成零，也不把实时当天聚合送入
日线 PIT 回测。若要补齐该维度，应接入公开 forceOrder 流并保持同一 raw/PIT 契约。

网络边界只在 ``tools/``。每次响应都可选写入不可变 raw archive，规范化结果
保存 ``event_time``、``knowledge_time`` 和来源标识，便于清算/拥挤 Alpha 后续
在有足够样本时独立评估。
"""
from __future__ import annotations

import math
import os
import time

from services.data.raw_archive import DEFAULT_RAW_ARCHIVE, archive_json_response
from tools.cache_io import atomic_write_json
from tools.coingecko_fetch import _http_get_json

_FAPI = "https://fapi.binance.com"
_DAY_MS = 86_400_000
_MAX_WINDOW_DAYS = 7
_DEFAULT_CACHE = os.path.join("data", "cache", "crypto_liquidations.json")


class LiquidationDataError(ValueError):
    """响应缺失关键字段或违反 PIT/数值契约。"""


def _get(path: str, http_get=_http_get_json):
    return http_get(_FAPI + path, {"accept": "application/json"})


def _number(value, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LiquidationDataError(f"invalid liquidation {field}") from exc
    if not math.isfinite(number) or number <= 0.0:
        raise LiquidationDataError(f"invalid liquidation {field}")
    return number


def parse_force_orders(payload, ticker: str = "BTC", *, now_ms: int,
                       diagnostics: dict | None = None) -> dict:
    """把一页 force-order 事件聚合为 ``{closed_day_ms: metrics}``。

    Binance 返回字段是字符串；``side=SELL`` 代表被强平的多头，``BUY`` 代表
    被强平的空头。坏行被计数并丢弃，整页没有任何可用行时仍返回空字典——
    “没有观测”与“观测为 0”严格区分。
    """
    accepted = rejected = 0
    out: dict[int, dict] = {}
    symbol = ticker.upper() + "USDT"
    if not isinstance(payload, list):
        if diagnostics is not None:
            diagnostics.update({"status": "REJECTED", "input_rows": 0,
                                "accepted_rows": 0, "rejected_rows": 1})
        raise LiquidationDataError("force-order response must be a list")
    for item in payload:
        try:
            if not isinstance(item, dict) or str(item.get("symbol", "")) != symbol:
                raise LiquidationDataError("symbol mismatch")
            ts = int(item["time"])
            if ts < 0 or ts > int(now_ms):
                raise LiquidationDataError("event time is outside PIT bounds")
            side = str(item["side"]).upper()
            if side not in {"BUY", "SELL"}:
                raise LiquidationDataError("side must be BUY or SELL")
            qty = _number(item.get("executedQty") or item.get("origQty"), field="quantity")
            price = _number(item.get("averagePrice") or item.get("price"), field="price")
            notional = qty * price
            if not math.isfinite(notional) or notional <= 0.0:
                raise LiquidationDataError("notional is not finite")
        except (KeyError, TypeError, ValueError, LiquidationDataError):
            rejected += 1
            continue
        day = (ts // _DAY_MS) * _DAY_MS
        rec = out.setdefault(day, {
            "event_time": day,
            "knowledge_time": day + _DAY_MS - 1,
            "liquidation_notional": 0.0,
            "long_liquidation_notional": 0.0,
            "short_liquidation_notional": 0.0,
            "order_count": 0,
            "largest_notional": 0.0,
        })
        rec["liquidation_notional"] += notional
        if side == "SELL":
            rec["long_liquidation_notional"] += notional
        else:
            rec["short_liquidation_notional"] += notional
        rec["order_count"] += 1
        rec["largest_notional"] = max(rec["largest_notional"], notional)
        accepted += 1
    for rec in out.values():
        total = rec["long_liquidation_notional"] + rec["short_liquidation_notional"]
        rec["imbalance"] = ((rec["long_liquidation_notional"] -
                              rec["short_liquidation_notional"]) / total
                             if total > 0.0 else None)
        for key in ("liquidation_notional", "long_liquidation_notional",
                    "short_liquidation_notional", "largest_notional", "imbalance"):
            if rec[key] is not None:
                rec[key] = float(rec[key])
    if diagnostics is not None:
        diagnostics.update({"status": "ACCEPTED" if accepted else "REJECTED",
                            "input_rows": len(payload), "accepted_rows": accepted,
                            "rejected_rows": rejected, "duplicate_rows": 0})
    return out


def _archive(*, archive_root, payload, source_uri, ticker, observed_at_ms, diagnostics):
    if archive_root is None:
        return
    archive_json_response(
        root=archive_root, payload=payload, source_uri=source_uri,
        provider="Binance USD-M force-order history",
        dataset_id="liquidations", instrument_id=ticker,
        observed_at_ms=int(observed_at_ms), parse_diagnostics=diagnostics,
    )


def fetch_daily_liquidations(ticker: str = "BTC", *, days: int = 7,
                             http_get=_http_get_json, now_ms: int | None = None,
                             sleep=time.sleep, archive_root=None,
                             observed_at_ms: int | None = None,
                             limit: int = 1000) -> dict:
    """抓最近最多 7 个已闭合 UTC 日的强平聚合。

    该端点的历史能力有限，``days>7`` 直接报错而不是假装可以深历史回测。
    ``knowledge_time`` 取日末；当前未闭合日被剔除。
    """
    if days <= 0 or days > _MAX_WINDOW_DAYS:
        raise ValueError(f"days must be in 1..{_MAX_WINDOW_DAYS} for force orders")
    if limit <= 0 or limit > 1000:
        raise ValueError("limit must be in 1..1000")
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if archive_root is not None and observed_at_ms is None:
        raise ValueError("observed_at_ms is required when archive_root is set")
    start = max(0, int(now_ms) - days * _DAY_MS)
    end = int(now_ms)
    by_day: dict[int, dict] = {}
    last_ts = None
    for _ in range(20):
        path = (f"/fapi/v1/allForceOrders?symbol={ticker.upper()}USDT"
                f"&startTime={start}&endTime={end}&limit={limit}")
        payload = _get(path, http_get)
        diagnostics = {}
        try:
            parsed = parse_force_orders(payload, ticker, now_ms=now_ms,
                                        diagnostics=diagnostics)
        except LiquidationDataError:
            diagnostics.setdefault("status", "REJECTED")
            _archive(archive_root=archive_root, payload=payload,
                     source_uri=_FAPI + path, ticker=ticker,
                     observed_at_ms=observed_at_ms, diagnostics=diagnostics)
            raise
        _archive(archive_root=archive_root, payload=payload,
                 source_uri=_FAPI + path, ticker=ticker,
                 observed_at_ms=observed_at_ms, diagnostics=diagnostics)
        for day, row in parsed.items():
            existing = by_day.get(day)
            if existing is None:
                by_day[day] = dict(row)
            else:
                # A page boundary can split one UTC day. Combine rather than overwrite.
                for key in ("liquidation_notional", "long_liquidation_notional",
                            "short_liquidation_notional", "order_count"):
                    existing[key] += row[key]
                existing["largest_notional"] = max(existing["largest_notional"],
                                                     row["largest_notional"])
                total = (existing["long_liquidation_notional"] +
                         existing["short_liquidation_notional"])
                existing["imbalance"] = ((existing["long_liquidation_notional"] -
                                           existing["short_liquidation_notional"]) / total
                                          if total else None)
        if not isinstance(payload, list) or not payload or len(payload) < limit:
            break
        try:
            newest = max(int(item["time"]) for item in payload if isinstance(item, dict))
        except (TypeError, ValueError):
            break
        if last_ts is not None and newest <= last_ts:
            break
        last_ts = newest
        if newest >= end - 1:
            break
        start = newest + 1
        if sleep:
            sleep(0.15)
    today = (int(now_ms) // _DAY_MS) * _DAY_MS
    return {day: row for day, row in sorted(by_day.items()) if day < today}


def update_cache(path: str = _DEFAULT_CACHE, *, ticker: str,
                 rows: dict[int, dict], fetched_at_ms: int) -> dict:
    """幂等更新近期缓存；已观测日可被重算，raw archive 保留每次原始响应。"""
    if os.path.exists(path):
        import json
        payload = json.load(open(path, encoding="utf-8"))
    else:
        payload = {"schema_version": 1,
                   "provenance": "REAL_BINANCE_FORCE_ORDERS_RECENT",
                   "data": {}, "_meta": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise LiquidationDataError("liquidation cache has invalid root schema")
    data = payload["data"].setdefault(ticker.upper(), {})
    for day, row in rows.items():
        data[str(int(day))] = dict(row)
    payload["_meta"][ticker.upper()] = {
        "fetched_at_ms": int(fetched_at_ms),
        "through": max((int(day) for day in data), default=None),
        "history_limit_days": _MAX_WINDOW_DAYS,
    }
    atomic_write_json(path, payload)
    return payload


def main() -> None:
    import sys
    ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "BTC"
    now_ms = int(time.time() * 1000)
    rows = fetch_daily_liquidations(
        ticker, now_ms=now_ms, archive_root=DEFAULT_RAW_ARCHIVE,
        observed_at_ms=now_ms,
    )
    update_cache(ticker=ticker, rows=rows, fetched_at_ms=now_ms)
    print(f"{ticker} liquidation days: {len(rows)} (recent public window only)")


if __name__ == "__main__":
    main()
