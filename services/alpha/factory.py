"""Alpha Factory orchestrator (Phase 6, step 13).

Thin, deterministic wiring over the parts — no new math: register candidates,
evaluate them, and advance each as far as its evidence allows. It runs as a
NON-HUMAN actor and therefore has NO code path to APPROVED: a great alpha stops
at PAPER_TRADING; a FAIL is rejected; WARN / REQUIRES_HUMAN_REVIEW is parked in
TESTING for a human. Promotion to APPROVED is exclusively a human action through
the lifecycle manager.
"""
from __future__ import annotations

from typing import Optional, Sequence

from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition
from quantos.contracts.research import AlphaState, ValidationVerdict

from .catalog import AlphaCatalog
from .config import AlphaFactoryConfig
from .evaluator import AlphaEvaluator
from .lifecycle import AlphaLifecycleManager


class AlphaFactory:
    def __init__(self, catalog: Optional[AlphaCatalog] = None, config: Optional[AlphaFactoryConfig] = None,
                 evaluator=None, manager=None, actor: Optional[Actor] = None):
        self.config = config or AlphaFactoryConfig()
        self.catalog = catalog or AlphaCatalog()
        self.evaluator = evaluator or AlphaEvaluator(self.catalog, self.config)
        self.manager = manager or AlphaLifecycleManager(self.catalog, self.config)
        self.actor = actor or Actor(actor_id="alpha-factory", kind=ActorKind.AGENT)
        if self.actor.is_human:
            raise ValueError("the automated factory must run as a non-human actor (no self-approval)")

    def run(self, candidates: Sequence[tuple[AlphaDefinition, object]], *, timestamp: int = 0) -> dict:
        report: dict[str, dict] = {}
        for defn, periods in candidates:
            self.catalog.register(defn)
            ev = self.evaluator.evaluate(defn.alpha_id, periods, as_of=timestamp)
            self.manager.advance(defn.alpha_id, AlphaState.TESTING, self.actor,
                                 timestamp=timestamp, evaluation=ev)
            if ev.verdict == ValidationVerdict.PASS:
                self.manager.advance(defn.alpha_id, AlphaState.VALIDATED, self.actor,
                                     timestamp=timestamp, evaluation=ev)
                self.manager.advance(defn.alpha_id, AlphaState.PAPER_TRADING, self.actor,
                                     timestamp=timestamp, evaluation=ev)
            elif ev.verdict == ValidationVerdict.FAIL:
                self.manager.advance(defn.alpha_id, AlphaState.REJECTED, self.actor, timestamp=timestamp)
            # WARN / REQUIRES_HUMAN_REVIEW -> parked in TESTING for a human
            report[defn.alpha_id] = {"evaluation": ev, "state": self.catalog.state(defn.alpha_id)}
        return report
