"""End-to-end research validation pipeline (Phase 5 wiring).

Ties the real machinery together so a ValidationResult reflects ACTUAL
out-of-sample evidence rather than caller-supplied numbers:

  walk-forward backtest  ->  OOS returns per candidate
  candidates x folds      ->  PBO (combinatorially symmetric CV)
  best candidate's OOS    ->  observed Sharpe -> Deflated Sharpe (n_trials = #candidates)
  leakage check           ->  leakage_ok
  ----------------------------------------------------------------------
  ValidationGate.evaluate(...) -> PASS / WARN / FAIL / REQUIRES_HUMAN_REVIEW

`candidate_targets` are the strategy variants that were tried (the multiple-
testing trials). var_sr is estimated from the spread of the candidates' Sharpe
estimates.
"""
from __future__ import annotations

import statistics
from typing import Optional, Sequence

from quantos.contracts.cost import CostModel
from quantos.contracts.data import Bar
from quantos.contracts.research import ValidationResult
from services.research.ports.backtest import BacktestExecutionPort, BacktestOutcome
from services.validation.deflated_sharpe import deflated_sharpe_ratio, pbo_cscv
from services.validation.gate import ValidationGate
from services.validation.performance import returns_from_equity, sharpe
from services.validation.walk_forward import walk_forward_splits

_VAR_SR_FLOOR = 1e-4


def _oos(backtest, bars, targets, splits, cost):
    """Per-bar OOS returns (concatenated across test windows) and per-fold return."""
    per_bar: list[float] = []
    per_fold: list[float] = []
    for _train, test in splits:
        tb = [bars[i] for i in test]
        tt = [targets[i] for i in test]
        res = backtest.run(tb, tt, cost=cost)
        if not isinstance(res, BacktestOutcome):
            raise TypeError("backtest must return BacktestOutcome")
        if len(res.equity_curve) != len(tb):
            raise ValueError("backtest equity_curve must align with OOS bars")
        per_bar.extend(returns_from_equity(res.equity_curve))
        per_fold.append(res.total_pnl / res.initial_capital)
    return per_bar, per_fold


def _observed_sr(returns: Sequence[float]) -> float:
    if len(returns) < 2:
        return 0.0
    sd = statistics.pstdev(returns)
    return statistics.fmean(returns) / sd if sd > 0 else 0.0


def run_research_validation(
    subject_id: str,
    bars: Sequence[Bar],
    candidate_targets: Sequence[Sequence[int]],
    *,
    backtest: BacktestExecutionPort,
    train_size: int,
    test_size: int,
    cost: CostModel = CostModel(),
    leakage_ok: bool,
    regime_profitable_frac: float = 1.0,
    gate: Optional[ValidationGate] = None,
    n_trials_override: Optional[int] = None,
    var_sr_override: Optional[float] = None,
) -> ValidationResult:
    """`leakage_ok` is REQUIRED (no passing default): the gate trusts it, so the caller must COMPUTE
    it — via the Bar-level `services.features.leakage.detect_leakage` or the cross-sectional
    `services.research.expression_leakage.compute_leakage_ok` — rather than letting a silent True wave
    a leaking strategy through (audit fix: this was the one non-fail-closed leakage input).

    `backtest` is a required port so validation cannot silently select a concrete
    execution engine. `n_trials_override` / `var_sr_override` let a caller (the Alpha Factory
    catalog) inject the CATALOG-WIDE trial count and Sharpe-variance estimate, so
    Deflated Sharpe corrects for every alpha ever tried — not just the local
    candidate batch. Both must come from the same (catalog) source."""
    gate = gate or ValidationGate()
    if not isinstance(backtest, BacktestExecutionPort):
        raise TypeError("backtest must implement BacktestExecutionPort")
    if not candidate_targets:
        raise ValueError("need at least one candidate strategy")
    splits = walk_forward_splits(len(bars), train_size, test_size)
    if not splits:
        raise ValueError("not enough bars for the requested walk-forward windows")

    cand_per_bar = []
    cand_per_fold = []
    for targets in candidate_targets:
        if len(targets) != len(bars):
            raise ValueError("each candidate's targets must align with bars")
        pb, pf = _oos(backtest, bars, targets, splits, cost)
        cand_per_bar.append(pb)
        cand_per_fold.append(pf)

    n_folds = len(splits)
    n_trials = len(candidate_targets)

    # PBO over (folds x candidates) — needs >=2 of each
    if n_trials >= 2 and n_folds >= 2:
        perf = [[cand_per_fold[c][f] for c in range(n_trials)] for f in range(n_folds)]
        pbo = pbo_cscv(perf)
    else:
        pbo = 0.0

    # best candidate by total OOS return -> its OOS series feeds Sharpe/DSR
    best = max(range(n_trials), key=lambda c: sum(cand_per_fold[c]))
    oos = cand_per_bar[best]
    oos_sharpe = sharpe(oos)
    observed_sr = _observed_sr(oos)

    # var_sr estimated from spread of candidates' per-observation Sharpe
    cand_sr = [_observed_sr(pb) for pb in cand_per_bar if len(pb) > 1]
    var_sr = statistics.pstdev(cand_sr) ** 2 if len(cand_sr) > 1 else _VAR_SR_FLOOR
    var_sr = max(var_sr, _VAR_SR_FLOOR)

    # Catalog-wide multiple-testing: prefer the injected catalog counts so DSR
    # deflates for every alpha ever tried, and ValidationResult.trial_count
    # reflects the catalog (the lifecycle guard checks it equals the catalog).
    eff_trials = n_trials_override if n_trials_override is not None else n_trials
    eff_var_sr = max(var_sr_override, _VAR_SR_FLOOR) if var_sr_override is not None else var_sr

    dsr = deflated_sharpe_ratio(observed_sr, max(len(oos), 2), eff_trials, eff_var_sr) if len(oos) >= 2 else 0.0
    walk_forward_passed = oos_sharpe > 0.0

    return gate.evaluate(
        subject_id,
        oos_sharpe=oos_sharpe,
        deflated_sharpe=dsr,
        pbo=pbo,
        trial_count=eff_trials,
        leakage_ok=leakage_ok,
        regime_profitable_frac=regime_profitable_frac,
        walk_forward_passed=walk_forward_passed,
    )
