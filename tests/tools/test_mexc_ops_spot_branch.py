"""live_ops_api 现货 dashboard 分支(2026-07 MEXC 对接 1.7)+ live_snapshot 现货解析。

钉死:mexc venue 的 dashboard 走 ungated 只读镜像(绝不构造 gated 适配器、绝不 arm),
现货行无强平价/无杠杆字段、upnl 诚实 None,缺价资产不臆造 notional;期货分支行为零回归。
全程假 transport/临时目录——零真网、零真凭证。
"""
import json

import pytest

from tools.live_snapshot import mexc_spot_account_rows


# --- mexc_spot_account_rows 纯解析 ---

def test_spot_rows_skip_usdt_and_zero_and_unpriced_is_honest_none():
    acct = {"balances": [
        {"asset": "BTC", "free": "0.01", "locked": "0.002"},
        {"asset": "USDT", "free": "500.0", "locked": "0"},
        {"asset": "ETH", "free": "0", "locked": "0"},
        {"asset": "DOGE", "free": "7", "locked": "0"},
    ]}
    rows = mexc_spot_account_rows(acct, {"BTC": 60_000.0})
    assert len(rows) == 2                              # USDT/零持仓跳过,BTC+DOGE
    btc = next(r for r in rows if r["instrument_id"] == "BTC")
    assert btc["qty"] == pytest.approx(0.01)
    assert btc["entry_price"] == 0.0                   # 无成本基,诚实 0.0
    assert btc["upnl"] is None                         # 无成本基 → upnl 不可计算,非 0
    assert btc["notional"] == pytest.approx(600.0)
    assert "liquidation_price" not in btc and "leverage" not in btc   # 现货无强平/杠杆字段
    doge = next(r for r in rows if r["instrument_id"] == "DOGE")
    assert doge["mark_price"] is None and doge["notional"] is None    # 缺价不臆造


# --- _venue_dashboard 现货分支 ---

def _dashboard_env(monkeypatch, tmp_path):
    import tools.live_ops_api as ops
    monkeypatch.chdir(tmp_path)                        # 账本/mandate/status 全缺 → 诚实空
    monkeypatch.setenv("MEXC_API_KEY", "k")
    monkeypatch.setenv("MEXC_API_SECRET", "s")
    acct = {"canTrade": True, "canWithdraw": False, "balances": [
        {"asset": "BTC", "free": "0.01", "locked": "0"},
        {"asset": "USDT", "free": "400.0", "locked": "0"},
    ]}
    monkeypatch.setattr(ops, "fetch_mexc_account", lambda k, s: acct)
    monkeypatch.setattr(ops, "_mexc_public_marks", lambda _acct: {"BTC": 60_000.0})
    return ops


def test_venue_dashboard_spot_branch_reports_spot_equity_without_futures_fields(monkeypatch,
                                                                                tmp_path):
    ops = _dashboard_env(monkeypatch, tmp_path)
    out = ops._venue_dashboard("mexc")
    assert out["configured"] is True
    assert out["ok"] is True and out["spot"] is True
    assert out["available"] == pytest.approx(400.0)              # USDT 现金腿
    assert out["equity"] == pytest.approx(400.0 + 0.01 * 60_000.0)  # 现金 + BTC×标记价
    assert out["upnl_total"] is None                           # 现货无成本基
    assert out["gross_notional"] == pytest.approx(600.0)
    btc = out["positions"][0]
    assert "liquidation_price" not in btc and "leverage" not in btc
    assert "mandate" in out and out["mandate"] is None         # mandate 未签 → 诚实 None


def test_venue_dashboard_spot_branch_fails_soft_on_account_error(monkeypatch, tmp_path):
    import tools.live_ops_api as ops
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEXC_API_KEY", "k")
    monkeypatch.setenv("MEXC_API_SECRET", "s")

    def boom(k, s):
        raise RuntimeError("account read failed")
    monkeypatch.setattr(ops, "fetch_mexc_account", boom)
    out = ops._venue_dashboard("mexc")
    assert out["ok"] is False and "error" in out


def test_venue_dashboard_futures_branch_unchanged(monkeypatch, tmp_path):
    # 期货分支零回归:binance 仍走 gated 适配器 + binance_position_rows
    import tools.live_ops_api as ops
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "k")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "s")
    acct = {"availableBalance": "100.0", "totalMarginBalance": "110.0",
            "positions": [{"symbol": "BTCUSDT", "positionAmt": "0.01",
                           "entryPrice": "59000", "unrealizedProfit": "10.0"}]}

    class _FakeVenue:
        def account(self):
            return acct
    monkeypatch.setattr(ops, "_venue_adapter", lambda venue: _FakeVenue())
    out = ops._venue_dashboard("binance")
    assert out["ok"] is True and "spot" not in out
    assert out["upnl_total"] == pytest.approx(10.0)
    assert out["positions"][0]["instrument_id"] == "BTC"
