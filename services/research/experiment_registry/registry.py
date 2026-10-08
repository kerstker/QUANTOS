"""Experiment registry — durable catalog snapshot + immutable evaluation artifacts.

Makes the catalog-wide Deflated-Sharpe trial count and per-alpha Sharpe population
survive across processes (load_into at session start, save at end), and records
every AlphaEvaluation as a reproducible, write-once artifact. Optionally emits an
EXPERIMENT_RECORDED event into the global tamper-evident audit chain. Deterministic:
all timestamps are caller-supplied; content hashing uses stable_hash."""

from __future__ import annotations

import json
from contextlib import contextmanager, ExitStack
from typing import Optional, Sequence

from quantos.contracts.alpha import AlphaDefinition, AlphaEvaluation
from quantos.contracts.events import EventOutboxMessage
from quantos.contracts.hashing import stable_hash
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.research.ports.catalog import CatalogPersistencePort
from services.lifecycle.catalog_snapshot import AlphaStateRow, CatalogSnapshot
from quantos.contracts.evaluation_evidence import (
    bind_evaluation_reproducibility,
    verify_evaluation_reproducibility,
)
# 2026-09-20 审计 P2-12:record_trial_provenance 的注解引用了从未导入的
# TrialProvenance。因为本模块有 `from __future__ import annotations`,注解不会被求值,
# 所以这不是运行时崩溃——但它让类型检查器无法校验这个边界,而且一旦有人去掉 future
# 导入就会立刻变成 NameError。导入本身无环:trial_provenance 只依赖 quantos.contracts。
from services.research.trial_provenance import TrialProvenance
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
    EvaluationAuditIntent,
    ExperimentStoreError,
    ExperimentStorePort,
    ResearchEvidenceArtifactWrite,
)
from services.validation.pbo_evidence import verify_batch_pbo_evidence
from services.validation.catalog_evidence import verify_catalog_evidence
from services.validation.selection_authority import LEGACY_INVALIDATION_CODE


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

    @staticmethod
    def _consume_project_review(*, reviewed_cut, read_snapshot, expected_subject,
                                clock_ms, registry_view, read_review_rows):
        with ExperimentRegistry._consume_project_review_context(reviewed_cut=reviewed_cut,
                read_snapshot=read_snapshot, expected_subject=expected_subject, clock_ms=clock_ms,
                registry_view=registry_view, read_review_rows=read_review_rows) as (assessment, observed, _):
            return assessment, observed

    @staticmethod
    @contextmanager
    def _consume_project_review_context(*, reviewed_cut, expected_subject, clock_ms, registry_view,
                                       read_review_rows, read_snapshot=None, read_snapshot_context=None):
        from services.validation import project_research_evidence_artifact as artifact
        from services.validation import project_research_evidence_authority as authority
        artifact.require((read_snapshot is None) != (read_snapshot_context is None), 'PROJECT_READER_CONFIGURATION')
        observations = []
        materials = []

        with ExitStack() as stack:
            def read():
                return observe(stack)

            def report_view(snapshot):
                artifact._same(snapshot, observations[-1]['snapshot'], 'PROJECT_MATERIAL_READ_BINDING')
                return artifact.retained_reports(snapshot['project_cut'], materials[-1])

            def observe(stack):
                kwargs = dict(read_review_rows=read_review_rows)
                lease_reader = getattr(read_review_rows, 'registry_view_for', None)
                if lease_reader is not None:
                    kwargs['registry_view_for'] = lease_reader
                if read_snapshot_context is None:
                    observed, reports = read_snapshot(reviewed_cut, **kwargs), None
                else:
                    observed, reports = stack.enter_context(read_snapshot_context(reviewed_cut, **kwargs))
                authority.shape._fields(observed, ('snapshot', 'current_cut'), 'PROJECT_READ_FIELDS')
                snapshot, cut = observed['snapshot'], observed['current_cut']
                artifact.verify_current_cut(reviewed_cut, cut,
                    review_rows=read_review_rows(artifact.added_review_ids(reviewed_cut, cut)), reports=reports)
                artifact._same(dict(population=cut['registry_population'], history=cut['registry_history']),
                               registry_view, 'REGISTRY_READ_CHANGED')
                artifact._same({k:v for k,v in snapshot['project_cut'].items() if k != 'read_identity_sha256'},
                    {k:v for k,v in reviewed_cut.items() if k != 'read_identity_sha256'}, 'SIGNED_CUT_CHANGED')
                artifact.require(snapshot['read_identity_sha256'] == cut['read_identity_sha256']
                    == snapshot['project_cut']['read_identity_sha256'], 'PROJECT_PHYSICAL_READ_BINDING')
                if observations:
                    artifact._same(observed, observations[-1], 'PROJECT_READ_CHANGED')
                observations.append(observed)
                materials.append(reports)
                return snapshot

            assessment = authority.consume_project_evidence(read_snapshot=read,
                expected_subject=expected_subject, clock_ms=clock_ms,
                reports_for_snapshot=report_view if read_snapshot_context is not None else None)
            yield assessment, observations[-1], materials[-1]

    def record_project_research_evidence(self, *, reviewed_cut, expected_subject, clock_ms,
                                        read_snapshot=None, read_snapshot_context=None):
        """Keep a post-computation review in the original evaluation/audit lease.

        Bootstrap selects a fixed reader; queue material selects no paths or
        keys. Context readers hold complete report views through construction.
        """
        from services.research.ports.persistence import ProjectResearchEvidenceArtifactWrite
        from services.validation import project_research_evidence_artifact as artifact
        from services.validation.research_evidence_artifact import delivery_time

        def prepare(view, read_rows):
            with self._consume_project_review_context(reviewed_cut=reviewed_cut,
                    read_snapshot=read_snapshot, read_snapshot_context=read_snapshot_context,
                    expected_subject=expected_subject, clock_ms=clock_ms,
                    registry_view=view, read_review_rows=read_rows) as (assessment, observed, reports):
                value = artifact.prepare(snapshot=observed['snapshot'], assessment=assessment, reports=reports)
                fields = artifact.row_fields(value, reports=reports)
                intent = EvaluationAuditIntent(event_id=f"exp-{fields['run_id']}", actor='experiment-registry',
                    action='EXPERIMENT_RECORDED', timestamp=fields['recorded_ts'], target=fields['alpha_id'],
                    metadata={k:fields[k] for k in ('run_id', 'content_hash', 'verdict')})
                write = ProjectResearchEvidenceArtifactWrite(**fields, audit_intent=intent)
            if read_snapshot_context is not None:
                delivery_time(observed['snapshot'], checked_at_ms=assessment['checked_at_ms'], clock_ms=clock_ms)
            return write

        def recheck(value, before, after, read_rows):
            with self._consume_project_review_context(reviewed_cut=value['snapshot']['project_cut'],
                    read_snapshot=read_snapshot, read_snapshot_context=read_snapshot_context,
                    expected_subject=value['snapshot']['subject'], clock_ms=clock_ms,
                    registry_view=before, read_review_rows=read_rows) as (assessment, observed, reports):
                current_value = artifact.prepare(snapshot=observed['snapshot'], assessment=assessment, reports=reports)
                artifact.require(artifact.review_identity(current_value, reports=reports)
                    == artifact.review_identity(value, reports=reports), 'REVIEW_CHANGED')
                expected = artifact.decode(artifact.canonical(before))
                row = artifact.row_fields(value, reports=reports)
                rows = expected['history']['evaluations']
                present = next((r for r in rows if r['run_id'] == row['run_id']), None)
                metadata = {k:row[k] for k in artifact.METADATA_FIELDS}
                if present is None:
                    rows.append(metadata)
                    rows.sort(key=lambda r:r['run_id'])
                else:
                    artifact._same(present, metadata, 'REVIEW_RETRY_METADATA')
                artifact._same(expected, after, 'PROJECT_TRANSACTION_CHANGED')
            # Context closure completes all physical report/source rechecks.
            # Sample the final clock only after those potentially slow reads.
            delivery_time(observed['snapshot'], checked_at_ms=assessment['checked_at_ms'], clock_ms=clock_ms)

        return self._store.retain_project_research_evidence(prepare=prepare, recheck=recheck)

    def get_project_research_evidence(self, run_id):
        """Historical proof only; independently current trust is not implied."""
        row = self._store.get_project_research_evidence(run_id)
        return None if row is None else dict(row, current_admission_verified=False,
                                             promotion_approved=False, submission_enabled=False)

    def preflight_project_research_evidence(self, run_id, *, clock_ms,
                                          read_snapshot=None, read_snapshot_context=None):
        from services.validation import project_research_evidence_artifact as artifact
        from services.validation.research_evidence_artifact import delivery_time
        row = self._store.get_project_research_evidence(run_id)
        artifact.require(row is not None, 'RECEIPT_MISSING')
        decoded = artifact.decode(row['evaluation_json'].encode())
        with self._store.project_research_result_materials(decoded['snapshot']['project_cut']) as reports:
            value = artifact.validate_row(row, reports=reports)
        view = self._store.project_research_registry_view()
        with self._consume_project_review_context(reviewed_cut=value['snapshot']['project_cut'],
                read_snapshot=read_snapshot, read_snapshot_context=read_snapshot_context,
                expected_subject=value['snapshot']['subject'], clock_ms=clock_ms,
                registry_view=view, read_review_rows=self._store.read_project_research_review_rows) as (checked, observed, reports):
            artifact._same(view, self._store.project_research_registry_view(), 'REGISTRY_READ_CHANGED')
            candidate = artifact.prepare(snapshot=observed['snapshot'], assessment=checked, reports=reports)
            artifact.require(artifact.review_identity(candidate, reports=reports)
                == artifact.review_identity(value, reports=reports), 'REVIEW_CHANGED')
        checked['checked_at_ms'] = delivery_time(observed['snapshot'],
            checked_at_ms=checked['checked_at_ms'], clock_ms=clock_ms)
        return checked

    def record_research_evidence(self, *, read_snapshot, expected_subject, search_id,
                                config_hash, native_trial_mapping, provenance_trial_mapping,
                                clock_ms):
        """Retain independently reviewed facts using a composition-root reader.

        The expected subject is an assertion, not permission to bind arbitrary
        results to a search. Its producer/kind/native definition are checked
        against original search/candidate and the complete registry population.
        """
        from services.validation import research_evidence_artifact as artifact
        from services.validation import research_evidence_authority as authority

        def prepare(population, identity):
            snapshots = []

            def read():
                snapshot = read_snapshot()
                snapshots[:] = [snapshot]
                return snapshot

            assessment = authority.consume_evidence(read_snapshot=read,
                expected_subject=expected_subject, clock_ms=clock_ms)
            value = artifact.prepare(snapshot=snapshots[0], assessment=assessment,
                population=population, search_identity=identity, config_hash=config_hash,
                native_trial_mapping=native_trial_mapping, provenance_trial_mapping=provenance_trial_mapping)
            fields = artifact.row_fields(value)
            intent = EvaluationAuditIntent(event_id=f"exp-{fields['run_id']}", actor='experiment-registry',
                action='EXPERIMENT_RECORDED', timestamp=fields['recorded_ts'], target=fields['alpha_id'],
                metadata={k:fields[k] for k in ('run_id', 'content_hash', 'verdict')})
            return ResearchEvidenceArtifactWrite(**fields, audit_intent=intent)

        def recheck(value):
            def read():
                snapshot = read_snapshot()
                artifact.require(artifact.canonical(artifact.pack_snapshot(snapshot)) ==
                                 artifact.canonical(value['snapshot']), 'READ_CHANGED')
                return snapshot

            authority.validate_assessment(value['assessment'], read_snapshot=read,
                expected_subject=expected_subject, clock_ms=clock_ms)

        return self._store.retain_research_evidence(prepare=prepare, recheck=recheck,
            search_id=search_id, config_hash=config_hash)

    def get_research_evidence(self, run_id):
        """Verify a historical receipt without asserting current trust/eligibility."""
        row = self._store.get_research_evidence(run_id)
        return None if row is None else dict(row, current_admission_verified=False,
                                             promotion_approved=False, submission_enabled=False)

    def preflight_research_evidence(self, run_id, *, read_snapshot, clock_ms):
        from services.validation import research_evidence_artifact as artifact
        from services.validation import research_evidence_authority as authority
        row = self._store.get_research_evidence(run_id)
        artifact.require(row is not None, 'RECEIPT_MISSING')
        value = artifact.validate_row(row)
        artifact.verify_population_prefix(value['population'], self._store.research_evidence_population(), exact=True)
        snapshots = []

        def read():
            snapshot = read_snapshot()
            snapshots[:] = [snapshot]
            return snapshot

        checked = authority.validate_assessment(value['assessment'], read_snapshot=read,
            expected_subject=value['assessment']['subject'], clock_ms=clock_ms)
        # Trial population can grow while the fixed evidence reader is working.
        artifact.verify_population_prefix(value['population'], self._store.research_evidence_population(), exact=True)
        checked['checked_at_ms'] = artifact.delivery_time(snapshots[0],
            checked_at_ms=checked['checked_at_ms'], clock_ms=clock_ms)
        return checked

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
        catalog: CatalogPersistencePort,
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
            sharpe = catalog.sharpe(row.alpha_id)  # denormalized for queries
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
        catalog: CatalogPersistencePort,
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
                    "sharpe": catalog.sharpe(row.alpha_id),
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

    def load_into(self, catalog: CatalogPersistencePort) -> None:
        trials = tuple(self._store.alpha_hypothesis_trial_hashes())  # raises ExperimentStoreError if uninit
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

    def load_for_evaluation(self, catalog: CatalogPersistencePort) -> str:
        """Load trial/statistical context without stale lifecycle identities."""
        trial_rows, alpha_rows = self._store.evaluation_context_snapshot()
        trials = tuple(self._store.alpha_hypothesis_trial_hashes())
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

        # This v2 registry stores legacy statistical evaluations, not an
        # independently signed V2 perpetual-backtest authority record.  Keep
        # the immutable metric artifact for audit, but never persist PASS as a
        # selection-bearing verdict through this schema.
        reasons = tuple(parsed.reasons)
        if LEGACY_INVALIDATION_CODE not in reasons:
            reasons += (LEGACY_INVALIDATION_CODE,)
        parsed = parsed.model_copy(
            update={
                "verdict": (
                    ValidationVerdict.REQUIRES_HUMAN_REVIEW
                    if parsed.verdict == ValidationVerdict.PASS
                    else parsed.verdict
                ),
                "reasons": reasons,
            }
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
        row = self._store.get_evaluation(run_id)
        return None if row is None else self._diagnostic_evaluation_row(row)

    def list_evaluations(self, alpha_id: str) -> list[dict]:
        return [
            self._diagnostic_evaluation_row(row)
            for row in self._store.list_evaluations(alpha_id)
        ]

    @staticmethod
    def _diagnostic_evaluation_row(row: dict) -> dict:
        return {
            **row,
            "evidence_status": "DIAGNOSTIC_ONLY",
            "selection_authority": False,
            "invalidation_code": LEGACY_INVALIDATION_CODE,
        }

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
