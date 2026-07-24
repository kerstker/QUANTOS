"""Run the fixed 10,000 USD isolated L2 paper profile once.

This is a short-lived scheduled worker, not a resident poller.  It delegates
to the same public-mark, paper-only driver as the default account while
keeping its order ledger, realtime ledger, receipts, and health report
separate.  The fixed profile makes the small-capital paper account usable
from Task Scheduler without allowing arbitrary worker arguments or any live
capability.
"""
from __future__ import annotations

from collections.abc import Sequence

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
        raise ValueError("paper_realtime_10k does not accept worker arguments")
    return paper_realtime.main(list(PROFILE_ARGS))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["PROFILE_AUM", "PROFILE_ARGS", "main"]
