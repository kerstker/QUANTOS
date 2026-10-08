"""Unified fail-closed risk verdict for the continuous L2 Testnet cycle."""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from live.execution.reconciliation import reconcile_positions
from live.execution.risk_monitor import (
    LiveConstraintBreach,
    RiskBreach,
    evaluate_live_constraints,
    evaluate_risk_state,
    respond_to_breach_persisted,
)
from live.governance.mandate import Mandate
from live.governance.mandate_store import RiskSupervisorPolicy
from quantos.contracts.mft_execution import MFT_TESTNET_ENVIRONMENT
from services.risk.pre_trade.kill_switch_store import KillSwitchStateStore


class RiskSupervisorVerdict(str, Enum):
    ALLOW_TESTNET_NEW_RISK = "ALLOW_TESTNET_NEW_RISK"
    HALT_NEW_RISK = "HALT_NEW_RISK"
    REDUCE_ONLY_REQUIRED = "REDUCE_ONLY_REQUIRED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


@dataclass(frozen=True, slots=True)
class RiskSupervisorSnapshot:
    now_ms: int
    execution_environment: str
    venue: str
    exchange: str
    market_publishable: bool | None
    market_watermark_ms: int | None
    scheduler_last_success_ms: int | None
    mandate: Mandate | None
    account_equity: float | None
    peak_equity: float | None
    positions: Mapping[str, float] | None
    prices: Mapping[str, float] | None
    maint_margin_balance: float | None
    margin_maint_amount: float | None
    participation_rates: Mapping[str, float] | None
    ledger_positions: Mapping[str, float] | None
    unknown_order_count: int | None
    kill_switch_tripped: bool | None
    previous_cycle_at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class RiskSupervisorDecision:
    verdict: RiskSupervisorVerdict
    reasons: tuple[str, ...]
    latch_kill_switch: bool
    automatic_action: str
    evaluated_at_ms: int
    policy_id: str
    policy_version: str
    policy_hash: str
    risk_breach: RiskBreach | None
    constraint_breach: LiveConstraintBreach | None
    reconciliation: Mapping[str, object] | None


_REQUIRED_FIELDS = (
    "market_publishable",
    "market_watermark_ms",
    "scheduler_last_success_ms",
    "mandate",
    "account_equity",
    "peak_equity",
    "positions",
    "prices",
    "maint_margin_balance",
    "margin_maint_amount",
    "participation_rates",
    "ledger_positions",
    "unknown_order_count",
    "kill_switch_tripped",
)


def _time(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _finite_mapping(value: object) -> bool:
    return isinstance(value, Mapping) and all(
        isinstance(key, str) and key and _finite(item)
        for key, item in value.items()
    )


def _upper_reason(reason: str) -> str:
    head, separator, tail = reason.partition(":")
    return head.upper() + (separator + tail if separator else "")


def _append_unique(reasons: list[str], value: str) -> None:
    if value not in reasons:
        reasons.append(value)


def _decision(
    *,
    verdict: RiskSupervisorVerdict,
    reasons: list[str],
    latch: bool,
    now_ms: int,
    policy: RiskSupervisorPolicy,
    risk_breach: RiskBreach | None = None,
    constraint_breach: LiveConstraintBreach | None = None,
    reconciliation: Mapping[str, object] | None = None,
) -> RiskSupervisorDecision:
    actions = {
        RiskSupervisorVerdict.ALLOW_TESTNET_NEW_RISK: "NONE",
        RiskSupervisorVerdict.HALT_NEW_RISK: "HALT_NEW_RISK",
        RiskSupervisorVerdict.REDUCE_ONLY_REQUIRED: "REDUCE_ONLY",
        RiskSupervisorVerdict.MANUAL_REVIEW: "HALT_NEW_RISK",
    }
    return RiskSupervisorDecision(
        verdict=verdict,
        reasons=tuple(reasons),
        latch_kill_switch=latch,
        automatic_action=actions[verdict],
        evaluated_at_ms=now_ms,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_hash=policy.content_hash,
        risk_breach=risk_breach,
        constraint_breach=constraint_breach,
        reconciliation=reconciliation,
    )


def evaluate_risk_supervisor(
    snapshot: RiskSupervisorSnapshot,
    policy: RiskSupervisorPolicy,
) -> RiskSupervisorDecision:
    """Combine existing risk primitives into one deterministic cycle verdict."""

    if not isinstance(snapshot, RiskSupervisorSnapshot):
        raise TypeError("snapshot must be a RiskSupervisorSnapshot")
    if not isinstance(policy, RiskSupervisorPolicy):
        raise TypeError("policy must be a RiskSupervisorPolicy")
    now_ms = _time(snapshot.now_ms)
    if now_ms is None:
        return _decision(
            verdict=RiskSupervisorVerdict.HALT_NEW_RISK,
            reasons=["NOW_MS_INVALID"],
            latch=False,
            now_ms=0,
            policy=policy,
        )
    reasons: list[str] = []
    manual = False
    reduce = False
    latch = False
    risk_breach = None
    constraint_breach = None
    reconciliation = None
    try:
        for field in _REQUIRED_FIELDS:
            if getattr(snapshot, field) is None:
                _append_unique(reasons, f"MISSING_{field.upper()}")

        if snapshot.execution_environment != MFT_TESTNET_ENVIRONMENT:
            _append_unique(reasons, "EXECUTION_ENVIRONMENT_NOT_TESTNET")
        if not isinstance(snapshot.venue, str) or not snapshot.venue:
            _append_unique(reasons, "VENUE_INVALID")
        if not isinstance(snapshot.exchange, str) or not snapshot.exchange:
            _append_unique(reasons, "EXCHANGE_INVALID")
        if snapshot.market_publishable is not None:
            if not isinstance(snapshot.market_publishable, bool):
                _append_unique(reasons, "MARKET_PUBLISHABLE_INVALID")
            elif not snapshot.market_publishable:
                _append_unique(reasons, "MARKET_NOT_PUBLISHABLE")
        market_time = _time(snapshot.market_watermark_ms)
        if snapshot.market_watermark_ms is not None and market_time is None:
            _append_unique(reasons, "MARKET_WATERMARK_INVALID")
        elif market_time is not None:
            if market_time > now_ms:
                _append_unique(reasons, "MARKET_WATERMARK_TIME_REGRESSION")
            elif now_ms - market_time > policy.max_market_age_ms:
                _append_unique(reasons, "MARKET_WATERMARK_STALE")
        scheduler_time = _time(snapshot.scheduler_last_success_ms)
        if snapshot.scheduler_last_success_ms is not None and scheduler_time is None:
            _append_unique(reasons, "SCHEDULER_TIME_INVALID")
        elif scheduler_time is not None:
            if scheduler_time > now_ms:
                _append_unique(reasons, "SCHEDULER_TIME_REGRESSION")
            elif now_ms - scheduler_time > policy.max_scheduler_age_ms:
                _append_unique(reasons, "SCHEDULER_STALE")
        previous_time = _time(snapshot.previous_cycle_at_ms)
        if snapshot.previous_cycle_at_ms is not None and previous_time is None:
            _append_unique(reasons, "PREVIOUS_CYCLE_TIME_INVALID")
        elif previous_time is not None and previous_time > now_ms:
            _append_unique(reasons, "SUPERVISOR_TIME_REGRESSION")

        mandate = snapshot.mandate
        if mandate is not None:
            if not isinstance(mandate, Mandate):
                _append_unique(reasons, "MANDATE_INVALID")
            else:
                if mandate.venue != snapshot.venue:
                    _append_unique(reasons, "MANDATE_VENUE_MISMATCH")
                if mandate.exchange != snapshot.exchange:
                    _append_unique(reasons, "MANDATE_EXCHANGE_MISMATCH")
                if not mandate.approved_by:
                    _append_unique(reasons, "MANDATE_UNSIGNED")
                if not mandate.allowed_instruments:
                    _append_unique(reasons, "MANDATE_EMPTY_UNIVERSE")
                if (
                    not _finite(mandate.max_single_order_notional)
                    or float(mandate.max_single_order_notional) <= 0
                    or not _finite(mandate.max_gross_notional)
                    or float(mandate.max_gross_notional) <= 0
                ):
                    _append_unique(reasons, "MANDATE_NO_RISK_CAPACITY")
                if mandate.issued_at > now_ms:
                    _append_unique(reasons, "MANDATE_TIME_REGRESSION")
                if mandate.expires_at is None:
                    _append_unique(reasons, "MANDATE_EXPIRY_MISSING")
                elif mandate.expires_at < now_ms:
                    _append_unique(reasons, "MANDATE_EXPIRED")

        if snapshot.kill_switch_tripped is not None:
            if not isinstance(snapshot.kill_switch_tripped, bool):
                _append_unique(reasons, "KILL_SWITCH_STATE_INVALID")
            elif snapshot.kill_switch_tripped:
                _append_unique(reasons, "KILL_SWITCH_TRIPPED")

        for field in ("maint_margin_balance", "margin_maint_amount"):
            value = getattr(snapshot, field)
            if value is not None and (not _finite(value) or float(value) < 0):
                _append_unique(reasons, f"{field.upper()}_INVALID")

        unknown_count = snapshot.unknown_order_count
        if unknown_count is not None:
            if (
                isinstance(unknown_count, bool)
                or not isinstance(unknown_count, int)
                or unknown_count < 0
            ):
                _append_unique(reasons, "UNKNOWN_ORDER_COUNT_INVALID")
            elif unknown_count > 0:
                _append_unique(reasons, f"UNKNOWN_ORDERS_PRESENT:{unknown_count}")
                manual = True
                latch = True

        mappings = (
            snapshot.positions,
            snapshot.prices,
            snapshot.participation_rates,
            snapshot.ledger_positions,
        )
        if all(isinstance(value, Mapping) for value in mappings) and isinstance(
            mandate, Mandate
        ):
            risk_breach = evaluate_risk_state(
                equity=snapshot.account_equity,
                peak_equity=snapshot.peak_equity,
                positions=snapshot.positions,
                prices=snapshot.prices,
                max_drawdown_pct=policy.max_drawdown_pct,
                max_gross_exposure=mandate.max_gross_notional,
                max_net_exposure=mandate.max_net_notional,
                min_margin_ratio=policy.min_margin_ratio,
                maint_margin_balance=snapshot.maint_margin_balance,
                margin_maint_amount=snapshot.margin_maint_amount,
            )
            for raw_reason in risk_breach.reasons:
                reason = _upper_reason(raw_reason)
                _append_unique(reasons, reason)
                if reason.startswith("MARGIN_RATIO_BELOW_FLOOR"):
                    reduce = True
                    latch = True
                elif reason.startswith(
                    ("DRAWDOWN_", "GROSS_EXPOSURE_", "NET_EXPOSURE_")
                ):
                    reduce = True
            constraint_breach = evaluate_live_constraints(
                positions=snapshot.positions,
                prices=snapshot.prices,
                participation_rates=snapshot.participation_rates,
                max_single_name_fraction=policy.max_single_name_fraction,
                max_participation_rate=policy.max_participation_rate,
            )
            for raw_reason in constraint_breach.reasons:
                reason = _upper_reason(raw_reason)
                _append_unique(reasons, reason)
                if reason.startswith("CONCENTRATION_EXCEEDED"):
                    reduce = True
            if _finite_mapping(snapshot.positions) and _finite_mapping(
                snapshot.ledger_positions
            ):
                reconciliation = reconcile_positions(
                    snapshot.ledger_positions,
                    snapshot.positions,
                    tolerance=policy.reconciliation_tolerance,
                )
                if reconciliation["in_sync"] is not True:
                    _append_unique(
                        reasons, "POSITION_LEDGER_RECONCILIATION_FAILED"
                    )
                    manual = True
                    latch = True
        else:
            for field in (
                "positions",
                "prices",
                "participation_rates",
                "ledger_positions",
            ):
                value = getattr(snapshot, field)
                if value is not None and not isinstance(value, Mapping):
                    _append_unique(reasons, f"{field.upper()}_INVALID")

        if manual:
            verdict = RiskSupervisorVerdict.MANUAL_REVIEW
        elif reduce:
            verdict = RiskSupervisorVerdict.REDUCE_ONLY_REQUIRED
        elif reasons:
            verdict = RiskSupervisorVerdict.HALT_NEW_RISK
        else:
            verdict = RiskSupervisorVerdict.ALLOW_TESTNET_NEW_RISK
        return _decision(
            verdict=verdict,
            reasons=reasons,
            latch=latch,
            now_ms=now_ms,
            policy=policy,
            risk_breach=risk_breach,
            constraint_breach=constraint_breach,
            reconciliation=reconciliation,
        )
    except Exception as exc:
        _append_unique(reasons, f"SUPERVISOR_EVALUATION_ERROR:{type(exc).__name__}")
        return _decision(
            verdict=RiskSupervisorVerdict.HALT_NEW_RISK,
            reasons=reasons,
            latch=latch,
            now_ms=now_ms,
            policy=policy,
            risk_breach=risk_breach,
            constraint_breach=constraint_breach,
            reconciliation=reconciliation,
        )


def apply_risk_supervisor_action(
    decision: RiskSupervisorDecision,
    *,
    store: KillSwitchStateStore,
) -> dict[str, object]:
    """Latch severe failures; this API deliberately exposes no reset path."""

    if not isinstance(decision, RiskSupervisorDecision):
        raise TypeError("decision must be a RiskSupervisorDecision")
    if not isinstance(store, KillSwitchStateStore):
        raise TypeError("store must be a KillSwitchStateStore")
    if not decision.latch_kill_switch:
        return {"action": "none", "verdict": decision.verdict.value}
    risk = decision.risk_breach
    breach = RiskBreach(
        breached=True,
        reasons=decision.reasons,
        drawdown_pct=0.0 if risk is None else risk.drawdown_pct,
        gross_exposure=0.0 if risk is None else risk.gross_exposure,
        net_exposure=0.0 if risk is None else risk.net_exposure,
    )
    response = respond_to_breach_persisted(
        store=store,
        breach=breach,
        at=decision.evaluated_at_ms,
    )
    return {**response, "verdict": decision.verdict.value}


__all__ = [
    "RiskSupervisorDecision",
    "RiskSupervisorSnapshot",
    "RiskSupervisorVerdict",
    "apply_risk_supervisor_action",
    "evaluate_risk_supervisor",
]
