"""End-to-end AI Agent Automation & Strategy Graduation Orchestrator.

Bridges autonomous AI alpha discovery (`services/intelligence/`) with human
governance & bounded mandate drafting (`live/governance/`) without ever
granting direct deployment authority to an AI agent.

Safety boundaries (immutable):
  - `ai_direct_authority` is always False.
  - `requires_human_signature` is always True.
  - Legacy `INVALIDATED_PERP_LEDGER_V1` records are unconditionally rejected.
  - Mainnet graduation stays fail-closed (`BLOCKED_EXTERNAL_VERIFIER_REQUIRED`)
    unless an independently controlled external cryptographic verifier has
    signed and verified the exact strategy + mandate envelope.
  - Proposed mandate bounds can only tighten — never exceed — the system risk
    policy ceiling.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence, Tuple

INVALIDATED_LEGACY_LEDGER = "INVALIDATED_PERP_LEDGER_V1"
V2_PERP_LEDGER = "LINEAR_PERP_LEDGER_V2"

STAGE_PASS = "PASS"
STAGE_FAIL = "FAIL"
STAGE_BLOCKED = "BLOCKED"

VERDICT_REJECTED = "REJECTED"
VERDICT_TESTNET_MANDATE_READY = "TESTNET_MANDATE_READY"
VERDICT_BLOCKED_EXTERNAL_VERIFIER_REQUIRED = "BLOCKED_EXTERNAL_VERIFIER_REQUIRED"
VERDICT_MAINNET_HUMAN_SIGN_READY = "MAINNET_HUMAN_SIGN_READY"

_ALLOWED_TARGET_VENUES = frozenset({"testnet", "mainnet"})
_ALLOWED_EXCHANGES = frozenset({"binance", "bybit", "mexc"})


def _is_finite_real(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _canonical_sha256(payload: Mapping[str, object]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class StrategyEvidenceBundle:
    """Consolidated evidence collected across research, risk, and soak stages."""

    strategy_id: str
    strategy_version_hash: str
    target_venue: str                      # "testnet" | "mainnet"
    exchange: str                          # "binance" | "bybit" | "mexc"
    allowed_instruments: Tuple[str, ...]

    # Stage 1: PIT data & panel quality
    pit_as_of_verified: bool
    leakage_audit_passed: bool
    panel_freshness_days: float
    max_allowed_freshness_days: float = 1.0

    # Stage 2: V2 perpetual ledger & statistical validation
    ledger_version: str = V2_PERP_LEDGER
    includes_fees_funding_slippage: bool = False
    oos_net_sharpe: float = 0.0
    deflated_sharpe_prob: float = 0.0
    cpcv_passed: bool = False
    min_oos_sharpe: float = 0.75
    min_deflated_sharpe_prob: float = 0.80

    # Stage 3: 9-engine research risk & drawdown
    research_risk_gates_passed: int = 0
    research_risk_gates_total: int = 9
    max_backtest_drawdown: float = 1.0
    max_allowed_drawdown: float = 0.15

    # Stage 4: Paper / Testnet soak & TCA parity
    soak_hours_completed: float = 0.0
    min_soak_hours: float = 72.0
    observed_fills: int = 0
    min_observed_fills: int = 20
    realized_slippage_bps: float = 999.0
    max_slippage_bps: float = 12.0
    ledger_reconciliation_drift_usd: float = 999.0
    max_reconciliation_drift_usd: float = 0.05

    # Stage 5: Independent authority & human governance
    human_reviewer_id: str = ""
    external_verifier_integrated: bool = False
    external_signature_verified: bool = False

    # Stage 6: Runtime execution guardrails
    pre_trade_risk_gate_active: bool = False
    kill_switch_ready: bool = False
    drawdown_controller_active: bool = False
    exchange_filters_configured: bool = False


@dataclass(frozen=True)
class SystemRiskCeiling:
    """Hard institutional risk envelope that an AI-proposed mandate may never exceed."""

    allowed_instruments: Tuple[str, ...]
    max_single_order_notional: float
    max_gross_notional: float
    max_net_notional: float
    max_validity_hours: int = 24


@dataclass(frozen=True)
class StageEvaluation:
    stage_id: str
    title: str
    status: str                            # PASS | FAIL | BLOCKED
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class GraduationReport:
    strategy_id: str
    strategy_version_hash: str
    target_venue: str
    exchange: str
    verdict: str
    stages: Tuple[StageEvaluation, ...]
    report_sha256: str
    ai_direct_authority: bool = False
    requires_human_signature: bool = True


def _validate_ceiling(ceiling: SystemRiskCeiling) -> None:
    if not ceiling.allowed_instruments:
        raise ValueError("system risk ceiling must define at least one allowed instrument")
    for name, val in (
        ("max_single_order_notional", ceiling.max_single_order_notional),
        ("max_gross_notional", ceiling.max_gross_notional),
        ("max_net_notional", ceiling.max_net_notional),
    ):
        if not _is_finite_real(val) or float(val) <= 0.0:
            raise ValueError(f"system risk ceiling {name} must be finite and > 0, got {val!r}")
    if float(ceiling.max_single_order_notional) > float(ceiling.max_gross_notional):
        raise ValueError("max_single_order_notional cannot exceed max_gross_notional")
    if float(ceiling.max_net_notional) > float(ceiling.max_gross_notional):
        raise ValueError("max_net_notional cannot exceed max_gross_notional")
    if isinstance(ceiling.max_validity_hours, bool) or not isinstance(ceiling.max_validity_hours, int) or ceiling.max_validity_hours < 1:
        raise ValueError("max_validity_hours must be an integer >= 1")


def evaluate_graduation_readiness(evidence: StrategyEvidenceBundle) -> GraduationReport:
    """Fail-closed 6-stage readiness evaluator from AI research to bounded mandate."""
    if not isinstance(evidence.strategy_id, str) or not evidence.strategy_id.strip():
        raise ValueError("strategy_id must be a non-empty string")
    if not isinstance(evidence.strategy_version_hash, str) or not evidence.strategy_version_hash.strip():
        raise ValueError("strategy_version_hash must be a non-empty string")
    if evidence.target_venue not in _ALLOWED_TARGET_VENUES:
        raise ValueError(f"unsupported target_venue: {evidence.target_venue!r}")
    if evidence.exchange not in _ALLOWED_EXCHANGES:
        raise ValueError(f"unsupported exchange: {evidence.exchange!r}")

    stages: list[StageEvaluation] = []

    # Stage 1: PIT Data & Quality Integrity
    s1_reasons: list[str] = []
    if not evidence.pit_as_of_verified:
        s1_reasons.append("pit_as_of_not_verified")
    if not evidence.leakage_audit_passed:
        s1_reasons.append("leakage_audit_failed")
    if (
        not _is_finite_real(evidence.panel_freshness_days)
        or not _is_finite_real(evidence.max_allowed_freshness_days)
        or evidence.panel_freshness_days < 0.0
        or evidence.max_allowed_freshness_days <= 0.0
        or evidence.panel_freshness_days > evidence.max_allowed_freshness_days
    ):
        s1_reasons.append("panel_freshness_out_of_bounds")
    if not evidence.allowed_instruments:
        s1_reasons.append("no_allowed_instruments")
    stages.append(StageEvaluation(
        "stage_1_pit_data",
        "PIT Data & Leakage Integrity",
        STAGE_PASS if not s1_reasons else STAGE_FAIL,
        tuple(s1_reasons),
    ))

    # Stage 2: V2 Perpetual Ledger & Statistical Validation
    s2_reasons: list[str] = []
    if evidence.ledger_version == INVALIDATED_LEGACY_LEDGER or evidence.ledger_version != V2_PERP_LEDGER:
        s2_reasons.append(f"invalid_ledger_version:{evidence.ledger_version}")
    if not evidence.includes_fees_funding_slippage:
        s2_reasons.append("missing_realistic_cost_model")
    if (
        not _is_finite_real(evidence.oos_net_sharpe)
        or not _is_finite_real(evidence.min_oos_sharpe)
        or evidence.oos_net_sharpe < evidence.min_oos_sharpe
    ):
        s2_reasons.append("oos_net_sharpe_below_threshold")
    if (
        not _is_finite_real(evidence.deflated_sharpe_prob)
        or not _is_finite_real(evidence.min_deflated_sharpe_prob)
        or evidence.deflated_sharpe_prob < evidence.min_deflated_sharpe_prob
    ):
        s2_reasons.append("deflated_sharpe_below_threshold")
    if not evidence.cpcv_passed:
        s2_reasons.append("cpcv_validation_not_passed")
    stages.append(StageEvaluation(
        "stage_2_v2_validation",
        "V2 Perpetual Ledger & Statistical Validation",
        STAGE_PASS if not s2_reasons else STAGE_FAIL,
        tuple(s2_reasons),
    ))

    # Stage 3: 9-Engine Research Risk & Drawdown Resilience
    s3_reasons: list[str] = []
    if (
        isinstance(evidence.research_risk_gates_passed, bool)
        or isinstance(evidence.research_risk_gates_total, bool)
        or evidence.research_risk_gates_total <= 0
        or evidence.research_risk_gates_passed < evidence.research_risk_gates_total
    ):
        s3_reasons.append("research_risk_gates_incomplete")
    if (
        not _is_finite_real(evidence.max_backtest_drawdown)
        or not _is_finite_real(evidence.max_allowed_drawdown)
        or evidence.max_backtest_drawdown < 0.0
        or evidence.max_allowed_drawdown <= 0.0
        or evidence.max_backtest_drawdown > evidence.max_allowed_drawdown
    ):
        s3_reasons.append("backtest_drawdown_exceeds_limit")
    stages.append(StageEvaluation(
        "stage_3_research_risk",
        "9-Engine Research Risk & Drawdown",
        STAGE_PASS if not s3_reasons else STAGE_FAIL,
        tuple(s3_reasons),
    ))

    # Stage 4: Paper / Testnet Soak & TCA Parity
    # For initial testnet graduation, soak is required only when graduating to mainnet.
    s4_reasons: list[str] = []
    if evidence.target_venue == "mainnet":
        if (
            not _is_finite_real(evidence.soak_hours_completed)
            or not _is_finite_real(evidence.min_soak_hours)
            or evidence.soak_hours_completed < evidence.min_soak_hours
        ):
            s4_reasons.append("testnet_soak_hours_insufficient")
        if (
            isinstance(evidence.observed_fills, bool)
            or isinstance(evidence.min_observed_fills, bool)
            or evidence.observed_fills < evidence.min_observed_fills
        ):
            s4_reasons.append("testnet_observed_fills_insufficient")
        if (
            not _is_finite_real(evidence.realized_slippage_bps)
            or not _is_finite_real(evidence.max_slippage_bps)
            or evidence.realized_slippage_bps < 0.0
            or evidence.realized_slippage_bps > evidence.max_slippage_bps
        ):
            s4_reasons.append("tca_slippage_exceeds_budget")
        if (
            not _is_finite_real(evidence.ledger_reconciliation_drift_usd)
            or not _is_finite_real(evidence.max_reconciliation_drift_usd)
            or evidence.ledger_reconciliation_drift_usd < 0.0
            or evidence.ledger_reconciliation_drift_usd > evidence.max_reconciliation_drift_usd
        ):
            s4_reasons.append("ledger_reconciliation_drift_exceeded")
    stages.append(StageEvaluation(
        "stage_4_soak_tca",
        "Testnet Soak & TCA Reconciliation",
        STAGE_PASS if not s4_reasons else STAGE_FAIL,
        tuple(s4_reasons),
    ))

    # Stage 5: Independent External Authority & Human Mandate Envelope
    s5_reasons: list[str] = []
    s5_status = STAGE_PASS
    if not isinstance(evidence.human_reviewer_id, str) or not evidence.human_reviewer_id.strip():
        s5_reasons.append("human_reviewer_missing")
        s5_status = STAGE_FAIL
    if evidence.target_venue == "mainnet":
        if not evidence.external_verifier_integrated:
            s5_reasons.append("external_signed_authority_verifier_not_integrated")
            s5_status = STAGE_BLOCKED
        elif not evidence.external_signature_verified:
            s5_reasons.append("external_human_signature_missing_or_invalid")
            s5_status = STAGE_BLOCKED
    stages.append(StageEvaluation(
        "stage_5_human_authority",
        "Human Reviewer & Independent External Signer",
        s5_status,
        tuple(s5_reasons),
    ))

    # Stage 6: Runtime Deterministic Guardrails
    s6_reasons: list[str] = []
    if not evidence.pre_trade_risk_gate_active:
        s6_reasons.append("pre_trade_risk_gate_inactive")
    if not evidence.kill_switch_ready:
        s6_reasons.append("kill_switch_not_ready")
    if not evidence.drawdown_controller_active:
        s6_reasons.append("drawdown_controller_inactive")
    if not evidence.exchange_filters_configured:
        s6_reasons.append("exchange_filters_unconfigured")
    stages.append(StageEvaluation(
        "stage_6_runtime_guardrails",
        "Runtime Risk Gate, Kill-Switch & Drawdown Controller",
        STAGE_PASS if not s6_reasons else STAGE_FAIL,
        tuple(s6_reasons),
    ))

    # Determine final verdict (strictly fail-closed)
    any_fail = any(s.status == STAGE_FAIL for s in stages)
    any_blocked = any(s.status == STAGE_BLOCKED for s in stages)
    if any_fail:
        verdict = VERDICT_REJECTED
    elif any_blocked:
        verdict = VERDICT_BLOCKED_EXTERNAL_VERIFIER_REQUIRED
    elif evidence.target_venue == "testnet":
        verdict = VERDICT_TESTNET_MANDATE_READY
    else:
        verdict = VERDICT_MAINNET_HUMAN_SIGN_READY

    body = {
        "strategy_id": evidence.strategy_id.strip(),
        "strategy_version_hash": evidence.strategy_version_hash.strip(),
        "target_venue": evidence.target_venue,
        "exchange": evidence.exchange,
        "verdict": verdict,
        "stages": [
            {"stage_id": s.stage_id, "status": s.status, "reasons": list(s.reasons)}
            for s in stages
        ],
        "ai_direct_authority": False,
        "requires_human_signature": True,
    }
    return GraduationReport(
        strategy_id=evidence.strategy_id.strip(),
        strategy_version_hash=evidence.strategy_version_hash.strip(),
        target_venue=evidence.target_venue,
        exchange=evidence.exchange,
        verdict=verdict,
        stages=tuple(stages),
        report_sha256=_canonical_sha256(body),
        ai_direct_authority=False,
        requires_human_signature=True,
    )


def build_human_mandate_proposal(
    report: GraduationReport,
    ceiling: SystemRiskCeiling,
    *,
    requested_instruments: Sequence[str],
    requested_single_order_notional: float,
    requested_gross_notional: float,
    requested_net_notional: float,
    validity_hours: int,
    issued_at_ms: int,
    nonce: str,
    per_instrument_caps: Optional[Mapping[str, float]] = None,
) -> dict[str, object]:
    """Generate a tamper-evident unsigned Mandate Proposal for explicit human signing.

    Enforces tightening-only bounds relative to `ceiling`. Refuses to draft a
    proposal for REJECTED graduation reports. Never produces a signed mandate.
    """
    _validate_ceiling(ceiling)
    if report.verdict == VERDICT_REJECTED:
        raise ValueError("cannot draft mandate proposal for a REJECTED graduation report")
    if isinstance(issued_at_ms, bool) or not isinstance(issued_at_ms, int) or issued_at_ms < 0:
        raise ValueError("issued_at_ms must be a non-negative integer")
    if isinstance(validity_hours, bool) or not isinstance(validity_hours, int) or not (1 <= validity_hours <= ceiling.max_validity_hours):
        raise ValueError(f"validity_hours must be between 1 and {ceiling.max_validity_hours}")
    if not isinstance(nonce, str) or len(nonce.strip()) < 16:
        raise ValueError("nonce must be a string with at least 16 non-whitespace characters")

    allowed_set = set(ceiling.allowed_instruments)
    insts = tuple(sorted(set(i.strip() for i in requested_instruments if isinstance(i, str) and i.strip())))
    if not insts or not set(insts).issubset(allowed_set):
        raise ValueError("requested_instruments must be a non-empty subset of ceiling.allowed_instruments")

    for name, val, cap in (
        ("requested_single_order_notional", requested_single_order_notional, ceiling.max_single_order_notional),
        ("requested_gross_notional", requested_gross_notional, ceiling.max_gross_notional),
        ("requested_net_notional", requested_net_notional, ceiling.max_net_notional),
    ):
        if not _is_finite_real(val) or float(val) <= 0.0:
            raise ValueError(f"{name} must be finite and > 0")
        if float(val) > float(cap):
            raise ValueError(f"{name} ({val}) exceeds system risk ceiling ({cap})")

    if float(requested_single_order_notional) > float(requested_gross_notional):
        raise ValueError("requested_single_order_notional cannot exceed requested_gross_notional")
    if float(requested_net_notional) > float(requested_gross_notional):
        raise ValueError("requested_net_notional cannot exceed requested_gross_notional")

    caps_list: list[list[object]] = []
    if per_instrument_caps:
        for inst in sorted(per_instrument_caps.keys()):
            if inst not in insts:
                raise ValueError(f"per-instrument cap for {inst!r} is outside requested_instruments")
            cap_val = per_instrument_caps[inst]
            if not _is_finite_real(cap_val) or float(cap_val) <= 0.0 or float(cap_val) > float(requested_gross_notional):
                raise ValueError(f"invalid per-instrument cap for {inst!r}: {cap_val!r}")
            caps_list.append([inst, float(cap_val)])

    expires_at_ms = issued_at_ms + validity_hours * 3_600_000
    unsigned_payload: dict[str, object] = {
        "schema_version": 1,
        "status": "UNSIGNED_AWAITING_HUMAN_SIGNATURE",
        "ai_direct_authority": False,
        "requires_human_signature": True,
        "graduation_verdict": report.verdict,
        "graduation_report_sha256": report.report_sha256,
        "strategy_id": report.strategy_id,
        "strategy_version_hash": report.strategy_version_hash,
        "venue": report.target_venue,
        "exchange": report.exchange,
        "allowed_instruments": list(insts),
        "max_single_order_notional": float(requested_single_order_notional),
        "max_gross_notional": float(requested_gross_notional),
        "max_net_notional": float(requested_net_notional),
        "per_instrument_max_notional": caps_list,
        "issued_at_ms": issued_at_ms,
        "expires_at_ms": expires_at_ms,
        "nonce": nonce.strip(),
    }
    digest = _canonical_sha256(unsigned_payload)
    return {
        **unsigned_payload,
        "proposal_id": f"mandate-proposal-{digest[:20]}",
        "payload_sha256": digest,
    }
