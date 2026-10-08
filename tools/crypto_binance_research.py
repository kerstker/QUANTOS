"""Out-of-gate, OFFLINE-core WIDE cross-sectional alpha research on the Binance public-data universe.

The whole question: does the system make money once the cross-section is WIDE enough to reach the
inefficient tail? Binance public klines give multi-year daily for ~437 USDT pairs, free, no key — an
order of magnitude more breadth than the rate-limited CryptoCompare path. This driver pulls that whole
universe (minus stablecoins / wrapped / too-short / pegged), builds the field panel, and runs the SAME
curated cross-sectional alphas through the durable registered-candidate composition, with
per-CALENDAR-YEAR net Sharpe (incl. 2022 bear) and a long-sample no-peek OOS.

Read-only public DATA only (no key/account/order/SDK). auto_approved must stay 0. Honest caveats stand:
(a) survivorship bias — Binance lists today's surviving pairs, dead coins absent (biases UP);
(b) small-cap trading costs are large — a gross edge may not survive the cost model (which we measure).
"""
from __future__ import annotations

import json
import os
import sys
import time

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.data.market_provider import ingest_provider_rows
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
from tools.binance_klines_fetch import fetch_usdt_symbols, fetch_daily_rows
from tools.crypto_alpha_research import ALPHAS, FWD, _periods_timed, _per_year
from tools.crypto_smallcap_research import _BLOCK, _ret_vol
from tools.search_trial_registry import (
    field_panel_dataset_version,
    make_search_id,
    production_experiment_registry,
)

_DB = os.path.join("data", "cache", "crypto_binance.db")
_SNAP = os.path.join("data", "cache", "crypto_binance_snapshot.json")
_MIN_BARS = 400
_MIN_VOL = 0.01
_SEARCH_KIND = "CRYPTO_BINANCE_CURATED_ALPHA_RESEARCH"
_SEARCH_SOURCE = "tools.crypto_binance_research"


def _run_registered_panel(
    panel,
    *,
    registry: ExperimentRegistry,
    recorded_at: int,
):
    candidate_rows = []
    for name, expr in ALPHAS.items():
        alpha = ax.evaluate(expr, panel)
        periods, times = _periods_timed(alpha, panel)
        if len(periods) < 24:
            continue
        definition = AlphaDefinition(
            alpha_id=ax.alpha_id(expr),
            name=name,
            factor_ids=("close",),
            params={"family": "cs_alpha", "expr": ax.serialize(expr)},
        )
        candidate_rows.append(
            (name, expr, tuple(periods), tuple(times), definition)
        )

    dataset_version = field_panel_dataset_version(
        panel,
        source_versions={"forward_horizon": FWD},
    )
    batch_result = run_registered_candidate_batch(
        tuple(
            (definition, periods)
            for _name, _expr, periods, _times, definition in candidate_rows
        ),
        registry=registry,
        search_id=make_search_id(
            source=_SEARCH_SOURCE,
            search_kind=_SEARCH_KIND,
            dataset_version=dataset_version,
            recorded_at=recorded_at,
            params={"candidate_count": len(candidate_rows), "forward_horizon": FWD},
        ),
        search_kind=_SEARCH_KIND,
        search_source=_SEARCH_SOURCE,
        dataset_version=dataset_version,
        recorded_at=recorded_at,
        config=AlphaFactoryConfig(compute_regime_evidence=True),
    )
    return tuple(candidate_rows), batch_result, dataset_version


def _main(registry: ExperimentRegistry) -> None:
    quarantine_legacy_backtest("tools.crypto_binance_research")
    cap = int(sys.argv[1]) if len(sys.argv) > 1 else 0     # 0 = all USDT pairs
    now_ms = int(time.time() * 1000)
    os.makedirs(os.path.join("data", "cache"), exist_ok=True)
    syms = fetch_usdt_symbols()
    if cap:
        syms = syms[:cap]
    print(f"Binance USDT universe: {len(syms)} pairs", flush=True)

    store = SqlitePITStore(_DB, create=True)
    instruments, all_ets, dropped, failed = [], set(), 0, 0
    for tk in syms:
        if tk in _BLOCK:
            dropped += 1
            continue
        try:
            rows = fetch_daily_rows(tk, days=2000, now_ms=now_ms)
        except Exception:
            failed += 1
            continue
        if not rows or len(rows) < _MIN_BARS or _ret_vol(rows) < _MIN_VOL:
            dropped += 1
            continue
        ingest_provider_rows(rows, store, dataset_id=f"bn-{tk}", now=now_ms, time_unit="ms")
        instruments.append(tk)
        all_ets.update(r["event_time"] for r in rows)
        if len(instruments) % 25 == 0:
            print(f"  ingested {len(instruments)} (dropped {dropped}, failed {failed})...", flush=True)
        time.sleep(0.15)

    event_times = sorted(all_ets)
    if len(instruments) < 10:
        raise SystemExit(f"only {len(instruments)} usable coins — unexpected")
    store = CachingPITStore(store)
    t0 = time.perf_counter()
    panel = ax.build_field_panel(
        store,
        instruments,
        event_times,
        ("close", "volume"),
        decision_times=[t + 86_400_000 for t in event_times],
        # Existing DBs may still contain the old open-time knowledge rows.
        # Reject them even when corrected vintages coexist in the same store.
        minimum_knowledge_lag=86_400_000,
    )
    print(f"\n{len(instruments)} coins x {len(event_times)} daily periods "
          f"(dropped {dropped}, failed {failed}); panel built {time.perf_counter()-t0:.1f}s", flush=True)

    candidate_rows, batch_result, _dataset_version = _run_registered_panel(
        panel,
        registry=registry,
        recorded_at=now_ms,
    )
    report = batch_result["report"]
    rows_out, pool_ret = [], {}
    for name, _expr, periods, times, definition in candidate_rows:
        ret = list(run_panel_backtest(periods).period_returns)
        pool_ret[name] = ret
        full = round(sharpe(ret), 3)
        py = _per_year(times, ret)
        ev = report.get(definition.alpha_id, {}).get("evaluation")
        rows_out.append({"name": name, "full_sharpe": full,
                         "deflated": round(ev.deflated_sharpe, 4) if ev else None,
                         "ic": round(ev.ic_mean, 4) if ev else None,
                         "pos_years": sum(1 for v in py.values() if v > 0), "n_years": len(py),
                         "per_year": py, "verdict": ev.verdict.value if ev else "n/a"})

    oos = None
    aligned = {k: v for k, v in pool_ret.items() if len(v) >= 2}
    if len(aligned) >= 2:
        L = min(len(v) for v in aligned.values())
        al = {k: v[-L:] for k, v in aligned.items()}
        sel = walk_forward_selection_oos(al, train_size=max(40, L // 2), test_size=max(20, L // 10),
                                         step=max(20, L // 10), embargo=1, top_k=1)
        bid, bsr = full_sample_best(al)
        oos = {"n_aligned": L, "selection": sel, "naive_best": {"alpha_id": bid, "sharpe": round(bsr, 4)}}

    rows_out.sort(key=lambda r: r["full_sharpe"], reverse=True)
    auto = sum(1 for entry in report.values() if entry.get("state") == "APPROVED")
    snap = {"generated_at": now_ms // 1000, "provider": "Binance public klines (wide cross-section)",
            "n_instruments": len(instruments), "n_periods": len(event_times), "instruments": instruments,
            "auto_approved": auto, "alphas": rows_out, "oos": oos}
    with open(_SNAP, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=2)

    print(f"\n=== WIDE cross-sectional alphas (Binance): {len(instruments)} coins x {len(event_times)} periods ===")
    print(f"auto_approved={auto} (must be 0)")
    print(f"{'alpha':15s} {'fullSR':>7} {'DSR':>7} {'IC':>8} {'+yrs':>6}   per-year")
    for r in rows_out:
        dsr = f"{r['deflated']:.3f}" if r["deflated"] is not None else "n/a"
        ic = f"{r['ic']:.4f}" if r["ic"] is not None else "n/a"
        pys = " ".join(f"{y}:{v:+.1f}" for y, v in r["per_year"].items())
        print(f"{r['name']:15s} {r['full_sharpe']:>7.3f} {dsr:>7} {ic:>8} {r['pos_years']:>3}/{r['n_years']:<2}  {pys}")
    if oos:
        s = oos["selection"]
        print(f"\n[no-peek OOS] mean IS {s['mean_is_sharpe']}  mean OOS {s['mean_oos_sharpe']}  "
              f"OOS+ {s['oos_positive_frac']} ({s['n_folds']} folds)  naive-best {oos['naive_best']}")
    survivors = [r["name"] for r in rows_out if r["full_sharpe"] > 0 and r["pos_years"] >= max(1, r["n_years"] - 1)]
    print(f"cross-regime survivors (full +ve AND +ve in all-but-one year): {survivors or 'NONE'}")
    print(f"-> wrote {_SNAP}")


def main() -> None:
    quarantine_legacy_backtest("tools.crypto_binance_research")
    with production_experiment_registry() as registry:
        _main(registry)


if __name__ == "__main__":
    main()
