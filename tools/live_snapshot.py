"""venue 实时快照解析(2026-07-05)。ops API 与风险哨兵共用的仓位/权益解析纯函数——
输入是 venue 的原始响应形状,输出统一行格式,零网络零 IO(网络调用在调用方)。

行格式:{instrument_id, qty(带符号), entry_price, mark_price, upnl, notional(带符号,按标记价)}。
Binance /fapi/v2/account 不直接给 markPrice——用 entry + upnl/qty 反推(uPnL 定义即
(mark-entry)*qty,反推是恒等式不是近似);Bybit V5 position/list 直接带 markPrice/unrealisedPnl。
"""
from __future__ import annotations

from typing import List


def binance_position_rows(raw_account: dict) -> List[dict]:
    out = []
    for p in raw_account.get("positions", []):
        symbol = p.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        try:
            qty = float(p.get("positionAmt", 0) or 0)
        except (TypeError, ValueError):
            continue
        if qty == 0.0:
            continue
        try:
            entry = float(p.get("entryPrice", 0) or 0)
            upnl = float(p.get("unrealizedProfit", 0) or 0)
        except (TypeError, ValueError):
            entry, upnl = 0.0, 0.0
        mark = entry + (upnl / qty if qty else 0.0)
        out.append({"instrument_id": symbol[:-4], "qty": qty, "entry_price": entry,
                    "mark_price": mark, "upnl": upnl, "notional": qty * mark})
    return out


def bybit_position_rows(raw_position_list: dict) -> List[dict]:
    """输入 = /v5/position/list 的整个响应信封(含 result.list)。"""
    result = (raw_position_list or {}).get("result") or {}
    out = []
    for p in result.get("list", []) or []:
        symbol = p.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        try:
            size = float(p.get("size", 0) or 0)
        except (TypeError, ValueError):
            continue
        if size == 0.0:
            continue
        side = p.get("side")
        if side not in ("Buy", "Sell"):
            continue                                     # side 未知,诚实跳过不猜方向
        qty = size if side == "Buy" else -size
        try:
            entry = float(p.get("avgPrice", 0) or 0)
            mark = float(p.get("markPrice", 0) or 0)
            upnl = float(p.get("unrealisedPnl", 0) or 0)
        except (TypeError, ValueError):
            entry, mark, upnl = 0.0, 0.0, 0.0
        out.append({"instrument_id": symbol[:-4], "qty": qty, "entry_price": entry,
                    "mark_price": mark, "upnl": upnl, "notional": qty * (mark or entry)})
    return out


def portfolio_summary(rows: List[dict]) -> dict:
    gross = sum(abs(r["notional"]) for r in rows)
    net = sum(r["notional"] for r in rows)
    return {"gross_notional": gross, "net_notional": net,
            "upnl_total": sum(r["upnl"] for r in rows), "n_positions": len(rows)}
