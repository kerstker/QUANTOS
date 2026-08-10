"""Diagnostic-only worker for event-gated liquidation hypotheses.

This worker deliberately does not use the global experiment registry or any
promotion path.  It freezes and records exactly two five-minute mechanisms,
then evaluates them only on retained public PIT frames.  Before 30 days the
output is exploratory; after 30 days it still requires the separate formal
DSR/PBO/walk-forward/OOS workflow before any forward admission.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:  # pragma: no cover - direct CLI compatibility
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantos.contracts.hashing import stable_hash
from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SAFE_BLOCKED,
    SCHEDULE_EXIT_SUCCESS,
)
from quantos.runtime_paths import runtime_state_root
from services.features.binance_realtime_projection import FEATURE_VERSION
from services.research.alpha_family_registry import AlphaFamilyRegistry
from services.research.liquidation_events import (
    LIQUIDATION_EVENT_LABEL_POLICY,
    LiquidationEventSpec,
    default_liquidation_event_specs,
    evaluate_liquidation_event_spec,
)
from services.research.realtime_alpha_discovery import (
    DAY_MS,
    DataAdmissionReport,
    RealtimeAlphaPolicy,
    RealtimeFrame,
    assess_data_admission,
)
from tools.cache_io import atomic_write_json
from tools.realtime_alpha_discovery import (
    coverage_from_store,
    load_bucketed_frames,
    realtime_frame_view_hash,
)


ROOT = runtime_state_root()
DEFAULT_SOURCE_STORE = ROOT / "data" / "cache" / "binance_realtime_features.db"
DEFAULT_FEATURE_STATUS = (
    ROOT / "data" / "cache" / "binance_realtime_feature_status.json"
)
DEFAULT_FRAME_CACHE = ROOT / "data" / "cache" / "realtime_decision_frames.db"
DEFAULT_REGISTRY_DB = ROOT / "data" / "cache" / "realtime_alpha_registry.db"
DEFAULT_OUTPUT = ROOT / "data" / "cache" / "liquidation_event_research.json"
MAX_LOOKBACK_MS = 90 * DAY_MS


class LiquidationEventWorkerError(RuntimeError):
    """The diagnostic input or evidence boundary is invalid."""


def _read_quality_status(path: str | Path) -> tuple[str, dict[str, Any]]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "UNAVAILABLE", {}
    if not isinstance(payload, dict):
        return "UNAVAILABLE", {}
    return str(payload.get("status", "UNAVAILABLE")).upper(), payload


def _policy_payload(policy: RealtimeAlphaPolicy) -> dict[str, object]:
    return {
        "execution_latency_ms": policy.execution_latency_ms,
        "max_label_lateness_ms": policy.max_label_lateness_ms,
        "min_cross_section": policy.min_cross_section,
        "fee_bps": policy.fee_bps,
        "slippage_bps": policy.slippage_bps,
        "horizon_policy": "EXACTLY_5M_NO_GRID",
        "activation_policy": "STRICTLY_POSITIVE_AND_NEGATIVE_NONZERO_SIDES",
        "position_policy": "INDEPENDENT_EVENT_ROUND_TRIP_THEN_FLAT",
        "funding_policy": "NO_PRORATED_FUNDING_WITHOUT_SETTLEMENT_ATTESTATION",
    }


def _candidate_payload(spec: LiquidationEventSpec) -> dict[str, object]:
    identity = {
        **asdict(spec),
        "family_id": "liquidation_event",
        "required_features": [
            "rt_mid_price",
            "rt_spread_bps",
            "rt_liquidation_imbalance_5s",
        ],
        "signal_version": "liquidation-event-gated.v1",
        "label_policy": LIQUIDATION_EVENT_LABEL_POLICY,
    }
    return {
        **identity,
        "config_hash": stable_hash(identity),
        "intended_frequency": "event_driven_5m",
        "evaluation_frequency": "realtime_pit_30s_buckets",
        "paper_eligible": False,
        "notes": (
            "Exploratory public-data event research; 30d formal validation and "
            "all unchanged statistical/risk/forward gates remain mandatory"
        ),
    }


def _activation_diagnostics(
    frames: Mapping[str, Sequence[RealtimeFrame]], *, min_cross_section: int
) -> dict[str, object]:
    buckets: dict[int, list[float]] = {}
    for rows in frames.values():
        for frame in rows:
            buckets.setdefault(frame.decision_time_ms, []).append(
                frame.liquidation_imbalance_5s
            )
    eligible = [values for values in buckets.values() if len(values) >= min_cross_section]
    all_zero = sum(all(abs(value) <= 1e-12 for value in values) for values in eligible)
    two_sided = sum(
        any(value > 1e-12 for value in values)
        and any(value < -1e-12 for value in values)
        for values in eligible
    )
    one_sided = len(eligible) - all_zero - two_sided
    return {
        "eligible_buckets": len(eligible),
        "all_zero_buckets": all_zero,
        "one_sided_event_buckets": one_sided,
        "two_sided_event_buckets": two_sided,
        "all_zero_fraction": all_zero / len(eligible) if eligible else None,
        "two_sided_fraction": two_sided / len(eligible) if eligible else None,
    }


def build_report(
    frames: Mapping[str, Sequence[RealtimeFrame]],
    *,
    admission: DataAdmissionReport,
    dataset_version: str,
    generated_at_ms: int,
    policy: RealtimeAlphaPolicy | None = None,
) -> dict[str, object]:
    """Build a truthful diagnostic report without granting formal authority."""
    policy = policy or RealtimeAlphaPolicy()
    specs = default_liquidation_event_specs()
    evaluations = (
        [
            evaluate_liquidation_event_spec(spec, frames, policy=policy).as_dict()
            for spec in specs
        ]
        if admission.exploration_allowed
        else []
    )
    if not admission.exploration_allowed:
        status = admission.status
        blocking_reasons = list(admission.reasons)
    elif not admission.formal_validation_allowed:
        status = "EXPLORATION_ONLY"
        blocking_reasons = ["REALTIME_HISTORY_BELOW_30D"]
    else:
        status = "FORMAL_VALIDATION_REQUIRED"
        blocking_reasons = ["DSR_PBO_WALK_FORWARD_AND_OOS_NOT_RUN"]
    return {
        "schema_version": 1,
        "generated_at_ms": int(generated_at_ms),
        "status": status,
        "dataset_version": dataset_version,
        "data_admission": admission.as_dict(),
        "policy": _policy_payload(policy),
        "activation": _activation_diagnostics(
            frames, min_cross_section=policy.min_cross_section
        ),
        "evaluations": evaluations,
        "formal_trial_registered": False,
        "forward_eligible": False,
        "testnet_eligible": False,
        "blocking_reasons": blocking_reasons,
        "safety": {
            "diagnostic_only": True,
            "credentials_accessed": False,
            "broker_connectivity": False,
            "order_submission": False,
            "mainnet": False,
        },
    }


def run_cycle(
    *,
    source_store: str | Path = DEFAULT_SOURCE_STORE,
    feature_status: str | Path = DEFAULT_FEATURE_STATUS,
    frame_cache_path: str | Path = DEFAULT_FRAME_CACHE,
    registry_db: str | Path = DEFAULT_REGISTRY_DB,
    output_path: str | Path = DEFAULT_OUTPUT,
    now_ms: int | None = None,
    policy: RealtimeAlphaPolicy | None = None,
) -> dict[str, object]:
    now = int(time.time() * 1_000) if now_ms is None else int(now_ms)
    policy = policy or RealtimeAlphaPolicy()
    quality_status, quality_payload = _read_quality_status(feature_status)
    coverage = coverage_from_store(source_store)
    admission = assess_data_admission(
        coverage,
        now_ms=now,
        quality_status=quality_status,
        policy=policy,
    )
    frames: dict[str, tuple[RealtimeFrame, ...]] = {}
    if admission.exploration_allowed:
        if admission.common_start_ms is None or admission.common_end_ms is None:
            raise LiquidationEventWorkerError("exploration admission lacks a time range")
        start_ms = max(
            admission.common_start_ms,
            admission.common_end_ms - MAX_LOOKBACK_MS,
        )
        frames = load_bucketed_frames(
            source_store,
            instruments=admission.common_instruments,
            start_ms=start_ms,
            end_ms=admission.common_end_ms,
            bucket_ms=policy.decision_interval_ms,
            frame_cache_path=frame_cache_path,
            max_cache_lag_ms=policy.max_feature_age_ms,
        )
    frame_hash = realtime_frame_view_hash(frames)
    specs = default_liquidation_event_specs()
    dataset_version = stable_hash(
        {
            "feature_version": FEATURE_VERSION,
            "quality_status_hash": stable_hash(quality_payload),
            "coverage": {
                feature_id: {
                    "rows": item.row_count,
                    "instruments": sorted(item.instruments),
                    "first": item.first_event_ms,
                    "last": item.last_event_ms,
                }
                for feature_id, item in sorted(coverage.items())
            },
            "frame_view_hash": frame_hash,
            "specs": [asdict(spec) for spec in specs],
            "policy": _policy_payload(policy),
        }
    )
    report = build_report(
        frames,
        admission=admission,
        dataset_version=dataset_version,
        generated_at_ms=now,
        policy=policy,
    )

    with AlphaFamilyRegistry(str(registry_db), create=True) as registry:
        for spec in specs:
            registry.register_candidate(_candidate_payload(spec), recorded_at=now)
        evaluations = {
            str(item["candidate_id"]): item
            for item in report["evaluations"]
            if isinstance(item, Mapping)
        }
        for spec in specs:
            registry.record_evaluation(
                spec.candidate_id,
                {
                    "mode": str(report["status"]),
                    "formal_trial_registered": False,
                    "forward_eligible": False,
                    "blocking_reasons": list(report["blocking_reasons"]),
                    "result": evaluations.get(spec.candidate_id),
                },
                dataset_version=dataset_version,
                recorded_at=now,
            )
        report["registry"] = {
            "authority_hash": registry.authority_hash(),
            "event_count": registry.event_count(),
        }

    resolved_output = Path(output_path)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(str(resolved_output), report, indent=2)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-store", default=str(DEFAULT_SOURCE_STORE))
    parser.add_argument("--feature-status", default=str(DEFAULT_FEATURE_STATUS))
    parser.add_argument("--frame-cache", default=str(DEFAULT_FRAME_CACHE))
    parser.add_argument("--registry-db", default=str(DEFAULT_REGISTRY_DB))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    try:
        report = run_cycle(
            source_store=args.source_store,
            feature_status=args.feature_status,
            frame_cache_path=args.frame_cache,
            registry_db=args.registry_db,
            output_path=args.output,
        )
    except Exception as exc:
        print(f"liquidation event research failed: {type(exc).__name__}: {exc}")
        return SCHEDULE_EXIT_FAILURE
    print(
        "liquidation event research "
        f"status={report['status']} "
        f"two_sided={report['activation']['two_sided_event_buckets']} "
        f"evaluated={len(report['evaluations'])}"
    )
    if report["status"] not in {"EXPLORATION_ONLY", "FORMAL_VALIDATION_REQUIRED"}:
        return SCHEDULE_EXIT_SAFE_BLOCKED
    return SCHEDULE_EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_OUTPUT",
    "LiquidationEventWorkerError",
    "build_report",
    "main",
    "run_cycle",
]
