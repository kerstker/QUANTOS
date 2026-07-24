"""carry_binance_validate: 从 Binance 真实数据验证 carry alpha。"""
import json
import os

import pytest

from services.data.persistent_store import SqlitePITStore


def test_build_carry_pit_store_writes_readable_bars(tmp_path):
    """将 Binance 数据写入 PIT Store 后，可正确读取 Bar 和 funding 字段。"""
    from tools.carry_binance_validate import build_carry_pit_store

    kline_data = {
        "BTCUSDT": {
            "klines": [
                [1609459200000, "29000", "29100", "28900", "29050", "100"],
                [1609545600000, "29050", "29200", "29000", "29100", "120"],
                [1609632000000, "29100", "29300", "29050", "29250", "110"],
            ]
        },
        "ETHUSDT": {
            "klines": [
                [1609459200000, "730", "740", "720", "735", "200"],
                [1609545600000, "735", "745", "730", "740", "180"],
                [1609632000000, "740", "750", "735", "745", "190"],
            ]
        },
    }
    funding_data = {
        "BTCUSDT": {"1609459200000": 0.0001, "1609545600000": 0.00015, "1609632000000": 0.00005},
        "ETHUSDT": {"1609459200000": 0.0002, "1609545600000": 0.0001, "1609632000000": 0.0003},
    }
    db_path = os.path.join(tmp_path, "test_carry.db")
    instruments = build_carry_pit_store(
        kline_data, funding_data, db_path=db_path, snapshot_dir=str(tmp_path),
    )

    store = SqlitePITStore(db_path, create=False)
    # as_of 接受单个 key，逐币读取最新 Bar（PIT 时间戳为毫秒）
    last_et_ms = 1609632000000
    bars = []
    for inst in instruments:
        bar = store.latest_as_of(inst, last_et_ms)
        assert bar is not None, f"no bar for {inst}"
        bars.append(bar)
    assert len(bars) == 2
    assert bars[0].close > 0
    # funding 存储为伴生 JSON 文件，验证存在并可读
    funding_path = os.path.join(tmp_path, "crypto_funding_rates.json")
    assert os.path.exists(funding_path)
    fdata = json.load(open(funding_path, encoding="utf-8"))
    assert "BTCUSDT" in fdata
    assert "ETHUSDT" in fdata
    # 验证 funding 值能正确匹配
    btc_funding_at_t3 = fdata["BTCUSDT"].get("1609632000000")
    assert float(btc_funding_at_t3) == 0.00005


def test_carry_walkforward_runs_on_synthetic_data(tmp_path):
    """用 carry walkforward Ladder 在合成数据上产出有效报告。"""
    import random
    from tools.carry_binance_validate import run_carry_walkforward, build_carry_pit_store

    random.seed(42)
    # 5 个币，60 个周期（足够 _legs 和 QUANTILE=0.2 选出 legs）
    coins = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT"]
    kline_data = {}
    funding_data = {}
    base_prices = [29000.0, 730.0, 30.0, 20.0, 0.3]
    for idx, coin in enumerate(coins):
        kline_data[coin] = {"klines": []}
        funding_data[coin] = {}
        bp = base_prices[idx]
        for i in range(60):
            ts = 1609459200000 + i * 86400000
            # 生成合理的 OHLCV 数据（含轻微趋势 + 随机波动）
            px_open = bp * (1.0 + i * 0.0005 + random.uniform(-0.003, 0.003))
            px_close = px_open * (1.0 + random.uniform(-0.005, 0.005))
            high = max(px_open, px_close) * (1.0 + random.uniform(0, 0.002))
            low = min(px_open, px_close) * (1.0 - random.uniform(0, 0.002))
            vol = random.uniform(500, 2000)
            kline_data[coin]["klines"].append(
                [ts,
                 str(round(px_open, 2)),
                 str(round(high, 2)),
                 str(round(low, 2)),
                 str(round(px_close, 2)),
                 str(round(vol, 0))]
            )
            funding_data[coin][str(ts)] = round(random.uniform(-0.002, 0.002), 6)

    db_path = os.path.join(tmp_path, "test_carry.db")
    instruments = build_carry_pit_store(
        kline_data, funding_data, db_path=db_path, snapshot_dir=str(tmp_path),
    )
    report = run_carry_walkforward(
        db_path=db_path, instruments=instruments, snapshot_dir=str(tmp_path),
    )

    assert "ladder" in report
    assert len(report["ladder"]) == 3  # raw_neg_funding, z_neg_decay7, composite
    assert all("net_sharpe" in r for r in report["ladder"].values())
