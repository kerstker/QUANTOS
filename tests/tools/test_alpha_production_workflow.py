from __future__ import annotations

import json
import sqlite3

import pytest

from quantos.contracts.hashing import stable_hash
from services.research import alpha_expression as ax
from services.research.alpha_production_workflow import (
    AlphaProductionWorkflow,
    CandidateDefinitionKind,
    CandidateIdentity,
    LIFECYCLE_FALSE_RETIREMENT_CORRECTION,
    LIFECYCLE_FALSE_RETIREMENT_RELEASE,
    MaterialChangeClass,
)
from services.research.paper_trading.attested_store import AttestedForwardLedger
from services.research.paper_trading.forward_evidence import ForwardAdmissionRef
from services.research.portfolio_evidence import PortfolioGatePolicy
from tools.alpha_production_workflow import (
    _build_release_run_attestation,
    _realtime_formal_shadow_required,
    reconcile_workflow,
)


COMMIT = "b" * 40
DAY = 86_400_000

_ADMISSION_REF = ForwardAdmissionRef(
    admission_id="adm-test",
    evidence_sha256="a" * 64,
    decision_hash="b" * 64,
    recorded_at_ms=1,
    price_basis="PERP_MARK_PRICE",
    instrument_scope=("BTC",),
)


def _identity(candidate_id: str) -> CandidateIdentity:
    expression = ("cs_rank", ("neg", ("field", "global_ls")))
    return CandidateIdentity(
        candidate_id=candidate_id,
        expression=ax.serialize(expression),
        quantile=0.3,
        cadence_days=7,
        universe="cs_majors",
        family_id="CROWDING_REVERSAL",
        source_kind="STATIC_PREREGISTERED",
        source_ref="test",
        source_definition_hash=stable_hash({"candidate": candidate_id}),
        required_datasets=("global_ls",),
        economic_hypothesis="Crowding should mean revert.",
        falsification_criteria=("Held-out net spread is non-positive.",),
        created_at_ms=0,
        change_class=MaterialChangeClass.NEW_CANDIDATE,
    )


def _realtime_identity(candidate_id: str) -> CandidateIdentity:
    signal_spec = json.dumps(
        {"candidate_id": candidate_id, "policy_hash": "a" * 64},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return CandidateIdentity(
        candidate_id=candidate_id,
        expression="",
        quantile=0.3,
        cadence_days=0,
        universe="binance_usdm_14_realtime_public",
        family_id="order_flow",
        source_kind="AUTONOMOUS_REALTIME_PREREGISTERED",
        source_ref="realtime_alpha_registry:" + "b" * 64,
        source_definition_hash=stable_hash({"candidate": candidate_id}),
        required_datasets=("rt_mid_price", "rt_taker_imbalance_5s"),
        economic_hypothesis="Aggressive flow should predict the next price move.",
        falsification_criteria=("Net held-out spread is non-positive.",),
        created_at_ms=1,
        change_class=MaterialChangeClass.NEW_CANDIDATE,
        definition_kind=CandidateDefinitionKind.REALTIME_MECHANISM,
        signal_spec_json=signal_spec,
        decision_interval_ms=30_000,
        label_horizon_ms=120_000,
    )


def _evaluation(candidate_id: str, verdict: str = "PASS") -> dict:
    return {
        "schema_version": "forward_alpha_evaluations.v1",
        "generated_at": 2_000,
        "dataset_version": "dataset-v1",
        "alphas": {
            candidate_id: {
                "status": "VERIFIED",
                "evaluation": {
                    "verified": True,
                    "verdict": verdict,
                    "run_id": f"run-{candidate_id}",
                },
            }
        },
    }


def _write_ledger(path, windows_by_id: dict) -> None:
    """真 AttestedForwardLedger + 链上 attested 决策/兑现行(非 mock)。"""
    ledger = AttestedForwardLedger(str(path), create=True)
    try:
        for alpha_id, windows in windows_by_id.items():
            for decided_ts, as_of_ts in windows:
                ledger.record_decision(
                    alpha_id,
                    decided_ts,
                    recorded_at=decided_ts,
                    weights={"BTC": 1.0},
                    admission_ref=_ADMISSION_REF,
                )
                ledger.commit_realization(
                    alpha_id,
                    decided_ts,
                    as_of_ts=as_of_ts,
                    realized_return=0.01,
                    realized_ic=None,
                    inputs={"BTC": 1.0},
                    admission_ref=_ADMISSION_REF,
                    committed_at_ms=as_of_ts,
                )
    finally:
        ledger.close()


def _weekly_windows(n: int = 8, *, spacing: int = 7, base_day: int = 100) -> list:
    """spacing=7 → 端点相接不重叠;spacing<7 → 重叠(独立观察数打折)。"""
    return [
        ((base_day + i * spacing) * DAY, (base_day + i * spacing + 7) * DAY)
        for i in range(n)
    ]


def test_release_run_attestation_binds_sources_policies_chains_and_projection(
    tmp_path,
) -> None:
    output = tmp_path / "alpha_production_workflow.json"
    output.write_text('{"status":"VERIFIED"}\n', encoding="utf-8")
    snapshot = {
        "status": "VERIFIED",
        "source_hashes": {
            "candidate_identity_set": "1" * 64,
            "attested_forward_realizations": "2" * 64,
            "historical_evaluations": "3" * 64,
        },
        "reconciliation": {
            "release_commit": COMMIT,
            "unblinding_policy_hash": "4" * 64,
            "portfolio_gate_policy_hash": "5" * 64,
        },
        "population_chain_head": "6" * 64,
        "event_chain_head": "7" * 64,
        "safety": {
            "paper_only": True,
            "live_allowed": False,
            "order_submission_allowed": False,
            "credentials_accessed": False,
            "broker_connectivity": False,
        },
    }

    attestation = _build_release_run_attestation(snapshot, output_path=output)

    assert attestation["complete"] is True
    assert attestation["release_commit"] == COMMIT
    assert attestation["input_hashes"]["candidate_identity_set"] == "1" * 64
    assert attestation["input_hashes"]["unblinding_policy"] == "4" * 64
    assert attestation["input_hashes"]["portfolio_gate_policy"] == "5" * 64
    assert attestation["output_hashes"]["population_chain_head"] == "6" * 64
    assert attestation["output_hashes"]["event_chain_head"] == "7" * 64
    assert len(attestation["output_hashes"]["workflow_projection_file"]) == 64


def _cyclic_funding_document(n: int = 200) -> dict:
    """5 日周期资金费:跨窗口落在多个 expanding 三分位(多 regime 类)。"""
    return {"BTC": {str(d * DAY): ((d % 5) - 2) * 0.0001 for d in range(n)}}


def _quadratic_funding_document(n: int = 200) -> dict:
    """凸增长资金费:trailing z 单调,全部窗口落同一三分位(单 regime 类)。"""
    return {"BTC": {str(d * DAY): 1e-6 * d * d for d in range(n)}}


def _monovol_marks_document(n: int = 201) -> dict:
    """单调升波动 BTC 价格:rv 维 expanding z 三分位恒为一档。"""
    price = 100.0
    series = {"0": price}
    for i in range(1, n):
        magnitude = 0.002 + 0.00005 * i
        price *= 1 + (magnitude if i % 2 == 0 else -magnitude)
        series[str(i * DAY)] = price
    return {"_meta": {}, "data": {"BTC": series}, "provenance": {}}


def _shadow_inputs(tmp_path, candidate_ids, *, spacing: int = 7, funding=None) -> dict:
    ledger_path = tmp_path / "paper_track.db"
    _write_ledger(
        ledger_path,
        {candidate_id: _weekly_windows(spacing=spacing) for candidate_id in candidate_ids},
    )
    return {
        "paper_track_path": ledger_path,
        "marks_document": _monovol_marks_document(),
        "funding_document": (
            funding if funding is not None else _cyclic_funding_document()
        ),
    }


def _portfolio_row(*, missing=()) -> dict:
    """完备且 W4-3 merit PASS 的 portfolio evidence 行(形状镜像真 artifact);
    missing 声明驱动 INCOMPLETE,子块保持 available——隔离「声明缺失」变量。"""
    return {
        "status": "INCOMPLETE" if missing else "OK",
        "marginal_contribution": {"available": True, "marginal_ir": 0.13,
                                  "incremental_sharpe": 0.34},
        "common_exposure": {"available": True, "r_squared": 0.05,
                            "max_residual_corr_vs_incumbent": 0.07},
        "capacity": {"available": True, "low": 7_000_000.0},
        "crowding": {"available": True, "blocked_share": 0.09},
        "tca": {"available": True},
        "missing": list(missing),
    }


def _portfolio_document(candidate_ids, *, missing=()) -> dict:
    """WP4 W4-2/W4-3 portfolio evidence 文档:默认完备且 merit PASS。"""
    return {
        "schema_version": 1,
        "generated_at_ms": 2_400,
        "alphas": {
            candidate_id: _portfolio_row(missing=missing)
            for candidate_id in candidate_ids
        },
    }


def _reconcile(
    workflow: AlphaProductionWorkflow,
    identities: tuple[CandidateIdentity, ...],
    *,
    shadow: dict,
    risk_ready: bool = True,
    portfolio: dict | None = None,
    portfolio_policy=None,
    portfolio_reference_aum: float | None = None,
    lifecycle: dict | None = None,
    release_commit: str = COMMIT,
) -> dict:
    candidate_ids = [identity.candidate_id for identity in identities]
    forward_eval = {
        "schema_version": "forward_alpha_evaluations.v1",
        "generated_at": 2_000,
        "alphas": {
            candidate_id: _evaluation(candidate_id)["alphas"][candidate_id]
            for candidate_id in candidate_ids
        },
    }
    return reconcile_workflow(
        workflow=workflow,
        identities=identities,
        historical_document={},
        historical_path="historical.json",
        forward_evaluations_document=forward_eval,
        forward_evaluations_path="forward-evaluations.json",
        forward_status_document={
            "ran_at": 2_100,
            "alphas": {
                candidate_id: {"n_forward_periods": 8, "evidence_state": "RUNNING"}
                for candidate_id in candidate_ids
            },
        },
        forward_status_path="forward-status.json",
        risk_document={
            "generated_at": 2_200,
            "config_hash": "risk-v1",
            "alphas": {
                candidate_id: {"status": "READY" if risk_ready else "PENDING"}
                for candidate_id in candidate_ids
            },
        },
        risk_path="risk.json",
        realtime_document={
            "generated_at": 2_300,
            "alphas": {
                candidate_id: {"status": "PAPER_MARKED"}
                for candidate_id in candidate_ids
            },
        },
        realtime_path="realtime.json",
        lifecycle_document=lifecycle or {},
        lifecycle_path="lifecycle.json",
        release_commit=release_commit,
        paper_track_path=shadow["paper_track_path"],
        marks_document=shadow["marks_document"],
        funding_document=shadow["funding_document"],
        portfolio_document=(
            portfolio if portfolio is not None else _portfolio_document(candidate_ids)
        ),
        portfolio_policy=portfolio_policy,
        portfolio_reference_aum=portfolio_reference_aum,
    )


def _gates(row: dict) -> dict:
    return {gate["gate"]: gate for gate in row["workflow"]["gates"]}


def test_reconciler_creates_one_authoritative_candidate_state(tmp_path) -> None:
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        snapshot = _reconcile(workflow, (identity,), shadow=shadow)
        assert snapshot["status"] == "VERIFIED"
        assert snapshot["active_candidate_ids"] == [identity.candidate_id]
        assert snapshot["execution_paper_eligible_ids"] == [identity.candidate_id]
        assert snapshot["execution_paper_ids"] == [identity.candidate_id]
        assert snapshot["reconciliation"]["inserted_candidates"] == 1
        assert snapshot["reconciliation"]["inserted_events"] == 1
        assert snapshot["source_hashes"]["candidate_identity_set"] == stable_hash(
            [
                {
                    "candidate_id": identity.candidate_id,
                    "identity_hash": identity.identity_hash,
                    "definition_hash": identity.definition_hash,
                }
            ]
        )
        assert snapshot["source_hashes"][
            "attested_forward_realizations"
        ] == stable_hash(
            {
                identity.candidate_id: [
                    {"decided_ts": decided_ts, "as_of_ts": as_of_ts}
                    for decided_ts, as_of_ts in _weekly_windows()
                ]
            }
        )
        row = snapshot["candidates"][0]
        assert row["workflow"]["stage"] == "EXECUTION_PAPER"
        assert row["workflow"]["eligible_for_execution_paper"] is True
        # 8 个互不重叠期 + 多 regime 类 → 揭盲 READY → MATURE(现行行为保持)
        assert _gates(row)["SIGNAL_SHADOW"]["status"] == "MATURE"

        replay = _reconcile(workflow, (identity,), shadow=shadow)
        assert replay["reconciliation"]["inserted_candidates"] == 0
        assert replay["reconciliation"]["inserted_events"] == 0


def test_lifecycle_gate_consumes_per_alpha_cockpit_rows_without_changing_eligibility(
    tmp_path,
) -> None:
    """The cockpit stores ``alphas`` as a list, unlike older map artifacts.

    Lifecycle evidence must become visible to the workflow, but its presence
    must not alter the existing eligibility predicate by itself.
    """
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    lifecycle = {
        "artifact": "ALPHA_LIFECYCLE_COCKPIT",
        "generated_at_ms": 2_500,
        "alphas": [
            {
                "alpha_id": identity.candidate_id,
                "derived_stage": "PAPER",
                "stages": {
                    "paper": {"status": "PRESENT"},
                    "retirement": {"status": "PRESENT", "retired": False},
                    "decay": {"status": "PRESENT", "degraded": False},
                },
            }
        ],
    }
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        snapshot = _reconcile(
            workflow,
            (identity,),
            shadow=shadow,
            lifecycle=lifecycle,
        )

    row = snapshot["candidates"][0]
    assert _gates(row)["LIFECYCLE"]["status"] == "RUNNING"
    assert snapshot["execution_paper_eligible_ids"] == [identity.candidate_id]


def test_lifecycle_degraded_and_retired_states_fail_closed(tmp_path) -> None:
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    degraded = {
        "artifact": "ALPHA_LIFECYCLE_COCKPIT",
        "generated_at_ms": 2_500,
        "alphas": [
            {
                "alpha_id": identity.candidate_id,
                "derived_stage": "PAPER",
                "stages": {
                    "retirement": {"status": "PRESENT", "retired": False},
                    "decay": {
                        "status": "PRESENT",
                        "degraded": True,
                        "alpha_action": {"state": "DEGRADED"},
                    },
                },
            }
        ],
    }
    with AlphaProductionWorkflow(tmp_path / "degraded.db") as workflow:
        blocked = _reconcile(
            workflow, (identity,), shadow=shadow, lifecycle=degraded
        )
    blocked_row = blocked["candidates"][0]
    assert _gates(blocked_row)["LIFECYCLE"]["status"] == "BLOCKED"
    assert "LIFECYCLE_BLOCKED" in blocked_row["workflow"]["blockers"]
    assert blocked["execution_paper_eligible_ids"] == []

    retired = {
        **degraded,
        "alphas": [
            {
                **degraded["alphas"][0],
                "stages": {
                    "retirement": {"status": "PRESENT", "retired": True},
                    "decay": {"status": "PRESENT", "degraded": False},
                },
            }
        ],
    }
    with AlphaProductionWorkflow(tmp_path / "retired.db") as workflow:
        terminal = _reconcile(
            workflow, (identity,), shadow=shadow, lifecycle=retired
        )
    terminal_row = terminal["candidates"][0]
    assert _gates(terminal_row)["LIFECYCLE"]["status"] == "RETIRED"
    assert terminal_row["workflow"]["stage"] == "RETIRED"
    assert "LIFECYCLE_RETIRED" in terminal_row["workflow"]["blockers"]
    assert terminal["active_candidate_ids"] == []
    assert terminal["execution_paper_eligible_ids"] == []


def test_known_false_retirement_is_corrected_with_an_audited_event(tmp_path) -> None:
    identity = _identity("crowd_contra")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    retired = {
        "artifact": "ALPHA_LIFECYCLE_COCKPIT",
        "generated_at_ms": 2_500,
        "alphas": [
            {
                "alpha_id": identity.candidate_id,
                "derived_stage": "RETIRED",
                "stages": {
                    "retirement": {"status": "PRESENT", "retired": True},
                },
            }
        ],
    }
    running = {
        "artifact": "ALPHA_LIFECYCLE_COCKPIT",
        "generated_at_ms": 2_600,
        "alphas": [
            {
                "alpha_id": identity.candidate_id,
                "derived_stage": "PAPER",
                "stages": {
                    "retirement": {"status": "PRESENT", "retired": False},
                    "decay": {"status": "PRESENT", "degraded": False},
                },
            }
        ],
    }
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        first = _reconcile(
            workflow,
            (identity,),
            shadow=shadow,
            lifecycle=retired,
            release_commit=LIFECYCLE_FALSE_RETIREMENT_RELEASE,
        )
        assert first["candidates"][0]["workflow"]["stage"] == "RETIRED"

        repaired = _reconcile(
            workflow,
            (identity,),
            shadow=shadow,
            lifecycle=running,
        )

    row = repaired["candidates"][0]
    assert row["workflow"]["stage"] == "PARALLEL_VALIDATION"
    assert row["workflow"]["transition_reason"] == LIFECYCLE_FALSE_RETIREMENT_CORRECTION
    assert _gates(row)["LIFECYCLE"]["status"] == "RUNNING"
    assert repaired["active_candidate_ids"] == [identity.candidate_id]
    assert repaired["reconciliation"]["inserted_events"] == 1


def test_real_retirement_remains_terminal(tmp_path) -> None:
    identity = _identity("crowd_contra")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    retired = {
        "artifact": "ALPHA_LIFECYCLE_COCKPIT",
        "generated_at_ms": 2_500,
        "alphas": [
            {
                "alpha_id": identity.candidate_id,
                "derived_stage": "RETIRED",
                "stages": {"retirement": {"status": "PRESENT", "retired": True}},
            }
        ],
    }
    running = {
        "artifact": "ALPHA_LIFECYCLE_COCKPIT",
        "generated_at_ms": 2_600,
        "alphas": [
            {
                "alpha_id": identity.candidate_id,
                "derived_stage": "PAPER",
                "stages": {"retirement": {"status": "PRESENT", "retired": False}},
            }
        ],
    }
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        _reconcile(workflow, (identity,), shadow=shadow, lifecycle=retired)
        with pytest.raises(ValueError, match="retired stage requires lifecycle evidence"):
            _reconcile(workflow, (identity,), shadow=shadow, lifecycle=running)


def test_realtime_engineering_tracks_are_visible_but_never_formal_evidence(
    tmp_path,
) -> None:
    identity = _realtime_identity("rt_flow_test_v1")
    discovery = {
        "generated_at_ms": 3_000,
        "research_policy_hash": "c" * 64,
        "data_admission": {"formal_validation_allowed": True},
        "candidates": [
            {
                "candidate_id": identity.candidate_id,
                "evaluation_status": "EVALUATED",
                "gate_pass": True,
                "verdict": "PASS",
            }
        ],
    }
    engineering_shadow = {
        "generated_at_ms": 3_100,
        "tracks": {
            identity.candidate_id: {
                "candidate_id": identity.candidate_id,
                "n_realizations": 500,
                "cum_return": 1.0,
            }
        },
    }
    engineering_execution = {
        "generated_at_ms": 3_200,
        "tracks": {
            identity.candidate_id: {
                "candidate_id": identity.candidate_id,
                "state": "EXECUTING",
                "n_executions": 500,
            }
        },
    }
    formal_shadow = {
        "generated_at_ms": 3_150,
        "mode": "AUTONOMOUS_REALTIME_FORMAL_BLINDED_SHADOW",
        "authority": {
            "name": "FORMAL_BLINDED_SHADOW",
            "formal_blinded": True,
            "selector_feedback_consumed": False,
            "outcome_feedback_feed": "NONE",
            "ledger_separate_from_engineering": True,
        },
        "safety": {
            "formal_blinded": True,
            "selector_feedback_consumed": False,
            "outcome_feedback_feed": "NONE",
        },
        "verdict": {
            "promotion_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
        "ledger": {"chain_valid": True},
        "tracks": {
            identity.candidate_id: {
                "candidate_id": identity.candidate_id,
                "n_realizations": 1,
                "formal_maturity": False,
            }
        },
    }
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        snapshot = reconcile_workflow(
            workflow=workflow,
            identities=(identity,),
            historical_document={},
            historical_path="historical.json",
            forward_evaluations_document={},
            forward_evaluations_path="forward.json",
            forward_status_document={},
            forward_status_path="formal-shadow.json",
            risk_document={
                "generated_at_ms": 3_000,
                "alphas": {identity.candidate_id: {"status": "READY"}},
            },
            risk_path="risk.json",
            portfolio_document=_portfolio_document((identity.candidate_id,)),
            realtime_document={},
            realtime_path="legacy-paper.json",
            lifecycle_document={},
            lifecycle_path="lifecycle.json",
            release_commit=COMMIT,
            realtime_discovery_document=discovery,
            realtime_discovery_path="realtime-discovery.json",
            realtime_formal_shadow_document=formal_shadow,
            realtime_formal_shadow_path="formal-shadow.json",
            realtime_shadow_document=engineering_shadow,
            realtime_shadow_path="engineering-shadow.json",
            realtime_execution_document=engineering_execution,
            realtime_execution_path="engineering-execution.json",
        )

    row = snapshot["candidates"][0]
    gates = _gates(row)
    assert gates["HISTORICAL_OOS"]["status"] == "PASS"
    assert gates["SIGNAL_SHADOW"]["status"] == "RUNNING"
    assert gates["EXECUTION_PAPER"]["status"] == "RUNNING"
    assert row["workflow"]["stage"] == "PARALLEL_VALIDATION"
    assert row["workflow"]["eligible_for_execution_paper"] is False
    assert "FORMAL_BLINDED_SIGNAL_SHADOW_NOT_AVAILABLE" not in row["workflow"]["blockers"]
    assert "ENGINEERING_EXECUTION_PAPER_NOT_FORMAL_EVIDENCE" in row["workflow"]["blockers"]
    assert snapshot["execution_paper_eligible_ids"] == []
    assert snapshot["reconciliation"]["realtime_handoff_count"] == 1


def test_exploratory_realtime_candidate_waits_before_formal_shadow_requirement():
    discovery = {
        "candidates": [
            {
                "candidate_id": "rt_exploratory",
                "evaluation_status": "INSUFFICIENT_OBSERVATIONS",
                "gate_pass": False,
            }
        ]
    }
    assert (
        _realtime_formal_shadow_required(
            "rt_exploratory", discovery_document=discovery
        )
        is False
    )

    discovery["candidates"][0].update(
        {"evaluation_status": "EVALUATED", "gate_pass": True}
    )
    assert (
        _realtime_formal_shadow_required(
            "rt_exploratory", discovery_document=discovery
        )
        is True
    )


def test_current_gate_regression_blocks_eligibility_without_erasing_stage(tmp_path) -> None:
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        _reconcile(workflow, (identity,), shadow=shadow)
        regressed = _reconcile(workflow, (identity,), shadow=shadow, risk_ready=False)
        row = regressed["candidates"][0]["workflow"]
        assert row["stage"] == "EXECUTION_PAPER"
        assert row["eligible_for_execution_paper"] is False
        assert "RESEARCH_RISK_NOT_READY" in row["blockers"]
        assert regressed["execution_paper_eligible_ids"] == []
        assert regressed["execution_paper_ids"] == [identity.candidate_id]


def test_legacy_paper_mark_remains_quarantined(tmp_path) -> None:
    identity = _identity("crowd_contra")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        snapshot = _reconcile(workflow, (identity,), shadow=shadow)
        row = snapshot["candidates"][0]["workflow"]
        assert row["stage"] == "PARALLEL_VALIDATION"
        assert row["legacy_diagnostic"] is True
        assert row["eligible_for_execution_paper"] is False
        assert "LEGACY_DIAGNOSTIC_NOT_DEPLOYMENT_ELIGIBLE" in row["blockers"]


def test_shadow_gate_overlapping_windows_stay_running(tmp_path) -> None:
    """重叠兑现窗口 → 有效独立观察数 < 原始期数 → MATURE 更晚到来(绝不更早)。"""
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,), spacing=3)
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        snapshot = _reconcile(workflow, (identity,), shadow=shadow)
        row = snapshot["candidates"][0]
        assert _gates(row)["SIGNAL_SHADOW"]["status"] == "RUNNING"
        assert "SIGNAL_SHADOW_NOT_MATURE" in row["workflow"]["blockers"]
        assert "UNBLINDING_INSUFFICIENT_INDEPENDENT_PERIODS" in (
            row["workflow"]["blockers"]
        )
        assert "UNBLINDING_INSUFFICIENT_PERIODS" not in row["workflow"]["blockers"]
        assert snapshot["execution_paper_eligible_ids"] == []


def test_shadow_gate_single_regime_class_stays_running(tmp_path) -> None:
    """8 期全落同一 regime 类 → 覆盖不足 → RUNNING(揭盲未就绪)。"""
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(
        tmp_path,
        (identity.candidate_id,),
        funding=_quadratic_funding_document(),
    )
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        snapshot = _reconcile(workflow, (identity,), shadow=shadow)
        row = snapshot["candidates"][0]
        assert _gates(row)["SIGNAL_SHADOW"]["status"] == "RUNNING"
        assert "UNBLINDING_REGIME_COVERAGE_INSUFFICIENT" in (
            row["workflow"]["blockers"]
        )
        assert snapshot["execution_paper_eligible_ids"] == []


def test_shadow_gate_missing_regime_evidence_stays_running(tmp_path) -> None:
    """regime 数据源缺席 → 标签 None → SEALED(诚实记录,绝不猜、绝不崩)。"""
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    shadow["funding_document"] = {}
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        snapshot = _reconcile(workflow, (identity,), shadow=shadow)
        row = snapshot["candidates"][0]
        assert _gates(row)["SIGNAL_SHADOW"]["status"] == "RUNNING"
        assert "UNBLINDING_REGIME_EVIDENCE_MISSING" in row["workflow"]["blockers"]


def test_shadow_gate_invalid_realization_chain_fails_closed(tmp_path) -> None:
    """兑现哈希链被篡改 → fail-closed RuntimeError(与 registry 同款,绝不带伤继续)。"""
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    connection = sqlite3.connect(str(shadow["paper_track_path"]))
    connection.execute("DROP TRIGGER forward_realizations_no_update")
    connection.execute(
        "UPDATE forward_realizations SET realized_return = 9.9 WHERE realization_seq = 1"
    )
    connection.commit()
    connection.close()
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        with pytest.raises(RuntimeError, match="attested forward ledger"):
            _reconcile(workflow, (identity,), shadow=shadow)


# ---------------------------------------------------------------------------
# WP4 W4-2: RESEARCH_RISK READY 需要 portfolio evidence 完备(只查 PRESENCE);
# W4-3 起完备行再过 portfolio_gate_policy.v1 merit 判定(见文件尾部 W4-3 段)。
# ---------------------------------------------------------------------------


def test_risk_gate_ready_requires_portfolio_evidence(tmp_path) -> None:
    """九引擎包 READY 但 portfolio evidence artifact 缺席 → 门降 PENDING +
    PORTFOLIO_EVIDENCE_INCOMPLETE,不崩、绝不放行。"""
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        snapshot = _reconcile(workflow, (identity,), shadow=shadow, portfolio={})
        row = snapshot["candidates"][0]
        gate = _gates(row)["RESEARCH_RISK"]
        assert gate["status"] == "PENDING"
        assert "PORTFOLIO_EVIDENCE_INCOMPLETE" in row["workflow"]["blockers"]
        assert "RESEARCH_RISK_NOT_READY" in row["workflow"]["blockers"]
        assert snapshot["execution_paper_eligible_ids"] == []


def test_risk_gate_sub_evidence_missing_names_each_gap(tmp_path) -> None:
    """行存在但非容忍子项缺失 → PENDING + 逐子项 PORTFOLIO_EVIDENCE_MISSING:<sub>。"""
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    portfolio = _portfolio_document(
        (identity.candidate_id,), missing=["capacity", "crowding", "tca"]
    )
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        snapshot = _reconcile(workflow, (identity,), shadow=shadow, portfolio=portfolio)
        row = snapshot["candidates"][0]
        gate = _gates(row)["RESEARCH_RISK"]
        assert gate["status"] == "PENDING"
        blockers = row["workflow"]["blockers"]
        assert "PORTFOLIO_EVIDENCE_INCOMPLETE" in blockers
        assert "PORTFOLIO_EVIDENCE_MISSING:capacity" in blockers
        assert "PORTFOLIO_EVIDENCE_MISSING:crowding" in blockers
        # tca 是本切片显式容忍的缺失(book 级 canary 引用,非 per 候选实测)。
        assert "PORTFOLIO_EVIDENCE_MISSING:tca" not in blockers
        assert snapshot["execution_paper_eligible_ids"] == []


def test_risk_gate_tolerated_tca_missing_alone_stays_ready(tmp_path) -> None:
    """仅 tca 缺失(容忍集)→ 门仍 READY(现行行为钉死)。"""
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    portfolio = _portfolio_document((identity.candidate_id,), missing=["tca"])
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        snapshot = _reconcile(workflow, (identity,), shadow=shadow, portfolio=portfolio)
        row = snapshot["candidates"][0]
        assert _gates(row)["RESEARCH_RISK"]["status"] == "READY"
        assert "PORTFOLIO_EVIDENCE_INCOMPLETE" not in row["workflow"]["blockers"]
        assert snapshot["execution_paper_eligible_ids"] == [identity.candidate_id]


def test_portfolio_evidence_row_binds_into_gate_content_hash(tmp_path) -> None:
    """evidence 行内容变化 → RESEARCH_RISK content hash 变化(哈希绑定承重)。"""
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    complete = _portfolio_document((identity.candidate_id,))
    richer = _portfolio_document((identity.candidate_id,))
    richer["alphas"][identity.candidate_id]["marginal_contribution"] = {
        "available": True,
        "marginal_ir": 0.42,
    }
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        first = _reconcile(workflow, (identity,), shadow=shadow, portfolio=complete)
    with AlphaProductionWorkflow(tmp_path / "workflow2.db") as workflow:
        second = _reconcile(workflow, (identity,), shadow=shadow, portfolio=richer)
    hash_first = _gates(first["candidates"][0])["RESEARCH_RISK"]["content_hash"]
    hash_second = _gates(second["candidates"][0])["RESEARCH_RISK"]["content_hash"]
    # evidence 行只经 content hash 绑定(gate 不持久化 row),哈希分歧即绑定证明。
    assert hash_first != hash_second


def test_malformed_portfolio_missing_list_fails_closed(tmp_path) -> None:
    """missing 字段形状畸形 → RuntimeError fail-closed(带伤证据绝不当完备)。"""
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    portfolio = _portfolio_document((identity.candidate_id,))
    portfolio["alphas"][identity.candidate_id]["missing"] = "capacity"
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        with pytest.raises(RuntimeError, match="portfolio evidence row"):
            _reconcile(workflow, (identity,), shadow=shadow, portfolio=portfolio)


def test_risk_not_ready_ignores_portfolio_blockers(tmp_path) -> None:
    """九引擎包本身未 READY → 只报 RESEARCH_RISK_NOT_READY,不叠加 portfolio
    blocker(组合证据只在包 READY 时承重)。"""
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        snapshot = _reconcile(
            workflow, (identity,), shadow=shadow, risk_ready=False, portfolio={}
        )
        row = snapshot["candidates"][0]
        assert "RESEARCH_RISK_NOT_READY" in row["workflow"]["blockers"]
        assert "PORTFOLIO_EVIDENCE_INCOMPLETE" not in row["workflow"]["blockers"]


# ---------------------------------------------------------------------------
# WP4 W4-3: 组合门 merit 判定(portfolio_gate_policy.v1)生效。
# ---------------------------------------------------------------------------


def test_portfolio_gate_pass_keeps_ready_path(tmp_path) -> None:
    """完备且 merit PASS → RESEARCH_RISK 仍 READY(READY 仍需九引擎包 READY),
    判定块并入 gate row,政策哈希进调和摘要。"""
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        snapshot = _reconcile(workflow, (identity,), shadow=shadow)
        row = snapshot["candidates"][0]
        gate = _gates(row)["RESEARCH_RISK"]
        assert gate["status"] == "READY"
        assert snapshot["execution_paper_eligible_ids"] == [identity.candidate_id]
        assert not any(
            blocker.startswith("PORTFOLIO_GATE_")
            for blocker in row["workflow"]["blockers"]
        )
        reconciliation = snapshot["reconciliation"]
        assert (
            reconciliation["portfolio_gate_policy_version"]
            == "portfolio_gate_policy.v1"
        )
        assert (
            reconciliation["portfolio_gate_policy_hash"]
            == PortfolioGatePolicy().content_hash()
        )
        assert reconciliation["portfolio_reference_aum"] == 1_000_000.0


def test_portfolio_gate_merit_fail_blocks_with_gate_codes(tmp_path) -> None:
    """完备而未过阈 → 门 EvidenceStatus.FAIL + 逐失败码 PORTFOLIO_GATE_<CODE>
    (+ RESEARCH_RISK_NOT_READY),INCOMPLETE blocker 不叠加。"""
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    portfolio = _portfolio_document((identity.candidate_id,))
    row = portfolio["alphas"][identity.candidate_id]
    row["marginal_contribution"]["incremental_sharpe"] = -0.5
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        snapshot = _reconcile(workflow, (identity,), shadow=shadow, portfolio=portfolio)
        candidate = snapshot["candidates"][0]
        gate = _gates(candidate)["RESEARCH_RISK"]
        assert gate["status"] == "FAIL"
        blockers = candidate["workflow"]["blockers"]
        assert "PORTFOLIO_GATE_INCREMENTAL_SHARPE_BELOW_MIN" in blockers
        assert "RESEARCH_RISK_NOT_READY" in blockers
        assert "PORTFOLIO_EVIDENCE_INCOMPLETE" not in blockers
        assert snapshot["execution_paper_eligible_ids"] == []


def test_portfolio_gate_evaluation_binds_into_gate_content_hash(tmp_path) -> None:
    """merit 判定(PASS vs FAIL 行)→ RESEARCH_RISK content hash 分歧
    (portfolio_gate 判定块进哈希,政策内容寻址承重)。"""
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    passing = _portfolio_document((identity.candidate_id,))
    failing = _portfolio_document((identity.candidate_id,))
    failing["alphas"][identity.candidate_id]["crowding"]["blocked_share"] = 0.9
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        first = _reconcile(workflow, (identity,), shadow=shadow, portfolio=passing)
    with AlphaProductionWorkflow(tmp_path / "workflow2.db") as workflow:
        second = _reconcile(workflow, (identity,), shadow=shadow, portfolio=failing)
    assert _gates(first["candidates"][0])["RESEARCH_RISK"]["status"] == "READY"
    assert _gates(second["candidates"][0])["RESEARCH_RISK"]["status"] == "FAIL"
    assert (
        _gates(first["candidates"][0])["RESEARCH_RISK"]["content_hash"]
        != _gates(second["candidates"][0])["RESEARCH_RISK"]["content_hash"]
    )


def test_portfolio_gate_incomplete_semantics_unchanged(tmp_path) -> None:
    """缺非容忍子项 → 仍 PENDING + PORTFOLIO_EVIDENCE_* blocker,
    绝不产生 PORTFOLIO_GATE_* merit 失败码(INCOMPLETE 不是 merit FAIL)。"""
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    portfolio = _portfolio_document((identity.candidate_id,), missing=["crowding"])
    row = portfolio["alphas"][identity.candidate_id]
    row["crowding"] = {"available": False, "reason": "INPUT_MISSING"}
    # 负 incremental_sharpe 在 INCOMPLETE 下不得升级为 merit FAIL
    row["marginal_contribution"]["incremental_sharpe"] = -0.5
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        snapshot = _reconcile(workflow, (identity,), shadow=shadow, portfolio=portfolio)
        candidate = snapshot["candidates"][0]
        gate = _gates(candidate)["RESEARCH_RISK"]
        assert gate["status"] == "PENDING"
        blockers = candidate["workflow"]["blockers"]
        assert "PORTFOLIO_EVIDENCE_INCOMPLETE" in blockers
        assert "PORTFOLIO_EVIDENCE_MISSING:crowding" in blockers
        assert not any(b.startswith("PORTFOLIO_GATE_") for b in blockers)


def test_portfolio_gate_policy_override_plumbing(tmp_path) -> None:
    """政策可注入覆盖(与 unblinding 政策同法):更严政策 → FAIL,政策版本与
    哈希进调和摘要;reference_aum 覆盖驱动容量比判定。"""
    identity = _identity("factory_alpha")
    shadow = _shadow_inputs(tmp_path, (identity.candidate_id,))
    strict = PortfolioGatePolicy(
        min_marginal_ir=99.0,
        policy_version="portfolio_gate_policy.test-strict",
    )
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        snapshot = _reconcile(
            workflow, (identity,), shadow=shadow, portfolio_policy=strict
        )
        candidate = snapshot["candidates"][0]
        gate = _gates(candidate)["RESEARCH_RISK"]
        assert gate["status"] == "FAIL"
        assert (
            "PORTFOLIO_GATE_MARGINAL_IR_BELOW_MIN"
            in candidate["workflow"]["blockers"]
        )
        reconciliation = snapshot["reconciliation"]
        assert (
            reconciliation["portfolio_gate_policy_version"]
            == "portfolio_gate_policy.test-strict"
        )
        assert (
            reconciliation["portfolio_gate_policy_hash"] == strict.content_hash()
        )

    # reference_aum 覆盖:capacity.low 7e6 / 3e6 = 2.33 < 3.0 → 容量失败
    with AlphaProductionWorkflow(tmp_path / "workflow2.db") as workflow:
        snapshot = _reconcile(
            workflow, (identity,), shadow=shadow, portfolio_reference_aum=3_000_000.0
        )
        candidate = snapshot["candidates"][0]
        assert _gates(candidate)["RESEARCH_RISK"]["status"] == "FAIL"
        assert (
            "PORTFOLIO_GATE_CAPACITY_RATIO_BELOW_MIN"
            in candidate["workflow"]["blockers"]
        )
        assert snapshot["reconciliation"]["portfolio_reference_aum"] == 3_000_000.0
