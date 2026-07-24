"""tools/dry_run_btc_spot_multi.py 纯逻辑 + 前向 paper-track 接线(2026-07 MEXC 对接 1.1)。

钉死:信号核确定性(字面/迟滞两变体,无 RNG/墙钟)、_build_periods 在 PIT 库上的确定性、
_existing_run 的 (strategy_id, data_source) 双键幂等(LEGACY 旧 run 不挡 MEXC 新跑)、
_advance_paper_track 的诚实 train/paper 切分 + append-only 幂等。
"""
import os

import pytest

from services.data.market_provider import ingest_provider_rows
from services.data.persistent_store import SqlitePITStore
from services.execution.dry_run_store import DryRunRun, DryRunStore
from services.research.paper_trading.store import PaperTrackStore
from tools import dry_run_btc_spot_multi as drv

_DAY = 86_400_000
_T0 = (1_700_000_000_000 // _DAY) * _DAY


def _seed_pit_db(path, closes, *, start=_T0):
    store = SqlitePITStore(path, create=True)
    rows = [{"instrument_id": "BTC", "event_time": start + i * _DAY,
             "knowledge_time": start + i * _DAY,
             "open": c, "high": c, "low": c, "close": c, "volume": 1e6}
            for i, c in enumerate(closes)]
    ingest_provider_rows(rows, store, dataset_id="mexc-BTC", now=start + len(closes) * _DAY,
                         time_unit="ms", strict=False)
    return store


# --- 信号核 ---

def test_literal_signal_requires_donchian_breakout_and_positive_momentum():
    base = [100.0] * 31
    assert drv._signal(base) == 0.0                     # 无突破、动量=0 → 空仓
    breakout = [100.0] * 30 + [101.0]
    assert drv._signal(breakout) == 1.0                 # 收 > 前 20 日最高 且 > 30 日前收
    pullback = [100.0] * 30 + [99.0]
    assert drv._signal(pullback) == 0.0


def test_hysteresis_holds_until_momentum_turns_negative():
    closes = [100.0] * 30 + [101.0, 100.5, 100.5, 99.0]
    sig = drv._signal_series_hysteresis(closes)
    # 进场(突破+动量)→ 持有(回落但仍 > 30 日前)→ 动量转负(99 < 100)→ 退场
    assert sig[30] == 1.0
    assert sig[31] == 1.0 and sig[32] == 1.0
    assert sig[33] == 0.0


def test_signal_deterministic_same_input_same_output():
    closes = [100.0 + (i % 7) for i in range(60)]
    assert drv._signal_series_hysteresis(closes) == drv._signal_series_hysteresis(list(closes))


# --- _build_periods 确定性 ---

def test_build_periods_deterministic_on_pit_store(tmp_path):
    closes = [100.0 + i for i in range(70)]
    db = str(tmp_path / "mexc.db")
    _seed_pit_db(db, closes)
    from services.data.caching_store import CachingPITStore
    store = CachingPITStore(SqlitePITStore(db, create=False))
    a = drv._build_periods(store)
    b = drv._build_periods(store)
    assert a == b
    periods, weights, prices, vols = a
    assert periods and all(w == {"BTC": weights[t]["BTC"]} for t, w in weights.items())
    assert all(0.0 <= weights[t]["BTC"] <= 1.0 for t in periods)   # long-only 0/1


# --- _existing_run 双键幂等 ---

def _save_run(db_path, strategy_id, data_source):
    store = DryRunStore(db_path, create=True)
    store.save_run(DryRunRun(run_id=f"r-{strategy_id}-{data_source}", strategy_id=strategy_id,
                             timestamp=_T0, n_intents=0, n_approved=0, n_rejected=0,
                             rejected_reasons=[], pnl={}, audit_root="x",
                             detail=({"data_source": data_source} if data_source else {})))
    store.close()


def test_existing_run_matches_on_strategy_and_data_source(tmp_path, monkeypatch):
    db = str(tmp_path / "dry_run.db")
    monkeypatch.setattr(drv, "_OUT", db)
    _save_run(db, "btc_spot_trend", None)                # LEGACY(无 data_source 标记)
    _save_run(db, "btc_spot_trend", "MEXC_PIT")
    # LEGACY 旧 run 绝不顶替 MEXC 主口径(否则 CoinGecko 证据冒充 MEXC 证据)
    assert drv._existing_run("btc_spot_trend", "COINGECKO_PIT") is None
    hit = drv._existing_run("btc_spot_trend", "MEXC_PIT")
    assert hit is not None and hit["run_id"] == "r-btc_spot_trend-MEXC_PIT"


# --- 前向 paper-track(断点2 持久层) ---

def _fake_detail(n=30):
    curve = []
    for i in range(n):
        curve.append({"t": _T0 + i * 7 * _DAY, "equity": 1e6 * (1.0 + 0.001 * i),
                      "ret": 0.001})
    return {"equity_curve": curve}


def test_advance_paper_track_honest_split_and_idempotent(tmp_path):
    db = str(tmp_path / "paper_track.db")
    drv._advance_paper_track(_fake_detail(30), alpha_id="btc_spot_trend", db_path=db)
    store = PaperTrackStore(db, create=False)
    track = store.track("btc_spot_trend")
    assert track is not None
    assert track["n_periods"] == 30 - max(10, int(30 * 0.7))     # 后 30% = 前向 paper
    assert track["paper_sharpe"] is not None
    assert track["backtest_sharpe"] is not None
    periods = store.periods("btc_spot_trend")
    assert all(p["realized_ic"] is None for p in periods)        # 单名无截面,诚实 None
    ts = [p["as_of_ts"] for p in periods]
    assert ts == sorted(ts) and ts[0] == _T0 + max(10, int(30 * 0.7)) * 7 * _DAY
    # 幂等:再跑一遍零新增(append-only 主键去重)
    drv._advance_paper_track(_fake_detail(30), alpha_id="btc_spot_trend", db_path=db)
    assert len(store.periods("btc_spot_trend")) == len(periods)


def test_advance_paper_track_skips_short_series(tmp_path, capsys):
    db = str(tmp_path / "paper_track.db")
    drv._advance_paper_track(_fake_detail(5), alpha_id="btc_spot_trend", db_path=db)
    assert "跳过" in capsys.readouterr().out
    assert not os.path.exists(db)                                # 不足 20 期不落库
