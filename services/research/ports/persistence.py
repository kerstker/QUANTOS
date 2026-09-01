"""Persistence interfaces consumed by research domain facades."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Optional, Protocol, Sequence, runtime_checkable

from quantos.contracts.events import EventOutboxMessage

if TYPE_CHECKING:
    from services.research.paper_trading.forward_evidence import ForwardAdmissionRef


class ExperimentStoreError(Exception):
    """Experiment persistence is missing, corrupt, or uninitialized."""


class TrialPersistenceError(ExperimentStoreError):
    """A selection-bearing candidate could not enter durable trial provenance."""


class ImmutableArtifactError(Exception):
    """A write attempted to change an immutable evaluation artifact."""


class PaperTrackStoreError(RuntimeError):
    """Paper-track persistence is missing, corrupt, or uninitialized."""


class ForwardLedgerError(RuntimeError):
    """Forward-decision persistence is missing, corrupt, or invalid."""


@dataclass(frozen=True)
class EvaluationAuditIntent:
    """Immutable audit command committed with an evaluation artifact."""

    event_id: str
    actor: str
    action: str
    timestamp: int
    target: str
    metadata: Mapping[str, object]


@dataclass(frozen=True)
class EvaluationArtifactWrite:
    """Prepared immutable evaluation payload for one store-owned transaction."""

    run_id: str
    alpha_id: str
    dataset_version: str
    recorded_ts: int
    verdict: str
    content_hash: str
    evaluation_json: str
    audit_intent: EvaluationAuditIntent | None = None


@runtime_checkable
class ExperimentStorePort(Protocol):
    def begin_search_attempt(
        self,
        *,
        search_id: str,
        search_kind: str,
        source: str,
        dataset_version: str,
        recorded_at: int,
    ) -> bool: ...

    def register_search_candidates(
        self,
        search_id: str,
        config_hashes: Iterable[str],
        *,
        registered_at: int,
    ) -> int: ...

    def heartbeat_search_attempt(
        self,
        search_id: str,
        *,
        recorded_at: int,
    ) -> bool: ...

    def finish_search_attempt(
        self,
        search_id: str,
        *,
        status: str,
        recorded_at: int,
    ) -> bool: ...

    def recover_stale_search_attempts(
        self,
        *,
        recorded_at: int,
        timeout_ms: int,
    ) -> tuple[str, ...]: ...

    def search_attempt_events(self, search_id: str) -> list[dict]: ...

    def search_candidates(self, search_id: str) -> list[dict]: ...

    def search_attempt_recoveries(self, search_id: str) -> list[dict]: ...

    def search_evaluations(self, search_id: str) -> list[dict]: ...

    def search_history_valid(self) -> bool: ...

    def search_identity(self, search_id: str) -> dict[str, str]: ...

    def evaluation_binding(self, run_id: str) -> Optional[dict]: ...

    def add_trials(
        self,
        config_hashes: Iterable[str],
        *,
        first_seen_ts: int,
    ) -> None: ...

    def save_catalog_snapshot(
        self,
        *,
        config_hashes: Iterable[str],
        alphas: Iterable[Mapping[str, object]],
        first_seen_ts: int,
        event_outbox: Sequence[EventOutboxMessage] = (),
    ) -> None: ...

    def save_evaluation_catalog_snapshot(
        self,
        *,
        config_hashes: Iterable[str],
        alphas: Iterable[Mapping[str, object]],
        first_seen_ts: int,
    ) -> None: ...

    def trial_count(self) -> int: ...

    def all_trial_hashes(self) -> list[str]: ...

    # --- trial provenance (727 §7.1 TrialRecord) ----------------------------

    def record_trial_provenance(self, record: Any) -> bool: ...

    def trial_provenance(self, trial_id: str) -> Optional[dict]: ...

    def trial_provenance_for_config(self, config_hash: str) -> list[dict]: ...

    def trial_provenance_chain_valid(self) -> bool: ...

    def trial_provenance_head(self) -> str: ...

    def record_search_oos_access(
        self,
        search_id: str,
        accesses: Mapping[str, Sequence[str]],
        *,
        viewed_at: int,
    ) -> int: ...

    def trial_oos_access(self, trial_id: str) -> list[dict]: ...

    def trial_oos_access_chain_valid(self) -> bool: ...

    def trial_oos_access_head(self) -> str: ...

    def oos_peek_totals(self) -> dict[str, int]: ...

    def upsert_alpha(
        self,
        alpha_id: str,
        definition_json: str,
        state: str,
        returns_json: str,
        sharpe: Optional[float],
        pvalue: Optional[float],
        *,
        research_state: Optional[str] = None,
        deployment_state: Optional[str] = None,
        health_state: Optional[str] = None,
        research_reason: Optional[str] = None,
        deployment_reason: Optional[str] = None,
        health_reason: Optional[str] = None,
        lossy_json: Optional[str] = None,
    ) -> None: ...

    def all_alphas(self) -> list[dict]: ...

    def save_bandit(self, snapshot_json: str) -> None: ...

    def load_bandit(self) -> Optional[str]: ...

    def insert_evaluation(
        self,
        *,
        run_id: str,
        alpha_id: str,
        dataset_version: str,
        recorded_ts: int,
        verdict: str,
        content_hash: str,
        evaluation_json: str,
        search_id: str | None = None,
        config_hash: str | None = None,
        audit_intent: EvaluationAuditIntent | None = None,
    ) -> bool: ...

    def get_evaluation(self, run_id: str) -> Optional[dict]: ...

    def list_evaluations(self, alpha_id: str) -> list[dict]: ...

    def pending_evaluation_audits(
        self,
        *,
        run_id: str | None = None,
        limit: int = 1_000,
    ) -> tuple[dict, ...]: ...

    def mark_evaluation_audit_dispatched(
        self,
        event_id: str,
        *,
        dispatched_at: int,
        audit_entry_hash: str,
    ) -> bool: ...

    def evaluation_audit_chain_valid(self) -> bool: ...

    def pending_event_outbox(self, *, limit: int = 1_000) -> tuple[dict, ...]: ...

    def mark_event_dispatched(
        self,
        outbox_id: str,
        *,
        dispatched_at: int,
        event_repro_hash: str,
    ) -> bool: ...

    def event_outbox_chain_valid(self) -> bool: ...


@runtime_checkable
class RunStorePort(Protocol):
    # Compatibility writes for LEGACY_MIRROR rows only. Runner invocations use insert_or_get and CAS.
    def upsert(self, run: Any) -> None: ...

    def insert(self, run: Any) -> None: ...

    def insert_or_get(self, run: Any) -> tuple[Any, bool]: ...

    def replace_if_status(
        self,
        run: Any,
        *,
        expected_status: str,
    ) -> bool: ...

    def get(self, run_id: str) -> Optional[Any]: ...

    def get_by_invocation(self, invocation_id: str) -> Optional[Any]: ...

    def list(
        self,
        kind: Optional[str] = None,
        limit: int = 200,
    ) -> list[Any]: ...

    def set_status(
        self,
        run_id: str,
        status: str,
        error: Optional[str] = None,
    ) -> None: ...

    def compare_and_set_status(
        self,
        run_id: str,
        *,
        expected_statuses: tuple[str, ...],
        status: str,
        error: Optional[str] = None,
    ) -> bool: ...

    def reconcile_interrupted_runner_runs(
        self,
        *,
        finished_ts: int,
    ) -> tuple[tuple[str, str], ...]: ...

    def append_log(self, run_id: str, line: str) -> None: ...


@runtime_checkable
class PaperTrackStorePort(Protocol):
    def append_periods(
        self,
        alpha_id: str,
        rows: Sequence[tuple],
    ) -> None: ...

    def periods(self, alpha_id: str) -> list[dict]: ...

    def recompute_summary(
        self,
        alpha_id: str,
        *,
        backtest_sharpe: Optional[float] = None,
    ) -> None: ...

    def track(self, alpha_id: str) -> Optional[dict]: ...

    def all_tracks(self) -> list[dict]: ...


@runtime_checkable
class ForwardLedgerPort(Protocol):
    def record_decision(
        self,
        alpha_id: str,
        decided_ts: int,
        *,
        recorded_at: int,
        weights: Mapping[str, float],
        admission_ref: ForwardAdmissionRef,
        signals: Optional[Mapping[str, float]] = None,
        source: str = "FORWARD_L2_ATTESTED_DECISION",
    ) -> bool: ...

    def decisions(self, alpha_id: str) -> list[dict]: ...

    def pending(self, alpha_id: str) -> list[dict]: ...

    def attested_pending(self, alpha_id: str) -> list[dict]: ...

    def legacy_decisions(self) -> list[dict]: ...

    def decision_chain_valid(self) -> bool: ...

    def commit_realization(
        self,
        alpha_id: str,
        decided_ts: int,
        *,
        as_of_ts: int,
        realized_return: float,
        realized_ic: Optional[float],
        source: str,
        inputs: Mapping[str, object],
        admission_ref: ForwardAdmissionRef,
        committed_at_ms: int,
    ) -> bool: ...

    def attested_realizations(self, alpha_id: str | None = None) -> list[dict]: ...

    def realization_chain_valid(self) -> bool: ...

    def mark_realized(self, alpha_id: str, decided_ts: int) -> None: ...
