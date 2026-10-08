"""Cross-sectional factor statistics independent of research orchestration."""

from __future__ import annotations

import statistics
from typing import Dict, List, Optional


def _rank(values: List[float]) -> List[float]:
    sorted_pairs = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    for rank, (index, _) in enumerate(sorted_pairs):
        ranks[index] = float(rank) / (len(values) - 1) if len(values) > 1 else 0.5
    return ranks


def compute_ic(
    factor_values: List[float], forward_returns: List[float]
) -> Dict[str, Optional[float]]:
    """Compute Pearson IC, rank IC, dispersion, and ICIR for one cross-section."""
    n = min(len(factor_values), len(forward_returns))
    if n < 10:
        return {"ic_mean": None, "rank_ic": None, "icir": None, "ic_std": None}

    mean_factor = sum(factor_values[:n]) / n
    mean_return = sum(forward_returns[:n]) / n
    covariance = sum(
        (factor_values[index] - mean_factor)
        * (forward_returns[index] - mean_return)
        for index in range(n)
    ) / n
    factor_std = (
        sum((value - mean_factor) ** 2 for value in factor_values[:n]) / n
    ) ** 0.5
    return_std = (
        sum((value - mean_return) ** 2 for value in forward_returns[:n]) / n
    ) ** 0.5
    ic = (
        covariance / (factor_std * return_std)
        if factor_std > 0 and return_std > 0
        else None
    )

    ranked_factor = _rank(factor_values[:n])
    ranked_return = _rank(forward_returns[:n])
    mean_ranked_factor = sum(ranked_factor) / n
    mean_ranked_return = sum(ranked_return) / n
    ranked_covariance = sum(
        (ranked_factor[index] - mean_ranked_factor)
        * (ranked_return[index] - mean_ranked_return)
        for index in range(n)
    ) / n
    ranked_factor_std = (
        sum((value - mean_ranked_factor) ** 2 for value in ranked_factor) / n
    ) ** 0.5
    ranked_return_std = (
        sum((value - mean_ranked_return) ** 2 for value in ranked_return) / n
    ) ** 0.5
    rank_ic = (
        ranked_covariance / (ranked_factor_std * ranked_return_std)
        if ranked_factor_std > 0 and ranked_return_std > 0
        else None
    )

    ic_std = (
        statistics.stdev(
            [
                (factor_values[index] - mean_factor)
                * (forward_returns[index] - mean_return)
                / (factor_std * return_std)
                for index in range(n)
            ]
        )
        if ic is not None
        else None
    )
    icir = ic / ic_std if ic is not None and ic_std is not None and ic_std > 0 else None
    return {"ic_mean": ic, "rank_ic": rank_ic, "icir": icir, "ic_std": ic_std}
