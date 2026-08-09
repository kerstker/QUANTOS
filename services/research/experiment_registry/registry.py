"""Experiment registry — durable catalog snapshot + immutable evaluation artifacts.

Makes the catalog-wide Deflated-Sharpe trial count and per-alpha Sharpe population
survive across processes (load_into at session start, save at end), and records
every AlphaEvaluation as a reproducible, write-once artifact. Optionally emits an
EXPERIMENT_RECORDED event into the global tamper-evident audit chain. Deterministic:
all timestamps are caller-supplied; content hashing uses stable_hash."""

from __future__ import annotations

import json
from typing import Optional, Sequence

from quantos.contracts.alpha import AlphaDefinition, AlphaEvaluation
from quantos.contracts.events import EventOutboxMessage
from quantos.contracts.hashing import stable_hash
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog, AlphaStateRow, CatalogSnapshot
from services.alpha.evaluation_evidence import (
    bind_evaluation_reproducibility,
    verify_evaluation_reproducibility,
)
from services.lifecycle import (
    DeploymentState,
    HealthReason,
    HealthState,
    HealthVerdict,
    LifecycleTriple,
    ResearchState,
    project,
)
from services.research.experiment_registry.evaluation_artifact import (
    prepare_evaluation_artifact,
)
from services.research.ports.persistence import (
    ExperimentStoreError,
    ExperimentStorePort,
)
from services.validation.pbo_evidence import verify_batch_pbo_evidence
from services.validation.catalog_evidence import verify_catalog_evidence


def _triple_db_fields(triple: LifecycleTriple) -> dict:
    return {
        "research_state": triple.research.value,
        "deployment_state": triple.deployment.value,
        "health_state": triple.health.state.value,
        "research_reason": None,
        "deployment_reason": None,
        "health_reason": triple.health.reason.value,
        "lossy_json": json.dumps(list(triple.lossy)),
    }


def _triple_from_row(row: dict) -> LifecycleTriple:
    legacy = AlphaState(row["state"])
    if row.get("research_state") is None:
        return project(legacy)
    health_reason = row.get("health_reason") or HealthReason.NONE.value
    lossy_json = row.get("lossy_json")
    lossy = (
        project(legacy).lossy
        if lossy_json is None
        else tuple(json.loads(lossy_json))
    )
    return LifecycleTriple(
        research=ResearchState(row["research_state"]),
        deployment=DeploymentState(row["deployment_state"]),
        health=HealthVerdict(
            HealthState(row["health_state"]),
            HealthReason(health_reason),
        ),
        lossy=lossy,
    )


class ExperimentRegistry:
    def __init__(self, store: ExperimentStorePort) -> None:
        self._store = store

    # --- catalog snapshot persistence ---------------------------------------

    def begin_search_attempt(
        self,
        *,
        search_id: str,
        search_kind: str,
        source: str,
        dataset_version: str,
        recorded_at: int,
    ) -> bool:
        return self._store.begin_search_attempt(
            search_id=search_id,
            search_kind=search_kind,
            source=source,
            dataset_version=dataset_version,
            recorded_at=recorded_at,
        )

    def register_search_candidates(
        self,
        search_id: str,
        config_hashes,
        *,
        registered_at: int,
    ) -> int:
        return self._store.register_search_candidates(
            search_id,
            config_hashes,
            registered_at=registered_at,
        )

    def heartbeat_search_attempt(
        self,
        search_id: str,
        *,
        recorded_at: int,
    ) -> bool:
        return self._store.heartbeat_search_attempt(
            search_id,
            recorded_at=recorded_at,
        )

    def finish_search_attempt(
        self,
        search_id: str,
        *,
        status: str,
        recorded_at: int,
    ) -> bool:
        return self._store.finish_search_attempt(
            search_id,
            status=status,
            recorded_at=recorded_at,
        )

    def recover_stale_search_attempts(
        self,
        *,
        recorded_at: int,
        timeout_ms: int,
    ) -> tuple[str, ...]:
        return self._store.recover_stale_search_attempts(
            recorded_at=recorded_at,
            timeout_ms=timeout_ms,
        )

    def search_attempt_events(self, search_id: str) -> list[dict]:
        return self._store.search_attempt_events(search_id)

    def search_candidates(self, search_id: str) -> list[dict]:
        return self._store.search_candidates(search_id)

    def search_attempt_recoveries(self, search_id: str) -> list[dict]:
        return self._store.search_attempt_recoveries(search_id)

    def search_evaluations(self, search_id: str) -> list[dict]:
        return self._store.search_evaluations(search_id)

    def search_history_valid(self) -> bool:
        return self._store.search_history_valid()

    def search_identity(self, search_id: str) -> dict[str, str]:
        """Return the verified immutable identity of a persistent search."""
        return self._store.search_identity(search_id)

    # --- trial provenance (727 §7.1) ----------------------------------------

    def record_trial_provenance(self, record: TrialProvenance) -> bool:
        """Retain one immutable TrialRecord. False means already retained
        (idempotent replay), NOT that the write was skipped silently."""
        return self._store.record_trial_provenance(record)

    def trial_provenance(self, trial_id: str) -> Optional[dict]:
        return self._store.trial_provenance(trial_id)

    def trial_provenance_for_config(self, config_hash: str) -> list[dict]:
        return self._store.trial_provenance_for_config(config_hash)

    def trial_provenance_chain_valid(self) -> bool:
        return self._store.trial_provenance_chain_valid()

    def trial_provenance_head(self) -> str:
        return self._store.trial_provenance_head()

    def record_search_oos_access(
        self,
        search_id: str,
        accesses,
        *,
        viewed_at: int,
    ) -> int:
        return self._store.record_search_oos_access(
            search_id, accesses, viewed_at=viewed_at
        )

    def trial_oos_access(self, trial_id: str) -> list[dict]:
        return self._store.trial_oos_access(trial_id)

    def trial_oos_access_chain_valid(self) -> bool:
        return self._store.trial_oos_access_chain_valid()

    def trial_oos_access_head(self) -> str:
        return self._store.trial_oos_access_head()

    def oos_peek_totals(self) -> dict[str, int]:
        return self._store.oos_peek_totals()

    def evaluation_binding(self, run_id: str) -> dict | None:
        """Return the verified search identity bound to an evaluation artifact."""
        return self._store.evaluation_binding(run_id)

    def save(
        self,
        catalog: AlphaCatalog,
        *,
        now: int = 0,
        event_outbox: Sequence[EventOutboxMessage] = (),
    ) -> None:
        # NOTE (review ER-3): save() ACCUMULATES (add_trials + upsert_alpha); it does
        # NOT mirror/delete. Follow the protocol load_into(catalog) at session START
        # then save(catalog) at END — saving a catalog NOT initialized from this store
        # leaves prior rows (incl. a stale APPROVED) in place. The monotonic-accumulate
        # is the safe direction for multiple-testing counting (never un-deflates DSR).
        snap = catalog.snapshot()
        alpha_rows = []
        for row in snap.alphas:
            sharpe = catalog._sharpes.get(row.alpha_id)  # denormalized for queries
            alpha_rows.append(
                {
                    "alpha_id": row.alpha_id,
                    "definition_json": json.dumps(
                        row.definition.model_dump(), sort_keys=True, default=str
                    ),
                    "state": row.state.value,
                    "returns_json": json.dumps(list(row.returns)),
                    "sharpe": sharpe,
                    "pvalue": row.pvalue,
                    **_triple_db_fields(row.triple or project(row.state)),
                }
            )
        self._store.save_catalog_snapshot(
            config_hashes=snap.trials,
            alphas=alpha_rows,
            first_seen_ts=now,
            event_outbox=event_outbox,
        )

    def save_evaluation_context(
        self,
        catalog: AlphaCatalog,
        *,
        now: int,
    ) -> None:
        """Persist new evaluated alphas without rewriting existing lifecycle rows."""
        snapshot = catalog.snapshot()
        alpha_rows = []
        for row in snapshot.alphas:
            alpha_rows.append(
                {
                    "alpha_id": row.alpha_id,
                    "definition_json": json.dumps(
                        row.definition.model_dump(), sort_keys=True, default=str
                    ),
                    "state": row.state.value,
                    "returns_json": json.dumps(list(row.returns)),
                    "sharpe": catalog._sharpes.get(row.alpha_id),
                    "pvalue": row.pvalue,
                    **_triple_db_fields(row.triple or project(row.state)),
                }
            )
        self._store.save_evaluation_catalog_snapshot(
            config_hashes=snapshot.trials,
            alphas=alpha_rows,
            first_seen_ts=now,
        )

    def pending_event_outbox(self, *, limit: int = 1_000) -> tuple[dict, ...]:
        return self._store.pending_event_outbox(limit=limit)

    def mark_event_dispatched(
        self,
        outbox_id: str,
        *,
        dispatched_at: int,
        event_repro_hash: str,
    ) -> bool:
        return self._store.mark_event_dispatched(
            outbox_id,
            dispatched_at=dispatched_at,
            event_repro_hash=event_repro_hash,
        )

    def event_outbox_chain_valid(self) -> bool:
        return self._store.event_outbox_chain_valid()

    def load_into(self, catalog: AlphaCatalog) -> None:
        trials = tuple(self._store.all_trial_hashes())  # raises ExperimentStoreError if uninit
        rows = []
        try:
            for r in self._store.all_alphas():
                rows.append(
                    AlphaStateRow(
                        alpha_id=r["alpha_id"],
                        definition=AlphaDefinition.model_validate(json.loads(r["definition_json"])),
                        state=AlphaState(r["state"]),
                        returns=tuple(json.loads(r["returns_json"])),
                        pvalue=r["pvalue"],
                        triple=_triple_from_row(r),
                    )
                )
        except Exception as exc:  # intra-row corruption (bad JSON/state) -> fail closed,
            raise ExperimentStoreError(f"corrupt alpha_state row: {exc}") from exc  # never a silent partial load
        catalog.restore(CatalogSnapshot(trials=trials, alphas=tuple(rows)))

    def load_for_evaluation(self, catalog: AlphaCatalog) -> str:
        """Load trial/statistical context without stale lifecycle identities."""
        trial_rows, alpha_rows = self._store.evaluation_context_snapshot()
        trials = tuple(trial_rows)
        historical_returns = {}
        historical_pvalues = {}
        historical_definition_hashes = {}
        try:
            for row in alpha_rows:
                # Validate the complete canonical row even though lifecycle state is
                # deliberately not installed into the fresh evaluation catalog.
                definition = AlphaDefinition.model_validate(
                    json.loads(row["definition_json"])
                )
                AlphaState(row["state"])
                _triple_from_row(row)  # fail closed on corrupt C2 columns too
                values = json.loads(row["returns_json"])
                if not isinstance(values, list):
                    raise ValueError("returns_json must decode to a list")
                historical_returns[row["alpha_id"]] = tuple(
                    float(value) for value in values
                )
                historical_definition_hashes[row["alpha_id"]] = (
                    definition.definition_hash
                )
                if row["pvalue"] is not None:
                    historical_pvalues[row["alpha_id"]] = float(row["pvalue"])
        except Exception as exc:
            raise ExperimentStoreError(
                f"corrupt alpha_state row: {exc}"
            ) from exc
        catalog.restore_evaluation_context(
            trials=trials,
            historical_returns=historical_returns,
            historical_pvalues=historical_pvalues,
            historical_definition_hashes=historical_definition_hashes,
        )
        return stable_hash(
            {
                "schema_version": 1,
                "trials": sorted(trials),
                "alpha_state_rows": alpha_rows,
            }
        )

    # --- immutable evaluation artifacts -------------------------------------

    def record_evaluation(
        self,
        evaluation,
        *,
        dataset_version: str,
        recorded_ts: int,
        search_id: str | None = None,
        config_hash: str | None = None,
        audit_log=None,
    ) -> str:
        raw_evaluation = (
            evaluation.model_dump()
            if hasattr(evaluation, "model_dump")
            else evaluation
        )
        parsed = AlphaEvaluation.model_validate(raw_evaluation)
        if parsed.catalog_evidence is not None and not verify_catalog_evidence(
            parsed.catalog_evidence
        ):
            raise ValueError("evaluation catalog evidence does not replay")
        if parsed.pbo_evidence is not None and not verify_batch_pbo_evidence(
            parsed.pbo_evidence
        ):
            raise ValueError("evaluation PBO evidence does not replay")
        if parsed.verdict.value == "PASS" and (
            parsed.catalog_evidence is None or parsed.pbo_evidence is None
        ):
            raise ValueError("PASS evaluation requires complete replayable evidence")
        if (
            config_hash is not None
            and parsed.pbo_evidence is not None
            and config_hash not in parsed.pbo_evidence.candidate_definition_hashes
        ):
            raise ValueError("evaluation PBO evidence is not bound to the search candidate")
        if (
            config_hash is not None
            and parsed.catalog_evidence is not None
            and config_hash
            != parsed.catalog_evidence.subject_definition_hash
        ):
            raise ValueError(
                "evaluation catalog evidence is not bound to the search candidate"
            )
        parsed = bind_evaluation_reproducibility(
            parsed, dataset_version=dataset_version
        )
        write = prepare_evaluation_artifact(
            parsed,
            dataset_version,
            recorded_ts,
        )
        self._store.insert_evaluation(
            run_id=write.run_id,
            alpha_id=write.alpha_id,
            dataset_version=write.dataset_version,
            recorded_ts=write.recorded_ts,
            verdict=write.verdict,
            content_hash=write.content_hash,
            evaluation_json=write.evaluation_json,
            search_id=search_id,
            config_hash=config_hash,
            audit_intent=write.audit_intent,
        )
        if audit_log is not None:
            self.recover_evaluation_audits(
                audit_log,
                dispatched_at=recorded_ts,
                run_id=write.run_id,
            )
        return write.run_id

    def pending_evaluation_audits(
        self,
        *,
        run_id: str | None = None,
        limit: int = 1_000,
    ) -> tuple[dict, ...]:
        return self._store.pending_evaluation_audits(
            run_id=run_id,
            limit=limit,
        )

    @staticmethod
    def _matching_audit_event(audit_log, record: dict):
        events = getattr(audit_log, "events", None)
        if events is None:
            raise TypeError("audit_log must expose an immutable events view")
        for event in events:
            if getattr(event, "event_id", None) != record["event_id"]:
                continue
            if (
                getattr(event, "actor", None) != record["actor"]
                or getattr(event, "action", None) != record["action"]
                or getattr(event, "timestamp", None) != record["timestamp"]
                or getattr(event, "target", None) != record["target"]
                or dict(getattr(event, "metadata", {})) != record["metadata"]
            ):
                raise ExperimentStoreError(
                    f"audit sink event conflict: {record['event_id']}"
                )
            return event
        return None

    def recover_evaluation_audits(
        self,
        audit_log,
        *,
        dispatched_at: int,
        run_id: str | None = None,
        limit: int = 1_000,
    ) -> int:
        """Explicitly replay pending audit intents using publish-then-ack semantics.

        This is a single-node recovery primitive, not a background worker. The
        sink must retain event IDs so a crash after append and before ack can be
        recognized as an idempotent replay.
        """
        pending = self.pending_evaluation_audits(run_id=run_id, limit=limit)
        recovered = 0
        for record in pending:
            event = self._matching_audit_event(audit_log, record)
            if event is None:
                event = audit_log.append(
                    event_id=record["event_id"],
                    actor=record["actor"],
                    action=record["action"],
                    timestamp=record["timestamp"],
                    target=record["target"],
                    metadata=record["metadata"],
                )
            self._audit_dispatch_checkpoint("after_publish")
            entry_hash = getattr(event, "entry_hash", "")
            if not isinstance(entry_hash, str) or not entry_hash:
                raise ExperimentStoreError(
                    f"audit sink omitted entry hash: {record['event_id']}"
                )
            self._store.mark_evaluation_audit_dispatched(
                record["event_id"],
                dispatched_at=dispatched_at,
                audit_entry_hash=entry_hash,
            )
            recovered += 1
        return recovered

    def _audit_dispatch_checkpoint(self, stage: str) -> None:
        """Test seam for publish-before-ack crash recovery."""
        del stage

    def evaluation_audit_chain_valid(self) -> bool:
        return self._store.evaluation_audit_chain_valid()

    # --- bandit (guided-search) state persistence ----------------------------

    def save_bandit(self, strategy, *, dataset_version: str = "") -> None:
        """Persist the guided-search bandit's learned arm statistics so the agent's
        learning accumulates across sessions (mirrors catalog persistence)."""
        self._store.save_bandit(
            json.dumps(
                {
                    "schema_version": 2,
                    "dataset_version": dataset_version,
                    "snapshot": strategy.snapshot(),
                },
                sort_keys=True,
            )
        )

    def load_bandit_into(self, strategy, *, dataset_version: Optional[str] = None) -> None:
        raw = self._store.load_bandit()
        if raw is not None:
            payload = json.loads(raw)
            if isinstance(payload, dict) and payload.get("schema_version") == 2:
                if (
                    dataset_version is not None
                    and payload.get("dataset_version") != dataset_version
                ):
                    return
                strategy.restore(payload["snapshot"])
            elif dataset_version is None:
                strategy.restore(payload)

    def get_evaluation(self, run_id: str) -> Optional[dict]:
        return self._store.get_evaluation(run_id)

    def list_evaluations(self, alpha_id: str) -> list[dict]:
        return self._store.list_evaluations(alpha_id)

    def verify_evaluation(self, run_id: str) -> bool:
        """Rank #12 — the missing verify half of the write-once evaluation store: recompute the
        content_hash from the STORED evaluation_json + dataset_version and compare it to the stored
        content_hash. Detects post-hoc tampering of a recorded artifact (a metric edited directly in
        sqlite). Fail-closed: an unknown run_id or any error is False (never a silent 'verified')."""
        try:
            row = self._store.get_evaluation(run_id)
            if row is None:
                return False
            payload = json.loads(row["evaluation_json"])
            recomputed = stable_hash(
                {"evaluation": payload, "dataset_version": row["dataset_version"]}
            )
            if recomputed != row["content_hash"]:
                return False
            evaluation = AlphaEvaluation.model_validate(payload)
            return (
                evaluation.catalog_evidence is not None
                and verify_catalog_evidence(evaluation.catalog_evidence)
                and evaluation.pbo_evidence is not None
                and verify_batch_pbo_evidence(evaluation.pbo_evidence)
                and evaluation.catalog_evidence.subject_definition_hash
                in evaluation.pbo_evidence.candidate_definition_hashes
                and verify_evaluation_reproducibility(
                    evaluation,
                    dataset_version=row["dataset_version"],
                )
            )
        except Exception:
            return False
