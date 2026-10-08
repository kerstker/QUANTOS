"""Run the fixed 10,000 USD isolated L2 paper profile once.

This is a short-lived scheduled worker, not a resident poller.  It delegates
to the same public-mark, paper-only driver as the default account while
keeping its order ledger, realtime ledger, receipts, and health report
separate.  The fixed profile makes the small-capital paper account usable
from Task Scheduler without allowing arbitrary worker arguments or any live
capability.
"""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

# The scheduled worker is also a documented direct-script entrypoint.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools import paper_realtime


PROFILE_AUM = 10_000.0
PROFILE_ARGS: tuple[str, ...] = (
    "--cycles",
    "1",
    "--health-check",
    "--aum",
    "10000",
    "--paper-store",
    "data/cache/paper_order_ledger_10k.db",
    "--store",
    "data/cache/paper_realtime_10k.db",
    "--output",
    "data/cache/paper_realtime_10k_status.json",
    "--operator-receipt",
    "data/cache/l2_paper_operator_10k.json",
    "--health-output",
    "data/cache/paper_health_10k.json",
)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the fixed profile; arbitrary scheduled arguments are rejected."""
    if argv:
        if any(str(arg) in {"-h", "--help"} for arg in argv):
            argparse.ArgumentParser(
                description="Run the fixed 10,000 USD isolated L2 paper profile once"
            ).parse_args(list(argv))
        raise ValueError("paper_realtime_10k does not accept worker arguments")
    return paper_realtime.main(list(PROFILE_ARGS))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


__all__ = ["PROFILE_AUM", "PROFILE_ARGS", "main"]
