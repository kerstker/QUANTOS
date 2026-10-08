import json
from services.console.api import market_dashboard as dashboard
from services.data.binance_realtime_store import BinanceRealtimeStore


def snapshot(at, **extra):
    return {'instrument_id':'BTCUSDT','provider':'BINANCE_PUBLIC_USDM_WEBSOCKET',
            'event_time_ms':at,'observed_at_ms':at,'received_at_ms':at,'processed_at_ms':at,
            'kline_1m':{'open_time_ms':60000,'close_time_ms':119999,'open':100,'high':105,'low':99,'close':102,'volume':12,'closed':True},
            **extra}


def test_missing_store_is_not_created(tmp_path):
    result=dashboard.market_payload(tmp_path/'absent.db','BTCUSDT',now_ms=180000)
    assert result['status']=='UNAVAILABLE'
    assert result['candles']==[]
    assert not (tmp_path/'absent.db').exists()

def test_closed_candle_survives_next_open_update_before_snapshot_flush(tmp_path):
    from tools.binance_realtime_stream import StreamState
    state=StreamState(('BTC',))
    def frame(opened,closed,event):
        return {'stream':'btcusdt@kline_1m','data':{'e':'kline','s':'BTCUSDT','E':event,
            'k':{'t':opened,'T':opened+59999,'o':'100','h':'105','l':'99','c':'102',
                 'v':'12','q':'1200','n':10,'V':'6','Q':'600','x':closed}}}
    state.handle(frame(60000,True,120000),received_at_ms=120010)
    state.handle(frame(120000,False,120250),received_at_ms=120260)
    row=state.snapshots(125000)['BTCUSDT']
    assert row['kline_1m']['closed'] is False
    assert row['last_closed_kline_1m']['received_at_ms']==120010
    path=tmp_path/'market.db'
    store=BinanceRealtimeStore(str(path),create=True)
    try:store.append_snapshot(row)
    finally:store.close()
    result=dashboard.market_payload(path,'BTCUSDT',now_ms=130000)
    assert len(result['candles'])==1 and result['candles'][0]['time']==60000


def test_deduplicates_candles_and_hides_stale_orderbook(tmp_path):
    path=tmp_path/'market.db';store=BinanceRealtimeStore(str(path),create=True)
    for at in [120000,125000]:
        store.append_snapshot(snapshot(at,depth20={'event_time_ms':at,'received_at_ms':at,'cursor_state':'CONTIGUOUS','bids':[[100,2]],'asks':[[101,3]]}))
    store.close()
    result=dashboard.market_payload(path,'BTCUSDT',now_ms=130000)
    assert len(result['candles'])==1
    assert result['depth']['bids']==[[100,2]]
    stale=dashboard.market_payload(path,'BTCUSDT',now_ms=190000)
    assert stale['status']=='STALE' and stale['depth'] is None
    assert len(stale['candles'])==1


def test_future_and_other_symbol_do_not_supply_chart(tmp_path):
    path=tmp_path/'market.db';store=BinanceRealtimeStore(str(path),create=True)
    store.append_snapshot(snapshot(200000));store.close()
    assert dashboard.market_payload(path,'BTCUSDT',now_ms=190000)['candles']==[]
    assert dashboard.market_payload(path,'BTCUSDC',now_ms=210000)['candles']==[]


def test_corrupt_payload_fails_closed(tmp_path):
    import sqlite3
    path=tmp_path/'market.db';store=BinanceRealtimeStore(str(path),create=True)
    store.append_snapshot(snapshot(120000));store.close()
    with sqlite3.connect(path) as db:
        db.execute('DROP TRIGGER binance_market_snapshots_no_update')
        db.execute('UPDATE binance_market_snapshots SET payload_json=?',(json.dumps(snapshot(120000,mark={'mark_price':999})),))
    assert dashboard.market_payload(path,'BTCUSDT',now_ms=130000)['status']=='CORRUPT_OR_UNREADABLE'


def test_closed_candle_must_be_available_at_its_own_snapshot(tmp_path):
    path=tmp_path/'market.db'
    store=BinanceRealtimeStore(str(path),create=True)
    store.append_snapshot(snapshot(90000))
    store.close()
    assert dashboard.market_payload(path,'BTCUSDT',now_ms=130000)['status']=='CORRUPT_OR_UNREADABLE'


def test_orderbook_requires_explicit_continuity_and_nonfuture_event(tmp_path):
    path=tmp_path/'market.db'
    store=BinanceRealtimeStore(str(path),create=True)
    store.append_snapshot(snapshot(120000,depth20={'event_time_ms':140000,'received_at_ms':120000,'cursor_state':'CONTIGUOUS','bids':[[100,2]],'asks':[[101,3]]}))
    store.close()
    assert dashboard.market_payload(path,'BTCUSDT',now_ms=130000)['depth'] is None


def test_malformed_nested_payload_is_explicitly_unreadable(tmp_path):
    path=tmp_path/'market.db';store=BinanceRealtimeStore(str(path),create=True)
    store.append_snapshot(snapshot(120000,mark=['invalid']))
    store.close()
    assert dashboard.market_payload(path,'BTCUSDT',now_ms=130000)['status']=='CORRUPT_OR_UNREADABLE'
