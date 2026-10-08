"""End-to-end research validation with catalog-minted evidence.

The pipeline runs every candidate through sequential OOS windows, retains the
exact CSCV/PBO matrix, writes the exact OOS return series into the authoritative
alpha catalog, and consumes the catalog's replayable Deflated-Sharpe evidence.
Candidates are ranked on the walk-forward TRAIN halves only: the single OOS
report belongs to the locked candidate and never feeds the selection.
Raw trial-count or variance overrides are intentionally not part of this API.
"""

from __future__ import annotations

from typing import Optional, Sequence

from quantos.contracts.cost import CostModel
from quantos.contracts.data import Bar
from quantos.contracts.research import ValidationResult, ValidationVerdict
from services.research.ports.backtest import (
    BacktestExecutionPort,
    BacktestOutcome,
)
from services.validation.catalog_evidence import (
    verify_catalog_evidence_against_catalog,
)
from services.validation.gate import ValidationGate
from services.validation.performance import returns_from_equity, sharpe
from services.validation.pbo_evidence import create_batch_pbo_evidence
from services.validation.walk_forward import walk_forward_splits


def _realising_event_times(window_bars, equity_curve, period_returns):
    """Event time of the bar that closes each realized return.

    A return produced between two equity observations is knowable only at the bar
    that closes it, so it is stamped with that bar.  ``returns_from_equity`` skips a
    pair whose base equity is zero; the same skip keeps the two axes in step instead
    of sliding an event time onto the wrong bar.
    """

    event_times = [
        bar.event_time
        for base, bar in zip(equity_curve, window_bars[1:])
        if base != 0
    ]
    if len(event_times) != len(period_returns):
        raise ValueError("per-bar returns must align with their realising bars")
    return event_times


def _oos(backtest, bars, targets, splits, cost):
    """Return concatenated per-bar OOS returns, one return per fold, and their event times."""

    per_bar: list[float] = []
    per_fold: list[float] = []
    event_times: list[int] = []
    for _train, test in splits:
        test_bars = [bars[index] for index in test]
        test_targets = [targets[index] for index in test]
        result = backtest.run(test_bars, test_targets, cost=cost)
        if not isinstance(result, BacktestOutcome):
            raise TypeError("backtest must return BacktestOutcome")
        if len(result.equity_curve) != len(test_bars):
            raise ValueError("backtest equity_curve must align with OOS bars")
        period_returns = returns_from_equity(result.equity_curve)
        per_bar.extend(period_returns)
        event_times.extend(
            _realising_event_times(test_bars, result.equity_curve, period_returns)
        )
        per_fold.append(result.total_pnl / result.initial_capital)
    return per_bar, per_fold, event_times


def _train_returns(backtest, bars, targets, splits, cost):
    """Score the same windows on their TRAIN halves; reuse ``_oos`` rather than fork it.

    Candidate selection must not see the folds that are later reported as the
    out-of-sample evidence, so the selection key is computed on ``(train, train)``.
    """

    return _oos(backtest, bars, targets, [(train, train) for train, _ in splits], cost)


def run_research_validation(
    subject_id: str,
    bars: Sequence[Bar],
    candidate_targets: Sequence[Sequence[int]],
    *,
    candidate_ids: Sequence[str],
    catalog,
    backtest: BacktestExecutionPort,
    train_size: int,
    test_size: int,
    cost: CostModel = CostModel(),
    leakage_ok: bool,
    regime_profitable_frac: float = 1.0,
    gate: Optional[ValidationGate] = None,
) -> ValidationResult:
    """Validate the selected best candidate from one catalog-bound batch.

    ``leakage_ok`` remains a required computed input.  ``candidate_ids`` must
    identify already-registered catalog definitions in the same order as
    ``candidate_targets``.  The caller must name the candidate selected on the
    walk-forward TRAIN halves as ``subject_id``; a mismatch fails rather than
    relabeling results, and the OOS folds are reported once, for that candidate.
    """

    gate = gate or ValidationGate()
    if not isinstance(backtest, BacktestExecutionPort):
        raise TypeError("backtest must implement BacktestExecutionPort")
    if len(candidate_targets) < 2:
        raise ValueError(
            "PBO evidence requires at least two candidate strategies"
        )
    if (
        len(candidate_ids) != len(candidate_targets)
        or len(set(candidate_ids)) != len(candidate_ids)
    ):
        raise ValueError("candidate_ids must be distinct and align with targets")
    splits = walk_forward_splits(len(bars), train_size, test_size)
    if not splits:
        raise ValueError("not enough bars for the requested walk-forward windows")
    if not (2 <= len(splits) <= 8):
        raise ValueError(
            "replayable PBO evidence requires between 2 and 8 folds"
        )

    candidate_per_bar: list[list[float]] = []
    candidate_per_bar_events: list[list[int]] = []
    candidate_train_per_fold: list[list[float]] = []
    for targets in candidate_targets:
        if len(targets) != len(bars):
            raise ValueError("each candidate's targets must align with bars")
        per_bar, _oos_per_fold, event_times = _oos(backtest, bars, targets, splits, cost)
        _train_per_bar, train_per_fold, _train_events = _train_returns(
            backtest, bars, targets, splits, cost
        )
        candidate_per_bar.append(per_bar)
        candidate_per_bar_events.append(event_times)
        candidate_train_per_fold.append(train_per_fold)

    definition_hashes = tuple(
        catalog.get(candidate_id).definition_hash
        for candidate_id in candidate_ids
    )
    if not set(definition_hashes) <= set(catalog.snapshot().trials):
        raise ValueError(
            "candidate definitions are absent from the catalog ledger"
        )

    # Each OOS return carries the event time of its OWN bar (the bar that realises
    # it), never the head of the full sample, which is train-heavy and shorter than
    # the walk-forward windows allow.
    pbo_evidence = create_batch_pbo_evidence(
        return_series_by_definition={
            definition_hash: tuple(
                (event_time, float(value))
                for event_time, value in zip(event_times, candidate_returns)
            )
            for definition_hash, candidate_returns, event_times in zip(
                definition_hashes, candidate_per_bar, candidate_per_bar_events
            )
        },
    )

    # Selection (and therefore the caller's ``subject_id``) is decided on the TRAIN
    # halves only.  The OOS stream below is reported once, for the locked candidate.
    best = max(
        range(len(candidate_ids)),
        key=lambda candidate: sum(candidate_train_per_fold[candidate]),
    )
    if candidate_ids[best] != subject_id:
        raise ValueError(
            "subject_id must identify the selected best candidate"
        )

    for candidate_id, candidate_returns in zip(
        candidate_ids, candidate_per_bar, strict=True
    ):
        catalog.set_returns(candidate_id, candidate_returns)
    catalog_evidence = catalog.evidence(subject_id)
    if catalog_evidence is None:
        return ValidationResult(
            subject_id=subject_id,
            verdict=ValidationVerdict.REQUIRES_HUMAN_REVIEW,
            reasons=("INSUFFICIENT_CATALOG_FOR_DSR",),
        )
    if not verify_catalog_evidence_against_catalog(
        catalog_evidence, catalog, subject_id
    ):
        return ValidationResult(
            subject_id=subject_id,
            verdict=ValidationVerdict.FAIL,
            reasons=("CATALOG_EVIDENCE_INVALID",),
        )

    oos = candidate_per_bar[best]
    oos_sharpe = sharpe(oos)
    return gate.evaluate(
        subject_id,
        oos_sharpe=oos_sharpe,
        catalog_evidence=catalog_evidence,
        pbo_evidence=pbo_evidence,
        leakage_ok=leakage_ok,
        regime_profitable_frac=regime_profitable_frac,
        walk_forward_passed=oos_sharpe > 0.0,
    )
