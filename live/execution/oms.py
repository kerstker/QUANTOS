"""Continuous order-event authority and deterministic ledger projection."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from live.execution.order_event_store import (
    OrderEventRecord,
    OrderEventSource,
    SqliteOrderEventStore,
    create_order_event,
)
from live.execution.position_ledger import LiveLedger
from live.execution.order_recovery import (
    RecoveryState,
    SqliteOrderRecoveryStore,
)
from quantos.contracts.hashing import stable_hash
from quantos.contracts.order import OrderState
from quantos.contracts.trading import OrderSide
from live.execution.oms_state import assert_snapshot_transition


_EPS = 1e-12
_TERMINAL = frozenset(
    {
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
        OrderState.FAILED,
    }
)


class OmsError(RuntimeError):
    """Base failure for the event-authoritative OMS."""


class OmsConflict(OmsError):
    """An event conflicts with known order identity or cumulative execution."""


class OmsProjectionError(OmsError):
    """A journaled event cannot be safely projected; replay must stop."""


@dataclass(frozen=True, slots=True)
class OmsFillProjection:
    fill_id: str
    quantity: float
    price: float


@dataclass(frozen=True, slots=True)
class OmsEventProjection:
    record: OrderEventRecord
    fill: OmsFillProjection | None


@dataclass(frozen=True, slots=True)
class OmsOrderReadModel:
    client_order_id: str
    state: OrderState
    cumulative_filled_qty: float
    avg_fill_price: float
    venue_order_id: str
    last_observed_at_ms: int
    last_event_hash: str


@dataclass(slots=True)
class _Aggregate:
    first: OrderEventRecord
    state: OrderState
    cumulative: float
    average: float
    venue_order_id: str
    observed_at_ms: int
    last_event_hash: str


def _same_order(first: OrderEventRecord, current: OrderEventRecord) -> bool:
    left = first.event
    right = current.event
    return (
        left.client_order_id,
        left.instrument_id,
        left.side,
        left.order_quantity,
        left.strategy_id,
        left.order_type,
        left.limit_price,
    ) == (
        right.client_order_id,
        right.instrument_id,
        right.side,
        right.order_quantity,
        right.strategy_id,
        right.order_type,
        right.limit_price,
    )


def _validate_state_quantity(record: OrderEventRecord) -> None:
    event = record.event
    quantity = event.cumulative_filled_qty
    if event.canonical_state in {OrderState.RISK_APPROVED, OrderState.SUBMITTED}:
        if quantity != 0:
            raise OmsConflict("unfilled order state carries cumulative execution")
    elif event.canonical_state is OrderState.PARTIALLY_FILLED:
        if not 0 < quantity < event.order_quantity:
            raise OmsConflict("partial fill quantity is not strictly partial")
    elif event.canonical_state is OrderState.FILLED:
        if not math.isclose(quantity, event.order_quantity, rel_tol=0, abs_tol=_EPS):
            raise OmsConflict("FILLED quantity does not equal order quantity")
    elif event.canonical_state in {OrderState.REJECTED, OrderState.FAILED}:
        if quantity != 0:
            raise OmsConflict("non-executed terminal state carries a fill")


def reduce_order_events(
    records: Iterable[OrderEventRecord],
) -> tuple[tuple[OmsEventProjection, ...], dict[str, OmsOrderReadModel]]:
    """Replay the journal into deterministic projections and read models."""

    aggregates: dict[str, _Aggregate] = {}
    projections: list[OmsEventProjection] = []
    for record in records:
        if not isinstance(record, OrderEventRecord):
            raise TypeError("records must contain OrderEventRecord values")
        event = record.event
        _validate_state_quantity(record)
        aggregate = aggregates.get(event.client_order_id)
        if aggregate is None:
            if (
                event.source is not OrderEventSource.COMMAND
                or event.source_sequence != 0
                or event.venue_status != "PENDING_NEW"
                or event.canonical_state is not OrderState.RISK_APPROVED
                or event.cumulative_filled_qty != 0
            ):
                raise OmsConflict("first order event must be COMMAND PENDING_NEW")
            aggregate = _Aggregate(
                first=record,
                state=event.canonical_state,
                cumulative=0.0,
                average=0.0,
                venue_order_id=event.venue_order_id,
                observed_at_ms=event.observed_at_ms,
                last_event_hash=record.event_hash,
            )
            aggregates[event.client_order_id] = aggregate
            projections.append(OmsEventProjection(record=record, fill=None))
            continue
        if event.source is OrderEventSource.COMMAND:
            raise OmsConflict("COMMAND source cannot mutate an existing order")
        if not _same_order(aggregate.first, record):
            raise OmsConflict("order immutable identity changed across events")
        if event.observed_at_ms < aggregate.observed_at_ms:
            raise OmsConflict("cross-source event time regressed")
        if (
            aggregate.venue_order_id
            and event.venue_order_id
            and aggregate.venue_order_id != event.venue_order_id
        ):
            raise OmsConflict("venue_order_id changed across events")
        try:
            assert_snapshot_transition(aggregate.state, event.canonical_state)
        except Exception as exc:
            raise OmsConflict(str(exc)) from exc
        if aggregate.state in _TERMINAL and (
            not math.isclose(
                event.cumulative_filled_qty,
                aggregate.cumulative,
                rel_tol=0,
                abs_tol=_EPS,
            )
            or not math.isclose(
                event.avg_fill_price,
                aggregate.average,
                rel_tol=0,
                abs_tol=_EPS,
            )
        ):
            raise OmsConflict("terminal order economics changed")
        if event.cumulative_filled_qty + _EPS < aggregate.cumulative:
            raise OmsConflict("cumulative fill quantity regressed")
        delta = event.cumulative_filled_qty - aggregate.cumulative
        fill = None
        if abs(delta) <= _EPS:
            if not math.isclose(
                event.avg_fill_price,
                aggregate.average,
                rel_tol=0,
                abs_tol=_EPS,
            ):
                raise OmsConflict("average fill price changed without new fill")
        else:
            incremental_notional = (
                event.avg_fill_price * event.cumulative_filled_qty
                - aggregate.average * aggregate.cumulative
            )
            price = incremental_notional / delta
            if not math.isfinite(price) or price <= 0:
                raise OmsConflict("incremental fill price is invalid")
            fill = OmsFillProjection(
                fill_id=stable_hash(
                    {
                        "client_order_id": event.client_order_id,
                        "event_hash": record.event_hash,
                        "cumulative_filled_qty": event.cumulative_filled_qty,
                    }
                ),
                quantity=delta,
                price=price,
            )
        aggregate.state = event.canonical_state
        aggregate.cumulative = event.cumulative_filled_qty
        aggregate.average = event.avg_fill_price
        aggregate.venue_order_id = event.venue_order_id or aggregate.venue_order_id
        aggregate.observed_at_ms = event.observed_at_ms
        aggregate.last_event_hash = record.event_hash
        projections.append(OmsEventProjection(record=record, fill=fill))
    read_models = {
        client_order_id: OmsOrderReadModel(
            client_order_id=client_order_id,
            state=aggregate.state,
            cumulative_filled_qty=aggregate.cumulative,
            avg_fill_price=aggregate.average,
            venue_order_id=aggregate.venue_order_id,
            last_observed_at_ms=aggregate.observed_at_ms,
            last_event_hash=aggregate.last_event_hash,
        )
        for client_order_id, aggregate in aggregates.items()
    }
    return tuple(projections), read_models


class OmsAuthority:
    """Journal first, then atomically project each event into ``LiveLedger``."""

    def __init__(
        self,
        event_store: SqliteOrderEventStore,
        ledger: LiveLedger,
        *,
        recovery_store: SqliteOrderRecoveryStore | None = None,
    ) -> None:
        if not isinstance(event_store, SqliteOrderEventStore):
            raise TypeError("event_store must be a SqliteOrderEventStore")
        if not isinstance(ledger, LiveLedger):
            raise TypeError("ledger must be a LiveLedger")
        if recovery_store is not None and not isinstance(
            recovery_store, SqliteOrderRecoveryStore
        ):
            raise TypeError("recovery_store must be a SqliteOrderRecoveryStore")
        self._events = event_store
        self._ledger = ledger
        self._recovery = recovery_store

    def _queue_projection_unknown(self, record: OrderEventRecord) -> None:
        if self._recovery is None:
            raise OmsProjectionError(
                "OMS projection failed without an order recovery authority"
            )
        event = record.event
        existing = self._recovery.latest(event.client_order_id)
        if existing is not None:
            immutable = (
                existing.deployment_id,
                existing.instrument_id,
                existing.side,
                existing.quantity,
            )
            expected = (
                event.strategy_id,
                event.instrument_id,
                event.side.value,
                event.order_quantity,
            )
            if immutable != expected:
                raise OmsProjectionError(
                    "OMS projection recovery identity conflicts with existing record"
                )
            if existing.state in {
                RecoveryState.ORDER_SUBMISSION_UNKNOWN,
                RecoveryState.FOUND,
                RecoveryState.INDETERMINATE,
                RecoveryState.KILL_SWITCHED,
            }:
                return
            raise OmsProjectionError(
                "OMS projection failure conflicts with terminal recovery state"
            )
        self._recovery.register_unknown(
            client_order_id=event.client_order_id,
            deployment_id=event.strategy_id,
            instrument_id=event.instrument_id,
            side=event.side.value,
            quantity=event.order_quantity,
            now_ms=event.observed_at_ms,
            exchange_order_id=event.venue_order_id,
            reason="OMS journal committed but ledger projection is unresolved",
        )

    def _reduce_existing(self):
        try:
            return reduce_order_events(self._events.events())
        except Exception as exc:
            raise OmsProjectionError(str(exc)) from exc

    def replay(self) -> dict[str, OmsOrderReadModel]:
        projections, read_models = self._reduce_existing()
        try:
            for projection in projections:
                fill = projection.fill
                try:
                    self._ledger.apply_oms_projection(
                        projection.record,
                        fill_id="" if fill is None else fill.fill_id,
                        fill_quantity=0.0 if fill is None else fill.quantity,
                        fill_price=0.0 if fill is None else fill.price,
                    )
                except Exception:
                    if (
                        projection.record.event.source
                        is not OrderEventSource.COMMAND
                    ):
                        self._queue_projection_unknown(projection.record)
                    raise
        except Exception as exc:
            raise OmsProjectionError(str(exc)) from exc
        return read_models

    def register_submission(
        self,
        *,
        client_order_id: str,
        instrument_id: str,
        side: OrderSide,
        order_quantity: float,
        strategy_id: str,
        order_type: str,
        limit_price: float | None,
        observed_at_ms: int,
        command_event_id: str,
    ) -> OrderEventRecord:
        self.replay()
        event = create_order_event(
            source=OrderEventSource.COMMAND,
            source_sequence=0,
            venue_event_id=command_event_id,
            client_order_id=client_order_id,
            venue_order_id="",
            instrument_id=instrument_id,
            side=side,
            order_quantity=order_quantity,
            strategy_id=strategy_id,
            order_type=order_type,
            limit_price=limit_price,
            venue_status="PENDING_NEW",
            cumulative_filled_qty=0.0,
            avg_fill_price=0.0,
            observed_at_ms=observed_at_ms,
            venue_payload_hash=stable_hash(
                {
                    "kind": "OMS_COMMAND",
                    "command_event_id": command_event_id,
                    "client_order_id": client_order_id,
                }
            ),
        )
        record = self._events.append(event)
        self.replay()
        return record

    def ingest_venue_snapshot(
        self,
        *,
        client_order_id: str,
        source: OrderEventSource,
        source_sequence: int,
        venue_event_id: str,
        venue_order_id: str,
        venue_status: str,
        cumulative_filled_qty: float,
        avg_fill_price: float,
        observed_at_ms: int,
        venue_payload_hash: str,
    ) -> OrderEventRecord:
        if self._recovery is None:
            raise OmsConflict(
                "venue ingestion requires an order recovery authority"
            )
        _, read_models = self._reduce_existing()
        latest = self._events.latest_for_order(client_order_id)
        if latest is None or client_order_id not in read_models:
            raise OmsConflict("unknown order cannot enter venue event journal")
        first = self._events.events(client_order_id)[0].event
        event = create_order_event(
            source=source,
            source_sequence=source_sequence,
            venue_event_id=venue_event_id,
            client_order_id=client_order_id,
            venue_order_id=venue_order_id,
            instrument_id=first.instrument_id,
            side=first.side,
            order_quantity=first.order_quantity,
            strategy_id=first.strategy_id,
            order_type=first.order_type,
            limit_price=first.limit_price,
            venue_status=venue_status,
            cumulative_filled_qty=cumulative_filled_qty,
            avg_fill_price=avg_fill_price,
            observed_at_ms=observed_at_ms,
            venue_payload_hash=venue_payload_hash,
        )
        record = self._events.append(event)
        try:
            self.replay()
        except OmsProjectionError:
            self._queue_projection_unknown(record)
            raise
        return record


__all__ = [
    "OmsAuthority",
    "OmsConflict",
    "OmsError",
    "OmsEventProjection",
    "OmsFillProjection",
    "OmsOrderReadModel",
    "OmsProjectionError",
    "reduce_order_events",
]
