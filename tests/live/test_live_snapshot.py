"""tools/live_snapshot.py(共享仓位快照解析)+ tools/live_risk_sentinel.py 纯部件。2026-07-05。"""
import json

import pytest

from tools.live_snapshot import binance_position_rows, bybit_position_rows, portfolio_summary


def test_binance_rows_derive_mark_from_upnl_identity():
    raw = {"positions": [
        {"symbol": "BTCUSDT", "positionAmt": "0.01", "entryPrice": "60000", "unrealizedProfit": "10"},
        {"symbol": "ETHUSDT", "positionAmt": "-2", "entryPrice": "3000", "unrealizedProfit": "50"},
        {"symbol": "SOLUSDT", "positionAmt": "0", "entryPrice": "0", "unrealizedProfit": "0"},   # 平仓跳过
        {"symbol": "BTCUSDC", "positionAmt": "1", "entryPrice": "1", "unrealizedProfit": "0"},   # 非 USDT
    ]}
    rows = binance_position_rows(raw)
    assert len(rows) == 2
    btc = next(r for r in rows if r["instrument_id"] == "BTC")
    # uPnL = (mark-entry)*qty 是恒等式 -> mark = entry + upnl/qty = 60000 + 10/0.01 = 61000
    assert btc["mark_price"] == pytest.approx(61000.0)
    eth = next(r for r in rows if r["instrument_id"] == "ETH")
    assert eth["qty"] == pytest.approx(-2.0)
    assert eth["mark_price"] == pytest.approx(3000 + 50 / -2)   # 空头盈利 -> mark 低于 entry


def test_bybit_rows_parse_envelope_and_sign_by_side():
    raw = {"result": {"list": [
        {"symbol": "ADAUSDT", "side": "Sell", "size": "840", "avgPrice": "0.19",
         "markPrice": "0.185", "unrealisedPnl": "4.2"},
        {"symbol": "OPUSDT", "side": "Buy", "size": "1375", "avgPrice": "0.108",
         "markPrice": "0.110", "unrealisedPnl": "2.75"},
        {"symbol": "XUSDT", "side": "", "size": "5", "avgPrice": "1", "markPrice": "1",
         "unrealisedPnl": "0"},                                  # side 未知,诚实跳过
    ]}}
    rows = bybit_position_rows(raw)
    assert len(rows) == 2
    ada = next(r for r in rows if r["instrument_id"] == "ADA")
    assert ada["qty"] == pytest.approx(-840.0)
    assert ada["notional"] == pytest.approx(-840.0 * 0.185)


def test_bybit_rows_parse_liquidation_price_when_present():
    # 审计 §4.4 [P1-R3]:Bybit V5 position/list 自带 liqPrice(交易所已算好的真实强平价),
    # 此前被丢弃——这里只断言展示层如实透传,不做任何判断/告警。
    raw = {"result": {"list": [
        {"symbol": "ADAUSDT", "side": "Sell", "size": "840", "avgPrice": "0.19",
         "markPrice": "0.185", "unrealisedPnl": "4.2", "liqPrice": "0.24"},
    ]}}
    rows = bybit_position_rows(raw)
    assert rows[0]["liquidation_price"] == pytest.approx(0.24)


def test_bybit_rows_liquidation_price_none_when_missing_or_empty():
    # Bybit 对无杠杆/部分仓位类型可能不返回 liqPrice,或返回空字符串——诚实缺省 None,
    # 绝不臆造一个数字。
    raw = {"result": {"list": [
        {"symbol": "ADAUSDT", "side": "Sell", "size": "840", "avgPrice": "0.19",
         "markPrice": "0.185", "unrealisedPnl": "4.2"},                   # liqPrice 缺失
        {"symbol": "OPUSDT", "side": "Buy", "size": "1375", "avgPrice": "0.108",
         "markPrice": "0.110", "unrealisedPnl": "2.75", "liqPrice": ""},  # liqPrice 空字符串
    ]}}
    rows = bybit_position_rows(raw)
    assert len(rows) == 2
    assert all(r["liquidation_price"] is None for r in rows)


def test_bybit_rows_liquidation_price_none_when_unparseable():
    # 防御性:字段存在但不是合法数字(理论上不该发生,但解析层不能因此崩溃)-> None。
    raw = {"result": {"list": [
        {"symbol": "ADAUSDT", "side": "Sell", "size": "840", "avgPrice": "0.19",
         "markPrice": "0.185", "unrealisedPnl": "4.2", "liqPrice": "n/a"},
    ]}}
    rows = bybit_position_rows(raw)
    assert rows[0]["liquidation_price"] is None


def test_portfolio_summary_gross_net_upnl():
    rows = [{"instrument_id": "A", "qty": 1, "entry_price": 1, "mark_price": 1,
             "upnl": 2.0, "notional": 100.0},
            {"instrument_id": "B", "qty": -1, "entry_price": 1, "mark_price": 1,
             "upnl": -0.5, "notional": -40.0}]
    s = portfolio_summary(rows)
    assert s["gross_notional"] == pytest.approx(140.0)
    assert s["net_notional"] == pytest.approx(60.0)
    assert s["upnl_total"] == pytest.approx(1.5)
    assert s["n_positions"] == 2


def test_sentinel_caps_derive_from_mandate_or_fall_back_to_drawdown_only():
    from live.governance.mandate import Mandate
    from tools.live_risk_sentinel import sentinel_caps
    m = Mandate(mandate_id="m", venue="testnet", exchange="binance",
                allowed_instruments=frozenset({"BTC"}), max_single_order_notional=2500.0,
                max_gross_notional=10000.0, max_net_notional=3000.0)
    caps = sentinel_caps(m)
    assert caps["max_gross_exposure"] == 10000.0 and caps["max_net_exposure"] == 3000.0
    assert caps["source"] == "mandate"
    # mandate 缺失:0 敞口上限会永远告警(无意义),诚实退化为"只看回撤"并标注
    caps2 = sentinel_caps(None)
    assert caps2["max_gross_exposure"] == float("inf")
    assert caps2["source"] == "no_mandate_drawdown_only"


def test_append_equity_point_is_append_only(tmp_path):
    from tools.live_risk_sentinel import append_equity_point
    p = str(tmp_path / "eq.jsonl")
    append_equity_point(p, {"ts": 1, "equity": 100.0})
    append_equity_point(p, {"ts": 2, "equity": 101.0})
    lines = [json.loads(x) for x in open(p, encoding="utf-8").read().strip().splitlines()]
    assert [x["ts"] for x in lines] == [1, 2]
