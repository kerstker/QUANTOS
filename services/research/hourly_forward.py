"""Causal hourly Shadow/Paper evidence bound to validation and workflow receipts."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Collection, Mapping

from services.data.hourly_market import HourlyMarketPanel, validate_panel
from services.research.alpha_workflow import BatchReceipt
from services.research.paper_trading.forward import compute_forward_metrics
from services.research.paper_trading.forward_evidence import ForwardAdmissionRef
from services.research.paper_trading.forward_store import ForwardLedger
from services.research.ports.persistence import ForwardLedgerError
from services.research.validation_dag import (
    ValidationArtifact,
    validation_panel_digest,
)


_HORIZON_SECONDS = {"1h": 3_600, "4h": 14_400}
_SCHEMA_VERSION = 1
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_MIN_COVERAGE = 0.8
DEFAULT_EXTREME_ABS_RETURN = 0.20


@dataclass(frozen=True, slots=True)
class HourlyDecision:
    schema_version: int
    alpha_id: str
    decided_ts: int
    horizon: str
    due_ts: int
    recorded_at: int
    workflow_receipt_hash: str
    panel_digest: str
    weights: Mapping[str, float]
    signals: Mapping[str, float] | None
    entry_prices: Mapping[str, float]
    source: str
    admission_id: str
    decision_hash: str

    def __post_init__(self) -> None:
        if (
            self.schema_version != _SCHEMA_VERSION
            or type(self.alpha_id) is not str
            or _HEX64.fullmatch(self.alpha_id) is None
            or self.horizon not in _HORIZON_SECONDS
            or type(self.decided_ts) is not int
            or type(self.due_ts) is not int
            or self.due_ts != self.decided_ts + _HORIZON_SECONDS[self.horizon]
            or type(self.recorded_at) is not int
            or not self.decided_ts <= self.recorded_at < self.due_ts
            or any(
                type(value) is not str or _HEX64.fullmatch(value) is None
                for value in (
                    self.workflow_receipt_hash,
                    self.panel_digest,
                    self.decision_hash,
                )
            )
            or not self.weights
            or not self.entry_prices
            or any(
                isinstance(value, bool)
                or type(value) not in (int, float)
                or not math.isfinite(value)
                for mapping in (self.weights, self.entry_prices, self.signals or {})
                for value in mapping.values()
            )
        ):
            raise ValueError("invalid HourlyDecision")
        object.__setattr__(self, "weights", MappingProxyType(dict(self.weights)))
        if self.signals is not None:
            object.__setattr__(self, "signals", MappingProxyType(dict(self.signals)))
        object.__setattr__(self, "entry_prices", MappingProxyType(dict(self.entry_prices)))


@dataclass(frozen=True, slots=True)
class HourlyRealization:
    schema_version: int
    alpha_id: str
    decided_ts: int
    horizon: str
    as_of_ts: int
    workflow_receipt_hash: str
    panel_digest: str
    realized_return: float
    realized_ic: float | None
    source: str
    inputs: Mapping[str, object]
    admission_id: str
    realization_hash: str

    def __post_init__(self) -> None:
        if (
            self.schema_version != _SCHEMA_VERSION
            or type(self.alpha_id) is not str
            or _HEX64.fullmatch(self.alpha_id) is None
            or self.horizon not in _HORIZON_SECONDS
            or type(self.decided_ts) is not int
            or type(self.as_of_ts) is not int
            or self.as_of_ts < self.decided_ts + _HORIZON_SECONDS[self.horizon]
            or any(
                type(value) is not str or _HEX64.fullmatch(value) is None
                for value in (
                    self.workflow_receipt_hash,
                    self.panel_digest,
                    self.realization_hash,
                )
            )
            or not math.isfinite(self.realized_return)
            or (self.realized_ic is not None and not math.isfinite(self.realized_ic))
            or not self.inputs
        ):
            raise ValueError("invalid HourlyRealization")
        object.__setattr__(
            self,
            "inputs",
            MappingProxyType(
                {
                    name: MappingProxyType(dict(values))
                    for name, values in self.inputs.items()
                }
            ),
        )


def _decision_from_row(row: Mapping[str, object]) -> HourlyDecision:
    return HourlyDecision(
        schema_version=int(row["schema_version"]),
        alpha_id=str(row["alpha_id"]),
        decided_ts=int(row["decided_ts"]),
        horizon=str(row["horizon"]),
        due_ts=int(row["due_ts"]),
        recorded_at=int(row["recorded_at"]),
        workflow_receipt_hash=str(row["workflow_receipt_hash"]),
        panel_digest=str(row["panel_digest"]),
        weights=json.loads(str(row["weights_json"])),
        signals=(
            None
            if row["signals_json"] is None
            else json.loads(str(row["signals_json"]))
        ),
        entry_prices=json.loads(str(row["entry_prices_json"])),
        source=str(row["source"]),
        admission_id=str(row["admission_id"]),
        decision_hash=str(row["row_hash"]),
    )


def _realization_from_row(row: Mapping[str, object]) -> HourlyRealization:
    return HourlyRealization(
        schema_version=int(row["schema_version"]),
        alpha_id=str(row["alpha_id"]),
        decided_ts=int(row["decided_ts"]),
        horizon=str(row["horizon"]),
        as_of_ts=int(row["as_of_ts"]),
        workflow_receipt_hash=str(row["workflow_receipt_hash"]),
        panel_digest=str(row["panel_digest"]),
        realized_return=float(row["realized_return"]),
        realized_ic=(None if row["realized_ic"] is None else float(row["realized_ic"])),
        source=str(row["source"]),
        inputs=json.loads(str(row["inputs_json"])),
        admission_id=str(row["admission_id"]),
        realization_hash=str(row["row_hash"]),
    )


def _verify_validation_binding(
    artifact: ValidationArtifact,
    receipt: BatchReceipt,
    validation_panel: Any,
    hourly_panel: HourlyMarketPanel,
    decided_ts: int,
) -> str:
    if not isinstance(artifact, ValidationArtifact) or not isinstance(receipt, BatchReceipt):
        raise ForwardLedgerError("typed validation artifact and workflow receipt required")
    if artifact.verdict != "PASS" or receipt.state != "VALIDATED":
        raise ForwardLedgerError("PASS validation artifact and VALIDATED workflow receipt required")
    if hourly_panel.as_of != decided_ts or any(
        row.event_time > decided_ts or row.knowledge_time > decided_ts
        for rows in (hourly_panel.bars, hourly_panel.funding, hourly_panel.open_interest)
        for row in rows
    ):
        raise ForwardLedgerError("hourly panel contains knowledge after decision time")
    panel_digest = validation_panel_digest(validation_panel)
    if (
        artifact.event_at != decided_ts
        or receipt.event_at != decided_ts
        or artifact.panel_digest != panel_digest
        or receipt.panel_digest != panel_digest
    ):
        raise ForwardLedgerError("hourly panel, artifact, and workflow receipt binding mismatch")
    candidates = [
        item
        for item in receipt.candidate_receipts
        if item.candidate_id == artifact.alpha_id
    ]
    if (
        len(candidates) != 1
        or candidates[0].state != "VALIDATED"
        or candidates[0].trial_id != artifact.trial_id
        or candidates[0].artifact_commitment != artifact.artifact_commitment
    ):
        raise ForwardLedgerError("validation artifact is not committed by workflow receipt")
    return panel_digest


def _hourly_panel(value: Any) -> HourlyMarketPanel:
    if isinstance(value, HourlyMarketPanel):
        return value
    if isinstance(value, Mapping) and isinstance(value.get("hourly_market"), HourlyMarketPanel):
        return value["hourly_market"]
    raise ForwardLedgerError("hourly market panel is required")


def record_hourly_decision(
    ledger: ForwardLedger,
    artifact: ValidationArtifact,
    panel: Any,
    workflow_receipt: BatchReceipt,
    *,
    horizon: str,
    decided_ts: int,
    recorded_at: int,
    weights: Mapping[str, float],
    admission_ref: ForwardAdmissionRef,
    signals: Mapping[str, float] | None = None,
    source: str = "HOURLY_FORWARD_L2_ATTESTED_DECISION",
) -> HourlyDecision:
    if not isinstance(ledger, ForwardLedger):
        raise TypeError("ledger must be ForwardLedger")
    hourly_panel = _hourly_panel(panel)
    validate_panel(hourly_panel)
    if horizon not in _HORIZON_SECONDS or type(decided_ts) is not int:
        raise ForwardLedgerError("unsupported hourly horizon")
    panel_digest = _verify_validation_binding(
        artifact, workflow_receipt, panel, hourly_panel, decided_ts
    )
    entry_prices = {
        bar.instrument: float(bar.close)
        for bar in hourly_panel.bars
        if bar.event_time == decided_ts and bar.knowledge_time <= decided_ts
    }
    if not set(weights).issubset(entry_prices):
        raise ForwardLedgerError("weighted instruments need causal decision-time prices")
    if signals is not None and not set(signals).issubset(entry_prices):
        raise ForwardLedgerError("signals need causal decision-time inputs")
    row = ledger.record_hourly_decision(
        artifact.alpha_id,
        decided_ts,
        horizon,
        recorded_at=recorded_at,
        workflow_receipt_hash=workflow_receipt.receipt_digest,
        panel_digest=panel_digest,
        weights=weights,
        signals=signals,
        entry_prices=entry_prices,
        admission_ref=admission_ref,
        source=source,
    )
    return _decision_from_row(row)


def realize_due_hourly(
    ledger: ForwardLedger,
    alpha_id: str,
    *,
    panel: HourlyMarketPanel,
    admission_ref: ForwardAdmissionRef,
    committed_at: int,
    blocked_instruments: Collection[str] = (),
    min_coverage: float = DEFAULT_MIN_COVERAGE,
    extreme_abs_return: float = DEFAULT_EXTREME_ABS_RETURN,
    source: str = "HOURLY_FORWARD_L2_ATTESTED_REALIZATION",
) -> dict:
    if not isinstance(ledger, ForwardLedger):
        raise TypeError("ledger must be ForwardLedger")
    validate_panel(panel)
    if (
        type(committed_at) is not int
        or committed_at < panel.as_of
        or type(min_coverage) not in (int, float)
        or not math.isfinite(min_coverage)
        or not 0 <= min_coverage <= 1
        or type(extreme_abs_return) not in (int, float)
        or not math.isfinite(extreme_abs_return)
        or extreme_abs_return <= 0
    ):
        raise ForwardLedgerError("invalid hourly realization policy or time")
    blocked = {str(item) for item in blocked_instruments}
    alerts: list[str] = []
    realizations: list[HourlyRealization] = []
    n_quality_blocked = 0
    n_coverage_blocked = 0
    for row in ledger.hourly_pending(alpha_id):
        decision = _decision_from_row(row)
        outcome_times = sorted(
            {
                bar.event_time
                for bar in panel.bars
                if bar.event_time >= decision.due_ts and bar.knowledge_time <= panel.as_of
            }
        )
        if not outcome_times:
            continue
        as_of_ts = outcome_times[0]
        if set(decision.weights) & blocked:
            n_quality_blocked += 1
            alerts.append(
                f"{decision.alpha_id}@{decision.decided_ts}/{decision.horizon}: quality BLOCK"
            )
            continue
        exit_prices = {
            bar.instrument: float(bar.close)
            for bar in panel.bars
            if bar.event_time == as_of_ts and bar.knowledge_time <= panel.as_of
        }
        names = [
            name
            for name in decision.weights
            if name in decision.entry_prices and name in exit_prices
        ]
        coverage = len(names) / len(decision.weights)
        if coverage < min_coverage:
            n_coverage_blocked += 1
            alerts.append(
                f"{decision.alpha_id}@{decision.decided_ts}/{decision.horizon}: "
                f"coverage {coverage:.2f} < {min_coverage}"
            )
            continue
        funding = {
            name: sum(
                float(item.rate)
                for item in panel.funding
                if item.instrument == name
                and decision.decided_ts < item.event_time <= as_of_ts
                and item.knowledge_time <= panel.as_of
            )
            for name in names
        }
        realized_return, realized_ic, inputs = compute_forward_metrics(
            weights=decision.weights,
            entry_prices=decision.entry_prices,
            exit_prices=exit_prices,
            signals=decision.signals,
            funding_by_instrument=funding,
        )
        if abs(realized_return) > extreme_abs_return:
            alerts.append(
                f"{decision.alpha_id}@{as_of_ts}/{decision.horizon}: extreme return recorded"
            )
        realized_row = ledger.commit_hourly_realization(
            decision.alpha_id,
            decision.decided_ts,
            decision.horizon,
            as_of_ts=as_of_ts,
            realized_return=realized_return,
            realized_ic=realized_ic,
            inputs=inputs,
            admission_ref=admission_ref,
            committed_at=committed_at,
            source=source,
        )
        realizations.append(_realization_from_row(realized_row))
    return {
        "n_realized": len(realizations),
        "n_quality_blocked": n_quality_blocked,
        "n_coverage_blocked": n_coverage_blocked,
        "alerts": alerts,
        "realizations": tuple(realizations),
    }


__all__ = [
    "HourlyDecision",
    "HourlyRealization",
    "record_hourly_decision",
    "realize_due_hourly",
]
