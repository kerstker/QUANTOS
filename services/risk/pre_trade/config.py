"""Pre-trade RiskLimits (Phase 8). Frozen; every limit has a tight default and
there is NO sentinel meaning "unlimited" (review fail-closed requirement). Empty
allow-lists reject everything until explicitly populated."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

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
    rate_window_s: int = 60
    # liquidity / volatility
    max_participation_rate: float = 0.10
    max_instrument_vol: float = 0.80
    # cash buffer
    cash_buffer_pct: float = 0.0

    def __post_init__(self) -> None:
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
