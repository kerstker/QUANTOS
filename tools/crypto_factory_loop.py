"""Out-of-gate: LOOP-UNTIL-DRY autonomous orthogonal-edge mining across 9 data dimensions.

The vision, exhaustively: keep letting the machine discover NEW orthogonal edges until it cannot find any
more. Each round runs the hardened engine (run_alpha_factory with the orthogonal-novelty reward) against
the CURRENT known-edge basis; the single best discovery that passes the full quick gauntlet (orthogonal to
the basis + PIT-safe + survives a 1-day execution lag AND realistic cost) is ACCEPTED and folded into the
basis — so the next round's reward orthogonalizes against it too, and the genome is pushed into the next
unexplored dimension. The loop stops after K consecutive rounds find nothing new (the orthogonal space is
mined out). Reports the chain of discovered edges + a final no-peek selection OOS over all of them.

9 dims: price, volume, funding, basis (perp-mark/spot premium), open interest, oi_value, top/global
long-short, taker. Deterministic, pure-stdlib via services. Containment unchanged: research output, drives
no lifecycle, auto_approved stays 0. Observed numbers only — the chain is whatever the machine finds.
"""

from __future__ import annotations

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
from tools.alpha_candidate_admission import run as run_candidate_admission
from tools.cache_io import atomic_write_json

# F2 (2026-07-27): re-exported from the single source of truth (was a verbatim duplicate).
from services.factor_garden.canonical import CROWD_CONTRA_EXPRESSION as CROWD_CONTRA

FIELDS = ("close", "volume", "funding", "basis", "oi", "oi_value", "top_ls", "global_ls", "taker_ls")
WINDOWS = (3, 7, 14, 30)
QUANTILE = 0.3
DRY_LIMIT = 2
MAX_ROUNDS = 8
# 2026-07-08 top-tier gap audit S4.3/S4.8 ("highest-value single fix"): population scoring inside
# run_alpha_factory is a pure function per candidate (no RNG/wall-clock) and was single-threaded --
# historical mining runs evaluated only a few hundred genomes total. min(4, cpu_count) -- NOT the
# full core count -- both because this is an offline research run sharing the machine, and because
# an ad hoc benchmark at this driver's actual scale (24 instruments, daily OI panel, population 48)
# found 4 workers beat 8 (~1.1-1.3x vs ~1.1x): per-candidate compute here is only moderately larger
# than one-time process/pickle overhead, so piling on more workers thins each worker's task share
# without amortizing better. See services/research/alpha_factory.py::run_alpha_factory max_workers
# and tests/research/test_alpha_factory_parallel.py for the bit-identical-results proof.
MAX_WORKERS = min(4, os.cpu_count() or 1)
_OUT = os.path.join("data", "cache", "crypto_factory_loop.json")


def next_search_recorded_at(*, previous: int | None, observed_at: int) -> int:
    """Return a non-decreasing search clock after expensive input assembly.

    The production experiment authority rejects global STARTED-clock rollback.
    A factory invocation therefore must not freeze its clock before
    ``build_panel`` (which can take minutes), and every subsequent round must
    observe wall time again. ``previous`` supplies the local one-millisecond
    floor when rounds begin inside the same clock tick.
    """
    if isinstance(observed_at, bool) or not isinstance(observed_at, int) or observed_at < 0:
        raise ValueError("observed_at must be a non-negative integer")
    if previous is None:
        return observed_at
    if isinstance(previous, bool) or not isinstance(previous, int) or previous < 0:
        raise ValueError("previous must be a non-negative integer or None")
    return max(observed_at, previous + 1)


def _gauntlet(e, panel, basis):
    """Quick reality gauntlet for a candidate expr: returns a dict if it is a REAL orthogonal edge
    (orthogonal to basis, PIT-safe, survives a 1-day lag AND 10bps cost), else None."""
    px, fm = panel.matrices["close"], panel.matrices["funding"]
    sig = ax.evaluate(e, panel)
    pr, _ec, tr = _streams(sig, px, fm, q=QUANTILE)
    if len([x for x in pr if x]) < 200:
        return None
    econ = economic_return_series(sig, panel, quantile=QUANTILE)
    orth = orthogonalize(tuple(econ), basis, min_overlap=200)
    if orth.explained_r2 is None or orth.explained_r2 >= 0.2:
        return None  # not orthogonal to what we already know
    from services.research.expression_leakage import compute_leakage_ok

    if compute_leakage_ok(e, panel, rows=list(range(150, len(pr), max(1, len(pr) // 25)))) is not True:
        return None
    lag1 = sharpe(_streams(sig, px, fm, q=QUANTILE, lag=1)[0])
    net10 = sharpe([p - t * 10 / 1e4 for p, t in zip(pr, tr)])
    if not (sharpe(pr) > 0.5 and lag1 > 0.5 and net10 > 0.4):
        return None  # fragile (timing artifact) or dies on cost
    return {
        "expr": ax.serialize(e),
        "econ_stream": tuple(econ),
        "price_sharpe": round(sharpe(pr), 3),
        "lag1_sharpe": round(lag1, 3),
        "net10_sharpe": round(net10, 3),
        "r2_vs_known": round(orth.explained_r2, 3),
        "turnover": round(statistics.fmean(tr), 3),
        "per_year": _per_year(pr, panel.event_times),
        "fields": sorted({n[1] for _p, n in _nodes(e) if isinstance(n, tuple) and n and n[0] == "field"}),
    }


def _nodes(t, path=()):
    yield path, t
    if isinstance(t, tuple):
        for i, a in enumerate(t[1:], start=1):
            if isinstance(a, tuple):
                yield from _nodes(a, path + (i,))


def main() -> None:
    panel, instruments = build_panel(add_basis=True)
    dataset_version = field_panel_dataset_version(panel)
    print(f"=== loop-until-dry orthogonal mining ({len(instruments)} coins, {len(FIELDS)} dims) ===")
    known = {
        "carry": economic_return_series(ax.evaluate(CARRY, panel), panel, quantile=QUANTILE),
        "crowd_contra": economic_return_series(ax.evaluate(CROWD_CONTRA, panel), panel, quantile=QUANTILE),
    }
    known_ids = {ax.alpha_id(CARRY), ax.alpha_id(CROWD_CONTRA)}
    print(f"seed basis: carry SR {sharpe(known['carry']):.2f}, crowd_contra SR {sharpe(known['crowd_contra']):.2f}")

    chain, eligible_discoveries, dry, rnd, n_evaluated_total = [], [], 0, 0, 0
    previous_recorded_at: int | None = None
    while dry < DRY_LIMIT and rnd < MAX_ROUNDS:
        rnd += 1
        recorded_at = next_search_recorded_at(
            previous=previous_recorded_at,
            observed_at=time.time_ns() // 1_000_000,
        )
        previous_recorded_at = recorded_at
        search_id = make_search_id(
            source="tools.crypto_factory_loop",
            search_kind="ALPHA_FACTORY_LOOP",
            dataset_version=dataset_version,
            recorded_at=recorded_at,
            params={"round": rnd, "generations": 5, "population": 48},
        )
        with production_experiment_registry() as registry:
            out = run_alpha_factory(
                panel,
                list(FIELDS),
                list(WINDOWS),
                generations=5,
                population_size=48,
                elite_k=14,
                quantile=QUANTILE,
                basis_returns=known,
                basis_min_overlap=200,
                dataset_version=dataset_version,
                timestamp=recorded_at,
                max_workers=MAX_WORKERS,
                search_registry=registry,
                search_id=search_id,
                search_kind="ALPHA_FACTORY_LOOP",
                search_source="tools.crypto_factory_loop",
            )
            evaluation_refs = {}
            for run_id in out.get("recorded_run_ids", ()):
                row = registry.get_evaluation(str(run_id))
                if not isinstance(row, dict):
                    continue
                alpha_id = str(row.get("alpha_id", ""))
                if not alpha_id:
                    continue
                evaluation_refs[alpha_id] = {
                    "evaluation_run_id": str(run_id),
                    "verified": registry.verify_evaluation(str(run_id)),
                    "verdict": row.get("verdict"),
                    "definition_hash": row.get("config_hash"),
                    "dataset_version": row.get("dataset_version"),
                    "source_search_id": row.get("search_id"),
                }
        n_evaluated_total += int(out.get("n_evaluated", 0) or 0)
        found = None
        for b in out["best"]:
            e = ax.deserialize(b["expr"])
            aid = ax.alpha_id(e)
            if aid in known_ids:
                continue
            g = _gauntlet(e, panel, known)
            if g is not None:
                found = (aid, e, g)
                break
        if found is None:
            dry += 1
            print(f"round {rnd}: evaluated {out['n_evaluated']}, NO new orthogonal survivor (dry {dry}/{DRY_LIMIT})")
            continue
        aid, e, g = found
        known[aid[:12]] = g["econ_stream"]
        known_ids.add(aid)
        dry = 0
        discovery = {
            "alpha_id": aid,
            **{k: v for k, v in g.items() if k != "econ_stream"},
            "source_search_id": search_id,
            "dataset_version": dataset_version,
        }
        chain.append(discovery)
        evaluation_ref = evaluation_refs.get(aid)
        if (
            isinstance(evaluation_ref, dict)
            and evaluation_ref.get("verified") is True
            and evaluation_ref.get("verdict") == "PASS"
            and evaluation_ref.get("source_search_id") == search_id
            and evaluation_ref.get("dataset_version") == dataset_version
            and isinstance(evaluation_ref.get("definition_hash"), str)
        ):
            eligible_discoveries.append(
                {
                    **discovery,
                    "definition_hash": evaluation_ref["definition_hash"],
                    "evaluation_run_id": evaluation_ref["evaluation_run_id"],
                    "quantile": QUANTILE,
                    "cadence_days": 7,
                }
            )
        print(
            f"round {rnd}: + NEW orthogonal edge  SR {g['price_sharpe']}  lag1 {g['lag1_sharpe']}  "
            f"net10 {g['net10_sharpe']}  R2|known {g['r2_vs_known']}  {g['fields']}"
        )
        print(f"          {g['expr']}")

    print(f"\nmined {len(chain)} orthogonal edge(s) beyond carry+crowd_contra before going dry:")
    for i, c in enumerate(chain, 1):
        print(
            f"  {i}. SR {c['price_sharpe']:>5} lag1 {c['lag1_sharpe']:>5} net10 {c['net10_sharpe']:>5} "
            f"{','.join(c['fields']):<22} {c['expr'][:64]}"
        )

    # final no-peek OOS over the whole discovered book
    T = len(known["carry"])
    oos = walk_forward_selection_oos(known, train_size=max(252, T // 4), test_size=max(120, T // 8), embargo=2, top_k=1)
    picks = [f["selected"] for f in oos.get("folds", [])]
    print(
        f"\nno-peek OOS over full discovered book ({len(known)} edges): folds={oos['n_folds']} "
        f"mean_OOS={oos.get('mean_oos_sharpe')} picks={picks}"
    )

    verdict = (
        f"machine autonomously mined {len(chain)} NEW orthogonal edge(s) (each lag+cost-robust) "
        f"before drying out — multi-dimensional discovery loop works"
        if chain
        else "no orthogonal edge beyond carry+crowd_contra survived the gauntlet"
    )
    print(f"\nVERDICT: {verdict}")
    document = {
        "schema_version": 2,
        "universe": instruments,
        "fields": list(FIELDS),
        "rounds": rnd,
        "dry_limit": DRY_LIMIT,
        "n_evaluated_total": n_evaluated_total,
        "seed_basis": ["carry", "crowd_contra"],
        "discovered_chain": chain,
        "eligible_discoveries": eligible_discoveries,
        "oos_mean": oos.get("mean_oos_sharpe"),
        "oos_picks": picks,
        "verdict": verdict,
        "containment": (
            "research; verified PASS survivors auto-register to L2 forward paper only; "
            "no lifecycle promotion; no live/flags"
        ),
        "handoff_contract": "VERIFIED_FACTORY_PASS_TO_FORWARD_PAPER",
        "generated_at": int(time.time()),
    }
    atomic_write_json(_OUT, document, indent=2)
    admission = run_candidate_admission(factory_path=_OUT)
    print(
        "candidate handoff: "
        f"status={admission['status']} admitted={len(admission['admitted'])} "
        f"replayed={len(admission['replayed'])}"
    )
    print(f"-> wrote {_OUT}")


if __name__ == "__main__":
    main()
