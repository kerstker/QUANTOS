"""Out-of-gate, OFFLINE regime-conditional analysis of the DAILY trend finding.

Answers the one question the cost/concentration tests could NOT: "does dist_from_high only earn
in a rising market?" Reads only the persisted daily PIT store (data/cache/crypto_daily.db) — NO
network. For each top factor it builds the per-period dollar-neutral net return AND an equal-weight
market return for the SAME periods, then slices the factor's returns two ways (both PIT, no-lookahead):

  A) VOLATILITY regime (services.research.regime.label_regimes_by_vol on the market equity curve;
     calm / normal / storm by EXPANDING-window vol percentile) — does the edge survive high-vol stress?
  B) MARKET-DIRECTION regime (sign of the market's trailing return, strictly past) — does the edge
     survive flat/down markets, or is it a pure up-trend bet?

Reports per-regime n / mean / (raw, un-annualized) Sharpe / positive_frac per factor. A factor that
is positive in BOTH up AND down/flat is robust; one that is strongly positive in up and negative in
down/flat is a directional trend bet (the regime risk, quantified). Records/promotes/fetches nothing.
"""
from __future__ import annotations

import json
import os

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research.panel_builder import build_panel_periods
from services.research.signals import SIGNAL_OPS
from services.research.regime import label_regimes_by_vol, regime_conditional_stats
from services.alpha.panel_backtest import run_panel_backtest

FWD_HORIZON = 1
TREND_WINDOW = 14                       # trailing market window for the up/down label
FACTORS = (("dist_from_high", 14), ("dist_from_high", 7), ("accel", 14), ("momentum", 7))
_DAILY_DB = os.path.join("data", "cache", "crypto_daily.db")


def _periods(store, instruments, event_times, op, window):
    fn = SIGNAL_OPS[op]
    return build_panel_periods(store, instruments, event_times=event_times, fwd_horizon=FWD_HORIZON,
                              signal_fn=lambda closes: fn(closes, window))


def _market_returns(periods) -> list:
    """Equal-weight market return per period = mean of that cross-section's forward returns."""
    return [sum(p.forward_returns) / len(p.forward_returns) if p.forward_returns else 0.0
            for p in periods]


def _trend_labels(market_ret, window) -> list:
    """PIT up(1)/down(0) by the sign of the market's STRICTLY-PAST trailing `window` return.
    None until `window` past returns exist (no-lookahead)."""
    labels = [None] * len(market_ret)
    for i in range(len(market_ret)):
        if i < window:
            continue
        comp = 1.0
        for j in range(i - window, i):       # strictly past: [i-window, i-1]
            comp *= (1.0 + market_ret[j])
        labels[i] = 1 if comp > 1.0 else 0
    return labels


def _fmt(stats, names) -> str:
    out = []
    for lab in sorted(stats):
        s = stats[lab]
        nm = names.get(lab, str(lab))
        sh = f"{s['sharpe']:.4f}" if isinstance(s['sharpe'], (int, float)) else str(s['sharpe'])
        out.append(f"{nm}: n={s['n']:>3} mean={s['mean']} sharpe={sh} pos_frac={s['positive_frac']}")
    return "  |  ".join(out)


def main() -> None:
    store = CachingPITStore(SqlitePITStore(_DAILY_DB, create=False))
    snap_path = os.path.join("data", "cache", "crypto_research_snapshot.json")
    instruments = sorted(c["instrument_id"] for c in
                         json.load(open(snap_path, encoding="utf-8")).get("meta", {}).get("coins", []))
    if len(instruments) < 2:
        raise SystemExit("need >=2 instruments; run tools/crypto_daily_research.py first")
    event_times = sorted({b.event_time for inst in instruments
                          for b in store.fetch_bars(inst, 0, 10 ** 14)})
    print(f"instruments={len(instruments)} daily periods={len(event_times)} trend_window={TREND_WINDOW}\n")

    vol_names = {0: "calm", 1: "normal", 2: "storm"}
    dir_names = {0: "down/flat market", 1: "up market"}
    for op, w in FACTORS:
        per = _periods(store, instruments, event_times, op, w)
        if not per:
            print(f"h_{op}_{w}: no periods"); continue
        fac_ret = list(run_panel_backtest(per).period_returns)     # net @ default 3bps
        mkt_ret = _market_returns(per)
        # market equity curve for the vol-regime labeller
        eq, v = [1.0], 1.0
        for r in mkt_ret:
            v *= (1.0 + r); eq.append(v)
        vol_labels = label_regimes_by_vol(eq[1:], vol_window=20, n_regimes=3)   # align to periods
        dir_labels = _trend_labels(mkt_ret, TREND_WINDOW)

        vol_stats = regime_conditional_stats(fac_ret, vol_labels)
        dir_stats = regime_conditional_stats(fac_ret, dir_labels)
        print(f"=== h_{op}_{w}  ({len(per)} periods) ===")
        print(f"  by VOL  : { _fmt(vol_stats, vol_names) }")
        print(f"  by TREND: { _fmt(dir_stats, dir_names) }")
        print()


if __name__ == "__main__":
    main()
