"""Exact public inputs for execution research, separate from five-second features.

This module has no network or persistence surface. A synchronized visible book
is market-by-price evidence, never proof of an individual order's queue position.
Receive clocks remain distinct from transaction and publication clocks. A broken
stream cannot continue serving a seemingly healthy old book.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from typing import Any

from quantos.contracts.decimal_strings import nonnegative_decimal_string

SYMBOLS = frozenset({'BTCUSDC', 'BTCUSDT'})
MAX_SNAPSHOT_LEVELS = 1000
MAX_BOOK_LEVELS = 5000
MAX_EVENTS = 20000


class ExecutionTapeError(ValueError):
    """A public input lacks the identity, sequence, or clock needed for replay."""


def payload_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                     ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _integer(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ExecutionTapeError(f'INVALID_{name}')
    return value


def _amount(value: object, *, positive: bool = False) -> Decimal:
    try:
        return nonnegative_decimal_string(value, positive=positive)
    except ValueError as exc:
        raise ExecutionTapeError(str(exc)) from exc


def _symbol(value: object, allowed: frozenset[str] = SYMBOLS) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ExecutionTapeError('UNSUPPORTED_CONTRACT')
    return value


def _clocks(payload: dict, received_at_ms: int) -> tuple[int, int]:
    event = _integer(payload['E'], 'EVENT_TIME')
    transaction = _integer(payload['T'], 'TRANSACTION_TIME')
    received = _integer(received_at_ms, 'RECEIVE_TIME')
    if not transaction <= event <= received:
        raise ExecutionTapeError('INCONSISTENT_FEED_CLOCKS')
    return event, transaction


def _levels(value: object, *, snapshot: bool = False) -> tuple[tuple[Decimal, Decimal], ...]:
    if (not isinstance(value, list) or len(value) > MAX_SNAPSHOT_LEVELS
            or snapshot and not value):
        raise ExecutionTapeError('INVALID_DEPTH_LEVEL_COUNT')
    result = []
    seen = set()
    for row in value:
        if not isinstance(row, list) or len(row) != 2:
            raise ExecutionTapeError('INVALID_DEPTH_LEVEL')
        price, size = _amount(row[0], positive=True), _amount(row[1], positive=snapshot)
        if price in seen:
            raise ExecutionTapeError('DUPLICATE_DEPTH_PRICE')
        seen.add(price)
        result.append((price, size))
    return tuple(result)


def _uncrossed(bids: dict, asks: dict) -> None:
    if not bids or not asks or max(bids) >= min(asks):
        raise ExecutionTapeError('EMPTY_OR_CROSSED_BOOK')
    if max(len(bids), len(asks)) > MAX_BOOK_LEVELS:
        raise ExecutionTapeError('BOOK_LEVEL_BUDGET_EXHAUSTED')


@dataclass(frozen=True)
class DepthReceipt:
    status: str
    last_update_id: int
    event_hash: str
    available_at_ms: int


@dataclass(frozen=True)
class DepthView:
    symbol: str
    last_update_id: int
    event_time_ms: int
    transaction_time_ms: int
    received_at_ms: int
    available_at_ms: int
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    snapshot_hash: str
    source_hash: str
    initial_bid_floor: Decimal
    initial_ask_ceiling: Decimal


class BtcDepthBook:
    """One snapshot epoch plus contiguous diff updates, with immutable views."""

    _supported_symbols = SYMBOLS

    def __init__(self, symbol: str, snapshot: dict, *, received_at_ms: int):
        self.symbol = _symbol(symbol, self._supported_symbols)
        self._blocked: str | None = None
        self._synchronized = False
        self._seen: dict[int, str] = {}
        self._last_received: int | None = None
        try:
            if not isinstance(snapshot, dict):
                raise ExecutionTapeError('INVALID_SNAPSHOT')
            self._cursor = _integer(snapshot['lastUpdateId'], 'SNAPSHOT_UPDATE_ID')
            self._event, self._transaction = _clocks(snapshot, received_at_ms)
            bids, asks = _levels(snapshot['bids'], snapshot=True), _levels(snapshot['asks'], snapshot=True)
            if list(bids) != sorted(bids, reverse=True) or list(asks) != sorted(asks):
                raise ExecutionTapeError('UNORDERED_SNAPSHOT')
            self._bids, self._asks = dict(bids), dict(asks)
            _uncrossed(self._bids, self._asks)
            self._bid_floor, self._ask_ceiling = min(self._bids), max(self._asks)
            self._received = self._available = received_at_ms
            self._snapshot_hash = payload_hash({'symbol': symbol, 'payload': snapshot,
                                                'received_at_ms': received_at_ms})
            self._source_hash = self._snapshot_hash
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ExecutionTapeError):
                raise
            raise ExecutionTapeError('INVALID_SNAPSHOT') from exc

    def apply(self, payload: dict, *, received_at_ms: int) -> DepthReceipt:
        if self._blocked:
            raise ExecutionTapeError(f'BLOCKED:{self._blocked}')
        try:
            return self._apply(payload, received_at_ms=received_at_ms)
        except (KeyError, TypeError, ValueError) as exc:
            self._blocked = str(exc) if isinstance(exc, ExecutionTapeError) else 'INVALID_DEPTH_EVENT'
            raise ExecutionTapeError(self._blocked) from exc

    def _apply(self, payload: dict, *, received_at_ms: int) -> DepthReceipt:
        if not isinstance(payload, dict) or payload.get('e') != 'depthUpdate' or payload.get('s') != self.symbol:
            raise ExecutionTapeError('DEPTH_CONTRACT_MISMATCH')
        event, transaction = _clocks(payload, received_at_ms)
        if self._last_received is not None and received_at_ms < self._last_received:
            raise ExecutionTapeError('RECEIVE_TIME_REVERSED')
        self._last_received = received_at_ms
        first, last, previous = (_integer(payload[k], k.upper()) for k in ('U', 'u', 'pu'))
        if first > last or previous >= last:
            raise ExecutionTapeError('INVALID_DEPTH_UPDATE_RANGE')
        bids, asks = _levels(payload['b']), _levels(payload['a'])
        identity = payload_hash(payload)
        if last in self._seen:
            if identity != self._seen[last]:
                raise ExecutionTapeError('CONFLICTING_DEPTH_ID')
            return DepthReceipt('DUPLICATE', self._cursor, identity, self._available)
        if not self._synchronized:
            if last < self._cursor:
                return DepthReceipt('BEFORE_SNAPSHOT', self._cursor, identity, self._available)
            if not first <= self._cursor <= last:
                raise ExecutionTapeError('BOOTSTRAP_GAP')
        else:
            if last <= self._cursor:
                raise ExecutionTapeError('UNVERIFIABLE_OLD_DEPTH_EVENT')
            if previous != self._cursor:
                raise ExecutionTapeError('DEPTH_SEQUENCE_GAP')
            if transaction < self._transaction or event < self._event:
                raise ExecutionTapeError('DEPTH_TIME_REVERSED')
        if len(self._seen) >= MAX_EVENTS:
            raise ExecutionTapeError('DEPTH_EVENT_BUDGET_EXHAUSTED')
        staged_bids, staged_asks = dict(self._bids), dict(self._asks)
        for updates, destination in ((bids, staged_bids), (asks, staged_asks)):
            for price, size in updates:
                if size == 0:
                    destination.pop(price, None)
                else:
                    destination[price] = size
        _uncrossed(staged_bids, staged_asks)
        available = max(self._available, received_at_ms)
        source = payload_hash({'parent': self._source_hash, 'event_hash': identity,
                               'received_at_ms': received_at_ms, 'available_at_ms': available})
        self._bids, self._asks = staged_bids, staged_asks
        self._cursor, self._event, self._transaction = last, event, transaction
        self._received, self._available, self._source_hash = received_at_ms, available, source
        self._seen[last] = identity
        self._synchronized = True
        return DepthReceipt('APPLIED', last, identity, available)

    def view(self, *, as_of_ms: int) -> DepthView:
        _integer(as_of_ms, 'AS_OF_TIME')
        if self._blocked:
            raise ExecutionTapeError(f'BLOCKED:{self._blocked}')
        if not self._synchronized:
            raise ExecutionTapeError('BOOK_NOT_SYNCHRONIZED')
        if as_of_ms < self._available:
            raise ExecutionTapeError('BOOK_NOT_AVAILABLE')
        return DepthView(self.symbol, self._cursor, self._event, self._transaction,
                         self._received, self._available, tuple(sorted(self._bids.items(), reverse=True)),
                         tuple(sorted(self._asks.items())), self._snapshot_hash, self._source_hash,
                         self._bid_floor, self._ask_ceiling)


@dataclass(frozen=True)
class PublicTrade:
    symbol: str
    aggregate_id: int
    first_trade_id: int
    last_trade_id: int
    price: Decimal
    quantity: Decimal
    aggressor_side: str
    transaction_time_ms: int
    event_time_ms: int
    received_at_ms: int
    source_hash: str
    normal_quantity: Decimal | None = None


class PublicTradeCursor:
    """Retain individual aggregate events; gaps require a new research epoch."""

    _supported_symbols = SYMBOLS

    def __init__(self, symbol: str):
        self.symbol = _symbol(symbol, self._supported_symbols)
        self._seen: dict[int, str] = {}
        self._previous: PublicTrade | None = None
        self._last_received: int | None = None
        self._blocked: str | None = None

    def apply(self, payload: dict, *, received_at_ms: int) -> PublicTrade | None:
        if self._blocked:
            raise ExecutionTapeError(f'BLOCKED:{self._blocked}')
        try:
            return self._apply(payload, received_at_ms=received_at_ms)
        except (KeyError, TypeError, ValueError) as exc:
            self._blocked = str(exc) if isinstance(exc, ExecutionTapeError) else 'INVALID_TRADE_EVENT'
            raise ExecutionTapeError(self._blocked) from exc

    def _apply(self, payload: dict, *, received_at_ms: int) -> PublicTrade | None:
        if not isinstance(payload, dict) or payload.get('e') != 'aggTrade' or payload.get('s') != self.symbol:
            raise ExecutionTapeError('TRADE_CONTRACT_MISMATCH')
        event, transaction = _clocks(payload, received_at_ms)
        if self._last_received is not None and received_at_ms < self._last_received:
            raise ExecutionTapeError('RECEIVE_TIME_REVERSED')
        self._last_received = received_at_ms
        aggregate, first, last = (_integer(payload[k], k.upper()) for k in ('a', 'f', 'l'))
        if first > last:
            raise ExecutionTapeError('INVALID_TRADE_RANGE')
        price, size = _amount(payload['p'], positive=True), _amount(payload['q'], positive=True)
        normal = _amount(payload['nq'], positive=False) if 'nq' in payload else None
        if normal is not None and normal>size:
            raise ExecutionTapeError('NORMAL_QUANTITY_EXCEEDS_TOTAL')
        if type(payload['m']) is not bool:
            raise ExecutionTapeError('INVALID_MAKER_FLAG')
        identity = payload_hash(payload)
        if aggregate in self._seen:
            if identity != self._seen[aggregate]:
                raise ExecutionTapeError('CONFLICTING_TRADE_ID')
            return None
        previous = self._previous
        if previous is not None:
            if aggregate != previous.aggregate_id + 1:
                raise ExecutionTapeError('TRADE_SEQUENCE_GAP')
            if first <= previous.last_trade_id:
                raise ExecutionTapeError('TRADE_RANGE_OVERLAP')
            if transaction < previous.transaction_time_ms or event < previous.event_time_ms:
                raise ExecutionTapeError('TRADE_TIME_REVERSED')
        if len(self._seen) >= MAX_EVENTS:
            raise ExecutionTapeError('TRADE_EVENT_BUDGET_EXHAUSTED')
        result = PublicTrade(self.symbol, aggregate, first, last, price, size,
                             'SELL' if payload['m'] else 'BUY', transaction, event, received_at_ms,
                             payload_hash({'event_hash': identity, 'received_at_ms': received_at_ms}),normal)
        self._seen[aggregate] = identity
        self._previous = result
        return result
