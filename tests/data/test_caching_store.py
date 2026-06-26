"""CachingPITStore must be a BIT-IDENTICAL read-through view of SqlitePITStore — same as_of /
latest_as_of / fetch_bars results and ordering, including restatements and event_time_max. It only
changes performance (loads once, serves from memory); if it ever diverged it would silently corrupt
every panel built on top of it, so this comparison is the safety net."""
from quantos.contracts.data import Bar
from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore


def _bar(inst, et, kt, close):
    return Bar(instrument_id=inst, event_time=et, knowledge_time=kt,
               open=close, high=close + 1, low=close - 1, close=close, volume=100)


def _seed():
    s = SqlitePITStore(":memory:", create=True)
    # restatements (same event_time, later knowledge), knowledge lags, multiple instruments
    s.add_many("BTC", [_bar("BTC", 10, 10, 100), _bar("BTC", 20, 20, 101),
                       _bar("BTC", 20, 35, 102), _bar("BTC", 30, 30, 103)])
    s.add_many("ETH", [_bar("ETH", 10, 25, 50), _bar("ETH", 15, 15, 51), _bar("ETH", 30, 30, 52)])
    s.add_many("SOL", [_bar("SOL", 5, 5, 9)])
    return s


def _tup(bars):
    return [(b.event_time, b.knowledge_time, b.close) for b in bars]


def test_keys_match():
    s = _seed()
    assert CachingPITStore(s).keys() == s.keys()


def test_as_of_and_latest_identical_across_times():
    s = _seed()
    c = CachingPITStore(s)
    for key in s.keys():
        for t in range(0, 60):
            assert _tup(c.as_of(key, t)) == _tup(s.as_of(key, t)), (key, t)
            ls, lc = s.latest_as_of(key, t), c.latest_as_of(key, t)
            assert (ls is None) == (lc is None)
            if ls is not None:
                assert (lc.event_time, lc.knowledge_time, lc.close) == (ls.event_time, ls.knowledge_time, ls.close)


def test_event_time_max_identical():
    s = _seed()
    c = CachingPITStore(s)
    for key in s.keys():
        for t in (15, 35, 60):
            for em in (None, 5, 10, 20, 25):
                assert _tup(c.as_of(key, t, em)) == _tup(s.as_of(key, t, em)), (key, t, em)


def test_fetch_bars_identical():
    s = _seed()
    c = CachingPITStore(s)
    for key in s.keys():
        for lo, hi in ((0, 100), (15, 25), (30, 30), (6, 9)):
            assert _tup(c.fetch_bars(key, lo, hi)) == _tup(s.fetch_bars(key, lo, hi)), (key, lo, hi)


def test_missing_key_returns_empty():
    c = CachingPITStore(_seed())
    assert c.as_of("NOPE", 100) == [] and c.latest_as_of("NOPE", 100) is None
    assert c.fetch_bars("NOPE", 0, 100) == []
