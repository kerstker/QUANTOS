"""System-derived, human-approved ceilings for Binance Futures Testnet.

The risk policy remains the authority for numerical limits.  This module turns
the exact versioned runtime policy used by the pre-trade gate into an unsigned,
review-only mandate proposal.  It cannot sign approval, load credentials,
construct an exchange adapter, or grant execution/Micro-Live authority.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import Any

from live.governance.mandate import Mandate
from quantos.contracts.hashing import stable_hash
from quantos.contracts.mft_execution import TestnetExecutionIntent
from services.risk.pre_trade.config import RiskLimits


RUNTIME_SCHEMA = "mft-testnet-runtime.v1"
PROPOSAL_ARTIFACT = "MFT_TESTNET_MANDATE_PROPOSAL"
PROPOSAL_STATUS = "HUMAN_REVIEW_REQUIRED"


class MandateProposalError(RuntimeError):
    """The runtime policy cannot support a trustworthy mandate proposal."""


def _finite(value: object, *, name: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MandateProposalError(f"{name} must be finite")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise MandateProposalError(f"{name} must be finite and positive")
    return number


def _canonical_text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise MandateProposalError(f"{name} must be non-empty canonical text")
    return value


def _canonical_text_set(value: object, *, name: str) -> frozenset[str]:
    if isinstance(value, (str, bytes)) or not isinstance(
        value, (list, tuple, set, frozenset)
    ):
        raise MandateProposalError(f"{name} must be a collection")
    materialized = tuple(_canonical_text(item, name=name) for item in value)
    if not materialized or len(set(materialized)) != len(materialized):
        raise MandateProposalError(f"{name} must contain unique canonical text")
    return frozenset(materialized)


def parse_runtime_risk_limits(raw: object) -> RiskLimits:
    """Parse the exact runtime RiskLimits shape shared with the Testnet worker."""

    if not isinstance(raw, Mapping):
        raise MandateProposalError("risk_limits must be an object")
    required = {field.name for field in fields(RiskLimits)}
    if set(raw) != required:
        raise MandateProposalError("risk_limits fields do not match RiskLimits")
    values = dict(raw)
    values["allowed_instruments"] = _canonical_text_set(
        values["allowed_instruments"], name="allowed_instruments"
    )
    values["allowed_accounts"] = _canonical_text_set(
        values["allowed_accounts"], name="allowed_accounts"
    )
    for name in (
        "default_max_position_qty",
        "fat_finger_qty",
        "max_orders_per_window",
        "rate_window_ms",
        "min_cancel_rate_samples",
    ):
        value = values[name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise MandateProposalError(f"{name} must be a positive integer")
    position_caps: list[tuple[str, int]] = []
    seen: set[str] = set()
    source_caps = values["max_position_qty"]
    if isinstance(source_caps, (str, bytes)) or not isinstance(
        source_caps, (list, tuple)
    ):
        raise MandateProposalError("max_position_qty must be a collection")
    for item in source_caps:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise MandateProposalError("max_position_qty entry is malformed")
        instrument, cap = item
        instrument = _canonical_text(instrument, name="max_position_qty instrument")
        if (
            instrument in seen
            or isinstance(cap, bool)
            or not isinstance(cap, int)
            or cap <= 0
        ):
            raise MandateProposalError("max_position_qty entry is invalid")
        seen.add(instrument)
        position_caps.append((instrument, cap))
    values["max_position_qty"] = tuple(position_caps)
    for name in (
        "max_order_notional",
        "max_price_deviation_pct",
        "max_gross_exposure",
        "max_net_exposure",
        "max_leverage",
        "max_single_name_pct",
        "daily_loss_limit",
        "max_drawdown",
        "max_turnover",
        "max_cancel_rate",
        "max_participation_rate",
        "max_instrument_vol",
    ):
        values[name] = _finite(values[name], name=name, positive=True)
    values["cash_buffer_pct"] = _finite(
        values["cash_buffer_pct"], name="cash_buffer_pct"
    )
    if values["cash_buffer_pct"] < 0:
        raise MandateProposalError("cash_buffer_pct must be non-negative")
    for name in (
        "max_price_deviation_pct",
        "max_single_name_pct",
        "max_drawdown",
        "max_cancel_rate",
        "max_participation_rate",
        "cash_buffer_pct",
    ):
        if values[name] > 1.0:
            raise MandateProposalError(f"{name} must not exceed one")
    try:
        return RiskLimits(**values)
    except (TypeError, ValueError) as exc:
        raise MandateProposalError("risk_limits are invalid") from exc


@dataclass(frozen=True, slots=True)
class TestnetMandateCeiling:
    intent_hash: str
    intent_decided_at_ms: int
    intent_expires_at_ms: int
    runtime_input_hash: str
    risk_policy_version: str
    risk_limits_hash: str
    allowed_instruments: tuple[str, ...]
    max_single_order_notional: float
    max_gross_notional: float
    max_net_notional: float

    def body(self) -> dict[str, object]:
        return {
            "intent_hash": self.intent_hash,
            "intent_decided_at_ms": self.intent_decided_at_ms,
            "intent_expires_at_ms": self.intent_expires_at_ms,
            "runtime_input_hash": self.runtime_input_hash,
            "risk_policy_version": self.risk_policy_version,
            "risk_limits_hash": self.risk_limits_hash,
            "allowed_instruments": list(self.allowed_instruments),
            "max_single_order_notional": self.max_single_order_notional,
            "max_gross_notional": self.max_gross_notional,
            "max_net_notional": self.max_net_notional,
        }

    @property
    def ceiling_hash(self) -> str:
        return stable_hash(self.body())


def derive_testnet_mandate_ceiling(
    intent: TestnetExecutionIntent,
    runtime_document: Mapping[str, Any],
) -> TestnetMandateCeiling:
    if not isinstance(intent, TestnetExecutionIntent):
        raise TypeError("intent must be a TestnetExecutionIntent")
    if not intent.testnet_submission_requested:
        raise MandateProposalError("intent testnet_submission_requested is false")
    if not isinstance(runtime_document, Mapping):
        raise MandateProposalError("runtime input must be an object")
    runtime = dict(runtime_document)
    if runtime.get("schema_version") != RUNTIME_SCHEMA:
        raise MandateProposalError("runtime schema is invalid")
    if runtime.get("market_snapshot_hash") != intent.market_snapshot_hash:
        raise MandateProposalError("runtime market snapshot does not match intent")
    runtime_watermark = runtime.get("market_watermark_ms")
    if (
        isinstance(runtime_watermark, bool)
        or not isinstance(runtime_watermark, int)
        or runtime_watermark != intent.market_watermark_ms
    ):
        raise MandateProposalError("runtime market watermark does not match intent")
    policy_version = _canonical_text(
        runtime.get("risk_policy_version"), name="risk_policy_version"
    )
    limits = parse_runtime_risk_limits(runtime.get("risk_limits"))
    targets = {target.instrument_id for target in intent.targets}
    if not targets.issubset(limits.allowed_instruments):
        raise MandateProposalError(
            "intent targets are not allowed by runtime risk policy"
        )
    gross = _finite(
        limits.max_gross_exposure,
        name="max_gross_exposure",
        positive=True,
    )
    net = min(
        _finite(limits.max_net_exposure, name="max_net_exposure", positive=True),
        gross,
    )
    single = min(
        _finite(limits.max_order_notional, name="max_order_notional", positive=True),
        gross,
    )
    return TestnetMandateCeiling(
        intent_hash=intent.intent_hash,
        intent_decided_at_ms=intent.decided_at_ms,
        intent_expires_at_ms=intent.expires_at_ms,
        runtime_input_hash=stable_hash(runtime),
        risk_policy_version=policy_version,
        risk_limits_hash=limits.limits_hash(),
        allowed_instruments=tuple(sorted(limits.allowed_instruments)),
        max_single_order_notional=single,
        max_gross_notional=gross,
        max_net_notional=net,
    )


def validate_mandate_within_ceiling(
    mandate: Mandate,
    ceiling: TestnetMandateCeiling,
) -> None:
    if not isinstance(mandate, Mandate):
        raise TypeError("mandate must be a Mandate")
    if not isinstance(ceiling, TestnetMandateCeiling):
        raise TypeError("ceiling must be a TestnetMandateCeiling")
    if mandate.venue != "testnet" or mandate.exchange != "binance":
        raise MandateProposalError("mandate is not Binance Testnet-only")
    allowed = set(mandate.allowed_instruments)
    if not allowed or not allowed.issubset(ceiling.allowed_instruments):
        raise MandateProposalError("mandate instruments exceed system policy")
    for name, value, cap in (
        (
            "max_single_order_notional",
            mandate.max_single_order_notional,
            ceiling.max_single_order_notional,
        ),
        (
            "max_gross_notional",
            mandate.max_gross_notional,
            ceiling.max_gross_notional,
        ),
        (
            "max_net_notional",
            mandate.max_net_notional,
            ceiling.max_net_notional,
        ),
    ):
        number = _finite(value, name=name, positive=True)
        if number > cap:
            raise MandateProposalError(f"{name} exceeds system policy")
    seen: set[str] = set()
    for instrument, value in mandate.per_instrument_max_notional:
        if instrument in seen or instrument not in allowed:
            raise MandateProposalError("per-instrument mandate scope is invalid")
        seen.add(instrument)
        if (
            _finite(value, name="per_instrument_max_notional", positive=True)
            > ceiling.max_gross_notional
        ):
            raise MandateProposalError("per-instrument mandate cap exceeds system policy")


def build_testnet_mandate_proposal(
    ceiling: TestnetMandateCeiling,
    *,
    generated_at_ms: int,
) -> dict[str, object]:
    if not isinstance(ceiling, TestnetMandateCeiling):
        raise TypeError("ceiling must be a TestnetMandateCeiling")
    if (
        isinstance(generated_at_ms, bool)
        or not isinstance(generated_at_ms, int)
        or generated_at_ms < 0
    ):
        raise ValueError("generated_at_ms must be a non-negative integer")
    if not (
        ceiling.intent_decided_at_ms
        <= generated_at_ms
        < ceiling.intent_expires_at_ms
    ):
        raise MandateProposalError("proposal time is outside the intent validity window")
    proposal_id = f"mft-testnet-mandate-proposal-{ceiling.ceiling_hash[:24]}"
    document: dict[str, object] = {
        "schema_version": 1,
        "artifact": PROPOSAL_ARTIFACT,
        "proposal_id": proposal_id,
        "generated_at_ms": generated_at_ms,
        "status": PROPOSAL_STATUS,
        "derivation": "SYSTEM_RISK_POLICY_CEILING_HUMAN_MAY_ONLY_TIGHTEN",
        "ceiling": ceiling.body(),
        "ceiling_hash": ceiling.ceiling_hash,
        "unsigned_mandate": {
            "mandate_id": f"UNSIGNED-{ceiling.ceiling_hash[:16]}",
            "venue": "testnet",
            "exchange": "binance",
            "allowed_instruments": list(ceiling.allowed_instruments),
            "max_single_order_notional": ceiling.max_single_order_notional,
            "max_gross_notional": ceiling.max_gross_notional,
            "max_net_notional": ceiling.max_net_notional,
            "per_instrument_max_notional": {},
            "approved_by": [],
            "issued_at": 0,
            "expires_at": 0,
        },
        "execution_authority": False,
        "micro_live_authority": False,
        "live_order_authority": False,
        "safety_flags": {
            "LIVE_TRADING_ENABLED": False,
            "REAL_BROKER_ENABLED": False,
            "ALLOW_ORDER_SUBMISSION": False,
            "ALLOW_AI_DIRECT_TRADING": False,
            "REQUIRE_HUMAN_APPROVAL": True,
        },
    }
    document["proposal_hash"] = stable_hash(document)
    return document


__all__ = [
    "MandateProposalError",
    "PROPOSAL_ARTIFACT",
    "PROPOSAL_STATUS",
    "RUNTIME_SCHEMA",
    "TestnetMandateCeiling",
    "build_testnet_mandate_proposal",
    "derive_testnet_mandate_ceiling",
    "parse_runtime_risk_limits",
    "validate_mandate_within_ceiling",
]
