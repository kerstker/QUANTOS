"""Pre-trade RiskLimits (Phase 8). Frozen; every limit has a tight default and
there is NO sentinel meaning "unlimited" (review fail-closed requirement). Empty
allow-lists reject everything until explicitly populated."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from quantos.contracts.hashing import stable_hash


def _finite_scalar(name: str, value, *, positive: bool = False) -> None:
    """Reject a limit that the comparison check it feeds cannot see (P0-1).

    Every limit in checks.py sits on the RIGHT of a `>`/`<` comparison, so a NaN
    limit makes the check silently no-op (`notional > NaN` is False) and a negative
    one inverts it (`rate_window_ms=-1` looks 1ms into the future). Exact-type test:
    a bool is not a number here. `0` stays legal — a tight limit is fail-closed, not
    "unlimited"; live/execution/demo_batch_risk._scaled_limits had to re-validate
    every field from outside because this dataclass left them open.
    """
    if type(value) is not int and (type(value) is not float or not math.isfinite(value)):
        # Exact type: bool/str/Decimal are not limits. int is always finite (and
        # float(a huge int) would raise OverflowError instead of ValueError).
        raise ValueError(f"{name} must be a finite number")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'non-negative'}")


# Every RiskLimits scalar that must be finite and non-negative. rate_window_ms is
# validated separately (a non-positive window collapses the rate guard and fails
# OPEN) and min_cancel_rate_samples keeps its own integer rule below.
_NON_NEGATIVE_SCALARS = (
    "default_max_position_qty",
    "max_order_notional",
    "fat_finger_qty",
    "max_price_deviation_pct",
    "max_gross_exposure",
    "max_net_exposure",
    "max_leverage",
    "max_single_name_pct",
    "daily_loss_limit",
    "max_drawdown",
    "max_turnover",
    "max_orders_per_window",
    "max_cancel_rate",
    "max_participation_rate",
    "max_instrument_vol",
    "cash_buffer_pct",
)


@dataclass(frozen=True)
class RiskLimits:
    # allow-lists (empty default => fail-closed reject-all)
    allowed_instruments: frozenset[str] = frozenset()
    allowed_accounts: frozenset[str] = frozenset()
    # per-instrument position / order size
    default_max_position_qty: int = 1_000
    max_position_qty: tuple[tuple[str, int], ...] = ()      # frozen mapping (instrument -> cap)
    max_order_notional: float = 1_000_000.0                 # fat-finger notional
    fat_finger_qty: int = 100_000                           # fat-finger absolute qty
    # price sanity
    max_price_deviation_pct: float = 0.10
    # exposure / leverage (all -> POSITION_LIMIT_EXCEEDED)
    max_gross_exposure: float = 5_000_000.0
    max_net_exposure: float = 3_000_000.0
    max_leverage: float = 2.0
    # concentration
    max_single_name_pct: float = 0.20
    # loss / drawdown
    daily_loss_limit: float = 100_000.0                     # reject if day_pnl <= -limit (AT limit rejects)
    max_drawdown: float = 0.20
    # turnover / rate (rate -> TURNOVER_LIMIT_EXCEEDED)
    max_turnover: float = 1.0
    max_orders_per_window: int = 50                         # AT limit rejects (>=)
    # 单位不变量(审计 P1-5):此字段与 ctx.now / ctx.recent_timestamps 同单位——live 路径全是**毫秒**。
    # 旧名 rate_window_s 是个陷阱:它被当 ms 从 ms 的 ctx.now 相减(check_rate_limit),"_s"名不副实,
    # 把它设成秒会让窗口塌成毫秒级、护栏失效。改名 rate_window_ms 消除这个未来必然误导的地雷。
    rate_window_ms: int = 60_000                             # 60s(dry-run 无害占位;live 由 gate_limits 覆盖)
    max_cancel_rate: float = 0.80
    min_cancel_rate_samples: int = 20
    # liquidity / volatility
    max_participation_rate: float = 0.10
    max_instrument_vol: float = 0.80
    # cash buffer
    cash_buffer_pct: float = 0.0

    def __post_init__(self) -> None:
        for name in _NON_NEGATIVE_SCALARS:
            _finite_scalar(name, getattr(self, name))
        # 非正窗口 = 护栏结构性失效(check_rate_limit 的 cutoff 越过 now,窗内恒 0 单)。
        _finite_scalar("rate_window_ms", self.rate_window_ms, positive=True)
        if not 0.0 <= self.max_cancel_rate <= 1.0:
            raise ValueError("max_cancel_rate must be between 0 and 1")
        # P0-1: NaN < 1 is False — the old `samples < 1` rule let NaN through the floor.
        if type(self.min_cancel_rate_samples) is not int or self.min_cancel_rate_samples < 1:
            raise ValueError("min_cancel_rate_samples must be a positive integer")
        seen = set()
        for inst, cap in self.max_position_qty:
            if inst in seen:
                raise ValueError(f"duplicate instrument in max_position_qty: {inst}")
            seen.add(inst)
            # NaN cap -> `abs(proj) > cap` is False -> the position limit disappears.
            _finite_scalar(f"max_position_qty[{inst}]", cap)

    def position_cap(self, instrument_id: str) -> int:
        for inst, cap in self.max_position_qty:
            if inst == instrument_id:
                return cap
        return self.default_max_position_qty

    def limits_hash(self) -> str:
        d = asdict(self)
        d["allowed_instruments"] = sorted(self.allowed_instruments)
        d["allowed_accounts"] = sorted(self.allowed_accounts)
        d["max_position_qty"] = sorted([list(x) for x in self.max_position_qty])
        return stable_hash(d)
