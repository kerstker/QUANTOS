"""Past-only realized-volatility primitives shared by validation components."""

from __future__ import annotations

import statistics
from typing import Optional, Sequence


def trailing_vol(closes: Sequence[float], end: int, window: int) -> Optional[float]:
    """Return trailing simple-return volatility using only closes through ``end``."""
    if window < 2 or end < window:
        return None
    returns = []
    for index in range(end - window + 1, end + 1):
        if closes[index - 1] == 0:
            return None
        returns.append(closes[index] / closes[index - 1] - 1.0)
    return statistics.pstdev(returns) if len(returns) >= 2 else None
