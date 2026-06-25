"""Experiment registry — durable catalog snapshot + immutable evaluation artifacts.

Makes the catalog-wide Deflated-Sharpe trial count and per-alpha Sharpe population
survive across processes (load_into at session start, save at end), and records
every AlphaEvaluation as a reproducible, write-once artifact. Optionally emits an
EXPERIMENT_RECORDED event into the global tamper-evident audit chain. Deterministic:
all timestamps are caller-supplied; content hashing uses stable_hash."""
from __future__ import annotations

import json
from typing import Optional

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import short_hash, stable_hash
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog, AlphaStateRow, CatalogSnapshot

from .store import ExperimentStoreError, SqliteExperimentStore


class ExperimentRegistry:
    def __init__(self, store: SqliteExperimentStore) -> None:
        self._store = store

    # --- catalog snapshot persistence ---------------------------------------

    def save(self, catalog: AlphaCatalog, *, now: int = 0) -> None:
        # NOTE (review ER-3): save() ACCUMULATES (add_trials + upsert_alpha); it does
        # NOT mirror/delete. Follow the protocol load_into(catalog) at session START
        # then save(catalog) at END — saving a catalog NOT initialized from this store
        # leaves prior rows (incl. a stale APPROVED) in place. The monotonic-accumulate
        # is the safe direction for multiple-testing counting (never un-deflates DSR).
        snap = catalog.snapshot()
        self._store.add_trials(snap.trials, first_seen_ts=now)
        for row in snap.alphas:
            sharpe = catalog._sharpes.get(row.alpha_id)   # denormalized for queries
            self._store.upsert_alpha(
                alpha_id=row.alpha_id,
                definition_json=json.dumps(row.definition.model_dump(), sort_keys=True, default=str),
                state=row.state.value,
                returns_json=json.dumps(list(row.returns)),
                sharpe=sharpe,
                pvalue=row.pvalue,
            )

    def load_into(self, catalog: AlphaCatalog) -> None:
        trials = tuple(self._store.all_trial_hashes())     # raises ExperimentStoreError if uninit
        rows = []
        try:
            for r in self._store.all_alphas():
                rows.append(AlphaStateRow(
                    alpha_id=r["alpha_id"],
                    definition=AlphaDefinition.model_validate(json.loads(r["definition_json"])),
                    state=AlphaState(r["state"]),
                    returns=tuple(json.loads(r["returns_json"])),
                    pvalue=r["pvalue"],
                ))
        except Exception as exc:   # intra-row corruption (bad JSON/state) -> fail closed,
            raise ExperimentStoreError(f"corrupt alpha_state row: {exc}") from exc  # never a silent partial load
        catalog.restore(CatalogSnapshot(trials=trials, alphas=tuple(rows)))

    # --- immutable evaluation artifacts -------------------------------------

    def record_evaluation(self, evaluation, *, dataset_version: str, recorded_ts: int,
                          audit_log=None) -> str:
        # run_id identity is derived from reproducibility_hash; an empty one (the
        # contract default) would collide DISTINCT evaluations of the same alpha+
        # dataset to one id and silently drop the later artifact (review ER-2).
        if not evaluation.reproducibility_hash:
            raise ValueError("evaluation.reproducibility_hash is empty; cannot form a unique run_id")
        payload = evaluation.model_dump()
        content_hash = stable_hash({"evaluation": payload, "dataset_version": dataset_version})
        run_id = short_hash({"alpha_id": evaluation.alpha_id,
                             "repro": evaluation.reproducibility_hash,
                             "dataset_version": dataset_version})
        inserted = self._store.insert_evaluation(
            run_id=run_id, alpha_id=evaluation.alpha_id, dataset_version=dataset_version,
            recorded_ts=recorded_ts, verdict=evaluation.verdict.value,
            content_hash=content_hash,
            evaluation_json=json.dumps(payload, sort_keys=True, default=str),
        )
        # only emit the audit event on a genuine insert — a no-op re-record must not
        # re-append a duplicate event_id (review ER-1: that crashed the retry path)
        if audit_log is not None and inserted:
            audit_log.append(
                event_id=f"exp-{run_id}", actor="experiment-registry",
                action="EXPERIMENT_RECORDED", timestamp=recorded_ts,
                target=evaluation.alpha_id,
                metadata={"run_id": run_id, "content_hash": content_hash,
                          "verdict": evaluation.verdict.value},
            )
        return run_id

    # --- bandit (guided-search) state persistence ----------------------------

    def save_bandit(self, strategy) -> None:
        """Persist the guided-search bandit's learned arm statistics so the agent's
        learning accumulates across sessions (mirrors catalog persistence)."""
        self._store.save_bandit(json.dumps(strategy.snapshot(), sort_keys=True))

    def load_bandit_into(self, strategy) -> None:
        raw = self._store.load_bandit()
        if raw is not None:
            strategy.restore(json.loads(raw))

    def get_evaluation(self, run_id: str) -> Optional[dict]:
        return self._store.get_evaluation(run_id)

    def list_evaluations(self, alpha_id: str) -> list[dict]:
        return self._store.list_evaluations(alpha_id)
