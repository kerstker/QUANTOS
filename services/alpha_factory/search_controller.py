"""Search controller (727 §8.3 funnel, layers 1-4) — plan a hypothesis-driven search.

The controller ORCHESTRATES the first funnel layers as pure composition:

  candidate generation (caller's candidate_exprs)
  -> semantic check (compile + constrained-space check; ELIMINATED_SEMANTIC)
  -> budget check (W10 ResearchBudget over the declared family)
  -> canonicalize / deduplicate (ELIMINATED_DUPLICATE)

The later layers (data availability at the store level, prescreen, post-cost
backtest, walk-forward, DSR/PBO/FDR, perturbation, regime, capacity) live in
the evaluator / gauntlet and are NOT this slice.

DIVISION OF LABOUR (deliberate): the controller writes NOTHING. It returns a
SearchPlan whose `eliminations` are typed records the CALLER persists through
the P5-1 wiring (record_trial_provenance) alongside the survivors' RECORDED
rows — ledger IO stays at the driver boundary, the core stays deterministic
(no I/O, no wall clock: `now_ms` is caller-supplied and carried as
`planned_at_ms` for exactly those downstream writes).

727 §8.1 is a hard PRECONDITION: a search whose hypothesis fails the economic
rules never starts (SearchPlanError) —「无合格假设不开搜」.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from services.research.research_budget import (
    BudgetExhausted,
    ResearchBudget,
    check_budget,
    evaluate_stopping_rules,
)
from services.research.trial_provenance import TrialStatus

from .candidate_compiler import (
    CandidateCompileError,
    CompiledCandidate,
    compile_from_hypothesis,
)
from .candidate_deduplicator import CandidateDeduplicator
from .constrained_search_space import (
    SearchSpaceConstraints,
    check_expression,
)
from .economic_rules import check_hypothesis_rules
from .hypothesis import Hypothesis


class SearchPlanError(ValueError):
    """The search plan violates a fail-closed precondition (727 §8.1/§8.2)."""


@dataclass(frozen=True)
class EliminationRecord:
    """One funnel-layer elimination, shaped for direct TrialProvenance writes:
    status is a TrialStatus member and reasons are the stable machine codes."""

    expression_hash: str
    status: TrialStatus
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.status, TrialStatus):
            raise SearchPlanError("status must be a TrialStatus")
        if not self.reasons:
            # §7.1 fail-closed: an elimination without a reason is not recordable.
            raise SearchPlanError("an elimination requires at least one reason")


@dataclass(frozen=True)
class SearchPlan:
    """The survivors + the elimination trail of one planned search."""

    search_id: str
    hypothesis_id: str
    budget: ResearchBudget
    constraints: SearchSpaceConstraints
    candidates: tuple[CompiledCandidate, ...]
    eliminations: tuple[EliminationRecord, ...]
    planned_at_ms: int


def _require_text(name: str, value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SearchPlanError(f"{name} must be a non-empty string")
    return value


class SearchController:
    """Pure planner for the §8.3 funnel's first layers (see module docstring)."""

    def __init__(self, *, deduplicator: Optional[CandidateDeduplicator] = None) -> None:
        if deduplicator is not None and not isinstance(
            deduplicator, CandidateDeduplicator
        ):
            raise SearchPlanError("deduplicator must be a CandidateDeduplicator")
        self._deduplicator = deduplicator or CandidateDeduplicator()

    def plan_search(
        self,
        *,
        search_id: str,
        hypothesis: Hypothesis,
        budget: ResearchBudget,
        constraints: SearchSpaceConstraints,
        candidate_exprs: Sequence[tuple],
        now_ms: int,
    ) -> SearchPlan:
        """Plan one hypothesis-driven search family.

        Layer order per candidate: compile (bound to the hypothesis) -> space
        check -> [family-level budget check] -> dedup. Compilation and space
        violations eliminate with ELIMINATED_SEMANTIC + the stable codes;
        duplicates eliminate with ELIMINATED_DUPLICATE. Survivor order is the
        deterministic first-seen order of the input family.
        """
        search_id = _require_text("search_id", search_id)
        if not isinstance(hypothesis, Hypothesis):
            raise SearchPlanError("hypothesis must be a Hypothesis")
        if not isinstance(budget, ResearchBudget):
            raise SearchPlanError("budget must be a ResearchBudget")
        if not isinstance(constraints, SearchSpaceConstraints):
            raise SearchPlanError("constraints must be a SearchSpaceConstraints")
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise SearchPlanError("now_ms must be a non-negative integer")
        try:
            declared = tuple(candidate_exprs)
        except TypeError as exc:
            raise SearchPlanError(
                "candidate_exprs must be an iterable family"
            ) from exc
        if not declared:
            raise SearchPlanError("candidate_exprs must be non-empty")

        # §8.1 precondition: no qualified hypothesis, no search.
        violations = check_hypothesis_rules(hypothesis)
        if violations:
            raise SearchPlanError(
                "hypothesis fails 727 §8.1 economic rules: " + ", ".join(violations)
            )

        eliminations: list[EliminationRecord] = []
        compiled: list[CompiledCandidate] = []
        for expr in declared:
            try:
                candidate = compile_from_hypothesis(hypothesis, expr)
            except CandidateCompileError as exc:
                message = str(exc)
                eliminations.append(
                    EliminationRecord(
                        expression_hash="",
                        status=TrialStatus.ELIMINATED_SEMANTIC,
                        reasons=("COMPILE_INVALID_DSL", message[:200]),
                    )
                )
                continue
            space_violations = check_expression(candidate.expression, constraints)
            if space_violations:
                eliminations.append(
                    EliminationRecord(
                        expression_hash=candidate.expression_hash,
                        status=TrialStatus.ELIMINATED_SEMANTIC,
                        reasons=space_violations,
                    )
                )
                continue
            compiled.append(candidate)

        # W10 budget over the DECLARED family (the pre-registration contract
        # bounds the enumerated family, not incremental novelty — same usage
        # semantics as run_registered_candidate_batch). Compute hours are not
        # metered here, so a declared compute budget fails closed as
        # unverified_dimension — only declare verifiable dimensions.
        usage = {
            "trials_used": len(declared),
            "feature_count": max(
                (len(_candidate_fields(c)) for c in compiled), default=0
            ),
            "parameter_dimensions": max(
                (len(c.params) for c in compiled), default=0
            ),
        }
        budget_check = check_budget(budget, usage)
        if budget_check.exhausted:
            raise BudgetExhausted(budget_check)

        kept, rejected = self._deduplicator.filter_batch(compiled)
        for candidate, decision in rejected:
            eliminations.append(
                EliminationRecord(
                    expression_hash=candidate.expression_hash,
                    status=TrialStatus.ELIMINATED_DUPLICATE,
                    reasons=(decision.verdict, decision.matched_expression_hash),
                )
            )

        return SearchPlan(
            search_id=search_id,
            hypothesis_id=hypothesis.hypothesis_id,
            budget=budget,
            constraints=constraints,
            candidates=tuple(kept),
            eliminations=tuple(eliminations),
            planned_at_ms=now_ms,
        )

    def evaluate_stopping(
        self,
        trial_count: int,
        best_scores: Sequence[float],
        budget: ResearchBudget,
    ) -> bool:
        """Should this search stop? Thin forward over research_budget's
        evaluate_stopping_rules plus the budget's own max_trials ceiling:
        - trial_count >= budget.max_trials (declared hard cap) -> stop;
        - otherwise the declared stopping_rules are evaluated over the
          per-trial best-score history (higher-is-better, per research_budget).
        trial_count is the SEARCH's own counter (the ledgered population),
        which may legitimately exceed len(best_scores) when some trials never
        produced a score — hence a separate parameter, not len(history)."""
        if not isinstance(budget, ResearchBudget):
            raise SearchPlanError("budget must be a ResearchBudget")
        if (
            isinstance(trial_count, bool)
            or not isinstance(trial_count, int)
            or trial_count < 0
        ):
            raise SearchPlanError("trial_count must be a non-negative integer")
        if budget.max_trials is not None and trial_count >= budget.max_trials:
            return True
        return evaluate_stopping_rules(budget.stopping_rules, best_scores).stop


def _candidate_fields(candidate: CompiledCandidate) -> tuple[str, ...]:
    """Fields referenced by a compiled candidate (budget feature_count usage)."""
    fields: set[str] = set()

    def walk(node) -> None:
        if not isinstance(node, tuple) or not node:
            return
        if node[0] == "field" and len(node) == 2:
            fields.add(node[1])
            return
        for child in node[1:]:
            walk(child)

    walk(candidate.expression)
    return tuple(sorted(fields))


__all__ = [
    "EliminationRecord",
    "SearchController",
    "SearchPlan",
    "SearchPlanError",
]
