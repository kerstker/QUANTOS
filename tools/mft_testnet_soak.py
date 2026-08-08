"""Record and evaluate Task 39 Testnet-only soak evidence.

This tool cannot submit orders or load credentials.  It consumes an external
operator-start receipt and per-cycle evidence receipts, stores them in an
append-only hash chain, and publishes a fail-closed status projection.
"""
from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from services.monitoring.mft_soak import (
    MftSoakError,
    build_soak_status,
    parse_soak_policy,
)
from services.monitoring.mft_soak_store import MftSoakStore, MftSoakStoreError
from tools.cache_io import atomic_write_json


class MftSoakCliError(RuntimeError):
    pass


def _load(path: str, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise MftSoakCliError(f"{label}_INVALID_{type(exc).__name__.upper()}") from None
    if not isinstance(value, dict):
        raise MftSoakCliError(f"{label}_INVALID_TYPE")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--store", required=True)
    parser.add_argument("--status-out", required=True)
    parser.add_argument("--evidence-root", required=True)
    mutation = parser.add_mutually_exclusive_group()
    mutation.add_argument("--start-receipt")
    mutation.add_argument("--cycle-receipt")
    return parser


def _safe_error(exc: BaseException) -> str:
    value = str(exc)
    if value == "SOAK_START_RECEIPT_MISSING":
        return value
    if isinstance(exc, MftSoakCliError):
        return value
    if isinstance(exc, (MftSoakError, MftSoakStoreError, ValueError, TypeError)):
        return f"SOAK_INPUT_INVALID_{type(exc).__name__.upper()}"
    return f"SOAK_STARTUP_ERROR_{type(exc).__name__.upper()}"


def main(
    argv: Sequence[str] | None = None,
    *,
    clock_ms: Callable[[], int] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    store: MftSoakStore | None = None
    try:
        now_ms = int((clock_ms or (lambda: time.time_ns() // 1_000_000))())
        if now_ms < 0:
            raise MftSoakCliError("CLOCK_INVALID")
        policy = parse_soak_policy(_load(args.policy, label="POLICY"))
        store_path = Path(args.store)
        store = MftSoakStore(str(store_path), create=not store_path.exists())
        run_id: str | None = None
        if args.start_receipt:
            start = _load(args.start_receipt, label="START_RECEIPT")
            store.append_start(start, policy=policy, recorded_at_ms=now_ms)
            run_id = start.get("run_id") if isinstance(start.get("run_id"), str) else None
        elif args.cycle_receipt:
            cycle = _load(args.cycle_receipt, label="CYCLE_RECEIPT")
            store.append_cycle(
                cycle,
                policy=policy,
                evidence_root=args.evidence_root,
                recorded_at_ms=now_ms,
            )
            run_id = cycle.get("run_id") if isinstance(cycle.get("run_id"), str) else None
        else:
            run_id = store.latest_run_id()
        start_receipt = None if run_id is None else store.start_receipt(run_id)
        cycles: tuple[Mapping[str, Any], ...] = (
            () if run_id is None else tuple(store.cycles(run_id))
        )
        status = build_soak_status(
            policy=policy,
            start_receipt=start_receipt,
            cycles=cycles,
            chain_valid=store.chain_valid(),
            as_of_ms=now_ms,
        )
        atomic_write_json(args.status_out, status, indent=2)
        print(json.dumps(status, sort_keys=True, separators=(",", ":")))
        return 2 if status["status"] == "OPERATIONAL_SOAK_BLOCKED" else 0
    except Exception as exc:
        print(
            json.dumps(
                {"error_code": _safe_error(exc), "status": "ERROR"},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 3
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["MftSoakCliError", "build_parser", "main"]
