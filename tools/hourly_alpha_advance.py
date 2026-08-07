"""Local-only L2 scheduler for the hourly alpha-to-forward-paper workflow.

This worker reads an already materialized and attested panel.  It performs no
network refresh, has no broker dependency, and cannot submit or authorize an
order.  All mutable progress is protected by durable SQLite leases/checkpoints.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable, Mapping

from quantos.contracts.hashing import stable_hash
from services.data.admission_store import DataQualityAdmissionStore
from services.data.hourly_market import (
    HourlyBar,
    HourlyFunding,
    HourlyMarketPanel,
    HourlyOpenInterest,
    validate_panel,
)
from services.orchestration.leader_lease import SqliteLeaderLeaseStore
from services.research.alpha_family import family_id
from services.research.alpha_garden import (
    GenerationBudget,
    GeneratorEntry,
    GeneratorRegistry,
)
from services.research.alpha_identity import AlphaDefinition
from services.research.alpha_workflow import BatchReceipt, run_candidate_batch
from services.research.alpha_workflow_store import WorkflowStore
from services.research.hourly_forward import record_hourly_decision
from services.research.paper_trading.forward_evidence import (
    ForwardAdmissionRef,
    ForwardEvidenceError,
    load_forward_admission_ref,
)
from services.research.paper_trading.forward_store import ForwardLedger
from services.research.trial_ledger import TrialLedger
from services.research.validation_dag import (
    ValidationArtifact,
    ValidationPolicy,
    validation_panel_digest,
)
from tools.hourly_market_materialize import load_materialized_partition


DEFAULT_GENERATION_BUDGET = 10_000
MAX_GENERATION_BUDGET = 100_000
_LEASE_SECONDS = 600
_SCHEMA_VERSION = "quantos.hourly-alpha-advance.v1"


@dataclass(frozen=True, slots=True)
class ForwardDecisionSpec:
    horizon: str
    weights: Mapping[str, float]
    signals: Mapping[str, float] | None = None


@dataclass(frozen=True, slots=True)
class AdvanceResult:
    status: str
    workflow_state: str
    receipt: BatchReceipt
    recorded_decisions: int
    skipped_decisions: int
    decision_errors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "status": self.status,
            "workflow_state": self.workflow_state,
            "batch_id": self.receipt.batch_id,
            "receipt_digest": self.receipt.receipt_digest,
            "generated_count": self.receipt.generated_count,
            "representative_count": self.receipt.representative_count,
            "validated_count": self.receipt.validated_count,
            "blocked_count": self.receipt.blocked_count,
            "recorded_decisions": self.recorded_decisions,
            "skipped_decisions": self.skipped_decisions,
            "decision_errors": list(self.decision_errors),
            "safety": {
                "readiness": "L2_DRY_RUN_ONLY",
                "live_trading_enabled": False,
                "real_broker_enabled": False,
                "allow_order_submission": False,
                "allow_ai_direct_trading": False,
                "require_human_approval": True,
            },
        }


DecisionBuilder = Callable[[str, HourlyMarketPanel], ForwardDecisionSpec | None]


def _require_budget(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_GENERATION_BUDGET:
        raise ValueError(f"budget must be an integer from 1 through {MAX_GENERATION_BUDGET}")
    return value


def _batch_id(
    *,
    panel: Any,
    registry: GeneratorRegistry,
    policy: ValidationPolicy,
    budget: int,
    as_of: int,
) -> str:
    return stable_hash(
        {
            "schema": _SCHEMA_VERSION,
            "as_of": as_of,
            "panel_digest": validation_panel_digest(panel),
            "policy_digest": policy.digest,
            "budget": budget,
            "registry": [
                {
                    "name": entry.name,
                    "version": entry.version,
                    "family": entry.family,
                    "max_share": entry.max_share,
                }
                for entry in registry.entries
            ],
        }
    )


def advance_hourly_alpha(
    *,
    panel: HourlyMarketPanel,
    validation_panel: Any,
    registry: GeneratorRegistry,
    policy: ValidationPolicy,
    workflow_store: WorkflowStore,
    trial_ledger: TrialLedger,
    forward_ledger: ForwardLedger,
    admission_ref: ForwardAdmissionRef | None,
    decision_builder: DecisionBuilder | None,
    as_of: int,
    budget: int = DEFAULT_GENERATION_BUDGET,
    worker_id: str,
    lease_now: int,
    lease_until: int,
    correlation_threshold: float = 0.999,
    production: bool = True,
) -> AdvanceResult:
    """Advance one deterministic batch and, when fully admitted, record shadow decisions."""
    validate_panel(panel)
    budget = _require_budget(budget)
    if type(as_of) is not int or panel.as_of != as_of:
        raise ValueError("as_of must exactly match the attested hourly panel")
    if isinstance(validation_panel, Mapping) and validation_panel.get("hourly_market") != panel:
        raise ValueError("validation panel hourly_market binding mismatch")

    receipt = run_candidate_batch(
        batch_id=_batch_id(
            panel=validation_panel,
            registry=registry,
            policy=policy,
            budget=budget,
            as_of=as_of,
        ),
        registry=registry,
        budget=GenerationBudget(budget, budget, as_of),
        context={"hourly_market": panel, "as_of": as_of},
        panel=validation_panel,
        policy=policy,
        ledger=trial_ledger,
        store=workflow_store,
        event_at=as_of,
        worker_id=worker_id,
        lease_now=lease_now,
        lease_until=lease_until,
        correlation_threshold=correlation_threshold,
        production=production,
    )

    recorded = 0
    skipped = 0
    errors: list[str] = []
    for item in receipt.candidate_receipts:
        if item.state != "VALIDATED":
            continue
        if admission_ref is None or decision_builder is None:
            skipped += 1
            errors.append(f"{item.candidate_id}: forward admission or decision builder unavailable")
            continue
        try:
            spec = decision_builder(item.candidate_id, panel)
            if spec is None:
                skipped += 1
                errors.append(f"{item.candidate_id}: insufficient causal decision inputs")
                continue
            artifact = ValidationArtifact(
                **trial_ledger.load_validation_artifact(
                    item.trial_id,
                    panel_digest=receipt.panel_digest,
                    policy_digest=receipt.policy_digest,
                )
            )
            record_hourly_decision(
                forward_ledger,
                artifact,
                validation_panel,
                receipt,
                horizon=spec.horizon,
                decided_ts=as_of,
                recorded_at=as_of,
                weights=spec.weights,
                signals=spec.signals,
                admission_ref=admission_ref,
            )
            recorded += 1
        except Exception as exc:
            skipped += 1
            errors.append(f"{item.candidate_id}: {type(exc).__name__}")

    snapshot = workflow_store.snapshot(receipt.batch_id)
    if receipt.validated_count and recorded == receipt.validated_count and not errors:
        if snapshot.state == "VALIDATED":
            snapshot = workflow_store.transition(receipt.batch_id, "SHADOW", at=as_of)
    status = "COMPLETED" if receipt.validated_count == 0 or not errors else "BLOCKED"
    if snapshot.state == "SHADOW" and not errors:
        status = "COMPLETED"
    return AdvanceResult(
        status=status,
        workflow_state=snapshot.state,
        receipt=receipt,
        recorded_decisions=recorded,
        skipped_decisions=skipped,
        decision_errors=tuple(errors),
    )


def _partition(panel_root: Path, as_of: int) -> Path:
    root = Path(panel_root)
    if (root / "manifest.json").is_file():
        return root
    date = datetime.fromtimestamp(as_of, tz=timezone.utc).strftime("%Y-%m-%d")
    direct = root / date / "interval-3600"
    nested = root / "hourly_market" / date / "interval-3600"
    return direct if direct.is_dir() else nested


def load_attested_panel(panel_root: Path, *, as_of: int) -> HourlyMarketPanel:
    partition = _partition(Path(panel_root), as_of)
    rows = load_materialized_partition(partition)
    manifest = json.loads((partition / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("as_of") != as_of or manifest.get("interval_seconds") != 3_600:
        raise ValueError("hourly materialization does not match requested as_of")
    bars = tuple(
        HourlyBar(*(row[key] for key in ("instrument", "event_time", "knowledge_time", "open", "high", "low", "close", "quote_volume")))
        for row in rows
        if row["kind"] == "bar"
    )
    funding = tuple(
        HourlyFunding(*(row[key] for key in ("instrument", "event_time", "knowledge_time", "rate")))
        for row in rows
        if row["kind"] == "funding"
    )
    open_interest = tuple(
        HourlyOpenInterest(*(row[key] for key in ("instrument", "event_time", "knowledge_time", "value")))
        for row in rows
        if row["kind"] == "open_interest"
    )
    panel = HourlyMarketPanel(
        "hourly-market-v1",
        as_of,
        bars,
        funding,
        open_interest,
        manifest["manifest_digest"],
    )
    validate_panel(panel)
    return panel


def _default_registry(policy: ValidationPolicy) -> GeneratorRegistry:
    definition = AlphaDefinition(
        definition=("ts_mean", ("field", "close"), 4),
        feature_contract={"fields": ["close"], "causal": True},
        data_schema={"name": "hourly-market-v1", "interval_seconds": 3_600},
        universe_policy={"source": "attested-hourly-panel"},
        horizon="1h",
        cost_model={"fee_bps": 5.0},
        validation_policy={"digest": policy.digest},
        model="ridge",
        model_version="1",
    )

    def generate(context, rng):
        del context, rng
        return (definition,)

    return GeneratorRegistry(
        (GeneratorEntry("hourly-causal-baseline", "1", family_id(definition), generate),)
    )


def _momentum_decision(alpha_id: str, panel: HourlyMarketPanel) -> ForwardDecisionSpec | None:
    del alpha_id
    histories: dict[str, list[HourlyBar]] = {}
    for bar in panel.bars:
        histories.setdefault(bar.instrument, []).append(bar)
    signals = {
        instrument: rows[-1].close / rows[-2].close - 1.0
        for instrument, rows in histories.items()
        if len(rows) >= 2 and panel.as_of - rows[-1].event_time <= 3_600
    }
    if len(signals) < 2:
        return None
    mean = sum(signals.values()) / len(signals)
    centered = {name: value - mean for name, value in signals.items()}
    gross = sum(abs(value) for value in centered.values())
    if gross == 0.0:
        return None
    weights = {name: value / gross for name, value in centered.items()}
    return ForwardDecisionSpec("1h", weights, signals)


def _latest_admission(path: Path, instruments: set[str]) -> ForwardAdmissionRef | None:
    if not path.is_file() or not instruments:
        return None
    store = DataQualityAdmissionStore(str(path), create=False)
    try:
        decision = store.latest_decision()
        if decision is None:
            return None
        try:
            return load_forward_admission_ref(
                store,
                admission_id=decision.admission_id,
                evidence_sha256=decision.evidence_sha256,
                decision_hash=decision.decision_hash,
                instruments=instruments,
            )
        except ForwardEvidenceError:
            return None
    finally:
        store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Advance the local L2 hourly alpha workflow")
    parser.add_argument("--panel", required=True)
    parser.add_argument("--as-of", required=True, type=int)
    parser.add_argument("--budget", type=int, default=DEFAULT_GENERATION_BUDGET)
    parser.add_argument("--state-root", default="data/cache")
    parser.add_argument("--worker-id", default="hourly-alpha-scheduler")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _require_budget(args.budget)
    state_root = Path(args.state_root)
    state_root.mkdir(parents=True, exist_ok=True)
    panel = load_attested_panel(Path(args.panel), as_of=args.as_of)
    policy = ValidationPolicy()
    registry = _default_registry(policy)
    validation_panel = {"hourly_market": panel}
    instruments = {bar.instrument for bar in panel.bars if bar.event_time == panel.as_of}
    admission = _latest_admission(state_root / "data_quality_admission.db", instruments)
    lease_store = SqliteLeaderLeaseStore(str(state_root / "hourly_alpha_lease.db"), create=True)
    lease_now_ms = time.time_ns() // 1_000_000
    lease = lease_store.acquire(
        "hourly-alpha-advance",
        owner_id=args.worker_id,
        now_ms=lease_now_ms,
        lease_ms=_LEASE_SECONDS * 1_000,
    )
    try:
        workflow_now = time.time_ns() // 1_000_000_000
        with WorkflowStore(str(state_root / "hourly_alpha_workflow.db")) as workflow, TrialLedger(
            str(state_root / "hourly_alpha_trials.db")
        ) as trials, ForwardLedger(
            str(state_root / "hourly_alpha_forward.db"), create=True
        ) as forward:
            result = advance_hourly_alpha(
                panel=panel,
                validation_panel=validation_panel,
                registry=registry,
                policy=policy,
                workflow_store=workflow,
                trial_ledger=trials,
                forward_ledger=forward,
                admission_ref=admission,
                decision_builder=_momentum_decision,
                as_of=args.as_of,
                budget=args.budget,
                worker_id=args.worker_id,
                lease_now=workflow_now,
                lease_until=workflow_now + _LEASE_SECONDS,
                production=True,
            )
    finally:
        lease_store.release(lease, now_ms=lease_now_ms)
        lease_store.close()
    print(json.dumps(result.as_dict(), sort_keys=True, separators=(",", ":")))
    return 0 if result.status == "COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_GENERATION_BUDGET",
    "MAX_GENERATION_BUDGET",
    "ForwardDecisionSpec",
    "AdvanceResult",
    "advance_hourly_alpha",
    "load_attested_panel",
    "build_parser",
    "main",
]
