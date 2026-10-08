#!/usr/bin/env python
"""Run a fresh isolated risk-state recovery drill for the canonical gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Sequence


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.risk_recovery_drill import RiskRecoveryDrillError, run_risk_recovery_drill


def run_fresh_recovery_gate(*, rto_ms: int, observed_at_ms: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="quantos-risk-recovery-") as drill_dir:
        return run_risk_recovery_drill(
            drill_dir=drill_dir,
            rto_ms=rto_ms,
            observed_at_ms=observed_at_ms,
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a fresh isolated QUANTOS risk recovery gate"
    )
    parser.add_argument("--rto-ms", type=int, default=300_000)
    parser.add_argument("--observed-at-ms", type=int)
    args = parser.parse_args(argv)
    observed_at_ms = (
        args.observed_at_ms
        if args.observed_at_ms is not None
        else time.time_ns() // 1_000_000
    )
    try:
        result = run_fresh_recovery_gate(
            rto_ms=args.rto_ms,
            observed_at_ms=observed_at_ms,
        )
    except (OSError, RiskRecoveryDrillError, ValueError) as exc:
        print(f"fresh risk recovery gate failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
