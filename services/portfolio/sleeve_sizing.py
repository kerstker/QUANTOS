"""策略 sleeve 级 sizing —— 组合优化器接 live sizing 的**诚实**形态(Phase 3 子项2,P1-6 相关)。

诚实裁定(见 docs/engineering/specs/2026-07-07-phase3-optimizer-and-crowding.md):组合优化器
(`services/portfolio/optimizer.py`)是**跨 alpha sleeve(策略)**的风险预算分配器("sizes RISK across
streams"),每条 alpha 内部已美元中性。**单策略 live 下 sleeve 集合退化为 1,风险平衡是恒等空操作**;
把 1×1 优化器硬塞进 live sizing 只会造死代码/假 chokepoint、改变已被干跑验证的等权 `_legs` book 口径引
回归,零收益。故正确形态 = per-strategy 裸权重(sleeve 内 sizing)+ per-strategy mandate(sleeve 风险
预算)+ firm 硬门(账户级跨 sleeve 预算)= 组合优化器会给的账户级风险预算分配,只是以硬门实现。

本模块是**默认关闭的 opt-in hook**,备多策略 live 上线时显式启用;live 默认路径永不调用它(回归锁)。
纯函数、无 IO/网络/墙钟。
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

from services.portfolio.optimizer import inverse_vol_weights, risk_parity_weights

_MODES = ("off", "risk_parity", "inverse_vol")


def size_strategy_sleeves(sleeve_cov, strategy_ids: Sequence[str], *, mode: str = "off") -> dict:
    """跨 sleeve(策略)风险预算标量 {strategy_id: scalar}。fail-closed:未知 mode / 非有限 cov raise。
    **单 sleeve 或 mode='off' → 全 1.0**(恒等:证明单策略 live 下组合层是 no-op,= per-strategy 隔离
    与组合优化器等价);≥2 sleeve 且非 off → 复用 optimizer 在 sleeve 收益协方差上算每策略标量。"""
    if mode not in _MODES:
        raise ValueError(f"unknown sleeve sizing mode {mode!r} (choose from {_MODES})")
    ids = list(strategy_ids)
    if mode == "off" or len(ids) <= 1:
        return {sid: 1.0 for sid in ids}
    for row in sleeve_cov:                                   # fail-closed:cov 非有限绝不静默
        for v in row:
            if not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))):
                raise ValueError(f"non-finite covariance entry {v!r}")
    ws = risk_parity_weights(sleeve_cov) if mode == "risk_parity" else inverse_vol_weights(sleeve_cov)
    return {sid: float(w) for sid, w in zip(ids, ws)}


def apply_sleeve_scalar(weights: Mapping[str, float], scalar: float) -> dict:
    """把整个 book 的权重同乘 sleeve 标量:保持美元中性(net 与 gross 同因子缩放,方向结构不变),
    只按标量缩放规模。scalar=1.0 → 原样(单策略/off 下的恒等,字节不变)。"""
    s = float(scalar)
    return {k: v * s for k, v in weights.items()}
