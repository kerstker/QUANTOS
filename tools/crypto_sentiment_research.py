"""Does market-wide SENTIMENT (Fear & Greed) time BTC — and does it add anything over plain price
mean-reversion, given F&G is itself ~half price-derived?

A genuinely-different-KIND-of-data probe (vs the mined-out price/volume/funding/positioning). Tests the
classic CONTRARIAN-sentiment prior (extreme fear -> buy, extreme greed -> sell) on BTC daily, 2021-2026
(2021 bull, 2022 bear, 2023 recovery, 2024-25 cycle). Honesty-first gauntlet:
  - real benchmark = BTC BUY-AND-HOLD Sharpe (a timing strategy that can't beat holding is useless);
  - per-year regime breakdown (incl the 2022 bear) — the decisive cross-regime test;
  - the generalization_gap (the in-search overfit signal just added) on the strategy stream;
  - no-peek walk-forward selection OOS over the specs + buy-and-hold;
  - **ORTHOGONALITY control**: a pure BTC 5-day return-reversal signal — if F&G-contra ≈ price-reversal,
    the "sentiment edge" is just short-term mean-reversion repackaged (F&G is ~25% volatility + ~25%
    momentum/volume by construction), NOT a new orthogonal source.
PIT-safe (signal at t, return t->t+1). Observed numbers only; the verdict is whatever the machine reports.
"""
from __future__ import annotations

import json
import os
import statistics

from services.data.persistent_store import SqlitePITStore
from services.research.backtest_engine.metrics import sharpe
from services.research.oos_validation import walk_forward_selection_oos
from services.intelligence.reward import generalization_gap

_FG = os.path.join("data", "cache", "crypto_fear_greed.json")
_BTC_DB = os.path.join("data", "cache", "crypto_history.db")
_OUT = os.path.join("data", "cache", "crypto_sentiment_research.json")
_DAY_MS = 86_400_000


def _load_aligned():
    fg_raw = json.load(open(_FG, encoding="utf-8"))["data"]
    fg = {int(t): float(v) for t, v in fg_raw.items()}
    bars = SqlitePITStore(_BTC_DB, create=False).fetch_bars("BTC", 0, 10**18)
    close = {(b.event_time // _DAY_MS) * _DAY_MS: b.close for b in bars}
    days = sorted(set(fg) & set(close))
    fgv = [fg[d] for d in days]
    px = [close[d] for d in days]
    fwd = [(px[i + 1] / px[i] - 1.0) for i in range(len(px) - 1)]  # t -> t+1 simple return
    return days, fgv, px, fwd


def _zchg(series, w):
    out = [0.0] * len(series)
    for i in range(w, len(series)):
        out[i] = series[i] - series[i - w]
    return out


def _strategy(pos, fwd, cost_bps=0.0):
    """Daily strategy return = position[t] * fwd_return[t] - cost*|Δposition|. pos/fwd same length-1."""
    n = len(fwd)
    rets, prev, turn = [], 0.0, 0.0
    for t in range(n):
        p = pos[t]
        turn += abs(p - prev)
        rets.append(p * fwd[t] - (cost_bps * 1e-4) * abs(p - prev))
        prev = p
    return rets, turn / max(1, n)


def _corr(a, b):
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((y - mb) ** 2 for y in b) ** 0.5
    return round(num / (da * db), 3) if da > 0 and db > 0 else None


def _per_year(rets, days):
    import datetime
    buckets = {}
    for r, d in zip(rets, days):
        yr = datetime.datetime.utcfromtimestamp(d / 1000).year
        buckets.setdefault(yr, []).append(r)
    return {str(y): round(sharpe(v), 2) for y, v in sorted(buckets.items()) if len(v) > 20}


def main() -> None:
    days, fg, px, fwd = _load_aligned()
    T = len(fwd)
    print(f"aligned BTC × Fear&Greed: {T+1} days, "
          f"{__import__('datetime').datetime.utcfromtimestamp(days[0]/1000).date()} -> "
          f"{__import__('datetime').datetime.utcfromtimestamp(days[-1]/1000).date()}")

    # forward-return signals are evaluated PIT-safe: position[t] uses fg[t] (known eod), earns fwd[t]=t->t+1.
    fg_t = fg[:T]
    chg5 = _zchg(fg, 5)[:T]
    # a-priori CONTRARIAN sentiment specs (positions in [-1,1]); plus the price-reversal orthogonality control.
    btc_chg5 = _zchg([0.0] + [(px[i] / px[i - 1] - 1.0) for i in range(1, len(px))], 5)[:T]
    specs = {
        "fg_contra_level": [-(v - 50.0) / 50.0 for v in fg_t],                 # fear->long, greed->short
        "fg_extreme": [1.0 if v < 25 else -1.0 if v > 75 else 0.0 for v in fg_t],  # act only at extremes
        "fg_long_only_fear": [max(0.0, (50.0 - v) / 50.0) for v in fg_t],      # long-only, scaled by fear
        "fg_momentum5": [max(-1.0, min(1.0, c / 20.0)) for c in chg5],         # follow rising sentiment
        "price_reversal5_CONTROL": [max(-1.0, min(1.0, -c * 20.0)) for c in btc_chg5],  # pure price MR
    }

    bh = sharpe(fwd)  # BTC buy-and-hold Sharpe over the window — the bar to beat
    print(f"\nBTC buy-and-hold Sharpe (the bar to beat): {bh:.3f}")

    rows, streams = [], {"buy_and_hold": tuple(fwd)}
    for name, pos in specs.items():
        rets, turn = _strategy(pos, fwd)
        net10, _ = _strategy(pos, fwd, cost_bps=10.0)
        rows.append({
            "name": name, "corr_signal_fwd": _corr(pos, fwd), "sharpe": round(sharpe(rets), 3),
            "net_sharpe_10bps": round(sharpe(net10), 3), "turnover": round(turn, 3),
            "vs_buy_hold": round(sharpe(rets) - bh, 3),
            "gen_gap": (lambda g: None if g is None else round(g, 3))(generalization_gap(rets)),
            "corr_vs_price_reversal": None,
        })
        streams[name] = tuple(rets)
    # orthogonality: how correlated is each sentiment strategy stream with the pure price-reversal control?
    ctrl = streams["price_reversal5_CONTROL"]
    for r in rows:
        if r["name"] != "price_reversal5_CONTROL":
            r["corr_vs_price_reversal"] = _corr(list(streams[r["name"]]), list(ctrl))

    rows.sort(key=lambda r: -r["sharpe"])
    print(f"\n{'spec':26s} {'corr':>6s} {'SR':>6s} {'net@10':>7s} {'turn':>5s} {'vsBH':>6s} {'genGap':>6s} {'~priceMR':>8s}")
    for r in rows:
        c = "" if r["corr_signal_fwd"] is None else f"{r['corr_signal_fwd']:+.3f}"
        pm = "" if r["corr_vs_price_reversal"] is None else f"{r['corr_vs_price_reversal']:+.2f}"
        gg = "" if r["gen_gap"] is None else f"{r['gen_gap']:.2f}"
        print(f"{r['name']:26s} {c:>6s} {r['sharpe']:>6.2f} {r['net_sharpe_10bps']:>7.2f} "
              f"{r['turnover']:>5.2f} {r['vs_buy_hold']:>+6.2f} {gg:>6s} {pm:>8s}")

    # per-year regime breakdown for the best sentiment spec (exclude the price control)
    best = next((r for r in rows if r["name"] != "price_reversal5_CONTROL"), rows[0])
    by_year = _per_year(list(streams[best["name"]]), days[:T])
    print(f"\nper-year Sharpe [{best['name']}]: {by_year}")
    print(f"per-year Sharpe [buy_and_hold]: {_per_year(fwd, days[:T])}")

    # no-peek selection OOS over all specs + buy-and-hold
    oos = walk_forward_selection_oos(streams, train_size=max(252, T // 4),
                                     test_size=max(120, T // 8), embargo=1, top_k=1)
    print(f"\nno-peek selection OOS: folds={oos['n_folds']} mean_IS={oos.get('mean_is_sharpe')} "
          f"mean_OOS={oos.get('mean_oos_sharpe')} haircut={oos.get('oos_minus_is')}")
    for fold in oos.get("folds", []):
        print(f"  fold {fold['window']}: picked {fold['selected']} IS {fold['is_sharpe']:+.2f} OOS {fold['oos_sharpe']:+.2f}")

    # honest verdict
    sent = [r for r in rows if r["name"] != "price_reversal5_CONTROL"]
    beats_bh = [r for r in sent if r["vs_buy_hold"] > 0.3 and r["net_sharpe_10bps"] > 0.3]
    price_proxy = [r for r in sent if (r["corr_vs_price_reversal"] or 0) > 0.6]
    if beats_bh and any(r["name"] in {f["selected"][0] for f in oos.get("folds", []) if f["oos_sharpe"] > 0}
                        for r in beats_bh):
        verdict = (f"CANDIDATE sentiment-timing edge: {[r['name'] for r in beats_bh]} beat buy-and-hold "
                   f"net of cost AND survive no-peek OOS. Check price-reversal orthogonality + the "
                   f"~half-price-derived caveat before believing it.")
    elif price_proxy:
        verdict = (f"NO orthogonal sentiment edge: the contrarian specs are largely a PRICE mean-reversion "
                   f"proxy (corr vs price-reversal control > 0.6: {[r['name'] for r in price_proxy]}) — "
                   f"F&G is ~half price-derived, so this is repackaged short-term reversal, not new info.")
    else:
        verdict = ("NO usable sentiment-timing edge: no contrarian spec beats BTC buy-and-hold net of cost "
                   "and survives OOS. Market-wide Fear&Greed does not time BTC at daily horizon.")
    print(f"\nVERDICT: {verdict}")

    json.dump({"n_days": T + 1, "buy_and_hold_sharpe": round(bh, 3), "specs": rows,
               "per_year_best": {"spec": best["name"], "by_year": by_year},
               "per_year_buy_hold": _per_year(fwd, days[:T]), "oos": oos, "verdict": verdict,
               "caveat": "Fear&Greed is ~25% volatility + ~25% momentum/volume by construction — "
                         "only the social/dominance/trends slice is orthogonal to price."},
              open(_OUT, "w", encoding="utf-8"), indent=2)
    print(f"\n-> wrote {_OUT}")


if __name__ == "__main__":
    main()
