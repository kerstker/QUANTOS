"""Out-of-gate, READ-ONLY DAILY-horizon crypto research -> persisted JSON snapshot.

Why this exists: every prior crypto run was SHORT-horizon (4h / hourly) on a SMALL sample,
and the honest verdict was "no robust out-of-sample edge". That null has two confounds —
horizon too short (1-day-ahead noise) and sample too small. The single most-documented crypto
anomaly is DAILY/WEEKLY momentum & trend (with short-term REVERSAL at the 1-day edge). This
driver tests exactly that, WITHOUT opening any new "口子":

  - same CoinGecko Demo key, same read-only market_chart endpoint, just days=365 -> the demo
    tier auto-returns DAILY granularity (~365 bars), a sample an order of magnitude larger;
  - bars are snapped to the UTC DAY grid (bucket_ms = 86_400_000) so every coin's daily points
    land on the SAME period keys (cross-sectional alignment); the trailing LIVE day is dropped
    (no lookahead);
  - the UNCHANGED guardrailed evolutionary search runs at DAILY windows (weekly/biweekly/monthly
    momentum & trend; reversal reachable by genome negation). Agent can never reach APPROVED;
    each distinct factor is one catalog trial -> Deflated Sharpe deflates the search;
  - then the DECISIVE no-peek walk-forward SELECTION OOS over an atomic daily price-factor pool:
    re-rank on each TRAIN window, measure the SELECTED factor on the never-seen TEST window. If
    the edge persists OOS it is real; if it collapses, the in-sample number was selection illusion.

Writes data/cache/crypto_research_snapshot.json (granularity '1d') — the only thing the read-only
console API consumes (it never fetches). Gitignored raw research output. Network lives HERE, out
of the gate; the research core stays offline/deterministic. Nothing here promotes an alpha, flips
a flag, or raises a readiness level.
"""
from __future__ import annotations

import json
import os
import sys
import time

from services.data.persistent_store import SqlitePITStore
from services.data.market_provider import ingest_provider_rows
from services.intelligence.search_space import HypothesisSpace
from services.intelligence.evolutionary_search import make_evolution_provider, genome_to_definition
from services.research.session import run_research_session
from services.research.panel_builder import make_data_periods_provider
from services.research.backtest_engine.metrics import sharpe
from services.research.validation.walk_forward import walk_forward_splits
from services.research.oos_validation import walk_forward_selection_oos, full_sample_best
from services.alpha.panel_backtest import run_panel_backtest
from tools.coingecko_volume_fetch import fetch_market_chart, parse_market_chart

# Wide liquid cross-section (breadth is the real statistical-power lever: IR ≈ IC·√breadth).
COINS = [
    ("bitcoin", "BTC"), ("ethereum", "ETH"), ("binancecoin", "BNB"),
    ("solana", "SOL"), ("ripple", "XRP"), ("cardano", "ADA"),
    ("dogecoin", "DOGE"), ("avalanche-2", "AVAX"), ("chainlink", "LINK"),
    ("polkadot", "DOT"), ("litecoin", "LTC"), ("tron", "TRX"),
    ("matic-network", "MATIC"), ("shiba-inu", "SHIB"), ("uniswap", "UNI"),
    ("cosmos", "ATOM"), ("stellar", "XLM"), ("near", "NEAR"), ("aptos", "APT"),
    ("arbitrum", "ARB"), ("optimism", "OP"), ("filecoin", "FIL"),
    ("hedera-hashgraph", "HBAR"), ("vechain", "VET"), ("maker", "MKR"),
    ("aave", "AAVE"), ("the-graph", "GRT"), ("algorand", "ALGO"),
    ("injective-protocol", "INJ"), ("sui", "SUI"), ("render-token", "RNDR"),
    ("immutable-x", "IMX"), ("tezos", "XTZ"), ("eos", "EOS"), ("flow", "FLOW"),
    ("axie-infinity", "AXS"), ("chiliz", "CHZ"), ("quant-network", "QNT"),
    ("gala", "GALA"), ("curve-dao-token", "CRV"), ("kava", "KAVA"),
    ("pancakeswap-token", "CAKE"),
]
DAYS = 365
DAY_MS = 86_400_000
GRANULARITY = "1d"
FWD_HORIZON = 1                  # daily-ahead: clean, NO overlap (honest); reversal & trend both reachable
OPERATORS = ("momentum", "ts_mean", "zscore", "volatility", "ts_rank",
             "ema_ratio", "dist_from_high", "accel")
WINDOWS = (3, 7, 14, 30)         # days: short-term reversal (3) .. weekly/biweekly/monthly trend
EVO_GENERATIONS, EVO_POPULATION, EVO_ELITE = 3, 8, 3
DEFAULT_N = 24

_DAILY_DB = os.path.join("data", "cache", "crypto_daily.db")
_SNAPSHOT_PATH = os.path.join("data", "cache", "crypto_research_snapshot.json")


def _round(x, n=4):
    return round(x, n) if isinstance(x, (int, float)) else None


def _build_factors(report: dict) -> list:
    factors = []
    for alpha_id, entry in sorted(report.items()):
        ev = entry.get("evaluation")
        if ev is None:
            continue
        factors.append({
            "alpha_id": alpha_id,
            "name": getattr(ev, "alpha_id", alpha_id),
            "family": "interaction" if alpha_id.startswith("hx_") else "price",
            "ic": _round(ev.ic_mean),
            "rank_ic": _round(ev.rank_ic) if hasattr(ev, "rank_ic") else None,
            "net_sharpe": _round(ev.net_sharpe, 3),
            "capacity_adj_sharpe": _round(ev.capacity_adj_sharpe, 3),
            "deflated_sharpe": _round(ev.deflated_sharpe, 4),
            "pbo": _round(ev.pbo, 4),
            "turnover": _round(ev.turnover, 4),
            "breadth": _round(ev.mean_breadth, 2),
            "verdict": ev.verdict.value,
            "state": entry.get("state"),
            "reproducibility_hash": ev.reproducibility_hash,
        })
    factors.sort(key=lambda f: (f["net_sharpe"] if f["net_sharpe"] is not None else -1e9), reverse=True)
    return factors


def _returns_for(provider, genome) -> list:
    periods = provider(genome_to_definition(genome))
    return list(run_panel_backtest(periods).period_returns)


def _no_peek_oos(store, instruments) -> dict:
    """The decisive test: no-peek walk-forward SELECTION OOS over an atomic DAILY price-factor
    pool, plus the naive (peeking) full-sample best tracked IS-vs-OOS. Same shape the console
    OOS panel renders. Read-only over the persisted daily store; deterministic."""
    event_times = sorted({b.event_time for inst in instruments
                          for b in store.fetch_bars(inst, 0, 10 ** 14)})
    price_provider = make_data_periods_provider(store, instruments, event_times=event_times,
                                                fwd_horizon=FWD_HORIZON)
    pool = {}
    for op in OPERATORS:
        for w in WINDOWS:
            r = _returns_for(price_provider, ("P", op, str(w)))
            if len(r) >= 2:
                pool[f"h_{op}_{w}"] = r
    if len(pool) < 2:
        return None
    L = min(len(r) for r in pool.values())
    aligned = {k: v[-L:] for k, v in pool.items()}
    test_size = max(8, L // 8)
    train_size = max(20, L // 2)
    step = test_size
    embargo = FWD_HORIZON
    sel = walk_forward_selection_oos(aligned, train_size=train_size, test_size=test_size,
                                     step=step, embargo=embargo, top_k=1)
    best_id, best_sr = full_sample_best(aligned)

    # track the naive winner IS-vs-OOS on the SAME windows (does the peeking pick hold up?)
    winner = aligned[best_id]
    folds = []
    for window, (train, test) in enumerate(walk_forward_splits(L, train_size, test_size, step)):
        tr = train[:-embargo] if embargo and embargo < len(train) else train
        if len(tr) < 2:
            continue
        folds.append({
            "window": window,
            "is_sharpe": round(sharpe([winner[j] for j in tr]), 4),
            "oos_sharpe": round(sharpe([winner[j] for j in test]), 4),
        })
    h_oos = [f["oos_sharpe"] for f in folds]
    headline = {
        "alpha_id": best_id,
        "expr": None,
        "full_sample_sharpe": round(sharpe(winner), 4),
        "folds": folds,
        "mean_oos_sharpe": round(sum(h_oos) / len(h_oos), 4) if h_oos else None,
        "oos_positive_frac": round(sum(1 for v in h_oos if v > 0) / len(h_oos), 4) if h_oos else None,
    }
    return {
        "method": "WALK_FORWARD_NO_PEEK_SELECTION_OOS",
        "n_aligned_periods": L,
        "candidate_pool": "ATOMIC_PRICE_FACTORS_DAILY",
        "n_candidates": len(aligned),
        "selection": sel,
        "naive_full_sample_best": {"alpha_id": best_id, "sharpe": round(best_sr, 4)},
        "headline_factor": headline,
    }


def main() -> None:
    # argv: [n_coins] [days]
    n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_N
    days = int(sys.argv[2]) if len(sys.argv) > 2 else DAYS
    universe = COINS[:max(2, n)]
    now_ms = int(time.time() * 1000)
    os.makedirs(os.path.join("data", "cache"), exist_ok=True)
    store = SqlitePITStore(_DAILY_DB, create=True)

    coin_meta, all_event_times, instruments, dropped = [], set(), [], []
    for coin_id, ticker in universe:
        try:
            payload = fetch_market_chart(coin_id, days=days)
            rows = parse_market_chart(payload, instrument_id=ticker, now_ms=now_ms, bucket_ms=DAY_MS)
            if not rows:
                raise ValueError("no daily bars")
            rep = ingest_provider_rows(rows, store, dataset_id=f"cg-daily-{ticker}",
                                       now=now_ms, time_unit="ms")
        except Exception as exc:                          # bad id / gap / rate-limit -> skip, never crash
            dropped.append({"instrument_id": ticker, "coin_id": coin_id, "reason": type(exc).__name__})
            print(f"{ticker:5s} SKIPPED ({type(exc).__name__})", flush=True)
            time.sleep(2.5)
            continue
        instruments.append(ticker)
        all_event_times.update(r["event_time"] for r in rows)
        coin_meta.append({"instrument_id": ticker, "coin_id": coin_id,
                          "bars": len(rows), "ingested": rep.bars_ingested, "rejected": len(rep.rejected)})
        print(f"{ticker:5s} fetched {len(rows):3d} daily bars  ingested {rep.bars_ingested:3d}", flush=True)
        time.sleep(2.5)  # stay under the demo-tier ~30 calls/min rate limit

    event_times = sorted(all_event_times)
    space = HypothesisSpace(factors=("close",), operators=OPERATORS, windows=WINDOWS)
    print(f"\nDAILY periods={len(event_times)} instruments={len(instruments)} "
          f"-> evolutionary search + no-peek OOS ...", flush=True)
    result = run_research_session(
        store, instruments, event_times=event_times, fwd_horizon=FWD_HORIZON,
        space=space, rounds=1, batch_size=1, timestamp=now_ms,
        evolutionary=True, evo_generations=EVO_GENERATIONS,
        evo_population=EVO_POPULATION, evo_elite=EVO_ELITE, synergistic_k=8)

    factors = _build_factors(result["report"])
    oos = _no_peek_oos(store, instruments) if len(instruments) >= 2 else None
    review_queue = set(result.get("human_review_queue", []))
    n_eval = result["n_evaluated"]
    auto_approved = sum(1 for f in factors if f["state"] == "APPROVED")  # must be 0 (containment)
    rejected = sum(1 for f in factors if str(f["state"]) == "REJECTED")
    net_sharpes = [f["net_sharpe"] for f in factors if f["net_sharpe"] is not None]
    deflated = [f["deflated_sharpe"] for f in factors if f["deflated_sharpe"] is not None]

    snapshot = {
        "generated_at": now_ms // 1000,
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ms / 1000)),
        "dataset_version": result["dataset_version"],
        "meta": {
            "provider": "CoinGecko v3 Demo (read-only market_chart; DAILY)",
            "asset_class": "CRYPTO",
            "vs_currency": "usd",
            "days": days,
            "granularity": GRANULARITY,
            "fwd_horizon": FWD_HORIZON,
            "n_instruments": len(instruments),
            "n_periods": len(event_times),
            "coins": coin_meta,
            "dropped": dropped,
            "n_dropped": len(dropped),
            "search_mode": result["mode"],
            "evo_generations": EVO_GENERATIONS,
            "evo_population": EVO_POPULATION,
            "evo_elite": EVO_ELITE,
            "operators": list(OPERATORS),
            "windows": list(WINDOWS),
        },
        "summary": {
            "n_evaluated": n_eval,
            "trial_count": n_eval,
            "auto_approved": auto_approved,
            "requires_human_review": len(review_queue),
            "rejected": rejected,
            "max_net_sharpe": max(net_sharpes) if net_sharpes else None,
            "max_deflated_sharpe": max(deflated) if deflated else None,
            "top_alpha_id": factors[0]["alpha_id"] if factors else None,
        },
        "factors": factors,
        "oos": oos,
        "synergistic_set": result.get("synergistic_set"),
    }
    with open(_SNAPSHOT_PATH, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2)

    print(f"\n=== DAILY crypto research: {len(instruments)} coins x {len(event_times)} daily periods ===")
    print(f"evaluated={n_eval} auto_approved={auto_approved} (must be 0) review={len(review_queue)} "
          f"rejected={rejected}")
    if net_sharpes:
        print(f"max in-sample net Sharpe={max(net_sharpes):.3f}  max Deflated Sharpe={max(deflated):.4f}")
    if oos:
        sel = oos["selection"]
        print(f"[no-peek OOS] aligned L={oos['n_aligned_periods']}  mean IS Sharpe={sel['mean_is_sharpe']}  "
              f"mean OOS Sharpe={sel['mean_oos_sharpe']}  haircut={sel['oos_minus_is']}  "
              f"OOS+ folds={sel['oos_positive_frac']} (n_folds={sel['n_folds']})")
        print(f"    naive full-sample best (peeking): {oos['naive_full_sample_best']}")
    print(f"-> wrote {_SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
