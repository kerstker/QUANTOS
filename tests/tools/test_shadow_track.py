"""tools/shadow_track.py(SHADOW 运行时驱动器,727 §12.2 Phase 7 S3)单测。

全部假 venue / 注入 signal_fn / tmp_path 账本——零真网络、零墙钟(now_ms 注入)。
"""
import json
import sqlite3

import pytest

from live.contracts import BookLevel, OrderBookSnapshot
from live.execution.shadow import ShadowLeg
from live.execution.shadow_store import SqliteShadowStore
from tools.shadow_track import (build_shadow_legs, build_shadow_readiness,
                                build_shadow_venue, run_once, run_shadow_pass,
                                shadow_venues)

_NOW = 1_700_000_000_000


def _book(instrument_id, *, as_of_ms=_NOW - 500, bid=100.0, ask=100.5, depth=5.0):
    return OrderBookSnapshot(instrument_id=instrument_id,
                             bids=(BookLevel(bid, depth),),
                             asks=(BookLevel(ask, depth),),
                             as_of_ms=as_of_ms, source="test")


class _FakeVenue:
    def __init__(self, books=None, raises=False):
        self._books = books or {}
        self._raises = raises

    def depth(self, instrument_id):
        if self._raises:
            raise RuntimeError("boom")
        return self._books.get(instrument_id)


class _NoDepthVenue:
    """无 depth 可选能力的 venue(hasattr 探测落空 -> BOOK_UNAVAILABLE)。"""


def _legs():
    return [ShadowLeg(instrument_id="BTC", side="BUY", quantity=1.0,
                      decision_price=100.0),
            ShadowLeg(instrument_id="ETH", side="SELL", quantity=2.0,
                      decision_price=100.0)]


def _store(tmp_path, name="shadow.db"):
    return SqliteShadowStore(str(tmp_path / name), create=True)


# --- build_shadow_legs ---

def test_build_shadow_legs_math_zero_skip_and_deterministic_order():
    legs = build_shadow_legs({"ETH": 0.5, "BTC": -0.25, "SOL": 0.0},
                             {"ETH": 2000.0, "BTC": 50_000.0}, notional=10_000.0)
    assert [l.instrument_id for l in legs] == ["BTC", "ETH"]     # 排序确定
    btc, eth = legs
    assert btc.side == "SELL" and btc.quantity == pytest.approx(0.05)
    assert eth.side == "BUY" and eth.quantity == pytest.approx(2.5)
    assert btc.decision_price == 50_000.0 and eth.decision_price == 2000.0


def test_build_shadow_legs_missing_price_fails_closed():
    with pytest.raises(ValueError, match="decision price"):
        build_shadow_legs({"BTC": 0.5}, {}, notional=10_000.0)


def test_build_shadow_legs_rejects_bad_notional():
    for bad in (0.0, -1.0, float("nan")):
        with pytest.raises(ValueError):
            build_shadow_legs({"BTC": 0.5}, {"BTC": 100.0}, notional=bad)


# --- run_shadow_pass ---

def test_run_shadow_pass_observes_appends_and_summarizes(tmp_path):
    store = _store(tmp_path)
    venue = _FakeVenue({"BTC": _book("BTC"), "ETH": _book("ETH")})
    summary = run_shadow_pass(venue=venue, store=store, strategy_id="crowd_contra",
                              legs=_legs(), now_ms=_NOW)
    assert summary["legs"] == 2 and summary["observations"] == 2
    assert summary["appended"] == 2 and summary["idempotent_replays"] == 0
    assert summary["unavailable"] == 0 and summary["stale"] == 0
    assert summary["executable_count"] == 2
    # BUY:vwap 100.5 vs 100 -> 50bps;SELL:vwap 100 vs 100 -> 0bps(卖 bid 100 无成本)
    assert summary["mean_abs_slippage_bps"] == pytest.approx(25.0)
    assert summary["run_label"] == f"crowd_contra:{_NOW}"
    assert store.count() == 2
    assert store.event_outbox_chain_valid()


def test_run_shadow_pass_idempotent_on_exact_replay(tmp_path):
    store = _store(tmp_path)
    venue = _FakeVenue({"BTC": _book("BTC"), "ETH": _book("ETH")})
    run_shadow_pass(venue=venue, store=store, strategy_id="s", legs=_legs(), now_ms=_NOW)
    again = run_shadow_pass(venue=venue, store=store, strategy_id="s", legs=_legs(),
                            now_ms=_NOW)
    assert again["appended"] == 0 and again["idempotent_replays"] == 2
    assert store.count() == 2


def test_run_shadow_pass_measures_latency_with_injected_clock(tmp_path):
    store = _store(tmp_path)
    venue = _FakeVenue({"BTC": _book("BTC"), "ETH": _book("ETH")})
    times = iter([0.0, 0.05, 1.0, 1.03])
    run_shadow_pass(venue=venue, store=store, strategy_id="s", legs=_legs(),
                    now_ms=_NOW, latency_clock=lambda: next(times))
    rows = store.records()
    lat = {r["payload"]["instrument_id"]: r["payload"]["latency_ms"] for r in rows}
    assert lat == {"BTC": 50, "ETH": 30}


def test_run_shadow_pass_without_depth_capability_records_book_unavailable(tmp_path):
    store = _store(tmp_path)
    summary = run_shadow_pass(venue=_NoDepthVenue(), store=store, strategy_id="s",
                              legs=_legs(), now_ms=_NOW)
    assert summary["unavailable"] == 2 and summary["executable_count"] == 0
    assert summary["mean_abs_slippage_bps"] is None
    rows = store.records()
    assert all(r["payload"]["walk"]["reason"] == "BOOK_UNAVAILABLE" for r in rows)


def test_run_shadow_pass_depth_raise_or_none_is_fail_soft(tmp_path):
    store = _store(tmp_path)
    raising = run_shadow_pass(venue=_FakeVenue(raises=True), store=store,
                              strategy_id="s", legs=_legs(), now_ms=_NOW)
    assert raising["unavailable"] == 2
    empty = run_shadow_pass(venue=_FakeVenue({}), store=store, strategy_id="s",
                            legs=_legs(), now_ms=_NOW + 1)
    assert empty["unavailable"] == 2
    assert store.event_outbox_chain_valid()


# --- build_shadow_readiness ---

def test_readiness_empty_store_is_no_observations(tmp_path):
    store = _store(tmp_path)
    readiness = build_shadow_readiness(store=store, now_ms=_NOW)
    assert readiness["shadow_ready"] is False
    assert readiness["blockers"] == ["NO_OBSERVATIONS"]
    assert readiness["observation_count"] == 0
    assert readiness["latest_as_of_ms"] is None


def test_readiness_all_green_is_ready(tmp_path):
    store = _store(tmp_path)
    venue = _FakeVenue({"BTC": _book("BTC"), "ETH": _book("ETH")})
    run_shadow_pass(venue=venue, store=store, strategy_id="s", legs=_legs(), now_ms=_NOW)
    readiness = build_shadow_readiness(store=store, now_ms=_NOW + 1_000)
    assert readiness == {
        "shadow_ready": True, "blockers": [], "observation_count": 2,
        "latest_recorded_at_ms": _NOW, "latest_as_of_ms": _NOW - 500,
        "chain_valid": True, "outbox_chain_valid": True,
        "max_obs_age_ms": 86_400_000,
    }


def test_readiness_stale_latest_observation_blocks(tmp_path):
    store = _store(tmp_path)
    venue = _FakeVenue({"BTC": _book("BTC"), "ETH": _book("ETH")})
    run_shadow_pass(venue=venue, store=store, strategy_id="s", legs=_legs(), now_ms=_NOW)
    readiness = build_shadow_readiness(store=store, now_ms=_NOW + 86_400_001)
    assert readiness["shadow_ready"] is False
    assert "LATEST_OBSERVATION_STALE" in readiness["blockers"]


def test_readiness_no_executable_observation_blocks(tmp_path):
    store = _store(tmp_path)
    run_shadow_pass(venue=_NoDepthVenue(), store=store, strategy_id="s", legs=_legs(),
                    now_ms=_NOW)
    readiness = build_shadow_readiness(store=store, now_ms=_NOW + 1)
    assert readiness["shadow_ready"] is False
    assert "NO_EXECUTABLE_OBSERVATION" in readiness["blockers"]


def test_readiness_chain_tamper_blocks(tmp_path):
    path = str(tmp_path / "shadow.db")
    store = SqliteShadowStore(path, create=True)
    venue = _FakeVenue({"BTC": _book("BTC"), "ETH": _book("ETH")})
    run_shadow_pass(venue=venue, store=store, strategy_id="s", legs=_legs(), now_ms=_NOW)
    store.close()
    external = sqlite3.connect(path)
    external.execute("DROP TRIGGER shadow_observations_no_update")
    external.execute("UPDATE shadow_observations SET payload_json=? WHERE obs_seq=1",
                     (json.dumps({"forged": True}),))
    external.commit()
    external.close()
    reopened = SqliteShadowStore(path, create=True)
    readiness = build_shadow_readiness(store=reopened, now_ms=_NOW + 1)
    assert readiness["shadow_ready"] is False
    assert "CHAIN_INVALID" in readiness["blockers"]
    assert "OUTBOX_CHAIN_INVALID" in readiness["blockers"]


# --- venue 注册 / CLI 守卫 ---

def test_shadow_venues_are_structurally_testnet_only():
    venues = shadow_venues()
    assert set(venues) == {"binance_testnet", "bybit_testnet", "bybit_demo"}
    for cfg in venues.values():
        assert not cfg.get("mainnet_only") and not cfg.get("spot")


def test_build_shadow_venue_rejects_mainnet_and_unknown():
    for bad in ("mexc", "binance_mainnet", "binance", "bybit", "kraken"):
        with pytest.raises(SystemExit):
            build_shadow_venue(bad)


def test_build_shadow_venue_constructs_testnet_without_credentials():
    venue = build_shadow_venue("binance_testnet")
    assert venue.venue_mode == "testnet"
    assert hasattr(venue, "depth") and hasattr(venue, "book_ticker")


def test_main_rejects_unknown_venue_and_strategy(tmp_path):
    from tools.shadow_track import main
    with pytest.raises(SystemExit):
        main(["--venue", "mexc", "--db", str(tmp_path / "s.db")])
    with pytest.raises(SystemExit):
        main(["--strategy", "nope", "--db", str(tmp_path / "s.db")])


# --- run_once(注入 venue/signal,零网络)---

def test_run_once_writes_evidence_with_readiness(tmp_path):
    evidence = str(tmp_path / "shadow_status.json")
    db = str(tmp_path / "shadow.db")
    venue = _FakeVenue({"BTC": _book("BTC")})
    signal = lambda: ({"BTC": 1.0}, {"BTC": 100.0}, 0, {}, {})   # noqa: E731
    document = run_once(strategy_id="crowd_contra", venue_alias="binance_testnet",
                        notional=100.0, db_path=db, evidence_path=evidence,
                        venue=venue, signal_fn=signal, now_ms=_NOW)
    assert document["provenance"] == "SHADOW"
    assert document["venue"] == "binance_testnet"
    assert document["last_pass"]["observations"] == 1
    assert document["last_pass"]["appended"] == 1
    assert document["last_pass"]["executable_count"] == 1
    assert document["readiness"]["shadow_ready"] is True
    assert document["honesty_note"]
    assert document["safety"]["order_path_touched"] is False
    on_disk = json.loads(open(evidence, encoding="utf-8").read())
    assert on_disk == document
    store = SqliteShadowStore(db, create=False)
    assert store.count() == 1
    assert store.event_outbox_chain_valid()
