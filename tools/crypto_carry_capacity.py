"""Out-of-gate, OFFLINE CAPACITY + CROWDING analysis of the funding carry (the funder's first question:
"how much money can it hold?"). Reuses the validated carry composite on the liquid subset.

CAPACITY: as AUM grows, each coin's position notional (|w_i|*AUM) and daily TRADED notional (|Δw_i|*AUM) grow
relative to that coin's ADV (median daily USD volume). We charge a square-root market-impact cost on top of the
base tiered slippage: impact_bps_i = IMPACT_AT_10PCT * sqrt(participation_i / 0.10), participation_i =
traded_notional_i / ADV_i. Net return at AUM = price + funding - base_slippage - impact(AUM). We sweep AUM and
report net Sharpe + annual % + annual $ P&L → the capacity curve: where Sharpe halves, and where $ P&L peaks
(beyond that, more capital makes LESS money). IMPACT_AT_10PCT is a modelling assumption (crypto perp ~10-20bps at
10% ADV participation); we report at two coefficients so the reader sees the sensitivity. Observed P&L, modelled impact.

CROWDING: (a) avg funding COLLECTED (bps/day) per year — a compressing premium = the trade getting crowded/arbed;
(b) P&L concentration — share of total carry P&L in the top-5 coins (concentrated = fragile). Local data, no network.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import time
from collections import defaultdict

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe
from tools.crypto_carry_strategy import COMPOSITE, QUANTILE, _legs
from tools.crypto_carry_stress import _slip_bps

_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_FUND_CACHE = os.path.join("data", "cache", "crypto_funding_rates.json")
_OUT = os.path.join("data", "cache", "crypto_carry_capacity.json")

AUM_LEVELS = [1e6, 5e6, 25e6, 1e8, 2.5e8, 5e8, 1e9]
IMPACT_COEFS = [10.0, 20.0]            # bps of impact at 10% ADV participation (sqrt model); show sensitivity


def _carry_book(store, funding, instruments, slip_by_inst):
    """Run the carry composite; return per-day (base_net, per-coin traded fraction, per-coin position fraction,
    funding collected, decision time). adv_by_inst = median daily USD volume per coin."""
    ets = sorted({b.event_time for inst in instruments for b in store.fetch_bars(inst, -10**18, 10**18)})
    panel = ax.build_field_panel(store, instruments, ets, ("close", "volume"))
    fmat = [[funding.get(inst, {}).get(t) for inst in panel.instruments] for t in panel.event_times]
    panel.matrices["funding"] = fmat
    sig, px, vol = ax.evaluate(COMPOSITE, panel), panel.matrices["close"], panel.matrices["volume"]
    insts = panel.instruments
    days, prev = [], {}
    for r in range(len(sig) - 1):
        w = _legs(sig[r], QUANTILE)
        if not w:
            prev = {}
            continue
        base, fu = 0.0, 0.0
        traded, pos = {}, {}
        for i, wi in w.items():
            p0, p1, f1 = px[r][i], px[r + 1][i], fmat[r + 1][i]
            if p0 is None or p1 is None or p0 <= 0:
                continue
            base += wi * (p1 / p0 - 1.0)
            if f1 is not None:
                fu += -wi * f1
            pos[insts[i]] = abs(wi)
        for i in set(w) | set(prev):
            dw = abs(w.get(i, 0.0) - prev.get(i, 0.0))
            if dw > 0:
                traded[insts[i]] = dw
                base -= dw * slip_by_inst[insts[i]] / 10000.0          # base tiered slippage
        days.append({"t": panel.event_times[r], "base": base, "fund": fu, "traded": traded, "pos": pos})
        prev = w
    return days, insts


def main() -> None:
    instruments = sorted(json.load(open(_SNAP, encoding="utf-8"))["instruments"])
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    funding = {tk: {int(k): v for k, v in d.items()}
               for tk, d in json.load(open(_FUND_CACHE, encoding="utf-8")).items()}
    adv = {}
    for inst in instruments:
        dv = [b.close * b.volume for b in store.fetch_bars(inst, -10**18, 10**18) if b.close > 0 and b.volume > 0]
        adv[inst] = statistics.median(dv) if dv else 0.0
    ranked = sorted(instruments, key=lambda x: adv[x])
    pctile = {inst: (i + 1) / len(ranked) for i, inst in enumerate(ranked)}
    slip = {inst: _slip_bps(pctile[inst]) for inst in instruments}
    liquid = [inst for inst in instruments if pctile[inst] >= 0.5 and adv[inst] > 0]

    days, _ = _carry_book(store, funding, liquid, slip)
    base_net = [d["base"] + d["fund"] for d in days]
    base_sr = sharpe(base_net)
    print(f"=== CARRY 容量曲线 · 流动 {len(liquid)} 币 · {len(days)} 天 · 基线净夏普 {base_sr:.2f} ===", flush=True)

    def _net_at(aum, coef):
        out = []
        for d in days:
            impact = 0.0
            for inst, dw in d["traded"].items():
                if adv[inst] <= 0:
                    continue
                participation = (dw * aum) / adv[inst]
                imp_bps = coef * math.sqrt(participation / 0.10) if participation > 0 else 0.0
                impact += dw * imp_bps / 10000.0                       # impact charged on traded fraction
            out.append(d["base"] + d["fund"] - impact)
        return out

    curve = {}
    for coef in IMPACT_COEFS:
        print(f"\n-- impact = {coef:.0f}bps @10%ADV (sqrt model) --", flush=True)
        print(f"{'AUM':>10} {'netSR':>7} {'ann%':>7} {'ann$P&L':>12}", flush=True)
        rows = []
        for aum in AUM_LEVELS:
            net = _net_at(aum, coef)
            sr = sharpe(net)
            ann_ret = statistics.fmean(net) * 252
            ann_pnl = ann_ret * aum
            rows.append({"aum": aum, "net_sharpe": round(sr, 3), "ann_return": round(ann_ret, 4),
                         "ann_pnl_usd": round(ann_pnl)})
            print(f"{aum/1e6:>8.0f}M {sr:>7.2f} {ann_ret*100:>6.1f}% {ann_pnl/1e6:>10.2f}M$", flush=True)
        # capacity = largest AUM whose Sharpe still >= half the base; $-peak = AUM maximizing ann $ P&L
        ok = [r for r in rows if r["net_sharpe"] >= 0.5 * base_sr]
        cap = max((r["aum"] for r in ok), default=0)
        peak = max(rows, key=lambda r: r["ann_pnl_usd"])
        curve[f"impact_{int(coef)}bps"] = {"rows": rows, "capacity_half_sharpe_usd": cap,
                                           "pnl_peak_aum_usd": peak["aum"], "pnl_peak_usd": peak["ann_pnl_usd"]}
        print(f"  容量(夏普≥半)≈ ${cap/1e6:.0f}M · $P&L 峰值 @ ${peak['aum']/1e6:.0f}M = ${peak['ann_pnl_usd']/1e6:.1f}M/yr", flush=True)

    # crowding: funding premium per year + P&L concentration in top-5 coins
    by_year_fund = defaultdict(list)
    coin_pnl = defaultdict(float)
    for d in days:
        by_year_fund[time.gmtime(d["t"] / 1000).tm_year].append(d["fund"] * 10000)   # bps/day collected
    fund_by_year = {y: round(statistics.fmean(v), 2) for y, v in sorted(by_year_fund.items()) if v}
    # per-coin P&L share: approximate by |position| share of the funding collected (carry P&L driver)
    for d in days:
        tot = sum(d["pos"].values()) or 1.0
        for inst, pw in d["pos"].items():
            coin_pnl[inst] += (pw / tot) * d["fund"]
    top5 = sorted(coin_pnl.items(), key=lambda x: -abs(x[1]))[:5]
    tot_abs = sum(abs(v) for v in coin_pnl.values()) or 1.0
    top5_share = round(sum(abs(v) for _, v in top5) / tot_abs, 3)
    print(f"\n=== 拥挤信号 ===", flush=True)
    print(f"  逐年资金费收取(bps/day): {fund_by_year}  ← 逐年压缩=溢价被套利/拥挤", flush=True)
    print(f"  P&L 集中度: top-5 币占 {top5_share*100:.0f}%  ({[t[0] for t in top5]})", flush=True)

    out = {"universe": "liquid_half", "n_liquid": len(liquid), "n_days": len(days), "base_sharpe": round(base_sr, 3),
           "capacity_curve": curve, "funding_bps_per_year": fund_by_year,
           "pnl_top5_share": top5_share, "pnl_top5": [t[0] for t in top5]}
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    json.dump(out, open(_OUT, "w", encoding="utf-8"), indent=2, sort_keys=True)
    print(f"\n-> {_OUT}", flush=True)


if __name__ == "__main__":
    main()
