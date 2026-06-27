"""Out-of-gate ADVERSARIAL refutation of the crowd-positioning candidate (crowd_contra / top_contra).

crypto_oi_research surfaced `crowd_contra` = cs_rank(neg(global_ls)) ("fade crowded retail longs") as a
~carry-orthogonal price signal (R^2|carry ~0.5%, no-peek OOS held in 4/6 folds). Before it can be called
a real SECOND edge it must survive refutation. This harness tries to KILL it on the real cached panel:

  1. per-year regime robustness (one lucky year vs broad)
  2. turnover + net Sharpe after costs (2/5/10/20 bps)  -- positioning signals can churn
  3. quantile robustness (q = 0.2 / 0.3 / 0.4)
  4. universe-subset robustness (drop BTC / drop top-4 / even / odd coins)
  5. EXECUTION-LAG: trade one full day late (signal t -> enter t+1 -> exit t+2). If the edge needs same-day
     entry it is a timing artifact; a real positioning edge survives a 1-day lag.
  6. combine with carry: synergistic incremental IR + combined Sharpe (does it actually ADD to carry?)
  7. tighter embargoed no-peek OOS (embargo=2, more folds)
  8. direction sanity (the opposite sign must be symmetrically bad)

Observed numbers only; the verdict (survives / dies) is whatever the machine reports.
"""
from __future__ import annotations

import json
import os
import statistics

from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe
from services.research.oos_validation import walk_forward_selection_oos
from services.research.factor_set import select_synergistic_set, incremental_ir

from tools.crypto_oi_research import build_panel, CARRY, _legs, _rank_ic, _OUT  # reuse the real pipeline

CROWD_CONTRA = ("cs_rank", ("neg", ("field", "global_ls")))
TOP_CONTRA = ("cs_zscore", ("neg", ("field", "top_ls")))
CROWD_FOLLOW = ("cs_rank", ("field", "global_ls"))
_OUTV = os.path.join("data", "cache", "crypto_oi_verify.json")


def _streams(sig, px, fund_mat, q=0.3, lag=0):
    """Per-day price-LS / economic / turnover streams. lag=1 enters one full day after the signal
    (signal[r] -> book held over [r+1, r+2)), to test for same-bar timing artifacts."""
    T = len(sig)
    price_r, econ_r, turn_r = [], [], []
    prev = {}
    for r in range(T - 1 - lag):
        w = _legs(sig[r], q)
        e0, e1 = r + lag, r + 1 + lag
        pr = fu = 0.0
        for i, wi in w.items():
            p0, p1 = px[e0][i], px[e1][i]
            f1 = fund_mat[e1][i] if fund_mat else None
            if p0 is None or p1 is None or p0 <= 0:
                continue
            pr += wi * (p1 / p0 - 1.0)
            if f1 is not None:
                fu += -wi * f1
        names = set(w) | set(prev)
        turn_r.append(sum(abs(w.get(i, 0.0) - prev.get(i, 0.0)) for i in names))
        price_r.append(pr); econ_r.append(pr + fu); prev = w
    return price_r, econ_r, turn_r


def _per_year(series, times):
    import time
    from collections import defaultdict
    by = defaultdict(list)
    for t, v in zip(times, series):
        by[time.gmtime(t / 1000).tm_year].append(v)
    return {y: round(sharpe(by[y]), 2) for y in sorted(by) if len(by[y]) > 1}


def main() -> None:
    panel, instruments = build_panel()
    px = panel.matrices["close"]; fm = panel.matrices["funding"]
    times = panel.event_times
    sig = ax.evaluate(CROWD_CONTRA, panel)
    out = {"universe": instruments, "candidate": "crowd_contra = cs_rank(neg(global_ls))"}

    # baseline streams (q=0.3, no lag)
    pr, ec, tr = _streams(sig, px, fm, q=0.3, lag=0)
    base = {"price_sharpe": round(sharpe(pr), 3), "econ_sharpe": round(sharpe(ec), 3),
            "avg_turnover": round(statistics.fmean(tr), 3)}
    print(f"=== crowd_contra refutation ({len(instruments)} coins, {len(pr)} days) ===")
    print(f"base: price SR {base['price_sharpe']}  econ SR {base['econ_sharpe']}  turnover {base['avg_turnover']}/day")

    # 1. per-year
    out["per_year_price"] = _per_year(pr, times)
    out["per_year_econ"] = _per_year(ec, times)
    print(f"1) per-year price SR: {out['per_year_price']}")
    print(f"   per-year econ  SR: {out['per_year_econ']}")

    # 2. cost sweep (net = price - turnover*fee)
    out["net_sharpe_by_fee_bps"] = {}
    for fee in (2, 5, 10, 20):
        net = [p - t * fee / 1e4 for p, t in zip(pr, tr)]
        out["net_sharpe_by_fee_bps"][fee] = round(sharpe(net), 3)
    print(f"2) net price-SR by fee: {out['net_sharpe_by_fee_bps']}  (turnover {base['avg_turnover']}/day)")

    # 3. quantile robustness
    out["price_sharpe_by_quantile"] = {}
    for q in (0.2, 0.3, 0.4):
        pq, _e, _t = _streams(sig, px, fm, q=q)
        out["price_sharpe_by_quantile"][q] = round(sharpe(pq), 3)
    print(f"3) price SR by quantile: {out['price_sharpe_by_quantile']}")

    # 4. universe subsets (rebuild panel per subset so the cross-section actually changes)
    top4 = {"BTC", "ETH", "BNB", "SOL"}
    subsets = {"drop_BTC": [c for c in instruments if c != "BTC"],
               "drop_top4": [c for c in instruments if c not in top4],
               "even_idx": instruments[::2], "odd_idx": instruments[1::2]}
    out["price_sharpe_by_subset"] = {}
    for name, sub in subsets.items():
        if len(sub) < 5:
            continue
        p2, ins2 = build_panel(only=set(sub))
        s2 = ax.evaluate(CROWD_CONTRA, p2)
        pr2, _e, _t = _streams(s2, p2.matrices["close"], p2.matrices["funding"], q=0.3)
        out["price_sharpe_by_subset"][name] = {"n": len(ins2), "price_sharpe": round(sharpe(pr2), 3)}
    print(f"4) price SR by subset: { {k: v['price_sharpe'] for k, v in out['price_sharpe_by_subset'].items()} }")

    # 5. EXECUTION LAG (the key artifact test)
    out["price_sharpe_by_lag"] = {}
    for lag in (0, 1, 2):
        pl, _e, _t = _streams(sig, px, fm, q=0.3, lag=lag)
        out["price_sharpe_by_lag"][lag] = round(sharpe(pl), 3)
    print(f"5) price SR by execution lag (days): {out['price_sharpe_by_lag']}  <-- survives 1d lag?")

    # 6. combine with carry: incremental IR + combined equal-weight Sharpe
    carry_sig = ax.evaluate(CARRY, panel)
    cpr, cec, _ = _streams(carry_sig, px, fm, q=0.3)
    inc = incremental_ir(pr, {"carry": tuple(cpr)}, min_overlap=120)
    combo = [statistics.fmean([a, b]) for a, b in zip(cpr, pr)]
    syn = select_synergistic_set({"carry": tuple(cpr), "crowd_contra": tuple(pr),
                                  "top_contra": tuple(_streams(ax.evaluate(TOP_CONTRA, panel), px, fm)[0])},
                                 max_k=3, min_incremental_ir=0.0, min_overlap=120)
    out["combine"] = {"carry_sharpe": round(sharpe(cpr), 3), "crowd_sharpe": round(sharpe(pr), 3),
                      "equalweight_combo_sharpe": round(sharpe(combo), 3),
                      "incremental_ir_vs_carry": None if inc is None else round(inc, 3),
                      "synergistic_selected": syn.get("selected")}
    print(f"6) combine: carry {out['combine']['carry_sharpe']} + crowd {out['combine']['crowd_sharpe']} "
          f"-> equal-wt {out['combine']['equalweight_combo_sharpe']}  incIR {out['combine']['incremental_ir_vs_carry']}")
    print(f"   synergistic set: {out['combine']['synergistic_selected']}")

    # 7. tighter embargoed no-peek OOS (crowd vs carry vs follow vs top)
    streams = {"carry": tuple(cpr), "crowd_contra": tuple(pr),
               "top_contra": tuple(_streams(ax.evaluate(TOP_CONTRA, panel), px, fm)[0]),
               "crowd_follow": tuple(_streams(ax.evaluate(CROWD_FOLLOW, panel), px, fm)[0])}
    T = len(cpr)
    oos = walk_forward_selection_oos(streams, train_size=max(200, T // 6),
                                     test_size=max(90, T // 12), embargo=2, top_k=1)
    out["embargoed_oos"] = {"n_folds": oos["n_folds"], "mean_is": oos.get("mean_is_sharpe"),
                            "mean_oos": oos.get("mean_oos_sharpe"), "haircut": oos.get("oos_minus_is"),
                            "picks": [(f["window"], f["selected"], round(f["oos_sharpe"], 2)) for f in oos.get("folds", [])]}
    print(f"7) embargoed OOS (embargo=2): folds={oos['n_folds']} mean_OOS={oos.get('mean_oos_sharpe')} "
          f"haircut={oos.get('oos_minus_is')}")
    for f in oos.get("folds", []):
        print(f"   fold {f['window']}: {f['selected']} IS {f['is_sharpe']:+.2f} OOS {f['oos_sharpe']:+.2f}")

    # 8. direction sanity: the opposite sign must be symmetrically bad
    fpr, _e, _t = _streams(ax.evaluate(CROWD_FOLLOW, panel), px, fm, q=0.3)
    out["direction_sanity"] = {"crowd_contra": round(sharpe(pr), 3), "crowd_follow": round(sharpe(fpr), 3)}
    print(f"8) direction: contra {out['direction_sanity']['crowd_contra']} vs follow {out['direction_sanity']['crowd_follow']}")

    # machine verdict from the refutation battery
    survives_lag = out["price_sharpe_by_lag"].get(1, 0) is not None and out["price_sharpe_by_lag"][1] > 0.5
    survives_cost = out["net_sharpe_by_fee_bps"].get(10, 0) > 0.5
    broad_years = sum(1 for v in out["per_year_price"].values() if v > 0) >= max(2, len(out["per_year_price"]) - 1)
    survives_subsets = all(v["price_sharpe"] > 0.3 for v in out["price_sharpe_by_subset"].values())
    adds_to_carry = (out["combine"]["incremental_ir_vs_carry"] or 0) > 0.3
    checks = {"survives_1d_lag": survives_lag, "survives_10bps_cost": survives_cost,
              "broad_across_years": broad_years, "survives_subsets": survives_subsets,
              "adds_to_carry": adds_to_carry}
    out["refutation_checks"] = checks
    n_pass = sum(checks.values())
    out["verdict"] = (f"SURVIVES {n_pass}/5 refutation checks — " +
                      ("strong second-edge candidate; proceed to paper-track" if n_pass >= 4 else
                       "partial; treat as weak lead" if n_pass >= 2 else "FAILS — likely artifact"))
    print(f"\nrefutation checks: {checks}")
    print(f"VERDICT: {out['verdict']}")
    json.dump(out, open(_OUTV, "w", encoding="utf-8"), indent=2)
    print(f"-> wrote {_OUTV}")


if __name__ == "__main__":
    main()
