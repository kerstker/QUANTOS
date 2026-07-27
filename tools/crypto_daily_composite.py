"""Out-of-gate, OFFLINE composite multi-factor signal — the "signal perfection" step.

Goes from "we found good individual factors" to "here is ONE robust combined alpha". Reads only
the persisted daily PIT store (data/cache/crypto_daily.db) — NO network. For each period it computes
several sub-signals per instrument, CROSS-SECTIONALLY z-scores each (so different-scale signals
combine fairly), averages them into a composite, and runs the same dollar-neutral long/short book.

Then it puts the composite through the full battery and compares it to the best single factor:
  - daily vs WEEKLY rebalance (turnover / cost trade-off; weekly annualizes with 52, daily with 252);
  - no-peek walk-forward SELECTION OOS over {singles + composite} — does selection pick the composite
    and does it hold out-of-sample?
  - COST STRESS (one-way bps sweep -> breakeven);
  - REGIME split (vol calm/storm + up/down market) — is the composite robust across states?

PIT / no-lookahead throughout (signals use only bars knowable at t; z-score is within-period cross
section; market-trend label uses strictly-past returns). Records/promotes/fetches nothing.
"""
from __future__ import annotations

import json
import math
import os
import statistics

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research.signals import SIGNAL_OPS
from services.research.backtest_engine.costs import CostModel
from services.research.backtest_engine.metrics import sharpe
from services.research.regime import label_regimes_by_vol, regime_conditional_stats
from services.validation.walk_forward import walk_forward_splits
from services.research.oos_validation import walk_forward_selection_oos
from services.alpha.panel_backtest import PanelPeriod, run_panel_backtest

FWD_HORIZON = 1
# the composite: the dominant trend-proximity family (+ a momentum leg for diversification)
# F2 (2026-07-27): MOVED to services/factor_garden/canonical.py (single source of truth,
# shared with dry_run_trend_multi); re-exported here under the historical name.
from services.factor_garden.canonical import TREND_COMPOSITE_EXPRESSION as COMPOSITE
# single-factor pool the composite competes against in the OOS selection
SINGLES = (("dist_from_high", 7), ("dist_from_high", 14), ("dist_from_high", 30),
           ("accel", 14), ("momentum", 7))
BEST_SINGLE = ("dist_from_high", 14)
COST_LEVELS_BPS = (3, 10, 20, 30, 50, 70)
TREND_WINDOW = 14
_DAILY_DB = os.path.join("data", "cache", "crypto_daily.db")


def _instruments():
    snap = json.load(open(os.path.join("data", "cache", "crypto_research_snapshot.json"), encoding="utf-8"))
    return sorted(c["instrument_id"] for c in snap.get("meta", {}).get("coins", []))


def _single_periods(store, instruments, event_times, op, window):
    insts, ets = sorted(instruments), sorted(event_times)
    fn = SIGNAL_OPS[op]
    periods = []
    for i, t in enumerate(ets):
        j = i + FWD_HORIZON
        if j >= len(ets):
            break
        t_fwd = ets[j]
        rows = []
        for inst in insts:
            hist = store.as_of(inst, t)
            by_close = {}
            for b in hist:
                by_close[b.event_time] = b.close
            closes = [by_close[e] for e in sorted(by_close)]
            now_bar, fwd_bar = store.latest_as_of(inst, t), store.latest_as_of(inst, t_fwd)
            if (not closes or now_bar is None or fwd_bar is None or now_bar.close == 0
                    or fwd_bar.event_time <= now_bar.event_time):
                continue
            s = fn(closes, window)
            if s is None or not math.isfinite(s):
                continue
            rows.append((inst, float(s), fwd_bar.close / now_bar.close - 1.0))
        if len(rows) >= 2:
            periods.append(PanelPeriod(tuple(r[0] for r in rows), tuple(r[1] for r in rows),
                                       tuple(r[2] for r in rows)))
    return periods


def _composite_periods(store, instruments, event_times, sub_specs):
    """Per period: compute every sub-signal per instrument, cross-sectionally z-score each, average."""
    insts, ets = sorted(instruments), sorted(event_times)
    periods = []
    for i, t in enumerate(ets):
        j = i + FWD_HORIZON
        if j >= len(ets):
            break
        t_fwd = ets[j]
        rows = []                          # (inst, [sub values], fwd_ret)
        for inst in insts:
            hist = store.as_of(inst, t)
            by_close = {}
            for b in hist:
                by_close[b.event_time] = b.close
            closes = [by_close[e] for e in sorted(by_close)]
            now_bar, fwd_bar = store.latest_as_of(inst, t), store.latest_as_of(inst, t_fwd)
            if (not closes or now_bar is None or fwd_bar is None or now_bar.close == 0
                    or fwd_bar.event_time <= now_bar.event_time):
                continue
            subs, ok = [], True
            for op, w in sub_specs:
                s = SIGNAL_OPS[op](closes, w)
                if s is None or not math.isfinite(s):
                    ok = False
                    break
                subs.append(float(s))
            if ok:
                rows.append((inst, subs, fwd_bar.close / now_bar.close - 1.0))
        if len(rows) < 3:                  # need a cross-section to z-score
            continue
        comp = []
        for k in range(len(sub_specs)):
            vals = [r[1][k] for r in rows]
            mu, sd = statistics.fmean(vals), statistics.pstdev(vals)
            comp.append([(v - mu) / sd if sd > 0 else 0.0 for v in vals])
        composite = [sum(comp[k][idx] for k in range(len(sub_specs))) / len(sub_specs)
                     for idx in range(len(rows))]
        periods.append(PanelPeriod(tuple(r[0] for r in rows), tuple(composite),
                                   tuple(r[2] for r in rows)))
    return periods


def _net(periods, one_way_bps):
    cm = CostModel(fee_bps=0.0, slippage_bps=float(one_way_bps))
    res = run_panel_backtest(periods, cm)
    return list(res.period_returns), (statistics.fmean(res.turnovers) if res.turnovers else 0.0)


def _track_oos(returns, ppy):
    """Track ONE return series IS-vs-OOS across walk-forward folds; annualized by ppy."""
    L = len(returns)
    test_size, train_size = max(8, L // 8), max(20, L // 2)
    folds = []
    for _, (train, test) in enumerate(walk_forward_splits(L, train_size, test_size, test_size)):
        tr = train[:-FWD_HORIZON] if FWD_HORIZON < len(train) else train
        if len(tr) < 2:
            continue
        folds.append((sharpe([returns[j] for j in tr], ppy), sharpe([returns[j] for j in test], ppy)))
    oos = [o for _, o in folds]
    return (round(statistics.fmean(oos), 3) if oos else None,
            round(sum(1 for o in oos if o > 0) / len(oos), 2) if oos else None, len(oos))


def _market_returns(periods):
    return [sum(p.forward_returns) / len(p.forward_returns) if p.forward_returns else 0.0 for p in periods]


def _trend_labels(mkt, window):
    labels = [None] * len(mkt)
    for i in range(len(mkt)):
        if i < window:
            continue
        comp = 1.0
        for j in range(i - window, i):
            comp *= (1.0 + mkt[j])
        labels[i] = 1 if comp > 1.0 else 0
    return labels


def main() -> None:
    store = CachingPITStore(SqlitePITStore(_DAILY_DB, create=False))
    instruments = _instruments()
    event_times = sorted({b.event_time for inst in instruments
                          for b in store.fetch_bars(inst, 0, 10 ** 14)})
    # data-completeness report (survivorship-free panel logic drops short/stale names per-period,
    # so a truncated series — e.g. MATIC after the POL rename — never fabricates returns).
    bars = {inst: len(store.fetch_bars(inst, 0, 10 ** 14)) for inst in instruments}
    full = sum(1 for n in bars.values() if n >= len(event_times) - 5)
    partial = sorted((n, inst) for inst, n in bars.items() if n < len(event_times) - 5)
    print(f"instruments={len(instruments)} daily periods={len(event_times)}  "
          f"full-history={full} partial={len(partial)}")
    if partial:
        print("  partial/short series: " + ", ".join(f"{inst}({n})" for n, inst in partial))
    comp_label = "+".join(f"{op}{w}" for op, w in COMPOSITE)
    print(f"composite = mean z-score of [{comp_label}]\n")

    # ---- DAILY: composite vs singles, no-peek selection OOS ----
    comp_daily = _composite_periods(store, instruments, event_times, COMPOSITE)
    comp_ret, comp_to = _net(comp_daily, 3)
    pool = {}
    for op, w in SINGLES:
        per = _single_periods(store, instruments, event_times, op, w)
        r, _ = _net(per, 3)
        if len(r) >= 2:
            pool[f"h_{op}_{w}"] = r
    pool["COMPOSITE"] = comp_ret
    L = min(len(v) for v in pool.values())
    aligned = {k: v[-L:] for k, v in pool.items()}
    sel = walk_forward_selection_oos(aligned, train_size=max(20, L // 2),
                                     test_size=max(8, L // 8), step=max(8, L // 8),
                                     embargo=FWD_HORIZON, top_k=1)
    picks = [f["selected"][0] for f in sel["folds"]]
    comp_oos_mean, comp_oos_pos, nf = _track_oos(aligned["COMPOSITE"], 252)
    best_oos_mean, best_oos_pos, _ = _track_oos(aligned[f"h_{BEST_SINGLE[0]}_{BEST_SINGLE[1]}"], 252)

    print("=== DAILY: no-peek SELECTION OOS over {singles + composite} ===")
    print(f"  fold picks: {picks}")
    print(f"  pool mean IS={sel['mean_is_sharpe']} mean OOS={sel['mean_oos_sharpe']} "
          f"OOS+ frac={sel['oos_positive_frac']} (n_folds={sel['n_folds']})")
    print(f"  COMPOSITE   full net Sharpe(252)={sharpe(aligned['COMPOSITE']):.3f} "
          f"turnover={comp_to:.3f}  tracked OOS mean={comp_oos_mean} OOS+={comp_oos_pos}")
    print(f"  best single {BEST_SINGLE}: full Sharpe={sharpe(aligned['h_%s_%s' % BEST_SINGLE]):.3f} "
          f"tracked OOS mean={best_oos_mean} OOS+={best_oos_pos}")

    # ---- COST STRESS on composite ----
    print("\n=== COMPOSITE cost stress (full-sample net Sharpe, ann 252) ===")
    print(f"  {'one-way bps':>11} {'net Sharpe':>11} {'mean turnover':>13}")
    for bps in COST_LEVELS_BPS:
        r, to = _net(comp_daily, bps)
        print(f"  {bps:>11} {sharpe(r):>11.3f} {to:>13.3f}")

    # ---- WEEKLY rebalance variant ----
    weekly_ets = event_times[::7]
    comp_weekly = _composite_periods(store, instruments, weekly_ets, COMPOSITE)
    print("\n=== WEEKLY rebalance (every 7th day, annualized by 52) ===")
    print(f"  {'one-way bps':>11} {'net Sharpe':>11} {'mean turnover':>13}")
    for bps in COST_LEVELS_BPS:
        r, to = _net(comp_weekly, bps)
        print(f"  {bps:>11} {sharpe(r, 52):>11.3f} {to:>13.3f}")

    # ---- REGIME split on the composite (daily) ----
    mkt = _market_returns(comp_daily)
    eq, v = [1.0], 1.0
    for r in mkt:
        v *= (1.0 + r)
        eq.append(v)
    vol_labels = label_regimes_by_vol(eq[1:], vol_window=20, n_regimes=3)
    dir_labels = _trend_labels(mkt, TREND_WINDOW)
    vol_stats = regime_conditional_stats(comp_ret, vol_labels)
    dir_stats = regime_conditional_stats(comp_ret, dir_labels)
    vn = {0: "calm", 1: "normal", 2: "storm"}
    dn = {0: "down/flat", 1: "up"}
    print("\n=== COMPOSITE regime split (raw per-period Sharpe) ===")
    print("  by VOL  : " + "  |  ".join(
        f"{vn.get(k, k)}: n={s['n']} sharpe={s['sharpe']} pos={s['positive_frac']}"
        for k, s in sorted(vol_stats.items())))
    print("  by TREND: " + "  |  ".join(
        f"{dn.get(k, k)}: n={s['n']} sharpe={s['sharpe']} pos={s['positive_frac']}"
        for k, s in sorted(dir_stats.items())))


if __name__ == "__main__":
    main()
