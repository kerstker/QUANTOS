"""Out-of-gate, READ-ONLY real-volume liquidity-factor research run.

Fetches REAL 24h volume for a multi-coin cross-section (CoinGecko market_chart, existing demo
key), ingests it, and runs the VOLUME/liquidity operators through the UNCHANGED contained
evaluator (agent can never reach APPROVED; each factor is one catalog trial -> Deflated Sharpe
deflates the search). Prints the first real liquidity-factor verdicts. Network lives here,
out of the gate; the research core stays offline/deterministic.
"""
from __future__ import annotations

import os
import sys
import time

from services.data.persistent_store import SqlitePITStore
from services.data.market_provider import ingest_provider_rows
from services.research.panel_builder import make_volume_periods_provider
from services.research.volume_signals import VOLUME_OPS
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.intelligence.agent_loop import ResearchAgentLoop
from quantos.contracts.alpha import AlphaDefinition
from tools.coingecko_volume_fetch import fetch_volume_rows

COINS = [("bitcoin", "BTC"), ("ethereum", "ETH"), ("binancecoin", "BNB"), ("solana", "SOL"),
         ("ripple", "XRP"), ("cardano", "ADA"), ("dogecoin", "DOGE"), ("avalanche-2", "AVAX"),
         ("chainlink", "LINK"), ("polkadot", "DOT"), ("litecoin", "LTC"), ("tron", "TRX"),
         ("uniswap", "UNI"), ("stellar", "XLM"), ("near", "NEAR")]
DAYS = 30
FWD_HORIZON = 1
WINDOWS = (3, 6, 12, 24)          # hours (market_chart days=30 -> hourly)


def main() -> None:
    # argv: [days] [n_coins] — fewer days -> fewer periods -> the O(periods^2) panel build is
    # dramatically faster (this is a CPU/algorithmic knob, not a GPU one; see module note).
    days = int(sys.argv[1]) if len(sys.argv) > 1 else DAYS
    universe = COINS[:int(sys.argv[2])] if len(sys.argv) > 2 else COINS
    now_ms = int(time.time() * 1000)
    os.makedirs(os.path.join("data", "cache"), exist_ok=True)
    store = SqlitePITStore(os.path.join("data", "cache", "crypto_volume.db"), create=True)
    instruments, all_ets, dropped = [], set(), []
    for coin_id, ticker in universe:
        try:
            rows = fetch_volume_rows(coin_id, ticker, days=days, now_ms=now_ms)
            if not rows:
                raise ValueError("no rows")
            ingest_provider_rows(rows, store, dataset_id=f"mc-{ticker}", now=now_ms, time_unit="ms")
        except Exception as exc:
            dropped.append(ticker)
            print(f"{ticker:5s} SKIPPED ({type(exc).__name__})", flush=True)
            time.sleep(2.5)
            continue
        instruments.append(ticker)
        all_ets.update(r["event_time"] for r in rows)
        print(f"{ticker:5s} {len(rows)} points", flush=True)
        time.sleep(2.5)

    event_times = sorted(all_ets)
    provider = make_volume_periods_provider(store, instruments, event_times=event_times,
                                            fwd_horizon=FWD_HORIZON)
    catalog = AlphaCatalog()
    loop = ResearchAgentLoop(catalog=catalog, config=AlphaFactoryConfig(compute_regime_evidence=True))

    candidates = []
    for op in VOLUME_OPS:
        for w in WINDOWS:
            defn = AlphaDefinition(alpha_id=f"hv_{op}_{w}", name=f"VOL({op}:{w})",
                                   factor_ids=("volume",),
                                   params={"operator": op, "window": str(w), "family": "volume"})
            try:
                candidates.append((defn, provider(defn)))
            except Exception as exc:
                print(f"build failed {defn.alpha_id}: {type(exc).__name__}")

    print(f"fetched {len(instruments)} coins x {len(event_times)} periods; "
          f"evaluating {len(candidates)} volume factors (contained) ...", flush=True)
    out = loop.run(candidates, timestamp=now_ms)
    rows_rep = []
    for aid, entry in out["report"].items():
        ev = entry.get("evaluation")
        if ev is None:
            continue
        rows_rep.append((aid, ev.net_sharpe, ev.deflated_sharpe, ev.ic_mean, ev.mean_breadth,
                         entry.get("state"), ev.verdict.value))
    rows_rep.sort(key=lambda r: (r[1] if r[1] is not None else -1e9), reverse=True)

    auto = sum(1 for aid in catalog.all_ids() if str(catalog.state(aid).value) == "APPROVED")
    print(f"\n=== REAL volume/liquidity factors: {len(instruments)} coins x {len(event_times)} hourly periods ===")
    print(f"instruments={instruments} dropped={dropped}")
    print(f"evaluated={len(rows_rep)} auto_approved={auto} (must be 0) trial_count={catalog.global_trial_count()}")
    print(f"{'factor':28s} {'netSharpe':>10} {'deflated':>9} {'ic':>8} {'breadth':>8}  verdict")
    for aid, ns, dsr, ic, br, state, verdict in rows_rep:
        ns_s = f"{ns:.3f}" if ns is not None else "n/a"
        dsr_s = f"{dsr:.4f}" if dsr is not None else "n/a"
        ic_s = f"{ic:.4f}" if ic is not None else "n/a"
        br_s = f"{br:.1f}" if br is not None else "n/a"
        print(f"{aid:28s} {ns_s:>10} {dsr_s:>9} {ic_s:>8} {br_s:>8}  {verdict}")


if __name__ == "__main__":
    main()
