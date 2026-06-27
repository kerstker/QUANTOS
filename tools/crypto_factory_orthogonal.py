"""Out-of-gate: AUTONOMOUS multi-dimensional orthogonal-edge DISCOVERY.

The vision: let the MACHINE find edges across many data dimensions — including ones we did not hand-write —
and reward it for NOVELTY, not for rediscovering what we already know. The deterministic cs_genome generator
(seed / mutate / crossover over the cross-sectional DSL — no RNG) breeds expressions over the FULL field set
(price, volume, funding, AND positioning: open interest + long/short + taker), so it can compose cross-
DIMENSION interactions a human did not specify (e.g. cs_rank(ts_cov(funding, global_ls, 7))).

The key change vs run_alpha_factory: the search reward is the INCREMENTAL IR ORTHOGONAL to the known edges
(carry + crowd_contra). A candidate that merely re-derives carry scores ~0 (fully explained by the basis);
the genome is pushed toward genuinely NEW orthogonal return sources. Survivors then pass the honesty
gauntlet: machine-proven PIT-safety + a no-peek walk-forward selection OOS against the basis.

Deterministic, pure-stdlib via services. Containment unchanged: research output, drives no lifecycle, no
APPROVED surface, auto_approved stays 0. Observed numbers only — whatever the machine finds, reported as-is.
"""
from __future__ import annotations

import json
import os
import statistics
import time

from services.research import alpha_expression as ax
from services.research import cs_genome
from services.research.backtest_engine.metrics import sharpe
from services.research.expression_leakage import compute_leakage_ok
from services.research.factor_set import incremental_ir
from services.research.oos_validation import walk_forward_selection_oos
from services.alpha.orthogonalize import orthogonalize

from tools.crypto_oi_research import build_panel, CARRY
from tools.crypto_oi_verify import _streams

CROWD_CONTRA = ("cs_rank", ("neg", ("field", "global_ls")))
FIELDS = ("close", "volume", "funding", "oi", "oi_value", "top_ls", "global_ls", "taker_ls")
WINDOWS = (3, 7, 14, 30)
_OUT = os.path.join("data", "cache", "crypto_factory_orthogonal.json")

GENERATIONS = 6
POP = 48
ELITE = 14


def main() -> None:
    panel, instruments = build_panel()
    px = panel.matrices["close"]; fm = panel.matrices["funding"]
    # KNOWN-edge basis (full-length aligned economic streams)
    basis = {"carry": tuple(_streams(ax.evaluate(CARRY, panel), px, fm, q=0.3)[1]),
             "crowd_contra": tuple(_streams(ax.evaluate(CROWD_CONTRA, panel), px, fm, q=0.3)[1])}
    print(f"=== autonomous ORTHOGONAL discovery ({len(instruments)} coins, {len(FIELDS)} dims) ===")
    print(f"basis (known edges): carry SR {sharpe(list(basis['carry'])):.2f}, "
          f"crowd_contra SR {sharpe(list(basis['crowd_contra'])):.2f}")

    scored: dict = {}  # alpha_id -> (incIR, expr, econ_stream)

    def score(expr):
        aid = ax.alpha_id(expr)
        if aid in scored:
            return scored[aid][0]
        try:
            r = _streams(ax.evaluate(expr, panel), px, fm, q=0.3)[1]
        except Exception:
            scored[aid] = (float("-inf"), expr, ()); return float("-inf")
        inc = incremental_ir(r, basis, min_overlap=200)  # reward = novelty-weighted orthogonal IR
        s = inc if (inc is not None and inc == inc) else float("-inf")
        scored[aid] = (s, expr, tuple(r))
        return s

    # seed over ALL dimensions, then evolve
    seeds = cs_genome.seed_alphas(list(FIELDS), list(WINDOWS))
    n_seeds = len(seeds)
    pop = ([seeds[(i * n_seeds) // POP] for i in range(POP)] if n_seeds > POP else list(seeds))
    for gen in range(GENERATIONS):
        for e in pop:
            score(e)
        elite = [e for _s, e, _r in sorted(scored.values(), key=lambda sv: (-sv[0], ax.serialize(sv[1])))[:ELITE]]
        nxt = list(elite)
        for i, e in enumerate(elite):
            nxt.append(cs_genome.mutate(e, list(FIELDS), list(WINDOWS), gen * 1000 + i))
            nxt.append(cs_genome.crossover(e, elite[(i + 1) % len(elite)], list(FIELDS), list(WINDOWS), gen * 1000 + i))
        seen, pop = set(), []
        for e in nxt:
            a = ax.alpha_id(e)
            if a not in seen:
                seen.add(a); pop.append(e)
            if len(pop) >= POP:
                break
    print(f"evaluated {len(scored)} unique cross-dimensional alphas over {GENERATIONS} generations")

    # rank discoveries by orthogonal incremental IR; report the top novel ones with the honesty gauntlet
    ranked = sorted(scored.values(), key=lambda sv: (-sv[0], ax.serialize(sv[1])))
    top = [(s, e, r) for s, e, r in ranked if s > float("-inf")][:12]
    rows = []
    print(f"\n{'incIR':>6} {'rawSR':>6} {'R2|known':>8} {'leak':5} expr")
    for s, e, r in top:
        orth = orthogonalize(tuple(r), basis, min_overlap=200)
        leak = compute_leakage_ok(e, panel, rows=list(range(100, len(r), max(1, len(r)//30))))
        r2 = None if orth.explained_r2 is None else round(orth.explained_r2, 3)
        rows.append({"expr": ax.serialize(e), "incremental_ir": round(s, 3), "raw_econ_sharpe": round(sharpe(list(r)), 3),
                     "r2_explained_by_known": r2, "leakage_ok": leak,
                     "fields_used": sorted({n[1] for _p, n in cs_genome._nodes(e) if isinstance(n, tuple) and n and n[0] == "field"})})
        print(f"{s:6.2f} {sharpe(list(r)):6.2f} {str(r2):>8} {str(leak):5} {ax.serialize(e)[:80]}")

    # honesty gauntlet: no-peek selection OOS of the top discoveries AGAINST the known basis
    streams = dict(basis)
    for s, e, r in top[:8]:
        streams[ax.alpha_id(e)[:10]] = tuple(r)
    T = len(basis["carry"])
    oos = walk_forward_selection_oos(streams, train_size=max(252, T // 4), test_size=max(120, T // 8),
                                     embargo=2, top_k=1)
    picks = [(f["window"], f["selected"], round(f["oos_sharpe"], 2)) for f in oos.get("folds", [])]
    print(f"\nno-peek OOS (known basis + top discoveries): folds={oos['n_folds']} "
          f"mean_OOS={oos.get('mean_oos_sharpe')} picks={[p[1] for p in picks]}")

    # verdict: did the machine find a NOVEL orthogonal edge (high incIR, low R2|known, PIT-safe, picked OOS)?
    novel = [r for r in rows if (r["incremental_ir"] or 0) > 0.5 and (r["r2_explained_by_known"] or 1) < 0.2
             and r["leakage_ok"] is True and abs(r["raw_econ_sharpe"]) > 0.5]
    picked_novel = any(sel and sel[0] not in basis for _w, sel, _o in picks)
    if novel and picked_novel:
        verdict = f"MACHINE FOUND {len(novel)} novel orthogonal candidate(s) that ALSO survive no-peek OOS — investigate"
    elif novel:
        verdict = f"{len(novel)} novel orthogonal in-sample candidate(s) but NONE survived no-peek OOS selection (likely overfit)"
    else:
        verdict = "no novel orthogonal edge beyond carry+crowd_contra — the known edges span the discoverable space here"
    print(f"\nVERDICT: {verdict}")

    json.dump({"universe": instruments, "fields": list(FIELDS), "n_evaluated": len(scored),
               "basis": {k: round(sharpe(list(v)), 3) for k, v in basis.items()},
               "discoveries": rows, "oos_picks": picks, "oos_mean": oos.get("mean_oos_sharpe"),
               "verdict": verdict, "containment": "research; auto_approved=0; drives no lifecycle; no live/flags",
               "generated_at": int(time.time())}, open(_OUT, "w", encoding="utf-8"), indent=2)
    print(f"-> wrote {_OUT}")


if __name__ == "__main__":
    main()
