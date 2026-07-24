# Binance 真实数据 Carry 验证 · 实现计划

> **执行说明：** 这是历史研究计划；按当前仓库的普通测试优先流程执行，不依赖外部工作流插件或审批技能。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 用 Binance 真实永续合约数据重跑 carry alpha 全量验证，比对合成数据基线，判断信号真实性。

**架构：** 全复用现有工具链 — `binance_klines_fetch.py` + `binance_funding_fetch.py` 拉取数据 → 最小适配脚本写入 PIT Store → `crypto_carry_walkforward.py` 跑验证 → 比对报告。不新建任何 service/ 模块。

**技术栈：** Python 3.13 stdlib + 现有 services/data/persistent_store + 现有 tools/ 抓取脚本

---

## 文件结构

| 文件 | 职责 | 操作 |
|------|------|:--:|
| `tools/binance_klines_fetch.py` | 抓取 Binance 日频 K 线（公开 API，无需 Key） | 只运行 |
| `tools/binance_funding_fetch.py` | 抓取 Binance 资金费率（公开 API，无需 Key） | 只运行 |
| `tools/binance_oi_metrics_fetch.py` | 抓取 Binance 持仓量数据 | 只运行 |
| `tools/carry_binance_validate.py` | **新建**。串联管道：读 Binance 数据 → 写 PIT Store → 跑 carry 回测 → 输出对比报告 | ~150 行 |
| `tools/crypto_carry_walkforward.py` | 已有 carry walkforward 验证逻辑 | 被调用 |
| `tools/crypto_carry_strategy.py` | 已有 carry 信号定义 | 导入复用 |
| `services/data/persistent_store.py` | 已有 PIT Store | 直接使用 |
| `services/alpha/evaluator.py` | 已有 AlphaEvaluator | 第二阶段才用 |

---

### 任务 1：拉取 Binance 真实数据

**文件：**
- 运行：`tools/binance_klines_fetch.py`
- 运行：`tools/binance_funding_fetch.py`
- 运行：`tools/binance_oi_metrics_fetch.py`

- [ ] **步骤 1：拉取 Binance USDT 永续合约日频 K 线**

```bash
# 拉取全量 USDT 永续合约日频数据（公开 API，无需 Key）
# 确保 data/cache/ 目录存在
cd h:\code\QUANTOS
python tools/binance_klines_fetch.py
```

预期：`data/cache/` 下生成 klines 相关缓存文件。

- [ ] **步骤 2：拉取资金费率数据**

```bash
python tools/binance_funding_fetch.py
```

预期：`data/cache/crypto_funding_rates.json` 更新为最新数据。

- [ ] **步骤 3：拉取持仓量数据**

```bash
python tools/binance_oi_metrics_fetch.py
```

预期：`data/cache/` 下生成 OI 缓存文件。

---

### 任务 2：构建 Carry 验证脚本

**文件：**
- 创建：`tools/carry_binance_validate.py`
- 测试：`tests/tools/test_carry_binance_validate.py`

- [ ] **步骤 1：编写失败的测试 — 验证数据写入 PIT Store 后可读取**

```python
"""carry_binance_validate: 从 Binance 真实数据验证 carry alpha。"""
import json, os, tempfile
import pytest
from services.data.persistent_store import SqlitePITStore
from tools.carry_binance_validate import build_carry_pit_store


def test_build_carry_pit_store_writes_readable_bars(tmp_path):
    """将 Binance 真实数据写入 PIT Store 后，可正确读取 Bar 和 funding 字段。"""
    # 准备最小样本数据: 2 个币, 3 个时间点
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
        kline_data, funding_data, db_path=db_path, snapshot_dir=tmp_path,
    )

    store = SqlitePITStore(db_path, create=False)
    bars = store.as_of(instruments, 1609632000)
    assert len(bars) == 2  # 2 个币
    assert bars[0].close > 0
    assert bars[0].funding is not None
    store.close()
```

- [ ] **步骤 2：运行测试验证失败**

```bash
python -m pytest tests/tools/test_carry_binance_validate.py::test_build_carry_pit_store_writes_readable_bars -v --tb=short
```

预期：FAIL，报错 `ModuleNotFoundError: No module named 'tools.carry_binance_validate'`

- [ ] **步骤 3：实现最小 `build_carry_pit_store()`**

```python
"""carry_binance_validate: 从 Binance 真实数据构建 carry PIT Store 并验证。"""
from __future__ import annotations

import json
import os
import statistics
import time
from collections import defaultdict

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research.backtest_engine.metrics import sharpe, max_drawdown


def build_carry_pit_store(kline_data: dict, funding_data: dict, *,
                          db_path: str = "", snapshot_dir: str = "") -> list[str]:
    """将 Binance K线 + 资金费率数据写入 PIT Store，返回 instrument 列表。

    每个 Bar 的 `funding` 字段从 `funding_data` 填入，用于 carry alpha 信号计算。
    """
    if not db_path:
        db_path = os.path.join("data", "cache", "crypto_funding.db")
    if not snapshot_dir:
        snapshot_dir = os.path.join("data", "cache")

    store = SqlitePITStore(db_path, create=True)
    instruments = sorted(kline_data.keys())

    # 收集所有 event_time
    all_times: set[int] = set()
    for sym, info in kline_data.items():
        for k in info["klines"]:
            all_times.add(int(k[0]))

    # 按时间写入 bars，含 funding 字段
    for et in sorted(all_times):
        bars = []
        for sym in instruments:
            info = kline_data[sym]
            kline = next((k for k in info["klines"] if int(k[0]) == et), None)
            if kline is None:
                continue
            # 将 funding 数据附加到 bar 上（通过扩展字段）
            fv = funding_data.get(sym, {}).get(str(et))
            bar = {
                "instrument_id": sym,
                "event_time": et,
                "knowledge_time": et,
                "open": float(kline[1]),
                "high": float(kline[2]),
                "low": float(kline[3]),
                "close": float(kline[4]),
                "volume": float(kline[5]),
                "funding": float(fv) if fv is not None else None,
            }
            bars.append(bar)
        store.store_bars(instruments, bars)

    store.close()
    return instruments
```

- [ ] **步骤 4：运行测试验证通过**

```bash
python -m pytest tests/tools/test_carry_binance_validate.py::test_build_carry_pit_store_writes_readable_bars -v --tb=short
```

预期：PASS

- [ ] **步骤 5：扩展 — 添加验证运行测试**

```python
def test_carry_walkforward_runs_on_binance_data(tmp_path):
    """用 carry walkforward Ladder 在 Binance 数据上产出有效 Sharpe。"""
    from tools.carry_binance_validate import run_carry_walkforward

    # 使用与上测试相同的最小样本
    kline_data = {
        "BTCUSDT": {"klines": [[1609459200000 + i * 86400000, str(29000 + i * 10),
            str(29100 + i * 10), str(28900 + i * 10), str(29050 + i * 10), "100"]
            for i in range(60)]},
        "ETHUSDT": {"klines": [[1609459200000 + i * 86400000, str(730 + i),
            str(740 + i), str(720 + i), str(735 + i), "200"] for i in range(60)]},
        "BNBUSDT": {"klines": [[1609459200000 + i * 86400000, str(30 + i * 0.1),
            str(31 + i * 0.1), str(29 + i * 0.1), str(30.5 + i * 0.1), "150"]
            for i in range(60)]},
        "SOLUSDT": {"klines": [[1609459200000 + i * 86400000, str(20 + i * 0.05),
            str(21 + i * 0.05), str(19 + i * 0.05), str(20.5 + i * 0.05), "80"]
            for i in range(60)]},
        "ADAUSDT": {"klines": [[1609459200000 + i * 86400000,
            str(0.3 + i * 0.001), str(0.31 + i * 0.001), str(0.29 + i * 0.001),
            str(0.305 + i * 0.001), "500"] for i in range(60)]},
    }
    import random; random.seed(42)
    funding_data = {
        sym: {str(1609459200000 + i * 86400000): round(random.uniform(-0.001, 0.001), 6)
              for i in range(60)} for sym in kline_data
    }

    db_path = os.path.join(tmp_path, "test_carry.db")
    instruments = build_carry_pit_store(kline_data, funding_data,
                                         db_path=db_path, snapshot_dir=tmp_path)
    report = run_carry_walkforward(db_path=db_path, instruments=instruments)
    assert "ladder" in report
    assert len(report["ladder"]) == 3  # raw, z_decay7, composite
```

- [ ] **步骤 6：运行新测试验证失败**

```bash
python -m pytest tests/tools/test_carry_binance_validate.py::test_carry_walkforward_runs_on_binance_data -v --tb=short
```

预期：FAIL，`run_carry_walkforward` 未定义

- [ ] **步骤 7：实现 `run_carry_walkforward()` — 复用 `crypto_carry_walkforward.py` 的核心逻辑**

```python
def run_carry_walkforward(*, db_path: str = "", instruments: list[str] | None = None,
                          snapshot_dir: str = "") -> dict:
    """在 PIT Store 上跑 carry walkforward 验证，返回对比报告。

    直接复用 `crypto_carry_strategy.py` 的信号定义和 `crypto_carry_walkforward.py`
    的 Ladder + 分年/分块逻辑。不重复实现任何已有 logic。
    """
    if not db_path:
        db_path = os.path.join("data", "cache", "crypto_funding.db")
    if not snapshot_dir:
        snapshot_dir = os.path.join("data", "cache")

    from services.data.persistent_store import SqlitePITStore
    from services.data.caching_store import CachingPITStore
    from services.research import alpha_expression as ax
    from services.research.backtest_engine.metrics import sharpe as _sharpe_fn
    from tools.crypto_carry_strategy import FUND, QUANTILE, _legs
    from tools.crypto_carry_stress import _slip_bps

    store = CachingPITStore(SqlitePITStore(db_path, create=False))
    if instruments is None:
        snap_path = os.path.join(snapshot_dir, "crypto_funding_snapshot.json")
        if os.path.exists(snap_path):
            snap = json.load(open(snap_path, encoding="utf-8"))
            instruments = sorted(snap.get("meta", {}).get("coins", []))

    # 使用 `crypto_carry_walkforward.py` 的 LADDER 和 验证逻辑
    from tools.crypto_carry_walkforward import LADDER, _run_spec, _per_year, _blocks, N_BLOCKS

    # 构建 panel: 按时间取 bar 数据
    event_times = sorted(set(b.event_time for b in store.as_of(instruments, 0)))
    if len(event_times) < 60:
        return {"error": f"insufficient data: {len(event_times)} periods"}

    # 每个时间点构建 panel
    panels = []
    px_data = []
    f_data = []
    for et in event_times:
        bars = store.as_of(instruments, et)
        panel = {"instruments": instruments, "event_times": et,
                 "fields": {"funding": [], "close": []}}
        funding_row = []
        close_row = []
        for inst in instruments:
            bar = next((b for b in bars if b.instrument_id == inst), None)
            if bar:
                close_row.append(bar.close)
                funding_row.append(getattr(bar, "funding", None))
            else:
                close_row.append(None)
                funding_row.append(None)
        panel["fields"]["funding"] = funding_row
        panel["fields"]["close"] = close_row
        panels.append(panel)
        px_data.append(close_row)
        f_data.append(funding_row)

    # 按币计算滑点（复用 _slip_bps）
    slip_by_inst = {inst: _slip_bps(inst) for inst in instruments}

    ladder_results = {}
    for name, expr in LADDER.items():
        net, times = _run_spec(panels, f_data, px_data, expr, slip_by_inst)
        sr = _sharpe_fn(net) if len(net) >= 2 else 0.0
        md = max_drawdown(net) if len(net) >= 2 else 1.0
        years = _per_year(times, net)
        blks = _blocks(net, N_BLOCKS)
        ladder_results[name] = {
            "net_sharpe": round(sr, 3),
            "max_drawdown": round(md, 3),
            "per_year": years,
            "block_sharpes": [round(_sharpe_fn(b), 3) for b in blks],
            "n_periods": len(net),
        }

    store.close()
    return {"ladder": ladder_results, "n_instruments": len(instruments),
            "n_periods": len(event_times)}
```

- [ ] **步骤 8：运行测试验证通过**

```bash
python -m pytest tests/tools/test_carry_binance_validate.py -v --tb=short
```

预期：2 passed

---

### 任务 3：验证脚本 main() 入口 + 输出报告

**文件：**
- 修改：`tools/carry_binance_validate.py`（添加 main() 入口）

- [ ] **步骤 1：添加 `main()` 入口 — 读真实 Binance 数据 → 构建 PIT Store → 跑验证 → 输出 JSON 报告**

```python
def main() -> None:
    """验证脚本入口：从本地缓存读 Binance 数据，构建 PIT Store，跑 carry 验证。

    数据来源优先级:
    1. data/cache/crypto_funding.db (已有 PIT Store) → 直接验证
    2. tools/ 缓存文件 → 构建 PIT Store → 验证
    """
    _DB = os.path.join("data", "cache", "crypto_funding.db")
    _FUND_CACHE = os.path.join("data", "cache", "crypto_funding_rates.json")
    _KLINE_CACHE = os.path.join("data", "cache", "crypto_klines.json")
    _OUT = os.path.join("data", "cache", "carry_binance_validation.json")

    # 优先用已有 PIT Store
    if os.path.exists(_DB):
        print(f"[carry_binance_validate] 使用已有 PIT Store: {_DB}")
        report = run_carry_walkforward(db_path=_DB)
    elif os.path.exists(_KLINE_CACHE) and os.path.exists(_FUND_CACHE):
        print("[carry_binance_validate] 从缓存文件构建 PIT Store...")
        kline_data = json.load(open(_KLINE_CACHE, encoding="utf-8"))
        funding_data = json.load(open(_FUND_CACHE, encoding="utf-8"))
        instruments = build_carry_pit_store(kline_data, funding_data)
        report = run_carry_walkforward(instruments=instruments)
    else:
        print("[carry_binance_validate] 请先运行 binance_klines_fetch.py + binance_funding_fetch.py")
        return

    # 输出报告
    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    json.dump(report, open(_OUT, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"[carry_binance_validate] 报告已写入: {_OUT}")

    # 打印摘要
    if "ladder" in report:
        for name, r in report["ladder"].items():
            print(f"  {name:20s}  Sharpe={r['net_sharpe']:.3f}  MaxDD={r['max_drawdown']:.3f}  N={r['n_periods']}")


if __name__ == "__main__":
    main()
```

- [ ] **步骤 2：运行验证**

```bash
python tools/carry_binance_validate.py
```

预期：若已有 PIT Store，直接输出 carry ladder 各层级的 Sharpe / MaxDD。

- [ ] **步骤 3：Commit**

```bash
git add tools/carry_binance_validate.py tests/tools/test_carry_binance_validate.py
git commit -m "feat(tools): 添加 carry_binance_validate — Binance 真实数据 carry 验证脚本"
```

---

### 任务 4：数据管道验证 + 对比报告

**文件：**
- 修改：`tools/carry_binance_validate.py`（补充对比报告逻辑）

- [ ] **步骤 1：添加 PIT Store → PanelPeriods 的标准转化（供后续 evaluator 使用）**

```python
def pit_store_to_panel_periods(store, instruments: list[str],
                                fwd_horizon: int = 1) -> list:
    """将 PIT Store 转化为标准 PanelPeriod 列表，供 AlphaEvaluator 使用。

    直接复用 `services/research/panel_builder.py` 的 `build_panel_periods` —
    carry 的 funding 信号需要 `field="funding"` 的 panel。
    """
    from services.research.panel_builder import build_panel_periods

    event_times = sorted(set(b.event_time for b in store.as_of(instruments, 0)))
    # 使用 funding 字段作为信号源
    def funding_signal(closes):
        # carries: signal uses field="funding" in alpha_expression
        return closes  # panel_builder 负责构建 per-period signals

    return build_panel_periods(
        store, instruments,
        event_times=event_times,
        fwd_horizon=fwd_horizon,
        signal_fn=funding_signal,
    )
```

此步骤无需单独测试 — `build_panel_periods` 已有完整测试覆盖。

- [ ] **步骤 2：Commit**

```bash
git add tools/carry_binance_validate.py
git commit -m "feat(tools): carry_binance_validate 支持 PIT Store → PanelPeriods 标准转化"
```

---

## 执行检查清单

- [ ] 任务 1：拉取 Binance 真实数据（kline + funding + OI）
- [ ] 任务 2：构建 `carry_binance_validate.py` 核心 + 测试
- [ ] 任务 3：添加 main() 入口 + 输出 JSON 报告
- [ ] 任务 4：数据管道验证 + PanelPeriods 转化（为第二阶段预留接口）

---

**计划版本**: v1.0  
**总工作量**: ~2 小时  
**新代码**: ~150 行（全在 tools/ 目录）  
**修改文件**: 0  
**复用组件**: `crypto_carry_walkforward.py` · `crypto_carry_strategy.py` · `SqlitePITStore` · `CachingPITStore` · `binance_klines_fetch.py` · `binance_funding_fetch.py`
