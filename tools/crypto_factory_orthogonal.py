"""Out-of-gate: AUTONOMOUS multi-dimensional orthogonal-edge DISCOVERY (drives the HARDENED engine).

The vision: let the MACHINE find edges across many data dimensions — including ones we did not hand-write —
and reward NOVELTY, not rediscovery. This is now a first-class engine capability: `run_alpha_factory(...,
basis_returns=KNOWN_EDGES)` switches the cs_genome search reward to the INCREMENTAL IR ORTHOGONAL to the
known edges, so the machine is pushed toward genuinely NEW orthogonal return sources. This driver is a thin
orchestrator: build the 8-dimension panel, hand the engine the known-edge basis (carry + crowd_contra), let
it breed across price/volume/funding/OI/long-short/taker, then run the honesty gauntlet (PIT-leakage proof +
no-peek selection OOS + lag/cost reality-check) over what it discovers.

Deterministic, pure-stdlib via services. Containment unchanged: research output, drives no lifecycle, no
APPROVED surface, auto_approved stays 0. Observed numbers only — whatever the machine finds, reported as-is.
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
from services.research.oos_validation import walk_forward_selection_oos
from services.alpha.orthogonalize import orthogonalize

from tools.crypto_oi_research import build_panel, CARRY
from tools.crypto_oi_verify import _streams, _per_year
from tools.search_trial_registry import (
    field_panel_dataset_version,
    make_search_id,
    production_experiment_registry,
)

# F2 (2026-07-27): re-exported from the single source of truth (was a verbatim duplicate).
from services.factor_garden.canonical import CROWD_CONTRA_EXPRESSION as CROWD_CONTRA
FIELDS = ("close", "volume", "funding", "oi", "oi_value", "top_ls", "global_ls", "taker_ls")
WINDOWS = (3, 7, 14, 30)
QUANTILE = 0.3
_OUT = os.path.join("data", "cache", "crypto_factory_orthogonal.json")

# scaled search
GENERATIONS = 8
POP = 64
ELITE = 16


def main(generations: int = GENERATIONS, population: int = POP) -> None:
    recorded_at = time.time_ns() // 1_000_000
    elite = max(4, min(ELITE, population // 4))     # keep elite sane relative to a tuned population
    panel, instruments = build_panel()
    dataset_version = field_panel_dataset_version(panel)
    px = panel.matrices["close"]
    fm = panel.matrices["funding"]
    # KNOWN-edge basis as aligned ECONOMIC streams (same construction the engine scores with)
    basis = {"carry": economic_return_series(ax.evaluate(CARRY, panel), panel, quantile=QUANTILE),
             "crowd_contra": economic_return_series(ax.evaluate(CROWD_CONTRA, panel), panel, quantile=QUANTILE)}
    print(f"=== autonomous ORTHOGONAL discovery via hardened engine ({len(instruments)} coins, {len(FIELDS)} dims) ===")
    print(f"basis: carry SR {sharpe(basis['carry']):.2f}, crowd_contra SR {sharpe(basis['crowd_contra']):.2f}")
    print(f"search: {generations} gen x pop {population}, reward = incremental IR orthogonal to basis")

    with production_experiment_registry() as registry:
        out = run_alpha_factory(
            panel,
            list(FIELDS),
            list(WINDOWS),
            generations=generations,
            population_size=population,
            elite_k=elite,
            quantile=QUANTILE,
            basis_returns=basis,
            basis_min_overlap=200,
            dataset_version=dataset_version,
            timestamp=recorded_at,
            search_registry=registry,
            search_id=make_search_id(
                source="tools.crypto_factory_orthogonal",
                search_kind="ALPHA_FACTORY_ORTHOGONAL",
                dataset_version=dataset_version,
                recorded_at=recorded_at,
                params={"generations": generations, "population": population},
            ),
            search_kind="ALPHA_FACTORY_ORTHOGONAL",
            search_source="tools.crypto_factory_orthogonal",
        )
    assert out["scored_by"] == "orthogonal_incremental_ir"
    print(f"engine evaluated {out['n_evaluated']} cross-dimensional alphas; elite {len(out['best'])}")

    # honesty gauntlet over the elite discoveries
    rows, disc_streams = [], {}
    print(f"\n{'incIR':>6} {'rawSR':>6} {'R2|known':>8} {'leak':5} fields | expr")
    for b in out["best"]:
        # recompute the economic stream for orthogonality/Sharpe reporting
        e = ax.deserialize(b["expr"])
        r = economic_return_series(ax.evaluate(e, panel), panel, quantile=QUANTILE)
        orth = orthogonalize(tuple(r), basis, min_overlap=200)
        flds = sorted({n[1] for _p, n in _nodes(e) if isinstance(n, tuple) and n and n[0] == "field"})
        r2 = None if orth.explained_r2 is None else round(orth.explained_r2, 3)
        rows.append({"expr": b["expr"], "incremental_ir": b["score"], "raw_econ_sharpe": round(sharpe(r), 3),
                     "r2_explained_by_known": r2, "leakage_ok": b["leakage_ok"], "fields_used": flds})
        disc_streams[b["alpha_id"][:10]] = tuple(r)
        print(f"{b['score']:6.2f} {sharpe(r):6.2f} {str(r2):>8} {str(b['leakage_ok']):5} {','.join(flds):<22} {b['expr'][:70]}")

    # no-peek selection OOS: basis + discoveries
    streams = dict(basis)
    streams.update(dict(list(disc_streams.items())[:8]))
    T = len(basis["carry"])
    oos = walk_forward_selection_oos(streams, train_size=max(252, T // 4), test_size=max(120, T // 8),
                                     embargo=2, top_k=1)
    picks = [f["selected"] for f in oos.get("folds", [])]
    print(f"\nno-peek OOS (basis + discoveries): folds={oos['n_folds']} mean_OOS={oos.get('mean_oos_sharpe')} "
          f"picks={picks}")

    # reality-check the single best NOVEL discovery (orthogonal, PIT-safe, real Sharpe)
    novel = [r for r in rows if (r["r2_explained_by_known"] or 1) < 0.2 and r["leakage_ok"] is True
             and abs(r["raw_econ_sharpe"]) > 0.5]
    rc = None
    if novel:
        top = novel[0]
        e = ax.deserialize(top["expr"])
        sig = ax.evaluate(e, panel)
        pr, _ec, tr = _streams(sig, px, fm, q=QUANTILE)
        lags = {L: round(sharpe(_streams(sig, px, fm, q=QUANTILE, lag=L)[0]), 3) for L in (0, 1, 2)}
        net = {f: round(sharpe([p - t * f / 1e4 for p, t in zip(pr, tr)]), 3) for f in (5, 10, 20)}
        rc = {"expr": top["expr"], "per_year": _per_year(pr, panel.event_times),
              "exec_lag_sharpe": lags, "net_sharpe_by_fee": net, "turnover": round(statistics.fmean(tr), 3)}
        survives = lags.get(1, 0) > 0.5 and net.get(10, 0) > 0.4
        print(f"\nreality-check top novel {top['expr'][:60]}")
        print(f"  per-year {rc['per_year']}")
        print(f"  exec-lag {lags}  net-by-fee {net}  turnover {rc['turnover']}  -> {'SURVIVES lag+cost' if survives else 'FAILS lag/cost'}")
    else:
        survives = False

    verdict = ("MACHINE found a NOVEL orthogonal lead surviving lag+cost (autonomous discovery works) — verify fully"
               if novel and survives else
               "novel in-sample orthogonal candidates but none survive lag+cost reality-check (honest filter caught them)"
               if novel else
               "no novel orthogonal edge beyond carry+crowd_contra in this search")
    print(f"\nVERDICT: {verdict}")
    json.dump({"universe": instruments, "fields": list(FIELDS), "engine": "run_alpha_factory(basis_returns=...)",
               "generations": GENERATIONS, "population": POP, "n_evaluated": out["n_evaluated"],
               "basis": {k: round(sharpe(v), 3) for k, v in basis.items()}, "discoveries": rows,
               "oos_picks": picks, "oos_mean": oos.get("mean_oos_sharpe"), "reality_check": rc,
               "verdict": verdict, "containment": "research; auto_approved=0; drives no lifecycle; no live/flags",
               "generated_at": int(time.time())}, open(_OUT, "w", encoding="utf-8"), indent=2)
    print(f"-> wrote {_OUT}")


def _nodes(t, path=()):
    yield path, t
    if isinstance(t, tuple):
        for i, a in enumerate(t[1:], start=1):
            if isinstance(a, tuple):
                yield from _nodes(a, path + (i,))


if __name__ == "__main__":
    main()
