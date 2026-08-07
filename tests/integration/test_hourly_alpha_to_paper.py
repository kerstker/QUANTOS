from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

import pytest

from quantos.contracts.hashing import stable_hash
from services.data.hourly_market import HourlyBar, HourlyMarketPanel
from services.data.hourly_universe import UniversePolicy
from services.research.alpha_family import AlphaCandidate, deduplicate_candidates, family_id
from services.research.alpha_garden import (
    GenerationBudget,
    GeneratorEntry,
    GeneratorRegistry,
    generate_batch,
)
from services.research.alpha_identity import AlphaDefinition
from services.research.alpha_workflow_store import WorkflowStore
from services.research.paper_trading.forward_evidence import ForwardAdmissionRef
from services.research.paper_trading.forward_store import ForwardLedger
from services.research.trial_ledger import TrialLedger
from services.research.validation_dag import StageEvidence, ValidationPolicy
from tools.hourly_alpha_advance import (
    ForwardDecisionSpec,
    advance_hourly_alpha,
    load_attested_panel,
)
from tools.hourly_market_materialize import INPUT_SCHEMA, materialize_hourly_market


AS_OF = 14_100
HOUR = 3_600


class Stage:
    version = "1"

    def __init__(self, name: str, calls: list[tuple[str, str]], crash: set[str] | None = None):
        self.name = name
        self.calls = calls
        self.crash = crash if crash is not None else set()
        self.implementation_id = f"tests.integration.hourly_alpha.{name}.v1"

    def evaluate(self, context):
        alpha_id = context.candidate.alpha_id
        self.calls.append((self.name, alpha_id))
        if self.name in self.crash:
            self.crash.remove(self.name)
            raise KeyboardInterrupt(f"injected {self.name} loss")
        return StageEvidence(
            stage=self.name,
            version=self.version,
            implementation_id=self.implementation_id,
            outcome="PASS",
            evidence={"alpha_id": alpha_id, "as_of": context.event_at},
        )


class CrashAfterPlannedLedger(TrialLedger):
    def __init__(self, path: str):
        super().__init__(path)
        self.armed = True

    def append(self, record):
        result = super().append(record)
        if self.armed and record.status == "PLANNED":
            self.armed = False
            raise KeyboardInterrupt("injected ledger loss")
        return result


class CrashAfterForwardWriteLedger(ForwardLedger):
    def __init__(self, path: str):
        super().__init__(path, create=True)
        self.armed = True

    def record_hourly_decision(self, *args, **kwargs):
        result = super().record_hourly_decision(*args, **kwargs)
        if self.armed:
            self.armed = False
            raise KeyboardInterrupt("injected forward loss")
        return result


def _raw_fixture(root: Path, *, gap: bool = False) -> Path:
    root.mkdir(parents=True)
    rows = []
    for instrument, drift, digest in (("BTC", 0.2, "a" * 64), ("ETH", -0.1, "b" * 64)):
        for index in range(48):
            if gap and instrument == "BTC" and index == 7:
                continue
            event_time = index * 300
            price = 100.0 + drift * index
            rows.append(
                {
                    "schema_version": INPUT_SCHEMA,
                    "kind": "bar",
                    "source_digest": digest,
                    "instrument": instrument,
                    "event_time": event_time,
                    "knowledge_time": event_time,
                    "open": price,
                    "high": price + 0.1,
                    "low": price - 0.1,
                    "close": price,
                    "quote_volume": 1_000_000.0,
                    "interval_seconds": 300,
                }
            )
        rows.extend(
            (
                {
                    "schema_version": INPUT_SCHEMA,
                    "kind": "funding",
                    "source_digest": digest,
                    "instrument": instrument,
                    "event_time": 0,
                    "knowledge_time": 0,
                    "rate": 0.0001,
                },
                {
                    "schema_version": INPUT_SCHEMA,
                    "kind": "open_interest",
                    "source_digest": digest,
                    "instrument": instrument,
                    "event_time": 0,
                    "knowledge_time": 0,
                    "value": 1_000_000.0,
                },
                {
                    "schema_version": INPUT_SCHEMA,
                    "kind": "universe",
                    "source_digest": digest,
                    "instrument": instrument,
                    "event_time": 0,
                    "knowledge_time": 0,
                    "quote_volume": 1_000_000.0,
                    "spread_bps": 1.0,
                    "listed_at": 0,
                    "data_coverage": 1.0,
                },
            )
        )
    (root / "records.jsonl").write_text(
        "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows),
        encoding="utf-8",
    )
    return root


def _materialized(tmp_path: Path) -> tuple[HourlyMarketPanel, Path]:
    output = tmp_path / "materialized"
    materialize_hourly_market(
        raw_root=_raw_fixture(tmp_path / "raw"),
        output_root=output,
        as_of=AS_OF,
        policy=UniversePolicy(min_size=2, max_size=2),
        intervals=(3_600,),
    )
    return load_attested_panel(output, as_of=AS_OF), output


def _definition(window: int, policy_digest: str, *, op: str = "ts_mean") -> AlphaDefinition:
    return AlphaDefinition(
        definition=(op, ("field", "close"), window),
        feature_contract={"fields": ["close"]},
        data_schema={"name": "hourly-market-v1"},
        universe_policy={"name": "attested-dynamic"},
        horizon="1h",
        cost_model={"bps": 5},
        validation_policy={"digest": policy_digest},
        model="ridge",
        model_version="1",
    )


def _workflow_inputs(*, crash_stage: str | None = None, generation_crash=None):
    calls: list[tuple[str, str]] = []
    crash = set() if crash_stage is None else {crash_stage}
    policy = ValidationPolicy(
        (Stage("cheap", calls, crash), Stage("oos", calls, crash), Stage("realism", calls, crash))
    )
    definitions = (_definition(2, policy.digest), _definition(3, policy.digest))

    def generate(context, rng):
        del context, rng
        if generation_crash is not None and generation_crash[0]:
            generation_crash[0] = False
            raise KeyboardInterrupt("injected generation loss")
        return definitions

    registry = GeneratorRegistry(
        (GeneratorEntry("integration-grid", "1", family_id(definitions[0]), generate),)
    )
    return calls, policy, definitions, registry


def _admission() -> ForwardAdmissionRef:
    return ForwardAdmissionRef(
        admission_id="wave1-integration",
        evidence_sha256="a" * 64,
        decision_hash="b" * 64,
        recorded_at_ms=AS_OF,
        price_basis="PERP_MARK_PRICE",
        instrument_scope=("BTC", "ETH"),
    )


def _decision(alpha_id: str, panel: HourlyMarketPanel) -> ForwardDecisionSpec:
    direction = 1.0 if int(alpha_id[0], 16) % 2 else -1.0
    del panel
    return ForwardDecisionSpec(
        "1h",
        {"BTC": 0.5 * direction, "ETH": -0.5 * direction},
        {"BTC": direction, "ETH": -direction},
    )


def _advance(
    root: Path,
    panel: HourlyMarketPanel,
    registry: GeneratorRegistry,
    policy: ValidationPolicy,
    *,
    worker: str = "worker-a",
    lease_now: int = 10,
    trial_ledger: TrialLedger | None = None,
    forward_ledger: ForwardLedger | None = None,
):
    owns_trials = trial_ledger is None
    owns_forward = forward_ledger is None
    trials = trial_ledger or TrialLedger(str(root / "trials.db"))
    forward = forward_ledger or ForwardLedger(str(root / "forward.db"), create=True)
    try:
        with WorkflowStore(str(root / "workflow.db")) as workflow:
            return advance_hourly_alpha(
                panel=panel,
                validation_panel=panel,
                registry=registry,
                policy=policy,
                workflow_store=workflow,
                trial_ledger=trials,
                forward_ledger=forward,
                admission_ref=_admission(),
                decision_builder=_decision,
                as_of=AS_OF,
                budget=2,
                worker_id=worker,
                lease_now=lease_now,
                lease_until=lease_now + 10,
                production=False,
            )
    finally:
        if owns_trials:
            trials.close()
        if owns_forward:
            forward.close()


def _assert_traceable(root: Path, result) -> None:
    with WorkflowStore(str(root / "workflow.db"), create=False) as workflow:
        snapshot = workflow.snapshot(result.receipt.batch_id)
        assert snapshot.state == "SHADOW"
        assert snapshot.checkpoint_watermark == len(result.receipt.candidate_ids)
        assert workflow.events(result.receipt.batch_id)[-1].state == "SHADOW"
    with TrialLedger(str(root / "trials.db"), create=False) as trials:
        assert trials.verify()
        for candidate in result.receipt.candidate_receipts:
            artifact = trials.load_validation_artifact(candidate.trial_id)
            assert artifact["alpha_id"] == candidate.candidate_id
            assert artifact["artifact_commitment"] == candidate.artifact_commitment
    with ForwardLedger(str(root / "forward.db"), read_only=True) as forward:
        assert forward.verify_hourly()
        assert sum(len(forward.hourly_decisions(alpha_id)) for alpha_id in result.receipt.candidate_ids) == 2


def test_fixed_raw_fixture_is_replayable_from_materialization_to_shadow(tmp_path):
    panel, _ = _materialized(tmp_path)
    calls, policy, _, registry = _workflow_inputs()

    first = _advance(tmp_path / "state", panel, registry, policy)
    before = len(calls)
    second = _advance(
        tmp_path / "state",
        panel,
        registry,
        policy,
        worker="worker-b",
        lease_now=20,
    )

    assert first.status == "COMPLETED"
    assert first.workflow_state == "SHADOW"
    assert first.receipt == second.receipt
    assert len(calls) == before == 6
    _assert_traceable(tmp_path / "state", first)


def test_generation_loss_recovers_to_the_same_receipt(tmp_path):
    panel, _ = _materialized(tmp_path)
    armed = [True]
    _, policy, _, registry = _workflow_inputs(generation_crash=armed)
    with pytest.raises(KeyboardInterrupt, match="generation"):
        _advance(tmp_path / "fault", panel, registry, policy)
    recovered = _advance(tmp_path / "fault", panel, registry, policy)

    _, clean_policy, _, clean_registry = _workflow_inputs()
    clean = _advance(tmp_path / "clean", panel, clean_registry, clean_policy)
    assert recovered.receipt == clean.receipt
    _assert_traceable(tmp_path / "fault", recovered)


def test_oos_loss_resumes_receipts_without_repeating_completed_cheap_stage(tmp_path):
    panel, _ = _materialized(tmp_path)
    calls, policy, definitions, registry = _workflow_inputs(crash_stage="oos")
    first_id = sorted(AlphaCandidate(item).alpha_id for item in definitions)[0]
    with pytest.raises(KeyboardInterrupt, match="oos"):
        _advance(tmp_path / "state", panel, registry, policy)
    recovered = _advance(
        tmp_path / "state", panel, registry, policy, worker="worker-b", lease_now=20
    )

    counts = Counter(calls)
    assert counts[("cheap", first_id)] == 1
    assert counts[("oos", first_id)] == 2
    assert recovered.workflow_state == "SHADOW"
    _assert_traceable(tmp_path / "state", recovered)


def test_ledger_loss_after_append_recovers_without_duplicate_trial(tmp_path):
    panel, _ = _materialized(tmp_path)
    _, policy, _, registry = _workflow_inputs()
    root = tmp_path / "state"
    root.mkdir(parents=True)
    crashing = CrashAfterPlannedLedger(str(root / "trials.db"))
    with pytest.raises(KeyboardInterrupt, match="ledger"):
        _advance(root, panel, registry, policy, trial_ledger=crashing)
    crashing.close()

    recovered = _advance(root, panel, registry, policy)

    with TrialLedger(str(root / "trials.db"), create=False) as ledger:
        assert ledger.verify()
        assert all(ledger.load(item.trial_id).status == "PASSED" for item in recovered.receipt.candidate_receipts)
    _assert_traceable(root, recovered)


def test_forward_loss_after_append_recovers_without_duplicate_decision(tmp_path):
    panel, _ = _materialized(tmp_path)
    _, policy, _, registry = _workflow_inputs()
    root = tmp_path / "state"
    root.mkdir(parents=True)
    crashing = CrashAfterForwardWriteLedger(str(root / "forward.db"))
    with pytest.raises(KeyboardInterrupt, match="forward"):
        _advance(root, panel, registry, policy, forward_ledger=crashing)
    crashing.close()

    recovered = _advance(root, panel, registry, policy, worker="worker-b", lease_now=20)

    assert recovered.workflow_state == "SHADOW"
    _assert_traceable(root, recovered)


def test_gap_tamper_and_stale_decision_inputs_fail_closed(tmp_path):
    with pytest.raises(ValueError, match="gap"):
        materialize_hourly_market(
            raw_root=_raw_fixture(tmp_path / "gap-raw", gap=True),
            output_root=tmp_path / "gap-output",
            as_of=AS_OF,
            policy=UniversePolicy(min_size=2, max_size=2),
            intervals=(3_600,),
        )

    panel, output = _materialized(tmp_path / "tamper")
    partition = next(output.glob("hourly_market/*/interval-3600"))
    data = partition / "data.jsonl"
    data.write_bytes(data.read_bytes() + b" ")
    with pytest.raises(ValueError, match="digest|canonical"):
        load_attested_panel(output, as_of=AS_OF)

    stale_bars = tuple(
        HourlyBar(name, AS_OF - HOUR - 1, AS_OF - 1, price, price, price, price, 1.0)
        for name, price in (("BTC", 101.0), ("ETH", 99.0))
    )
    stale_panel = HourlyMarketPanel(
        "hourly-market-v1",
        AS_OF,
        stale_bars,
        (),
        (),
        stable_hash({"fixture": "stale-integration"}),
    )
    _, policy, _, registry = _workflow_inputs()
    blocked = _advance(tmp_path / "stale", stale_panel, registry, policy)
    assert blocked.status == "BLOCKED"
    assert blocked.recorded_decisions == 0
    assert blocked.skipped_decisions == 2


@pytest.mark.slow
def test_10000_candidate_generation_and_family_dedup_smoke_is_bounded():
    policy_digest = stable_hash({"policy": "wave1-slow-smoke"})

    def make(op: str, window: int) -> AlphaDefinition:
        return _definition(window, policy_digest, op=op)

    def means(context, rng):
        del context, rng
        return (make("ts_mean", index + 1) for index in range(5_000))

    def stds(context, rng):
        del context, rng
        return (make("ts_std", index + 1) for index in range(5_000))

    registry = GeneratorRegistry(
        (
            GeneratorEntry("means", "1", family_id(make("ts_mean", 1)), means),
            GeneratorEntry("stds", "1", family_id(make("ts_std", 1)), stds),
        )
    )
    batch = generate_batch(registry, GenerationBudget(10_000, 5_000, 7), {"panel": "fixed"})
    deduplicated = deduplicate_candidates(batch.candidates, correlation_threshold=0.999)

    assert len(batch.candidates) == 10_000
    assert len(deduplicated.representatives) == 10_000
    assert batch.generator_counts == {"means@1": 5_000, "stds@1": 5_000}

