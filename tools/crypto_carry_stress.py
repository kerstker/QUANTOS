"""Out-of-gate, OFFLINE the REALISTIC-deployable test of the funding carry: liquidity-tiered slippage,
a LIQUID-ONLY subset, and tail/drawdown — turning the optimistic 2.0 into an honest deployable number.

The carry collects ~5.4 bps/day of funding. If realistic per-coin slippage on the illiquid extreme-funding
tail exceeds that, the edge is NOT deployable; if it survives on a LIQUID subset (where slippage is small
and survivorship/capacity are far less of an issue), it is real. We model per-coin slippage by liquidity
tier (median dollar volume), run the same carry composite on (a) ALL coins and (b) the LIQUID HALF, and
report net Sharpe, per-year, MAX DRAWDOWN and WORST DAY (the carry tail). Observed numbers only.
"""
from __future__ import annotations

import json
import os
import statistics
import time

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe, max_drawdown
from tools.crypto_carry_strategy import COMPOSITE, QUANTILE, _legs

_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_FUND_CACHE = os.path.join("data", "cache", "crypto_funding_rates.json")

# conservative per-coin one-way slippage by liquidity tier (median USD daily volume percentile)
def _slip_bps(pct):
    return 5.0 if pct >= 0.75 else 12.0 if pct >= 0.5 else 25.0 if pct >= 0.25 else 50.0


def _run_carry(store, funding, instruments, slip_by_inst, now_ms):
    ets = sorted({b.event_time for inst in instruments for b in store.fetch_bars(inst, -10**18, 10**18)})
    panel = ax.build_field_panel(store, instruments, ets, ("close", "volume"))
    fmat = [[funding.get(inst, {}).get(t) for inst in panel.instruments] for t in panel.event_times]
    panel.matrices["funding"] = fmat
    sig, px = ax.evaluate(COMPOSITE, panel), panel.matrices["close"]
    insts = panel.instruments
    net, times, prev = [], [], {}
    for r in range(len(sig) - 1):
        w = _legs(sig[r], QUANTILE)
        if not w:
            prev = {}
            continue
        pr = fu = 0.0
        for i, wi in w.items():
            p0, p1, f1 = px[r][i], px[r + 1][i], fmat[r + 1][i]
            if p0 is None or p1 is None or p0 <= 0:
                continue
            pr += wi * (p1 / p0 - 1.0)
            if f1 is not None:
                fu += -wi * f1
        names = set(w) | set(prev)
        cost = sum(abs(w.get(i, 0.0) - prev.get(i, 0.0)) * slip_by_inst[insts[i]] / 10000.0 for i in names)
        net.append(pr + fu - cost)
        times.append(panel.event_times[r])
        prev = w
    return net, times


def _per_year(times, series):
    from collections import defaultdict
    by = defaultdict(list)
    for t, v in zip(times, series):
        by[time.gmtime(t / 1000).tm_year].append(v)
    return {y: round(sharpe(by[y]), 2) for y in sorted(by) if len(by[y]) > 1}


def main() -> None:
    instruments = sorted(json.load(open(_SNAP, encoding="utf-8"))["instruments"])
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    funding = {tk: {int(k): v for k, v in d.items()}
               for tk, d in json.load(open(_FUND_CACHE, encoding="utf-8")).items()}

    # per-coin liquidity = median USD daily volume (close * base-volume)
    liq = {}
    for inst in instruments:
        bars = store.fetch_bars(inst, -10**18, 10**18)
        dv = [b.close * b.volume for b in bars if b.close > 0 and b.volume > 0]
        liq[inst] = statistics.median(dv) if dv else 0.0
    ranked = sorted(instruments, key=lambda x: liq[x])
    pctile = {inst: (i + 1) / len(ranked) for i, inst in enumerate(ranked)}
    slip = {inst: _slip_bps(pctile[inst]) for inst in instruments}
    liquid_half = [inst for inst in instruments if pctile[inst] >= 0.5]

    now_ms = int(time.time() * 1000)
    print(f"liquidity tiers: median USD vol p25={liq[ranked[len(ranked)//4]]:,.0f} "
          f"p50={liq[ranked[len(ranked)//2]]:,.0f} p75={liq[ranked[3*len(ranked)//4]]:,.0f}\n")

    for label, universe in (("ALL coins (per-coin slippage)", instruments),
                            ("LIQUID HALF (top 50% by volume)", liquid_half)):
        net, times = _run_carry(store, funding, universe, slip, now_ms)
        eq, v = [1.0], 1.0
        for x in net:
            v *= (1.0 + x)
            eq.append(v)
        sr = sharpe(net)
        mdd = max_drawdown(eq)
        worst = min(net) if net else 0.0
        ann = (eq[-1] ** (252 / len(net)) - 1.0) if net and eq[-1] > 0 else 0.0
        print(f"=== {label}: {len(universe)} coins x {len(times)} days ===")
        print(f"  net Sharpe {sr:.3f}  ann.return {ann*100:.1f}%  maxDD {mdd*100:.1f}%  worst day {worst*100:.2f}%")
        print(f"  per-year: " + " ".join(f"{y}:{vv:+.1f}" for y, vv in _per_year(times, net).items()))
        print()


if __name__ == "__main__":
    main()
