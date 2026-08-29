"""Out-of-gate, OFFLINE-core COMPREHENSIVE cross-sectional alpha search over price + volume + funding.

Instead of 8 hand-picked alphas, systematically GENERATE several hundred cross-sectional expressions
(fields {close, volume, funding} x cs ops {rank, zscore} ∘ ts ops {delta, mean, decay, std, ts_rank}
x windows x sign x a few interactions) and evaluate them all on the 115-perp multi-year panel. The
DECISIVE, multiple-testing-honest metric is the NO-PEEK walk-forward SELECTION OOS over the WHOLE pool:
it selects the best on each TRAIN window and measures on the unseen TEST window, so "best-of-N" overfit
is priced in by construction. If that OOS is positive, the search found real signal; if negative, the
machine correctly says "selecting the best of hundreds is noise." Also reports the raw top + funding's
share of the top + the contained DSR on the top few (DSR should be read as deflated by the FULL N searched).

Funding is cached to data/cache/crypto_funding_rates.json (re-used across runs). Read-only public DATA;
auto_approved must stay 0. Reports ONLY observed numbers.
"""
from __future__ import annotations

import json
import os
import time

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.data.research_adapter import adapt_research_market_data
from services.execution.backtest_authority import quarantine_legacy_backtest
from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe
from services.research.oos_validation import walk_forward_selection_oos, full_sample_best
from services.alpha.panel_backtest import run_panel_backtest
from services.alpha.config import AlphaFactoryConfig
from services.intelligence.registered_candidate_batch import (
    run_registered_candidate_batch,
)
from services.research.experiment_registry.registry import ExperimentRegistry
from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from tools.binance_funding_fetch import fetch_daily_funding, validate_daily_funding
from tools.cache_io import atomic_write_json
from tools.crypto_alpha_research import _periods_timed, _per_year
from tools.search_trial_registry import (
    make_search_id,
    production_experiment_registry,
)

_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_FUND_CACHE = os.path.join("data", "cache", "crypto_funding_rates.json")
_OUT = os.path.join("data", "cache", "crypto_alpha_search_snapshot.json")
_DAY_MS = 86_400_000

_F = {"close": ("field", "close"), "volume": ("field", "volume"), "funding": ("field", "funding")}
_W = (3, 7, 14, 30)
_TS = {"delta": "ts_delta", "mean": "ts_mean", "decay": "decay_linear", "std": "ts_std", "tsrank": "ts_rank"}
_CS = {"rank": "cs_rank", "z": "cs_zscore"}


def generate_alphas() -> dict:
    out = {}
    for fn, fe in _F.items():
        for cn, co in _CS.items():
            out[f"{cn}_{fn}"] = (co, fe)
            out[f"{cn}_neg_{fn}"] = (co, ("neg", fe))
            for w in _W:
                for tn, to in _TS.items():
                    inner = (to, fe, w)
                    out[f"{cn}_{tn}{w}_{fn}"] = (co, inner)
                    out[f"{cn}_neg_{tn}{w}_{fn}"] = (co, ("neg", inner))
    # cross-field interactions (funding x price), the kind only this engine can express
    for w in (5, 10, 20):
        nf = ("neg", _F["funding"])
        out[f"rank_fundXmom{w}"] = ("cs_rank", ("mul", nf, ("ts_delta", _F["close"], w)))
        out[f"rank_fundXrev{w}"] = ("cs_rank", ("mul", nf, ("neg", ("ts_delta", _F["close"], w))))
        out[f"rank_fundXvol{w}"] = ("cs_rank", ("mul", nf, ("ts_std", _F["close"], w)))
    return out


def _load_funding(instruments, now_ms, *, fetch=fetch_daily_funding,
                  cache_path=_FUND_CACHE, sleep=0.1):
    expected_end = (now_ms // _DAY_MS) * _DAY_MS - _DAY_MS
    if os.path.exists(cache_path):
        cached = json.load(open(cache_path, encoding="utf-8"))
        funding = {}
        for ticker in instruments:
            raw = cached.get(ticker, {}) if isinstance(cached, dict) else {}
            daily = {int(day): value for day, value in raw.items()}
            validate_daily_funding(ticker, daily, expected_end=expected_end)
            funding[ticker] = daily
        return funding
    print(f"fetching funding for {len(instruments)} perps (one-time, then cached)...", flush=True)
    funding = {}
    for i, tk in enumerate(instruments):
        daily = fetch(tk, days=2200, now_ms=now_ms)
        validate_daily_funding(tk, daily, expected_end=expected_end)
        funding[tk] = daily
        if (i + 1) % 30 == 0:
            print(f"  {i+1}/{len(instruments)}", flush=True)
        if sleep:
            time.sleep(sleep)
    atomic_write_json(
        cache_path,
        {tk: {str(k): v for k, v in d.items()} for tk, d in funding.items()},
    )
    return funding


def _run_registered_pool(
    candidates,
    *,
    registry: ExperimentRegistry,
    search_id: str,
    dataset_version: str,
    recorded_at: int,
):
    return run_registered_candidate_batch(
        candidates,
        registry=registry,
        search_id=search_id,
        search_kind="CRYPTO_ALPHA_EXPRESSION_SEARCH",
        search_source="tools.crypto_alpha_search",
        dataset_version=dataset_version,
        recorded_at=recorded_at,
        config=AlphaFactoryConfig(compute_regime_evidence=True),
    )


def _main(registry: ExperimentRegistry) -> None:
    quarantine_legacy_backtest("tools.crypto_alpha_search")
    instruments = sorted(json.load(open(_SNAP, encoding="utf-8"))["instruments"])
    now_ms = int(time.time() * 1000)
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    event_times = sorted({b.event_time for inst in instruments
                          for b in store.fetch_bars(inst, -10**18, 10**18)})
    funding = _load_funding(instruments, now_ms)

    t0 = time.perf_counter()
    panel = ax.build_field_panel(
        store,
        instruments,
        event_times,
        ("close", "volume"),
        decision_times=[t + 86_400_000 for t in event_times],
        minimum_knowledge_lag=86_400_000,
    )
    panel.matrices["funding"] = [[funding.get(inst, {}).get(t) for inst in panel.instruments]
                                 for t in panel.event_times]
    alphas = generate_alphas()
    print(f"{len(instruments)} perps x {len(event_times)} periods; panel {time.perf_counter()-t0:.1f}s; "
          f"generated {len(alphas)} candidate alphas", flush=True)

    candidate_rows = []
    seen_ids = set()
    for name, expr in alphas.items():
        aid = ax.alpha_id(expr)
        if aid in seen_ids:                          # canonical dedup (e.g. commutative twins)
            continue
        seen_ids.add(aid)
        try:
            m = ax.evaluate(expr, panel)
            periods, times = _periods_timed(m, panel)
        except Exception:
            continue
        if len(periods) < 50:
            continue
        definition = AlphaDefinition(
            alpha_id=aid,
            name=name,
            factor_ids=("close",),
            params={"family": "search", "expr": ax.serialize(expr)},
        )
        candidate_rows.append((name, expr, tuple(periods), tuple(times), definition))

    bar_version = adapt_research_market_data(store).dataset_version(
        instruments,
        event_times,
    )
    funding_version = stable_hash(
        {
            ticker: tuple(sorted(daily.items()))
            for ticker, daily in sorted(funding.items())
        }
    )
    dataset_version = stable_hash(
        {
            "bars": bar_version,
            "funding": funding_version,
            "instruments": tuple(instruments),
            "event_times": tuple(event_times),
        }
    )
    batch_result = _run_registered_pool(
        tuple(
            (definition, periods)
            for _name, _expr, periods, _times, definition in candidate_rows
        ),
        registry=registry,
        search_id=make_search_id(
            source="tools.crypto_alpha_search",
            search_kind="CRYPTO_ALPHA_EXPRESSION_SEARCH",
            dataset_version=dataset_version,
            recorded_at=now_ms,
            params={"candidate_count": len(candidate_rows)},
        ),
        dataset_version=dataset_version,
        recorded_at=now_ms,
    )
    report = batch_result["report"]

    # Selection and all display rankings happen only after the complete pool is
    # durable in search_candidates and the global trial population.
    pool, full = {}, {}
    definitions = {}
    for name, expr, periods, times, definition in candidate_rows:
        ret = list(run_panel_backtest(periods).period_returns)
        pool[name] = ret
        full[name] = (round(sharpe(ret), 3), expr, times)
        definitions[name] = definition
    print(f"evaluable distinct alphas: {len(pool)}", flush=True)

    # DECISIVE: no-peek selection OOS over the WHOLE pool (multiple-testing-honest by construction)
    L = min(len(v) for v in pool.values())
    aligned = {k: v[-L:] for k, v in pool.items()}
    sel = walk_forward_selection_oos(aligned, train_size=max(60, L // 2), test_size=max(20, L // 10),
                                     step=max(20, L // 10), embargo=1, top_k=1)
    bid, bsr = full_sample_best(aligned)

    ranked = sorted(full.items(), key=lambda kv: kv[1][0], reverse=True)
    # Agent evaluations were run on the complete registered pool. The top 15 is
    # presentation only and cannot shrink the multiple-testing denominator.
    top_rows = []
    for name, (fsr, expr, times) in ranked[:15]:
        definition = definitions[name]
        ev = report.get(definition.alpha_id, {}).get("evaluation")
        py = _per_year(times, pool[name])
        top_rows.append((name, fsr, round(ev.deflated_sharpe, 4) if ev else None,
                         round(ev.ic_mean, 4) if ev else None,
                         sum(1 for v in py.values() if v > 0), len(py), py))
    auto = sum(
        1
        for entry in report.values()
        if entry.get("state") == "APPROVED"
    )

    atomic_write_json(
        _OUT,
        {"generated_at": now_ms // 1000, "n_instruments": len(instruments),
         "n_periods": len(event_times), "n_generated": len(alphas), "n_evaluable": len(pool),
         "selection_oos": sel, "naive_best": {"alpha_id": bid, "sharpe": round(bsr, 4)},
         "auto_approved": auto},
        indent=2,
    )

    print(f"\n=== COMPREHENSIVE SEARCH: {len(pool)} distinct alphas, {len(instruments)} perps x {len(event_times)} periods ===")
    print(f"auto_approved={auto} (must be 0)")
    print(f"--- DECISIVE no-peek SELECTION OOS over ALL {len(pool)} (best-of-N overfit priced in) ---")
    print(f"  mean IS {sel['mean_is_sharpe']}  mean OOS {sel['mean_oos_sharpe']}  OOS+ {sel['oos_positive_frac']} "
          f"({sel['n_folds']} folds)  naive-best(peeking) {bid} {round(bsr,3)}")
    print("--- top 15 by raw full Sharpe (SELECTION-INFLATED — not the honest number) ---")
    print(f"{'alpha':22s} {'fullSR':>7} {'DSR':>7} {'IC':>8} {'+yrs':>6}")
    for name, fsr, dsr, ic, posy, ny, py in top_rows:
        ds = f"{dsr:.3f}" if dsr is not None else "n/a"
        ics = f"{ic:.4f}" if ic is not None else "n/a"
        print(f"{name:22s} {fsr:>7.3f} {ds:>7} {ics:>8} {posy:>3}/{ny:<2}")
    print(f"-> wrote {_OUT}")


def main() -> None:
    quarantine_legacy_backtest("tools.crypto_alpha_search")
    with production_experiment_registry() as registry:
        _main(registry)


if __name__ == "__main__":
    main()
