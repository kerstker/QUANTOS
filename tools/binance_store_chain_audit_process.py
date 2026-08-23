"""Fixed subprocess boundary for the large Binance snapshot-chain audit.

The resident WebSocket worker must remain cancellable while a production store
is scanned from genesis.  This module owns the only fixed argv used for that
audit and validates that the child exit status agrees with its JSON verdict.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


STORE_CHAIN_VERIFY_TIMEOUT_SECONDS = 60 * 60.0


class StoreChainAuditProcessError(RuntimeError):
    """The isolated snapshot-chain verifier did not return one coherent result."""


def store_chain_audit_command(
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
        "--verify-store-chain",
        "--store",
        str(Path(store_path).resolve()),
        "--cursor-checkpoint-seq",
        str(checkpoint_seq),
        "--cursor-checkpoint-row-hash",
        checkpoint_row_hash,
    ]


def parse_store_chain_audit_result(
    *, stdout: str, returncode: int
) -> dict[str, Any]:
    """Validate that process status and its JSON verdict agree fail-closed."""

    try:
        verification = json.loads(stdout)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StoreChainAuditProcessError(
            "store chain verifier returned malformed output"
        ) from exc
    if not isinstance(verification, dict) or not isinstance(
        verification.get("valid"), bool
    ):
        raise StoreChainAuditProcessError(
            "store chain verifier returned an inconsistent result"
        )
    success = verification["valid"] is True
    if (returncode == 0) != success:
        raise StoreChainAuditProcessError(
            "store chain verifier returned an inconsistent result"
        )
    return verification


def run_store_chain_audit(
    store_path: str,
    *,
    checkpoint_seq: int = 0,
    checkpoint_row_hash: str = "",
) -> dict[str, Any]:
    """Run the fixed snapshot verifier outside the resident worker process."""

    result = subprocess.run(
        store_chain_audit_command(
            store_path,
            checkpoint_seq=checkpoint_seq,
            checkpoint_row_hash=checkpoint_row_hash,
        ),
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        timeout=STORE_CHAIN_VERIFY_TIMEOUT_SECONDS,
    )
    return parse_store_chain_audit_result(
        stdout=result.stdout,
        returncode=result.returncode,
    )


__all__ = [
    "STORE_CHAIN_VERIFY_TIMEOUT_SECONDS",
    "StoreChainAuditProcessError",
    "parse_store_chain_audit_result",
    "run_store_chain_audit",
    "store_chain_audit_command",
]
