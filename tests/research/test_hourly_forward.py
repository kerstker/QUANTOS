from __future__ import annotations

import sqlite3

import pytest

from quantos.contracts.hashing import stable_hash
from services.data.hourly_market import HourlyBar, HourlyMarketPanel
from services.research.alpha_family import family_id
from services.research.alpha_garden import GenerationBudget, GeneratorEntry, GeneratorRegistry
from services.research.alpha_identity import AlphaDefinition
from services.research.alpha_workflow import run_candidate_batch
from services.research.alpha_workflow_store import WorkflowStore
from services.research.hourly_forward import (
    HourlyDecision,
    HourlyRealization,
    realize_due_hourly,
    record_hourly_decision,
)
from services.research.paper_trading.forward_evidence import ForwardAdmissionRef
from services.research.paper_trading.forward_store import ForwardLedger
from services.research.ports.persistence import ForwardLedgerError
from services.research.trial_ledger import TrialLedger
from services.research.validation_dag import (
    StageEvidence,
    ValidationArtifact,
    ValidationPolicy,
)


T0 = 1_800_000_000
HOUR = 3_600


class Stage:
    version = "1"

    def __init__(self, name: str, outcome: str = "PASS"):
        self.name = name
        self.outcome = outcome
        self.implementation_id = f"tests.hourly_forward.{name}.v1"

    def evaluate(self, context):
        return StageEvidence(
            stage=self.name,
            version=self.version,
            implementation_id=self.implementation_id,
            outcome=self.outcome,
            evidence={"alpha_id": context.candidate.alpha_id},
            reason=None if self.outcome == "PASS" else "test block",
        )


def _panel(as_of: int, prices: dict[str, float], *, knowledge_time: int | None = None):
    known = as_of if knowledge_time is None else knowledge_time
    bars = tuple(
        HourlyBar(name, as_of, known, price, price, price, price, 1_000.0)
        for name, price in sorted(prices.items())
    )
    return HourlyMarketPanel(
        "hourly-market-v1",
        max(as_of, known),
        bars,
        (),
        (),
        stable_hash({"as_of": as_of, "prices": prices, "known": known}),
    )


def _admission(scope=("BTC", "ETH")):
    return ForwardAdmissionRef(
        admission_id="hourly-admission",
        evidence_sha256="a" * 64,
        decision_hash="b" * 64,
        recorded_at_ms=T0,
        price_basis="PERP_MARK_PRICE",
        instrument_scope=tuple(sorted(scope)),
    )


def _validated(tmp_path, panel, *, verdict="PASS"):
    stages = (
        Stage("cheap", verdict),
        Stage("oos"),
        Stage("realism"),
    )
    policy = ValidationPolicy(stages)
    definition = AlphaDefinition(
        definition=("ts_mean", ("field", "close"), 2),
        feature_contract={"fields": ["close"]},
        data_schema={"name": "hourly", "version": 1},
        universe_policy={"name": "dynamic"},
        horizon="1h",
        cost_model={"bps": 5},
        validation_policy={"digest": policy.digest},
        model="ridge",
        model_version="1",
    )

    def generate(context, rng):
        del context, rng
        return (definition,)

    registry = GeneratorRegistry(
        (GeneratorEntry("hourly", "1", family_id(definition), generate),)
    )
    with WorkflowStore(str(tmp_path / f"workflow-{verdict}.db")) as workflow_store, TrialLedger(
        str(tmp_path / f"trials-{verdict}.db")
    ) as trial_ledger:
        receipt = run_candidate_batch(
            batch_id=stable_hash({"hourly": verdict}),
            registry=registry,
            budget=GenerationBudget(1, 1, 3),
            context={},
            panel=panel,
            policy=policy,
            ledger=trial_ledger,
            store=workflow_store,
            event_at=T0,
            worker_id="hourly-test",
            lease_now=T0,
            lease_until=T0 + 1,
            correlation_threshold=0.99,
        )
        item = receipt.candidate_receipts[0]
        artifact = ValidationArtifact(**trial_ledger.load_validation_artifact(item.trial_id))
    return artifact, receipt


def _record(
    ledger,
    artifact,
    receipt,
    panel,
    *,
    horizon="1h",
    recorded_at=T0,
    weights=None,
):
    return record_hourly_decision(
        ledger,
        artifact,
        panel,
        receipt,
        horizon=horizon,
        decided_ts=T0,
        recorded_at=recorded_at,
        weights=weights or {"BTC": 0.5, "ETH": -0.5},
        signals={"BTC": 1.0, "ETH": -1.0},
        admission_ref=_admission(),
    )


def test_one_and_four_hour_horizons_realize_only_after_each_due_time(tmp_path):
    decision_panel = _panel(T0, {"BTC": 100.0, "ETH": 100.0})
    artifact, receipt = _validated(tmp_path, decision_panel)
    ledger = ForwardLedger(str(tmp_path / "forward.db"), create=True)
    one = _record(ledger, artifact, receipt, decision_panel, horizon="1h")
    four = _record(ledger, artifact, receipt, decision_panel, horizon="4h")
    assert isinstance(one, HourlyDecision)
    assert four.horizon == "4h"

    out_1h = realize_due_hourly(
        ledger,
        artifact.alpha_id,
        panel=_panel(T0 + HOUR, {"BTC": 102.0, "ETH": 98.0}),
        admission_ref=_admission(),
        committed_at=T0 + HOUR,
    )
    assert out_1h["n_realized"] == 1
    assert isinstance(out_1h["realizations"][0], HourlyRealization)
    assert out_1h["realizations"][0].horizon == "1h"
    assert len(ledger.hourly_pending(artifact.alpha_id)) == 1

    out_4h = realize_due_hourly(
        ledger,
        artifact.alpha_id,
        panel=_panel(T0 + 4 * HOUR, {"BTC": 104.0, "ETH": 96.0}),
        admission_ref=_admission(),
        committed_at=T0 + 4 * HOUR,
    )
    assert out_4h["n_realized"] == 1
    assert out_4h["realizations"][0].horizon == "4h"
    assert ledger.hourly_pending(artifact.alpha_id) == []


def test_late_decision_and_realization_without_prior_decision_are_rejected(tmp_path):
    panel = _panel(T0, {"BTC": 100.0, "ETH": 100.0})
    artifact, receipt = _validated(tmp_path, panel)
    ledger = ForwardLedger(str(tmp_path / "forward.db"), create=True)

    with pytest.raises(ForwardLedgerError, match="before horizon expiry"):
        _record(
            ledger,
            artifact,
            receipt,
            panel,
            recorded_at=T0 + HOUR,
        )
    with pytest.raises(ForwardLedgerError, match="decision not found"):
        ledger.commit_hourly_realization(
            artifact.alpha_id,
            T0,
            "1h",
            as_of_ts=T0 + HOUR,
            realized_return=0.01,
            realized_ic=None,
            inputs={"BTC": {"weight": 1.0, "price_return": 0.01, "funding": 0.0}},
            admission_ref=_admission(),
            committed_at=T0 + HOUR,
        )


def test_knowledge_after_decision_time_cannot_enter_signal_decision(tmp_path):
    causal = _panel(T0, {"BTC": 100.0, "ETH": 100.0})
    artifact, receipt = _validated(tmp_path, causal)
    future_known = _panel(
        T0,
        {"BTC": 100.0, "ETH": 100.0},
        knowledge_time=T0 + 1,
    )
    ledger = ForwardLedger(str(tmp_path / "forward.db"), create=True)

    with pytest.raises(ForwardLedgerError, match="decision time"):
        _record(ledger, artifact, receipt, future_known)


def test_composite_validation_panel_retains_hourly_market_binding(tmp_path):
    hourly = _panel(T0, {"BTC": 100.0, "ETH": 100.0})
    envelope = {"hourly_market": hourly, "validation_evidence": {"version": 1}}
    artifact, receipt = _validated(tmp_path, envelope)
    ledger = ForwardLedger(str(tmp_path / "forward.db"), create=True)

    decision = _record(ledger, artifact, receipt, envelope)

    assert decision.panel_digest == artifact.panel_digest == receipt.panel_digest


def test_insufficient_coverage_stays_pending(tmp_path):
    panel = _panel(T0, {"BTC": 100.0, "ETH": 100.0})
    artifact, receipt = _validated(tmp_path, panel)
    ledger = ForwardLedger(str(tmp_path / "forward.db"), create=True)
    _record(ledger, artifact, receipt, panel)

    result = realize_due_hourly(
        ledger,
        artifact.alpha_id,
        panel=_panel(T0 + HOUR, {"BTC": 101.0}),
        admission_ref=_admission(),
        committed_at=T0 + HOUR,
    )

    assert result["n_realized"] == 0
    assert result["n_coverage_blocked"] == 1
    assert len(ledger.hourly_pending(artifact.alpha_id)) == 1


def test_same_hourly_decision_is_idempotent_but_cannot_be_rewritten(tmp_path):
    panel = _panel(T0, {"BTC": 100.0, "ETH": 100.0})
    artifact, receipt = _validated(tmp_path, panel)
    ledger = ForwardLedger(str(tmp_path / "forward.db"), create=True)
    first = _record(ledger, artifact, receipt, panel)
    second = _record(ledger, artifact, receipt, panel)
    assert first == second
    with pytest.raises(ForwardLedgerError, match="different content"):
        _record(
            ledger,
            artifact,
            receipt,
            panel,
            weights={"BTC": 1.0, "ETH": -1.0},
        )


def test_block_validation_artifact_cannot_start_hourly_forward(tmp_path):
    panel = _panel(T0, {"BTC": 100.0, "ETH": 100.0})
    artifact, receipt = _validated(tmp_path, panel, verdict="BLOCK")
    ledger = ForwardLedger(str(tmp_path / "forward.db"), create=True)
    with pytest.raises(ForwardLedgerError, match="PASS"):
        _record(ledger, artifact, receipt, panel)


def test_hourly_tables_are_append_only(tmp_path):
    panel = _panel(T0, {"BTC": 100.0, "ETH": 100.0})
    artifact, receipt = _validated(tmp_path, panel)
    path = tmp_path / "forward.db"
    ledger = ForwardLedger(str(path), create=True)
    _record(ledger, artifact, receipt, panel)
    realize_due_hourly(
        ledger,
        artifact.alpha_id,
        panel=_panel(T0 + HOUR, {"BTC": 101.0, "ETH": 99.0}),
        admission_ref=_admission(),
        committed_at=T0 + HOUR,
    )

    connection = sqlite3.connect(path)
    for table in ("hourly_forward_decisions", "hourly_forward_realizations"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(f"UPDATE {table} SET alpha_id='tampered'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(f"DELETE FROM {table}")
    connection.close()


def test_legacy_weekly_rows_remain_readable_without_fake_hourly_backfill(tmp_path):
    path = tmp_path / "forward.db"
    ledger = ForwardLedger(str(path), create=True)
    ledger.record_decision(
        "legacy",
        T0,
        recorded_at=T0,
        weights={"BTC": 1.0},
        admission_ref=_admission(("BTC",)),
    )
    ledger._conn.executescript(
        """
        DROP TRIGGER hourly_forward_decisions_no_update;
        DROP TRIGGER hourly_forward_decisions_no_delete;
        DROP TRIGGER hourly_forward_realizations_no_update;
        DROP TRIGGER hourly_forward_realizations_no_delete;
        DROP TABLE hourly_forward_realizations;
        DROP TABLE hourly_forward_decisions;
        """
    )
    ledger._conn.commit()

    reopened = ForwardLedger(str(path), create=False)
    assert reopened.decisions("legacy")[0]["source"] == "FORWARD_L2_ATTESTED_DECISION"
    assert "horizon" not in reopened.decisions("legacy")[0]
    with pytest.raises(ForwardLedgerError, match="hourly schema unavailable"):
        reopened.hourly_decisions("legacy")
