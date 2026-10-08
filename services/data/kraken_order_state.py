"""Kraken order observations mapped onto the existing canonical OMS lifecycle.

A terminal order is not proof of complete fills, cash reconciliation, or permission
for another order. Unknown responses must be resolved by independent queries.
"""
from decimal import Decimal

from quantos.contracts.order import OrderState, TERMINAL_STATES, assert_snapshot_transition
from services.data.kraken_public import require


def state(status, volume, filled):
    require(type(volume) is Decimal and type(filled) is Decimal and volume.is_finite() and filled.is_finite()
            and volume>0 and 0<=filled<=volume,'KRAKEN_ORDER_STATE_QUANTITY')
    if status=='pending':
        require(filled==0,'KRAKEN_PENDING_HAS_FILL')
        return OrderState.SUBMITTED
    if status=='open':
        require(filled<volume,'KRAKEN_OPEN_FULLY_FILLED')
        return OrderState.PARTIALLY_FILLED if filled>0 else OrderState.SUBMITTED
    if status=='closed':
        require(filled==volume,'KRAKEN_CLOSED_INCOMPLETE')
        return OrderState.FILLED
    if status=='canceled':return OrderState.CANCELLED
    if status=='expired':return OrderState.EXPIRED
    raise ValueError('KRAKEN_ORDER_STATUS_UNKNOWN')


def assert_progress(previous_status, new_status, volume, previous_filled, new_filled):
    previous=state(previous_status,volume,previous_filled)
    new=state(new_status,volume,new_filled)
    require(new_filled>=previous_filled,'KRAKEN_ORDER_FILL_REGRESSION')
    if previous in TERMINAL_STATES:
        require(previous==new and previous_filled==new_filled,'KRAKEN_ORDER_TERMINAL_CHANGED')
    assert_snapshot_transition(previous,new)
