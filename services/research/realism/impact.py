"""市场冲击模型(现实主义引擎 RE-1)—— Almgren-Chriss 式:临时冲击(平方根律,撮合当时的滑点)+ 永久冲击
(线性,把价格推走留在市场里)。参与率 = |trade|/ADV 驱动。纯函数、确定性、无 IO。系数取文献标准量级,但**未用
真实成交校准**(L2 无 tick):调用方应扫系数给区间带,勿当精确值。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlmgrenChrissImpact:
    temp_coef: float = 10.0       # 临时冲击系数 η(bps),作用于参与率^α
    perm_coef: float = 5.0        # 永久冲击系数 γ(bps),线性作用于参与率
    temp_exponent: float = 0.5    # 临时冲击指数 α(平方根律,文献 0.5–0.6)

    def _participation(self, trade_notional: float, adv_notional: float) -> float:
        if adv_notional <= 0 or trade_notional == 0:
            return 0.0
        return abs(trade_notional) / adv_notional

    def temporary_bps(self, trade_notional: float, adv_notional: float) -> float:
        p = self._participation(trade_notional, adv_notional)
        return 0.0 if p == 0 else self.temp_coef * (p ** self.temp_exponent)

    def permanent_bps(self, trade_notional: float, adv_notional: float) -> float:
        return self.perm_coef * self._participation(trade_notional, adv_notional)

    def total_cost_notional(self, trade_notional: float, adv_notional: float) -> float:
        bps = self.temporary_bps(trade_notional, adv_notional) + self.permanent_bps(trade_notional, adv_notional)
        return abs(trade_notional) * bps / 10_000.0
