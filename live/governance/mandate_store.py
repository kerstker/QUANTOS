"""Mandate 文件加载器(Stage 1.5)。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.6 延伸。

真实数字(限额/允许清单/签署人)由用户自己创建文件签署——镜像 API 密钥的处理方式
(key/binance_test_key.json),这里不代填任何"看起来合理"的默认数字,避免 AI 变相给自己
发通行证。fail-closed:文件缺失/JSON 格式错误/字段缺失或类型错误都要有清楚、可诊断的错误,
而不是静默产出一个"看起来能用"但实际错误的 Mandate,或悄悄回落到某个隐含默认值。
"""
from __future__ import annotations

import json
import os

from live.governance.mandate import Mandate

_REQUIRED = ("mandate_id", "venue", "exchange", "allowed_instruments",
            "max_single_order_notional", "max_gross_notional", "max_net_notional")


class MandateLoadError(RuntimeError):
    pass


def load_mandate(path: str) -> Mandate:
    if not os.path.exists(path):
        raise MandateLoadError(
            f"mandate file not found: {path} — create it yourself with real signed-off limits "
            f"(see docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.6); "
            f"no default is provided on purpose")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise MandateLoadError(f"mandate file malformed/unreadable: {path}: {exc}") from exc

    missing = [f for f in _REQUIRED if f not in data]
    if missing:
        raise MandateLoadError(f"mandate file has invalid/missing fields {missing}: {path}")
    try:
        return Mandate(
            mandate_id=str(data["mandate_id"]),
            venue=str(data["venue"]),
            exchange=str(data["exchange"]),
            allowed_instruments=frozenset(data["allowed_instruments"]),
            max_single_order_notional=float(data["max_single_order_notional"]),
            max_gross_notional=float(data["max_gross_notional"]),
            max_net_notional=float(data["max_net_notional"]),
            per_instrument_max_notional=tuple(
                (str(k), float(v)) for k, v in data.get("per_instrument_max_notional", {}).items()),
            approved_by=tuple(str(a) for a in data.get("approved_by", ())),
            issued_at=int(data.get("issued_at", 0)),
            expires_at=(int(data["expires_at"]) if data.get("expires_at") is not None else None),
        )
    except (TypeError, ValueError) as exc:
        raise MandateLoadError(f"mandate file has invalid/missing fields: {path}: {exc}") from exc
