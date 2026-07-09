"""Register carry_composite/crowd_contra into the real AlphaCatalog (audit P0 fix, 2026-07-09).

The 26-module/185-test lifecycle/catalog/promotion machinery
(services/alpha/{catalog,lifecycle,evaluator,promotion}.py) has never processed a single real
candidate: the alphas actually running in dry-run / forward paper-track / live testnet
(CARRY/CROWD_CONTRA, tools/crypto_oi_research.py + tools/crypto_oi_verify.py) are raw Python
expression-tuple constants, invisible to AlphaCatalog — `grep services.alpha.{catalog,lifecycle,
promotion,registry}` across the tools/ drivers that actually run them returns zero hits.
services/alpha/promotion.py::gather_promotion_evidence already correctly reads real evaluation/
paper-track artifacts once an alpha IS in the catalog; this was the one missing step —
catalog.register() had simply never been called for them.

Runs as a non-human AGENT actor via the existing, tested services/alpha/factory.py::AlphaFactory
orchestrator (register -> evaluate -> advance as far as the evidence allows). Structurally CANNOT
reach APPROVED: AlphaFactory.run() only ever calls advance() up to PAPER_TRADING or REJECTED, and
that ceiling is independently enforced by services/alpha/lifecycle.py's assert_alpha_transition
(a non-human actor -> UnauthorizedPromotion) regardless of anything in this script. This script
computes REAL evidence (leakage via services.research.expression_leakage.compute_leakage_ok on the
actual expression + panel; overfitting-probability via CSCV over the two real candidates' net
returns, services.alpha.factory.batch_pbo; regime evidence self-computed by the evaluator from the
panel's own market state, AlphaFactoryConfig(compute_regime_evidence=True)) and lets the real
evaluation gate decide the outcome — it does not pre-select or bias the target state.

Idempotent / safely re-runnable: loads existing catalog state first and skips any alpha_id already
registered (AlphaCatalog.register() raises ValueError on a duplicate id — re-evaluating an
already-registered alpha with fresh evidence is AlphaLifecycleManager.advance()'s job, a distinct,
separately-invoked operation, not this one-time backfill driver's).

Persists to data/cache/experiment_registry.db — the real, durable catalog store that
services/governance/promotion_api/main.py reads for actual human promotion decisions. Kept safe
from tools/crypto_research_offline.py's destructive demo-panel rebuild by a 2026-07-09 fix that
gave that regenerator its own dedicated file (crypto_research_demo_registry.db) — before that fix,
re-running the demo regenerator would have silently wiped any real registration made here.

No network, no broker/SDK, no order/credential — reads only the already-cached local PIT store
(data/cache/crypto_binance.db et al, via tools.crypto_oi_research.build_panel).
"""
from __future__ import annotations

import os
import time
from typing import Dict, Optional, Sequence, Tuple

from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.alpha.factory import AlphaFactory
from services.research import alpha_expression as ax
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.expression_leakage import compute_leakage_ok
from tools.crypto_oi_research import CARRY
from tools.crypto_oi_verify import CROWD_CONTRA

_EXP_DB = os.path.join("data", "cache", "experiment_registry.db")
_FWD_HORIZON = 7   # weekly-forward returns on the daily panel — matches the deployed weekly-
                   # rebalanced dry-run/forward-paper-track book's real cadence (tools/dry_run_
                   # {carry,crowd_contra}_multi.py), not the original daily-discovery-time IC.
_DATASET_VERSION = "carry_crowd_registration_v2_pbo_align_fix"   # v1's evaluations predate the
                                                                  # 2026-07-09 batch_pbo calendar-
                                                                  # alignment fix; bumped so this
                                                                  # run's evaluation artifacts get a
                                                                  # fresh run_id rather than
                                                                  # colliding with the stale v1
                                                                  # artifact under the write-once
                                                                  # immutability check (correct
                                                                  # behavior: the code changed, this
                                                                  # is honestly a new evaluation
                                                                  # lineage, not the same one).

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
        periods = ax.panel_to_periods(matrix, panel, fwd_horizon=fwd_horizon)
        leak_ok = compute_leakage_ok(expr, panel)
        defn = AlphaDefinition(alpha_id=alpha_id, name=name, factor_ids=factor_ids, universe="cs_majors")
        out.append((defn, periods))
        evidence[alpha_id] = {"leakage_ok": leak_ok}
    return out, evidence


def register_new_candidates(catalog: AlphaCatalog, candidates: Sequence[tuple], evidence: dict, *,
                            config: Optional[AlphaFactoryConfig] = None,
                            actor: Optional[Actor] = None, timestamp: int = 0) -> dict:
    """Runs AlphaFactory only on candidates whose alpha_id is NOT already in `catalog` (idempotent
    re-run: AlphaCatalog.register() raises on a duplicate id, and re-scoring an already-registered
    alpha belongs to AlphaLifecycleManager.advance(), not here). Returns {} (no-op) when every
    candidate is already registered."""
    already = set(catalog.all_ids())
    fresh = [(defn, periods) for defn, periods in candidates if defn.alpha_id not in already]
    if not fresh:
        return {}
    factory = AlphaFactory(catalog=catalog, config=config, actor=actor)
    return factory.run(fresh, timestamp=timestamp, evidence=evidence)


def main() -> None:
    now = int(time.time() * 1000)
    from tools.crypto_oi_research import build_panel   # deferred: touches real cached data files
    panel, _instruments = build_panel()
    candidates, evidence = build_candidates(panel)

    os.makedirs(os.path.dirname(_EXP_DB), exist_ok=True)
    store = SqliteExperimentStore(_EXP_DB, create=True)
    registry = ExperimentRegistry(store)
    catalog = AlphaCatalog()
    registry.load_into(catalog)   # no-op (empty) on a freshly-created store; never raises on empty

    config = AlphaFactoryConfig(compute_regime_evidence=True)
    actor = Actor(actor_id="register-alpha-catalog", kind=ActorKind.SYSTEM)
    report = register_new_candidates(catalog, candidates, evidence, config=config, actor=actor, timestamp=now)

    if not report:
        print(f"no new candidates to register (already present in {_EXP_DB})")
        return
    for alpha_id, r in report.items():
        ev = r["evaluation"]
        registry.record_evaluation(ev, dataset_version=_DATASET_VERSION, recorded_ts=now)
        print(f"{alpha_id}: verdict={ev.verdict.value} state={r['state'].value} "
              f"net_sharpe={ev.net_sharpe:.3f} dsr={ev.deflated_sharpe:.3f} pbo={ev.pbo:.3f} "
              f"reasons={ev.reasons}")
    registry.save(catalog, now=now)
    print(f"saved catalog snapshot -> {_EXP_DB}")


if __name__ == "__main__":
    main()
