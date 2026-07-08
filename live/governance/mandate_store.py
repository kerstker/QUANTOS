"""Mandate 文件加载器(Stage 1.5)。设计:
docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.6 延伸。

真实数字(限额/允许清单/签署人)由用户自己创建文件签署——镜像 API 密钥的处理方式
(key/binance_test_key.json),这里不代填任何"看起来合理"的默认数字,避免 AI 变相给自己
发通行证。fail-closed:文件缺失/JSON 格式错误/字段缺失或类型错误都要有清楚、可诊断的错误,
而不是静默产出一个"看起来能用"但实际错误的 Mandate,或悄悄回落到某个隐含默认值。
"""
from __future__ import annotations

import json
import math
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
        # 2026-07-05 安全审查 HIGH 修复:`json.load` 默认放行非标准 NaN/Infinity 字面量
        # (标准 JSON 不允许,但 Python 的 json 模块默认 allow_nan=True)——`float("nan")`
        # 不抛异常,而 evaluate_mandate 全部用 `>`/`abs()>` 比较限额,NaN 参与比较恒为
        # False,会让"任意大小订单都未超限"静默成立,且不触发 fail-closed 分支。
        # 每个名义额字段转换后必须立刻校验有限,不能指望上游格式校验替我们把关。
        def _finite_float(value, field_name: str) -> float:
            v = float(value)
            if not math.isfinite(v):
                raise MandateLoadError(f"mandate field {field_name!r} must be a finite number, "
                                       f"got {value!r}: {path}")
            return v

        max_single = _finite_float(data["max_single_order_notional"], "max_single_order_notional")
        max_gross = _finite_float(data["max_gross_notional"], "max_gross_notional")
        max_net = _finite_float(data["max_net_notional"], "max_net_notional")
        per_instrument = tuple(
            (str(k), _finite_float(v, f"per_instrument_max_notional[{k}]"))
            for k, v in data.get("per_instrument_max_notional", {}).items())

        return Mandate(
            mandate_id=str(data["mandate_id"]),
            venue=str(data["venue"]),
            exchange=str(data["exchange"]),
            allowed_instruments=frozenset(data["allowed_instruments"]),
            max_single_order_notional=max_single,
            max_gross_notional=max_gross,
            max_net_notional=max_net,
            per_instrument_max_notional=per_instrument,
            approved_by=tuple(str(a) for a in data.get("approved_by", ())),
            issued_at=int(data.get("issued_at", 0)),
            expires_at=(int(data["expires_at"]) if data.get("expires_at") is not None else None),
        )
    except MandateLoadError:
        raise
    except (TypeError, ValueError) as exc:
        raise MandateLoadError(f"mandate file has invalid/missing fields: {path}: {exc}") from exc
