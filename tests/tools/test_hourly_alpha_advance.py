from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

from quantos.contracts.hashing import stable_hash
from services.data.hourly_market import HourlyBar, HourlyMarketPanel
from services.research.alpha_family import AlphaCandidate, family_id
from services.research.alpha_garden import GeneratorEntry, GeneratorRegistry
from services.research.alpha_identity import AlphaDefinition
from services.research.alpha_workflow_store import WorkflowStore
from services.research.paper_trading.forward_evidence import ForwardAdmissionRef
from services.research.paper_trading.forward_store import ForwardLedger
from services.research.trial_ledger import TrialLedger
from services.research.validation_dag import StageEvidence, ValidationPolicy
from tools.hourly_alpha_advance import (
    MAX_GENERATION_BUDGET,
    ForwardDecisionSpec,
    advance_hourly_alpha,
    build_parser,
)


T0 = 1_800_000_000
HOUR = 3_600


class Stage:
    version = "1"

    def __init__(
        self,
        name: str,
        calls: list[tuple[str, str]],
        *,
        outcome: str = "PASS",
        crash_once: set[str] | None = None,
    ) -> None:
        self.name = name
        self.calls = calls
        self.outcome = outcome
        self.crash_once = crash_once if crash_once is not None else set()
        self.implementation_id = f"tests.hourly_alpha_advance.{name}.v1"

    def evaluate(self, context):
        alpha_id = context.candidate.alpha_id
        self.calls.append((self.name, alpha_id))
        if alpha_id in self.crash_once:
            self.crash_once.remove(alpha_id)
            raise KeyboardInterrupt("simulated scheduled-worker loss")
        return StageEvidence(
            stage=self.name,
            version=self.version,
            implementation_id=self.implementation_id,
            outcome=self.outcome,
            evidence={"alpha_id": alpha_id},
            reason=None if self.outcome == "PASS" else "test block",
        )


def _panel() -> HourlyMarketPanel:
    bars = []
    for instrument, before, now in (("BTC", 100.0, 102.0), ("ETH", 100.0, 98.0)):
        bars.extend(
            (
                HourlyBar(instrument, T0 - HOUR, T0 - HOUR, before, before, before, before, 1_000.0),
                HourlyBar(instrument, T0, T0, now, now, now, now, 1_000.0),
            )
        )
    return HourlyMarketPanel(
        "hourly-market-v1",
        T0,
        tuple(bars),
        (),
        (),
        stable_hash({"fixture": "hourly-alpha-advance"}),
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


def _inputs(*, outcome: str = "PASS", crash_alpha: str | None = None):
    calls: list[tuple[str, str]] = []
    crash_once = set() if crash_alpha is None else {crash_alpha}
    policy = ValidationPolicy(
        (
            Stage("cheap", calls, outcome=outcome, crash_once=crash_once),
            Stage("oos", calls),
            Stage("realism", calls),
        )
    )
    definitions = (_definition(2, policy.digest), _definition(3, policy.digest))

    def generate(context, rng):
        del context, rng
        return definitions

    registry = GeneratorRegistry(
        (GeneratorEntry("hourly-grid", "1", family_id(definitions[0]), generate),)
    )
    return calls, policy, definitions, registry


def _admission() -> ForwardAdmissionRef:
    return ForwardAdmissionRef(
        admission_id="hourly-pass",
        evidence_sha256="a" * 64,
        decision_hash="b" * 64,
        recorded_at_ms=T0,
        price_basis="PERP_MARK_PRICE",
        instrument_scope=("BTC", "ETH"),
    )


def _decision(alpha_id: str, panel: HourlyMarketPanel) -> ForwardDecisionSpec:
    del alpha_id, panel
    return ForwardDecisionSpec(
        horizon="1h",
        weights={"BTC": 0.5, "ETH": -0.5},
        signals={"BTC": 1.0, "ETH": -1.0},
    )


def _run(
    tmp_path,
    registry,
    policy,
    *,
    worker="worker-a",
    lease_now=T0,
    paper_intent_projector=None,
):
    panel = _panel()
    with WorkflowStore(str(tmp_path / "workflow.db")) as workflow, TrialLedger(
        str(tmp_path / "trials.db")
    ) as trials:
        forward = ForwardLedger(str(tmp_path / "forward.db"), create=True)
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
            as_of=T0,
            budget=2,
            worker_id=worker,
            lease_now=lease_now,
            lease_until=lease_now + 10,
            production=False,
            paper_intent_projector=paper_intent_projector,
        )


def test_fixed_panel_runs_garden_validation_and_shadow_decisions_idempotently(tmp_path):
    calls, policy, _, registry = _inputs()

    first = _run(tmp_path, registry, policy)
    call_count = len(calls)
    second = _run(tmp_path, registry, policy, worker="worker-b")

    assert first.status == "COMPLETED"
    assert first.workflow_state == "SHADOW"
    assert first.receipt.validated_count == 2
    assert first.recorded_decisions == 2
    assert first.receipt == second.receipt
    assert second.recorded_decisions == 2
    assert len(calls) == call_count == 6
    ledger = ForwardLedger(str(tmp_path / "forward.db"))
    assert sum(len(ledger.hourly_decisions(alpha_id)) for alpha_id in first.receipt.candidate_ids) == 2


def test_zero_passers_is_a_successful_blocked_receipt(tmp_path):
    calls, policy, _, registry = _inputs(outcome="BLOCK")

    result = _run(tmp_path, registry, policy)

    assert result.status == "COMPLETED"
    assert result.workflow_state == "BLOCKED"
    assert result.receipt.validated_count == 0
    assert result.receipt.blocked_count == 2
    assert result.recorded_decisions == 0
    assert Counter(name for name, _ in calls) == {"cheap": 2}


def test_crash_checkpoint_resumes_without_revalidating_finished_candidate(tmp_path):
    _, probe_policy, definitions, _ = _inputs()
    del probe_policy
    ordered_ids = sorted(AlphaCandidate(item).alpha_id for item in definitions)
    calls, policy, _, registry = _inputs(crash_alpha=ordered_ids[1])

    with pytest.raises(KeyboardInterrupt, match="scheduled-worker loss"):
        _run(tmp_path, registry, policy)
    result = _run(tmp_path, registry, policy, worker="worker-b", lease_now=T0 + 10)

    counts = Counter(calls)
    assert result.workflow_state == "SHADOW"
    assert counts[("cheap", ordered_ids[0])] == 1
    assert counts[("cheap", ordered_ids[1])] == 2


def test_shadow_workflow_cannot_project_a_testnet_intent(tmp_path):
    _, policy, _, registry = _inputs()
    projected = []

    result = _run(
        tmp_path,
        registry,
        policy,
        paper_intent_projector=projected.append,
    )

    assert result.workflow_state == "SHADOW"
    assert projected == []


def test_existing_paper_workflow_invokes_intent_projection_after_completion(tmp_path):
    _, policy, _, registry = _inputs()
    initial = _run(tmp_path, registry, policy)
    with WorkflowStore(str(tmp_path / "workflow.db")) as workflow:
        workflow.transition(initial.receipt.batch_id, "PAPER", at=T0)
    projected = []

    result = _run(
        tmp_path,
        registry,
        policy,
        worker="worker-b",
        paper_intent_projector=projected.append,
    )

    assert result.status == "COMPLETED"
    assert result.workflow_state == "PAPER"
    assert projected == [result]


def test_paper_intent_outbox_failure_is_not_hidden_as_completed(tmp_path):
    _, policy, _, registry = _inputs()
    initial = _run(tmp_path, registry, policy)
    with WorkflowStore(str(tmp_path / "workflow.db")) as workflow:
        workflow.transition(initial.receipt.batch_id, "PAPER", at=T0)

    def fail_projection(result):
        del result
        raise OSError("simulated intent outbox failure")

    with pytest.raises(OSError, match="outbox failure"):
        _run(
            tmp_path,
            registry,
            policy,
            worker="worker-b",
            paper_intent_projector=fail_projection,
        )


def test_budget_is_bounded_and_cli_has_no_live_or_submission_switches(tmp_path):
    _, policy, _, registry = _inputs()
    with pytest.raises(ValueError, match="100000"):
        panel = _panel()
        with WorkflowStore(str(tmp_path / "workflow.db")) as workflow, TrialLedger(
            str(tmp_path / "trials.db")
        ) as trials:
            forward = ForwardLedger(str(tmp_path / "forward.db"), create=True)
            advance_hourly_alpha(
                panel=panel,
                validation_panel=panel,
                registry=registry,
                policy=policy,
                workflow_store=workflow,
                trial_ledger=trials,
                forward_ledger=forward,
                admission_ref=None,
                decision_builder=None,
                as_of=T0,
                budget=MAX_GENERATION_BUDGET + 1,
                worker_id="worker",
                lease_now=T0,
                lease_until=T0 + 10,
                production=False,
            )

    parser = build_parser()
    option_strings = {option for action in parser._actions for option in action.option_strings}
    assert {"--live", "--submit", "--arm"}.isdisjoint(option_strings)


def test_scheduler_source_has_no_network_broker_credentials_or_process_global_lock():
    source = Path("tools/hourly_alpha_advance.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(
        token in name.lower()
        for name in imports
        for token in ("requests", "httpx", "broker", "credential", ".live")
    )
    assert "SqliteLeaderLeaseStore" in source
    assert "threading.Lock" not in source
