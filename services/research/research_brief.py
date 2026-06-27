"""Auto Research Brief (Alpha Factory upgrade Phase 7).

The audit found ResearchReport was an empty shell — any "brief" shown in the console was hand-written
mock. This synthesizes a real brief DETERMINISTICALLY from a set of ResearchNotes (the Research Memory):
what was tested, what worked / failed and why, which alphas are redundant, the top performers, and
recommended next actions + mutation suggestions. No LLM is required — an LLM can later PROPOSE next
actions, but those proposals are just MORE notes that flow through the same gate; the brief itself is a
pure, reproducible aggregation. Pure-Python stdlib, deterministic (everything sorted).
"""
from __future__ import annotations

from typing import Sequence

_FAILED = ("FAIL", "REJECTED")


def _reason_code(reason: str) -> str:
    """Normalize a gate reason to its leading code token (drop the parenthetical detail) for the
    histogram, so 'PBO_TOO_HIGH (0.6>0.5); overfit' and 'PBO_TOO_HIGH' bucket together."""
    return reason.split(" ")[0].split("(")[0].strip(";:")


def compose_brief(notes: Sequence, *, top_n: int = 5) -> dict:
    """Aggregate ResearchNotes into a reproducible research brief (see module docstring)."""
    notes = list(notes)
    ids = sorted(n.alpha_id for n in notes)
    worked = sorted((n for n in notes if n.verdict == "PASS"), key=lambda n: n.alpha_id)
    failed = sorted((n for n in notes if n.verdict in _FAILED), key=lambda n: n.alpha_id)
    review = sorted((n for n in notes if n.verdict == "REQUIRES_HUMAN_REVIEW"), key=lambda n: n.alpha_id)

    failure_reasons: dict = {}
    for n in failed:
        for r in n.reasons:
            code = _reason_code(r)
            if code:
                failure_reasons[code] = failure_reasons.get(code, 0) + 1

    top = sorted((n for n in worked if n.deflated_sharpe is not None),
                 key=lambda n: (-n.deflated_sharpe, n.alpha_id))[:max(0, top_n)]

    clusters: dict = {}
    for n in notes:
        if n.cluster_id:
            clusters.setdefault(n.cluster_id, []).append(n.alpha_id)
    redundant = {c: sorted(v) for c, v in sorted(clusters.items()) if len(v) > 1}

    dep_hist: dict = {}
    gen_hist: dict = {}
    for n in worked:
        for d in n.data_dependencies:
            dep_hist[d] = dep_hist.get(d, 0) + 1
        if n.generator:
            gen_hist[n.generator] = gen_hist.get(n.generator, 0) + 1

    # next actions: explicit ones from the notes (sorted, deduped) + deterministic heuristics
    actions = sorted({n.next_action for n in notes if n.next_action})
    if not worked and notes:
        actions.append(f"all {len(notes)} tested alphas failed validation — pivot the data/grammar "
                       f"(this space looks null)")
    if dep_hist:
        top_dep = sorted(dep_hist.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        actions.append(f"expand factors using the '{top_dep}' data dependency (dominates the survivors)")
    if gen_hist:
        top_gen = sorted(gen_hist.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        actions.append(f"scale the '{top_gen}' generator (produced the survivors)")
    seen: set = set()
    next_actions = [a for a in actions if not (a in seen or seen.add(a))]

    mutation_suggestions = [
        f"mutate {n.alpha_id} ({n.generator or 'unknown'}, DSR {n.deflated_sharpe:.2f})" for n in top
    ]

    return {
        "n_tested": len(notes),
        "summary": (f"{len(worked)}/{len(notes)} alphas passed; {len(failed)} failed, "
                    f"{len(review)} need review"),
        "tested": ids,
        "worked": [n.alpha_id for n in worked],
        "failed": [n.alpha_id for n in failed],
        "needs_review": [n.alpha_id for n in review],
        "failure_reasons": failure_reasons,
        "top_performers": [n.alpha_id for n in top],
        "redundant_clusters": redundant,
        "data_dependency_histogram": dep_hist,
        "next_actions": next_actions,
        "mutation_suggestions": mutation_suggestions,
    }
