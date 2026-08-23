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


# A genesis audit over the production cursor authority currently covers several
# million payload-bound events.  The DGX ARM64 audit is CPU-bound and has crossed
# the former 30-minute ceiling while remaining healthy.  This is a bounded
# bootstrap/renewal proof; normal restarts use the trusted batch checkpoint and
# verify only the suffix.
CURSOR_CHAIN_VERIFY_TIMEOUT_SECONDS = 60 * 60.0


class CursorChainAuditProcessError(RuntimeError):
    """The isolated verifier did not return one coherent fail-closed result."""


def run_cursor_chain_audit(
    store_path: str,
    *,
    checkpoint_seq: int = 0,
    checkpoint_row_hash: str = "",
) -> dict[str, Any]:
    """Run the fixed cursor verifier outside the WebSocket process GIL."""
    command = [
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
    result = subprocess.run(
        command,
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        timeout=CURSOR_CHAIN_VERIFY_TIMEOUT_SECONDS,
    )
    try:
        verification = json.loads(result.stdout)
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
    if (result.returncode == 0) != success:
        raise CursorChainAuditProcessError(
            "cursor chain verifier returned an inconsistent result"
        )
    return verification


__all__ = [
    "CURSOR_CHAIN_VERIFY_TIMEOUT_SECONDS",
    "CursorChainAuditProcessError",
    "run_cursor_chain_audit",
]
