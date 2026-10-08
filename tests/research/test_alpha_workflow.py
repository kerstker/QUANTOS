from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
import sqlite3

import pytest

from quantos.contracts.hashing import stable_hash
from services.research.alpha_family import family_id
from services.research.alpha_garden import (
    GenerationBudget,
    GeneratorEntry,
    GeneratorRegistry,
)
from services.research.alpha_identity import AlphaDefinition
from services.research.alpha_workflow import run_candidate_batch
from services.research.alpha_workflow_store import (
    AlphaWorkflowBusy,
    AlphaWorkflowConflict,
    AlphaWorkflowError,
    AlphaWorkflowFenced,
    AlphaWorkflowStateError,
    WorkflowStore,
)
from services.research.trial_ledger import TrialLedger
from services.research.validation_dag import StageEvidence, ValidationPolicy


HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
HEX_D = "d" * 64


def _batch_id(label: str) -> str:
    return stable_hash({"batch": label})


def _register(store: WorkflowStore, label: str, *, candidates=(HEX_A, HEX_B)) -> str:
    batch_id = _batch_id(label)
    store.register_batch(
        batch_id,
        candidate_ids=candidates,
        panel_digest=HEX_A,
        registry_digest=HEX_B,
        policy_digest=HEX_C,
        budget_digest=HEX_D,
        event_at=10,
        generated_count=len(candidates),
    )
    return batch_id


def test_two_workers_cannot_claim_the_same_live_batch(tmp_path):
    path = tmp_path / "workflow.db"
    with WorkflowStore(str(path)) as first, WorkflowStore(str(path)) as second:
        batch_id = _register(first, "exclusive")

        claim = first.claim(batch_id, "worker-a", 20, now=10)

        assert claim.fencing_token == 1
        assert claim.checkpoint_watermark == 0
        with pytest.raises(AlphaWorkflowBusy, match="worker-a"):
            second.claim(batch_id, "worker-b", 20, now=11)


def test_expired_lease_can_be_taken_over_and_fences_stale_worker(tmp_path):
    path = tmp_path / "workflow.db"
    with WorkflowStore(str(path)) as first, WorkflowStore(str(path)) as second:
        batch_id = _register(first, "takeover")
        stale = first.claim(batch_id, "worker-a", 20, now=10)

        fresh = second.claim(batch_id, "worker-b", 30, now=20)

        assert fresh.fencing_token == stale.fencing_token + 1
        with pytest.raises(AlphaWorkflowFenced):
            first.checkpoint(
                batch_id,
                "worker-a",
                stale.fencing_token,
                HEX_A,
                "VALIDATED",
                trial_id=HEX_C,
                artifact_commitment=HEX_D,
                now=20,
            )


def test_checkpoint_requires_candidate_order_and_terminal_validation_state(tmp_path):
    with WorkflowStore(str(tmp_path / "workflow.db")) as store:
        batch_id = _register(store, "watermark")
        claim = store.claim(batch_id, "worker", 20, now=10)

        with pytest.raises(AlphaWorkflowConflict, match="next candidate"):
            store.checkpoint(
                batch_id,
                "worker",
                claim.fencing_token,
                HEX_B,
                "VALIDATED",
                trial_id=HEX_C,
                artifact_commitment=HEX_D,
                now=11,
            )
        with pytest.raises(AlphaWorkflowStateError, match="VALIDATED or BLOCKED"):
            store.checkpoint(
                batch_id,
                "worker",
                claim.fencing_token,
                HEX_A,
                "RUNNING",
                trial_id=HEX_C,
                artifact_commitment=HEX_D,
                now=11,
            )


def test_batch_state_is_monotonic_and_every_transition_is_audited(tmp_path):
    with WorkflowStore(str(tmp_path / "workflow.db")) as store:
        batch_id = _register(store, "states", candidates=(HEX_A,))
        claim = store.claim(batch_id, "worker", 20, now=10)
        store.checkpoint(
            batch_id,
            "worker",
            claim.fencing_token,
            HEX_A,
            "VALIDATED",
            trial_id=HEX_C,
            artifact_commitment=HEX_D,
            now=11,
        )
        store.complete(
            batch_id,
            "worker",
            claim.fencing_token,
            "VALIDATED",
            HEX_B,
            now=12,
        )
        store.transition(batch_id, "SHADOW", at=13)
        store.transition(batch_id, "PAPER", at=14)

        with pytest.raises(AlphaWorkflowStateError, match="PAPER.*VALIDATED"):
            store.transition(batch_id, "VALIDATED", at=15)

        snapshot = store.snapshot(batch_id)
        assert snapshot.state == "PAPER"
        assert [
            event.state
            for event in store.events(batch_id)
            if event.detail["kind"] != "CHECKPOINT"
        ] == [
            "QUEUED",
            "RUNNING",
            "VALIDATED",
            "SHADOW",
            "PAPER",
        ]


def test_checkpoint_projection_tampering_fails_closed(tmp_path):
    path = tmp_path / "workflow.db"
    with WorkflowStore(str(path)) as store:
        batch_id = _register(store, "tamper", candidates=(HEX_A,))
        claim = store.claim(batch_id, "worker", 20, now=10)
        store.checkpoint(
            batch_id,
            "worker",
            claim.fencing_token,
            HEX_A,
            "VALIDATED",
            trial_id=HEX_C,
            artifact_commitment=HEX_D,
            now=11,
        )
        connection = sqlite3.connect(path)
        connection.execute(
            "UPDATE alpha_workflow_candidates SET artifact_commitment=? WHERE batch_id=?",
            ("e" * 64, batch_id),
        )
        connection.commit()
        connection.close()

        with pytest.raises(AlphaWorkflowError, match="candidate projection"):
            store.snapshot(batch_id)


def test_workflow_event_history_rejects_update_and_delete(tmp_path):
    path = tmp_path / "workflow.db"
    with WorkflowStore(str(path)) as store:
        batch_id = _register(store, "append-only", candidates=(HEX_A,))

    connection = sqlite3.connect(path)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute(
            "UPDATE alpha_workflow_events SET state='BLOCKED' WHERE batch_id=?",
            (batch_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        connection.execute("DELETE FROM alpha_workflow_events WHERE batch_id=?", (batch_id,))
    connection.close()


class RecordingStage:
    version = "1"

    def __init__(self, name: str, calls: list[tuple[str, str]], *, crash_once: set[str] | None = None):
        self.name = name
        self.implementation_id = f"tests.alpha_workflow.{name}.v1"
        self.calls = calls
        self.crash_once = crash_once if crash_once is not None else set()

    def evaluate(self, context):
        alpha_id = context.candidate.alpha_id
        self.calls.append((self.name, alpha_id))
        if alpha_id in self.crash_once:
            self.crash_once.remove(alpha_id)
            raise KeyboardInterrupt("simulated process loss")
        return StageEvidence(
            stage=self.name,
            version=self.version,
            implementation_id=self.implementation_id,
            outcome="PASS",
            evidence={"alpha_id": alpha_id},
        )


def _definition(window: int, policy_digest: str) -> AlphaDefinition:
    return AlphaDefinition(
        definition=("ts_mean", ("field", "close"), window),
        feature_contract={"fields": ["close"]},
        data_schema={"name": "hourly", "version": 1},
        universe_policy={"name": "dynamic"},
        horizon="1h",
        cost_model={"bps": 5},
        validation_policy={"digest": policy_digest},
        model="ridge",
        model_version="1",
    )


def _workflow_inputs(calls, *, crash_once_alpha: str | None = None):
    crash_once = set() if crash_once_alpha is None else {crash_once_alpha}
    stages = (
        RecordingStage("cheap", calls, crash_once=crash_once),
        RecordingStage("oos", calls),
        RecordingStage("realism", calls),
    )
    policy = ValidationPolicy(stages)
    definitions = (_definition(2, policy.digest), _definition(3, policy.digest))

    def generate(context, rng):
        del context, rng
        return definitions

    registry = GeneratorRegistry(
        (GeneratorEntry("grid", "1", family_id(definitions[0]), generate),)
    )
    return registry, GenerationBudget(2, 2, 7), policy, definitions


def _run(
    *,
    batch_id,
    registry,
    budget,
    policy,
    ledger,
    store,
    worker,
    now,
    panel=None,
):
    return run_candidate_batch(
        batch_id=batch_id,
        registry=registry,
        budget=budget,
        context={"research": "hourly"},
        panel={"bars": [1, 2, 3]} if panel is None else panel,
        policy=policy,
        ledger=ledger,
        store=store,
        event_at=100,
        worker_id=worker,
        lease_now=now,
        lease_until=now + 5,
        correlation_threshold=0.999,
    )


def test_same_batch_retry_returns_same_receipt_without_revalidating(tmp_path):
    calls = []
    registry, budget, policy, _ = _workflow_inputs(calls)
    batch_id = _batch_id("idempotent")
    with WorkflowStore(str(tmp_path / "workflow.db")) as store, TrialLedger(
        str(tmp_path / "trials.db")
    ) as ledger:
        first = _run(
            batch_id=batch_id,
            registry=registry,
            budget=budget,
            policy=policy,
            ledger=ledger,
            store=store,
            worker="worker-a",
            now=10,
        )
        call_count = len(calls)
        second = _run(
            batch_id=batch_id,
            registry=registry,
            budget=budget,
            policy=policy,
            ledger=ledger,
            store=store,
            worker="worker-b",
            now=20,
        )
        effective_counts = {
            ledger.load_validation_artifact(item.trial_id)["effective_trial_count"]
            for item in first.candidate_receipts
        }

    assert first == second
    assert first.receipt_digest == second.receipt_digest
    assert first.state == "VALIDATED"
    assert first.generated_count == 2
    assert first.representative_count == 2
    assert first.validated_count == 2
    assert first.blocked_count == 0
    assert effective_counts == {2}
    assert len(calls) == call_count == 6


def test_crash_after_checkpoint_resumes_only_unfinished_candidate(tmp_path):
    probe_calls = []
    probe_registry, _, probe_policy, probe_definitions = _workflow_inputs(probe_calls)
    del probe_registry, probe_policy
    # Deduplication orders representatives by canonical alpha identity.
    from services.research.alpha_family import AlphaCandidate

    ordered_ids = sorted(AlphaCandidate(definition).alpha_id for definition in probe_definitions)
    calls = []
    registry, budget, policy, _ = _workflow_inputs(calls, crash_once_alpha=ordered_ids[1])
    batch_id = _batch_id("resume")
    with WorkflowStore(str(tmp_path / "workflow.db")) as store, TrialLedger(
        str(tmp_path / "trials.db")
    ) as ledger:
        with pytest.raises(KeyboardInterrupt, match="simulated process loss"):
            _run(
                batch_id=batch_id,
                registry=registry,
                budget=budget,
                policy=policy,
                ledger=ledger,
                store=store,
                worker="worker-a",
                now=10,
            )

        crashed = store.snapshot(batch_id)
        assert crashed.state == "RUNNING"
        assert crashed.checkpoint_watermark == 1

        receipt = _run(
            batch_id=batch_id,
            registry=registry,
            budget=budget,
            policy=policy,
            ledger=ledger,
            store=store,
            worker="worker-b",
            now=15,
        )

    counts = Counter(calls)
    assert receipt.state == "VALIDATED"
    assert counts[("cheap", ordered_ids[0])] == 1
    assert counts[("oos", ordered_ids[0])] == 1
    assert counts[("realism", ordered_ids[0])] == 1
    assert counts[("cheap", ordered_ids[1])] == 2
    assert counts[("oos", ordered_ids[1])] == 1
    assert counts[("realism", ordered_ids[1])] == 1


def test_same_batch_rejects_changed_panel_binding(tmp_path):
    calls = []
    registry, budget, policy, _ = _workflow_inputs(calls)
    batch_id = _batch_id("binding")
    with WorkflowStore(str(tmp_path / "workflow.db")) as store, TrialLedger(
        str(tmp_path / "trials.db")
    ) as ledger:
        _run(
            batch_id=batch_id,
            registry=registry,
            budget=budget,
            policy=policy,
            ledger=ledger,
            store=store,
            worker="worker-a",
            now=10,
        )
        with pytest.raises(AlphaWorkflowConflict, match="batch identity"):
            _run(
                batch_id=batch_id,
                registry=registry,
                budget=budget,
                policy=policy,
                ledger=ledger,
                store=store,
                worker="worker-b",
                now=20,
                panel={"bars": [9, 9, 9]},
            )


@pytest.mark.parametrize(
    "relative_path",
    (
        "services/research/alpha_workflow_store.py",
        "services/research/alpha_workflow.py",
    ),
)
def test_alpha_workflow_services_do_not_read_wall_clock(relative_path):
    source = Path(relative_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id in {"time", "datetime"}:
                forbidden.append((node.func.value.id, node.func.attr))
    assert forbidden == []
