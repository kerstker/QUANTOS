"""Backtest-vs-paper consistency (Phase 9).

Extracts the paper Sharpe from a PaperRun (its perf lives in a free-form metrics
dict) and computes the performance gap vs the alpha's backtest Sharpe. This gap is
exactly the signal the Phase-6 decay tracker / retirement consumes: a paper run
that materially underperforms its backtest is a decay trigger. Pure; imports only
the paper models (no cross-layer dependency — the orchestrator wires gap->decay)."""
from __future__ import annotations

from typing import Optional

from .models import PaperRun


def paper_sharpe(run: PaperRun) -> Optional[float]:
    s = run.metrics.get("sharpe") if run.metrics else None
    return float(s) if s is not None else None


def performance_gap(backtest_sharpe: Optional[float], paper_sharpe_value: Optional[float]) -> Optional[float]:
    """(backtest - paper) / backtest. None when it cannot be computed honestly
    (missing paper data, or non-positive backtest Sharpe)."""
    if backtest_sharpe is None or paper_sharpe_value is None or backtest_sharpe <= 0:
        return None
    return (backtest_sharpe - paper_sharpe_value) / backtest_sharpe


def is_consistent(backtest_sharpe: Optional[float], paper_sharpe_value: Optional[float],
                  max_gap: float = 0.6) -> bool:
    """Fail-closed: an un-computable gap (missing data) is treated as INCONSISTENT,
    so a promotion/retention decision can't lean on absent paper evidence."""
    gap = performance_gap(backtest_sharpe, paper_sharpe_value)
    return gap is not None and gap <= max_gap
