"""Out-of-gate: combine the carry edge + the crowd_contra second-edge CANDIDATE into a de-redundant
two-factor book via the REAL population optimizer, and measure the combined economic P&L.

carry (funding yield) and crowd_contra (fade crowded retail positioning) are ~orthogonal (R^2 ~0.5%,
incremental IR +1.13). This allocates capital ACROSS the two (+ top_contra) return streams using
services.alpha.population_optimizer (incremental-IR selection + inverse-vol weights + correlation de-dup),
then reports combined vs each-alone: Sharpe, per-year, max drawdown, turnover, and a cost sweep. Each
component stream is its OWN dollar-neutral book's economic return (price L/S + funding collected), so the
combination is a book-of-books (portfolio-level), not a muddled signal blend.

Observed numbers only. This is research output; containment unchanged (auto_approved=0, no live/flags).
"""
from __future__ import annotations

import json
import os
import statistics
import time

from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe
from services.alpha.population_optimizer import (
    optimize_population, combine_returns, weights_for, max_drawdown)

from tools.crypto_oi_research import build_panel, CARRY
from tools.crypto_oi_verify import _streams, _per_year

CROWD_CONTRA = ("cs_rank", ("neg", ("field", "global_ls")))
TOP_CONTRA = ("cs_zscore", ("neg", ("field", "top_ls")))
_OUT = os.path.join("data", "cache", "crypto_combo_book.json")


def main() -> None:
    panel, instruments = build_panel()
    px = panel.matrices["close"]; fm = panel.matrices["funding"]
    times = panel.event_times

    legs = {
        "carry": _streams(ax.evaluate(CARRY, panel), px, fm, q=0.3),
        "crowd_contra": _streams(ax.evaluate(CROWD_CONTRA, panel), px, fm, q=0.3),
        "top_contra": _streams(ax.evaluate(TOP_CONTRA, panel), px, fm, q=0.3),
    }
    econ = {k: tuple(v[1]) for k, v in legs.items()}      # economic (price + funding) stream per book
    turn = {k: v[2] for k, v in legs.items()}             # per-day turnover per book

    print(f"=== two-factor combo book ({len(instruments)} coins, {len(econ['carry'])} days) ===")
    for k in ("carry", "crowd_contra", "top_contra"):
        print(f"  {k:13s} econ SR {sharpe(econ[k]):.3f}  maxDD {max_drawdown(econ[k]):.3f}  "
              f"turnover {statistics.fmean(turn[k]):.3f}/day")

    # REAL optimizer: incremental-IR selection + inverse-vol weights + corr de-dup
    port = optimize_population(econ, max_k=3, scheme="inverse_vol", cost_bps=5.0,
                              rho_cut=0.7, min_overlap=120, min_incremental_ir=0.0)
    weights = port["weights"]
    print(f"\noptimizer: selected {port['selected_ids']}  weights "
          f"{ {k: round(w, 3) for k, w in weights.items()} }")
    print(f"  post_cost_ir {port.get('post_cost_ir')}  diversity {port.get('diversity')}")
    for m in port.get("selected", []):
        print(f"    {m['id']}: weight {m.get('weight')}  marginal_ir {m.get('marginal_ir')}")

    # combined book = weighted blend of the selected econ streams
    combo = combine_returns({k: econ[k] for k in weights}, weights)
    # combined NET stream: each book pays its own turnover cost, then blend
    def combo_net(fee_bps):
        per = {k: [e - t * fee_bps / 1e4 for e, t in zip(econ[k], turn[k])] for k in weights}
        return combine_returns(per, weights)

    out = {"universe": instruments, "n_days": len(combo),
           "alone": {k: {"econ_sharpe": round(sharpe(econ[k]), 3),
                         "max_drawdown": round(max_drawdown(econ[k]), 3)} for k in econ},
           "optimizer_selected": port["selected_ids"], "weights": {k: round(w, 4) for k, w in weights.items()},
           "combo_econ_sharpe": round(sharpe(combo), 3), "combo_max_drawdown": round(max_drawdown(combo), 3),
           "combo_per_year": _per_year(combo, times),
           "combo_net_sharpe_by_fee": {f: round(sharpe(combo_net(f)), 3) for f in (2, 5, 10, 20)}}

    print(f"\nCOMBINED book: econ SR {out['combo_econ_sharpe']}  maxDD {out['combo_max_drawdown']}")
    print(f"  per-year: {out['combo_per_year']}")
    print(f"  net SR by fee bps: {out['combo_net_sharpe_by_fee']}")
    # honest comparison
    best_alone = max(sharpe(econ[k]) for k in econ)
    lift = out["combo_econ_sharpe"] - round(best_alone, 3)
    out["lift_vs_best_alone"] = round(lift, 3)
    out["verdict"] = (f"combo SR {out['combo_econ_sharpe']} vs best-alone {round(best_alone,3)} "
                      f"(lift {out['lift_vs_best_alone']:+.3f}); "
                      + ("diversification helps" if lift > 0.05 else "no material lift — components already near-orthogonal but combo ~= best single"))
    print(f"\nVERDICT: {out['verdict']}")
    out["containment"] = "research output; auto_approved=0; REQUIRES_HUMAN_REVIEW; no live/flags"
    out["generated_at"] = int(time.time())
    json.dump(out, open(_OUT, "w", encoding="utf-8"), indent=2)
    print(f"-> wrote {_OUT}")


if __name__ == "__main__":
    main()
