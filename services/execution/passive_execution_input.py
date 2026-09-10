"""Offline raw public tape validation and separate venue/client clock views."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from decimal import Decimal
import hashlib
import json
from services.data.binance_execution_tape import (
    BtcDepthBook, PublicTradeCursor, PublicTrade, ExecutionTapeError, SYMBOLS, MAX_EVENTS, payload_hash,
)
from services.governance.json_integrity import load_json_without_duplicate_keys

SCHEMA = 'BINANCE_RAW_EXECUTION_TAPE_V1'
MAX_RAW_BYTES = 4_000_000
MAX_MESSAGE_BYTES = 262144


def stream_urls(symbol):
    if not isinstance(symbol,str) or symbol not in SYMBOLS:
        raise ValueError('BTCUSDC or BTCUSDT required')
    name = symbol.lower()
    return {'public': f'wss://fstream.binance.com/public/stream?streams={name}@depth@100ms',
            'market': f'wss://fstream.binance.com/market/stream?streams={name}@aggTrade/{name}@markPrice@1s'}


def _mark(payload, symbol, received):
    if payload.get('e') != 'markPriceUpdate' or payload.get('s') != symbol:
        raise ExecutionTapeError('MARK_CONTRACT_MISMATCH')
    event, next_settlement = payload.get('E'), payload.get('T')
    if type(event) is not int or not 0 <= event <= received or type(next_settlement) is not int or next_settlement <= event:
        raise ExecutionTapeError('INVALID_MARK_CLOCK')
    values = {}
    for name in ('p', 'i', 'r'):
        value = payload.get(name)
        if not isinstance(value, str) or not 1 <= len(value) <= 64:
            raise ExecutionTapeError('INVALID_MARK_DECIMAL')
        amount = Decimal(value)
        if not amount.is_finite() or name in ('p', 'i') and amount <= 0 or name == 'r' and abs(amount) >= 1:
            raise ExecutionTapeError('INVALID_MARK_AMOUNT')
        values[name] = value
    return {'event_time_ms': event, 'received_at_ms': received, 'mark_price': values['p'],
            'index_price': values['i'], 'indicative_funding_rate': values['r'],
            'next_settlement_ms': next_settlement, 'settled_funding_evidence': False}


def _number(value):
    if not isinstance(value, Decimal):
        return value
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def analyze(document):
    """Validate every retained packet. Missing input remains an explicit result."""
    counts = {'depth_updates': 0, 'aggregate_trades': 0, 'mark_updates': 0, 'duplicates': 0}
    failures, books, trades, marks = [], [], [], []
    symbol = document.get('symbol') if isinstance(document, dict) else None
    if not isinstance(symbol,str):symbol=None
    report = {'status': 'UNUSABLE_EXECUTION_INPUT', 'stage': 'P5_INPUT', 'symbol': symbol,
              'quote': symbol[3:] if symbol in SYMBOLS else None, 'counts': counts,
              'failures': failures, 'book': None, 'book_samples': books, 'trade_samples': trades,
              'mark': None, 'input_hash': None, 'venue_receipts_verified': False,
              'queue_model_calibrated': False, 'economic_eligible': False, 'authoritative': False,
              'limitations': ['PUBLIC_MARKET_BY_PRICE_NOT_INDIVIDUAL_QUEUE_POSITION',
                             'NO_REAL_ORDER_RECEIPTS_IN_PUBLIC_TAPE',
                             'DECLARED_LOCAL_RECEIVE_CLOCK_NOT_INDEPENDENT_CLOCK_ATTESTATION',
                             'RAW_TRADE_ID_GAPS_DO_NOT_IMPLY_AGGREGATE_ID_GAPS']}
    try:
        if not isinstance(document, dict):
            raise ExecutionTapeError('INVALID_TAPE')
        original = document
        document = {k: v for k, v in original.items() if k != 'result_hash'}
        if 'result_hash' in original and original['result_hash'] != hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest():
            raise ExecutionTapeError('TAPE_HASH_MISMATCH')
        report['input_hash'] = payload_hash(document)
        if document.get('schema') != SCHEMA or document.get('stream_urls') != stream_urls(symbol):
            raise ExecutionTapeError('TAPE_CONTRACT_MISMATCH')
        start, end = document['started_at_ms'], document['ended_at_ms']
        if (type(start) is not int or type(end) is not int or not 0 <= start <= end <= 8_640_000_000_000_000
                or end-start>150000):
            raise ExecutionTapeError('INVALID_CAPTURE_WINDOW')
        failures.extend({'reason': row['reason'], 'route': row.get('route')}
                        for row in document.get('failures', [])[:32])
        connections = document.get('connections')
        if (not isinstance(connections, dict) or set(connections) != {'public', 'market'}
                or any(not isinstance(row, dict) or row.get('status') != 'DURATION_REACHED'
                       for row in connections.values())):
            failures.append({'reason': 'INCOMPLETE_STREAM_COVERAGE'})
        snapshot = document.get('snapshot')
        if snapshot is None:
            raise ExecutionTapeError('NO_SNAPSHOT')
        received = snapshot['received_at_ms']
        if type(received) is not int or not start <= received <= end:
            raise ExecutionTapeError('SNAPSHOT_OUTSIDE_CAPTURE')
        raw = snapshot['raw_utf8']
        if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_MESSAGE_BYTES:
            raise ExecutionTapeError('INVALID_SNAPSHOT_RESPONSE')
        book = BtcDepthBook(symbol, load_json_without_duplicate_keys(raw), received_at_ms=received)
        cursor = PublicTradeCursor(symbol)
        rows = document['messages']
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_EVENTS:
            raise ExecutionTapeError('INVALID_MESSAGE_COUNT')
        expected = {symbol.lower()+'@depth@100ms': 'public', symbol.lower()+'@aggTrade': 'market',
                    symbol.lower()+'@markPrice@1s': 'market'}
        total_bytes, previous_receive, book_bucket = 0, start, None
        for index, row in enumerate(rows):
            received = row['received_at_ms']
            raw = row['raw_utf8']
            if (type(row.get('sequence')) is not int or row['sequence'] != index or type(received) is not int
                    or not previous_receive <= received <= end or not isinstance(raw, str)):
                raise ExecutionTapeError('INVALID_RECEIVE_SEQUENCE')
            previous_receive = received
            total_bytes += len(raw.encode('utf-8'))
            if len(raw.encode('utf-8')) > MAX_MESSAGE_BYTES or total_bytes > MAX_RAW_BYTES:
                raise ExecutionTapeError('RAW_BYTE_BUDGET_EXHAUSTED')
            envelope = load_json_without_duplicate_keys(raw)
            stream = envelope.get('stream')
            if stream not in expected or row['route'] != expected[stream]:
                raise ExecutionTapeError('PUBLIC_STREAM_IDENTITY_MISMATCH')
            payload = envelope['data']
            if stream.endswith('@depth@100ms'):
                receipt = book.apply(payload, received_at_ms=received)
                if receipt.status == 'APPLIED':
                    counts['depth_updates'] += 1
                    view = book.view(as_of_ms=receipt.available_at_ms)
                    value = {'available_at_ms': view.available_at_ms, 'event_time_ms': view.event_time_ms,
                             'transaction_time_ms': view.transaction_time_ms, 'last_update_id': view.last_update_id,
                             'bids': [[_number(p), _number(q)] for p, q in view.bids[:10]],
                             'asks': [[_number(p), _number(q)] for p, q in view.asks[:10]],
                             'source_hash': view.source_hash}
                    report['book'] = value
                    bucket = view.available_at_ms // 1000
                    if book_bucket != bucket:
                        books.append(value)
                        book_bucket = bucket
                elif receipt.status == 'DUPLICATE':
                    counts['duplicates'] += 1
            elif stream.endswith('@aggTrade'):
                value = cursor.apply(payload, received_at_ms=received)
                if value is None:
                    counts['duplicates'] += 1
                else:
                    counts['aggregate_trades'] += 1
                    trades.append({k: _number(v) for k, v in asdict(value).items()})
                    del trades[:-20]
            else:
                value = _mark(payload, symbol, received)
                if marks and value['event_time_ms'] < marks[-1]['event_time_ms']:
                    raise ExecutionTapeError('MARK_TIME_REVERSED')
                counts['mark_updates'] += 1
                marks[:] = [value]
        report['mark'] = marks[-1] if marks else None
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        failures.append({'reason': str(exc) if isinstance(exc, ExecutionTapeError) else 'MALFORMED_EXECUTION_INPUT'})
    for field, reason in [('depth_updates', 'NO_SYNCHRONIZED_DEPTH'), ('aggregate_trades', 'NO_TRADES'),
                          ('mark_updates', 'NO_MARK_PRICE')]:
        if counts[field] == 0:
            failures.append({'reason': reason})
    if not failures:
        report['status'] = 'PUBLIC_EXECUTION_INPUT_VALIDATED'
    return report


@dataclass(frozen=True)
class BookUpdate:
    transaction_ms: int
    available_ms: int
    update_id: int
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    bootstrap: bool
    source_hash: str


@dataclass(frozen=True)
class VisibleBook:
    available_ms: int
    transaction_ms: int
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]


@dataclass(frozen=True)
class ExecutionTape:
    symbol: str
    input_hash: str
    start_ms: int
    end_ms: int
    books: tuple[BookUpdate, ...]
    trades: tuple[PublicTrade, ...]
    bid_floor: Decimal
    ask_ceiling: Decimal

    def visible_book(self, as_of_ms: int) -> VisibleBook:
        if type(as_of_ms) is not int:
            raise ExecutionTapeError('INVALID_AS_OF')
        bids, asks, last = {}, {}, None
        for event in self.books:
            if event.available_ms > as_of_ms:
                break
            apply_levels(bids, event.bids)
            apply_levels(asks, event.asks)
            last = event
        if last is None:
            raise ExecutionTapeError('BOOK_NOT_AVAILABLE')
        return VisibleBook(last.available_ms, last.transaction_ms,
                           tuple(sorted(bids.items(), reverse=True)), tuple(sorted(asks.items())))


def apply_levels(side, updates):
    for price, quantity in updates:
        if quantity:
            side[price] = quantity
        else:
            side.pop(price, None)


def compile_tape(document) -> ExecutionTape:
    """Frozen broker input; its venue events are never an adaptive strategy feed.

    The joint window starts after the snapshot and all required streams became
    observable locally, and stops at the earliest final venue stream event.
    Compact absolute updates avoid storing a full 1,000-level book per packet.
    """
    quality = analyze(document)
    if quality['status'] != 'PUBLIC_EXECUTION_INPUT_VALIDATED':
        raise ExecutionTapeError('UNUSABLE_EXECUTION_INPUT')
    connections = document['connections']
    for route, connection in connections.items():
        times = [document['started_at_ms'], connection.get('started_at_ms'),
                 connection.get('connected_at_ms'), connection.get('closed_at_ms'), document['ended_at_ms']]
        if any(type(t) is not int for t in times) or times != sorted(times):
            raise ExecutionTapeError('INVALID_CONNECTION_CLOCK')
        rows = [r for r in document['messages'] if r['route'] == route]
        if (type(connection.get('messages')) is not int or connection['messages'] != len(rows)
                or any(not times[2] <= r['received_at_ms'] <= times[3] for r in rows)):
            raise ExecutionTapeError('CONNECTION_RECEIPT_MISMATCH')
    snapshot = document['snapshot']
    book = BtcDepthBook(document['symbol'], load_json_without_duplicate_keys(snapshot['raw_utf8']),
                        received_at_ms=snapshot['received_at_ms'])
    cursor = PublicTradeCursor(document['symbol'])
    books, trades, marks = [], [], []
    initial = None
    for row in document['messages']:
        envelope = load_json_without_duplicate_keys(row['raw_utf8'])
        payload, received = envelope['data'], row['received_at_ms']
        if payload['e'] == 'depthUpdate':
            receipt = book.apply(payload, received_at_ms=received)
            if receipt.status == 'APPLIED':
                view = book.view(as_of_ms=receipt.available_at_ms)
                initial = initial or view
                bootstrap = not books
                bids = view.bids if bootstrap else tuple((Decimal(p), Decimal(q)) for p, q in payload['b'])
                asks = view.asks if bootstrap else tuple((Decimal(p), Decimal(q)) for p, q in payload['a'])
                books.append(BookUpdate(view.transaction_time_ms, view.available_at_ms,
                    view.last_update_id, bids, asks, bootstrap, view.source_hash))
        elif payload['e'] == 'aggTrade':
            value = cursor.apply(payload, received_at_ms=received)
            if value is not None:
                trades.append(value)
        else:
            marks.append(_mark(payload, document['symbol'], received))
    start = max(books[0].available_ms, trades[0].received_at_ms, marks[0]['received_at_ms'])
    end = min(books[-1].transaction_ms, trades[-1].transaction_time_ms, marks[-1]['event_time_ms'])
    if start >= end:
        raise ExecutionTapeError('NO_JOINT_EXECUTION_WINDOW')
    return ExecutionTape(document['symbol'], quality['input_hash'], start, end,
                         tuple(books), tuple(trades), initial.initial_bid_floor, initial.initial_ask_ceiling)
