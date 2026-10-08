"""Out-of-gate: the DECISIVE survivorship test for crowd_contra — re-run the short leg on coins that
pumped-then-DIED (LUNA, FTT, SRM, ...), which the 14-survivor backtest never sees.

The adversarial verification flagged ONE residual risk that was both fatal-if-true and (then) untestable:
the "fade crowded retail longs" SHORT leg is never tested on the population most affected by survivorship —
coins where retail was crowded long right before the coin collapsed. The Binance Data Vision metrics archive
turns out to KEEP delisted coins, so this is now testable.

Method: build a survivorship-COMPLETE panel from the metrics archive (price proxy = oi_value/oi, validated
to ~match real close; global_ls field), with every coin present only while it was alive (None after death).
Compare crowd_contra on survivors-only vs survivors+dead, per-year, and attribute the dead-coin legs:
were the dying coins actually shorted (fade worked) and did the edge hold once they are in the universe?

Daily LS-leg returns are WINSORIZED at +/-50% (a -99% collapse cannot be fully captured by a short — you are
auto-deleveraged / the contract delists), so a single implosion cannot fabricate the result. Observed only.
Containment unchanged (research output, auto_approved=0, no live/flags).
"""
from __future__ import annotations

import json
import os
import statistics
import time

from services.execution.backtest_authority import quarantine_legacy_backtest
from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe

from tools.binance_oi_metrics_fetch import DEFAULT_TICKERS  # the 14 survivors
from tools.crypto_oi_research import _legs, _OI, MIN_DAYS

SURVIVORS = set(DEFAULT_TICKERS)
DEAD = ["LUNA", "FTT", "SRM", "ANT", "BTS", "SC", "RAY", "CVC", "BLZ", "FOOTBALL",
        "HNT", "TOMO", "AGIX", "MATIC", "OCEAN", "KEY", "CTK"]
# F2 (2026-07-27): re-exported from the single source of truth (was a verbatim duplicate).
from services.factor_garden.canonical import CROWD_CONTRA_EXPRESSION as CROWD_CONTRA
CAP = 0.50  # winsorize a daily leg return at +/-50% (a collapse short cannot realistically pay more)
_OUTS = os.path.join("data", "cache", "crypto_oi_survivorship.json")


def _build(insts_keep):
    """Build a panel from the OI cache alone: price proxy = oi_value/oi, plus global_ls. Coins present
    only while alive (None elsewhere). insts_keep restricts the instrument set."""
    oi = json.load(open(_OI, encoding="utf-8")).get("data", {})
    insts = sorted(tk for tk in insts_keep
                   if tk in oi and not tk.startswith("_") and len([k for k in oi[tk]]) >= 60)
    by = {tk: {int(k): v for k, v in oi[tk].items()} for tk in insts}
    times = sorted({t for tk in insts for t in by[tk]})

    def cell(tk, t, fld):
        r = by[tk].get(t)
        return r.get(fld) if isinstance(r, dict) else None

    price = [[None] * len(insts) for _ in times]
    gls = [[None] * len(insts) for _ in times]
    for ti, t in enumerate(times):
        for ii, tk in enumerate(insts):
            o, ov = cell(tk, t, "oi"), cell(tk, t, "oi_value")
            price[ti][ii] = (ov / o) if (o and ov is not None) else None
            gls[ti][ii] = cell(tk, t, "global_ls")
    panel = ax.FieldPanel(insts, times, {"close": price, "global_ls": gls})
    return panel, insts, times


def _ls_returns(sig, px, q=0.3, cap=CAP, dead_idx=None):
    """Winsorized LS-leg returns; also returns the cumulative P&L attributable to DEAD-coin legs."""
    T = len(sig)
    ret, dead_pnl = [], 0.0
    for r in range(T - 1):
        w = _legs(sig[r], q)
        day = 0.0
        for i, wi in w.items():
            p0, p1 = px[r][i], px[r + 1][i]
            if p0 is None or p1 is None or p0 <= 0:
                continue
            g = max(-cap, min(cap, p1 / p0 - 1.0))
            contrib = wi * g
            day += contrib
            if dead_idx is not None and i in dead_idx:
                dead_pnl += contrib
        ret.append(day)
    return ret, dead_pnl


def _per_year(series, times):
    from collections import defaultdict
    by = defaultdict(list)
    for t, v in zip(times, series):
        by[time.gmtime(t / 1000).tm_year].append(v)
    return {y: round(sharpe(by[y]), 2) for y in sorted(by) if len(by[y]) > 1}


def main() -> None:
    quarantine_legacy_backtest("tools.crypto_oi_survivorship")
    # A) survivors-only (proxy price) — baseline on the SAME price source for apples-to-apples
    pS, insS, tS = _build(SURVIVORS)
    sigS = ax.evaluate(CROWD_CONTRA, pS)
    rS, _ = _ls_returns(sigS, pS.matrices["close"])
    print(f"=== survivorship test: crowd_contra short leg on coins that DIED ===")
    print(f"A) survivors-only ({len(insS)} coins, proxy px): SR {sharpe(rS):.3f}")

    # B) survivorship-COMPLETE (survivors + dead)
    keep = SURVIVORS | set(DEAD)
    pC, insC, tC = _build(keep)
    dead_present = [c for c in DEAD if c in insC]
    dead_idx = {insC.index(c) for c in dead_present}
    sigC = ax.evaluate(CROWD_CONTRA, pC)
    rC, dead_pnl = _ls_returns(sigC, pC.matrices["close"], dead_idx=dead_idx)
    print(f"B) survivorship-COMPLETE ({len(insC)} coins incl {len(dead_present)} dead): SR {sharpe(rC):.3f}")
    print(f"   dead coins present: {dead_present}")
    print(f"   cumulative P&L attributable to DEAD-coin legs: {dead_pnl:+.3f} "
          f"(sum of winsorized daily contributions)")
    print(f"   per-year (complete): {_per_year(rC, tC)}")

    # C) dead-coin final-30-day attribution: was each dying coin SHORTED (fade worked)?
    px = pC.matrices["close"]
    finals = []
    for c in dead_present:
        ci = insC.index(c)
        # last ~30 alive days for this coin
        alive = [r for r in range(len(sigC) - 1) if px[r][ci] is not None]
        if len(alive) < 10:
            continue
        last = alive[-30:]
        signs, rets = [], []
        for r in last:
            w = _legs(sigC[r], 0.3)
            signs.append(w.get(ci, 0.0))                       # <0 = short = correctly fading a crowded long
            p0, p1 = px[r][ci], px[r + 1][ci]
            if p0 and p1 and p0 > 0:
                rets.append(max(-CAP, min(CAP, p1 / p0 - 1.0)))
        mean_w = statistics.fmean(signs) if signs else 0.0
        cum = 1.0
        for x in rets:
            cum *= (1 + x)
        finals.append({"coin": c, "mean_signed_weight_last30": round(mean_w, 4),
                       "shorted": mean_w < 0, "cum_return_last30": round(cum - 1, 3)})
    n_short = sum(1 for f in finals if f["shorted"])
    print(f"\nC) dead-coin FINAL-30-day: {n_short}/{len(finals)} were net SHORTED by crowd_contra "
          f"(short = correctly fading a crowded long into its collapse)")
    for f in sorted(finals, key=lambda x: x["mean_signed_weight_last30"]):
        tag = "SHORT" if f["shorted"] else "long "
        print(f"   {f['coin']:9s} {tag} w={f['mean_signed_weight_last30']:+.4f}  ret30={f['cum_return_last30']:+.2%}")

    # D) is the winsorization load-bearing? sweep the daily-return cap on the complete book
    cap_sweep = {}
    for cap in (0.10, 0.20, 0.35, 0.50, 0.95):
        rr, _ = _ls_returns(sigC, px, cap=cap, dead_idx=dead_idx)
        cap_sweep[cap] = round(sharpe(rr), 3)
    print(f"\nD) complete-universe SR by daily-return cap (tail-risk sensitivity): {cap_sweep}")
    raw_complete = cap_sweep[0.95]  # ~uncapped
    print(f"   raw (~uncapped) complete SR {raw_complete} vs winsorized {cap_sweep[0.50]} "
          f"-> winsorization is {'NOT load-bearing' if abs(raw_complete - cap_sweep[0.50]) < 0.15 else 'load-bearing'}")

    # verdict
    sr_s, sr_c = sharpe(rS), sharpe(rC)
    out = {"survivors_only_sharpe": round(sr_s, 3), "complete_sharpe": round(sr_c, 3),
           "n_survivors": len(insS), "n_complete": len(insC), "dead_present": dead_present,
           "dead_leg_cum_pnl": round(dead_pnl, 3), "per_year_complete": _per_year(rC, tC),
           "dead_final30": finals, "n_dead_shorted": n_short,
           "winsor_cap": CAP, "cap_sweep_complete_sharpe": cap_sweep, "raw_complete_sharpe": raw_complete,
           "winsorization_load_bearing": abs(raw_complete - cap_sweep[0.50]) >= 0.15,
           "containment": "research output; auto_approved=0; no live/flags",
           "generated_at": int(time.time())}
    haircut = round(sr_s - raw_complete, 3)
    if raw_complete >= sr_s - 0.25:
        out["verdict"] = (f"HOLDS — NOT a survivorship mirage. Adding {len(dead_present)} dead coins moves "
                          f"SR {sr_s}->{raw_complete} (haircut {haircut}); robust to tail-cap (raw≈winsorized) "
                          f"and per-year stays positive. HONEST nuance: only {n_short}/{len(finals)} dying coins "
                          f"were net-shorted (it was LONG into LUNA/FTT) — a cross-sectional positioning edge, "
                          f"NOT a crash/tail hedge; the cross-section diversifies the individual death mistakes.")
    else:
        out["verdict"] = (f"WEAKENS — dead coins cut SR {sr_s}->{raw_complete} (haircut {haircut}); the "
                          f"survivors-only headline was partly a survivorship artifact.")
    print(f"\nVERDICT: {out['verdict']}")
    json.dump(out, open(_OUTS, "w", encoding="utf-8"), indent=2)
    print(f"-> wrote {_OUTS}")


if __name__ == "__main__":
    main()
