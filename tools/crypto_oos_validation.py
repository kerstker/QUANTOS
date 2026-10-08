"""Out-of-sample validation of the crypto research findings (answers "is the
in-sample Deflated Sharpe real, or selection overfit?").

OFFLINE — reads only the already-persisted PIT store (data/cache/crypto.db); NO
network, no fetch. Two honest tests over the real per-period net-return series:

  A) NO-PEEK WALK-FORWARD SELECTION (the decisive one): re-rank a fixed candidate
     pool (atomic price factors) by Sharpe on each TRAIN window, then measure the
     SELECTED factor on the next, never-seen TEST window. If the selected edge
     persists OOS it is real; if it collapses, the in-sample number was a
     selection illusion. Reports the mean IS-vs-OOS Sharpe haircut.

  B) THE HEADLINE FACTOR TRACKED (no selection): the specific interaction factor
     that topped the in-sample run, evaluated IS vs OOS on the same windows — does
     THAT factor hold up out-of-sample?

Aligns factors on their common trailing periods (all series end on the same period
and are contiguous; only the leading warm-up length differs by window). Merges an
`oos` block into data/cache/crypto_research_snapshot.json for the console to render.
"""
from __future__ import annotations

import json
import os

from services.data.persistent_store import SqlitePITStore
from services.execution.backtest_authority import quarantine_legacy_backtest
from services.research.backtest_engine.metrics import sharpe
from services.research.panel_builder import make_data_periods_provider
from services.alpha.panel_backtest import run_panel_backtest
from services.intelligence.evolutionary_search import genome_to_definition, make_evolution_provider
from services.validation.walk_forward import walk_forward_splits
from services.research.oos_validation import walk_forward_selection_oos, full_sample_best

OPERATORS = ("momentum", "ts_mean", "zscore", "volatility", "ts_rank",
             "ema_ratio", "dist_from_high", "accel")
WINDOWS = (3, 5, 10)
FWD_HORIZON = 1
HEADLINE = ("X", ("price", "accel", "10"), ("price", "ema_ratio", "5"), "product")
_SNAPSHOT = os.path.join("data", "cache", "crypto_research_snapshot.json")


def _returns_for(provider, genome) -> list:
    periods = provider(genome_to_definition(genome))
    return list(run_panel_backtest(periods).period_returns)


def main() -> None:
    quarantine_legacy_backtest("tools.crypto_oos_validation")
    if not os.path.exists(_SNAPSHOT):
        raise SystemExit(f"no snapshot at {_SNAPSHOT} — run tools/crypto_research_run.py first")
    with open(_SNAPSHOT, encoding="utf-8") as fh:
        snap = json.load(fh)
    instruments = sorted(c["instrument_id"] for c in snap.get("meta", {}).get("coins", []))
    if len(instruments) < 2:
        raise SystemExit("snapshot has < 2 instruments — re-run the research driver")
    store = SqlitePITStore(os.path.join("data", "cache", "crypto.db"), create=False)
    # union of event_times across instruments (from the persisted PIT store)
    event_times = sorted({b.event_time for inst in instruments
                          for b in store.fetch_bars(inst, 0, 10 ** 14)})
    price_provider = make_data_periods_provider(store, instruments, event_times=event_times,
                                                fwd_horizon=FWD_HORIZON)
    evo_provider = make_evolution_provider(store, None, instruments,
                                           event_times=event_times, fwd_horizon=FWD_HORIZON)

    # candidate pool: atomic price factors (fixed, reproducible) -> per-period returns
    pool = {}
    for op in OPERATORS:
        for w in WINDOWS:
            r = _returns_for(price_provider, ("P", op, str(w)))
            if len(r) >= 2:
                pool[f"h_{op}_{w}"] = r
    # headline = whatever the current run discovered as the top factor (GP tree or interaction);
    # fall back to the fixed interaction headline if the snapshot has no factors.
    top = (snap.get("factors") or [{}])[0]
    if top.get("expr"):
        from services.research.gp_tree import parse as _parse_tree
        headline_genome = ("T", _parse_tree(top["expr"]))
        headline_label = top.get("alpha_id") or top["expr"]
    else:
        headline_genome = HEADLINE
        headline_label = "hx_product_price-accel-10_price-ema_ratio-5"
    headline_returns = _returns_for(evo_provider, headline_genome)

    # align on common trailing periods (series share the same final period; only the
    # leading warm-up differs by window)
    L = min([len(r) for r in pool.values()] + [len(headline_returns)])
    aligned = {k: v[-L:] for k, v in pool.items()}
    headline_aligned = headline_returns[-L:]

    test_size = max(8, L // 8)
    train_size = max(20, L // 2)
    step = test_size
    embargo = FWD_HORIZON

    sel = walk_forward_selection_oos(aligned, train_size=train_size, test_size=test_size,
                                     step=step, embargo=embargo, top_k=1)
    best_id, best_sr = full_sample_best(aligned)

    # B) headline factor tracked IS-vs-OOS on the SAME windows (no selection)
    headline_folds = []
    for window, (train, test) in enumerate(walk_forward_splits(L, train_size, test_size, step)):
        tr = train[:-embargo] if embargo and embargo < len(train) else train
        if len(tr) < 2:
            continue
        headline_folds.append({
            "window": window,
            "is_sharpe": round(sharpe([headline_aligned[j] for j in tr]), 4),
            "oos_sharpe": round(sharpe([headline_aligned[j] for j in test]), 4),
        })
    h_oos = [f["oos_sharpe"] for f in headline_folds]
    headline = {
        "alpha_id": headline_label,
        "expr": top.get("expr"),
        "full_sample_sharpe": round(sharpe(headline_aligned), 4),
        "folds": headline_folds,
        "mean_oos_sharpe": round(sum(h_oos) / len(h_oos), 4) if h_oos else None,
        "oos_positive_frac": round(sum(1 for v in h_oos if v > 0) / len(h_oos), 4) if h_oos else None,
    }

    oos = {
        "method": "WALK_FORWARD_NO_PEEK_SELECTION_OOS",
        "n_aligned_periods": L,
        "candidate_pool": "ATOMIC_PRICE_FACTORS",
        "n_candidates": len(aligned),
        "selection": sel,
        "naive_full_sample_best": {"alpha_id": best_id, "sharpe": round(best_sr, 4)},
        "headline_factor": headline,
    }

    print(f"aligned periods L={L}  train={train_size} test={test_size} step={step} embargo={embargo}")
    print(f"[A] no-peek selection: mean IS Sharpe={sel['mean_is_sharpe']}  "
          f"mean OOS Sharpe={sel['mean_oos_sharpe']}  haircut(OOS-IS)={sel['oos_minus_is']}  "
          f"OOS+ folds={sel['oos_positive_frac']}  (n_folds={sel['n_folds']})")
    print(f"    naive full-sample-best (peeking): {best_id} Sharpe={best_sr:.4f}")
    print(f"[B] headline {headline['alpha_id']}: full-sample={headline['full_sample_sharpe']}  "
          f"mean OOS={headline['mean_oos_sharpe']}  OOS+ frac={headline['oos_positive_frac']}")

    if os.path.exists(_SNAPSHOT):
        with open(_SNAPSHOT, encoding="utf-8") as fh:
            snap = json.load(fh)
        snap["oos"] = oos
        with open(_SNAPSHOT, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, indent=2)
        print(f"merged 'oos' block into {_SNAPSHOT}")
    else:
        print(f"(no snapshot at {_SNAPSHOT}; printed only)")


if __name__ == "__main__":
    main()
