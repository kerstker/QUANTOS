"""Pre-trade RiskLimits (Phase 8). Frozen; every limit has a tight default and
there is NO sentinel meaning "unlimited" (review fail-closed requirement). Empty
allow-lists reject everything until explicitly populated."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from quantos.contracts.hashing import stable_hash


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
        if not 0.0 <= self.max_cancel_rate <= 1.0:
            raise ValueError("max_cancel_rate must be between 0 and 1")
        if self.min_cancel_rate_samples < 1:
            raise ValueError("min_cancel_rate_samples must be positive")
        seen = set()
        for inst, _cap in self.max_position_qty:
            if inst in seen:
                raise ValueError(f"duplicate instrument in max_position_qty: {inst}")
            seen.add(inst)

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
