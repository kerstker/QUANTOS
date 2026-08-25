"""Narrow process boundary for the Binance cursor-chain verifier.

The realtime fetcher may request only this repository's fixed verifier module.
Arguments are passed as an argv list with ``shell=False``; callers cannot supply
an executable, module, or arbitrary command.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


# A genesis audit over the production cursor authority covered 45.9M
# payload-bound events on DGX ARM64 and crossed the former 90-minute ceiling
# while remaining CPU-bound.  Resident renewal starts four hours before expiry;
# this three-hour hard ceiling leaves one hour of scheduling margin.  Normal
# restarts still use the trusted batch checkpoint and verify only the suffix.
CURSOR_CHAIN_VERIFY_TIMEOUT_SECONDS = 3 * 60 * 60.0


class CursorChainAuditProcessError(RuntimeError):
    """The isolated verifier did not return one coherent fail-closed result."""


def cursor_chain_audit_command(
    store_path: str,
    *,
    checkpoint_seq: int = 0,
    checkpoint_row_hash: str = "",
) -> list[str]:
    """Return the fixed verifier argv; callers cannot select an executable."""

    return [
        sys.executable,
        "-m",
        "tools.binance_realtime_stream",
        "--verify-cursor-chain",
        "--store",
        str(Path(store_path).resolve()),
        "--cursor-checkpoint-seq",
        str(checkpoint_seq),
        "--cursor-checkpoint-row-hash",
        checkpoint_row_hash,
    ]


def parse_cursor_chain_audit_result(
    *, stdout: str, returncode: int
) -> dict[str, Any]:
    """Validate that process status and its JSON verdict agree fail-closed."""

    try:
        verification = json.loads(stdout)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CursorChainAuditProcessError(
            "cursor chain verifier returned malformed output"
        ) from exc
    if not isinstance(verification, dict) or not isinstance(
        verification.get("valid"), bool
    ):
        raise CursorChainAuditProcessError(
            "cursor chain verifier returned an inconsistent result"
        )
    success = verification["valid"] is True
    if (returncode == 0) != success:
        raise CursorChainAuditProcessError(
            "cursor chain verifier returned an inconsistent result"
        )
    return verification


def run_cursor_chain_audit(
    store_path: str,
    *,
    checkpoint_seq: int = 0,
    checkpoint_row_hash: str = "",
) -> dict[str, Any]:
    """Run the fixed cursor verifier outside the WebSocket process GIL."""
    command = cursor_chain_audit_command(
        store_path,
        checkpoint_seq=checkpoint_seq,
        checkpoint_row_hash=checkpoint_row_hash,
    )
    result = subprocess.run(
        command,
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        timeout=CURSOR_CHAIN_VERIFY_TIMEOUT_SECONDS,
    )
    return parse_cursor_chain_audit_result(
        stdout=result.stdout,
        returncode=result.returncode,
    )


__all__ = [
    "CURSOR_CHAIN_VERIFY_TIMEOUT_SECONDS",
    "CursorChainAuditProcessError",
    "cursor_chain_audit_command",
    "parse_cursor_chain_audit_result",
    "run_cursor_chain_audit",
]
