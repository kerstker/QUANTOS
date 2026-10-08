"""Recover pending records from every local transactional domain-event outbox."""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from services.alpha.maintenance_store import MaintenanceStore
from services.data.admission_store import DataQualityAdmissionStore
from services.data.refresh_history import RefreshHistoryStore
from services.deployment.runtime import DEPLOYMENT_STATE_DB
from services.deployment.store import DeploymentDecisionStore
from services.execution.dry_run_store import DryRunStore
from live.execution.shadow_store import SqliteShadowStore
from services.monitoring.domain_event_runtime import (
    DOMAIN_EVENT_DB,
    DOMAIN_EVENT_PROJECTION_DB,
    dispatch_data_quality_admission_event_outbox,
    dispatch_deployment_event_outbox,
    dispatch_experiment_event_outbox,
    dispatch_kill_switch_event_outbox,
    dispatch_maintenance_event_outbox,
    dispatch_refresh_event_outbox,
    dispatch_shadow_event_outbox,
    open_domain_event_bus,
    project_pending_domain_events,
)
from services.research.experiment_registry.store import SqliteExperimentStore
from services.risk.pre_trade.kill_switch_store import KillSwitchStateStore
from tools.cache_io import atomic_write_json
from tools.domain_event_bus import dispatch_dry_run_outbox


DRY_RUN_DB = os.path.join("data", "cache", "dry_run.db")
EXPERIMENT_REGISTRY_DB = os.path.join(
    "data", "cache", "experiment_registry.db"
)
ALPHA_MAINTENANCE_DB = os.path.join(
    "data", "cache", "alpha_maintenance.db"
)
KILL_SWITCH_STATE_DB = os.path.join(
    "data", "cache", "live_kill_switch.db"
)
DATA_REFRESH_HISTORY_DB = os.path.join(
    "data", "cache", "live_refresh_history.db"
)
DATA_QUALITY_ADMISSION_DB = os.path.join(
    "data", "cache", "data_quality_admission.db"
)
SHADOW_OBSERVATIONS_DB = os.path.join(
    "data", "cache", "shadow_observations.db"
)
DOMAIN_EVENT_DRAIN_SNAPSHOT = os.path.join(
    "data", "cache", "domain_event_outbox_drain.json"
)


@dataclass(frozen=True)
class OutboxSource:
    name: str
    path: str
    opener: Callable[[str], object]
    dispatcher: Callable[..., dict]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("outbox source name must be non-empty")
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("outbox source path must be non-empty")
        if not callable(self.opener) or not callable(self.dispatcher):
            raise TypeError("outbox source opener and dispatcher must be callable")


def default_outbox_sources() -> tuple[OutboxSource, ...]:
    return (
        OutboxSource(
            "dry_run",
            DRY_RUN_DB,
            lambda path: DryRunStore(path, create=False),
            dispatch_dry_run_outbox,
        ),
        OutboxSource(
            "experiment_registry",
            EXPERIMENT_REGISTRY_DB,
            lambda path: SqliteExperimentStore(path, create=False),
            dispatch_experiment_event_outbox,
        ),
        OutboxSource(
            "alpha_maintenance",
            ALPHA_MAINTENANCE_DB,
            lambda path: MaintenanceStore(path, create=False),
            dispatch_maintenance_event_outbox,
        ),
        OutboxSource(
            "kill_switch",
            KILL_SWITCH_STATE_DB,
            lambda path: KillSwitchStateStore(path, create=False),
            dispatch_kill_switch_event_outbox,
        ),
        OutboxSource(
            "deployment",
            DEPLOYMENT_STATE_DB,
            lambda path: DeploymentDecisionStore(path, create=False),
            dispatch_deployment_event_outbox,
        ),
        OutboxSource(
            "data_refresh",
            DATA_REFRESH_HISTORY_DB,
            lambda path: RefreshHistoryStore(path, create=False),
            dispatch_refresh_event_outbox,
        ),
        OutboxSource(
            "data_quality_admission",
            DATA_QUALITY_ADMISSION_DB,
            lambda path: DataQualityAdmissionStore(path, create=False),
            dispatch_data_quality_admission_event_outbox,
        ),
        # SHADOW(Phase 7 S2):owner store 在 live/execution/shadow_store.py——
        # 这是本文件唯一的 live/ import(纯 sqlite 持久化,无网络/无执行能力),
        # 治理测试以精确开洞钉死(其余 live/ 引用仍全面禁止)。
        OutboxSource(
            "shadow",
            SHADOW_OBSERVATIONS_DB,
            lambda path: SqliteShadowStore(path, create=False),
            dispatch_shadow_event_outbox,
        ),
    )


def _snapshot(
    *,
    now_ms: int,
    verdict: str,
    sources: dict,
    projection: dict,
) -> dict:
    return {
        "schema_version": 1,
        "verdict": verdict,
        "generated_at_ms": now_ms,
        "mode": "SINGLE_NODE_OUTBOX_RECOVERY",
        "data_provenance": "VERIFIED_TRANSACTIONAL_OUTBOXES",
        "sources": sources,
        "projection": projection,
        "safety": {
            "domain_state_mutation": False,
            "execution_capability": "NONE",
            "retention_mutation": False,
        },
    }


def drain_domain_event_outboxes(
    *,
    now_ms: int,
    event_path: str = DOMAIN_EVENT_DB,
    projection_path: str = DOMAIN_EVENT_PROJECTION_DB,
    output_path: str = DOMAIN_EVENT_DRAIN_SNAPSHOT,
    sources: Sequence[OutboxSource] | None = None,
    event_bus_factory=None,
    projector=None,
    projection_batch_limit: int = 1_000,
    max_projection_batches: int = 10,
) -> dict:
    """Drain source outboxes, then catch the projection up in bounded batches."""
    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise ValueError("now_ms must be a non-negative integer")
    resolved_sources = (
        default_outbox_sources() if sources is None else tuple(sources)
    )
    if any(not isinstance(source, OutboxSource) for source in resolved_sources):
        raise TypeError("sources must contain OutboxSource values")
    if (
        isinstance(projection_batch_limit, bool)
        or not isinstance(projection_batch_limit, int)
        or projection_batch_limit < 1
    ):
        raise ValueError("projection_batch_limit must be a positive integer")
    if (
        isinstance(max_projection_batches, bool)
        or not isinstance(max_projection_batches, int)
        or max_projection_batches < 1
    ):
        raise ValueError("max_projection_batches must be a positive integer")
    names = [source.name for source in resolved_sources]
    if len(names) != len(set(names)):
        raise ValueError("outbox source names must be unique")
    resolved_factory = event_bus_factory or open_domain_event_bus
    resolved_projector = projector or project_pending_domain_events
    source_results = {
        source.name: {"status": "UNAVAILABLE", "path": source.path}
        for source in resolved_sources
    }
    present = [source for source in resolved_sources if os.path.isfile(source.path)]
    event_authority_exists = os.path.isfile(event_path)

    if not present and not event_authority_exists:
        document = _snapshot(
            now_ms=now_ms,
            verdict="UNAVAILABLE",
            sources=source_results,
            projection={"status": "UNAVAILABLE"},
        )
        atomic_write_json(output_path, document, indent=2)
        return document

    try:
        event_bus = resolved_factory(
            event_path,
            create=not event_authority_exists,
        )
    except Exception as exc:
        for source in present:
            source_results[source.name] = {
                "status": "BLOCKED",
                "path": source.path,
            }
        document = _snapshot(
            now_ms=now_ms,
            verdict="BREACH",
            sources=source_results,
            projection={
                "status": "BREACH",
                "error_type": type(exc).__name__,
            },
        )
        atomic_write_json(output_path, document, indent=2)
        return document
    projection_result: dict
    try:
        for source in resolved_sources:
            if source not in present:
                continue
            store = None
            try:
                store = source.opener(source.path)
                dispatch = source.dispatcher(store, event_bus, at_ms=now_ms)
                source_results[source.name] = {
                    "status": "PASS",
                    "path": source.path,
                    **dict(dispatch),
                }
            except Exception as exc:
                source_results[source.name] = {
                    "status": "BREACH",
                    "error_type": type(exc).__name__,
                    "path": source.path,
                }
            finally:
                if store is not None:
                    close = getattr(store, "close", None)
                    if callable(close):
                        try:
                            close()
                        except Exception as exc:
                            if source_results[source.name]["status"] != "BREACH":
                                source_results[source.name] = {
                                    "status": "BREACH",
                                    "error_type": type(exc).__name__,
                                    "path": source.path,
                                }
        try:
            projected_total = 0
            idempotent_replays_total = 0
            processed_total = 0
            projected_batches = 0
            caught_up = False
            latest_projection: dict = {}
            for _ in range(max_projection_batches):
                latest_projection = dict(
                    resolved_projector(
                        event_bus,
                        now_ms=now_ms,
                        projection_path=projection_path,
                        worker_id="domain-event-outbox-recovery",
                        limit=projection_batch_limit,
                    )
                )
                batch_count = latest_projection.get("projected")
                if (
                    isinstance(batch_count, bool)
                    or not isinstance(batch_count, int)
                    or batch_count < 0
                    or batch_count > projection_batch_limit
                ):
                    raise ValueError("projector returned an invalid projected count")
                replay_count = latest_projection.get("idempotent_replays", 0)
                if (
                    isinstance(replay_count, bool)
                    or not isinstance(replay_count, int)
                    or replay_count < 0
                    or batch_count + replay_count > projection_batch_limit
                ):
                    raise ValueError(
                        "projector returned an invalid idempotent replay count"
                    )
                processed_count = batch_count + replay_count
                projected_total += batch_count
                idempotent_replays_total += replay_count
                processed_total += processed_count
                projected_batches += 1
                if processed_count < projection_batch_limit:
                    caught_up = True
                    break
            projection_result = {
                "status": "PASS",
                **latest_projection,
                "projected": projected_total,
                "idempotent_replays": idempotent_replays_total,
                "processed": processed_total,
                "batches": projected_batches,
                "batch_limit": projection_batch_limit,
                "caught_up": caught_up,
            }
        except Exception as exc:
            projection_result = {
                "status": "BREACH",
                "error_type": type(exc).__name__,
            }
    finally:
        try:
            event_bus.close()
        except Exception as exc:
            projection_result = {
                "status": "BREACH",
                "error_type": type(exc).__name__,
            }

    statuses = [result["status"] for result in source_results.values()]
    statuses.append(projection_result["status"])
    if "BREACH" in statuses:
        verdict = "BREACH"
    elif "UNAVAILABLE" in statuses or "BLOCKED" in statuses:
        verdict = "PARTIAL"
    else:
        verdict = "PASS"
    document = _snapshot(
        now_ms=now_ms,
        verdict=verdict,
        sources=source_results,
        projection=projection_result,
    )
    atomic_write_json(output_path, document, indent=2)
    return document


def main() -> int:
    document = drain_domain_event_outboxes(now_ms=int(time.time() * 1_000))
    print(
        "domain event outbox drain: "
        f"{document['verdict']}; sources={len(document['sources'])}"
    )
    return 1 if document["verdict"] == "BREACH" else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DATA_QUALITY_ADMISSION_DB",
    "DOMAIN_EVENT_DRAIN_SNAPSHOT",
    "OutboxSource",
    "SHADOW_OBSERVATIONS_DB",
    "default_outbox_sources",
    "drain_domain_event_outboxes",
]
