"""Phase 2 — data-moat core: PIT store, survivorship-free universe, versioning."""
from quantos.contracts.data import Bar
from services.data.pit_store import PITStore
from services.data.universe import PITUniverse
from services.data.versioning import build_dataset_version, content_hash_of


def _bar(et, kt, close=10.0):
    return Bar(instrument_id="X", event_time=et, knowledge_time=kt,
               open=close, high=close, low=close, close=close)


# --- PIT store: no lookahead on read -----------------------------------------

def test_as_of_excludes_future_knowledge():
    s = PITStore()
    s.add("X", _bar(100, 100))           # knowable at t=100
    s.add("X", _bar(200, 250, 11.0))     # event at 200 but only knowable at 250 (e.g. restatement)
    # As of t=210 we must NOT see the record that only became knowable at 250.
    rows = s.as_of("X", as_of_time=210)
    assert len(rows) == 1 and rows[0].event_time == 100
    # As of t=300 both are knowable.
    assert len(s.as_of("X", as_of_time=300)) == 2


def test_latest_as_of_and_event_cap():
    s = PITStore()
    for et in (100, 200, 300):
        s.add("X", _bar(et, et, close=et / 10))
    assert s.latest_as_of("X", as_of_time=250).event_time == 200
    assert s.latest_as_of("X", as_of_time=1000, event_time_max=150).event_time == 100
    assert s.latest_as_of("X", as_of_time=50) is None


def test_as_of_is_deterministically_ordered():
    s = PITStore()
    s.add("X", _bar(300, 300)); s.add("X", _bar(100, 100)); s.add("X", _bar(200, 200))
    times = [r.event_time for r in s.as_of("X", 1000)]
    assert times == [100, 200, 300]


# --- Survivorship-free universe ----------------------------------------------

def test_delisted_name_still_present_as_of_historical_date():
    u = PITUniverse()
    u.add_membership("DEAD", "2018-01-01", "2020-06-30")  # later delisted
    u.add_membership("LIVE", "2018-01-01", None)
    # As of a date inside DEAD's membership it MUST appear (no survivorship bias).
    assert u.members_as_of("2019-05-01") == {"DEAD", "LIVE"}
    # Today, DEAD is gone.
    assert u.members_as_of("2026-01-01") == {"LIVE"}


def test_delisted_then_readded_ticker():
    u = PITUniverse()
    u.add_membership("Z", "2015-01-01", "2017-12-31")
    u.add_membership("Z", "2021-01-01", None)
    assert u.is_member_as_of("Z", "2016-06-01")
    assert not u.is_member_as_of("Z", "2019-06-01")  # gap
    assert u.is_member_as_of("Z", "2022-06-01")


# --- Reproducible dataset versioning -----------------------------------------

def test_content_hash_is_order_independent_and_reproducible():
    a = [_bar(100, 100), _bar(200, 200)]
    b = [_bar(200, 200), _bar(100, 100)]
    assert content_hash_of(a) == content_hash_of(b)


def test_build_dataset_version_metadata():
    dv = build_dataset_version("eod_cn", "v1", [_bar(100, 100), _bar(200, 200)],
                               created_at=123, source="synthetic")
    assert dv.row_count == 2 and dv.point_in_time is True and dv.content_hash
    # same data -> same hash
    dv2 = build_dataset_version("eod_cn", "v1", [_bar(200, 200), _bar(100, 100)], created_at=999)
    assert dv2.content_hash == dv.content_hash
