"""Out-of-gate, OFFLINE volatility-targeting overlay on the funding carry — tames the 25-30% drawdown tail by
de-levering into turbulent regimes. Same liquid subset + per-coin tiered slippage as the carry stress/capacity
tools; applies a trailing-realized-vol leverage scalar (target constant annual vol, capped) to the carry net
daily return, then compares RAW vs VOL-TARGETED on Sharpe / max drawdown / worst day / realized vol / per-year.
Vol-targeting does not manufacture edge (Sharpe ~ unchanged) — it reshapes RISK (lower tail). Observed P&L,
modelled leverage. Local data, no network.
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
from services.execution.backtest_authority import quarantine_legacy_backtest
from services.research.backtest_engine.metrics import sharpe, max_drawdown
from tools.crypto_carry_stress import _run_carry, _slip_bps

_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_FUND_CACHE = os.path.join("data", "cache", "crypto_funding_rates.json")
_OUT = os.path.join("data", "cache", "crypto_carry_voltarget.json")

TARGET_ANN_VOL = 0.10        # target 10% annualized vol
LOOKBACK = 30                # trailing window for realized-vol estimate
LEV_CAP = 3.0                # max leverage (risk cap)


def _vol_target(net, target_ann=TARGET_ANN_VOL, lookback=LOOKBACK, lev_cap=LEV_CAP):
    """Scale each day's return by lev_t = min(cap, target_daily_vol / trailing_realized_vol). PIT: leverage
    at day t uses returns strictly BEFORE t (warmup days pass through unscaled)."""
    target_daily = target_ann / math.sqrt(252)
    out = []
    for t in range(len(net)):
        if t < lookback:
            out.append(net[t])
            continue
        sd = statistics.pstdev(net[t - lookback:t]) or 1e-9
        lev = min(lev_cap, target_daily / sd)
        out.append(lev * net[t])
    return out


def _stats(net):
    if len(net) < 2:
        return {}
    eq, v = [1.0], 1.0
    for x in net:
        v *= (1.0 + x); eq.append(v)
    realized_ann_vol = statistics.pstdev(net) * math.sqrt(252)
    return {"sharpe": round(sharpe(net), 3), "max_drawdown": round(max_drawdown(eq), 3),
            "worst_day": round(min(net), 4), "ann_vol": round(realized_ann_vol, 3),
            "ann_return": round(statistics.fmean(net) * 252, 4)}


def _per_year(times, net):
    by = defaultdict(list)
    for t, v in zip(times, net):
        by[time.gmtime(t / 1000).tm_year].append(v)
    return {y: round(sharpe(by[y]), 2) for y in sorted(by) if len(by[y]) > 1}


def main() -> None:
    quarantine_legacy_backtest("tools.crypto_carry_voltarget")
    instruments = sorted(json.load(open(_SNAP, encoding="utf-8"))["instruments"])
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    funding = {tk: {int(k): v for k, v in d.items()}
               for tk, d in json.load(open(_FUND_CACHE, encoding="utf-8")).items()}
    liq = {}
    for inst in instruments:
        dv = [b.close * b.volume for b in store.fetch_bars(inst, -10**18, 10**18) if b.close > 0 and b.volume > 0]
        liq[inst] = statistics.median(dv) if dv else 0.0
    ranked = sorted(instruments, key=lambda x: liq[x])
    pctile = {inst: (i + 1) / len(ranked) for i, inst in enumerate(ranked)}
    slip = {inst: _slip_bps(pctile[inst]) for inst in instruments}
    liquid = [inst for inst in instruments if pctile[inst] >= 0.5]

    net, times = _run_carry(store, funding, liquid, slip, int(time.time() * 1000))
    raw = _stats(net)
    vt = _vol_target(net)
    vts = _stats(vt)

    print(f"=== CARRY vol-targeting · 流动 {len(liquid)} 币 · {len(net)} 天 · 目标 {TARGET_ANN_VOL*100:.0f}% 年化波动 ===\n", flush=True)
    print(f"{'metric':14s} {'RAW':>10} {'VOL-TARGETED':>14}", flush=True)
    for k, lbl in (("sharpe", "净夏普"), ("ann_return", "年化收益"), ("ann_vol", "年化波动"),
                   ("max_drawdown", "最大回撤"), ("worst_day", "最差单日")):
        print(f"{lbl:14s} {raw.get(k):>10} {vts.get(k):>14}", flush=True)
    print(f"\nper-year Sharpe RAW: {_per_year(times, net)}", flush=True)
    print(f"per-year Sharpe  VT: {_per_year(times, vt)}", flush=True)

    dd_cut = (1 - vts["max_drawdown"] / raw["max_drawdown"]) * 100 if raw.get("max_drawdown") else 0.0
    print(f"\n→ 回撤 {raw['max_drawdown']*100:.0f}% → {vts['max_drawdown']*100:.0f}%(降 {dd_cut:.0f}%);"
          f"夏普 {raw['sharpe']} → {vts['sharpe']}(vol-target 重塑风险、不造 edge)", flush=True)

    json.dump({"universe": "liquid_half", "n_days": len(net), "target_ann_vol": TARGET_ANN_VOL,
               "lookback": LOOKBACK, "lev_cap": LEV_CAP, "raw": raw, "vol_targeted": vts,
               "per_year_raw": _per_year(times, net), "per_year_vt": _per_year(times, vt)},
              open(_OUT, "w", encoding="utf-8"), indent=2, sort_keys=True)
    print(f"-> {_OUT}", flush=True)


if __name__ == "__main__":
    main()
