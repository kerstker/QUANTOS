"""Export reviewable data-refresh SLO evidence from the durable operational ledger."""
from __future__ import annotations

import argparse
import os
import time

from services.data.refresh_history import RefreshHistoryStore
from tools.cache_io import atomic_write_json
from tools.live_data_refresh import (
    _HISTORY,
    _SOURCE_ORDER,
    GLOBAL_TIMEOUT_SECONDS,
    SLO_MAX_CONSECUTIVE_FAILURES,
    SLO_MINIMUM_INSTRUMENT_COVERAGE,
    SLO_MINIMUM_RUNS,
    SLO_MINIMUM_SUCCESS_RATE,
    SLO_WINDOW_RUNS,
    STALE_LIMITS,
)


DEFAULT_OUTPUT = os.path.join("evidence", "data_refresh_slo.json")


def build_evidence(*, history_path: str, generated_at_ms: int) -> dict:
    store = RefreshHistoryStore(history_path, create=False)
    try:
        slo = store.evaluate_slo(
            expected_sources=_SOURCE_ORDER,
            stale_limits=STALE_LIMITS,
            evaluated_at_ms=generated_at_ms,
            window_runs=SLO_WINDOW_RUNS,
            minimum_runs=SLO_MINIMUM_RUNS,
            minimum_success_rate=SLO_MINIMUM_SUCCESS_RATE,
            minimum_instrument_coverage=SLO_MINIMUM_INSTRUMENT_COVERAGE,
            max_consecutive_failures=SLO_MAX_CONSECUTIVE_FAILURES,
            max_global_duration_ms=int(GLOBAL_TIMEOUT_SECONDS * 1000),
        )
        history = {
            "path": history_path,
            "run_count": store.count(),
            "chain_valid": store.chain_valid(),
            "head_hash": store.head_hash,
        }
        watermarks = {
            source: store.watermark_history(source, limit=SLO_WINDOW_RUNS)
            for source in _SOURCE_ORDER
        }
    finally:
        store.close()

    return {
        "schema_version": 1,
        "generated_at_ms": int(generated_at_ms),
        "provenance": "REAL_REFRESH_HISTORY",
        "readiness": "L2_DRY_RUN_ONLY",
        "decision": "PASS" if slo["verdict"] == "PASS" else "BLOCK",
        "history": history,
        "slo": slo,
        "watermark_history": watermarks,
        "limitations": [
            "Local SQLite hash chaining does not replace external WORM/object-lock retention.",
            "PASS requires the configured minimum number of real scheduled refresh runs.",
            "This evidence does not enable live trading or broker connectivity.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", default=_HISTORY)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    evidence = build_evidence(
        history_path=args.history,
        generated_at_ms=int(time.time() * 1000),
    )
    atomic_write_json(args.output, evidence, indent=2)
    print(
        f"data refresh SLO: {evidence['slo']['verdict']} / "
        f"{evidence['history']['run_count']} runs -> {args.output}"
    )


if __name__ == "__main__":
    main()
