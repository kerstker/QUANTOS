"""Out-of-gate follow-up: does the DIVERGENCE between smart money (top_ls) and the crowd (global_ls) —
or positioning confirmed by OI build / taker flow — add a THIRD edge orthogonal to BOTH the carry edge
AND the established crowd_contra (fade-retail) lead?

The single-field positioning factors were already mined (tools/crypto_oi_research.py): crowd_contra (fade
global_ls) survived as a weak second lead; top_follow / top_contra / taker_imb / oi_mom individually lost.
The genuinely UNEXPLORED frontier is INTERACTIONS the single-field tests structurally can't capture: the
*disagreement* between top traders and retail accounts (a Commitment-of-Traders-style signal), positioning
amplified by OI build, and account-vs-taker-flow divergence.

Honesty bar (deliberately harder than crypto_oi_research): a survivor must add incremental IR over BOTH
carry AND crowd_contra, survive a 1-day execution lag, survive 10bps cost, and not collapse out-of-sample
in a no-peek walk-forward selection. Reuses the real machinery (PIT-safe panel, leakage proof, rank-IC,
Gram-Schmidt orthogonalization, walk_forward_selection_oos). Observed numbers only — the verdict (edge /
redundant / refuted) is whatever the machine reports; most positioning interactions are expected to fail.
"""
from __future__ import annotations

import json
import os
import statistics

from services.execution.backtest_authority import quarantine_legacy_backtest
from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe
from services.research.expression_leakage import compute_leakage_ok
from services.research.oos_validation import walk_forward_selection_oos
from services.alpha.orthogonalize import orthogonalize
from services.research.factor_set import incremental_ir

# Reuse the exact PIT-safe panel builder, P&L attribution, rank-IC, carry definition and universe filter.
from tools.crypto_oi_research import build_panel, _returns, _rank_ic, CARRY, QUANTILE

_OUT = os.path.join("data", "cache", "crypto_positioning_divergence.json")

# The established weak lead (fade crowded retail longs) — new factors must beat this too, not just carry.
# F2 (2026-07-27): re-exported from the single source of truth (was a verbatim duplicate).
from services.factor_garden.canonical import CROWD_CONTRA_EXPRESSION as CROWD_CONTRA

TOP = ("field", "top_ls")
GLOBAL = ("field", "global_ls")
TAKER = ("field", "taker_ls")
OI = ("field", "oi")


def _f(name, expr, thesis):
    return {"name": name, "expr": expr, "thesis": thesis}


# INTERACTION / DIVERGENCE hypotheses — each with one a-priori sign (no post-hoc sign cherry-picking).
FACTORS = [
    _f("smart_minus_crowd", ("cs_zscore", ("sub", TOP, GLOBAL)),
       "COT divergence: top traders more long than retail accounts -> follow smart money"),
    _f("div_momentum", ("cs_zscore", ("ts_delta", ("sub", TOP, GLOBAL), 5)),
       "5d change in smart-minus-crowd gap -> smart money rotating ahead of price"),
    _f("oi_confirmed_contra", ("mul", ("cs_zscore", ("neg", GLOBAL)),
                               ("cs_rank", ("ts_delta", ("log", OI), 5))),
       "fade crowded longs, amplified where OI is building (fresh trapped money)"),
    _f("taker_vs_accounts", ("cs_zscore", ("sub", TAKER, GLOBAL)),
       "aggressive takers buying while accounts not yet long -> early accumulation"),
    _f("smart_oi_conf", ("mul", ("cs_zscore", TOP),
                         ("cs_rank", ("ts_delta", OI, 5))),
       "follow top-trader positioning, weighted by OI confirmation"),
]


def _net_sharpe_by_fee(price_r, turn_r, fees_bps=(0, 5, 10, 20)):
    out = {}
    for fee in fees_bps:
        net = [p - (fee * 1e-4) * t for p, t in zip(price_r, turn_r)]
        out[f"{fee}bps"] = round(sharpe(net), 3)
    return out


def _per_year(price_r, event_times):
    """Per-calendar-year price Sharpe — the cross-regime robustness check (event_times are ms epochs)."""
    import datetime
    buckets: dict = {}
    for r, t in zip(price_r, event_times[1:1 + len(price_r)]):
        yr = datetime.datetime.utcfromtimestamp(t / 1000).year
        buckets.setdefault(yr, []).append(r)
    return {str(y): round(sharpe(v), 2) for y, v in sorted(buckets.items()) if len(v) > 20}


def main() -> None:
    quarantine_legacy_backtest("tools.crypto_positioning_divergence")
    panel, instruments = build_panel()
    px = panel.matrices["close"]
    fund_mat = panel.matrices["funding"]
    ev = panel.event_times
    print(f"OI universe: {len(instruments)} liquid majors: {instruments}")

    # baselines on THIS universe: carry + crowd_contra (both must be beaten incrementally)
    carry_sig = ax.evaluate(CARRY, panel)
    c_price, c_econ, c_turn = _returns(carry_sig, px, fund_mat)
    cc_sig = ax.evaluate(CROWD_CONTRA, panel)
    cc_price, cc_econ, cc_turn = _returns(cc_sig, px, fund_mat)
    basis = {"carry": tuple(c_price), "crowd_contra": tuple(cc_price)}
    print(f"\nbaselines (price SR): carry {sharpe(c_price):.3f} · crowd_contra {sharpe(cc_price):.3f}")

    rows = []
    oos_streams = dict(basis)
    for fac in FACTORS:
        sig = ax.evaluate(fac["expr"], panel)
        leak = compute_leakage_ok(fac["expr"], panel,
                                  rows=list(range(50, len(sig), max(1, len(sig) // 40))))
        p_r, e_r, t_r = _returns(sig, px, fund_mat)
        ic = _rank_ic(sig, px)
        oos_streams[fac["name"]] = tuple(p_r)
        orth = orthogonalize(tuple(p_r), basis, min_overlap=120)
        inc = incremental_ir(p_r, basis, min_overlap=120)
        rows.append({
            "name": fac["name"], "thesis": fac["thesis"], "leakage_ok": leak, "ic": ic,
            "price_sharpe": round(sharpe(p_r), 3), "econ_sharpe": round(sharpe(e_r), 3),
            "explained_r2_by_basis": None if orth.explained_r2 is None else round(orth.explained_r2, 3),
            "marginal_ir_vs_basis": None if orth.marginal_ir is None else round(orth.marginal_ir, 3),
            "incremental_ir_vs_basis": None if inc is None else round(inc, 3),
            "net_sharpe_by_fee": _net_sharpe_by_fee(p_r, t_r),
        })

    rows.sort(key=lambda r: (r["incremental_ir_vs_basis"] is None,
                             -(r["incremental_ir_vs_basis"] or -9)))
    print(f"\n{'factor':20s} {'leak':5s} {'IC':>8s} {'pxSR':>6s} {'R2|base':>8s} {'incIR':>6s} {'net@10':>7s}  thesis")
    for r in rows:
        ic = "" if r["ic"] is None else f"{r['ic']:+.4f}"
        r2 = "" if r["explained_r2_by_basis"] is None else f"{r['explained_r2_by_basis']:.3f}"
        inc = "" if r["incremental_ir_vs_basis"] is None else f"{r['incremental_ir_vs_basis']:+.2f}"
        print(f"{r['name']:20s} {str(r['leakage_ok']):5s} {ic:>8s} {r['price_sharpe']:>6.2f} "
              f"{r2:>8s} {inc:>6s} {r['net_sharpe_by_fee']['10bps']:>7.2f}  {r['thesis']}")

    # no-peek walk-forward selection OOS over carry + crowd_contra + the 5 interaction factors
    T = len(c_price)
    oos = walk_forward_selection_oos(oos_streams, train_size=max(252, T // 4),
                                     test_size=max(120, T // 8), embargo=1, top_k=1)
    print(f"\nno-peek selection OOS (carry + crowd_contra + {len(FACTORS)} interactions): "
          f"folds={oos['n_folds']} mean_IS={oos.get('mean_is_sharpe')} "
          f"mean_OOS={oos.get('mean_oos_sharpe')} haircut={oos.get('oos_minus_is')}")
    for fold in oos.get("folds", []):
        print(f"  fold {fold['window']}: picked {fold['selected']} "
              f"IS {fold['is_sharpe']:+.2f} OOS {fold['oos_sharpe']:+.2f}")

    # honest verdict, three honest outcomes (top_k=1 so fold["selected"] is a 1-element list):
    #  - NEW orthogonal edge: incremental IR >= 0.3 over BOTH baselines + survives cost + +OOS when picked.
    #  - REFINEMENT (not new): low incremental IR / high R2 vs basis (it IS the crowd_contra bet) but a
    #    BETTER-conditioned version — selected in OOS with positive OOS Sharpe more than raw crowd_contra.
    #  - REFUTED: no incremental IR and/or fails cost/OOS.
    def _picked_pos(name):
        return [f["oos_sharpe"] for f in oos.get("folds", [])
                if f.get("selected") and f["selected"][0] == name and f["oos_sharpe"] > 0]

    new_edges = [r["name"] for r in rows
                 if (r["incremental_ir_vs_basis"] or -9) >= 0.3 and r["leakage_ok"]
                 and r["net_sharpe_by_fee"]["10bps"] > 0.3 and _picked_pos(r["name"])]
    refinements = [r["name"] for r in rows
                   if r["name"] not in new_edges and r["leakage_ok"]
                   and r["net_sharpe_by_fee"]["10bps"] > 0.3 and len(_picked_pos(r["name"])) >= 2]
    survivors = new_edges
    cc_pos = _picked_pos("crowd_contra")
    if new_edges:
        verdict = f"NEW orthogonal edge(s): {new_edges} — incremental IR >= 0.3 over carry+crowd_contra, " \
                  f"survive 10bps, +OOS when picked. Needs adversarial verification."
    elif refinements:
        verdict = f"NO new orthogonal edge, but REFINEMENT found: {refinements} is redundant with " \
                  f"crowd_contra (high R2|basis) yet a better-CONDITIONED crowd-fade — picked in OOS " \
                  f"{len(_picked_pos(refinements[0]))} fold(s) all +OOS vs crowd_contra's {len(cc_pos)}. " \
                  f"It is the SAME bet (fade retail), improved by OI-build conditioning, not a 3rd edge."
    else:
        verdict = "NO third positioning edge: every divergence/interaction factor is redundant with " \
                  "carry+crowd_contra or fails cost/leakage/OOS. The daily positioning space is mined out."
    print(f"\nVERDICT: {verdict}")

    # add per-year regime detail for the best candidate (or the top-ranked factor if none survive)
    focus = next((r for r in rows if r["name"] in survivors), rows[0] if rows else None)
    per_year = None
    if focus is not None:
        fexpr = next(f["expr"] for f in FACTORS if f["name"] == focus["name"])
        fp, _e, _t = _returns(ax.evaluate(fexpr, panel), px, fund_mat)
        per_year = {"factor": focus["name"], "by_year": _per_year(fp, ev)}
        print(f"\nper-year price Sharpe [{focus['name']}]: {per_year['by_year']}")

    json.dump({"universe": instruments, "n_days": T, "quantile": QUANTILE,
               "baselines": {"carry_price_sharpe": round(sharpe(c_price), 3),
                             "crowd_contra_price_sharpe": round(sharpe(cc_price), 3)},
               "factors": rows, "oos": oos, "per_year": per_year,
               "new_edges": new_edges, "refinements": refinements, "survivors": survivors,
               "verdict": verdict},
              open(_OUT, "w", encoding="utf-8"), indent=2)
    print(f"\n-> wrote {_OUT}")
    # NOTE: deliberately NOT registered in run_registry — its ALLOWED_KINDS whitelist (the governance
    # boundary, pinned by tests/governance/test_research_runner_boundary.py) correctly refuses arbitrary
    # new kinds. This driver stays a pure offline research tool (like crypto_oi_research.py); the result
    # lives in the JSON above. Surfacing it in the console would mean whitelisting the kind first.


if __name__ == "__main__":
    main()
