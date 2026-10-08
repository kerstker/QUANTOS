import json

import pytest

from tools import binance_liquidations_fetch as lf

_DAY = 86_400_000
_NOW = 1_700_000_000_000


def _order(ts, side="SELL", qty="2", price="100"):
    return {"symbol": "BTCUSDT", "side": side, "time": ts,
            "executedQty": qty, "averagePrice": price}


def test_parse_force_orders_aggregates_sides_and_rejects_future_rows():
    payload = [_order(_NOW - 2 * _DAY + 1000, "SELL", "2", "100"),
               _order(_NOW - 2 * _DAY + 2000, "BUY", "1", "120"),
               _order(_NOW + 1, "BUY"),
               {"symbol": "ETHUSDT", "side": "SELL", "time": _NOW - _DAY,
                "executedQty": "1", "averagePrice": "100"}]
    diagnostics = {}
    rows = lf.parse_force_orders(payload, "BTC", now_ms=_NOW, diagnostics=diagnostics)
    day = (_NOW - 2 * _DAY) // _DAY * _DAY
    assert rows[day]["liquidation_notional"] == pytest.approx(320.0)
    assert rows[day]["long_liquidation_notional"] == pytest.approx(200.0)
    assert rows[day]["short_liquidation_notional"] == pytest.approx(120.0)
    assert rows[day]["order_count"] == 2
    assert rows[day]["imbalance"] == pytest.approx(80 / 320)
    assert diagnostics["accepted_rows"] == 2 and diagnostics["rejected_rows"] == 2


def test_fetch_daily_liquidations_rejects_deep_history_request():
    with pytest.raises(ValueError, match="1..7"):
        lf.fetch_daily_liquidations(days=8, now_ms=_NOW, http_get=lambda *a: [])


def test_fetch_daily_liquidations_archives_and_drops_open_day(tmp_path):
    closed = _NOW - 2 * _DAY + 1000
    calls = []

    def fake_get(url, headers):
        calls.append(url)
        return [_order(closed, "SELL", "1", "100")]

    rows = lf.fetch_daily_liquidations(
        "BTC", days=2, now_ms=_NOW, http_get=fake_get, sleep=None,
        archive_root=str(tmp_path), observed_at_ms=_NOW,
    )
    assert len(calls) == 1
    assert list(rows) == [closed // _DAY * _DAY]
    obs = list((tmp_path / "observations").rglob("*.json"))
    assert len(obs) == 1
    assert json.loads(obs[0].read_text(encoding="utf-8"))["dataset_id"] == "liquidations"


def test_update_cache_is_idempotent(tmp_path):
    path = str(tmp_path / "liq.json")
    day = _NOW - 2 * _DAY
    rows = {day: {"event_time": day, "knowledge_time": day + _DAY - 1,
                  "liquidation_notional": 1.0}}
    lf.update_cache(path, ticker="BTC", rows=rows, fetched_at_ms=_NOW)
    payload = lf.update_cache(path, ticker="BTC", rows=rows, fetched_at_ms=_NOW + 1)
    assert len(payload["data"]["BTC"]) == 1
    assert json.loads(open(path, encoding="utf-8").read())["_meta"]["BTC"]["through"] == day
