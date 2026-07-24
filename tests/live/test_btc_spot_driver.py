"""btc_spot_trend 驱动器接线(2026-07 MEXC 对接 1.2/1.4):_VENUES["mexc"] 注册形状、
_mexc_balances 现货权益折算、_latest_btc_spot_weights_and_prices 信号入口(PIT 种子库,
确定性)。main() 是网络胶水不可离线跑(仓库惯例),这里测可测纯件。
"""
import os

import pytest

from live.exchange.mexc_spot import MexcSpotAdapter
from services.data.market_provider import ingest_provider_rows
from services.data.persistent_store import SqlitePITStore
from tools import live_track_advance as drv

_DAY = 86_400_000
_T0 = (1_700_000_000_000 // _DAY) * _DAY


def _seed_mexc_db(path, closes):
    store = SqlitePITStore(path, create=True)
    rows = [{"instrument_id": "BTC", "event_time": _T0 + i * _DAY,
             "knowledge_time": _T0 + i * _DAY,
             "open": c, "high": c, "low": c, "close": c, "volume": 2.0}
            for i, c in enumerate(closes)]
    ingest_provider_rows(rows, store, dataset_id="mexc-BTC",
                         now=_T0 + len(closes) * _DAY, time_unit="ms", strict=False)
    return store


# --- _VENUES["mexc"] 注册形状(1.2) ---

def test_mexc_venue_registration_shape():
    cfg = drv._VENUES["mexc"]
    assert cfg["adapter"] is MexcSpotAdapter
    assert cfg["base_url"] == MexcSpotAdapter.MAINNET_BASE
    assert cfg["env"] == ("MEXC_API_KEY", "MEXC_API_SECRET")
    assert cfg["key_file"] == os.path.join("key", "mexc_spot.json")
    assert cfg["suffix"] == "_mexc"
    assert cfg["mandate_file"] == os.path.join("key", "live_mandate_mexc.json")
    assert cfg["spot"] is True
    assert callable(cfg["balances"]) and callable(cfg["margin"])
    # venue 归一化桥(mainnet_key_safety_skip_reason)依赖 venue_name 做交易所家族归一
    assert cfg["adapter"].venue_name == "mexc"


def test_mexc_margin_is_honest_spot_semantics():
    bal, req = drv._mexc_margin({})
    assert bal is None and req == 0.0        # 余额未知 fail-closed;现货无维持要求(事实)


# --- _mexc_balances(现货权益 = USDT free + BTC×最新价) ---

def test_mexc_balances_spot_equity_with_prices():
    acct = {"balances": [{"asset": "USDT", "free": "400.0", "locked": "0"},
                         {"asset": "BTC", "free": "0.01", "locked": "0.002"},
                         {"asset": "DOGE", "free": "7", "locked": "0"}]}
    aum, equity = drv._mexc_balances(acct, {"BTC": 60_000.0})
    assert aum == pytest.approx(400.0)
    assert equity == pytest.approx(400.0 + 600.0)      # DOGE 无价 → 按 0(保守低估)


def test_mexc_balances_without_prices_is_cash_only_and_conservative():
    acct = {"balances": [{"asset": "USDT", "free": "400.0", "locked": "0"},
                         {"asset": "BTC", "free": "0.01", "locked": "0"}]}
    aum, equity = drv._mexc_balances(acct, None)
    assert aum == pytest.approx(400.0)
    assert equity == pytest.approx(400.0)              # 无价持仓按 0 → 低估权益 → 偏向硬停


# --- _latest_btc_spot_weights_and_prices(注册表五元组) ---

def test_btc_spot_signal_returns_five_tuple_from_pit(tmp_path, monkeypatch):
    db = str(tmp_path / "crypto_mexc.db")
    closes = [100.0] * 39 + [200.0]                    # 末日突破 → 信号开
    _seed_mexc_db(db, closes)
    monkeypatch.setattr(drv, "_MEXC_DB", db)
    weights, prices, decided_ts, ls_row, evidence = drv._latest_btc_spot_weights_and_prices()
    assert weights == {"BTC": 1.0}
    assert prices == {"BTC": 200.0}
    assert decided_ts == _T0 + 39 * _DAY
    assert ls_row == {}                                 # 现货无 OI 维度
    assert set(evidence) == {"BTC"}
    assert evidence["BTC"]["adv_units"] is not None
    assert evidence["BTC"]["inst_vol"] is not None
    assert evidence["BTC"]["crowding_z"] is None


def test_btc_spot_signal_deterministic(tmp_path, monkeypatch):
    db = str(tmp_path / "crypto_mexc.db")
    _seed_mexc_db(db, [100.0 + (i % 5) for i in range(60)])
    monkeypatch.setattr(drv, "_MEXC_DB", db)
    assert drv._latest_btc_spot_weights_and_prices() == \
        drv._latest_btc_spot_weights_and_prices()


def test_btc_spot_signal_empty_when_db_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(drv, "_MEXC_DB", str(tmp_path / "nope.db"))
    assert drv._latest_btc_spot_weights_and_prices() == ({}, {}, 0, {}, {})


def test_btc_spot_signal_zero_weight_is_a_legitimate_target(tmp_path, monkeypatch):
    # 信号关 → {"BTC": 0.0}(真目标:生成平仓单),**不是**空五元组(那才触发数据质量跳批)
    db = str(tmp_path / "crypto_mexc.db")
    _seed_mexc_db(db, [100.0] * 45)
    monkeypatch.setattr(drv, "_MEXC_DB", db)
    weights, prices, decided_ts, _ls, _ev = drv._latest_btc_spot_weights_and_prices()
    assert weights == {"BTC": 0.0} and prices["BTC"] > 0 and decided_ts > 0


def test_strategy_registry_wires_btc_spot_trend():
    cfg = drv._STRATEGIES["btc_spot_trend"]
    assert cfg["crowding_gate"] is False
    assert cfg["signal"] is drv._latest_btc_spot_weights_and_prices
