"""Validation gate (Phase 5): the promotion chokepoint.

Produces a ValidationResult (PASS / WARN / FAIL / REQUIRES_HUMAN_REVIEW). A
strategy that leaks, is overfit (low Deflated Sharpe / high PBO), or is
single-regime CANNOT pass. Out-of-range inputs FAIL closed. Many trials — or
many trials with marginal stats — force human review.

GOVERNANCE NOTE: the gate trusts that `oos_sharpe`, `deflated_sharpe`, `pbo` and
`walk_forward_passed` were computed honestly and without lookahead by the
upstream backtest/validation pipeline. It is a metrics arbiter, not a proof that
the backtest was run — the caller must guarantee provenance (e.g. a stored
reproducibility hash / audit record).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from quantos.contracts.research import ValidationResult, ValidationVerdict


@dataclass(frozen=True)
class ValidationConfig:
    min_oos_sharpe: float = 0.5
    min_deflated_sharpe: float = 0.60     # DSR is a probability in [0,1]
    max_pbo: float = 0.50
    min_profitable_regime_frac: float = 0.50
    human_review_trials: int = 30         # this many trials => human review even if stats pass
    marginal_review_trials: int = 20      # fewer trials but marginal DSR => human review
    marginal_deflated_sharpe: float = 0.70


class ValidationGate:
    def __init__(self, config: ValidationConfig | None = None):
        self.config = config or ValidationConfig()

    def evaluate(
        self,
        subject_id: str,
        *,
        oos_sharpe: float,
        deflated_sharpe: float,
        pbo: float,
        trial_count: int,
        leakage_ok: bool,
        regime_profitable_frac: float,
        walk_forward_passed: bool,
    ) -> ValidationResult:
        c = self.config

        # Fail-closed input validation: impossible metrics must never pass.
        bad = []
        if not (0.0 <= deflated_sharpe <= 1.0):
            bad.append(f"deflated_sharpe={deflated_sharpe} not in [0,1]")
        if not (0.0 <= pbo <= 1.0):
            bad.append(f"pbo={pbo} not in [0,1]")
        if not (0.0 <= regime_profitable_frac <= 1.0):
            bad.append(f"regime_profitable_frac={regime_profitable_frac} not in [0,1]")
        if not math.isfinite(oos_sharpe):
            bad.append(f"oos_sharpe={oos_sharpe} is not finite")
        if trial_count < 1:
            bad.append(f"trial_count={trial_count} < 1")
        if bad:
            return ValidationResult(subject_id=subject_id, verdict=ValidationVerdict.FAIL,
                                    deflated_sharpe=min(max(deflated_sharpe, 0.0), 1.0),
                                    pbo=min(max(pbo, 0.0), 1.0), trial_count=max(trial_count, 1),
                                    reasons=tuple(["INVALID_INPUT"] + bad))

        serious: list[str] = []
        soft: list[str] = []
        if not leakage_ok:
            serious.append("LEAKAGE_DETECTED")
        if deflated_sharpe < c.min_deflated_sharpe:
            serious.append(f"DEFLATED_SHARPE_BELOW_MIN ({deflated_sharpe:.2f}<{c.min_deflated_sharpe}); multiple-testing")
        if pbo > c.max_pbo:
            serious.append(f"PBO_TOO_HIGH ({pbo:.2f}>{c.max_pbo}); overfit")
        if regime_profitable_frac < c.min_profitable_regime_frac:
            serious.append(f"REGIME_UNSTABLE ({regime_profitable_frac:.2f}<{c.min_profitable_regime_frac})")
        if oos_sharpe < c.min_oos_sharpe:
            soft.append(f"OOS_SHARPE_BELOW_MIN ({oos_sharpe:.2f}<{c.min_oos_sharpe})")
        if not walk_forward_passed:
            soft.append("WALK_FORWARD_FAILED")

        reasons = serious + soft
        needs_review = (
            trial_count >= c.human_review_trials
            or (trial_count >= c.marginal_review_trials and deflated_sharpe < c.marginal_deflated_sharpe)
        )
        if serious:
            verdict = ValidationVerdict.FAIL
            if needs_review:
                reasons = reasons + [f"HIGH_TRIAL_COUNT ({trial_count}); review overfitting risk"]
        elif len(soft) >= 2:
            verdict = ValidationVerdict.FAIL
        elif len(soft) == 1:
            verdict = ValidationVerdict.WARN
        elif needs_review:
            verdict = ValidationVerdict.REQUIRES_HUMAN_REVIEW
            reasons = [f"HIGH_OR_MARGINAL_TRIALS (trials={trial_count}, dsr={deflated_sharpe:.2f}); human review required"]
        else:
            verdict = ValidationVerdict.PASS

        return ValidationResult(
            subject_id=subject_id, verdict=verdict,
            deflated_sharpe=deflated_sharpe, pbo=pbo, trial_count=trial_count,
            reasons=tuple(reasons),
        )
