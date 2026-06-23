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
from services.research.backtest_engine.costs import CostModel
from services.research.validation.deflated_sharpe import pbo_cscv

from .catalog import AlphaCatalog
from .config import AlphaFactoryConfig
from .evaluator import AlphaEvaluator
from .lifecycle import AlphaLifecycleManager
from .panel_backtest import run_panel_backtest

_MAX_CSCV_BLOCKS = 8   # keep C(blocks, blocks/2) small + deterministic


def _batch_pbo(candidates, *, cost: CostModel, quantile: float, target_aum: float) -> Optional[float]:
    """Real Probability of Backtest Overfitting for SELECTING the best of this
    candidate batch, via CSCV over the candidates' net panel returns. Returns None
    (NOT a passing 0.0) when it cannot be honestly computed — a single candidate,
    too few periods, or malformed panels — so the evaluator fails closed to human
    review rather than treating an unmeasured selection as overfitting-free."""
    series: list[list[float]] = []
    for _defn, periods in candidates:
        try:
            res = run_panel_backtest(periods, cost=cost, quantile=quantile, target_aum=target_aum)
        except Exception:
            return None
        series.append(list(res.period_returns))
    if len(series) < 2:                       # CSCV selection bias needs >= 2 configs
        return None
    t = min((len(s) for s in series), default=0)
    if t < 4:                                 # need >= 2 IS/OOS blocks
        return None
    n_blocks = min(_MAX_CSCV_BLOCKS, t)
    block = t // n_blocks
    perf = []
    for b in range(n_blocks):
        lo, hi = b * block, (t if b == n_blocks - 1 else (b + 1) * block)
        perf.append([sum(s[lo:hi]) for s in series])
    try:
        return pbo_cscv(perf)
    except Exception:
        return None


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

    def run(self, candidates: Sequence[tuple[AlphaDefinition, object]], *, timestamp: int = 0,
            evidence: Optional[dict] = None) -> dict:
        """`evidence` maps alpha_id -> {"leakage_ok": bool, "regime_profitable_frac": float}.
        Leakage and regime profitability cannot be derived from pre-computed
        PanelPeriods alone, so a candidate without supplied evidence is parked in
        TESTING for a human (fail-closed) rather than auto-advanced on a default.
        The overfitting (PBO) input is computed here for the whole batch via CSCV."""
        evidence = evidence or {}
        # one selection-level PBO for the batch (None when not honestly computable)
        batch_pbo = _batch_pbo(
            candidates, cost=getattr(self.evaluator, "cost", CostModel()),
            quantile=getattr(self.evaluator, "quantile", 0.2),
            target_aum=getattr(self.evaluator, "target_aum", 1e7))
        report: dict[str, dict] = {}
        for defn, periods in candidates:
            self.catalog.register(defn)
            ev_in = evidence.get(defn.alpha_id, {})
            ev = self.evaluator.evaluate(
                defn.alpha_id, periods, as_of=timestamp, pbo=batch_pbo,
                leakage_ok=ev_in.get("leakage_ok"),
                regime_profitable_frac=ev_in.get("regime_profitable_frac"))
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
