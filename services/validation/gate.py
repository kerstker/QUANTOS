"""Domain-neutral validation gate: the promotion evidence chokepoint.

Produces a ValidationResult (PASS / WARN / FAIL / REQUIRES_HUMAN_REVIEW). A
strategy that leaks, is overfit (low Deflated Sharpe / high PBO), or is
single-regime CANNOT pass. Out-of-range inputs FAIL closed. Many trials — or
many trials with marginal stats — force human review.

GOVERNANCE NOTE: the gate trusts that `oos_sharpe`, `deflated_sharpe`, `pbo` and
`walk_forward_passed` were computed honestly and without lookahead by the
upstream backtest/validation pipeline. It is a metrics arbiter, not a proof that
the backtest was run — the caller must guarantee provenance (e.g. a stored
reproducibility hash / audit record).

CALIBRATION NOTE (2026-07-10, gap audit rank #7): min_deflated_sharpe=0.60 is DELIBERATELY looser
than the ~0.95 "statistically significant" convention in the Bailey/Lopez de Prado Deflated-Sharpe
literature — a pre-filter ahead of MANDATORY human review, not a standalone significance claim. See
services/alpha/config.py::AlphaFactoryConfig's module docstring for the full rationale (this value
must stay equal to that module's own independently-declared copy — tests/alpha/
test_threshold_configs_agree.py locks the two together so a future tuning pass can't silently drift
them apart and make this gate's PASS/FAIL disagree with the later guard_to_validated transition
check). max_pbo=0.50 is the same honest-policy-default posture, not literature-derived.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from quantos.contracts.research import ValidationResult, ValidationVerdict
from quantos.contracts.validation import BatchPboEvidence, CatalogEvidence
from services.validation.catalog_evidence import verify_catalog_evidence
from services.validation.pbo_evidence import verify_batch_pbo_evidence


@dataclass(frozen=True)
class ValidationConfig:
    min_oos_sharpe: float = 0.5
    min_deflated_sharpe: float = 0.60     # DSR is a probability in [0,1]
    max_pbo: float = 0.50
    min_profitable_regime_frac: float = 0.50
    human_review_trials: int = 30         # this many trials => human review even if stats pass
    marginal_review_trials: int = 20      # fewer trials but marginal DSR => human review
    marginal_deflated_sharpe: float = 0.70
    min_capacity_ratio: float = 0.50      # 2026-07-10 (gap audit rank #5): soft-fail floor on
                                          # capacity_notional/target_aum — an alpha that can't
                                          # carry at least this fraction of its own target AUM
                                          # before market-impact costs erode it warrants a WARN,
                                          # mirroring min_oos_sharpe's existing soft-fail pattern
                                          # (this is the "at minimum WARN" framing, not a hard
                                          # FAIL — capacity realism is UNCALIBRATED_PRINCIPLED per
                                          # services/execution/impact.py, not literature-
                                          # derived, same honest-policy-default posture as
                                          # min_deflated_sharpe/min_marginal_ir_vs_approved). A
                                          # policy pre-filter, not a derived constant.


class ValidationGate:
    def __init__(self, config: ValidationConfig | None = None):
        self.config = config or ValidationConfig()

    def evaluate(
        self,
        subject_id: str,
        *,
        oos_sharpe: float,
        catalog_evidence: CatalogEvidence,
        pbo_evidence: BatchPboEvidence,
        leakage_ok: bool,
        regime_profitable_frac: float,
        walk_forward_passed: bool,
        capacity_ratio: Optional[float] = None,
    ) -> ValidationResult:
        c = self.config

        evidence_reasons = []
        if (
            not isinstance(catalog_evidence, CatalogEvidence)
            or not verify_catalog_evidence(catalog_evidence)
        ):
            evidence_reasons.append("CATALOG_EVIDENCE_INVALID")
        elif catalog_evidence.subject_id != subject_id:
            evidence_reasons.append("CATALOG_EVIDENCE_SUBJECT_MISMATCH")
        if (
            not isinstance(pbo_evidence, BatchPboEvidence)
            or not verify_batch_pbo_evidence(pbo_evidence)
        ):
            evidence_reasons.append("PBO_EVIDENCE_INVALID")
        elif (
            isinstance(catalog_evidence, CatalogEvidence)
            and catalog_evidence.subject_definition_hash
            not in pbo_evidence.candidate_definition_hashes
        ):
            evidence_reasons.append("PBO_EVIDENCE_SUBJECT_MISMATCH")
        if evidence_reasons:
            return ValidationResult(
                subject_id=subject_id,
                verdict=ValidationVerdict.FAIL,
                reasons=tuple(evidence_reasons),
            )

        deflated_sharpe = catalog_evidence.deflated_sharpe
        pbo = pbo_evidence.pbo
        trial_count = catalog_evidence.trial_count
        evidence_fields = {
            "catalog_evidence": catalog_evidence,
            "catalog_evidence_hash": catalog_evidence.evidence_hash,
            "pbo_evidence": pbo_evidence,
            "pbo_evidence_hash": pbo_evidence.evidence_hash,
        }

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
                                    reasons=tuple(["INVALID_INPUT"] + bad),
                                    **evidence_fields)

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
        if capacity_ratio is not None and capacity_ratio < c.min_capacity_ratio:
            # 2026-07-10: soft-fail, not serious — capacity realism is diagnostic/UNCALIBRATED
            # (see ValidationConfig.min_capacity_ratio docstring). None (not assessed, the
            # default) is a complete no-op, matching every existing caller unchanged.
            soft.append(f"CAPACITY_BELOW_TARGET_AUM ({capacity_ratio:.2f}<{c.min_capacity_ratio})")
        if not walk_forward_passed:
            # a real out-of-sample walk-forward failure is a hard red flag, not a
            # soft warning: the edge did not persist across held-out folds (review)
            serious.append("WALK_FORWARD_FAILED")

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
            **evidence_fields,
        )
