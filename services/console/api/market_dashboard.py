"""Bounded public-market chart projections. No network, orders or account data."""
from __future__ import annotations

import math
from pathlib import Path
import sqlite3
import time
from fastapi import APIRouter, Query
from quantos.runtime_paths import runtime_state_root
from services.data.binance_realtime_store import BinanceRealtimeStore, BinanceRealtimeStoreError

router = APIRouter()
SYMBOLS = ('BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','DOGEUSDT','ADAUSDT','AVAXUSDT','LINKUSDT','DOTUSDT','LTCUSDT','BCHUSDT','ATOMUSDT','UNIUSDT','BTCUSDC')


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('missing numeric market value')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('non-finite market value')
    return result


def market_payload(path: Path, symbol: str, *, now_ms: int) -> dict:
    result = dict(source='BACKEND_COMPUTED_SNAPSHOT',read_only=True,api_version='0.1.0',
                  data_provenance='REAL_PUBLIC_MARKET_DIAGNOSTIC',authoritative=False,
                  symbol=symbol,symbols=list(SYMBOLS),as_of_ms=now_ms,observed_at_ms=None,
                  status='UNAVAILABLE',snapshot_status='NO_SNAPSHOT',candles=[],samples=[],depth=None,
                  integrity='PAYLOAD_HASH_AND_IDENTITY_ONLY',reason='No retained observations for this instrument.')
    if not path.is_file():
        return result
    store = None
    try:
        store = BinanceRealtimeStore(str(path))
        rows = store.recent_for_instrument(symbol,as_of_ms=now_ms)
        rows = [r for r in rows if r.get('provider')=='BINANCE_PUBLIC_USDM_WEBSOCKET']
        if not rows:
            return result
        candles = {}
        samples = []
        for row in rows:
            bar = row.get('last_closed_kline_1m',row.get('kline_1m',{}))
            if not isinstance(bar, dict):
                raise ValueError('invalid candle shape')
            if bar.get('closed') is True and number(bar.get('close_time_ms')) < now_ms:
                opened, closed = number(bar['open_time_ms']), number(bar['close_time_ms'])
                received = number(bar.get('received_at_ms',row['observed_at_ms']))
                if opened < 0 or opened % 60000 or closed != opened + 59999 or closed > row['observed_at_ms']:
                    raise ValueError('invalid candle availability or interval')
                if received < closed or received > row['observed_at_ms']:
                    raise ValueError('invalid closed candle receive time')
                item = {k:number(bar[k]) for k in ('open','high','low','close','volume')}
                if not 0 < item['low'] <= min(item['open'],item['close']) <= max(item['open'],item['close']) <= item['high'] or item['volume'] < 0:
                    raise ValueError('invalid OHLCV range')
                item['time'] = number(bar['open_time_ms'])
                candles[item['time']] = item
            mark = row.get('mark',{})
            if not isinstance(mark, dict):
                raise ValueError('invalid mark shape')
            if mark and 0 <= row['observed_at_ms']-number(mark.get('received_at_ms')) <= 30000:
                samples.append({'time':row['observed_at_ms'],'mark':number(mark['mark_price']),
                                'index':number(mark['index_price']),'funding_rate':number(mark['funding_rate'])})
        latest=max(rows,key=lambda r:r['observed_at_ms'])
        fresh=0 <= now_ms-latest['observed_at_ms'] <= 30000
        depth=latest.get('depth20',{})
        if not isinstance(depth, dict):
            raise ValueError('invalid depth shape')
        book=None
        if fresh and depth and 0 <= now_ms-number(depth.get('received_at_ms')) <= 30000:
            if depth.get('cursor_state') in ('CONTIGUOUS','RECOVERED') and 0 <= number(depth.get('event_time_ms')) <= number(depth.get('received_at_ms')):
                book={side:[[number(p),number(q)] for p,q in depth[side][:20]] for side in ('bids','asks')}
                if any(p<=0 or q<0 for levels in book.values() for p,q in levels):
                    raise ValueError('invalid depth values')
                book['bids'].sort(reverse=True);book['asks'].sort()
                if book['bids'] and book['asks'] and book['bids'][0][0]>=book['asks'][0][0]:
                    raise ValueError('crossed depth')
        result.update(status='AVAILABLE' if fresh else 'STALE',snapshot_status='REAL' if fresh else 'STALE',
                      observed_at_ms=latest['observed_at_ms'],candles=[candles[t] for t in sorted(candles)][-500:],
                      samples=samples[-500:],depth=book,reason='Retained public observations; historical gaps are not filled.')
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,BinanceRealtimeStoreError):
        result.update(status='CORRUPT_OR_UNREADABLE',snapshot_status='CORRUPT_OR_UNREADABLE',reason='Market projection failed integrity or shape validation.')
    finally:
        if store is not None:
            store.close()
    return result


@router.get('/api/v1/console/market-dashboard')
def market_dashboard(symbol: str = Query('BTCUSDT',pattern='^(?:BTCUSDT|ETHUSDT|SOLUSDT|BNBUSDT|XRPUSDT|DOGEUSDT|ADAUSDT|AVAXUSDT|LINKUSDT|DOTUSDT|LTCUSDT|BCHUSDT|ATOMUSDT|UNIUSDT|BTCUSDC)$')):
    return market_payload(runtime_state_root()/'data/cache/binance_realtime_stream.db',symbol,now_ms=time.time_ns()//1000000)
