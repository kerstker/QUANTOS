"""Portfolio layer policy constants (Phase 7). Frozen, single home, mirrors the
alpha/validation config pattern."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PortfolioConfig:
    min_overlap: int = 60           # min shared points for a trustworthy covariance
    shrink_intensity: float = 0.20  # covariance shrinkage toward the diagonal
    max_weight: float = 0.40        # per-alpha concentration cap
    no_trade_band: float = 0.02     # suppress rebalances smaller than this (cost control)
