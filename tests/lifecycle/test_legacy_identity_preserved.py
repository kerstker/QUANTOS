"""Acceptance core for the three-machine split (design doc §7 items 5 and 6).

The split is only safe if it voids nothing that already exists. Two things could
have been voided by a rename, and these tests prove they are not:

  * historical AlphaTransition chains still recompute (values are inside
    evidence_hash on a prev_hash chain);
  * Deployment Grant subjects still parse and verify (they are bound to the
    literal string "promotion:{alpha_id}:APPROVED").

If a future change renames an AlphaState member, these fail first and loudest.
"""

from __future__ import annotations

import pytest

from quantos.contracts.alpha import (
    ALPHA_ALLOWED_TRANSITIONS,
    AlphaTransition,
    Actor,
    ActorKind,
)
from quantos.contracts.hashing import stable_hash
from quantos.contracts.research import AlphaState
from services.alpha.guards import parse_promotion_subject, promotion_subject
from services.lifecycle import project

HUMAN = Actor(actor_id="alice", kind=ActorKind.HUMAN)


def _evidence_hash(alpha_id: str, src: AlphaState, dst: AlphaState) -> str:
    """Mirrors AlphaLifecycleManager.advance()'s evidence_hash construction."""
    return stable_hash(
        {
            "alpha_id": alpha_id,
            "src": src.value,
            "dst": dst.value,
            "eval": "",
            "approval": "",
        }
    )


def test_legacy_state_values_are_unchanged() -> None:
    """The exact strings that are baked into evidence hashes and grant subjects.

    Pinned as literals ON PURPOSE: this is the tripwire. A rename here silently
    invalidates historical chains and stored grants, so it must not be possible
    to do it without a test failing.
    """
    assert {state.value for state in AlphaState} == {
        "DRAFT",
        "TESTING",
        "VALIDATED",
        "PAPER_TRADING",
        "SHADOW",
        "APPROVED",
        "DEGRADED",
        "PAUSED",
        "RETIRED",
        "REJECTED",
    }


def test_historical_transition_chain_still_recomputes() -> None:
    """Design doc §7 item 5. Builds a chain over legacy values the way the
    orchestrator does, then verifies every link recomputes after the three
    machines exist."""
    chain: list[AlphaTransition] = []
    path = [
        (AlphaState.DRAFT, AlphaState.TESTING),
        (AlphaState.TESTING, AlphaState.VALIDATED),
        (AlphaState.VALIDATED, AlphaState.PAPER_TRADING),
        (AlphaState.PAPER_TRADING, AlphaState.SHADOW),
        (AlphaState.SHADOW, AlphaState.PAUSED),
    ]
    for index, (src, dst) in enumerate(path, start=1):
        assert dst in ALPHA_ALLOWED_TRANSITIONS[src], (src, dst)
        chain.append(
            AlphaTransition(
                transition_id=f"t{index}",
                alpha_id="a1",
                src_state=src,
                dst_state=dst,
                actor=HUMAN,
                evidence_hash=_evidence_hash("a1", src, dst),
                timestamp=index,
                prev_hash=chain[-1].entry_hash if chain else "",
            )
        )

    previous = ""
    for transition in chain:
        assert transition.prev_hash == previous
        assert transition.evidence_hash == _evidence_hash(
            "a1", transition.src_state, transition.dst_state
        )
        previous = transition.entry_hash
    assert previous


def test_stored_grant_subject_still_round_trips() -> None:
    """Design doc §7 item 6. The subject three real humans signed must still parse."""
    subject = promotion_subject("a1", AlphaState.APPROVED)
    assert subject == "alpha-promotion:a1:APPROVED"
    alpha_id, dst = parse_promotion_subject(subject)
    assert alpha_id == "a1"
    assert dst is AlphaState.APPROVED


def test_grant_subject_round_trips_for_every_legacy_state() -> None:
    for state in AlphaState:
        alpha_id, parsed = parse_promotion_subject(
            promotion_subject("alpha:with:colons", state)
        )
        assert alpha_id == "alpha:with:colons"
        assert parsed is state


def test_projection_does_not_mutate_or_shadow_the_legacy_enum() -> None:
    """The machines are additive. Projecting must leave the legacy vocabulary and
    its transition table untouched."""
    before_states = {state.value for state in AlphaState}
    before_table = {
        src: frozenset(dsts) for src, dsts in ALPHA_ALLOWED_TRANSITIONS.items()
    }
    for state in AlphaState:
        project(state)
    assert {state.value for state in AlphaState} == before_states
    assert {
        src: frozenset(dsts) for src, dsts in ALPHA_ALLOWED_TRANSITIONS.items()
    } == before_table


# Legacy edges the new deployment machine DELIBERATELY refuses. All three are the
# same defect: the legacy model lets a human authorize live directly from paper,
# shadow, or paused, with no MICRO_LIVE stage in between. §12.4 requires micro-live
# evidence (real fills, reconciliation, fault recovery, Reality Gap) before LIVE,
# and §11.2 forbids PAUSED -> LIVE outright. Harmless at L2 because the five flags
# block execution regardless, but it means the state machine itself never modelled
# §12's staged rollout. Adopting the new machine (C3) therefore TIGHTENS these.
_INTENTIONAL_DIVERGENCES: frozenset[tuple[str, str]] = frozenset(
    {
        ("PAPER_TRADING", "APPROVED"),
        ("SHADOW", "APPROVED"),
        ("PAUSED", "APPROVED"),
    }
)


def test_every_legacy_edge_projects_to_a_legal_target_or_a_named_divergence() -> None:
    """Every historical edge must either be representable in the new machine or be
    a DECLARED tightening. An edge whose endpoints project into UNKNOWN_LEGACY is
    exempt — that is precisely the information the legacy model destroyed."""
    from services.lifecycle import DeploymentState, can_deployment_transition

    observed_divergences: set[tuple[str, str]] = set()
    for src, dsts in ALPHA_ALLOWED_TRANSITIONS.items():
        for dst in dsts:
            a, b = project(src), project(dst)
            if DeploymentState.UNKNOWN_LEGACY in (a.deployment, b.deployment):
                continue
            if a.deployment == b.deployment:
                continue  # a pure research/health move; deployment did not change
            if not can_deployment_transition(a.deployment, b.deployment):
                observed_divergences.add((src.value, dst.value))

    # Exact equality, both directions: an undeclared new divergence fails, and so
    # does a declared one that silently disappeared (which would mean the new
    # machine had been loosened back to the legacy laxity).
    assert observed_divergences == _INTENTIONAL_DIVERGENCES


def test_the_declared_divergences_are_all_skip_the_micro_live_stage() -> None:
    """Names the defect rather than just listing edges: every divergence is an
    attempt to reach real capital without passing through MICRO_LIVE."""
    from services.lifecycle import DeploymentState, REAL_CAPITAL_STATES

    for src_value, dst_value in _INTENTIONAL_DIVERGENCES:
        src_deployment = project(AlphaState(src_value)).deployment
        dst_deployment = project(AlphaState(dst_value)).deployment
        assert dst_deployment is DeploymentState.LIVE
        assert dst_deployment in REAL_CAPITAL_STATES
        assert src_deployment is not DeploymentState.MICRO_LIVE


def test_the_staged_path_to_live_the_new_machine_requires() -> None:
    """§12's rollout, now expressible: paper -> shadow -> micro-live -> live, one
    stage at a time, with no shortcut."""
    from services.lifecycle import DeploymentState, can_deployment_transition

    staged = [
        DeploymentState.OFFLINE,
        DeploymentState.PAPER,
        DeploymentState.SHADOW,
        DeploymentState.MICRO_LIVE,
        DeploymentState.LIVE,
    ]
    for src, dst in zip(staged, staged[1:]):
        assert can_deployment_transition(src, dst), (src, dst)
    # and no shortcut skips a stage
    for index, src in enumerate(staged):
        for dst in staged[index + 2:]:
            assert not can_deployment_transition(src, dst), (src, dst)
