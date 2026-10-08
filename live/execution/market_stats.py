"""从面板 close/volume 算真实逐标的 ADV / 已实现波动喂 gate(Phase 4 子项1,P1-5 深化)。

现 make_ctx_factory 用占位符 est_daily_volume=1e12 / instrument_vol=0.8,使 PreTradeRiskGate 的
check_liquidity / check_volatility 在 live 结构性永不触发。本模块把它们升为**真实且承重**:从面板真实
volume/close 的 trailing 窗算 ADV / 已实现波动,喂进 RiskContext。**fail-closed**:缺/非有限/窗口不足一律
返回 None——gate 据字段 docstring 拒该标的(绝不像 dry-run 那样 fail-open 回退大数)。

诚实边界:$5–10k AUM 下真实参与率 ~1e-7(0.015BTC / 40k BTC 日量),liquidity 门在小资金下不实际绑定
(RE-1 实测参与率在 carry ~$2M / crowd ~$7M 才绑)——这是**诚实**的。接线把门从"结构性失效(1e12)"升为
"真实且承重"(防缩放/胖手指爆单 + 规模化上行 + 缺数据 fail-closed 拒),而非"今天开始主动拒单"。阈值
max_participation_rate / max_instrument_vol 是风险数字,须用户签(现占位)。纯函数、无 IO/网络/墙钟。
"""
from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence


def _finite_positive(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x)) and float(x) > 0.0


def average_daily_volume_units(volume_series: Optional[Sequence[float]], *, unit_scale: float,
                               window: int) -> Optional[float]:
    """trailing `window` 窗内**中位数** base-asset 量 × `unit_scale`,或 None(fail-closed)。
    ×unit_scale 是因为 gate 的 `intent.quantity` 也是缩放整数(UNIT_SCALE),`quantity/est_daily_volume`
    才等于真实参与率(镜像 gate_context.build_gate_positions / reference_price=price/unit_scale 的缩放约定)。
    空/None/全≤0/全非有限 → None。"""
    if not volume_series or window < 1:
        return None
    tail = [float(v) for v in list(volume_series)[-window:] if _finite_positive(v)]
    if not tail:
        return None
    return statistics.median(tail) * float(unit_scale)


def realized_volatility(close_series: Optional[Sequence[float]], *, window: int,
                        annualize: float = 1.0) -> Optional[float]:
    """trailing `window` 窗内对数收益的 population std × sqrt(annualize),或 None(fail-closed)。
    需至少 2 个有效收益(≥3 个有效收盘价);<2 收益 / 非有限 → None。`annualize` 显式定义单位(如日 σ
    传 365 得年化),须与 `max_instrument_vol` 阈值口径一致。"""
    if not close_series or window < 1:
        return None
    tail = [float(c) for c in list(close_series)[-window:] if _finite_positive(c)]
    if len(tail) < 3:
        return None
    rets = [math.log(tail[i] / tail[i - 1]) for i in range(1, len(tail))]
    if len(rets) < 2:
        return None
    return statistics.pstdev(rets) * math.sqrt(float(annualize))
