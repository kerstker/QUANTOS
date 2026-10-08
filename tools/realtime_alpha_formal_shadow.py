"""Advance realtime candidates through an independent formal shadow authority.

This worker deliberately reuses the deterministic frame/label implementation
of ``realtime_alpha_forward_shadow`` while writing a physically separate
append-only ledger and artifact.  The formal ledger is never read by the
adaptive feedback selector; its outcomes therefore cannot influence the
candidate family or mutation allocation that follows.

The authority is still L2 paper evidence.  It cannot promote a candidate,
access credentials, connect to a broker, or submit an order.  ``MATURE`` is
assigned only by the canonical workflow after its independent evidence
requirements are satisfied; this worker merely accumulates the blinded track.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Mapping, Sequence

from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_SAFE_BLOCKED,
    SCHEDULE_EXIT_SUCCESS,
)
from quantos.runtime_paths import runtime_state_root
from services.research.realtime_alpha_discovery import RealtimeAlphaPolicy
from tools.realtime_alpha_forward_shadow import (
    DEFAULT_DISCOVERY,
    DEFAULT_FAMILY_REGISTRY,
    DEFAULT_FEATURE_STATUS,
    DEFAULT_FEATURE_STORE,
    FORMAL_BLINDED_SHADOW_AUTHORITY,
    FORMAL_BLINDED_SHADOW_PAPER_CLASS,
    RealtimeAlphaForwardShadowError,
    release_run_attestation_for_authority,
    run_cycle as _run_cycle,
)


ROOT = runtime_state_root()
DEFAULT_FORMAL_SHADOW_STORE = ROOT / "data" / "cache" / "realtime_alpha_formal_shadow.db"
DEFAULT_FORMAL_OUTPUT = ROOT / "data" / "cache" / "realtime_alpha_formal_shadow.json"


def release_run_attestation() -> dict[str, Any] | None:
    """Return the latest detached formal-shadow release proof."""
    return release_run_attestation_for_authority(FORMAL_BLINDED_SHADOW_AUTHORITY)


def run_cycle(
    *,
    discovery_path: str | Path = DEFAULT_DISCOVERY,
    feature_status_path: str | Path = DEFAULT_FEATURE_STATUS,
    feature_store_path: str | Path = DEFAULT_FEATURE_STORE,
    family_registry_path: str | Path = DEFAULT_FAMILY_REGISTRY,
    shadow_store_path: str | Path = DEFAULT_FORMAL_SHADOW_STORE,
    output_path: str | Path = DEFAULT_FORMAL_OUTPUT,
    now_ms: int | None = None,
    policy: RealtimeAlphaPolicy | None = None,
) -> dict[str, Any]:
    report = _run_cycle(
        discovery_path=discovery_path,
        feature_status_path=feature_status_path,
        feature_store_path=feature_store_path,
        family_registry_path=family_registry_path,
        shadow_store_path=shadow_store_path,
        output_path=output_path,
        now_ms=now_ms,
        policy=policy,
        authority=FORMAL_BLINDED_SHADOW_AUTHORITY,
    )
    authority = report.get("authority")
    if not isinstance(authority, Mapping) or (
        authority.get("name") != FORMAL_BLINDED_SHADOW_AUTHORITY
        or authority.get("formal_blinded") is not True
        or authority.get("selector_feedback_consumed") is not False
        or authority.get("outcome_feedback_feed") != "NONE"
    ):
        raise RealtimeAlphaForwardShadowError(
            "formal shadow authority attestation is missing or invalid"
        )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery", default=str(DEFAULT_DISCOVERY))
    parser.add_argument("--feature-status", default=str(DEFAULT_FEATURE_STATUS))
    parser.add_argument("--feature-store", default=str(DEFAULT_FEATURE_STORE))
    parser.add_argument("--family-registry", default=str(DEFAULT_FAMILY_REGISTRY))
    parser.add_argument("--shadow-store", default=str(DEFAULT_FORMAL_SHADOW_STORE))
    parser.add_argument("--output", default=str(DEFAULT_FORMAL_OUTPUT))
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
        print(f"realtime formal shadow failed: {type(exc).__name__}: {exc}")
        return 1
    running = bool(report["verdict"].get("formal_blinded_shadow_running"))
    print(
        "realtime formal blinded shadow "
        f"status={report['status']} eligible={len(report['eligible_candidates'])} "
        f"decisions={report['cycle']['new_decisions']} "
        f"realizations={report['cycle']['new_realizations']} "
        f"paper_class={FORMAL_BLINDED_SHADOW_PAPER_CLASS}"
    )
    return SCHEDULE_EXIT_SUCCESS if running else SCHEDULE_EXIT_SAFE_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_FORMAL_OUTPUT",
    "DEFAULT_FORMAL_SHADOW_STORE",
    "FORMAL_BLINDED_SHADOW_AUTHORITY",
    "release_run_attestation",
    "run_cycle",
]
