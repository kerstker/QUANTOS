"""End-to-end Alpha pipeline (Alpha Factory upgrade — main-line wiring).

Threads the Phase 1-7 modules (built and unit-tested in isolation) into ONE contained research-to-
portfolio flow over a cross-sectional panel:

  cs expression
    -> compute leakage_ok           (P0  expression_leakage — COMPUTED, not trusted/None)
    -> evaluate                     (P2  AlphaEvaluator: IC battery, net backtest, catalog-wide DSR)
    -> rich AlphaAsset + registry   (P3  AlphaAsset / AlphaAssetRegistry)
    -> Research Memory note         (P7  ResearchMemory)
    ... over all alphas ...
    -> population optimizer         (P4  optimize_population: de-redundant set + weights)
    -> Signal -> Portfolio book     (P5  signal_to_portfolio: dollar-neutral target weights)
    -> auto research brief          (P7  compose_brief)

It drives NO lifecycle transition — it produces evaluations, a portfolio and a brief, so it can never
promote an alpha (APPROVED stays a separate human-only lifecycle action). RESEARCH / DRY-RUN ONLY.
Pure-Python stdlib, deterministic (caller supplies the timestamp).
"""
from __future__ import annotations

import math
from typing import Mapping, Optional, Sequence

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.alpha_asset import AlphaAsset
from services.alpha.catalog import AlphaCatalog
from services.alpha.evaluator import AlphaEvaluator
from services.alpha.population_optimizer import optimize_population
from services.alpha.lineage import make_child_asset
from services.alpha.registry import AlphaAssetRegistry
from services.portfolio.signal_adapter import SignalAdapterConfig, signal_to_portfolio

from services.research.backtest_engine.metrics import sharpe

from . import alpha_expression as ax
from .carry_pnl import economic_return_series
from .expression_leakage import compute_leakage_ok
from .research_brief import compose_brief
from .research_memory import ResearchMemory, ResearchNote


def _fields(expr, acc: Optional[set] = None) -> set:
    """Field-name leaves an expression depends on (its data dependencies)."""
    if acc is None:
        acc = set()
    if isinstance(expr, tuple):
        if expr and expr[0] == "field":
            acc.add(expr[1])
        else:
            for a in expr[1:]:
                if isinstance(a, tuple):
                    _fields(a, acc)
    return acc


def run_alpha_asset_pipeline(panel: ax.FieldPanel, exprs: Sequence, *, fwd_horizon: int = 1,
                             dataset_version: str = "", timestamp: int = 0, generator: str = "cs_genome",
                             max_k: int = 8, min_periods: int = 2,
                             adapter_config: Optional[SignalAdapterConfig] = None,
                             bred_by: Optional[Mapping[str, tuple]] = None) -> dict:
    """Run the contained research-to-portfolio pipeline over `exprs` (nested-tuple cs alphas) on `panel`.
    Returns the evaluated assets (with COMPUTED leakage_ok), the populated registry + memory, the
    optimized portfolio, the Signal->Portfolio target book, and the auto research brief."""
    catalog = AlphaCatalog()
    evaluator = AlphaEvaluator(catalog)
    registry = AlphaAssetRegistry()
    memory = ResearchMemory()
    cfg = adapter_config or SignalAdapterConfig(max_position=0.5)

    has_funding = "funding" in panel.matrices
    expr_by_id: dict = {}
    asset_rows: list = []
    eco_returns_by_id: dict = {}
    proven = leaked = unassessed = 0

    for expr in exprs:
        aid = ax.alpha_id(expr)
        if aid in expr_by_id:
            continue                                   # dedup identical expressions
        leak_ok = compute_leakage_ok(expr, panel)      # P0 — True / False / None
        proven += leak_ok is True
        leaked += leak_ok is False
        unassessed += leak_ok is None

        matrix = ax.evaluate(expr, panel)
        periods = ax.panel_to_periods(matrix, panel, fwd_horizon=fwd_horizon)
        deps = tuple(sorted(_fields(expr)))
        defn = AlphaDefinition(alpha_id=aid, name=aid, factor_ids=deps, universe="cs")
        catalog.register(defn)

        if len(periods) < min_periods:
            ev = None
        else:
            ev = evaluator.evaluate(aid, periods, leakage_ok=leak_ok, as_of=timestamp,
                                    dataset_version=dataset_version)

        # ECONOMIC (carry-aware) return: price_LS + funding_collected when the panel carries funding.
        # This is the "money" series — the perp book's real yield, not just the price-prediction Sharpe.
        eco = economic_return_series(matrix, panel, fwd_horizon=fwd_horizon) if has_funding else []
        if len(eco) >= 2:
            eco_returns_by_id[aid] = eco

        # 谱系接线(P3-4):育种子代(cs_genome mutate/crossover)用 make_child_asset 记 parent_ids/
        # mutation_op/perf_delta;非育种(seed / 外部 exprs)维持 from_definition(lineage 空,诚实)。
        # bred_by=None -> 全走 from_definition,与改动前逐字节相同。
        if bred_by and aid in bred_by:
            parent_ids, op = bred_by[aid]
            parents_in_scope = [p for p in (registry.get(pid) for pid in parent_ids) if p is not None]
            asset = make_child_asset(
                alpha_id=aid, expression=ax.serialize(expr), parents=parents_in_scope,
                parent_ids=parent_ids, mutation_op=op, evaluation=ev, generator=generator,
                name=aid, universe="cs", data_dependencies=deps, dataset_version=dataset_version)
        else:
            asset = AlphaAsset.from_definition(
                defn, evaluation=ev, generator=generator, expression=ax.serialize(expr),
                data_dependencies=deps, dataset_version=dataset_version)
        registry.add(asset, ts=timestamp)
        memory.record(ResearchNote.from_asset(asset, hypothesis=ax.serialize(expr)))
        expr_by_id[aid] = expr
        asset_rows.append({"alpha_id": aid, "leakage_ok": leak_ok,
                           "verdict": ev.verdict.value if ev is not None else "",
                           "net_sharpe": ev.net_sharpe if ev is not None else None,
                           "economic_sharpe": round(sharpe(eco), 4) if len(eco) >= 2 else None})

    # P4 — de-redundant set + weights. Optimize over the ECONOMIC returns when funding is present
    # (so the portfolio is built on real carry yield), else the evaluator's price-only net returns.
    returns_by_id = {aid: catalog.returns(aid) for aid in expr_by_id
                     if len(catalog.returns(aid)) >= 2}
    opt_returns = eco_returns_by_id or returns_by_id
    portfolio = (optimize_population(opt_returns, max_k=max_k)
                 if opt_returns else {"selected_ids": [], "weights": {}})

    # P5 — blend the selected alphas' latest cross-section into one signal -> target book
    target_book = _selected_signal_to_book(panel, expr_by_id, portfolio.get("weights", {}), cfg)

    # P7 — brief over the run's notes
    brief = compose_brief(memory.all())

    return {
        "n_evaluated": len(asset_rows),
        "assets": asset_rows,
        "registry": registry,
        "memory": memory,
        "returns_by_id": returns_by_id,             # per-alpha PRICE-only net returns (evaluator)
        "economic_returns_by_id": eco_returns_by_id,  # per-alpha price + funding-collected (real carry)
        "portfolio": portfolio,
        "target_book": target_book,
        "brief": brief,
        "leakage_summary": {"proven": proven, "leaked": leaked, "unassessed": unassessed},
    }


def _selected_signal_to_book(panel: ax.FieldPanel, expr_by_id: dict, weights: dict,
                             cfg: SignalAdapterConfig) -> dict:
    """Weighted blend of the selected alphas' MOST RECENT cross-section -> a dollar-neutral target book
    via the Signal->Portfolio adapter. The adapter winsorizes + z-scores, so mixed signal scales are
    standardized before sizing."""
    if not weights:
        return signal_to_portfolio({}, config=cfg)
    last = len(panel.event_times) - 1
    mats = {aid: ax.evaluate(expr_by_id[aid], panel) for aid in weights}
    composite: dict = {}
    for i, inst in enumerate(panel.instruments):
        total, seen = 0.0, False
        for aid, w in weights.items():
            v = mats[aid][last][i]
            if v is not None and math.isfinite(v):
                total += w * v
                seen = True
        if seen:
            composite[inst] = total
    return signal_to_portfolio(composite, config=cfg)
