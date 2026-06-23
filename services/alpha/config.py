"""Alpha Factory policy constants (Phase 6). Single home for all thresholds,
mirroring ValidationConfig. Frozen + deterministic."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AlphaFactoryConfig:
    rho_cut: float = 0.70                 # correlation cut for clustering / redundancy
    crowd_max: float = 0.60               # max public-factor R^2 before crowding penalty
    min_overlap: int = 60                 # min overlapping points for correlation/orthogonalization
    min_capacity_notional: float = 0.0
    min_ic_retention: float = 0.30        # decay: recent IC / baseline IC floor
    max_perf_gap: float = 0.60            # decay: (backtest - paper) / backtest ceiling
    min_half_life_frac: float = 0.50      # decay: half-life vs baseline floor
    confirm_windows: int = 3              # decay debounce (consecutive breaches)
    fdr_q: float = 0.10                   # Benjamini-Hochberg catalog-wide FDR
    var_sr_floor: float = 1e-4
    min_breadth: int = 10                 # min cross-section names per period (else IC undefined)
    min_periods: int = 12                 # min non-empty IC periods for a valid ICIR/t-stat
    composite_weights: dict[str, float] = field(default_factory=lambda: {
        # tilt toward the metrics hardest to overfit
        "icir": 0.22, "deflated_sharpe": 0.22, "capacity_adj_sharpe": 0.18,
        "marginal_ir": 0.16, "ic_tstat": 0.10, "hit_rate": 0.06, "neg_turnover": 0.06,
    })
