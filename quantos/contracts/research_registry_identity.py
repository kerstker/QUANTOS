"""Original registry hash identities; no persistence or authorization."""
import hashlib
import json
from collections.abc import Mapping


def canonical_hash(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def search_event_hash(
    *,
    search_id: str,
    search_kind: str,
    source: str,
    dataset_version: str,
    status: str,
    recorded_at: int,
    prev_event_hash: str,
) -> str:
    return canonical_hash(
        {
            "dataset_version": dataset_version,
            "prev_event_hash": prev_event_hash,
            "recorded_at": recorded_at,
            "search_id": search_id,
            "search_kind": search_kind,
            "source": source,
            "status": status,
        }
    )


def search_candidate_hash(
    *,
    search_id: str,
    config_hash: str,
    status: str,
    registered_at: int,
    prev_candidate_hash: str,
) -> str:
    return canonical_hash(
        {
            "config_hash": config_hash,
            "prev_candidate_hash": prev_candidate_hash,
            "registered_at": registered_at,
            "search_id": search_id,
            "status": status,
        }
    )


def search_heartbeat_hash(
    *,
    search_id: str,
    recorded_at: int,
    prev_heartbeat_hash: str,
) -> str:
    return canonical_hash(
        {
            "prev_heartbeat_hash": prev_heartbeat_hash,
            "recorded_at": recorded_at,
            "search_id": search_id,
        }
    )


def search_recovery_hash(
    *,
    search_id: str,
    reason: str,
    timeout_ms: int,
    last_activity_at: int,
    recovered_at: int,
    terminal_event_hash: str,
    prev_recovery_hash: str,
) -> str:
    return canonical_hash(
        {
            "last_activity_at": last_activity_at,
            "prev_recovery_hash": prev_recovery_hash,
            "reason": reason,
            "recovered_at": recovered_at,
            "search_id": search_id,
            "terminal_event_hash": terminal_event_hash,
            "timeout_ms": timeout_ms,
        }
    )


def search_identity_hash(
    *,
    search_id: str,
    search_kind: str,
    source: str,
    dataset_version: str,
) -> str:
    """Stable identity derived only from immutable STARTED metadata."""

    return canonical_hash(
        {
            "dataset_version": dataset_version,
            "search_id": search_id,
            "search_kind": search_kind,
            "source": source,
        }
    )


def trial_oos_access_hash(
    *,
    trial_id: str,
    method: str,
    boundary: str,
    viewed_at: int,
    prev_entry_hash: str,
) -> str:
    return canonical_hash(
        {
            "boundary": boundary,
            "method": method,
            "prev_entry_hash": prev_entry_hash,
            "trial_id": trial_id,
            "viewed_at": viewed_at,
        }
    )


def search_evaluation_hash(
    *,
    search_id: str,
    config_hash: str,
    run_id: str,
    recorded_at: int,
    evaluation_content_hash: str,
    prev_link_hash: str,
) -> str:
    return canonical_hash(
        {
            "config_hash": config_hash,
            "evaluation_content_hash": evaluation_content_hash,
            "prev_link_hash": prev_link_hash,
            "recorded_at": recorded_at,
            "run_id": run_id,
            "search_id": search_id,
        }
    )
