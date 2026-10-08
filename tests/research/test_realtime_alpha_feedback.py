from __future__ import annotations

from dataclasses import replace

import pytest

from services.research.realtime_alpha_discovery import (
    REALTIME_FEEDBACK_SIGN_FLIP_SIGNAL_VERSION,
    default_realtime_hypotheses,
)
from services.research.realtime_alpha_feedback import (
    RealtimeAlphaFeedbackError,
    build_feedback_plan,
)


def _run(candidate_id: str, value: float, as_of_ms: int) -> dict:
    return {
        "candidate_id": candidate_id,
        "status": "ENGINEERING_SHADOW_REALIZED",
        "metrics": {
            "candidate_id": candidate_id,
            "net_return": value,
            "as_of_ms": as_of_ms,
            "paper_only": True,
            "promotion_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
    }


def test_positive_uncertain_family_gets_bounded_deterministic_mutations() -> None:
    base = default_realtime_hypotheses()
    parent = "rt_liquidation_exhaustion_5m_v1"
    runs = {
        parent: [
            _run(parent, value, 1_000 + index)
            for index, value in enumerate(
                (0.0010, -0.0002, 0.0008, 0.0001, 0.0009, -0.0001, 0.0007, 0.0002)
            )
        ]
    }

    first = build_feedback_plan(base, runs, existing_candidate_ids=(), max_new=2)
    second = build_feedback_plan(base, runs, existing_candidate_ids=(), max_new=2)

    assert first == second
    assert first.status == "ADAPTIVE_EXPLORATION_READY"
    assert len(first.proposals) == 2
    assert {item.parent_candidate_id for item in first.proposals} == {parent}
    assert {item.spec.horizon_ms for item in first.proposals} == {150_000, 600_000}
    assert {item.feedback_cutoff_ms for item in first.proposals} == {1_007}
    assert all(item.fresh_oos_required for item in first.proposals)
    assert all(item.spec.candidate_id.endswith("_fb1") for item in first.proposals)
    assert first.proposal_blockers == ()
    assert first.candidate_budget_remaining == 0


def test_slow_state_adaptive_children_preserve_parent_lookback() -> None:
    base = default_realtime_hypotheses()
    parent_spec = next(
        item for item in base if item.mechanism == "slow_cross_sectional_reversal"
    )
    runs = {
        parent_spec.candidate_id: [
            _run(parent_spec.candidate_id, 0.001, 1_500 + index)
            for index in range(8)
        ]
    }

    plan = build_feedback_plan(base, runs, existing_candidate_ids=(), max_new=2)

    assert plan.proposals
    assert all(
        item.spec.lookback_ms == parent_spec.lookback_ms
        for item in plan.proposals
    )


def test_clearly_refuted_family_is_deprioritized_without_new_trials() -> None:
    base = default_realtime_hypotheses()
    parent = "rt_flow_continuation_2m_v1"
    runs = {
        parent: [
            _run(parent, -0.01, 2_000 + index)
            for index in range(12)
        ]
    }

    plan = build_feedback_plan(base, runs, existing_candidate_ids=(), max_new=4)

    family = next(item for item in plan.families if item.family_id == "order_flow")
    assert family.action == "DEPRIORITIZE"
    assert plan.proposals == ()


def test_existing_adaptive_identity_is_not_proposed_twice() -> None:
    base = default_realtime_hypotheses()
    parent = "rt_liquidation_exhaustion_5m_v1"
    runs = {
        parent: [_run(parent, 0.001, 3_000 + index) for index in range(8)]
    }
    initial = build_feedback_plan(base, runs, existing_candidate_ids=(), max_new=2)
    existing = {initial.proposals[0].spec.candidate_id}

    replay = build_feedback_plan(
        base,
        runs,
        existing_candidate_ids=existing,
        max_new=2,
    )

    assert existing.isdisjoint(item.spec.candidate_id for item in replay.proposals)
    assert len(replay.proposals) == 1
    assert "CANDIDATE_ID_ALREADY_REGISTERED" in replay.proposal_blockers


def test_all_registered_horizons_open_a_structural_direction_challenger() -> None:
    base = default_realtime_hypotheses()
    parent = "rt_basis_convergence_1h_v1"
    parent_spec = next(item for item in base if item.candidate_id == parent)
    registered_horizon_children = (
        replace(
            parent_spec,
            candidate_id="rt_basis_convergence_30m_fb1",
            horizon_ms=1_800_000,
        ),
        replace(
            parent_spec,
            candidate_id="rt_basis_convergence_2h_fb1",
            horizon_ms=7_200_000,
        ),
    )
    existing = tuple(item.candidate_id for item in base) + tuple(
        item.candidate_id for item in registered_horizon_children
    )
    runs = {parent: [_run(parent, 0.001, 8_000 + index) for index in range(8)]}

    plan = build_feedback_plan(
        (*base, *registered_horizon_children),
        runs,
        existing_candidate_ids=existing,
        proposal_parent_ids=tuple(item.candidate_id for item in base),
        max_new=1,
    )

    assert len(plan.proposals) == 1
    proposal = plan.proposals[0]
    assert proposal.parent_candidate_id == parent
    assert proposal.mutation == "SIGN_FLIP"
    assert proposal.spec.candidate_id == "rt_basis_convergence_1h_signflip_fb1"
    assert proposal.spec.horizon_ms == 3_600_000
    assert proposal.spec.signal_version == REALTIME_FEEDBACK_SIGN_FLIP_SIGNAL_VERSION
    assert plan.proposal_blockers == ()


def test_adaptive_realizations_are_consumed_without_child_of_child_generation() -> None:
    base = default_realtime_hypotheses()
    parent = "rt_liquidation_exhaustion_5m_v1"
    initial = build_feedback_plan(
        base,
        {parent: [_run(parent, 0.001, 6_000 + index) for index in range(8)]},
        existing_candidate_ids=(),
        max_new=2,
    )
    child = initial.proposals[0].spec

    plan = build_feedback_plan(
        (*base, child),
        {child.candidate_id: [_run(child.candidate_id, 0.001, 7_000 + index) for index in range(8)]},
        existing_candidate_ids=(child.candidate_id,),
        proposal_parent_ids=tuple(item.candidate_id for item in base),
        max_new=2,
    )

    child_feedback = next(
        item for item in plan.candidates if item.candidate_id == child.candidate_id
    )
    assert child_feedback.n_realizations == 8
    assert plan.consumed_realizations == 8
    assert plan.proposals == ()
    assert "POSITIVE_UCB_HAS_NO_ROOT_PARENT" in plan.proposal_blockers


def test_tampered_or_authority_widened_paper_result_fails_closed() -> None:
    base = default_realtime_hypotheses()
    parent = "rt_liquidation_exhaustion_5m_v1"
    unsafe = _run(parent, 0.001, 4_000)
    unsafe["metrics"]["promotion_eligible"] = True

    with pytest.raises(RealtimeAlphaFeedbackError, match="safety boundary"):
        build_feedback_plan(
            base,
            {parent: [unsafe] * 8},
            existing_candidate_ids=(),
            max_new=2,
        )


def test_feedback_never_consumes_unknown_candidate_results() -> None:
    base = default_realtime_hypotheses()
    run = _run("unknown", 0.001, 5_000)

    with pytest.raises(RealtimeAlphaFeedbackError, match="unknown candidate"):
        build_feedback_plan(
            base,
            {"unknown": [run] * 8},
            existing_candidate_ids=(),
            max_new=2,
        )
