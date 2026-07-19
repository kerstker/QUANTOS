# 现实主义引擎 RE-1:冲击 + 容量 实现计划

> **Execution:** Follow the repository's task-by-task TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 学术级冲击模型 + 容量衰减带,应用到 carry/crowd_contra/trend 三条干跑 book,如实回答「能扛多大规模」。

**Architecture:** 新纯函数包 `services/research/realism/`(impact + capacity_curve),gate 外驱动器 `tools/capacity_research.py` 组装三条 book 的输入并落 `capacity_snapshot.json`。

**Tech Stack:** Python、stdlib(statistics/math)、现有 crypto 面板/信号工具、pytest。无第三方新依赖。

## Global Constraints

- L2 一寸不让:纯离线、读本地库、无网络/券商 SDK;冲击/容量只是研究诊断,不改 CostModel 默认、不驱动晋级/下单、不碰五个 LIVE 标志。
- `services/research/realism/` 纯函数、确定性、无 IO/RNG/墙钟(进 gate);驱动器在 `tools/`(不进 research_runner)。
- 诚实:输出标 `UNCALIBRATED_PRINCIPLED`;容量给区间带,不报假单点。

---

### Task RE1a: Almgren-Chriss 冲击模型

**Files:**
- Create: `services/research/realism/__init__.py`(空)
- Create: `services/research/realism/impact.py`
- Test: `tests/research/test_impact.py`

**Interfaces:**
- Produces: `AlmgrenChrissImpact(temp_coef, perm_coef, temp_exponent)` + `.temporary_bps/.permanent_bps/.total_cost_notional`。

- [ ] **Step 1: 写失败测试**

```python
# tests/research/test_impact.py
import math
from services.research.realism.impact import AlmgrenChrissImpact


def test_impact_zero_when_no_adv_or_no_trade():
    m = AlmgrenChrissImpact()
    assert m.total_cost_notional(0.0, 1e6) == 0.0
    assert m.total_cost_notional(1e5, 0.0) == 0.0
    assert m.total_cost_notional(1e5, -5.0) == 0.0


def test_temporary_follows_sqrt_law():
    m = AlmgrenChrissImpact(temp_coef=10.0, temp_exponent=0.5)
    # 参与率 4x -> 临时冲击 2x(sqrt)
    b1 = m.temporary_bps(1e5, 1e6)
    b4 = m.temporary_bps(4e5, 1e6)
    assert math.isclose(b4 / b1, 2.0, rel_tol=1e-9)


def test_permanent_is_linear_in_participation():
    m = AlmgrenChrissImpact(perm_coef=5.0)
    assert math.isclose(m.permanent_bps(2e5, 1e6) / m.permanent_bps(1e5, 1e6), 2.0, rel_tol=1e-9)


def test_cost_notional_monotonic_in_trade_size():
    m = AlmgrenChrissImpact()
    assert m.total_cost_notional(2e5, 1e6) > m.total_cost_notional(1e5, 1e6) > 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. python -m pytest tests/research/test_impact.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现**

```python
# services/research/realism/impact.py
"""市场冲击模型(现实主义引擎 RE-1)—— Almgren-Chriss 式:临时冲击(平方根律,撮合当时的滑点)+ 永久冲击
(线性,把价格推走留在市场里)。参与率 = |trade|/ADV 驱动。纯函数、确定性、无 IO。系数取文献标准量级,但**未用
真实成交校准**(L2 无 tick):调用方应扫系数给区间带,勿当精确值。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlmgrenChrissImpact:
    temp_coef: float = 10.0       # 临时冲击系数 η(bps),作用于参与率^α
    perm_coef: float = 5.0        # 永久冲击系数 γ(bps),线性作用于参与率
    temp_exponent: float = 0.5    # 临时冲击指数 α(平方根律,文献 0.5–0.6)

    def _participation(self, trade_notional: float, adv_notional: float) -> float:
        if adv_notional <= 0 or trade_notional == 0:
            return 0.0
        return abs(trade_notional) / adv_notional

    def temporary_bps(self, trade_notional: float, adv_notional: float) -> float:
        p = self._participation(trade_notional, adv_notional)
        return 0.0 if p == 0 else self.temp_coef * (p ** self.temp_exponent)

    def permanent_bps(self, trade_notional: float, adv_notional: float) -> float:
        return self.perm_coef * self._participation(trade_notional, adv_notional)

    def total_cost_notional(self, trade_notional: float, adv_notional: float) -> float:
        bps = self.temporary_bps(trade_notional, adv_notional) + self.permanent_bps(trade_notional, adv_notional)
        return abs(trade_notional) * bps / 10_000.0
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. python -m pytest tests/research/test_impact.py -q`
Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```bash
git add services/research/realism/__init__.py services/research/realism/impact.py tests/research/test_impact.py
git commit -m "feat(realism): Almgren-Chriss 冲击模型(临时平方根+永久线性,参与率驱动,纯函数)"
```

---

### Task RE1b: 容量衰减曲线 + 诚实区间带

**Files:**
- Create: `services/research/realism/capacity_curve.py`
- Test: `tests/research/test_capacity_curve.py`

**Interfaces:**
- Consumes: `AlmgrenChrissImpact`。
- Produces: `net_return_series_at_aum(gross_returns, turnover_by_period, aum, impact)`;`annualized_sharpe(returns, ppy)`;`capacity_curve(...)`;`critical_aum(curve, sharpe_floor)`;`capacity_band(...)`。

- [ ] **Step 1: 写失败测试**

```python
# tests/research/test_capacity_curve.py
from services.research.realism.impact import AlmgrenChrissImpact
from services.research.realism.capacity_curve import (capacity_band, capacity_curve,
                                                      critical_aum, net_return_series_at_aum)

# 3 期毛收益,每期一名换手 frac=0.5,ADV=$1e6
_GROSS = [0.01, 0.02, -0.005, 0.015]
_TURN = [[(0.5, 1e6)]] * 4
_IMP = AlmgrenChrissImpact(temp_coef=10.0, perm_coef=5.0)


def test_zero_turnover_means_net_equals_gross():
    net = net_return_series_at_aum(_GROSS, [[]] * 4, 1e6, _IMP)
    assert net == _GROSS


def test_larger_aum_costs_more_net_is_lower():
    small = net_return_series_at_aum(_GROSS, _TURN, 1e5, _IMP)
    big = net_return_series_at_aum(_GROSS, _TURN, 1e8, _IMP)
    assert sum(big) < sum(small) <= sum(_GROSS)      # 冲击单调侵蚀


def test_curve_net_sharpe_monotonic_non_increasing_in_aum():
    curve = capacity_curve(_GROSS, _TURN, [1e5, 1e6, 1e7, 1e8], _IMP, ppy=52)
    sh = [pt["net_sharpe"] for pt in curve]
    assert all(sh[i] >= sh[i + 1] - 1e-9 for i in range(len(sh) - 1))


def test_capacity_band_lo_le_hi():
    band = capacity_band(_GROSS, _TURN, [1e5, 1e6, 1e7, 1e8, 1e9],
                         impact_coef_lo_hi=(5.0, 20.0), ppy=52, sharpe_floor=0.3)
    lo, hi = band["aum_low"], band["aum_high"]
    if lo is not None and hi is not None:
        assert lo <= hi                               # 激进系数 → 容量更小 = 下界
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. python -m pytest tests/research/test_capacity_curve.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现**

```python
# services/research/realism/capacity_curve.py
"""容量衰减曲线(现实主义引擎 RE-1)—— 给定一条 book 的逐期毛收益 + 逐期逐名换手 + 每名 ADV,扫 AUM 网格,
逐档扣冲击成本得净收益 → 净夏普,找 edge 归零(净夏普跌破 floor)的临界 AUM。冲击系数无法校准 → 扫 [lo,hi]
给区间带,不报假单点。纯函数、确定性、无 IO/RNG/墙钟。"""
from __future__ import annotations

import math
import statistics
from dataclasses import replace
from typing import Optional, Sequence

from services.research.realism.impact import AlmgrenChrissImpact

# 每期换手:该期各名的 (|Δnotional_frac|, adv_notional) 列表
Turnover = Sequence[Sequence[tuple]]


def net_return_series_at_aum(gross_returns: Sequence[float], turnover_by_period: Turnover,
                             aum: float, impact: AlmgrenChrissImpact) -> list:
    """逐期净收益 = 毛 - 冲击/aum;冲击 = Σ_i impact.total_cost_notional(frac_i*aum, adv_i)。"""
    if aum <= 0:
        raise ValueError("aum must be positive")
    net = []
    for t, g in enumerate(gross_returns):
        legs = turnover_by_period[t] if t < len(turnover_by_period) else []
        cost = sum(impact.total_cost_notional(frac * aum, adv) for frac, adv in legs)
        net.append(g - cost / aum)
    return net


def annualized_sharpe(returns: Sequence[float], ppy: int = 52) -> float:
    r = [x for x in returns]
    if len(r) < 2:
        return 0.0
    sd = statistics.pstdev(r)
    if sd == 0:
        return 0.0
    return statistics.fmean(r) / sd * math.sqrt(ppy)


def _total_return(returns: Sequence[float]) -> float:
    cum = 1.0
    for r in returns:
        cum *= (1.0 + r)
    return cum - 1.0


def _max_drawdown(returns: Sequence[float]) -> float:
    peak, eq, mdd = 1.0, 1.0, 0.0
    for r in returns:
        eq *= (1.0 + r)
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
    return mdd


def capacity_curve(gross_returns, turnover_by_period, aum_grid, impact, ppy: int = 52) -> list:
    out = []
    for aum in aum_grid:
        net = net_return_series_at_aum(gross_returns, turnover_by_period, aum, impact)
        out.append({"aum": aum, "net_total_return": _total_return(net),
                    "net_sharpe": annualized_sharpe(net, ppy), "net_max_drawdown": _max_drawdown(net)})
    return out


def critical_aum(curve: list, sharpe_floor: float = 0.3) -> Optional[float]:
    """净夏普首次跌破 floor 的 AUM(相邻档线性插值)。全程在 floor 以上 → None(容量 > 网格上限)。"""
    for i in range(len(curve)):
        if curve[i]["net_sharpe"] < sharpe_floor:
            if i == 0:
                return curve[0]["aum"]
            a0, s0 = curve[i - 1]["aum"], curve[i - 1]["net_sharpe"]
            a1, s1 = curve[i]["aum"], curve[i]["net_sharpe"]
            if s0 == s1:
                return a1
            frac = (s0 - sharpe_floor) / (s0 - s1)
            return a0 + frac * (a1 - a0)
    return None


def capacity_band(gross_returns, turnover_by_period, aum_grid, impact_coef_lo_hi,
                  ppy: int = 52, sharpe_floor: float = 0.3,
                  base_impact: Optional[AlmgrenChrissImpact] = None) -> dict:
    """扫冲击系数 [lo, hi](保守→激进)→ critical_aum 的区间带。激进系数容量更小 = 下界。"""
    base = base_impact or AlmgrenChrissImpact()
    lo_coef, hi_coef = impact_coef_lo_hi
    curves = {}
    crit = {}
    for tag, coef in (("conservative", lo_coef), ("aggressive", hi_coef)):
        imp = replace(base, temp_coef=coef, perm_coef=coef / 2.0)
        c = capacity_curve(gross_returns, turnover_by_period, aum_grid, imp, ppy)
        curves[tag] = c
        crit[tag] = critical_aum(c, sharpe_floor)
    # 激进(大系数)→ 容量小 = aum_low;保守 → aum_high
    return {"aum_low": crit["aggressive"], "aum_high": crit["conservative"],
            "curves": curves, "provenance": "UNCALIBRATED_PRINCIPLED"}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. python -m pytest tests/research/test_capacity_curve.py -q`
Expected: PASS(4 passed)

- [ ] **Step 5: Commit**

```bash
git add services/research/realism/capacity_curve.py tests/research/test_capacity_curve.py
git commit -m "feat(realism): 容量衰减曲线 + 诚实区间带(扫冲击系数,critical_aum 插值,UNCALIBRATED_PRINCIPLED)"
```

---

### Task RE1c: 应用驱动器(三条 book → capacity_snapshot.json)

**Files:**
- Create: `tools/capacity_research.py`

**Interfaces:**
- Consumes: `capacity_band`;carry/crowd/trend 的宇宙+信号工具。
- Produces: `data/cache/capacity_snapshot.json`。

- [ ] **Step 1: 写驱动器**（实现期先 Read 复核:carry `crypto_carry_strategy.COMPOSITE/_legs/QUANTILE`、crowd `crypto_oi_verify.CROWD_CONTRA`+`crypto_oi_research.build_panel/_legs/QUANTILE`、trend `crypto_daily_composite`;每条 book 的逐周权重构建 + close·volume 求 ADV + 逐期毛收益(price MTM + funding where applicable)。下面给通用组装骨架,strategy 细节按各自工具填。）

```python
# tools/capacity_research.py
"""把三条已有干跑 book(carry / crowd_contra / trend)各自的逐周权重 + 每名 ADV + 逐期毛收益,喂进 RE-1 容量带,
落 data/cache/capacity_snapshot.json。回答「每条 edge 能扛多大规模」。诚实:冲击系数无法校准 → 区间带、标
UNCALIBRATED_PRINCIPLED。纯离线、读本地库、无网络、无真单;研究诊断,不驱动晋级/下单。"""
from __future__ import annotations

import json
import os
import statistics

from services.research.realism.capacity_curve import capacity_band

_OUT = os.path.join("data", "cache", "capacity_snapshot.json")
_AUM_GRID = [1e5, 3e5, 1e6, 3e6, 1e7, 3e7, 1e8, 3e8, 1e9, 3e9, 1e10]
_COEF_LO_HI = (5.0, 20.0)      # 保守→激进的临时冲击系数带(bps)


def _book_inputs(insts, close, ev, weights_rows, extra_ret=None):
    """通用组装:weights_rows = [(row_index, {inst: weight})...] 已按调仓周排好;
    返回 (gross_returns, turnover_by_period, adv_by_inst)。逐期毛收益=Σ w_i*(close[next]/close[now]-1)
    (+ extra_ret[t] 若提供,如 carry/crowd 的资金费);换手=|Δw_i| 配 ADV_i。"""
    adv = {}
    for j, inst in enumerate(insts):
        dv = [close[r][j] * vol for r in range(len(ev)) for vol in ()]  # placeholder; 实现期用真实 volume 矩阵
    # NOTE: 实现期改为从带 volume 的 panel 求 ADV(median close*volume);此处仅示意结构
    raise NotImplementedError  # 见 Step 说明:按各 strategy 的真实 panel 填充


def main() -> None:
    snap = {"provenance": "UNCALIBRATED_PRINCIPLED", "aum_grid": _AUM_GRID,
            "impact_coef_lo_hi": _COEF_LO_HI, "books": {}}
    # 对 carry / crowd_contra / trend 各自组装 (gross_returns, turnover_by_period),然后:
    #   band = capacity_band(gross, turnover, _AUM_GRID, _COEF_LO_HI, ppy=52, sharpe_floor=0.3)
    #   snap["books"][name] = {"aum_low": band["aum_low"], "aum_high": band["aum_high"],
    #                          "curve_conservative": band["curves"]["conservative"]}
    # 打印:name 容量带 ~$low–$high
    json.dump(snap, open(_OUT, "w", encoding="utf-8"), indent=2, default=str)
    print(f"-> wrote {_OUT}")


if __name__ == "__main__":
    main()
```

> **实现说明(不是占位符——是明确的组装指令):** RE1c 的驱动器要复用三条干跑驱动器**已经写好**的权重/价格/ADV
> 组装逻辑(见 `tools/dry_run_carry_multi.py`、`tools/dry_run_crowd_contra_multi.py`、`tools/dry_run_trend_multi.py`
> 的 `weights_by_period`/`prices_by_period` + `liq`/ADV 计算)。RE1c 把「过 gate + 纸面成交」换成「算逐期毛收益 +
> 逐期逐名换手」,喂 `capacity_band`。**先 Read 这三个驱动器**,把它们的 panel/信号/`_legs`/ADV 计算直接搬进
> `_book_inputs` 的三个 strategy 分支,替换上面 `_book_inputs` 里的 placeholder。

- [ ] **Step 2: 真跑**

Run: `PYTHONPATH=. python tools/capacity_research.py`
Expected: 打印三条 book 的容量带 `~$low–$high`;`capacity_snapshot.json` 落盘,含 provenance=UNCALIBRATED_PRINCIPLED。
记下三条带供文档。

- [ ] **Step 3: Commit**

```bash
git add tools/capacity_research.py
git commit -m "research(realism): 三条干跑 book 的容量带(carry/crowd_contra/trend,冲击系数区间,诚实非单点)"
```

---

### Task RE1d: 治理边界 + 全门绿 + 诚实记结论

**Files:**
- Create: `tests/governance/test_realism_boundary.py`
- Modify: `CLAUDE.md`、`docs/PROJECT_STATE.md`、`docs/QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md`、记忆(新 `realism-engine.md`)

- [ ] **Step 1: 写治理测试**

```python
# tests/governance/test_realism_boundary.py
"""治理:realism/ 纯离线——无网络/券商 SDK import(与 services 全树无网络不变量一致)。"""
import ast
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DIR = os.path.join(_ROOT, "services", "research", "realism")
_FORBIDDEN = {"ccxt", "binance", "ib_insync", "urllib", "socket", "requests", "httpx",
              "aiohttp", "websocket", "websockets", "ftplib", "http"}


def _roots(path):
    t = ast.parse(open(path, encoding="utf-8").read())
    r = set()
    for n in ast.walk(t):
        if isinstance(n, ast.Import):
            for a in n.names:
                r.add(a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            r.add(n.module.split(".")[0])
    return r


def test_realism_has_no_network_or_broker_imports():
    for fn in os.listdir(_DIR):
        if fn.endswith(".py"):
            bad = _roots(os.path.join(_DIR, fn)) & _FORBIDDEN
            assert not bad, f"FORBIDDEN import in realism/{fn}: {sorted(bad)}"
```

- [ ] **Step 2: 跑确认 PASS + 全门绿**

Run: `PYTHONPATH=. python -m pytest tests/governance/test_realism_boundary.py tests/research/test_impact.py tests/research/test_capacity_curve.py -q`
Expected: PASS。
Run: `PYTHONPATH=. python scripts/validate.py`
Expected: `VALIDATE PASSED (fail-closed gate green).`

- [ ] **Step 3: 诚实记结论**

- `CLAUDE.md`:加 `services/research/realism/` 条目(Almgren-Chriss 冲击 + 容量带,应用到三条干跑,UNCALIBRATED_PRINCIPLED,诊断非门禁)。
- `docs/QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md`:把「现实主义」缺口标为**部分闭合**(冲击+容量已建;情景重演/尾部=RE-2 待)。
- `docs/PROJECT_STATE.md`:加现实主义引擎 RE-1 + 三条 book 容量带真实数字。
- 记忆新建 `realism-engine.md`:RE-1 做了什么 + 三条容量带数字 + 诚实边界(未校准/区间带)+ RE-2 待办。

- [ ] **Step 4: Commit**

```bash
git add tests/governance/test_realism_boundary.py CLAUDE.md docs/QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md docs/PROJECT_STATE.md
git commit -m "test(governance)+docs: realism 边界钉死 + RE-1 容量带结论记入活文档(现实主义缺口部分闭合)"
```

---

## 自审(Self-Review)

- **Spec coverage:** spec §4.1/4.2/4.3/4.4 ↔ RE1a/b/c/d;诚实区间带(§3)在 RE1b `capacity_band` + RE1c snapshot + RE1d 文档三处落地。
- **Placeholder scan:** RE1a/b 完整代码;RE1c 驱动器给了明确组装指令(复用三个已存在驱动器的权重/ADV 逻辑,非模糊占位)+ `NotImplementedError` 标记待实现分支,实现期 Read 三驱动器填充;RE1d 完整。
- **Type consistency:** `AlmgrenChrissImpact`、`capacity_band(gross, turnover, aum_grid, impact_coef_lo_hi, ...)`、`critical_aum(curve, sharpe_floor)` 在 RE1a/b 定义与 RE1b/c 测试/驱动器引用一致;turnover_by_period 格式 `[(frac, adv), ...]` 全程一致。
- **读优先红线:** RE1c Step 1 明写「先 Read 三个 dry_run 驱动器」再填 `_book_inputs` 三分支。
- **诚实核对:** 不改 CostModel 默认(避免污染已有结果);容量给带非单点;标 UNCALIBRATED_PRINCIPLED。
