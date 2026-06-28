"""①  Remaining INTERACTIONS in the existing data (diminishing-returns completeness pass): basis (perp
premium) carry, funding TERM-STRUCTURE (short vs long smoothing), and funding × positioning / × OI
interactions — none of which the single-field / smoothing sweeps tested. Honesty bar = incremental IR
over BOTH carry AND crowd_contra + 1-day lag + 10bps + no-peek OOS. Reuses the PIT-safe panel (with basis)
and the gauntlet from crypto_oi_research. Expected outcome per the mined-out conclusion: little/nothing new;
the value is closing the loop honestly. Observed numbers only.
"""
from __future__ import annotations

import json
import os
import statistics

from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe
from services.research.expression_leakage import compute_leakage_ok
from services.research.oos_validation import walk_forward_selection_oos
from services.alpha.orthogonalize import orthogonalize
from services.research.factor_set import incremental_ir
from services.intelligence.reward import generalization_gap

from tools.crypto_oi_research import build_panel, _returns, _rank_ic, CARRY

_OUT = os.path.join("data", "cache", "crypto_funding_interactions.json")
CROWD_CONTRA = ("cs_rank", ("neg", ("field", "global_ls")))

FUND = ("field", "funding")
# the smoothed carry z-score core, reused inside interactions
CARRY_Z = ("cs_zscore", ("neg", ("ts_mean", FUND, 7)))
BASIS, OI, GLOBAL = ("field", "basis"), ("field", "oi"), ("field", "global_ls")


def _f(name, expr, thesis):
    return {"name": name, "expr": expr, "thesis": thesis}


FACTORS = [
    _f("basis_carry", ("cs_zscore", ("neg", BASIS)),
       "perp premium carry: short rich (high-basis) perps, distinct from funding carry"),
    _f("basis_momentum", ("cs_zscore", ("ts_delta", BASIS, 5)),
       "basis trend: premium building -> momentum"),
    _f("funding_term", ("cs_zscore", ("sub", ("ts_mean", FUND, 3), ("ts_mean", FUND, 14))),
       "funding term structure: short minus long smoothing (carry intensifying?)"),
    _f("carry_x_crowd", ("mul", CARRY_Z, ("cs_rank", ("neg", GLOBAL))),
       "funding carry amplified where the crowd is over-long (stacked contrarian)"),
    _f("carry_x_oi", ("mul", CARRY_Z, ("cs_rank", ("ts_delta", OI, 5))),
       "funding carry weighted by OI build (fresh leverage entering the carry)"),
]


def _net_by_fee(price_r, turn_r, fees=(0, 5, 10, 20)):
    return {f"{f}bps": round(sharpe([p - (f * 1e-4) * t for p, t in zip(price_r, turn_r)]), 3) for f in fees}


def main() -> None:
    panel, instruments = build_panel(add_basis=True)
    px, fund_mat = panel.matrices["close"], panel.matrices["funding"]
    has_basis = "basis" in panel.matrices
    print(f"universe: {len(instruments)} liquid majors · basis={'yes' if has_basis else 'NO'}")

    carry_sig = ax.evaluate(CARRY, panel)
    c_price, _ce, _ct = _returns(carry_sig, px, fund_mat)
    cc_price, _e, _t = _returns(ax.evaluate(CROWD_CONTRA, panel), px, fund_mat)
    basis = {"carry": tuple(c_price), "crowd_contra": tuple(cc_price)}
    print(f"baselines (price SR): carry {sharpe(c_price):.3f} · crowd_contra {sharpe(cc_price):.3f}")

    rows, streams = [], dict(basis)
    for fac in FACTORS:
        if not has_basis and "basis" in str(fac["expr"]):
            continue
        sig = ax.evaluate(fac["expr"], panel)
        leak = compute_leakage_ok(fac["expr"], panel, rows=list(range(50, len(sig), max(1, len(sig) // 40))))
        p_r, _er, t_r = _returns(sig, px, fund_mat)
        orth = orthogonalize(tuple(p_r), basis, min_overlap=120)
        inc = incremental_ir(p_r, basis, min_overlap=120)
        gg = generalization_gap(p_r)
        rows.append({"name": fac["name"], "thesis": fac["thesis"], "leakage_ok": leak,
                     "ic": _rank_ic(sig, px), "price_sharpe": round(sharpe(p_r), 3),
                     "explained_r2_by_basis": None if orth.explained_r2 is None else round(orth.explained_r2, 3),
                     "incremental_ir_vs_basis": None if inc is None else round(inc, 3),
                     "net_sharpe_by_fee": _net_by_fee(p_r, t_r),
                     "gen_gap": None if gg is None else round(gg, 3)})
        streams[fac["name"]] = tuple(p_r)

    rows.sort(key=lambda r: (r["incremental_ir_vs_basis"] is None, -(r["incremental_ir_vs_basis"] or -9)))
    print(f"\n{'factor':16s} {'leak':5s} {'IC':>8s} {'pxSR':>6s} {'R2|base':>8s} {'incIR':>6s} {'net@10':>7s} {'genGap':>6s}")
    for r in rows:
        ic = "" if r["ic"] is None else f"{r['ic']:+.4f}"
        r2 = "" if r["explained_r2_by_basis"] is None else f"{r['explained_r2_by_basis']:.3f}"
        inc = "" if r["incremental_ir_vs_basis"] is None else f"{r['incremental_ir_vs_basis']:+.2f}"
        gg = "" if r["gen_gap"] is None else f"{r['gen_gap']:.2f}"
        print(f"{r['name']:16s} {str(r['leakage_ok']):5s} {ic:>8s} {r['price_sharpe']:>6.2f} "
              f"{r2:>8s} {inc:>6s} {r['net_sharpe_by_fee']['10bps']:>7.2f} {gg:>6s}")

    T = len(c_price)
    oos = walk_forward_selection_oos(streams, train_size=max(252, T // 4),
                                     test_size=max(120, T // 8), embargo=1, top_k=1)
    print(f"\nno-peek selection OOS (carry + crowd_contra + {len(rows)} interactions): "
          f"folds={oos['n_folds']} mean_IS={oos.get('mean_is_sharpe')} "
          f"mean_OOS={oos.get('mean_oos_sharpe')} haircut={oos.get('oos_minus_is')}")
    for fold in oos.get("folds", []):
        print(f"  fold {fold['window']}: picked {fold['selected']} IS {fold['is_sharpe']:+.2f} OOS {fold['oos_sharpe']:+.2f}")

    picked = {f["selected"][0] for f in oos.get("folds", []) if f.get("selected") and f["oos_sharpe"] > 0}
    survivors = [r["name"] for r in rows if (r["incremental_ir_vs_basis"] or -9) >= 0.3
                 and r["leakage_ok"] and r["net_sharpe_by_fee"]["10bps"] > 0.3 and r["name"] in picked]
    if survivors:
        verdict = f"CANDIDATE new edge(s): {survivors} — incremental IR>=0.3 over carry+crowd_contra, " \
                  f"survive 10bps, +OOS. Needs adversarial verification."
    else:
        verdict = "NO new edge from funding/basis interactions: all are redundant with carry+crowd_contra " \
                  "or fail cost/OOS. The existing-data interaction space is closed — confirms mined-out."
    print(f"\nVERDICT: {verdict}")

    json.dump({"universe": instruments, "n_days": T, "has_basis": has_basis,
               "baselines": {"carry_price_sharpe": round(sharpe(c_price), 3),
                             "crowd_contra_price_sharpe": round(sharpe(cc_price), 3)},
               "factors": rows, "oos": oos, "survivors": survivors, "verdict": verdict},
              open(_OUT, "w", encoding="utf-8"), indent=2)
    print(f"\n-> wrote {_OUT}")


if __name__ == "__main__":
    main()
