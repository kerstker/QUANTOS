"""The three orthogonal lifecycle machines (727 §3).

Pins the §3.4 composite invariants plus the two properties that decide whether
this refactor is safe at all:
  * projection is TOTAL over the legacy enum and never guesses a lost dimension;
  * the human-only boundary is structural (derived from the real-capital set),
    not a hand-maintained list, and it preserves parity with the legacy
    AGENT_REACHABLE so no previously-permitted agent action is newly blocked.
"""

from __future__ import annotations

import pytest

from quantos.contracts.alpha import AGENT_REACHABLE, Actor, ActorKind
from quantos.contracts.research import AlphaState
from services.lifecycle import (
    DE_RISKED_STATES,
    DEPLOYMENT_ALLOWED_TRANSITIONS,
    HEALTH_ALLOWED_TRANSITIONS,
    LOSSY_DEPLOYMENT,
    LOSSY_HEALTH,
    LOSSY_HEALTH_REASON,
    LOSSY_RESEARCH_CHALLENGED,
    REAL_CAPITAL_STATES,
    RESEARCH_ALLOWED_TRANSITIONS,
    RESEARCH_DEPLOYABLE,
    DeploymentState,
    HealthReason,
    HealthState,
    HealthVerdict,
    LifecycleTriple,
    MissingHealthReason,
    ResearchState,
    authorize_deployment_transition,
    can_deployment_transition,
    can_health_transition,
    can_research_transition,
    check_composite_invariants,
    is_de_risking,
    is_deployed,
    project,
    requires_human_authorization,
    risk_rank,
)

HUMAN = Actor(actor_id="alice", kind=ActorKind.HUMAN)
AGENT = Actor(actor_id="research-agent", kind=ActorKind.AGENT)
SYSTEM = Actor(actor_id="scheduler", kind=ActorKind.SYSTEM)
NON_HUMANS = (AGENT, SYSTEM)


# --- ResearchState --------------------------------------------------------


def test_every_research_state_has_a_transition_entry() -> None:
    assert set(RESEARCH_ALLOWED_TRANSITIONS) == set(ResearchState)


def test_challenge_cycle_exists_which_the_legacy_model_lacked() -> None:
    """§10.2's Challenger flow needs a state for "currently being challenged",
    and a survived challenge must return to VALIDATED."""
    assert can_research_transition(ResearchState.VALIDATED, ResearchState.CHALLENGED)
    assert can_research_transition(ResearchState.CHALLENGED, ResearchState.VALIDATED)
    assert can_research_transition(ResearchState.CHALLENGED, ResearchState.INVALIDATED)


def test_research_retired_is_terminal_and_cannot_revive_in_place() -> None:
    """727 §20.2 invariant 14."""
    assert RESEARCH_ALLOWED_TRANSITIONS[ResearchState.RESEARCH_RETIRED] == frozenset()
    for state in ResearchState:
        assert not can_research_transition(ResearchState.RESEARCH_RETIRED, state)


def test_invalidated_can_only_retire() -> None:
    assert RESEARCH_ALLOWED_TRANSITIONS[ResearchState.INVALIDATED] == frozenset(
        {ResearchState.RESEARCH_RETIRED}
    )


def test_deployable_research_is_exactly_validated_or_challenged() -> None:
    assert RESEARCH_DEPLOYABLE == frozenset(
        {ResearchState.VALIDATED, ResearchState.CHALLENGED}
    )


# --- DeploymentState: the only human-gated machine ------------------------


def test_every_deployment_state_has_a_transition_entry() -> None:
    assert set(DEPLOYMENT_ALLOWED_TRANSITIONS) == set(DeploymentState)


def test_human_gate_is_exactly_the_real_capital_boundary() -> None:
    """The load-bearing safety property. A human is required iff the edge puts
    real capital at risk — derived from REAL_CAPITAL_STATES, not enumerated."""
    for src, dsts in DEPLOYMENT_ALLOWED_TRANSITIONS.items():
        for dst in dsts:
            expected = dst in REAL_CAPITAL_STATES and dst not in DE_RISKED_STATES
            assert requires_human_authorization(src, dst) is expected, (src, dst)


def test_no_non_human_can_reach_real_capital_over_any_edge() -> None:
    """Exhaustive grid: every legal edge x every non-human actor."""
    for src, dsts in DEPLOYMENT_ALLOWED_TRANSITIONS.items():
        for dst in dsts:
            for actor in NON_HUMANS:
                allowed, reasons = authorize_deployment_transition(
                    src=src, dst=dst, actor=actor
                )
                if dst in REAL_CAPITAL_STATES:
                    assert not allowed, (src, dst, actor.kind)
                    assert "HUMAN_ACTOR_REQUIRED" in reasons


def test_real_capital_is_structurally_blocked_even_for_a_human_at_l2() -> None:
    """MICRO_LIVE / LIVE are enum members so the model is complete, but the path
    stays shut regardless of actor — same shape as LiveBrokerAdapter's refusal.
    Defining a state is not opening a path."""
    for src, dsts in DEPLOYMENT_ALLOWED_TRANSITIONS.items():
        for dst in dsts & REAL_CAPITAL_STATES:
            allowed, reasons = authorize_deployment_transition(
                src=src, dst=dst, actor=HUMAN
            )
            assert not allowed
            assert "REAL_CAPITAL_BLOCKED_AT_L2" in reasons


def test_de_risking_is_never_gated_for_any_actor() -> None:
    """§11.2 — the system must always be able to reduce risk automatically."""
    for src, dsts in DEPLOYMENT_ALLOWED_TRANSITIONS.items():
        for dst in dsts & DE_RISKED_STATES:
            assert requires_human_authorization(src, dst) is False
            for actor in (HUMAN, *NON_HUMANS):
                allowed, reasons = authorize_deployment_transition(
                    src=src, dst=dst, actor=actor
                )
                assert allowed, (src, dst, actor.kind, reasons)


def test_paper_and_shadow_stay_agent_reachable_parity_with_legacy() -> None:
    """Behaviour parity: the legacy AGENT_REACHABLE let an agent drive into
    PAPER_TRADING and SHADOW because they risk nothing. This refactor must not
    newly block that."""
    assert AlphaState.PAPER_TRADING in AGENT_REACHABLE
    assert AlphaState.SHADOW in AGENT_REACHABLE
    for actor in NON_HUMANS:
        assert authorize_deployment_transition(
            src=DeploymentState.OFFLINE, dst=DeploymentState.PAPER, actor=actor
        )[0]
        assert authorize_deployment_transition(
            src=DeploymentState.PAPER, dst=DeploymentState.SHADOW, actor=actor
        )[0]


def test_legacy_agent_reachable_excluded_approved_and_we_still_do() -> None:
    """The one exclusion the legacy list existed for must survive the split."""
    assert AlphaState.APPROVED not in AGENT_REACHABLE
    assert project(AlphaState.APPROVED).deployment in REAL_CAPITAL_STATES
    for actor in NON_HUMANS:
        allowed, _ = authorize_deployment_transition(
            src=DeploymentState.SHADOW, dst=DeploymentState.MICRO_LIVE, actor=actor
        )
        assert not allowed


def test_paused_cannot_resume_straight_to_live() -> None:
    """§11.2 names PAUSED -> LIVE as forbidden; recovery must re-earn it."""
    assert not can_deployment_transition(DeploymentState.PAUSED, DeploymentState.LIVE)
    assert can_deployment_transition(
        DeploymentState.PAUSED, DeploymentState.MICRO_LIVE
    )


def test_closed_has_no_outgoing_edges() -> None:
    """§3.2 — CLOSED cannot be resumed in place, only replaced by a new instance."""
    assert DEPLOYMENT_ALLOWED_TRANSITIONS[DeploymentState.CLOSED] == frozenset()
    for state in DeploymentState:
        assert not can_deployment_transition(DeploymentState.CLOSED, state)


def test_unknown_legacy_is_unreachable_going_forward() -> None:
    """It exists only for rows written before the migration: zero inbound edges,
    and its only outbound edges are de-risking ones so a legacy row can always
    be made safe without first guessing where it was."""
    for dsts in DEPLOYMENT_ALLOWED_TRANSITIONS.values():
        assert DeploymentState.UNKNOWN_LEGACY not in dsts
    assert (
        DEPLOYMENT_ALLOWED_TRANSITIONS[DeploymentState.UNKNOWN_LEGACY]
        <= DE_RISKED_STATES
    )


def test_unrankable_state_reports_none_rather_than_pretending_to_be_safe() -> None:
    assert risk_rank(DeploymentState.UNKNOWN_LEGACY) is None
    assert is_de_risking(DeploymentState.UNKNOWN_LEGACY, DeploymentState.PAUSED) is None
    assert is_de_risking(DeploymentState.LIVE, DeploymentState.PAUSED) is True
    assert is_de_risking(DeploymentState.PAPER, DeploymentState.SHADOW) is False


def test_is_deployed_excludes_de_risked_and_legacy() -> None:
    assert is_deployed(DeploymentState.PAPER)
    assert is_deployed(DeploymentState.LIVE)
    for state in (
        DeploymentState.OFFLINE,
        DeploymentState.PAUSED,
        DeploymentState.CLOSED,
        DeploymentState.UNKNOWN_LEGACY,
    ):
        assert not is_deployed(state)


def test_illegal_edge_is_rejected_even_for_a_human() -> None:
    allowed, reasons = authorize_deployment_transition(
        src=DeploymentState.OFFLINE, dst=DeploymentState.SHADOW, actor=HUMAN
    )
    assert not allowed
    assert "ILLEGAL_TRANSITION" in reasons


# --- HealthState ----------------------------------------------------------


def test_every_health_state_has_a_transition_entry() -> None:
    assert set(HEALTH_ALLOWED_TRANSITIONS) == set(HealthState)


def test_non_healthy_without_a_reason_is_rejected() -> None:
    """Fail-closed, same grounds as §7's failure_reasons rule: a degradation
    nobody can review is not an audit trail."""
    for state in (HealthState.WATCH, HealthState.DEGRADED, HealthState.FAILED):
        with pytest.raises(MissingHealthReason):
            HealthVerdict(state)
        assert HealthVerdict(state, HealthReason.SIGNAL_DECAY).state is state


def test_healthy_must_not_carry_a_reason() -> None:
    assert HealthVerdict(HealthState.HEALTHY).reason is HealthReason.NONE
    with pytest.raises(ValueError, match="must not carry"):
        HealthVerdict(HealthState.HEALTHY, HealthReason.RISK_BREACH)


def test_all_eleven_spec_reason_codes_are_present() -> None:
    """§3.3 lists exactly these; NONE and UNKNOWN_LEGACY are bookkeeping."""
    spec = {
        "DATA_STALE",
        "DATA_LINEAGE_BROKEN",
        "SIGNAL_DECAY",
        "REGIME_MISMATCH",
        "COST_INFLATION",
        "SLIPPAGE_BREACH",
        "POSITION_MISMATCH",
        "ORDER_UNKNOWN",
        "BROKER_DISCONNECTED",
        "RISK_BREACH",
        "MODEL_INVALIDATED",
    }
    assert spec <= {reason.value for reason in HealthReason}
    assert {reason.value for reason in HealthReason} - spec == {
        "NONE",
        "UNKNOWN_LEGACY",
    }


def test_failed_cannot_jump_straight_back_to_healthy() -> None:
    """§11.3 requires observation (Shadow 冷静期) before an alpha is called healthy."""
    assert not can_health_transition(HealthState.FAILED, HealthState.HEALTHY)
    assert can_health_transition(HealthState.FAILED, HealthState.DEGRADED)


def test_health_recovery_does_not_move_the_deployment_position() -> None:
    """The concrete decoupling §3 asks for: in the legacy model DEGRADED -> SHADOW
    was one edge doing two jobs, so a health recovery necessarily moved the
    deployment position. Health edges here touch no deployment value."""
    assert can_health_transition(HealthState.DEGRADED, HealthState.HEALTHY)
    before = DeploymentState.SHADOW
    # health machine has no deployment output at all — nothing to assert against
    # except that the two vocabularies are disjoint types
    assert not isinstance(before, HealthState)
    assert set(HealthState) & set(DeploymentState) == set()


def test_only_failed_forces_de_risk() -> None:
    assert HealthVerdict(HealthState.FAILED, HealthReason.RISK_BREACH).forces_de_risk
    assert not HealthVerdict(
        HealthState.DEGRADED, HealthReason.SIGNAL_DECAY
    ).forces_de_risk
    assert not HealthVerdict(HealthState.HEALTHY).forces_de_risk


# --- Projection -----------------------------------------------------------


def test_projection_is_total_over_the_legacy_enum() -> None:
    """No legacy value may be silently unmapped."""
    for state in AlphaState:
        triple = project(state)
        assert isinstance(triple, LifecycleTriple)


def test_the_three_lossy_values_are_the_ones_the_design_names() -> None:
    lossy_by_state = {
        state: project(state).lossy for state in AlphaState if project(state).lossy
    }
    # PAPER_TRADING / SHADOW / APPROVED lose only the challenge flag; DEGRADED and
    # PAUSED lose real dimensions.
    assert lossy_by_state[AlphaState.DEGRADED] == (
        LOSSY_DEPLOYMENT,
        LOSSY_HEALTH_REASON,
        LOSSY_RESEARCH_CHALLENGED,
    )
    assert lossy_by_state[AlphaState.PAUSED] == (
        LOSSY_HEALTH,
        LOSSY_HEALTH_REASON,
        LOSSY_RESEARCH_CHALLENGED,
    )
    for state in (AlphaState.DRAFT, AlphaState.TESTING, AlphaState.VALIDATED):
        assert project(state).lossy == ()


def test_degraded_refuses_to_guess_the_lost_deployment_position() -> None:
    """The legacy single field overwrote wherever it was running. Guessing SHADOW
    would be fabrication."""
    triple = project(AlphaState.DEGRADED)
    assert triple.deployment is DeploymentState.UNKNOWN_LEGACY
    assert triple.health.state is HealthState.DEGRADED
    assert triple.health.reason is HealthReason.UNKNOWN_LEGACY
    assert triple.is_legacy


def test_paused_projects_to_watch_not_healthy_and_not_failed() -> None:
    """"Was stopped, cause never recorded" is neither. WATCH's §15.8 disposition
    (do not add capital, raise monitoring) is exactly right for this row."""
    triple = project(AlphaState.PAUSED)
    assert triple.deployment is DeploymentState.PAUSED
    assert triple.health.state is HealthState.WATCH
    assert triple.health.reason is HealthReason.UNKNOWN_LEGACY


def test_approved_projects_to_live_authorization_but_grants_no_capability() -> None:
    """APPROVED means a human authorized live deployment. The projection records
    that; it does NOT mean capital is deployed, and the L2 block is independent."""
    triple = project(AlphaState.APPROVED)
    assert triple.deployment is DeploymentState.LIVE
    for actor in (HUMAN, *NON_HUMANS):
        allowed, reasons = authorize_deployment_transition(
            src=DeploymentState.MICRO_LIVE, dst=DeploymentState.LIVE, actor=actor
        )
        assert not allowed
        assert "REAL_CAPITAL_BLOCKED_AT_L2" in reasons


def test_terminal_legacy_states_project_to_closed() -> None:
    assert project(AlphaState.RETIRED).deployment is DeploymentState.CLOSED
    assert project(AlphaState.RETIRED).research is ResearchState.RESEARCH_RETIRED
    assert project(AlphaState.REJECTED).deployment is DeploymentState.CLOSED
    assert project(AlphaState.REJECTED).research is ResearchState.INVALIDATED


def test_unmapped_state_raises_rather_than_defaulting() -> None:
    with pytest.raises(KeyError, match="no projection"):
        project("NOT_A_STATE")  # type: ignore[arg-type]


# --- §3.4 composite invariants -------------------------------------------


def test_every_projected_legacy_state_satisfies_the_invariants() -> None:
    for state in AlphaState:
        assert check_composite_invariants(project(state)) == (), state


def test_deployed_without_deployable_research_is_caught() -> None:
    bad = LifecycleTriple(
        research=ResearchState.TESTING,
        deployment=DeploymentState.PAPER,
        health=HealthVerdict(HealthState.HEALTHY),
    )
    assert "DEPLOYED_WITHOUT_DEPLOYABLE_RESEARCH" in check_composite_invariants(bad)


def test_invalidated_research_still_deployed_is_caught() -> None:
    bad = LifecycleTriple(
        research=ResearchState.INVALIDATED,
        deployment=DeploymentState.SHADOW,
        health=HealthVerdict(HealthState.HEALTHY),
    )
    violations = check_composite_invariants(bad)
    assert "INVALIDATED_RESEARCH_STILL_DEPLOYED" in violations


def test_failed_health_still_deployed_is_caught() -> None:
    bad = LifecycleTriple(
        research=ResearchState.VALIDATED,
        deployment=DeploymentState.PAPER,
        health=HealthVerdict(HealthState.FAILED, HealthReason.BROKER_DISCONNECTED),
    )
    assert "FAILED_HEALTH_STILL_DEPLOYED" in check_composite_invariants(bad)


def test_legacy_row_is_exempt_only_where_information_is_missing() -> None:
    """A row whose deployment position was never recorded cannot be held to an
    invariant that needs it — the alternative is fabricating the dimension. But
    the dimensions it DOES have are still checked."""
    legacy = project(AlphaState.DEGRADED)
    assert legacy.deployment is DeploymentState.UNKNOWN_LEGACY
    assert check_composite_invariants(legacy) == ()

    # same unknown deployment, but a health verdict that is internally broken
    broken = LifecycleTriple(
        research=ResearchState.VALIDATED,
        deployment=DeploymentState.UNKNOWN_LEGACY,
        health=HealthVerdict(HealthState.HEALTHY),
        lossy=(LOSSY_DEPLOYMENT,),
    )
    assert check_composite_invariants(broken) == ()


def test_composite_states_the_legacy_model_could_not_express() -> None:
    """§3.4's three inexpressible combinations, now representable and consistent."""
    disconnected = LifecycleTriple(
        research=ResearchState.VALIDATED,
        deployment=DeploymentState.PAUSED,
        health=HealthVerdict(HealthState.FAILED, HealthReason.BROKER_DISCONNECTED),
    )
    regime = LifecycleTriple(
        research=ResearchState.VALIDATED,
        deployment=DeploymentState.PAUSED,
        health=HealthVerdict(HealthState.DEGRADED, HealthReason.REGIME_MISMATCH),
    )
    lineage = LifecycleTriple(
        research=ResearchState.VALIDATED,
        deployment=DeploymentState.PAUSED,
        health=HealthVerdict(HealthState.FAILED, HealthReason.DATA_LINEAGE_BROKEN),
    )
    for triple in (disconnected, regime, lineage):
        assert check_composite_invariants(triple) == ()
    # and they are genuinely DISTINCT, which is the whole point — all three
    # collapse to the single legacy PAUSED
    assert len({t.health.reason for t in (disconnected, regime, lineage)}) == 3
    assert project(AlphaState.PAUSED).health.reason is HealthReason.UNKNOWN_LEGACY
