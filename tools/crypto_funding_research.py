"""Out-of-gate, OFFLINE-core cross-sectional research on PRICE + FUNDING-RATE (an alt-data dimension).

Price+volume daily showed no cross-regime edge even across 336 coins. Funding rate is ORTHOGONAL data —
the perp long/short carry, a direct read of leverage/positioning. This driver pulls Binance public klines
(price) AND public funding history for the USDT-perp universe, builds the field panel with an extra
`funding` matrix, and runs FUNDING-based + funding×price cross-sectional alphas through the UNCHANGED
contained loop, with per-CALENDAR-YEAR net Sharpe (incl. 2022 bear) and a long-sample no-peek OOS.

Read-only public DATA only (no key/account/order/SDK). auto_approved must stay 0. Honest caveats: funding
carry is a documented-but-crowded crypto effect; survivorship bias (today's surviving perps); perp trading
costs (fees + funding paid) are real — measured by the cost model. Reports ONLY observed numbers.
"""
from __future__ import annotations

import os
import sys
import time

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.data.market_provider import ingest_provider_rows
from services.data.raw_archive import DEFAULT_RAW_ARCHIVE
from services.data.research_adapter import adapt_research_market_data
from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.oos_validation import walk_forward_selection_oos, full_sample_best
from services.alpha.panel_backtest import run_panel_backtest
from services.alpha.config import AlphaFactoryConfig
from services.intelligence.registered_candidate_batch import (
    run_registered_candidate_batch,
)
from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import stable_hash
from tools.binance_klines_fetch import fetch_daily_rows
from tools.binance_funding_fetch import (fetch_perp_symbols, fetch_daily_funding,
                                         validate_daily_funding)
from tools.cache_io import atomic_write_json
from tools.crypto_alpha_research import _periods_timed, _per_year
from tools.crypto_smallcap_research import _BLOCK, _ret_vol
from tools.search_trial_registry import (
    field_panel_dataset_version,
    make_search_id,
    production_experiment_registry,
)

_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_FUND_CACHE = os.path.join("data", "cache", "crypto_funding_rates.json")
_DAY_MS = 86_400_000
_MIN_BARS = 400
_MIN_VOL = 0.01

C, FUND = ("field", "close"), ("field", "funding")
# funding-based + funding×price cross-sectional alphas (none expressible without the funding dimension).
# carry convention: NEGATIVE funding => you are PAID to hold long, so signal = -funding (long high signal).
ALPHAS = {
    "fund_carry":    ("cs_rank", ("neg", FUND)),
    "fund_carry_z":  ("cs_zscore", ("neg", FUND)),
    "fund_avg7":     ("cs_rank", ("neg", ("ts_mean", FUND, 7))),
    "fund_mom3":     ("cs_rank", ("ts_delta", FUND, 3)),
    "fund_decay7":   ("cs_zscore", ("decay_linear", ("neg", FUND), 7)),
    "fund_x_pxmom":  ("cs_rank", ("mul", ("neg", FUND), ("ts_delta", C, 5))),
    "fund_x_rev":    ("cs_rank", ("mul", ("neg", FUND), ("neg", ("ts_delta", C, 1)))),
    "fund_lvl_rank": ("cs_rank", FUND),
    # price controls (no funding) for comparison
    "px_mom20":      ("cs_rank", ("ts_delta", C, 20)),
    "px_rev1":       ("cs_rank", ("neg", ("ts_delta", C, 1))),
}


def _funding_dataset_version(
    panel,
    *,
    bar_source_version: str,
    funding_source_version: str,
) -> str:
    return field_panel_dataset_version(
        panel,
        source_versions={
            "bars": bar_source_version,
            "funding": funding_source_version,
        },
    )


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
        search_kind="CRYPTO_FUNDING_RESEARCH",
        search_source="tools.crypto_funding_research",
        dataset_version=dataset_version,
        recorded_at=recorded_at,
        config=AlphaFactoryConfig(compute_regime_evidence=True),
    )


def _main(registry: ExperimentRegistry) -> None:
    cap = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    now_ms = int(time.time() * 1000)
    os.makedirs(os.path.join("data", "cache"), exist_ok=True)
    syms = [s for s in fetch_perp_symbols() if s not in _BLOCK][:cap]
    print(f"USDT-perp universe (capped): {len(syms)}", flush=True)

    store = SqlitePITStore(_DB, create=True)
    funding_by_tk, instruments, all_ets, dropped, failed = {}, [], set(), 0, 0
    failures = []
    for tk in syms:
        try:
            rows = fetch_daily_rows(
                tk,
                days=2200,
                now_ms=now_ms,
                archive_root=DEFAULT_RAW_ARCHIVE,
                observed_at_ms=now_ms,
            )
            fund = fetch_daily_funding(
                tk,
                days=2200,
                now_ms=now_ms,
                archive_root=DEFAULT_RAW_ARCHIVE,
                observed_at_ms=now_ms,
            )
            if (not rows or len(rows) < _MIN_BARS or _ret_vol(rows) < _MIN_VOL
                    or len(fund) < _MIN_BARS):
                dropped += 1
                continue
            expected_end = (now_ms // _DAY_MS) * _DAY_MS - _DAY_MS
            validate_daily_funding(tk, fund, expected_end=expected_end)
            ingest_provider_rows(
                rows,
                store,
                dataset_id=f"bnf-{tk}",
                now=now_ms,
                time_unit="ms",
                strict=True,
                expected_interval=_DAY_MS,
                expected_end=expected_end,
            )
        except Exception as exc:
            failed += 1
            failures.append({"ticker": tk, "error": f"{type(exc).__name__}: {exc}"})
            continue
        funding_by_tk[tk] = fund
        instruments.append(tk)
        all_ets.update(r["event_time"] for r in rows)
        if len(instruments) % 20 == 0:
            print(f"  ingested {len(instruments)} (dropped {dropped}, failed {failed})...", flush=True)
        time.sleep(0.1)

    event_times = sorted(all_ets)
    if len(instruments) < 10:
        raise SystemExit(f"only {len(instruments)} usable perps")
    store = CachingPITStore(store)
    t0 = time.perf_counter()
    panel = ax.build_field_panel(store, instruments, event_times, ("close", "volume"))
    # inject the funding field matrix aligned to (event_times x sorted instruments)
    panel.matrices["funding"] = [[funding_by_tk.get(inst, {}).get(t) for inst in panel.instruments]
                                 for t in panel.event_times]
    cover = sum(1 for row in panel.matrices["funding"] for v in row if v is not None)
    print(f"\n{len(instruments)} perps x {len(event_times)} daily periods (dropped {dropped}, failed {failed}); "
          f"panel {time.perf_counter()-t0:.1f}s; funding cells {cover}", flush=True)

    candidate_rows = []
    for name, expr in ALPHAS.items():
        alpha = ax.evaluate(expr, panel)
        periods, times = _periods_timed(alpha, panel)
        if len(periods) < 24:
            continue
        defn = AlphaDefinition(alpha_id=ax.alpha_id(expr), name=name, factor_ids=("close",),
                               params={"family": "funding_alpha", "expr": ax.serialize(expr)})
        candidate_rows.append((name, tuple(periods), tuple(times), defn))

    bar_source_version = adapt_research_market_data(store).dataset_version(
        instruments,
        event_times,
    )
    funding_source_version = stable_hash(
        {
            ticker: tuple(sorted(daily.items()))
            for ticker, daily in sorted(funding_by_tk.items())
        }
    )
    dataset_version = _funding_dataset_version(
        panel,
        bar_source_version=bar_source_version,
        funding_source_version=funding_source_version,
    )
    batch_result = _run_registered_pool(
        tuple(
            (definition, periods)
            for _name, periods, _times, definition in candidate_rows
        ),
        registry=registry,
        search_id=make_search_id(
            source="tools.crypto_funding_research",
            search_kind="CRYPTO_FUNDING_RESEARCH",
            dataset_version=dataset_version,
            recorded_at=now_ms,
            params={"candidate_count": len(candidate_rows)},
        ),
        dataset_version=dataset_version,
        recorded_at=now_ms,
    )
    report = batch_result["report"]

    rows_out, pool_ret = [], {}
    for name, periods, times, defn in candidate_rows:
        ret = list(run_panel_backtest(periods).period_returns)
        pool_ret[name] = ret
        py = _per_year(times, ret)
        ev = report.get(defn.alpha_id, {}).get("evaluation")
        rows_out.append({"name": name, "full_sharpe": round(sharpe(ret), 3),
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
    snap = {"generated_at": now_ms // 1000, "provider": "Binance public klines + funding (perp)",
            "n_instruments": len(instruments), "n_periods": len(event_times), "instruments": instruments,
            "auto_approved": auto, "alphas": rows_out, "oos": oos,
            "data_quality": {"strict_daily": True, "failures": failures}}
    atomic_write_json(
        _FUND_CACHE,
        {tk: {str(day): value for day, value in daily.items()}
         for tk, daily in funding_by_tk.items()},
    )
    atomic_write_json(_SNAP, snap, indent=2)

    print(f"\n=== PRICE+FUNDING cross-sectional alphas: {len(instruments)} perps x {len(event_times)} periods ===")
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
    print(f"cross-regime survivors: {survivors or 'NONE'}")
    print(f"-> wrote {_SNAP}")


def main() -> None:
    with production_experiment_registry() as registry:
        _main(registry)


if __name__ == "__main__":
    main()
