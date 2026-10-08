"""Process exit-code contract for supervised L2 scheduled workers.

Exit code 2 means the worker completed its evaluation and deliberately stayed
fail-closed because research or operational readiness is incomplete. It is not
interchangeable with exit code 1, which remains an execution/configuration
failure. Nothing in this module grants deployment or trading authority.
"""
from __future__ import annotations


SCHEDULE_EXIT_SUCCESS = 0
SCHEDULE_EXIT_FAILURE = 1
SCHEDULE_EXIT_SAFE_BLOCKED = 2


__all__ = [
    "SCHEDULE_EXIT_FAILURE",
    "SCHEDULE_EXIT_SAFE_BLOCKED",
    "SCHEDULE_EXIT_SUCCESS",
]
