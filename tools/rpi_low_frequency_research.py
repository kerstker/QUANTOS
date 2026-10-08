"""Scheduled L2 worker for the future-locked RPI low-frequency v2 plan."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from typing import Any, Sequence

from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SAFE_BLOCKED,
    SCHEDULE_EXIT_SUCCESS,
)
from quantos.runtime_paths import runtime_state_root
from tools.rpi_alpha_research import (
    DEFAULT_EXPERIMENT_DB,
    DEFAULT_FRAME_CACHE,
    DEFAULT_RPI_STORE,
    DEFAULT_SOURCE_STORE,
    run_low_frequency_cycle,
)


ROOT = runtime_state_root()
DEFAULT_OUTPUT = ROOT / "data" / "cache" / "rpi_low_frequency_research.json"
_LAST_REPORT: dict[str, Any] | None = None
_LAST_OUTPUT: Path | None = None


def main(argv: Sequence[str] | None = None) -> int:
    global _LAST_OUTPUT, _LAST_REPORT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpi-store", default=str(DEFAULT_RPI_STORE))
    parser.add_argument("--source-store", default=str(DEFAULT_SOURCE_STORE))
    parser.add_argument("--experiment-db", default=str(DEFAULT_EXPERIMENT_DB))
    parser.add_argument("--frame-cache", default=str(DEFAULT_FRAME_CACHE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    try:
        report = run_low_frequency_cycle(
            rpi_store=args.rpi_store,
            source_store=args.source_store,
            experiment_db=args.experiment_db,
            output_path=args.output,
            frame_cache_path=args.frame_cache,
        )
    except Exception as exc:  # noqa: BLE001 - scheduled boundary fails closed
        print(f"RPI low-frequency research failed: {type(exc).__name__}: {exc}")
        return SCHEDULE_EXIT_FAILURE
    _LAST_REPORT = report
    _LAST_OUTPUT = Path(args.output)
    print(
        "RPI low-frequency research "
        f"status={report['status']} evaluated={report['funnel']['evaluated']} "
        f"statistical_passes={report['funnel']['statistical_passes']} "
        f"promotion_eligible={report['funnel']['promotion_eligible']}"
    )
    return (
        SCHEDULE_EXIT_SUCCESS
        if report["data_admission"]["exploration_allowed"]
        else SCHEDULE_EXIT_SAFE_BLOCKED
    )


def release_run_attestation() -> dict[str, object]:
    if _LAST_REPORT is None or _LAST_OUTPUT is None or not _LAST_OUTPUT.is_file():
        raise RuntimeError("RPI low-frequency run has no complete report")
    authority_hash = _LAST_REPORT.get("rpi_authority_hash")
    plan_hash = _LAST_REPORT.get("plan_hash")
    if not isinstance(authority_hash, str) or len(authority_hash) != 64:
        raise RuntimeError("RPI authority digest is unavailable")
    if not isinstance(plan_hash, str) or len(plan_hash) != 64:
        raise RuntimeError("RPI plan digest is unavailable")
    release_commit = os.environ.get("QUANTOS_RELEASE_COMMIT", "")
    return {
        "schema_version": 1,
        "module": "rpi_low_frequency_research",
        "release_commit": release_commit,
        "complete": True,
        "input_hashes": {
            "rpi_authority": authority_hash,
            "research_plan": plan_hash,
        },
        "output_hashes": {
            "research_report": hashlib.sha256(_LAST_OUTPUT.read_bytes()).hexdigest(),
        },
        "safety": {
            "paper_only": True,
            "live_allowed": False,
            "order_submission_allowed": False,
            "credentials_accessed": False,
            "broker_connectivity": False,
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DEFAULT_OUTPUT", "main", "release_run_attestation"]
