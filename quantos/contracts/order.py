"""Canonical order lifecycle state machine (blueprint §7).

Defines the explicit set of order states and the ALLOWED transitions between
them. Strategy/execution code must route every state change through
`assert_transition` so illegal transitions are impossible.
"""
from __future__ import annotations

from enum import Enum


class OrderState(str, Enum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    RISK_REJECTED = "RISK_REJECTED"
    RISK_APPROVED = "RISK_APPROVED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


TERMINAL_STATES: frozenset[OrderState] = frozenset({
    OrderState.RISK_REJECTED,
    OrderState.FILLED,
    OrderState.CANCELLED,
    OrderState.REJECTED,
    OrderState.EXPIRED,
    OrderState.FAILED,
})

ALLOWED_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.CREATED: frozenset({OrderState.VALIDATING, OrderState.FAILED}),
    OrderState.VALIDATING: frozenset({OrderState.RISK_APPROVED, OrderState.RISK_REJECTED, OrderState.FAILED}),
    OrderState.RISK_APPROVED: frozenset({OrderState.SUBMITTED, OrderState.CANCEL_REQUESTED, OrderState.EXPIRED, OrderState.FAILED}),
    OrderState.SUBMITTED: frozenset({OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCEL_REQUESTED, OrderState.REJECTED, OrderState.EXPIRED, OrderState.FAILED}),
    OrderState.PARTIALLY_FILLED: frozenset({OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCEL_REQUESTED, OrderState.CANCELLED, OrderState.EXPIRED, OrderState.FAILED}),
    OrderState.CANCEL_REQUESTED: frozenset({OrderState.CANCELLED, OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.REJECTED, OrderState.FAILED}),
    # terminal states: no outgoing transitions
    OrderState.RISK_REJECTED: frozenset(),
    OrderState.FILLED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.EXPIRED: frozenset(),
    OrderState.FAILED: frozenset(),
}


class InvalidOrderTransition(ValueError):
    def __init__(self, src: OrderState, dst: OrderState):
        super().__init__(f"illegal order transition: {src.value} -> {dst.value}")
        self.src, self.dst = src, dst


def can_transition(src: OrderState, dst: OrderState) -> bool:
    return dst in ALLOWED_TRANSITIONS.get(src, frozenset())


def assert_transition(src: OrderState, dst: OrderState) -> None:
    if not can_transition(src, dst):
        raise InvalidOrderTransition(src, dst)


def is_terminal(state: OrderState) -> bool:
    return state in TERMINAL_STATES


def assert_snapshot_transition(prev: OrderState, new: OrderState) -> None:
    """A snapshot may skip intermediate states, but cannot rewrite a terminal."""
    if prev == new:
        return
    seen, frontier = {prev}, [prev]
    while frontier:
        nxt = []
        for state in frontier:
            for target in ALLOWED_TRANSITIONS.get(state, ()):
                if target == new:
                    return
                if target not in seen:
                    seen.add(target)
                    nxt.append(target)
        frontier = nxt
    raise InvalidOrderTransition(prev, new)
