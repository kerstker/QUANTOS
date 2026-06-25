"""Offline GP expression-tree discovery on the persisted crypto store.

Reads ONLY data/cache/crypto.db (no network) + the instrument set from the existing
snapshot, runs the contained GP search (run_research_session(gp=True)), and rewrites the
snapshot's `factors` with the machine-discovered TREES (each carrying its readable canonical
expression). Parsimony pressure biases the search toward smaller trees. The OOS block is
method-independent (recompute with tools/crypto_oos_validation.py after).

Containment + multiple-testing honesty are identical to every other path: every tree routes
through the unchanged ResearchAgentLoop (agent never reaches APPROVED); each distinct
canonical tree is exactly one catalog trial; nothing is auto-promoted.
"""
from __future__ import annotations

import json
import os
import time

from services.data.persistent_store import SqlitePITStore
from services.intelligence.search_space import HypothesisSpace
from services.intelligence.evolutionary_search import genome_to_definition
from services.research.session import run_research_session

OPERATORS = ("momentum", "ts_mean", "zscore", "volatility", "ts_rank",
             "ema_ratio", "dist_from_high", "accel")
WINDOWS = (3, 5, 10)
FWD_HORIZON = 1
GENERATIONS, POPULATION, ELITE = 5, 16, 6
PARSIMONY_LAMBDA, PARSIMONY_MU = 0.02, 0.01
_SNAP = os.path.join("data", "cache", "crypto_research_snapshot.json")


def _round(x, n=4):
    return round(x, n) if isinstance(x, (int, float)) else None


def main() -> None:
    if not os.path.exists(_SNAP):
        raise SystemExit(f"no snapshot at {_SNAP} — run tools/crypto_research_run.py first")
    with open(_SNAP, encoding="utf-8") as fh:
        snap = json.load(fh)
    instruments = sorted(c["instrument_id"] for c in snap.get("meta", {}).get("coins", []))
    if len(instruments) < 2:
        raise SystemExit("snapshot has < 2 instruments")
    store = SqlitePITStore(os.path.join("data", "cache", "crypto.db"), create=False)
    event_times = sorted({b.event_time for inst in instruments
                          for b in store.fetch_bars(inst, 0, 10 ** 14)})

    space = HypothesisSpace(factors=("close",), operators=OPERATORS, windows=WINDOWS)
    now_ms = int(time.time() * 1000)
    res = run_research_session(
        store, instruments, event_times=event_times, fwd_horizon=FWD_HORIZON, space=space,
        rounds=1, batch_size=1, timestamp=now_ms, gp=True,
        evo_generations=GENERATIONS, evo_population=POPULATION, evo_elite=ELITE,
        parsimony_lambda=PARSIMONY_LAMBDA, parsimony_mu=PARSIMONY_MU)

    # readable canonical expression per discovered tree (from the evaluated genomes)
    expr = {}
    for g, _r in res["best"]:
        if g and g[0] == "T":
            d = genome_to_definition(g)
            expr[d.alpha_id] = d.params["tree"]

    factors = []
    for aid, entry in sorted(res["report"].items()):
        ev = entry.get("evaluation")
        if ev is None:
            continue
        factors.append({
            "alpha_id": aid,
            "name": expr.get(aid, aid),
            "family": "gp_tree",
            "expr": expr.get(aid),
            "ic": _round(ev.ic_mean),
            "rank_ic": _round(getattr(ev, "rank_ic", None)),
            "net_sharpe": _round(ev.net_sharpe, 3),
            "capacity_adj_sharpe": _round(ev.capacity_adj_sharpe, 3),
            "deflated_sharpe": _round(ev.deflated_sharpe, 4),
            "pbo": _round(ev.pbo, 4),
            "turnover": _round(ev.turnover, 4),
            "breadth": _round(ev.mean_breadth, 2),
            "verdict": ev.verdict.value,
            "state": entry.get("state"),
            "reproducibility_hash": ev.reproducibility_hash,
        })
    factors.sort(key=lambda f: (f["net_sharpe"] if f["net_sharpe"] is not None else -1e9), reverse=True)

    auto_approved = sum(1 for f in factors if f["state"] == "APPROVED")
    net = [f["net_sharpe"] for f in factors if f["net_sharpe"] is not None]
    dsr = [f["deflated_sharpe"] for f in factors if f["deflated_sharpe"] is not None]
    sizes = [f["expr"].count("(") for f in factors if f.get("expr")]   # node-ish proxy
    snap["generated_at"] = now_ms // 1000
    snap["generated_at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ms / 1000))
    snap["dataset_version"] = res["dataset_version"]
    snap["meta"]["search_mode"] = "gp_tree"
    snap["meta"]["evo_generations"] = GENERATIONS
    snap["meta"]["evo_population"] = POPULATION
    snap["meta"]["evo_elite"] = ELITE
    snap["meta"]["parsimony_lambda"] = PARSIMONY_LAMBDA
    snap["meta"]["parsimony_mu"] = PARSIMONY_MU
    snap["summary"] = {
        "n_evaluated": res["n_evaluated"],
        "trial_count": res["n_evaluated"],
        "auto_approved": auto_approved,
        "requires_human_review": sum(1 for f in factors if f["verdict"] == "REQUIRES_HUMAN_REVIEW"),
        "rejected": sum(1 for f in factors if str(f["state"]) == "REJECTED"),
        "max_net_sharpe": max(net) if net else None,
        "max_deflated_sharpe": max(dsr) if dsr else None,
        "top_alpha_id": factors[0]["alpha_id"] if factors else None,
    }
    snap["factors"] = factors
    # de-redundant best SET (incremental-IR forward selection over ALL discovered factors):
    # the GP top trees are near-clones, so this collapses ~96 factors to a few independent ones.
    syn = dict(res.get("synergistic_set", {}))
    for s in syn.get("selected", []):
        s["expr"] = expr.get(s["id"])
    snap["synergistic_set"] = syn
    snap.pop("oos", None)        # method-independent; recompute via crypto_oos_validation.py
    with open(_SNAP, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=2)

    print(f"GP search: evaluated={res['n_evaluated']} auto_approved={auto_approved} (must be 0)")
    print(f"max net Sharpe={snap['summary']['max_net_sharpe']} max Deflated={snap['summary']['max_deflated_sharpe']}")
    print(f"largest tree ~{max(sizes) if sizes else 0} nodes; top tree expr:")
    if factors and factors[0].get("expr"):
        print("  " + factors[0]["expr"])
    print(f"wrote {_SNAP}")


if __name__ == "__main__":
    main()
