"""Out-of-gate, READ-ONLY real-crypto research run -> persisted JSON snapshot.

This is the bridge that turns the live CoinGecko data path into a first batch of
REAL signal conclusions the frontend can render. It lives OUT of the gate because
it touches the network (via tools/coingecko_fetch); the research core it calls
(services/research.session) stays fully offline/deterministic, and the snapshot it
writes is the only thing the read-only console API consumes — the API never fetches.

Pipeline (all read-only, no broker/SDK, no order/credential):
  1. fetch a multi-coin OHLC cross-section @ days=30 (4h candles — the demo tier's
     richest bar count), drop the in-progress trailing candle (no lookahead);
  2. ingest into a local PIT store (gitignored data/cache/crypto.db) via the
     offline multi-asset seam, fail-closed on bad/cross-scale rows;
  3. run the UNCHANGED guardrailed evolutionary research session (agent can never
     reach APPROVED) over the real periods;
  4. write data/cache/crypto_research_snapshot.json — factors, metrics, verdicts,
     and the multiple-testing-honesty summary. Gitignored (raw research output).

Nothing here promotes an alpha, flips a flag, or raises a readiness level.
"""
from __future__ import annotations

import json
import os
import time

import sys

from services.data.persistent_store import SqlitePITStore
from services.data.research_adapter import adapt_research_market_data
from services.data.market_provider import ingest_provider_rows
from services.intelligence.search_space import HypothesisSpace
from services.intelligence.research_session import run_research_session
from tools.search_trial_registry import (
    make_search_id,
    production_experiment_registry,
)
from tools.coingecko_fetch import fetch_ohlc, parse_ohlc, _INTERVAL_MS

# (coingecko id, ticker) — a wide liquid cross-section. On the DEMO tier days=30 (4h)
# is the richest bar count (~179); the only real statistical-power lever is BREADTH
# (fundamental law IR≈IC·√breadth), so we widen the universe rather than the history.
# Bad/illiquid ids are skipped per-coin at runtime (one failure never kills the run).
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
DAYS = 30
GRANULARITY = "4h"
FWD_HORIZON = 1
OPERATORS = ("momentum", "ts_mean", "zscore", "volatility", "ts_rank",
             "ema_ratio", "dist_from_high", "accel")
WINDOWS = (3, 5, 10)
EVO_GENERATIONS, EVO_POPULATION, EVO_ELITE = 4, 12, 5

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


def main() -> None:
    now_ms = int(time.time() * 1000)
    os.makedirs(os.path.join("data", "cache"), exist_ok=True)
    store = SqlitePITStore(os.path.join("data", "cache", "crypto.db"), create=True)
    interval_ms = _INTERVAL_MS.get(DAYS, 4 * 3600 * 1000)

    n = int(sys.argv[1]) if len(sys.argv) > 1 else len(COINS)
    universe = COINS[:max(2, n)]
    coin_meta, all_event_times, instruments, dropped = [], set(), [], []
    for coin_id, ticker in universe:
        try:
            payload = fetch_ohlc(coin_id, days=DAYS)
            rows = parse_ohlc(payload, instrument_id=ticker, interval_ms=interval_ms, now_ms=now_ms)
            if not rows:
                raise ValueError("no candles")
            rep = ingest_provider_rows(rows, store, dataset_id=f"coingecko-{ticker}",
                                       now=now_ms, time_unit="ms")
        except Exception as exc:                          # bad id / gap / rate-limit -> skip, never crash
            dropped.append({"instrument_id": ticker, "coin_id": coin_id, "reason": type(exc).__name__})
            print(f"{ticker:5s} SKIPPED ({type(exc).__name__})")
            time.sleep(2.5)
            continue
        instruments.append(ticker)
        all_event_times.update(r["event_time"] for r in rows)
        coin_meta.append({"instrument_id": ticker, "coin_id": coin_id,
                          "bars": len(rows), "ingested": rep.bars_ingested, "rejected": len(rep.rejected)})
        print(f"{ticker:5s} fetched {len(rows):3d} ingested {rep.bars_ingested:3d} rejected {len(rep.rejected)}")
        time.sleep(2.5)  # stay under the demo-tier ~30 calls/min rate limit

    event_times = sorted(all_event_times)
    space = HypothesisSpace(factors=("close",), operators=OPERATORS, windows=WINDOWS)
    print(f"\nperiods={len(event_times)} instruments={len(instruments)} -> evolutionary search ...")
    research_store = adapt_research_market_data(store)
    dataset_version = research_store.dataset_version(instruments, event_times)
    with production_experiment_registry() as registry:
        result = run_research_session(
            research_store, instruments, event_times=event_times,
            fwd_horizon=FWD_HORIZON, space=space, rounds=1, batch_size=1,
            timestamp=now_ms, dataset_version=dataset_version, registry=registry,
            search_id=make_search_id(
                source="tools.crypto_research_run",
                search_kind="RESEARCH_SESSION_EVOLUTIONARY",
                dataset_version=dataset_version,
                recorded_at=now_ms,
                params={
                    "generations": EVO_GENERATIONS,
                    "population": EVO_POPULATION,
                },
            ),
            search_kind="RESEARCH_SESSION_EVOLUTIONARY",
            search_source="tools.crypto_research_run",
            evolutionary=True, evo_generations=EVO_GENERATIONS,
            evo_population=EVO_POPULATION, evo_elite=EVO_ELITE,
        )

    factors = _build_factors(result["report"])
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
            "provider": "CoinGecko v3 Demo (read-only public OHLC)",
            "asset_class": "CRYPTO",
            "vs_currency": "usd",
            "days": DAYS,
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
    }
    with open(_SNAPSHOT_PATH, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2)
    print(f"\nevaluated={n_eval} auto_approved={auto_approved} (must be 0) "
          f"review={len(review_queue)} -> wrote {_SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
