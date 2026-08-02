"""Out-of-gate, OFFLINE the REAL economic P&L of the funding-carry strategy (the "does it make money" test).

Every earlier backtest measured only the PRICE return of the long/short book. But a perp funding-carry
strategy ALSO collects the funding itself: it longs negative-funding coins (shorts pay longs -> you receive)
and shorts positive-funding coins (longs pay shorts -> you, the short, receive), so the dollar-neutral carry
book RECEIVES funding on BOTH legs. The honest economic daily return is therefore:

    net = price_LS_return  +  funding_collected  -  fees

where funding_collected[day] = sum_i ( -w_i * funding_realized_i )  (a position of signed weight w pays
w*f, so it earns -w*f), realized over the holding day (PIT: signal uses funding<=t, P&L uses funding[t+1]).

Signal = de-redundant CARRY COMPOSITE = mean cross-sectional z-score of several robust funding smoothings
(the comprehensive search showed neg-decay/mean of funding, windows 3-14, all rank in the top 15). Reports
price-only vs +funding vs +funding-fees Sharpe, per-year, turnover, and a fee sweep. Observed numbers only.
"""
from __future__ import annotations

import json
import os
import statistics
import time

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe

_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_FUND_CACHE = os.path.join("data", "cache", "crypto_funding_rates.json")
_OUT = os.path.join("data", "cache", "crypto_carry_strategy.json")

# F2 (2026-07-27): COMPOSITE, FUND and the shared _legs book-builder MOVED to the single
# source of truth services/factor_garden/canonical.py (COMPOSITE was a verbatim duplicate of
# crypto_oi_research.CARRY — killed); re-exported here under the historical names so every
# importer sees the same objects. QUANTILE stays 0.2 = CARRY_QUANTILE_OFFLINE (the live track
# uses 0.3 — the divergence is deliberately NOT unified, recorded in canonical.py).
from services.factor_garden.canonical import (
    CARRY_COMPOSITE_EXPRESSION as COMPOSITE,
    CARRY_QUANTILE_OFFLINE,
    FUNDING_FIELD as FUND,
    legs as _legs,
)
QUANTILE = CARRY_QUANTILE_OFFLINE
FEE_BPS = (0, 2, 5, 10, 20)        # per unit traded notional, one-way (perp: ~2 maker / ~5 taker)


def main() -> None:
    instruments = sorted(json.load(open(_SNAP, encoding="utf-8"))["instruments"])
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    event_times = sorted({b.event_time for inst in instruments
                          for b in store.fetch_bars(inst, -10**18, 10**18)})
    funding = {tk: {int(k): v for k, v in d.items()}
               for tk, d in json.load(open(_FUND_CACHE, encoding="utf-8")).items()}

    panel = ax.build_field_panel(store, instruments, event_times, ("close", "volume"))
    fund_mat = [[funding.get(inst, {}).get(t) for inst in panel.instruments] for t in panel.event_times]
    panel.matrices["funding"] = fund_mat
    sig = ax.evaluate(COMPOSITE, panel)
    px = panel.matrices["close"]
    T, N = len(sig), len(panel.instruments)

    price_r, fund_r, turn_r, times = [], [], [], []
    prev = {}
    for r in range(T - 1):
        w = _legs(sig[r], QUANTILE)
        if not w:
            prev = {}
            continue
        pr = fu = 0.0
        for i, wi in w.items():
            p0, p1, f1 = px[r][i], px[r + 1][i], fund_mat[r + 1][i]
            if p0 is None or p1 is None or p0 <= 0:
                continue
            pr += wi * (p1 / p0 - 1.0)
            if f1 is not None:
                fu += -wi * f1                       # funding collected on the position over the holding day
        names = set(w) | set(prev)
        turn = sum(abs(w.get(i, 0.0) - prev.get(i, 0.0)) for i in names)
        price_r.append(pr); fund_r.append(fu); turn_r.append(turn); times.append(panel.event_times[r])
        prev = w

    price_only = [p for p in price_r]
    with_fund = [p + f for p, f in zip(price_r, fund_r)]
    avg_turn = round(statistics.fmean(turn_r), 3) if turn_r else 0.0
    avg_fund = round(statistics.fmean(fund_r) * 10000, 2) if fund_r else 0.0    # bps/day collected

    def _per_year(series):
        from collections import defaultdict
        by = defaultdict(list)
        for t, v in zip(times, series):
            by[time.gmtime(t / 1000).tm_year].append(v)
        return {y: round(sharpe(by[y]), 2) for y in sorted(by) if len(by[y]) > 1}

    print(f"=== FUNDING-CARRY strategy REAL P&L: {N} perps x {len(times)} held days ===")
    print(f"signal = de-redundant carry composite (mean z-score of neg decay3/7/14 + mean7 of funding)")
    print(f"avg turnover {avg_turn}/day · avg funding collected {avg_fund} bps/day\n")
    print(f"{'P&L component':28s} {'Sharpe(252)':>12}")
    print(f"{'price only':28s} {sharpe(price_only):>12.3f}")
    print(f"{'price + funding collected':28s} {sharpe(with_fund):>12.3f}")
    for fee in FEE_BPS:
        net = [p + f - t * fee / 10000.0 for p, f, t in zip(price_r, fund_r, turn_r)]
        print(f"{'  net @ '+str(fee)+'bps fee':28s} {sharpe(net):>12.3f}")
    print(f"\nper-year Sharpe (price+funding, pre-fee):")
    for y, v in _per_year(with_fund).items():
        print(f"  {y}: {v:+.2f}")
    net5 = [p + f - t * 5 / 10000.0 for p, f, t in zip(price_r, fund_r, turn_r)]
    print(f"per-year Sharpe (net @5bps):")
    for y, v in _per_year(net5).items():
        print(f"  {y}: {v:+.2f}")

    json.dump({"generated_at": int(time.time()), "n_instruments": N, "n_days": len(times),
               "avg_turnover": avg_turn, "avg_funding_bps_day": avg_fund,
               "sharpe_price_only": round(sharpe(price_only), 3),
               "sharpe_price_plus_funding": round(sharpe(with_fund), 3),
               "sharpe_net_5bps": round(sharpe(net5), 3),
               "paper_track_start_utc": time.strftime("%Y-%m-%d", time.gmtime()),
               "signal_expr": ax.serialize(COMPOSITE)}, open(_OUT, "w", encoding="utf-8"), indent=2)
    print(f"-> wrote {_OUT}")


if __name__ == "__main__":
    main()
