"""Validation gate (Phase 5): the promotion chokepoint.

Produces a ValidationResult (PASS / WARN / FAIL / REQUIRES_HUMAN_REVIEW). A
strategy that leaks, is overfit (low Deflated Sharpe / high PBO), or is
single-regime CANNOT pass. Many trials force human review even when the stats
look acceptable.
"""
from __future__ import annotations

from dataclasses import dataclass

from quantos.contracts.research import ValidationResult, ValidationVerdict


@dataclass(frozen=True)
class ValidationConfig:
    min_oos_sharpe: float = 0.5
    min_deflated_sharpe: float = 0.60     # DSR is a probability in [0,1]
    max_pbo: float = 0.50
    min_profitable_regime_frac: float = 0.50
    human_review_trials: int = 50         # many trials => human eyes even if stats pass


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
        if serious:
            verdict = ValidationVerdict.FAIL
        elif len(soft) >= 2:
            verdict = ValidationVerdict.FAIL
        elif len(soft) == 1:
            verdict = ValidationVerdict.WARN
        elif trial_count >= c.human_review_trials:
            verdict = ValidationVerdict.REQUIRES_HUMAN_REVIEW
            reasons = [f"HIGH_TRIAL_COUNT ({trial_count}); human review required"]
        else:
            verdict = ValidationVerdict.PASS

        return ValidationResult(
            subject_id=subject_id,
            verdict=verdict,
            deflated_sharpe=deflated_sharpe,
            pbo=pbo,
            trial_count=trial_count,
            reasons=tuple(reasons),
        )
