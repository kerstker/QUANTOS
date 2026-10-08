"""Resident single-node driver for the deterministic L2 MFT runtime.

This entrypoint owns the wall clock and sleep loop.  It creates no broker or
credential client, exposes no order switch, and leaves execution dispatch
structurally blocked until Task 32 wires the isolated Testnet service.
"""
from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SUCCESS,
)
from services.orchestration.mft_runtime import (
    MFT_TASK_IDS,
    MftRuntime,
    MftRuntimeError,
    open_mft_runtime,
)
from tools.cache_io import atomic_write_json


DEFAULT_STATE_ROOT = Path("data") / "cache"
DEFAULT_INTERVAL_MS = 60 * 60 * 1_000
DEFAULT_LEASE_MS = 5 * 60 * 1_000
DEFAULT_POLL_SECONDS = 5.0


def _clock_ms() -> int:
    return time.time_ns() // 1_000_000


def run_resident_loop(
    runtime: MftRuntime,
    *,
    owner_id: str,
    lease_ms: int,
    poll_seconds: float,
    once: bool = False,
    max_iterations: int | None = None,
    clock_ms: Callable[[], int] = _clock_ms,
    sleeper: Callable[[float], None] = time.sleep,
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if not isinstance(runtime, MftRuntime):
        raise TypeError("runtime must be an MftRuntime")
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise ValueError("owner_id must be non-empty")
    if isinstance(lease_ms, bool) or not isinstance(lease_ms, int) or lease_ms < 1:
        raise ValueError("lease_ms must be a positive integer")
    if not isinstance(poll_seconds, (int, float)) or poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    if max_iterations is not None and (
        isinstance(max_iterations, bool)
        or not isinstance(max_iterations, int)
        or max_iterations < 1
    ):
        raise ValueError("max_iterations must be a positive integer")
    latest: dict[str, Any] = {}
    iterations = 0
    while True:
        latest = runtime.run_once(
            owner_id=owner_id,
            now_ms=int(clock_ms()),
            lease_ms=lease_ms,
        )
        if on_result is not None:
            on_result(dict(latest))
        iterations += 1
        if once or (max_iterations is not None and iterations >= max_iterations):
            return latest
        sleeper(float(poll_seconds))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", default=str(DEFAULT_STATE_ROOT))
    parser.add_argument("--owner-id", default="mft-l2-runtime-node")
    parser.add_argument("--interval-ms", type=int, default=DEFAULT_INTERVAL_MS)
    parser.add_argument("--lease-ms", type=int, default=DEFAULT_LEASE_MS)
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--once", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    clock_ms: Callable[[], int] = _clock_ms,
    sleeper: Callable[[float], None] = time.sleep,
) -> int:
    args = build_parser().parse_args(argv)
    if args.interval_ms < 1 or args.lease_ms < 1 or args.poll_seconds <= 0:
        raise ValueError("runtime intervals must be positive")
    state_root = Path(args.state_root)
    state_root.mkdir(parents=True, exist_ok=True)
    database = state_root / "mft_l2_runtime.db"
    status_path = state_root / "mft_l2_runtime_status.json"
    create = not database.exists()
    runtime: MftRuntime | None = None
    try:
        runtime = open_mft_runtime(str(database), create=create)
        now_ms = int(clock_ms())
        if create:
            if now_ms < args.interval_ms:
                raise MftRuntimeError("clock is earlier than one runtime interval")
            runtime.register_tasks(
                interval_ms=args.interval_ms,
                first_due_ms=now_ms,
                now_ms=now_ms - args.interval_ms,
            )
        elif any(runtime.kernel.tasks.get(task_id) is None for task_id in MFT_TASK_IDS):
            raise MftRuntimeError("resident runtime task registration is incomplete")

        def publish(result: dict[str, Any]) -> None:
            atomic_write_json(status_path, result, indent=2)

        result = run_resident_loop(
            runtime,
            owner_id=args.owner_id,
            lease_ms=args.lease_ms,
            poll_seconds=args.poll_seconds,
            once=args.once,
            clock_ms=clock_ms,
            sleeper=sleeper,
            on_result=publish,
        )
    except KeyboardInterrupt:
        return SCHEDULE_EXIT_SUCCESS
    except Exception as exc:
        detail = str(exc).replace("\n", " ")[:160]
        print(f"mft L2 runtime: BLOCK ({type(exc).__name__}) {detail}")
        return SCHEDULE_EXIT_FAILURE
    finally:
        if runtime is not None:
            runtime.close()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return (
        SCHEDULE_EXIT_SUCCESS
        if result.get("runtime_status") in {"COMPLETED", "IDLE"}
        else SCHEDULE_EXIT_FAILURE
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_INTERVAL_MS",
    "DEFAULT_LEASE_MS",
    "DEFAULT_POLL_SECONDS",
    "DEFAULT_STATE_ROOT",
    "build_parser",
    "main",
    "run_resident_loop",
]
