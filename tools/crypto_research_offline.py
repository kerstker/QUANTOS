"""OFFLINE snapshot (re)generator — same research core as crypto_research_run, but reads the already-
ingested PIT store (data/cache/crypto.db) instead of fetching from the network. It exists so the read-only
console can run the REAL risk / PnL / validation engines on REAL per-factor return series (Phase 1B):
crypto_research_run persists factor METRICS only; this tool re-runs the (deterministic, offline) session and
ALSO persists each top factor's real per-period net return series under `factor_series`.

No network, no broker/SDK, no order/credential. Deterministic given the cached bars. Containment unchanged:
the agent can never reach APPROVED; auto_approved must stay 0.
"""
from __future__ import annotations

import json
import os
import time

from services.data.persistent_store import SqlitePITStore
from services.intelligence.search_space import HypothesisSpace
from services.research.session import run_research_session

from tools.crypto_research_run import (
    OPERATORS, WINDOWS, FWD_HORIZON, GRANULARITY, DAYS,
    EVO_GENERATIONS, EVO_POPULATION, EVO_ELITE, _build_factors, _round, _SNAPSHOT_PATH,
)

_DB_PATH = os.path.join("data", "cache", "crypto.db")
TOP_K_SERIES = 12          # persist real return series for the top-K factors (by net_sharpe)


def _reconstruct_panel(store: SqlitePITStore):
    """Instruments + sorted event_times straight from the cached bars (offline, no fetch)."""
    instruments = sorted(store.keys())
    far = 1 << 62
    event_times: set = set()
    for inst in instruments:
        for bar in store.fetch_bars(inst, 0, far):
            event_times.add(bar.event_time if hasattr(bar, "event_time") else bar["event_time"])
    return instruments, sorted(event_times)


def _factor_series(result: dict, factors: list, top_k: int = TOP_K_SERIES) -> dict:
    """Map the best factors (in net_sharpe order) that HAVE a stored series to their REAL per-period net
    return series (from the session's catalog-backed factor_returns), capped at top_k. Robust: walks the
    full ranked list and takes the first top_k WITH a series, so it never returns empty just because a few
    top entries lack one."""
    fr = result.get("factor_returns", {})
    series = {}
    for f in factors:                                  # already sorted by net_sharpe desc
        if len(series) >= top_k:
            break
        rs = fr.get(f["alpha_id"])
        if rs and len(rs) >= 2:
            series[f["alpha_id"]] = {"name": f["name"], "net_sharpe": f["net_sharpe"],
                                     "returns": [round(float(x), 8) for x in rs]}
    return series


def regenerate(now_ms: int) -> dict:
    if not os.path.exists(_DB_PATH):
        raise FileNotFoundError(f"{_DB_PATH} not found — run tools/crypto_research_run.py first to ingest data")
    store = SqlitePITStore(_DB_PATH, create=False)
    instruments, event_times = _reconstruct_panel(store)
    if len(instruments) < 2 or len(event_times) < 8:
        raise ValueError(f"cached store too thin: {len(instruments)} instruments, {len(event_times)} periods")
    # The store accumulates bars across runs, so the full grid is sparse (not every instrument has every
    # period). Use a recent contiguous window + cap the universe so every factor evaluates on a dense panel
    # (a sparse panel yields evaluations with no stored return stream -> no real series). Deterministic.
    MAX_INSTRUMENTS, RECENT_PERIODS = 18, 120
    instruments = instruments[:MAX_INSTRUMENTS]
    event_times = event_times[-RECENT_PERIODS:]

    # Phase 3: persist real immutable evaluation artifacts (+ DSR trial count) into a DEDICATED demo
    # registry so the Experiments / Alpha Zoo console pages read REAL recorded evidence, not synthetic.
    # Fresh each regen for a deterministic snapshot (drop any prior db first).
    #
    # 2026-07-09 (audit P0 fix, "AlphaCatalog governs zero real candidates"): this MUST be a file
    # distinct from data/cache/experiment_registry.db. That file is the real, durable production
    # AlphaCatalog (tools/register_alpha_catalog.py registers carry_composite/crowd_contra into it;
    # services/governance/promotion_api/main.py reads it for actual human promotion decisions) — before
    # this fix, this function's own os.remove()+rebuild silently wiped ANY real registration the moment
    # someone re-ran this demo-panel regenerator, with zero warning. Demo/exploration output and the real
    # production catalog must never share physical storage.
    from services.research.experiment_registry.store import SqliteExperimentStore
    from services.research.experiment_registry.registry import ExperimentRegistry
    exp_db = os.path.join("data", "cache", "crypto_research_demo_registry.db")
    if os.path.exists(exp_db):
        os.remove(exp_db)
    registry = ExperimentRegistry(SqliteExperimentStore(exp_db, create=True))

    space = HypothesisSpace(factors=("close",), operators=OPERATORS, windows=WINDOWS)
    result = run_research_session(
        store, instruments, event_times=event_times, fwd_horizon=FWD_HORIZON,
        space=space, rounds=1, batch_size=1, timestamp=now_ms, registry=registry,
        evolutionary=True, evo_generations=EVO_GENERATIONS,
        evo_population=EVO_POPULATION, evo_elite=EVO_ELITE)

    factors = _build_factors(result["report"])
    factor_series = _factor_series(result, factors)
    auto_approved = sum(1 for f in factors if f["state"] == "APPROVED")
    assert auto_approved == 0, "containment breach: a factor auto-approved (must stay 0)"
    rejected = sum(1 for f in factors if str(f["state"]) == "REJECTED")
    net_sharpes = [f["net_sharpe"] for f in factors if f["net_sharpe"] is not None]
    deflated = [f["deflated_sharpe"] for f in factors if f["deflated_sharpe"] is not None]

    snapshot = {
        "generated_at": now_ms // 1000,
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ms / 1000)),
        "dataset_version": result["dataset_version"],
        "meta": {
            "provider": "OFFLINE re-run of cached CoinGecko OHLC (data/cache/crypto.db)",
            "asset_class": "CRYPTO", "vs_currency": "usd", "days": DAYS, "granularity": GRANULARITY,
            "fwd_horizon": FWD_HORIZON, "n_instruments": len(instruments), "n_periods": len(event_times),
            "coins": [{"instrument_id": i} for i in instruments], "dropped": [], "n_dropped": 0,
            "search_mode": result["mode"], "evo_generations": EVO_GENERATIONS,
            "evo_population": EVO_POPULATION, "evo_elite": EVO_ELITE,
            "operators": list(OPERATORS), "windows": list(WINDOWS), "regen": "offline",
        },
        "summary": {
            "n_evaluated": result["n_evaluated"], "trial_count": result["n_evaluated"],
            "auto_approved": auto_approved, "requires_human_review": len(result.get("human_review_queue", [])),
            "rejected": rejected,
            "max_net_sharpe": max(net_sharpes) if net_sharpes else None,
            "max_deflated_sharpe": max(deflated) if deflated else None,
            "top_alpha_id": factors[0]["alpha_id"] if factors else None,
        },
        "factors": factors,
        "factor_series": factor_series,           # NEW: real per-period net return series for the top factors
    }
    with open(_SNAPSHOT_PATH, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2)
    print(f"offline regen: {len(instruments)} instruments x {len(event_times)} periods -> "
          f"{len(factors)} factors, {len(factor_series)} with real series (auto_approved={auto_approved}) "
          f"-> wrote {_SNAPSHOT_PATH}")
    return snapshot


def main() -> None:
    regenerate(int(time.time() * 1000))


if __name__ == "__main__":
    main()
