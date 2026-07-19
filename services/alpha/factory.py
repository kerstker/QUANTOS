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
from quantos.contracts.cost import CostModel
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.validation.deflated_sharpe import pbo_cscv

from .catalog import AlphaCatalog
from .config import AlphaFactoryConfig
from .evaluator import AlphaEvaluator
from .lifecycle import AlphaLifecycleManager
from .panel_backtest import run_panel_backtest

_MAX_CSCV_BLOCKS = 8   # keep C(blocks, blocks/2) small + deterministic


def batch_pbo(candidates, *, cost: CostModel, quantile: float, target_aum: float) -> Optional[float]:
    """Real Probability of Backtest Overfitting for SELECTING the best of this
    candidate batch, via CSCV over the candidates' net panel returns. Returns None
    (NOT a passing 0.0) when it cannot be honestly computed — a single candidate,
    too few periods, or malformed panels — so the evaluator fails closed to human
    review rather than treating an unmeasured selection as overfitting-free.

    **Calendar alignment (2026-07-09, real-data bug found registering carry_composite/
    crowd_contra into AlphaCatalog for the first time, tools/register_alpha_catalog.py):**
    two real alphas routinely have DIFFERENT valid-period histories — different factors drop
    different rows depending on their own data availability (e.g. an OI-based signal's history
    starting ~1.5 years after a funding-based signal's). The original implementation compared
    candidates' period_returns by LIST POSITION (series[:min_len]) — silently comparing
    different calendar windows as if they were contemporaneous. Confirmed on real data: this
    forced a spurious PBO=1.0 (every CSCV split "flipped") purely because one candidate's early,
    non-overlapping history contained an outlier the other candidate had no data for at all —
    unrelated to genuine selection overfitting, and it dropped to 0.086 once the same unmodified
    pbo_cscv() was fed calendar-aligned returns instead.

    When every candidate's PanelPeriod carries `event_time`, this aligns all candidates to the
    INTERSECTION of their common event_times (sorted) before block-summing — an empty
    intersection (zero true overlap) honestly returns None rather than fabricating a number from
    calendar-disjoint data. When ANY candidate's periods lack event_time (legacy callers that
    hand-build PanelPeriod directly, e.g. some tests), falls back unchanged to the original
    positional-truncation behavior — a documented, backward-compatible limitation for
    un-timestamped candidates, not a regression for those callers.

    Coverage (2026-07-09, extended same day as the fix once a review flagged the initial fix as
    scoped too narrowly): EVERY real PanelPeriod-construction site now stamps event_time —
    services.research.alpha_expression.panel_to_periods (register_alpha_catalog.py's path) AND
    the three provider builders the autonomous ResearchAgentLoop/GuidedSearchLoop actually run on
    (services.research.{panel_builder.build_panel_periods (+ its volume-signal variant, +
    gp_tree.py which calls it verbatim), fundamental_factors.build_fundamental_periods,
    interaction_factors.build_interaction_periods}) AND the standalone tools/ drivers that hand-
    build PanelPeriod inline before a multi-candidate ResearchAgentLoop.run() batch (tools/
    {crypto_alpha_research.py, crypto_history_research.py}, plus the four single-candidate
    drivers — crypto_alpha_search/crypto_binance_research/crypto_funding_research/
    crypto_smallcap_research.py — that reuse crypto_alpha_research._periods_timed and were fixed
    for consistency even though a 1-candidate batch always gets pbo=None regardless).
    tools/crypto_daily_composite.py's two PanelPeriod sites are the one deliberate exception: that
    driver never touches AlphaFactory/ResearchAgentLoop/batch_pbo at all, so it is out of scope.
    """
    series: list[list[float]] = []
    times: list[list[int]] = []
    for _defn, periods in candidates:
        try:
            res = run_panel_backtest(periods, cost=cost, quantile=quantile, target_aum=target_aum)
        except Exception:
            return None
        rets = list(res.period_returns)
        series.append(rets)
        times.append([p.event_time for p in periods])
    if len(series) < 2:                       # CSCV selection bias needs >= 2 configs
        return None

    all_timestamped = all(
        len(ts) == len(rs) and all(t is not None for t in ts) for ts, rs in zip(times, series))
    if all_timestamped:
        common = set(times[0])
        for ts in times[1:]:
            common &= set(ts)
        common_sorted = sorted(common)
        by_time = [dict(zip(ts, rs)) for ts, rs in zip(times, series)]
        series = [[d[ct] for ct in common_sorted] for d in by_time]
    # else: legacy positional fallback — `series` is already the raw per-candidate return lists.

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
        pbo_batch = batch_pbo(
            candidates, cost=getattr(self.evaluator, "cost", CostModel()),
            quantile=getattr(self.evaluator, "quantile", 0.2),
            target_aum=getattr(self.evaluator, "target_aum", 1e7))
        report: dict[str, dict] = {}
        for defn, periods in candidates:
            self.catalog.register(defn)
            ev_in = evidence.get(defn.alpha_id, {})
            ev = self.evaluator.evaluate(
                defn.alpha_id, periods, as_of=timestamp, pbo=pbo_batch,
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
