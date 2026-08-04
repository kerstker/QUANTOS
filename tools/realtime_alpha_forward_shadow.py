"""Advance evaluated realtime candidates through isolated engineering paper.

Paper observation starts as soon as the three-day exploration floor produces
an evaluated candidate.  Thirty-day formal validation and ninety-day maturity
remain independent graduation gates; this ledger never earns forward-evidence,
promotion, deployment, or live credit.  Each decision is append-only and is
recorded before its post-latency outcome can exist.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
from contextlib import ExitStack
import json
import math
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

from quantos.contracts.hashing import stable_hash
from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SAFE_BLOCKED,
    SCHEDULE_EXIT_SUCCESS,
)
from quantos.runtime_paths import runtime_state_root
from services.research.alpha_family_registry import AlphaFamilyRegistry
from services.research.realtime_alpha_discovery import (
    RealtimeAlphaPolicy,
    RealtimeFrame,
    RealtimeHypothesis,
    build_forward_paper_decision,
    default_realtime_hypotheses,
    realize_forward_paper_decision,
)
from services.research.realtime_alpha_shadow_store import RealtimeAlphaShadowStore
from tools.cache_io import atomic_write_json
from tools.realtime_alpha_discovery import load_bucketed_frames


ROOT = runtime_state_root()
DEFAULT_DISCOVERY = ROOT / "data" / "cache" / "realtime_alpha_discovery.json"
DEFAULT_FEATURE_STATUS = ROOT / "data" / "cache" / "binance_realtime_feature_status.json"
DEFAULT_FEATURE_STORE = ROOT / "data" / "cache" / "binance_realtime_features.db"
DEFAULT_FAMILY_REGISTRY = ROOT / "data" / "cache" / "realtime_alpha_registry.db"
DEFAULT_SHADOW_STORE = ROOT / "data" / "cache" / "realtime_alpha_forward_shadow.db"
DEFAULT_OUTPUT = ROOT / "data" / "cache" / "realtime_alpha_forward_shadow.json"
MAX_DISCOVERY_AGE_MS = 3 * 60 * 60_000
MAX_FEATURE_STATUS_AGE_MS = 15 * 60_000
RECENT_FRAME_LOOKBACK_MS = 3 * 60_000

_SAFE_DISCOVERY_BOUNDARY = {
    "paper_only": True,
    "live_trading_enabled": False,
    "real_broker_enabled": False,
    "allow_order_submission": False,
    "allow_ai_direct_trading": False,
    "promotion_eligible": False,
    "credentials_accessed": False,
}


class RealtimeAlphaForwardShadowError(RuntimeError):
    """Forward shadow evidence is missing, stale, unsafe, or conflicted."""


def _read_object(path: str | Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RealtimeAlphaForwardShadowError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise RealtimeAlphaForwardShadowError(f"{label} is not an object")
    return value


def eligible_candidates(
    discovery: Mapping[str, Any],
    *,
    now_ms: int,
) -> tuple[tuple[RealtimeHypothesis, Mapping[str, Any]], ...]:
    """Verify the autonomous handoff and return only exact manifest matches."""
    eligible, _blockers = _handoff_state(discovery, now_ms=now_ms)
    return eligible


def _handoff_state(
    discovery: Mapping[str, Any],
    *,
    now_ms: int,
) -> tuple[
    tuple[tuple[RealtimeHypothesis, Mapping[str, Any]], ...],
    tuple[str, ...],
]:
    hard_blockers: list[str] = []
    generated = discovery.get("generated_at_ms")
    if (
        type(generated) is not int
        or generated < 0
        or not 0 <= now_ms - generated <= MAX_DISCOVERY_AGE_MS
    ):
        hard_blockers.append("DISCOVERY_SNAPSHOT_STALE_OR_INVALID")
    if discovery.get("mode") != "L2_AUTONOMOUS_REALTIME_ALPHA_RESEARCH":
        hard_blockers.append("DISCOVERY_MODE_INVALID")
    safety = discovery.get("safety")
    if not isinstance(safety, Mapping) or any(
        safety.get(field) is not expected
        for field, expected in _SAFE_DISCOVERY_BOUNDARY.items()
    ):
        hard_blockers.append("DISCOVERY_SAFETY_BOUNDARY_INVALID")
    registry = discovery.get("registry")
    if not isinstance(registry, Mapping) or registry.get("chain_valid") is not True:
        hard_blockers.append("DISCOVERY_REGISTRY_CHAIN_INVALID")

    hypotheses = default_realtime_hypotheses()
    manifest = {item.candidate_id: item for item in hypotheses}
    expected_manifest_hash = stable_hash([item.as_dict() for item in hypotheses])
    if discovery.get("manifest_hash") != expected_manifest_hash:
        hard_blockers.append("DISCOVERY_MANIFEST_HASH_INVALID")

    rows = discovery.get("candidates")
    eligible: list[tuple[RealtimeHypothesis, Mapping[str, Any]]] = []
    seen: set[str] = set()
    if not isinstance(rows, list):
        hard_blockers.append("DISCOVERY_CANDIDATES_MISSING")
    else:
        for row in rows:
            if not isinstance(row, Mapping):
                hard_blockers.append("DISCOVERY_CANDIDATE_ROW_INVALID")
                continue
            candidate_id = str(row.get("candidate_id", ""))
            spec = manifest.get(candidate_id)
            if not candidate_id or candidate_id in seen:
                hard_blockers.append("DISCOVERY_CANDIDATE_ID_INVALID")
                continue
            seen.add(candidate_id)
            if (
                spec is None
                or row.get("family_id") != spec.family_id
                or row.get("mechanism") != spec.mechanism
                or row.get("horizon_ms") != spec.horizon_ms
            ):
                hard_blockers.append("DISCOVERY_CANDIDATE_MANIFEST_MISMATCH")
                continue
            gate_pass = row.get("gate_pass") is True
            formal_status = row.get("status") in {
                "SHADOW_CANDIDATE_READY",
                "VALIDATED_AWAITING_90D_SHADOW_MATURITY",
            }
            if gate_pass != formal_status:
                hard_blockers.append("DISCOVERY_CANDIDATE_GATE_STATUS_CONFLICT")
                continue
            if row.get("evaluation_status") != "EVALUATED":
                continue
            if row.get("promotion_eligible") is not False or row.get("live_allowed") is not False:
                hard_blockers.append("DISCOVERY_CANDIDATE_AUTHORITY_INVALID")
                continue
            eligible.append((spec, row))

    if hard_blockers:
        raise RealtimeAlphaForwardShadowError(
            "invalid realtime discovery handoff: "
            + ",".join(dict.fromkeys(hard_blockers))
        )

    admission_blockers: list[str] = []
    admission = discovery.get("data_admission")
    if not isinstance(admission, Mapping) or admission.get("exploration_allowed") is not True:
        admission_blockers.append("THREE_DAY_EXPLORATION_NOT_MET")
    capacity = discovery.get("executable_depth_capacity")
    shadow_aum = capacity.get("shadow_aum_usd") if isinstance(capacity, Mapping) else None
    if (
        not isinstance(shadow_aum, (int, float))
        or isinstance(shadow_aum, bool)
        or not math.isfinite(float(shadow_aum))
        or float(shadow_aum) <= 0
    ):
        admission_blockers.append("POSITIVE_CONSERVATIVE_SHADOW_SIZE_UNAVAILABLE")
    if admission_blockers:
        return (), tuple(admission_blockers)
    if not eligible:
        admission_blockers.append("NO_EVALUATED_CANDIDATE")
    return (
        tuple(sorted(eligible, key=lambda item: item[0].candidate_id)),
        tuple(admission_blockers),
    )


def _feature_status_ready(path: str | Path, *, now_ms: int) -> tuple[bool, dict[str, Any]]:
    try:
        status = _read_object(path, "realtime feature status")
    except RealtimeAlphaForwardShadowError:
        return False, {}
    generated = status.get("generated_at_ms")
    ready = bool(
        status.get("status") == "PASS"
        and type(generated) is int
        and 0 <= now_ms - generated <= MAX_FEATURE_STATUS_AGE_MS
        and isinstance(status.get("batch"), Mapping)
        and status["batch"].get("chain_valid") is True
    )
    return ready, status


def _common_latest_frames(
    frames_by_instrument: Mapping[str, Sequence[RealtimeFrame]],
    *,
    now_ms: int,
) -> list[RealtimeFrame]:
    if not frames_by_instrument:
        return []
    common: set[int] | None = None
    for frames in frames_by_instrument.values():
        times = {item.decision_time_ms for item in frames if item.decision_time_ms <= now_ms}
        common = times if common is None else common & times
    if not common:
        return []
    selected = max(common)
    result = []
    for instrument, frames in sorted(frames_by_instrument.items()):
        match = next((item for item in frames if item.decision_time_ms == selected), None)
        if match is not None and match.instrument_id == instrument:
            result.append(match)
    return result


def _future_marks(
    decision: Mapping[str, Any],
    frames_by_instrument: Mapping[str, Sequence[RealtimeFrame]],
    *,
    as_of_ms: int,
) -> dict[str, RealtimeFrame]:
    target = int(decision["target_time_ms"])
    lateness = int(decision["max_label_lateness_ms"])
    result: dict[str, RealtimeFrame] = {}
    for instrument in decision.get("weights", {}):
        frames = sorted(
            frames_by_instrument.get(str(instrument), ()),
            key=lambda item: item.decision_time_ms,
        )
        times = [item.decision_time_ms for item in frames]
        index = bisect_left(times, target)
        if (
            index >= len(frames)
            or frames[index].decision_time_ms > as_of_ms
            or frames[index].decision_time_ms - target > lateness
        ):
            return {}
        result[str(instrument)] = frames[index]
    return result


def _admission_identity(
    spec: RealtimeHypothesis,
    evaluation: Mapping[str, Any],
    discovery: Mapping[str, Any],
    policy: RealtimeAlphaPolicy,
) -> dict[str, Any]:
    admission = discovery.get("data_admission")
    capacity = discovery.get("executable_depth_capacity")
    if not isinstance(admission, Mapping) or not isinstance(capacity, Mapping):
        raise RealtimeAlphaForwardShadowError("admission inputs are missing")
    common_instruments = admission.get("common_instruments")
    shadow_aum = capacity.get("shadow_aum_usd")
    if (
        not isinstance(common_instruments, list)
        or len(common_instruments) < policy.min_cross_section
        or any(not isinstance(item, str) or not item for item in common_instruments)
        or not isinstance(shadow_aum, (int, float))
        or isinstance(shadow_aum, bool)
        or not math.isfinite(float(shadow_aum))
        or float(shadow_aum) <= 0
    ):
        raise RealtimeAlphaForwardShadowError("admission sizing/universe is invalid")
    policy_payload = dict(vars(policy))
    return {
        "candidate_id": spec.candidate_id,
        "family_id": spec.family_id,
        "mechanism": spec.mechanism,
        "horizon_ms": spec.horizon_ms,
        "signal_version": spec.signal_version,
        "manifest_hash": discovery.get("manifest_hash"),
        "dataset_version": discovery.get("dataset_version"),
        "source_search_id": discovery.get("search_id"),
        "common_instruments": sorted(set(common_instruments)),
        "shadow_aum_usd": float(shadow_aum),
        "formal_validation_passed_at_admission": evaluation.get("gate_pass") is True,
        "ninety_day_history_mature_at_admission": (
            admission.get("shadow_admission_allowed") is True
        ),
        "policy": policy_payload,
        "policy_hash": stable_hash(policy_payload),
        "paper_class": "AUTONOMOUS_REALTIME_ENGINEERING_SHADOW",
    }


def _admitted_spec(
    admission: Mapping[str, Any],
    *,
    policy: RealtimeAlphaPolicy,
) -> tuple[RealtimeHypothesis, Mapping[str, Any]]:
    payload = admission.get("payload")
    if not isinstance(payload, Mapping) or any(
        payload.get(field) is not expected
        for field, expected in {
            "paper_only": True,
            "promotion_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        }.items()
    ):
        raise RealtimeAlphaForwardShadowError("stored admission safety boundary is invalid")
    identity = payload.get("identity")
    if not isinstance(identity, Mapping):
        raise RealtimeAlphaForwardShadowError("stored admission identity is missing")
    candidate_id = str(identity.get("candidate_id", ""))
    manifest = {item.candidate_id: item for item in default_realtime_hypotheses()}
    spec = manifest.get(candidate_id)
    if (
        spec is None
        or identity.get("family_id") != spec.family_id
        or identity.get("mechanism") != spec.mechanism
        or identity.get("horizon_ms") != spec.horizon_ms
        or identity.get("signal_version") != spec.signal_version
        or identity.get("paper_class") != "AUTONOMOUS_REALTIME_ENGINEERING_SHADOW"
        or identity.get("policy_hash") != stable_hash(dict(vars(policy)))
    ):
        raise RealtimeAlphaForwardShadowError("stored admission identity is incompatible")
    instruments = identity.get("common_instruments")
    if (
        not isinstance(instruments, list)
        or len(instruments) < policy.min_cross_section
        or any(not isinstance(item, str) or not item for item in instruments)
    ):
        raise RealtimeAlphaForwardShadowError("stored admission universe is invalid")
    return spec, identity


def run_cycle(
    *,
    discovery_path: str | Path = DEFAULT_DISCOVERY,
    feature_status_path: str | Path = DEFAULT_FEATURE_STATUS,
    feature_store_path: str | Path = DEFAULT_FEATURE_STORE,
    family_registry_path: str | Path = DEFAULT_FAMILY_REGISTRY,
    shadow_store_path: str | Path = DEFAULT_SHADOW_STORE,
    output_path: str | Path = DEFAULT_OUTPUT,
    now_ms: int | None = None,
    policy: RealtimeAlphaPolicy | None = None,
) -> dict[str, Any]:
    now = int(time.time() * 1_000) if now_ms is None else int(now_ms)
    policy = policy or RealtimeAlphaPolicy()
    discovery = _read_object(discovery_path, "realtime discovery snapshot")
    handoff_valid = True
    handoff_error: str | None = None
    try:
        eligible, admission_blockers = _handoff_state(discovery, now_ms=now)
    except RealtimeAlphaForwardShadowError as exc:
        eligible = ()
        admission_blockers = ()
        handoff_valid = False
        handoff_error = str(exc)
    errors: dict[str, str] = {}
    new_admissions = 0
    new_decisions = 0
    new_realizations = 0
    tracks: dict[str, dict[str, Any]] = {}
    feature_ready, feature_status = _feature_status_ready(feature_status_path, now_ms=now)
    shadow_path = Path(shadow_store_path)
    chain_valid: bool | None = True if handoff_valid else None
    event_count: int | None = 0 if handoff_valid else None
    admitted_candidates: tuple[str, ...] = ()

    if handoff_valid and (eligible or shadow_path.exists()):
        try:
            with ExitStack() as stack:
                family_registry: AlphaFamilyRegistry | None = None
                if eligible:
                    family_registry = stack.enter_context(
                        AlphaFamilyRegistry(str(family_registry_path), create=False)
                    )
                    if not family_registry.verify_chain():
                        raise RealtimeAlphaForwardShadowError(
                            "family registry chain is invalid"
                        )
                store = stack.enter_context(
                    RealtimeAlphaShadowStore(
                        str(shadow_store_path), create=not shadow_path.exists()
                    )
                )
                if not store.chain_valid():
                    raise RealtimeAlphaForwardShadowError("shadow ledger chain is invalid")

                for spec, row in eligible:
                    inserted = store.admit(
                        spec.candidate_id,
                        _admission_identity(spec, row, discovery, policy),
                        admitted_at_ms=now,
                    )
                    new_admissions += int(inserted)

                admitted_candidates = store.admitted_candidate_ids()
                if admitted_candidates and family_registry is None:
                    family_registry = stack.enter_context(
                        AlphaFamilyRegistry(str(family_registry_path), create=False)
                    )
                    if not family_registry.verify_chain():
                        raise RealtimeAlphaForwardShadowError(
                            "family registry chain is invalid"
                        )

                for candidate_id in admitted_candidates:
                    try:
                        admission = store.admission(candidate_id)
                        assert admission is not None
                        spec, identity = _admitted_spec(admission, policy=policy)

                        if feature_ready:
                            for event in store.pending(candidate_id):
                                decision = event["payload"]
                                target = int(decision["target_time_ms"])
                                lateness = int(decision["max_label_lateness_ms"])
                                if now < target:
                                    continue
                                future_series = load_bucketed_frames(
                                    feature_store_path,
                                    instruments=tuple(decision.get("weights", {})),
                                    start_ms=max(0, target - policy.decision_interval_ms),
                                    end_ms=min(now, target + lateness),
                                    bucket_ms=policy.decision_interval_ms,
                                )
                                future = _future_marks(
                                    decision, future_series, as_of_ms=now
                                )
                                if not future:
                                    continue
                                realization = realize_forward_paper_decision(
                                    decision, future
                                )
                                realization["dataset_version"] = decision.get(
                                    "dataset_version"
                                )
                                inserted = store.record_realization(
                                    candidate_id,
                                    realization,
                                    recorded_at_ms=now,
                                )
                                new_realizations += int(inserted)

                        if feature_ready and not store.pending(candidate_id):
                            recent = load_bucketed_frames(
                                feature_store_path,
                                instruments=tuple(identity["common_instruments"]),
                                start_ms=max(0, now - RECENT_FRAME_LOOKBACK_MS),
                                end_ms=now,
                                bucket_ms=policy.decision_interval_ms,
                            )
                            frames = _common_latest_frames(recent, now_ms=now)
                            admitted_at = int(admission["payload"]["admitted_at_ms"])
                            prior_decisions = store.decisions(candidate_id)
                            last_decision = (
                                int(prior_decisions[-1]["identity_time_ms"])
                                if prior_decisions
                                else None
                            )
                            decision_time = frames[0].decision_time_ms if frames else None
                            due = bool(
                                decision_time is not None
                                and decision_time > admitted_at
                                and (
                                    last_decision is None
                                    or decision_time >= last_decision + spec.horizon_ms
                                )
                            )
                            if due:
                                decision = build_forward_paper_decision(
                                    spec,
                                    frames,
                                    previous_weights=store.latest_weights(candidate_id),
                                    policy=policy,
                                )
                                decision["dataset_version"] = identity.get(
                                    "dataset_version"
                                )
                                decision["source_search_id"] = identity.get(
                                    "source_search_id"
                                )
                                decision["shadow_aum_usd"] = identity.get(
                                    "shadow_aum_usd"
                                )
                                decision["decision_evidence_hash"] = stable_hash(decision)
                                inserted = store.record_decision(
                                    candidate_id,
                                    decision,
                                    recorded_at_ms=now,
                                )
                                new_decisions += int(inserted)

                        assert family_registry is not None
                        for realization_event in store.realizations(candidate_id):
                            realization = realization_event["payload"]
                            decision_time = int(realization["decision_time_ms"])
                            family_registry.record_paper_run(
                                candidate_id,
                                run_id=f"realtime-engineering:{candidate_id}:{decision_time}",
                                status="ENGINEERING_SHADOW_REALIZED",
                                metrics=realization,
                                dataset_version=str(
                                    realization.get("dataset_version")
                                    or identity.get("dataset_version")
                                ),
                                recorded_at=int(realization_event["recorded_at_ms"]),
                            )
                        tracks[candidate_id] = store.summary(candidate_id)
                    except Exception as exc:
                        errors[candidate_id] = (
                            f"{type(exc).__name__}: {str(exc)[:200]}"
                        )
                chain_valid = store.chain_valid()
                event_count = store.event_count()
        except Exception as exc:
            errors["_system"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            chain_valid = False

    running = bool(
        handoff_valid
        and admitted_candidates
        and feature_ready
        and chain_valid is True
        and not errors
    )
    if not handoff_valid or errors or (
        (eligible or admitted_candidates) and not feature_ready
    ):
        status = "SAFE_BLOCKED"
    elif running:
        status = "RUNNING"
    else:
        status = "WAITING_FOR_EVALUATED_CANDIDATE"
    report = {
        "schema_version": 1,
        "generated_at_ms": now,
        "status": status,
        "mode": "AUTONOMOUS_REALTIME_ENGINEERING_SHADOW",
        "eligible_candidates": [spec.candidate_id for spec, _ in eligible],
        "admitted_candidates": list(admitted_candidates),
        "handoff": {
            "valid": handoff_valid,
            "error": handoff_error,
            "admission_blockers": list(admission_blockers),
            "graduation_blockers": [
                blocker
                for blocker, blocked in (
                    (
                        "THIRTY_DAY_FORMAL_VALIDATION_REQUIRED",
                        not any(row.get("gate_pass") is True for _spec, row in eligible),
                    ),
                    (
                        "NINETY_DAY_HISTORY_MATURITY_REQUIRED",
                        not (
                            isinstance(discovery.get("data_admission"), Mapping)
                            and discovery["data_admission"].get("shadow_admission_allowed") is True
                        ),
                    ),
                )
                if blocked
            ],
        },
        "feature_input": {
            "ready": feature_ready,
            "status": feature_status.get("status"),
            "generated_at_ms": feature_status.get("generated_at_ms"),
        },
        "cycle": {
            "new_admissions": new_admissions,
            "new_decisions": new_decisions,
            "new_realizations": new_realizations,
        },
        "tracks": tracks,
        "errors": errors,
        "ledger": {
            "path": str(Path(shadow_store_path)),
            "append_only": True,
            "chain_valid": chain_valid,
            "event_count": event_count,
        },
        "verdict": {
            "forward_shadow_running": running,
            "engineering_shadow_running": running,
            "paper_observation_started_without_maturity_wait": running,
            "promotion_eligible": False,
            "forward_evidence_credit": False,
            "deployment_eligible": False,
            "live_allowed": False,
            "order_submission_allowed": False,
        },
        "safety": {
            "paper_only": True,
            "public_market_data_only": True,
            "separate_from_official_paper": True,
            "credentials_accessed": False,
            "broker_connectivity": False,
            "broker_order_submission": False,
            "allow_ai_direct_trading": False,
        },
    }
    atomic_write_json(str(output_path), report, indent=2)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery", default=str(DEFAULT_DISCOVERY))
    parser.add_argument("--feature-status", default=str(DEFAULT_FEATURE_STATUS))
    parser.add_argument("--feature-store", default=str(DEFAULT_FEATURE_STORE))
    parser.add_argument("--family-registry", default=str(DEFAULT_FAMILY_REGISTRY))
    parser.add_argument("--shadow-store", default=str(DEFAULT_SHADOW_STORE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    try:
        report = run_cycle(
            discovery_path=args.discovery,
            feature_status_path=args.feature_status,
            feature_store_path=args.feature_store,
            family_registry_path=args.family_registry,
            shadow_store_path=args.shadow_store,
            output_path=args.output,
        )
    except Exception as exc:
        print(f"realtime forward shadow failed: {type(exc).__name__}: {exc}")
        return SCHEDULE_EXIT_FAILURE
    print(
        "realtime forward shadow "
        f"status={report['status']} eligible={len(report['eligible_candidates'])} "
        f"decisions={report['cycle']['new_decisions']} "
        f"realizations={report['cycle']['new_realizations']}"
    )
    return SCHEDULE_EXIT_SUCCESS if report["verdict"]["forward_shadow_running"] else SCHEDULE_EXIT_SAFE_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_OUTPUT",
    "DEFAULT_SHADOW_STORE",
    "RealtimeAlphaForwardShadowError",
    "eligible_candidates",
    "main",
    "run_cycle",
]
