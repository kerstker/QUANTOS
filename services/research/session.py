"""Reproducible research session orchestrator (#2 remainder).

One deterministic call that composes the integrated subsystems: derive a
dataset_version from the persisted bars, optionally rehydrate the catalog from the
experiment registry (cross-session multiple-testing state), run the guided search
on REAL persisted-data PanelPeriods through the UNCHANGED guardrailed path (the
agent still can never reach APPROVED), record each evaluation as an immutable
registry artifact keyed by dataset_version, and persist the evolved catalog.

Composes existing entry points; changes none of them. Pure-Python, deterministic,
fail-closed, no new authority. (validation/pipeline.py is a separate single-
instrument validation tool, not consolidated here.)"""
from __future__ import annotations

from typing import Optional, Sequence

from services.alpha.catalog import AlphaCatalog
from services.data.versioning import content_hash_of
from services.intelligence.agent_loop import ResearchAgentLoop
from services.intelligence.guided_search import GuidedSearchLoop

from .panel_builder import make_data_periods_provider, universe_data_gaps


def _derive_dataset_version(store, instruments: Sequence[str], event_times: Sequence[int]) -> str:
    if not event_times:
        return ""
    lo, hi = min(event_times), max(event_times)
    bars = [b for inst in instruments for b in store.fetch_bars(inst, lo, hi)]
    return content_hash_of(bars) if bars else ""


def run_research_session(store, instruments: Sequence[str], *, event_times: Sequence[int],
                         fwd_horizon: int, space, rounds: int, batch_size: int,
                         timestamp: int = 0, registry=None,
                         dataset_version: Optional[str] = None, audit_log=None, universe=None) -> dict:
    if dataset_version is None:
        dataset_version = _derive_dataset_version(store, instruments, event_times)

    catalog = AlphaCatalog()
    if registry is not None:
        registry.load_into(catalog)          # cross-session trial count + var_sr (fail-closed)

    loop = GuidedSearchLoop(space, inner_loop=ResearchAgentLoop(catalog=catalog))
    if registry is not None:
        registry.load_bandit_into(loop.strategy)   # the agent's learning accumulates across sessions
    provider = make_data_periods_provider(store, instruments, event_times=event_times,
                                          fwd_horizon=fwd_horizon, universe=universe)
    out = loop.search(rounds, batch_size, provider, timestamp=timestamp)

    # honesty signal: members with no bar on a trading day (survivorship-free run only)
    data_gaps = universe_data_gaps(store, universe, event_times) if universe is not None \
        else {"total_gaps": 0, "by_date": {}}

    recorded = []
    if registry is not None:
        for _alpha_id, entry in sorted(out["report"].items()):
            ev = entry.get("evaluation")
            if ev is not None:
                recorded.append(registry.record_evaluation(
                    ev, dataset_version=dataset_version, recorded_ts=timestamp, audit_log=audit_log))
        registry.save(catalog, now=timestamp)
        registry.save_bandit(loop.strategy)         # persist the learned arm statistics

    return {
        "dataset_version": dataset_version,
        "arm_means": out["arm_means"],
        "human_review_queue": out["human_review_queue"],
        "recorded_run_ids": recorded,
        "n_evaluated": len(out["report"]),
        "report": out["report"],
        "data_gaps": data_gaps,
    }
