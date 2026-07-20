"""Guardrailed research agent loop (Phase 11).

Drives the alpha pipeline as a NON-HUMAN actor: propose -> evaluate -> advance up
to PAPER_TRADING, then hand candidates to a HUMAN review queue. Every step is
checked against the guardrail policy AND the lifecycle state machine, so the agent
cannot self-approve or reset the kill-switch (those raise). The loop physically has
no path to APPROVE — it can only REQUEST human approval.
"""
from __future__ import annotations

from typing import Optional, Sequence

from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.hashing import stable_hash
from quantos.contracts.research import AlphaState, ValidationVerdict

from services.alpha.config import AlphaFactoryConfig
from services.alpha.catalog import AlphaCatalog
from services.alpha.evaluator import AlphaEvaluator
from services.alpha.factory import batch_pbo
from services.alpha.lifecycle import AlphaLifecycleManager
from services.research.backtest_engine.costs import CostModel
from services.research.ports.persistence import TrialPersistenceError

from .guardrails import AgentAction, assert_action_allowed


class ResearchAgentLoop:
    def __init__(self, catalog: Optional[AlphaCatalog] = None,
                 config: Optional[AlphaFactoryConfig] = None,
                 evaluator=None, manager=None, actor: Optional[Actor] = None,
                 runtime=None):
        self.config = config or AlphaFactoryConfig()
        self.catalog = catalog or AlphaCatalog()
        self.evaluator = evaluator or AlphaEvaluator(self.catalog, self.config)
        self.manager = manager or AlphaLifecycleManager(self.catalog, self.config)
        self.actor = actor or Actor(actor_id="research-agent", kind=ActorKind.AGENT)
        self.runtime = runtime
        if self.actor.is_human:
            raise ValueError("the autonomous research loop must run as a non-human actor")

    def run(self, candidates: Sequence[tuple], *, timestamp: int = 0,
            evidence: Optional[dict] = None,
            runtime_context: Optional[dict] = None) -> dict:
        """candidates: (AlphaDefinition, periods). Returns a report + the human
        review queue (PAPER_TRADING alphas awaiting a human approval token).

        Evidence parity with AlphaFactory (the gauntlet ↔ evaluator wiring): the
        selection-level PBO is self-computed here for the whole batch via CSCV, and
        `evidence` maps alpha_id -> {"leakage_ok": bool, "regime_profitable_frac": float}
        for the two pieces that cannot be derived from pre-computed PanelPeriods alone
        (leakage needs the expression+panel; regime can also be self-computed by the
        evaluator under its opt-in flag). All default to None = NOT ASSESSED, so an
        alpha with un-supplied evidence stays in TESTING (fail-closed) — never an
        advance on a silent default. Containment is unchanged: the loop has no path
        to APPROVED regardless of evidence.

        Each candidate is isolated: an error (evaluation failure, guardrail) is
        recorded and the loop continues — it never propagates uncaught nor orphans
        the others, and an errored alpha is NOT advanced (fail-closed, review P1)."""
        candidate_pool = tuple(candidates)
        # Persist the complete batch before the selection-level PBO performs
        # any backtest. Provider-driven searches also record before provider
        # evaluation, making this an idempotent second line of defense.
        for definition, _periods in candidate_pool:
            self.catalog.register_trial(definition.definition_hash)

        report, review_queue = {}, []
        evidence = evidence or {}
        active_candidates = []
        for definition, periods in candidate_pool:
            try:
                self.catalog.register(definition)
                active_candidates.append((definition, periods))
            except Exception as exc:
                report[definition.alpha_id] = {
                    "error": type(exc).__name__,
                    "state": self._state_of(definition.alpha_id),
                }

        prepare_observation = getattr(
            self.evaluator, "prepare_catalog_observation", None
        )
        if callable(prepare_observation):
            prepared_candidates = []
            for definition, periods in active_candidates:
                try:
                    prepare_observation(definition.alpha_id, periods)
                    prepared_candidates.append((definition, periods))
                except Exception as exc:
                    report[definition.alpha_id] = {
                        "error": type(exc).__name__,
                        "state": self._state_of(definition.alpha_id),
                    }
            active_candidates = prepared_candidates
        pbo_batch = batch_pbo(
            active_candidates, cost=getattr(self.evaluator, "cost", CostModel()),
            quantile=getattr(self.evaluator, "quantile", 0.2),
            target_aum=getattr(self.evaluator, "target_aum", 1e7))
        for defn, periods in active_candidates:
            try:
                assert_action_allowed(self.actor, AgentAction.EVALUATE)
                ev_in = evidence.get(defn.alpha_id, {})
                evaluation_args = {
                    "as_of": timestamp,
                    "pbo_evidence": pbo_batch,
                    "leakage_ok": ev_in.get("leakage_ok"),
                    "regime_profitable_frac": ev_in.get("regime_profitable_frac"),
                }
                if self.runtime is None:
                    ev = self.evaluator.evaluate(
                        defn.alpha_id, periods, **evaluation_args)
                else:
                    if not isinstance(runtime_context, dict) or not runtime_context:
                        raise ValueError(
                            "a configured model runtime requires explicit runtime_context"
                        )
                    runtime_input = {
                        "alpha_id": defn.alpha_id,
                        "timestamp": timestamp,
                        "evidence_sha256": stable_hash(ev_in),
                        "runtime_context": runtime_context,
                    }
                    invocation_id = (
                        f"alpha-evaluation:{defn.alpha_id}:"
                        f"{stable_hash(runtime_input)[:16]}"
                    )
                    runtime_result = self.runtime.invoke(
                        invocation_id=invocation_id,
                        action="EVALUATE",
                        input_payload=runtime_input,
                        execute=lambda _payload: self.evaluator.evaluate(
                            defn.alpha_id, periods, **evaluation_args
                        ),
                        recorded_at=timestamp,
                    )
                    ev = runtime_result.value
                assert_action_allowed(self.actor, AgentAction.ADVANCE_TESTING)
                self.manager.advance(defn.alpha_id, AlphaState.TESTING, self.actor, timestamp=timestamp, evaluation=ev)
                if ev.verdict == ValidationVerdict.PASS:
                    assert_action_allowed(self.actor, AgentAction.ADVANCE_VALIDATED)
                    self.manager.advance(defn.alpha_id, AlphaState.VALIDATED, self.actor, timestamp=timestamp, evaluation=ev)
                    assert_action_allowed(self.actor, AgentAction.ADVANCE_PAPER)
                    self.manager.advance(defn.alpha_id, AlphaState.PAPER_TRADING, self.actor, timestamp=timestamp, evaluation=ev)
                    review_queue.append(defn.alpha_id)
                elif ev.verdict == ValidationVerdict.FAIL:
                    assert_action_allowed(self.actor, AgentAction.REJECT)
                    self.manager.advance(defn.alpha_id, AlphaState.REJECTED, self.actor, timestamp=timestamp)
                report[defn.alpha_id] = {"verdict": ev.verdict.value, "state": self._state_of(defn.alpha_id),
                                         "evaluation": ev}
            except TrialPersistenceError:
                raise
            except Exception as e:                       # isolate: record, do NOT advance, continue
                report[defn.alpha_id] = {"error": type(e).__name__, "state": self._state_of(defn.alpha_id)}
        return {"report": report, "human_review_queue": review_queue}

    def _state_of(self, alpha_id: str):
        try:
            return self.catalog.state(alpha_id).value
        except Exception:
            return None

    def request_human_approval(self, alpha_id: str) -> str:
        """The agent may REQUEST approval — a handoff token a HUMAN later acts on.
        It cannot grant it."""
        assert_action_allowed(self.actor, AgentAction.REQUEST_HUMAN_APPROVAL)
        return f"APPROVAL_REQUESTED:{alpha_id}"

    def try_approve(self, alpha_id: str) -> None:
        """Exists to prove the guardrail: an agent calling this RAISES — there is
        no agent path to APPROVE."""
        assert_action_allowed(self.actor, AgentAction.APPROVE)   # raises ForbiddenAgentAction for agents

    def try_reset_kill_switch(self, kill_switch) -> None:
        """Likewise: an agent cannot reset the kill-switch."""
        assert_action_allowed(self.actor, AgentAction.RESET_KILL_SWITCH)  # raises for agents
