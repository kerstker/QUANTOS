"""Out-of-gate, OFFLINE anti-overfit validation of the funding carry: a SIMPLICITY LADDER on the LIQUID subset
under per-coin tiered slippage. Addresses the "is the +2.0 just composite-tuning?" concern.

Carry signals here are PARAMETER-FREE expressions (nothing is fit), so the real overfit question is SPECIFICATION
robustness: does the edge survive simplifying the signal? We run, simplest -> tuned:
  raw       = neg(funding)                         (the dumbest carry: short high-funding, long low-funding)
  z_decay7  = cs_zscore(neg(decay_linear(funding,7)))
  composite = the tuned de-redundant COMPOSITE
On the liquid half, net of per-coin liquidity-tiered slippage + funding collected (PIT: signal<=t, P&L t+1).
For each: overall net Sharpe, per-year, max drawdown, and K=5 contiguous TIME BLOCKS (a parameter-free signal
makes every block genuinely out-of-sample-in-time). If even `raw` is net-positive across years+blocks, the carry
is a genuine funding-risk premium, NOT composite overfit. Observed numbers only; reads local funding db, no network.
"""
from __future__ import annotations

import json
import os
import statistics
import time
from collections import defaultdict

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.execution.backtest_authority import quarantine_legacy_backtest
from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe, max_drawdown
from tools.crypto_carry_strategy import FUND, QUANTILE, _legs
from tools.crypto_carry_stress import _slip_bps

_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_FUND_CACHE = os.path.join("data", "cache", "crypto_funding_rates.json")
_OUT = os.path.join("data", "cache", "crypto_carry_walkforward.json")

LADDER = {
    "raw_neg_funding": ("neg", FUND),
    "z_neg_decay7": ("cs_zscore", ("neg", ("decay_linear", FUND, 7))),
    "composite": ("div", ("add", ("add", ("cs_zscore", ("neg", ("decay_linear", FUND, 3))),
                                  ("cs_zscore", ("neg", ("decay_linear", FUND, 7)))),
                          ("add", ("cs_zscore", ("neg", ("decay_linear", FUND, 14))),
                           ("cs_zscore", ("neg", ("ts_mean", FUND, 7))))), ("const", 4.0)),
}
N_BLOCKS = 5


def _run_spec(panel, fmat, px, expr, slip_by_inst):
    sig = ax.evaluate(expr, panel)
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
                fu += -wi * f1                                  # funding collected (PIT: P&L uses t+1)
        cost = sum(abs(w.get(i, 0.0) - prev.get(i, 0.0)) * slip_by_inst[insts[i]] / 10000.0
                   for i in set(w) | set(prev))
        net.append(pr + fu - cost)
        times.append(panel.event_times[r])
        prev = w
    return net, times


def _per_year(times, net):
    by = defaultdict(list)
    for t, v in zip(times, net):
        by[time.gmtime(t / 1000).tm_year].append(v)
    return {y: round(sharpe(by[y]), 2) for y in sorted(by) if len(by[y]) > 1}


def _blocks(net, k=N_BLOCKS):
    if len(net) < k * 2:
        return []
    sz = len(net) // k
    return [round(sharpe(net[b * sz: (b + 1) * sz]), 2) for b in range(k)]


def main() -> None:
    quarantine_legacy_backtest("tools.crypto_carry_walkforward")
    instruments = sorted(json.load(open(_SNAP, encoding="utf-8"))["instruments"])
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    funding = {tk: {int(k): v for k, v in d.items()}
               for tk, d in json.load(open(_FUND_CACHE, encoding="utf-8")).items()}
    # liquidity = median USD daily volume; keep the liquid half
    liq = {}
    for inst in instruments:
        dv = [b.close * b.volume for b in store.fetch_bars(inst, -10**18, 10**18) if b.close > 0 and b.volume > 0]
        liq[inst] = statistics.median(dv) if dv else 0.0
    ranked = sorted(instruments, key=lambda x: liq[x])
    pctile = {inst: (i + 1) / len(ranked) for i, inst in enumerate(ranked)}
    slip = {inst: _slip_bps(pctile[inst]) for inst in instruments}
    liquid = [inst for inst in instruments if pctile[inst] >= 0.5]

    panel_event_times = sorted({b.event_time for inst in liquid
                                for b in store.fetch_bars(inst, -10**18, 10**18)})
    panel = ax.build_field_panel(
        store,
        liquid,
        panel_event_times,
        ("close", "volume"),
        decision_times=[t + 86_400_000 for t in panel_event_times],
        minimum_knowledge_lag=86_400_000,
    )
    fmat = [[funding.get(inst, {}).get(t) for inst in panel.instruments] for t in panel.event_times]
    panel.matrices["funding"] = fmat
    px = panel.matrices["close"]

    print(f"=== CARRY 简化阶梯 · 流动半数({len(liquid)} 币)· 每币分档滑点 · net of funding+slippage ===\n", flush=True)
    print(f"{'spec':16s} {'netSR':>7} {'maxDD':>7} {'worst':>7}  per-year / blocks", flush=True)
    out = {"universe": "liquid_half", "n_liquid": len(liquid), "n_blocks": N_BLOCKS, "specs": {}}
    for name, expr in LADDER.items():
        net, times = _run_spec(panel, fmat, px, expr, slip)
        if not net:
            print(f"{name:16s}  (no returns)"); continue
        eq, v = [1.0], 1.0
        for x in net:
            v *= (1.0 + x); eq.append(v)
        sr, mdd, worst = sharpe(net), max_drawdown(eq), min(net)
        py, bl = _per_year(times, net), _blocks(net)
        out["specs"][name] = {"net_sharpe": round(sr, 3), "max_drawdown": round(mdd, 3),
                              "worst_day": round(worst, 4), "per_year": py, "blocks": bl, "n_days": len(net)}
        print(f"{name:16s} {sr:>7.2f} {mdd*100:>6.1f}% {worst*100:>6.2f}%  "
              f"yr={py} blk={bl}", flush=True)

    # verdict: design-robust iff the SIMPLEST spec is net-positive across most years AND blocks
    raw = out["specs"].get("raw_neg_funding", {})
    comp = out["specs"].get("composite", {})
    raw_yrs_pos = sum(1 for v in raw.get("per_year", {}).values() if v > 0)
    raw_yrs = len(raw.get("per_year", {}) or [1])
    raw_blk_pos = sum(1 for v in raw.get("blocks", []) if v > 0)
    if raw.get("net_sharpe", -9) > 0.5 and raw_yrs_pos >= 0.7 * raw_yrs and raw_blk_pos >= N_BLOCKS - 1:
        verdict = "DESIGN_ROBUST"                              # dumbest carry holds -> genuine premium, not tuning
    elif comp.get("net_sharpe", -9) > 0.5 and raw.get("net_sharpe", -9) <= 0.5:
        verdict = "COMPOSITE_DEPENDENT"                        # only the tuned form works -> overfit risk
    elif comp.get("net_sharpe", -9) > 0.5:
        verdict = "ROBUST_BUT_RAW_WEAKER"
    else:
        verdict = "REFUTED"
    out["verdict"] = verdict
    print(f"\nverdict: {verdict}", flush=True)
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    json.dump(out, open(_OUT, "w", encoding="utf-8"), indent=2, sort_keys=True)
    print(f"-> {_OUT}", flush=True)


if __name__ == "__main__":
    main()
