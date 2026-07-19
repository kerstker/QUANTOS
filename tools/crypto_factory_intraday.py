"""Out-of-gate: does finer-resolution INTRADAY positioning data unlock a NEW orthogonal edge?

The "thin space" conclusion was drawn on DAILY (eod) features. This re-runs the autonomous orthogonal
search over a 15-dimension panel that ADDS 6 intraday-derived daily features (oi_range/oi_trend/gls_trend/
gls_range/taker_mean/oi_surge — positioning DYNAMICS the eod snapshot discards), with the search reward =
incremental IR orthogonal to the KNOWN edges (carry + crowd_contra + the loop's top_ls edge). It reports
whether any INTRADAY-using discovery survives the full honesty gauntlet (orthogonal + PIT-safe + 1-day lag +
10bps cost) — i.e. whether richer (finer) positioning data is a real new lever or is also mined out.

Deterministic, pure-stdlib via services. Containment unchanged: research output, auto_approved=0, no live/flags.
Observed numbers only.
"""
from __future__ import annotations

import json
import os
import statistics
import time

from services.research import alpha_expression as ax
from services.research.alpha_factory import run_alpha_factory
from services.research.carry_pnl import economic_return_series
from services.research.backtest_engine.metrics import sharpe
from services.research.expression_leakage import compute_leakage_ok
from services.alpha.orthogonalize import orthogonalize

from tools.crypto_oi_research import build_panel, CARRY, INTRADAY_FIELDS
from tools.crypto_oi_verify import _streams, _per_year
from tools.search_trial_registry import (
    field_panel_dataset_version,
    make_search_id,
    production_experiment_registry,
)

CROWD_CONTRA = ("cs_rank", ("neg", ("field", "global_ls")))
TOP_LS_EDGE = ("cs_rank", ("sign", ("cs_rank", ("field", "top_ls"))))   # the loop's only survivor
BASE_FIELDS = ("close", "volume", "funding", "basis", "oi", "oi_value", "top_ls", "global_ls", "taker_ls")
FIELDS = BASE_FIELDS + INTRADAY_FIELDS
WINDOWS = (3, 7, 14, 30)
QUANTILE = 0.3
_OUT = os.path.join("data", "cache", "crypto_factory_intraday.json")


def _gauntlet(e, panel, basis):
    px, fm = panel.matrices["close"], panel.matrices["funding"]
    sig = ax.evaluate(e, panel)
    pr, _ec, tr = _streams(sig, px, fm, q=QUANTILE)
    if len([x for x in pr if x]) < 200:
        return None
    econ = economic_return_series(sig, panel, quantile=QUANTILE)
    orth = orthogonalize(tuple(econ), basis, min_overlap=200)
    if orth.explained_r2 is None or orth.explained_r2 >= 0.2:
        return None
    if compute_leakage_ok(e, panel, rows=list(range(150, len(pr), max(1, len(pr)//25)))) is not True:
        return None
    lag1 = sharpe(_streams(sig, px, fm, q=QUANTILE, lag=1)[0])
    net10 = sharpe([p - t * 10 / 1e4 for p, t in zip(pr, tr)])
    fields = sorted({n[1] for _p, n in _nodes(e) if isinstance(n, tuple) and n and n[0] == "field"})
    return {"expr": ax.serialize(e), "price_sharpe": round(sharpe(pr), 3), "lag1_sharpe": round(lag1, 3),
            "net10_sharpe": round(net10, 3), "r2_vs_known": round(orth.explained_r2, 3),
            "turnover": round(statistics.fmean(tr), 3), "fields": fields,
            "uses_intraday": any(f in INTRADAY_FIELDS for f in fields),
            "survives": (sharpe(pr) > 0.5 and lag1 > 0.5 and net10 > 0.4),
            "per_year": _per_year(pr, panel.event_times)}


def _nodes(t, path=()):
    yield path, t
    if isinstance(t, tuple):
        for i, a in enumerate(t[1:], start=1):
            if isinstance(a, tuple):
                yield from _nodes(a, path + (i,))


def main() -> None:
    recorded_at = time.time_ns() // 1_000_000
    panel, instruments = build_panel(add_basis=True, add_intraday=True)
    dataset_version = field_panel_dataset_version(panel)
    have_intraday = [f for f in INTRADAY_FIELDS if f in panel.matrices]
    print(f"=== intraday-dimension orthogonal discovery ({len(instruments)} coins, {len(FIELDS)} dims) ===")
    print(f"intraday fields present: {have_intraday}")
    known = {"carry": economic_return_series(ax.evaluate(CARRY, panel), panel, quantile=QUANTILE),
             "crowd_contra": economic_return_series(ax.evaluate(CROWD_CONTRA, panel), panel, quantile=QUANTILE),
             "top_ls_edge": economic_return_series(ax.evaluate(TOP_LS_EDGE, panel), panel, quantile=QUANTILE)}

    with production_experiment_registry() as registry:
        out = run_alpha_factory(
            panel,
            list(FIELDS),
            list(WINDOWS),
            generations=8,
            population_size=64,
            elite_k=18,
            quantile=QUANTILE,
            basis_returns=known,
            basis_min_overlap=200,
            dataset_version=dataset_version,
            timestamp=recorded_at,
            search_registry=registry,
            search_id=make_search_id(
                source="tools.crypto_factory_intraday",
                search_kind="ALPHA_FACTORY_INTRADAY",
                dataset_version=dataset_version,
                recorded_at=recorded_at,
                params={"generations": 8, "population": 64},
            ),
            search_kind="ALPHA_FACTORY_INTRADAY",
            search_source="tools.crypto_factory_intraday",
        )
    print(f"engine evaluated {out['n_evaluated']} alphas over 15 dims")

    rows, intraday_survivors = [], []
    for b in out["best"]:
        g = _gauntlet(ax.deserialize(b["expr"]), panel, known)
        if g is None:
            continue
        rows.append(g)
        if g["uses_intraday"] and g["survives"]:
            intraday_survivors.append(g)

    print(f"\n{'SR':>5} {'lag1':>5} {'net10':>6} {'R2':>5} {'intra':5} {'surv':4} fields | expr")
    for g in rows[:14]:
        print(f"{g['price_sharpe']:5} {g['lag1_sharpe']:5} {g['net10_sharpe']:6} {g['r2_vs_known']:5} "
              f"{str(g['uses_intraday']):5} {str(g['survives']):4} {','.join(g['fields']):<26} {g['expr'][:54]}")

    survivors = [g for g in rows if g["survives"]]
    if intraday_survivors:
        verdict = (f"INTRADAY data unlocked {len(intraday_survivors)} orthogonal edge(s) surviving lag+cost — "
                   f"finer positioning data IS a new lever; verify fully")
    elif survivors:
        verdict = (f"{len(survivors)} orthogonal survivor(s) but NONE use intraday fields — intraday dynamics "
                   f"add no new robust edge beyond the eod signal (eod positioning already captures it)")
    else:
        verdict = "no orthogonal edge survived the gauntlet — intraday positioning data is also mined out"
    print(f"\nVERDICT: {verdict}")
    json.dump({"universe": instruments, "n_dims": len(FIELDS), "intraday_fields": have_intraday,
               "n_evaluated": out["n_evaluated"], "discoveries": rows,
               "intraday_survivors": intraday_survivors, "verdict": verdict,
               "containment": "research; auto_approved=0; no live/flags", "generated_at": int(time.time())},
              open(_OUT, "w", encoding="utf-8"), indent=2)
    print(f"-> wrote {_OUT}")


if __name__ == "__main__":
    main()
