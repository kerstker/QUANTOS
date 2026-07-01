# 现实主义 RE-2:情景重演 + 尾部 + trend 容量 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** 历史危机情景重演 + 尾部风险核 + trend 容量,应用到三条 book,如实记录危机行为与尾部。

**Architecture:** 两个纯核 `services/research/realism/{scenario,tail_risk}.py` + gate 外驱动器 `tools/scenario_tail_research.py`(复用 RE-1 book 组装)。

**Tech Stack:** Python、stdlib(statistics/math/calendar/time)、现有 crypto 面板/信号工具、pytest。

## Global Constraints

- L2 一寸不让:纯离线、读本地库、无网络;诊断非门禁,不改任何门/CostModel、不驱动晋级/下单、不碰 LIVE 标志。
- `realism/` 纯函数、确定性、无 IO/RNG/墙钟(治理 `test_realism_boundary` glob 整目录,新文件自动覆盖)。
- 诚实:数字为准,无数据/短样本窗口显式标注。

---

### Task RE2a: 情景重演核

**Files:** Create `services/research/realism/scenario.py`;Test `tests/research/test_scenario.py`

- [ ] **Step 1: 失败测试**

```python
# tests/research/test_scenario.py
from services.research.realism.scenario import (iso_to_ms, max_drawdown_window, window_stats)

_R = [0.02, -0.05, -0.03, 0.04, -0.10, 0.06]
_TS = [iso_to_ms(d) for d in ("2022-05-01", "2022-05-08", "2022-05-15", "2022-11-06",
                              "2022-11-13", "2023-03-12")]


def test_iso_to_ms_is_utc_and_ordered():
    assert iso_to_ms("2022-05-08") > iso_to_ms("2022-05-01")
    assert iso_to_ms("1970-01-01") == 0


def test_window_stats_selects_range():
    ws = window_stats(_R, _TS, iso_to_ms("2022-05-05"), iso_to_ms("2022-05-20"))
    assert ws["n_periods"] == 2                       # 2022-05-08, 2022-05-15
    assert ws["worst_period"] == -0.05


def test_window_stats_empty_when_no_data():
    ws = window_stats(_R, _TS, iso_to_ms("2019-01-01"), iso_to_ms("2019-02-01"))
    assert ws["n_periods"] == 0 and ws["total_return"] == 0.0


def test_max_drawdown_window_finds_peak_to_trough():
    mdd = max_drawdown_window(_R, _TS)
    assert mdd["drawdown"] > 0
    assert mdd["peak_ts"] <= mdd["trough_ts"]
```

- [ ] **Step 2: 确认失败** — `PYTHONPATH=. python -m pytest tests/research/test_scenario.py -q` → ModuleNotFoundError

- [ ] **Step 3: 实现**

```python
# services/research/realism/scenario.py
"""历史情景重演(现实主义引擎 RE-2)—— 把一条 book 的逐期收益拉过命名危机窗口 + 找全程最差回撤窗口。纯函数、
确定性、无 IO/RNG/墙钟。回答「这条边在真实危机里怎么表现」。"""
from __future__ import annotations

import calendar
import time
from typing import Sequence

# 仅 crypto 数据(2020-06 起)覆盖到的真实危机窗口
CRYPTO_CRISES = [("2021-05 crash", "2021-05-10", "2021-05-31"),
                 ("2022-05 LUNA", "2022-05-05", "2022-05-20"),
                 ("2022-11 FTX", "2022-11-05", "2022-11-21"),
                 ("2023-03 banking/USDC", "2023-03-08", "2023-03-20")]


def iso_to_ms(iso: str) -> int:
    return calendar.timegm(time.strptime(iso, "%Y-%m-%d")) * 1000


def _total_return(rs: Sequence[float]) -> float:
    cum = 1.0
    for r in rs:
        cum *= (1.0 + r)
    return cum - 1.0


def _max_drawdown(rs: Sequence[float]) -> float:
    peak, eq, mdd = 1.0, 1.0, 0.0
    for r in rs:
        eq *= (1.0 + r)
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
    return mdd


def window_stats(returns, timestamps, start_ms: int, end_ms: int) -> dict:
    sel = [r for r, t in zip(returns, timestamps) if start_ms <= t <= end_ms]
    if not sel:
        return {"n_periods": 0, "total_return": 0.0, "max_drawdown": 0.0, "worst_period": 0.0}
    return {"n_periods": len(sel), "total_return": _total_return(sel),
            "max_drawdown": _max_drawdown(sel), "worst_period": min(sel)}


def max_drawdown_window(returns, timestamps) -> dict:
    """全程最差回撤峰→谷。peak_ts=回撤前净值最高点的期,trough_ts=谷底期。"""
    peak_eq, eq, mdd = 1.0, 1.0, 0.0
    peak_i, best = 0, {"peak_ts": None, "trough_ts": None, "drawdown": 0.0, "peak_to_trough_periods": 0}
    for i, r in enumerate(returns):
        eq *= (1.0 + r)
        if eq > peak_eq:
            peak_eq, peak_i = eq, i
        dd = (peak_eq - eq) / peak_eq if peak_eq > 0 else 0.0
        if dd > mdd:
            mdd = dd
            best = {"peak_ts": timestamps[peak_i], "trough_ts": timestamps[i], "drawdown": dd,
                    "peak_to_trough_periods": i - peak_i}
    return best
```

- [ ] **Step 4: 确认通过** — `PYTHONPATH=. python -m pytest tests/research/test_scenario.py -q` → PASS

- [ ] **Step 5: Commit** — `feat(realism): 历史情景重演核(危机窗口统计 + 最差回撤窗口,纯函数)`

---

### Task RE2b: 尾部风险核

**Files:** Create `services/research/realism/tail_risk.py`;Test `tests/research/test_tail_risk.py`

- [ ] **Step 1: 失败测试**

```python
# tests/research/test_tail_risk.py
from services.research.realism.tail_risk import (historical_var_cvar, hill_tail_index,
                                                lower_tail_dependence)


def test_var_le_cvar():
    rs = [0.01, -0.02, 0.03, -0.05, 0.01, -0.10, 0.02, -0.01, 0.00, -0.07]
    vc = historical_var_cvar(rs, alpha=0.90)
    assert vc["cvar"] >= vc["var"] > 0                # CVaR(尾均损)>= VaR(尾分位)


def test_fatter_tail_smaller_hill_index():
    import random
    rng = random.Random(0)
    normal = [rng.gauss(0, 1) for _ in range(2000)]
    heavy = [rng.gauss(0, 1) * (5 if rng.random() < 0.05 else 1) for _ in range(2000)]
    a_norm = hill_tail_index(normal, tail_q=0.05)
    a_heavy = hill_tail_index(heavy, tail_q=0.05)
    assert a_heavy < a_norm                           # 更肥的尾 → 更小尾指数


def test_lower_tail_dependence_bounds_and_comonotone():
    x = [float(i) for i in range(100)]
    assert lower_tail_dependence(x, x, q=0.10) == 1.0     # 完全同向 → 1
    assert lower_tail_dependence(x, x[::-1], q=0.10) == 0.0  # 完全反向 → 0
    assert lower_tail_dependence([1.0], [1.0]) is None    # 太短 → None
```

- [ ] **Step 2: 确认失败** — ModuleNotFoundError

- [ ] **Step 3: 实现**

```python
# services/research/realism/tail_risk.py
"""尾部风险(现实主义引擎 RE-2)—— 历史 VaR/CVaR(期望损失)+ Hill 尾指数(尾多肥)+ 经验下尾依赖(崩盘 beta)。
纯函数、确定性、无 IO/RNG/墙钟。"""
from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence


def historical_var_cvar(returns: Sequence[float], alpha: float = 0.95) -> dict:
    """losses = -returns;VaR = losses 的 alpha 分位;CVaR = 超过 VaR 的损失均值(期望损失)。"""
    if not (0.0 < alpha < 1.0) or len(returns) < 2:
        return {"var": 0.0, "cvar": 0.0, "alpha": alpha}
    losses = sorted(-r for r in returns)
    idx = min(len(losses) - 1, int(math.ceil(alpha * len(losses))) - 1)
    var = losses[idx]
    tail = [x for x in losses if x >= var]
    cvar = statistics.fmean(tail) if tail else var
    return {"var": var, "cvar": cvar, "alpha": alpha}


def hill_tail_index(returns: Sequence[float], tail_q: float = 0.10) -> Optional[float]:
    """Hill 估计损失右尾尾指数 α̂ = 1 / mean(ln(x_(i)/x_(k)))(取最大的 k 个损失,x_(k)=阈值)。越小=尾越肥。"""
    losses = sorted((-r for r in returns), reverse=True)
    losses = [x for x in losses if x > 0]
    k = int(len(losses) * tail_q)
    if k < 2 or len(losses) <= k or losses[k] <= 0:
        return None
    thr = losses[k]
    s = sum(math.log(losses[i] / thr) for i in range(k) if losses[i] > 0)
    return None if s <= 0 else k / s


def lower_tail_dependence(x: Sequence[float], y: Sequence[float], q: float = 0.10) -> Optional[float]:
    """经验下尾依赖 λ_L ≈ #(x<Q_x(q) ∧ y<Q_y(q)) / #(y<Q_y(q))。x,y 长度不齐或太短 → None。"""
    n = min(len(x), len(y))
    if n < 10 or not (0.0 < q < 1.0):
        return None
    x, y = list(x[:n]), list(y[:n])
    qx = sorted(x)[max(0, int(q * n) - 1)]
    qy = sorted(y)[max(0, int(q * n) - 1)]
    y_low = [i for i in range(n) if y[i] <= qy]
    if not y_low:
        return None
    both = sum(1 for i in y_low if x[i] <= qx)
    return both / len(y_low)
```

- [ ] **Step 4: 确认通过** — PASS

- [ ] **Step 5: Commit** — `feat(realism): 尾部风险核(历史VaR/CVaR + Hill尾指数 + 经验下尾依赖,纯函数)`

---

### Task RE2c: 驱动器 → scenario_tail_snapshot.json

**Files:** Create `tools/scenario_tail_research.py`

- [ ] **Step 1: 写驱动器**（实现期先 Read 复核:`tools/capacity_research.py` 的 `_carry_panel`/`_book_inputs`/`_weekly_rows`/`_adv`;`tools/crypto_oi_research.build_panel`;`tools/dry_run_trend_multi._legs_and_prices`。组装每条 book 的 gross_returns + timestamps + BTC 逐周收益;trend 从 daily 库取 volume 算 ADV。）

关键组装(复用 + 扩展 capacity_research 的 `_book_inputs`,额外返回 timestamps + BTC 周收益):
- carry:`_carry_panel()` → COMPOSITE → 逐周 gross + ts;BTC 周收益 = close[rn][btc]/close[r][btc]-1。
- crowd:`build_panel()` → CROWD_CONTRA → 同上。
- trend:`crypto_daily.db` 每 inst `fetch_bars` 求 median(close*volume)=ADV;逐周 `_legs_and_prices` 权重 → `capacity_band`(短样本标注)。
- 每条 book:`window_stats` × 4 危机 + `max_drawdown_window` + `historical_var_cvar(0.95/0.99)` + `hill_tail_index` + `lower_tail_dependence(book, btc)`。
- 落 `data/cache/scenario_tail_snapshot.json`(provenance `REAL_HISTORICAL_REPLAY`)+ 打印。

- [ ] **Step 2: 真跑** — `PYTHONPATH=. python tools/scenario_tail_research.py`;记危机窗口表现 + 尾部指标 + trend 容量供文档。

- [ ] **Step 3: Commit** — `research(realism): 三条 book 的危机情景重演 + 尾部风险 + trend 容量(REAL_HISTORICAL_REPLAY)`

---

### Task RE2d: 全门绿 + 诚实记结论

**Files:** Modify `CLAUDE.md`、`docs/QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md`、`docs/PROJECT_STATE.md`、记忆 `realism-engine.md`

- [ ] **Step 1: 全门绿** — `PYTHONPATH=. python scripts/validate.py` → `VALIDATE PASSED`(治理 `test_realism_boundary` glob 目录,自动覆盖新 scenario/tail_risk;若报错先修)。
- [ ] **Step 2: 记结论** — CLAUDE.md realism 条目补 RE-2 + 危机/尾部/trend容量真实数字;gap 文档「现实主义」标闭合更多;PROJECT_STATE + 记忆 `realism-engine.md`(RE-2 结论、是否印证「尾部=截面价差非市场beta」)。
- [ ] **Step 3: Commit** — `test+docs: RE-2 情景/尾部结论记入活文档(现实主义缺口进一步闭合)`

---

## 自审

- **Spec coverage:** spec §3.1/3.2/3.3 ↔ RE2a/b/c;§2 诚实预判 ↔ RE2c 真跑 + RE2d 记结论。
- **Placeholder scan:** RE2a/b 完整代码;RE2c 给明确组装指令(复用 capacity_research + trend_multi,先 Read)。
- **Type consistency:** `window_stats(returns,timestamps,start,end)`、`max_drawdown_window`、`historical_var_cvar(returns,alpha)`、`hill_tail_index`、`lower_tail_dependence(x,y,q)` 在 RE2a/b 定义与测试/驱动器引用一致。
- **读优先红线:** RE2c 明写先 Read capacity_research + trend_multi。
- **诚实:** 数字为准;短样本(trend)/无数据窗口标注;provenance REAL_HISTORICAL_REPLAY。
