"""Build PIT research windows from retained Binance aggregate-trade packets.

Coverage is relative to the supplied contiguous aggregate stream, not signed
proof of capture completeness. Each aggregate's entire quantity belongs to its
published transaction timestamp; this is not a reconstruction of raw matches.
"""
from decimal import Decimal, localcontext
import hashlib
import json

from services.research.ports.gold_tape import TradeCursorFactory

# Independent research input budget, also enforced by the venue adapter.
MAX_EVENTS = 20000
from services.research.gold_signal import Window


class WindowError(ValueError):
    pass


def _time(value):
    return type(value) is int and 0<=value<=2**63-1


def build_window(packets, *, start_ms, end_ms, as_of_ms, max_age_ms, cursor_factory: TradeCursorFactory):
    """Require a predecessor and a received right-boundary watermark.

Future packets are not inspected. Missing/empty windows cannot be turned into
fabricated flat bars; each call either yields one verifiable window or rejects.
"""
    if not (all(_time(t) for t in (start_ms,end_ms,as_of_ms))
            and start_ms<end_ms<=as_of_ms and type(max_age_ms) is int and max_age_ms>0
            and as_of_ms-end_ms<=max_age_ms):
        raise WindowError('INVALID_OR_STALE_WINDOW_TIME')
    if not isinstance(packets,list) or len(packets)>MAX_EVENTS:
        raise WindowError('INVALID_PACKET_COUNT')
    cursor=cursor_factory()
    predecessor=False
    last_received=None
    selected=[]
    hashes=[]
    watermark=None
    for packet in packets:
        if not isinstance(packet,dict) or set(packet)!={'received_at_ms','payload'}:
            raise WindowError('INVALID_PACKET')
        received=packet['received_at_ms']
        if not _time(received) or (last_received is not None and received<last_received):
            raise WindowError('INVALID_RECEIVE_ORDER')
        if received>as_of_ms:
            break
        last_received=received
        try:
            trade=cursor.apply(packet['payload'],received_at_ms=received)
        except ValueError:
            raise WindowError('INVALID_AGGREGATE_SEQUENCE') from None
        if trade is None:
            continue
        hashes.append(trade.source_hash)
        if trade.transaction_time_ms<start_ms:
            predecessor=True
        elif trade.transaction_time_ms<end_ms:
            if not predecessor:
                raise WindowError('MISSING_LEFT_COVERAGE')
            selected.append(trade)
        else:
            watermark=trade
            break
    if not predecessor or watermark is None:
        raise WindowError('MISSING_WINDOW_COVERAGE')
    if not selected:
        raise WindowError('NO_WINDOW_TRADES')
    # The source parser bounds decimal strings to 64 chars and rows to 20k.
    with localcontext() as context:
        context.prec=150
        buy=sum((t.quantity for t in selected if t.aggressor_side=='BUY'),Decimal(0))
        sell=sum((t.quantity for t in selected if t.aggressor_side=='SELL'),Decimal(0))
    source=hashlib.sha256(json.dumps({'schema':'GOLD_AGGREGATE_T_WINDOW_V1',
        'start_ms':start_ms,'end_ms':end_ms,'source_hashes':hashes},
        sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return Window(symbol='XAUUSDT',start_ms=start_ms,end_ms=end_ms,
        received_at_ms=watermark.received_at_ms,
        low=min(t.price for t in selected),high=max(t.price for t in selected),
        close=selected[-1].price,buy_quantity=buy,sell_quantity=sell,
        source_hash=source,complete=True)
