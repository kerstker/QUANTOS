"""carry_binance_validate: 从 Binance 真实数据构建 carry PIT Store 并验证。

全复用现有基础设施，不新建 service/ 模块。"""
from __future__ import annotations

import json
import os
import statistics
import time
from collections import defaultdict

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.execution.backtest_authority import quarantine_legacy_backtest
from services.research.backtest_engine.metrics import sharpe as _sharpe_fn, max_drawdown


def build_carry_pit_store(kline_data: dict, funding_data: dict, *,
                          db_path: str = "", snapshot_dir: str = "") -> list[str]:
    """将 Binance K线 + 资金费率数据写入 PIT Store，返回 instrument 列表。

    资金费率存储为伴生 JSON 文件（Pydantic Bar 模型不支持额外字段）。
    """
    if not db_path:
        db_path = os.path.join("data", "cache", "crypto_funding.db")
    if not snapshot_dir:
        snapshot_dir = os.path.join("data", "cache")

    from quantos.contracts.data import Bar

    store = SqlitePITStore(db_path, create=True)
    instruments = sorted(kline_data.keys())

    # 收集所有 event_time
    all_times: set[int] = set()
    for sym, info in kline_data.items():
        for k in info["klines"]:
            all_times.add(int(k[0]))

    # 按时间写入 bars（每个时间点一批）
    for et in sorted(all_times):
        batch = []
        for sym in instruments:
            info = kline_data[sym]
            kline = next((k for k in info["klines"] if int(k[0]) == et), None)
            if kline is None:
                continue
            bar = Bar(
                instrument_id=sym,
                event_time=et,
                # Binance timestamps a 1d kline by its open.  The completed
                # OHLCV row is not knowable until the following UTC boundary.
                knowledge_time=et + 86_400_000,
                open=float(kline[1]),
                high=float(kline[2]),
                low=float(kline[3]),
                close=float(kline[4]),
                volume=float(kline[5]),
            )
            batch.append(bar)
        store.add_batch_atomic(batch)

    # 资金费率存为伴生 JSON（与 crypto_carry_strategy.py 的 _FUND_CACHE 一致）
    if funding_data and snapshot_dir:
        funding_path = os.path.join(snapshot_dir, "crypto_funding_rates.json")
        os.makedirs(snapshot_dir, exist_ok=True)
        json.dump(funding_data, open(funding_path, "w", encoding="utf-8"),
                  indent=2, sort_keys=True)

    # 显式关闭连接确保数据写入磁盘（SqlitePITStore 无 close()）
    store._conn.close()
    return instruments


def run_carry_walkforward(*, db_path: str = "", instruments: list[str] | None = None,
                          snapshot_dir: str = "") -> dict:
    """在 PIT Store 上跑 carry walkforward 验证。

    完全复用 crypto_carry_walkforward.py 的数据管道模式：
    snapshot JSON → PIT Store → build_field_panel → LADDER。"""
    if not db_path:
        db_path = os.path.join("data", "cache", "crypto_funding.db")
    if not snapshot_dir:
        snapshot_dir = os.path.join("data", "cache")

    from tools.crypto_carry_walkforward import LADDER, _run_spec, _per_year, _blocks, N_BLOCKS
    from tools.crypto_carry_strategy import FUND, QUANTILE, _legs
    from tools.crypto_carry_stress import _slip_bps
    from services.research import alpha_expression as ax

    store = CachingPITStore(SqlitePITStore(db_path, create=False))

    # 优先从 snapshot JSON 读 instruments（匹配 crypto_carry_walkforward.py 模式）
    snap_path = os.path.join(snapshot_dir, "crypto_funding_snapshot.json")
    if instruments is None and os.path.exists(snap_path):
        instruments = sorted(json.load(open(snap_path, encoding="utf-8")).get("instruments", []))
    if not instruments:
        # fallback: 从 funding_rates.json 的 key 推导
        funding_path = os.path.join(snapshot_dir, "crypto_funding_rates.json")
        if os.path.exists(funding_path):
            instruments = sorted(json.load(open(funding_path, encoding="utf-8")).keys())
    if not instruments:
        return {"error": "no instruments found", "ladder": {}}

    # 收集所有 event_times（含 funding 数据的时间点）
    funding_path = os.path.join(snapshot_dir, "crypto_funding_rates.json")
    if not os.path.exists(funding_path):
        return {"error": "funding_rates.json not found — run binance_funding_fetch first", "ladder": {}}
    funding_raw = json.load(open(funding_path, encoding="utf-8"))
    funding = {sym: {int(k): v for k, v in d.items()} for sym, d in funding_raw.items()}

    # 从 funding + bars 的交集取 event_times
    funding_times: set[int] = set()
    for inst_data in funding_raw.values():
        funding_times.update(int(k) for k in inst_data)
    bar_times: set[int] = set()
    for inst in instruments:
        bars = store.fetch_bars(inst, -10**18, 10**18)
        bar_times.update(b.event_time for b in bars)
    event_times = sorted(funding_times & bar_times)
    if len(event_times) < 60:
        return {"error": f"insufficient overlap: {len(event_times)} periods", "ladder": {}}

    # 流动性过滤（匹配 crypto_carry_walkforward.py 的 liquid_half 逻辑）
    instruments_with_data = []
    for inst in instruments:
        bars = store.fetch_bars(inst, -10**18, 10**18)
        if bars:
            instruments_with_data.append(inst)
    liq = {}
    for inst in instruments_with_data:
        bars = store.fetch_bars(inst, -10**18, 10**18)
        dv = [b.close * b.volume for b in bars if b.close > 0 and b.volume > 0]
        liq[inst] = statistics.median(dv) if dv else 0.0
    ranked = sorted(instruments_with_data, key=lambda x: liq[x])
    pctile = {inst: (i + 1) / len(ranked) for i, inst in enumerate(ranked)}
    slip_by_inst = {inst: _slip_bps(pctile[inst]) for inst in instruments_with_data}
    liquid = [inst for inst in instruments_with_data if pctile[inst] >= 0.5]

    # 构建 PIT panel（匹配 crypto_carry_walkforward.py 的 build_field_panel 调用）
    panel_event_times = sorted(bar_times)
    panel = ax.build_field_panel(
        store,
        liquid,
        panel_event_times,
        ("close", "volume"),
        decision_times=[t + 86_400_000 for t in panel_event_times],
        minimum_knowledge_lag=86_400_000,
    )
    # 注入 funding 矩阵
    fmat = [[funding.get(inst, {}).get(t) for inst in panel.instruments]
            for t in panel.event_times]
    panel.matrices["funding"] = fmat
    px = panel.matrices["close"]

    # LADDER 逐层验证
    ladder_results = {}
    for name, expr in LADDER.items():
        net, times = _run_spec(panel, fmat, px, expr, slip_by_inst)
        if not net:
            ladder_results[name] = {"net_sharpe": 0.0, "max_drawdown": 1.0,
                                    "per_year": {}, "block_sharpes": [], "n_periods": 0}
            continue
        eq, v = [1.0], 1.0
        for x in net:
            v *= (1.0 + x)
            eq.append(v)
        sr = _sharpe_fn(net) if len(net) >= 2 else 0.0
        md = max_drawdown(eq) if len(net) >= 2 else 1.0
        years = _per_year(times, net)
        blks = _blocks(net, N_BLOCKS)
        ladder_results[name] = {"net_sharpe": round(sr, 3), "max_drawdown": round(md, 3),
                                "per_year": years, "block_sharpes": blks,
                                "n_periods": len(net)}

    return {"ladder": ladder_results, "n_instruments": len(liquid),
            "n_periods": len(panel.event_times)}


def main() -> None:
    """验证脚本入口。数据来源优先级：
    1. data/cache/crypto_funding.db → 直接验证
    2. 缓存 JSON → 构建 PIT Store → 验证
    """
    quarantine_legacy_backtest("tools.carry_binance_validate")
    _DB = os.path.join("data", "cache", "crypto_funding.db")
    _FUND_CACHE = os.path.join("data", "cache", "crypto_funding_rates.json")
    _KLINE_CACHE = os.path.join("data", "cache", "crypto_klines.json")
    _OUT = os.path.join("data", "cache", "carry_binance_validation.json")

    if os.path.exists(_DB):
        print(f"[carry_binance_validate] using existing PIT Store: {_DB}")
        report = run_carry_walkforward(db_path=_DB)
    elif os.path.exists(_KLINE_CACHE) and os.path.exists(_FUND_CACHE):
        print("[carry_binance_validate] building PIT Store from cache files...")
        kline_data = json.load(open(_KLINE_CACHE, encoding="utf-8"))
        funding_data = json.load(open(_FUND_CACHE, encoding="utf-8"))
        instruments = build_carry_pit_store(kline_data, funding_data)
        report = run_carry_walkforward(instruments=instruments)
    else:
        print("[carry_binance_validate] run binance_klines_fetch + binance_funding_fetch first")
        return

    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    json.dump(report, open(_OUT, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"[carry_binance_validate] report written: {_OUT}")

    if "ladder" in report:
        for name, r in report["ladder"].items():
            print(f"  {name:20s}  Sharpe={r['net_sharpe']:.3f}  "
                  f"MaxDD={r['max_drawdown']:.3f}  N={r['n_periods']}")


if __name__ == "__main__":
    main()
