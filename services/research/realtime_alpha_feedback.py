"""Causal feedback policy for autonomous realtime Alpha exploration.

The policy consumes only already-realized, append-only engineering-shadow
outcomes.  It may allocate a small number of *new exploratory trials*, but it
never changes an existing definition, validation result, promotion state, or
execution authority.  Every proposal carries the last consumed outcome time as
its OOS cutoff; callers must evaluate it only on observations after that cutoff.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import statistics
from typing import Any, Mapping, Sequence

from services.research.realtime_alpha_discovery import (
    REALTIME_FEEDBACK_SIGNAL_VERSION,
    REALTIME_FEEDBACK_SIGN_FLIP_SIGNAL_VERSION,
    RealtimeHypothesis,
)


class RealtimeAlphaFeedbackError(ValueError):
    """The retained feedback evidence is malformed or authority-widened."""


@dataclass(frozen=True)
class CandidateFeedback:
    candidate_id: str
    family_id: str
    n_realizations: int
    mean_net_return: float | None
    standard_error: float | None
    ucb_95: float | None
    positive_fraction: float | None
    feedback_cutoff_ms: int | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FamilyFeedback:
    family_id: str
    action: str
    parent_candidate_id: str | None
    eligible_candidates: int
    best_ucb_95: float | None
    reason_code: str
    proposal_parent_candidate_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FeedbackProposal:
    spec: RealtimeHypothesis
    parent_candidate_id: str
    feedback_cutoff_ms: int
    mutation: str
    fresh_oos_required: bool = True
    adaptive_generation: int = 1

    def as_dict(self) -> dict[str, object]:
        return {
            "spec": self.spec.as_dict(),
            "parent_candidate_id": self.parent_candidate_id,
            "feedback_cutoff_ms": self.feedback_cutoff_ms,
            "mutation": self.mutation,
            "fresh_oos_required": self.fresh_oos_required,
            "adaptive_generation": self.adaptive_generation,
        }


@dataclass(frozen=True)
class RealtimeAlphaFeedbackPlan:
    status: str
    candidates: tuple[CandidateFeedback, ...]
    families: tuple[FamilyFeedback, ...]
    proposals: tuple[FeedbackProposal, ...]
    consumed_realizations: int
    min_realizations: int
    max_new: int
    candidate_budget_remaining: int = 0
    proposal_blockers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "candidates": [item.as_dict() for item in self.candidates],
            "families": [item.as_dict() for item in self.families],
            "proposals": [item.as_dict() for item in self.proposals],
            "consumed_realizations": self.consumed_realizations,
            "min_realizations": self.min_realizations,
            "max_new": self.max_new,
            "candidate_budget_remaining": self.candidate_budget_remaining,
            "proposal_blockers": list(self.proposal_blockers),
            "scope": "EXPLORATION_BUDGET_ONLY",
            "formal_gate_impact": False,
            "promotion_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        }


def _horizon_label(horizon_ms: int) -> str:
    if horizon_ms % 3_600_000 == 0:
        return f"{horizon_ms // 3_600_000}h"
    if horizon_ms % 60_000 == 0:
        return f"{horizon_ms // 60_000}m"
    return f"{horizon_ms // 1_000}s"


def _validated_returns(
    candidate_id: str,
    runs: Sequence[Mapping[str, Any]],
) -> tuple[list[float], int | None]:
    values: list[float] = []
    cutoff: int | None = None
    for row in runs:
        if not isinstance(row, Mapping):
            raise RealtimeAlphaFeedbackError("paper feedback row is invalid")
        metrics = row.get("metrics")
        if (
            row.get("candidate_id") != candidate_id
            or row.get("status") != "ENGINEERING_SHADOW_REALIZED"
            or not isinstance(metrics, Mapping)
            or metrics.get("candidate_id") != candidate_id
        ):
            raise RealtimeAlphaFeedbackError("paper feedback identity is invalid")
        if (
            metrics.get("paper_only") is not True
            or any(
                metrics.get(field) is not False
                for field in (
                    "promotion_eligible",
                    "live_allowed",
                    "order_submission_allowed",
                )
            )
        ):
            raise RealtimeAlphaFeedbackError("paper feedback safety boundary is invalid")
        value = metrics.get("net_return")
        as_of_ms = metrics.get("as_of_ms")
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or type(as_of_ms) is not int
            or as_of_ms < 0
        ):
            raise RealtimeAlphaFeedbackError("paper feedback metric is invalid")
        values.append(float(value))
        cutoff = as_of_ms if cutoff is None else max(cutoff, as_of_ms)
    return values, cutoff


def _candidate_feedback(
    spec: RealtimeHypothesis,
    runs: Sequence[Mapping[str, Any]],
) -> CandidateFeedback:
    values, cutoff = _validated_returns(spec.candidate_id, runs)
    if not values:
        return CandidateFeedback(
            candidate_id=spec.candidate_id,
            family_id=spec.family_id,
            n_realizations=0,
            mean_net_return=None,
            standard_error=None,
            ucb_95=None,
            positive_fraction=None,
            feedback_cutoff_ms=None,
        )
    mean = statistics.fmean(values)
    standard_error = (
        statistics.stdev(values) / math.sqrt(len(values))
        if len(values) > 1
        else None
    )
    ucb = mean + 1.96 * standard_error if standard_error is not None else None
    return CandidateFeedback(
        candidate_id=spec.candidate_id,
        family_id=spec.family_id,
        n_realizations=len(values),
        mean_net_return=mean,
        standard_error=standard_error,
        ucb_95=ucb,
        positive_fraction=sum(value > 0 for value in values) / len(values),
        feedback_cutoff_ms=cutoff,
    )


def build_feedback_plan(
    base_hypotheses: Sequence[RealtimeHypothesis],
    paper_runs_by_candidate: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    existing_candidate_ids: Sequence[str],
    proposal_parent_ids: Sequence[str] | None = None,
    max_new: int = 4,
    min_realizations: int = 8,
    decision_interval_ms: int = 30_000,
) -> RealtimeAlphaFeedbackPlan:
    """Allocate bounded adaptive trials from causal paper outcomes.

    ``UCB > 0`` means a family is not yet refuted at the exploration level; it
    does not mean the Alpha passed any statistical or deployment gate.
    """
    if type(max_new) is not int or max_new < 0:
        raise RealtimeAlphaFeedbackError("max_new must be a non-negative integer")
    if type(min_realizations) is not int or min_realizations < 2:
        raise RealtimeAlphaFeedbackError("min_realizations must be at least two")
    if type(decision_interval_ms) is not int or decision_interval_ms <= 0:
        raise RealtimeAlphaFeedbackError("decision interval is invalid")
    specs = {item.candidate_id: item for item in base_hypotheses}
    if len(specs) != len(tuple(base_hypotheses)):
        raise RealtimeAlphaFeedbackError("base hypothesis identities are duplicated")
    unknown = sorted(set(paper_runs_by_candidate) - set(specs))
    if unknown:
        raise RealtimeAlphaFeedbackError(f"unknown candidate feedback: {unknown}")
    proposal_parents = (
        set(specs)
        if proposal_parent_ids is None
        else {str(item) for item in proposal_parent_ids}
    )
    unknown_parents = sorted(proposal_parents - set(specs))
    if unknown_parents:
        raise RealtimeAlphaFeedbackError(
            f"unknown proposal parent candidates: {unknown_parents}"
        )

    candidate_rows = tuple(
        _candidate_feedback(spec, paper_runs_by_candidate.get(spec.candidate_id, ()))
        for spec in sorted(specs.values(), key=lambda item: item.candidate_id)
    )
    by_family: dict[str, list[CandidateFeedback]] = {}
    for item in candidate_rows:
        by_family.setdefault(item.family_id, []).append(item)

    family_rows: list[FamilyFeedback] = []
    expandable: list[tuple[float, CandidateFeedback]] = []
    for family_id in sorted({item.family_id for item in specs.values()}):
        eligible = [
            item
            for item in by_family.get(family_id, ())
            if item.n_realizations >= min_realizations and item.ucb_95 is not None
        ]
        parent = max(
            eligible,
            key=lambda item: (float(item.ucb_95), item.candidate_id),
            default=None,
        )
        proposal_parent = max(
            (item for item in eligible if item.candidate_id in proposal_parents),
            key=lambda item: (float(item.ucb_95), item.candidate_id),
            default=None,
        )
        if parent is None:
            action = "COLLECT"
            reason = "MIN_REALIZATIONS_NOT_MET"
        elif float(parent.ucb_95) > 0:
            action = "EXPAND"
            reason = "POSITIVE_EXPLORATION_UCB"
            if proposal_parent is not None and float(proposal_parent.ucb_95) > 0:
                expandable.append((float(proposal_parent.ucb_95), proposal_parent))
            else:
                reason = "POSITIVE_UCB_ADAPTIVE_PARENT_ONLY"
        else:
            action = "DEPRIORITIZE"
            reason = "NON_POSITIVE_EXPLORATION_UCB"
        family_rows.append(
            FamilyFeedback(
                family_id=family_id,
                action=action,
                parent_candidate_id=parent.candidate_id if parent else None,
                eligible_candidates=len(eligible),
                best_ucb_95=float(parent.ucb_95) if parent else None,
                reason_code=reason,
                proposal_parent_candidate_id=(
                    proposal_parent.candidate_id if proposal_parent else None
                ),
            )
        )

    existing = set(str(item) for item in existing_candidate_ids)
    horizons_by_mechanism: dict[str, set[int]] = {}
    for spec in specs.values():
        horizons_by_mechanism.setdefault(spec.mechanism, set()).add(spec.horizon_ms)
    proposals: list[FeedbackProposal] = []
    proposal_blockers: set[str] = set()
    if max_new == 0 and expandable:
        proposal_blockers.add("ADAPTIVE_BUDGET_EXHAUSTED")
    for _score, parent_row in sorted(
        expandable,
        key=lambda item: (-item[0], item[1].candidate_id),
    ):
        if max_new == 0:
            break
        parent = specs[parent_row.candidate_id]
        assert parent_row.feedback_cutoff_ms is not None
        horizon_mutation_blocked = True
        raw_mutations = (
            ("HALF_HORIZON", max(decision_interval_ms, parent.horizon_ms // 2)),
            ("DOUBLE_HORIZON", parent.horizon_ms * 2),
        )
        for mutation, raw_horizon in raw_mutations:
            horizon = max(
                decision_interval_ms,
                round(raw_horizon / decision_interval_ms) * decision_interval_ms,
            )
            if horizon in horizons_by_mechanism.get(parent.mechanism, set()):
                continue
            candidate_id = f"rt_{parent.mechanism}_{_horizon_label(horizon)}_fb1"
            if candidate_id in existing or any(
                item.spec.candidate_id == candidate_id for item in proposals
            ):
                proposal_blockers.add("CANDIDATE_ID_ALREADY_REGISTERED")
                continue
            horizon_mutation_blocked = False
            spec = RealtimeHypothesis(
                candidate_id=candidate_id,
                family_id=parent.family_id,
                mechanism=parent.mechanism,
                horizon_ms=horizon,
                required_features=parent.required_features,
                economic_hypothesis=(
                    f"Adaptive horizon challenger of {parent.candidate_id}; "
                    f"the original mechanism must persist on fresh post-feedback OOS data."
                ),
                falsification_standard=(
                    "Must accumulate a new locked post-feedback window and pass all ordinary "
                    "cost, holdout, PBO, DSR, regime, capacity, and drawdown gates."
                ),
                signal_version=REALTIME_FEEDBACK_SIGNAL_VERSION,
                lookback_ms=parent.lookback_ms,
            )
            proposals.append(
                FeedbackProposal(
                    spec=spec,
                    parent_candidate_id=parent.candidate_id,
                    feedback_cutoff_ms=parent_row.feedback_cutoff_ms,
                    mutation=mutation,
                )
            )
            if len(proposals) >= max_new:
                break

        # Once every horizon for a positive root is already registered, keep
        # exploring the mechanism itself instead of silently stopping.  This
        # is a direction challenger, not a child-of-child path: it keeps the
        # frozen root's family/mechanism/features and receives a fresh OOS
        # cutoff just like horizon mutations.
        if horizon_mutation_blocked and len(proposals) < max_new:
            candidate_id = (
                f"rt_{parent.mechanism}_{_horizon_label(parent.horizon_ms)}"
                "_signflip_fb1"
            )
            if candidate_id in existing or any(
                item.spec.candidate_id == candidate_id for item in proposals
            ):
                proposal_blockers.add("STRUCTURAL_MUTATION_ALREADY_REGISTERED")
                proposal_blockers.add("HORIZON_ALREADY_PRE_REGISTERED")
            else:
                spec = RealtimeHypothesis(
                    candidate_id=candidate_id,
                    family_id=parent.family_id,
                    mechanism=parent.mechanism,
                    horizon_ms=parent.horizon_ms,
                    required_features=parent.required_features,
                    economic_hypothesis=(
                        f"Direction-flip challenger of {parent.candidate_id}; "
                        "test whether the observed mechanism is anti-predictive "
                        "on fresh post-feedback OOS data."
                    ),
                    falsification_standard=(
                        "Must accumulate a new locked post-feedback window and pass all ordinary "
                        "cost, holdout, PBO, DSR, regime, capacity, and drawdown gates."
                    ),
                    signal_version=REALTIME_FEEDBACK_SIGN_FLIP_SIGNAL_VERSION,
                    lookback_ms=parent.lookback_ms,
                )
                proposals.append(
                    FeedbackProposal(
                        spec=spec,
                        parent_candidate_id=parent.candidate_id,
                        feedback_cutoff_ms=parent_row.feedback_cutoff_ms,
                        mutation="SIGN_FLIP",
                    )
                )
        if len(proposals) >= max_new:
            break

    if not proposals and not proposal_blockers:
        if not expandable:
            if not any(item.n_realizations for item in candidate_rows):
                proposal_blockers.add("NO_REALIZED_FEEDBACK")
            elif all(
                item.ucb_95 is None or float(item.ucb_95) <= 0
                for item in candidate_rows
            ):
                proposal_blockers.add("NO_POSITIVE_EXPLORATION_UCB")
            else:
                proposal_blockers.add("POSITIVE_UCB_HAS_NO_ROOT_PARENT")
        else:
            proposal_blockers.add("NO_UNREGISTERED_MUTATION")

    status = (
        "ADAPTIVE_EXPLORATION_READY"
        if proposals
        else "FEEDBACK_ACTIVE_NO_NEW_TRIAL"
        if any(item.n_realizations for item in candidate_rows)
        else "FEEDBACK_COLLECTING"
    )
    return RealtimeAlphaFeedbackPlan(
        status=status,
        candidates=candidate_rows,
        families=tuple(family_rows),
        proposals=tuple(proposals),
        consumed_realizations=sum(item.n_realizations for item in candidate_rows),
        min_realizations=min_realizations,
        max_new=max_new,
        candidate_budget_remaining=max_new - len(proposals),
        proposal_blockers=tuple(sorted(proposal_blockers)),
    )


__all__ = [
    "CandidateFeedback",
    "FamilyFeedback",
    "FeedbackProposal",
    "RealtimeAlphaFeedbackError",
    "RealtimeAlphaFeedbackPlan",
    "build_feedback_plan",
]
