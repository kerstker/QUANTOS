"""Re-evaluate carry_composite and crowd_contra through the durable search ledger.

The complete two-candidate pool is registered before scoring through
``run_registered_candidate_batch``. Each invocation creates an exact search attempt, binds the
real cached panel into the dataset version, preserves weekly annualization, and records immutable
evaluation links. Existing alpha state is never overwritten and a non-human research actor cannot
reach APPROVED.

The production registry is opened fail-closed with ``create=False`` by the shared helper. This tool
uses only cached research data and has no network, broker, credential, order, or live-trading path.
"""
from __future__ import annotations

import time
from typing import Dict, Tuple

from quantos.contracts.alpha import AlphaDefinition
from services.alpha.config import AlphaFactoryConfig
from services.intelligence.registered_candidate_batch import (
    run_registered_candidate_batch,
)
from services.research import alpha_expression as ax
from services.research.expression_leakage import compute_leakage_ok
from tools.crypto_oi_research import CARRY
from tools.crypto_oi_verify import CROWD_CONTRA
from tools.search_trial_registry import (
    field_panel_dataset_version,
    make_search_id,
    production_experiment_registry,
)

_FWD_HORIZON = 7   # weekly-forward returns on the daily panel — matches the deployed weekly-
                   # rebalanced dry-run/forward-paper-track book's real cadence (tools/dry_run_
                   # {carry,crowd_contra}_multi.py), not the original daily-discovery-time IC.
_PERIODS_PER_YEAR = 52   # weekly cadence (52wk/yr), matching services/execution/dry_run_pipeline.py
                         # ::_ann_sharpe's own convention for this same weekly-rebalanced book —
                         # paired with non_overlapping=True below (2026-07-09 fix; see panel_to_
                         # periods' docstring): daily-stride overlapping periods fed to sharpe()
                         # under a 252 annualization overstated net_sharpe ~1.9-2.2x (confirmed on
                         # real crowd_contra data, recorded 3.35 vs honest ~1.8 non-overlapping).
_DATASET_CONTRACT = {
    "driver": "register_alpha_catalog.v4",
    "fwd_horizon": _FWD_HORIZON,
    "non_overlapping": True,
    "periods_per_year": _PERIODS_PER_YEAR,
}

# (alpha_id, name, expression, factor_ids) — the two real, currently-deployed edges (CLAUDE.md:
# crowd_contra is the sole "deployable" book; carry is a real-but-weaker supplementary edge).
# Expressions imported, not retyped, so this driver can never silently drift from what's actually
# running in the dry-run/forward-track pipelines. CARRY/CROWD_CONTRA are plain expression-tuple
# constants (no file I/O at import time — build_panel() does that, called only inside main()), so
# importing them here does not require a populated data/cache/*.db on disk.
DEFAULT_CANDIDATES: Tuple[Tuple[str, str, object, Tuple[str, ...]], ...] = (
    ("carry_composite", "Carry — funding-rate composite (decay-weighted + ts_mean)",
     CARRY, ("funding",)),
    ("crowd_contra", "Crowd Contra — fade crowded retail OI positioning",
     CROWD_CONTRA, ("global_ls",)),
)


def build_candidates(panel, candidates=None, *, fwd_horizon: int = _FWD_HORIZON
                     ) -> Tuple[list, Dict[str, dict]]:
    """(AlphaDefinition, periods) pairs + real leakage evidence for each entry in `candidates`
    (defaults to the real CARRY/CROWD_CONTRA pair, DEFAULT_CANDIDATES). Pure given `panel`+
    `candidates` — no I/O here, so this is directly unit-testable against a synthetic FieldPanel."""
    candidates = candidates if candidates is not None else DEFAULT_CANDIDATES
    out: list = []
    evidence: Dict[str, dict] = {}
    for alpha_id, name, expr, factor_ids in candidates:
        matrix = ax.evaluate(expr, panel)
        periods = ax.panel_to_periods(matrix, panel, fwd_horizon=fwd_horizon, non_overlapping=True)
        leak_ok = compute_leakage_ok(expr, panel)
        # 2026-07-10 (gap audit rank #6 follow-up): params carries the serialized expression, matching
        # the established convention in gp_tree.py/search_space.py — AlphaDefinition.definition_hash
        # hashes ONLY factor_ids/universe/params, so two candidates sharing factor_ids (not the case
        # for carry_composite/crowd_contra today — "funding" vs "global_ls" — but a real risk for any
        # FUTURE alpha sharing a field with one of these two) would otherwise silently collide to one
        # trial-count entry. Confirmed safe for the two real, already-registered alphas: this
        # construction is only reached for candidates NOT already in the catalog
        # (register_new_candidates' idempotency skip), so it never touches their persisted state.
        defn = AlphaDefinition(alpha_id=alpha_id, name=name, factor_ids=factor_ids, universe="cs_majors",
                               params={"expr": ax.serialize(expr)})
        out.append((defn, periods))
        evidence[alpha_id] = {"leakage_ok": leak_ok}
    return out, evidence


def registration_dataset_version(panel) -> str:
    """Bind the registration run to the exact panel and evaluation cadence."""
    return field_panel_dataset_version(
        panel,
        source_versions=_DATASET_CONTRACT,
    )


def main() -> str:
    recorded_at = time.time_ns() // 1_000_000
    from tools.crypto_oi_research import build_panel   # deferred: touches real cached data files
    panel, _instruments = build_panel()
    candidates, evidence = build_candidates(panel)
    dataset_version = registration_dataset_version(panel)
    search_id = make_search_id(
        source="tools.register_alpha_catalog",
        search_kind="ALPHA_CATALOG_REGISTRATION",
        dataset_version=dataset_version,
        recorded_at=recorded_at,
        params=_DATASET_CONTRACT,
    )

    with production_experiment_registry() as registry:
        result = run_registered_candidate_batch(
            candidates,
            registry=registry,
            search_id=search_id,
            search_kind="ALPHA_CATALOG_REGISTRATION",
            search_source="tools.register_alpha_catalog",
            dataset_version=dataset_version,
            recorded_at=recorded_at,
            periods_per_year=_PERIODS_PER_YEAR,
            config=AlphaFactoryConfig(compute_regime_evidence=True),
            evidence=evidence,
        )
    report = result["report"]

    for alpha_id, r in report.items():
        ev = r.get("evaluation")
        if ev is None:
            print(f"{alpha_id}: evaluation_error={r.get('error', 'UNKNOWN')}")
            continue
        print(f"{alpha_id}: verdict={ev.verdict.value} state={r['state']} "
              f"net_sharpe={ev.net_sharpe:.3f} dsr={ev.deflated_sharpe:.3f} pbo={ev.pbo:.3f} "
              f"reasons={ev.reasons}")
    print("saved catalog snapshot -> data/cache/experiment_registry.db")
    print(f"search_id={search_id}")
    return search_id


if __name__ == "__main__":
    main()
