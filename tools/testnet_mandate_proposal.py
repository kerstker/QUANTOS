"""Generate an unsigned Testnet mandate proposal from authoritative inputs.

This command is offline and review-only.  It has no credential loader, network
client, exchange adapter, submission switch, signature, or approval path.
"""
from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from live.governance.testnet_mandate_proposal import (
    build_testnet_mandate_proposal,
    derive_testnet_mandate_ceiling,
)
from quantos.contracts.mft_execution import TestnetExecutionIntent
from tools.cache_io import atomic_write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", required=True)
    parser.add_argument("--runtime-input", required=True)
    parser.add_argument("--out", required=True)
    return parser


def _load_intent(path: str) -> TestnetExecutionIntent:
    return TestnetExecutionIntent.model_validate_json(
        Path(path).read_text(encoding="utf-8"), strict=True
    )


def _load_runtime(path: str) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("runtime input must be an object")
    return value


def main(
    argv: Sequence[str] | None = None,
    *,
    clock_ms: Callable[[], int] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        now_ms = (clock_ms or (lambda: time.time_ns() // 1_000_000))()
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise ValueError("clock is invalid")
        intent = _load_intent(args.intent)
        runtime = _load_runtime(args.runtime_input)
        ceiling = derive_testnet_mandate_ceiling(intent, runtime)
        proposal = build_testnet_mandate_proposal(
            ceiling, generated_at_ms=now_ms
        )
        atomic_write_json(args.out, proposal, indent=2)
        print(
            json.dumps(
                {
                    "status": proposal["status"],
                    "proposal_hash": proposal["proposal_hash"],
                    "risk_policy_version": ceiling.risk_policy_version,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "ERROR",
                    "error_code": f"MANDATE_PROPOSAL_INPUT_INVALID_{type(exc).__name__.upper()}",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
