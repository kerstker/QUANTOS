"""Hard-timeout process boundary for Binance public REST depth recovery.

This is public, credential-free market data only.  The resident stream worker
spawns this fixed module so DNS, proxy, or TLS stalls cannot leave an
uncancellable executor thread behind during shutdown.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.binance_rest_gap_fill import fetch_depth_snapshot


DEPTH_SNAPSHOT_TIMEOUT_SECONDS = 45.0


class DepthSnapshotProcessError(RuntimeError):
    """The isolated public depth fetch did not return one coherent result."""


def depth_snapshot_command(symbol: str, *, observed_at_ms: int) -> list[str]:
    """Return the fixed public-data fetch argv."""

    return [
        sys.executable,
        "-m",
        "tools.binance_depth_snapshot_process",
        "--symbol",
        str(symbol).upper(),
        "--observed-at-ms",
        str(int(observed_at_ms)),
    ]


def parse_depth_snapshot_result(*, stdout: str, returncode: int) -> dict[str, Any]:
    """Accept only a successful JSON object with the recovery identity fields."""

    if returncode != 0:
        raise DepthSnapshotProcessError("depth snapshot process failed")
    try:
        snapshot = json.loads(stdout)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DepthSnapshotProcessError(
            "depth snapshot process returned malformed output"
        ) from exc
    if not isinstance(snapshot, Mapping) or not snapshot:
        raise DepthSnapshotProcessError(
            "depth snapshot process returned an inconsistent result"
        )
    try:
        int(snapshot["last_update_id"])
        int(snapshot["event_time_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DepthSnapshotProcessError(
            "depth snapshot process returned an inconsistent result"
        ) from exc
    return dict(snapshot)


def run_depth_snapshot(symbol: str, *, observed_at_ms: int) -> dict[str, Any]:
    """Run the fixed public fetch with an outer hard wall-clock budget."""

    result = subprocess.run(
        depth_snapshot_command(symbol, observed_at_ms=observed_at_ms),
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        timeout=DEPTH_SNAPSHOT_TIMEOUT_SECONDS,
    )
    return parse_depth_snapshot_result(
        stdout=result.stdout,
        returncode=result.returncode,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--observed-at-ms", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        snapshot = fetch_depth_snapshot(
            str(args.symbol).upper(),
            observed_at_ms=int(args.observed_at_ms),
        )
    except Exception:  # noqa: BLE001 - child exposes status, never transport details
        return 1
    print(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEPTH_SNAPSHOT_TIMEOUT_SECONDS",
    "DepthSnapshotProcessError",
    "depth_snapshot_command",
    "parse_depth_snapshot_result",
    "run_depth_snapshot",
]
