# 限价单 + 排队位置 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标:** 给事件驱动回测器加一个 opt-in 的限价单成交模型,在纯 bar 数据下诚实建模"非执行风险 + 排队位置",且不改 `Book`、不破坏对账恒等式。

**架构:** 新增 `limit_orders.py`(`LimitFill` 成交模型 + `RestingOrder` 挂单状态 + 纯函数式 `match`);`event_engine.run_event_backtest` 新增可选参数 `limit_fill`,为 `None` 时逐字节保持现有市价路径,非 `None` 时改走跨 bar 存活的挂单簿。成交仍走 `book.apply_fill`,对账恒等式由构造保持。

**技术栈:** 纯 Python stdlib(`dataclasses`、`math`),确定性,无 RNG,无第三方库。

## 全局约束

- 纯 Python stdlib;禁止 numpy/scipy/cvxpy;禁止任何券商/CTP/FIX SDK。
- 确定性:无 RNG、无墙钟;挂单按 `sorted(instrument_id)` 遍历。
- 失败关闭:非法参数/缺 bar/非有限权重一律 `raise`。
- opt-in 零回归:`limit_fill=None` 时 `run_event_backtest` 行为与今天逐字节相同。
- 对账恒等式 `final_equity − initial == realized_pnl + final_unrealized + dividend_income − total_cost` 必须保持(残差 < 1e-6)。
- 文档/注释用中文,代码标识符用英文。
- 提交脚注:`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- 单测隔离运行需 `PYTHONPATH=H:/code/QUANTOS`;全量门禁用 `python scripts/validate.py`。

## 文件结构

- 新建 `services/research/backtest_engine/limit_orders.py` —— `LimitFill`(配置+校验+`limit_price`/`initial_queue`/`match`)、`RestingOrder`(挂单状态)。
- 修改 `services/research/backtest_engine/event_engine.py` —— `run_event_backtest` 加 `limit_fill=None` 参数 + 挂单簿分支(受 `None` 守卫)。
- 新建 `tests/research/test_limit_orders.py` —— 单元(match)+ 集成(引擎)测试矩阵。

---

### Task 1: `LimitFill` 配置 + `RestingOrder` 状态

**Files:**
- Create: `services/research/backtest_engine/limit_orders.py`
- Test: `tests/research/test_limit_orders.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class RestingOrder` 字段 `instrument_id: str, side: int, qty_remaining: int, limit_price: float, queue_ahead: float, bars_alive: int`。
  - `@dataclass(frozen=True) class LimitFill` 字段 `limit_offset_bps: float = 0.0, queue_fraction: float = 0.25, participation: float = 0.10, expire_bars: int = 3`;方法 `limit_price(side: int, close: float) -> float`、`initial_queue(bar) -> float`。

- [ ] **Step 1: 写失败测试**

```python
import pytest
from services.research.backtest_engine.limit_orders import LimitFill, RestingOrder


def test_limitfill_rejects_bad_params():
    with pytest.raises(ValueError):
        LimitFill(participation=0.0)
    with pytest.raises(ValueError):
        LimitFill(participation=1.5)
    with pytest.raises(ValueError):
        LimitFill(queue_fraction=-0.1)
    with pytest.raises(ValueError):
        LimitFill(expire_bars=0)
    with pytest.raises(ValueError):
        LimitFill(limit_offset_bps=-1.0)


def test_limit_price_buy_below_sell_above():
    lf = LimitFill(limit_offset_bps=10.0)            # 10 bps = 0.001
    assert lf.limit_price(1, 100.0) == pytest.approx(100.0 * (1 - 0.001))
    assert lf.limit_price(-1, 100.0) == pytest.approx(100.0 * (1 + 0.001))


def test_initial_queue_is_fraction_of_volume():
    lf = LimitFill(queue_fraction=0.25)

    class _B:
        volume = 10000
    assert lf.initial_queue(_B()) == pytest.approx(2500.0)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd H:/code/QUANTOS && PYTHONPATH=. python -m pytest tests/research/test_limit_orders.py -q`
Expected: FAIL —— `ModuleNotFoundError: services.research.backtest_engine.limit_orders`

- [ ] **Step 3: 写最小实现**

```python
"""限价单成交模型 + 跨 bar 挂单状态(事件驱动回测,#3 子项)。

纯 bar(OHLCV)数据下诚实建模两件事:非执行风险(限价可能永不成交)与排队位置
(用"前置量随成交消耗"近似)。无 L2、无逐笔。确定性、纯 Python、失败关闭。
"""
from __future__ import annotations

from dataclasses import dataclass

from .costs import CostModel


@dataclass(frozen=True)
class RestingOrder:
    """一张跨 bar 存活的限价挂单(每标的至多一张)。"""
    instrument_id: str
    side: int               # +1 买,−1 卖
    qty_remaining: int      # 仍待成交的有符号股数(买为正、卖为负)
    limit_price: float
    queue_ahead: float      # 必须先于我们成交的流动性;随该价位成交量递减
    bars_alive: int         # 每 bar 递增;> expire_bars 则撤销


@dataclass(frozen=True)
class LimitFill:
    """限价成交模型。限价由决策收盘价 ± offset 导出;触及价位按 participation×volume
    与 queue_ahead 双重约束部分成交;穿透则全成。默认保守(queue_fraction>0 低估成交)。"""
    limit_offset_bps: float = 0.0
    queue_fraction: float = 0.25
    participation: float = 0.10
    expire_bars: int = 3

    def __post_init__(self):
        if self.limit_offset_bps < 0:
            raise ValueError("limit_offset_bps must be >= 0")
        if self.queue_fraction < 0:
            raise ValueError("queue_fraction must be >= 0")
        if not (0.0 < self.participation <= 1.0):
            raise ValueError("participation must be in (0, 1]")
        if self.expire_bars < 1:
            raise ValueError("expire_bars must be >= 1")

    def limit_price(self, side: int, close: float) -> float:
        off = self.limit_offset_bps / 10_000.0
        return close * (1.0 - off) if side > 0 else close * (1.0 + off)

    def initial_queue(self, bar) -> float:
        return self.queue_fraction * bar.volume
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd H:/code/QUANTOS && PYTHONPATH=. python -m pytest tests/research/test_limit_orders.py -q`
Expected: PASS(3 passed)

- [ ] **Step 5: 提交**

```bash
git add services/research/backtest_engine/limit_orders.py tests/research/test_limit_orders.py
git commit -m "feat(backtest): LimitFill 配置 + RestingOrder 状态 (#3 限价单)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `LimitFill.match` —— 单 bar 撮合(纯函数)

**Files:**
- Modify: `services/research/backtest_engine/limit_orders.py`
- Test: `tests/research/test_limit_orders.py`

**Interfaces:**
- Consumes: `RestingOrder`、`CostModel`(来自 Task 1 与现有 `costs.py`)。
- Produces: `LimitFill.match(order: RestingOrder, bar, cost: CostModel) -> tuple[int, float, float, float]`
  返回 `(filled_signed, fill_price, fee_cost, new_queue_ahead)`;不修改 `order`,由调用方应用。`filled_signed` 可为 0。

- [ ] **Step 1: 写失败测试**

```python
from quantos.contracts.data import Bar
from services.research.backtest_engine.costs import CostModel


def _bar(o, h, l, c, v=10000):
    return Bar(instrument_id="x", event_time=0, knowledge_time=0,
               open=o, high=h, low=l, close=c, volume=v)


_NOCOST = CostModel(fee_bps=0.0, slippage_bps=0.0)


def test_match_buy_not_touched():
    lf = LimitFill()
    order = RestingOrder("x", 1, 100, limit_price=99.0, queue_ahead=0.0, bars_alive=0)
    filled, price, c, q = lf.match(order, _bar(100, 101, 99.5, 100.5), _NOCOST)
    assert filled == 0 and q == 0.0


def test_match_buy_through_fills_full_at_min_open_limit():
    lf = LimitFill()
    order = RestingOrder("x", 1, 100, limit_price=99.0, queue_ahead=1e9, bars_alive=0)
    filled, price, c, q = lf.match(order, _bar(98.0, 100, 97.0, 99.5), _NOCOST)
    assert filled == 100                       # 穿透 → 全成,排队无关
    assert price == pytest.approx(98.0)        # min(open=98, limit=99)


def test_match_buy_touched_partial_bounded():
    lf = LimitFill(participation=0.1, queue_fraction=0.0)
    order = RestingOrder("x", 1, 100000, limit_price=99.0, queue_ahead=0.0, bars_alive=0)
    bar = _bar(100, 101, 99.0, 100, v=10000)   # low==limit 触及;vol_at=0.1*10000=1000
    filled, price, c, q = lf.match(order, bar, _NOCOST)
    assert filled == 1000


def test_match_queue_blocks_then_reduces():
    lf = LimitFill(participation=0.1)
    bar = _bar(100, 101, 99.0, 100, v=10000)   # vol_at=1000
    o1 = RestingOrder("x", 1, 5000, limit_price=99.0, queue_ahead=2500, bars_alive=0)
    f1, _, _, q1 = lf.match(o1, bar, _NOCOST)
    assert f1 == 0 and q1 == pytest.approx(1500.0)
    o2 = RestingOrder("x", 1, 5000, limit_price=99.0, queue_ahead=q1, bars_alive=0)
    f2, _, _, q2 = lf.match(o2, bar, _NOCOST)
    assert f2 == 0 and q2 == pytest.approx(500.0)
    o3 = RestingOrder("x", 1, 5000, limit_price=99.0, queue_ahead=q2, bars_alive=0)
    f3, _, _, q3 = lf.match(o3, bar, _NOCOST)
    assert f3 == 500 and q3 == pytest.approx(0.0)   # fillable=max(0,1000-500)=500


def test_match_larger_queue_fraction_fills_no_more():
    bar = _bar(100, 101, 99.0, 100, v=10000)
    o_small = RestingOrder("x", 1, 5000, limit_price=99.0, queue_ahead=0.0, bars_alive=0)
    o_big = RestingOrder("x", 1, 5000, limit_price=99.0, queue_ahead=900.0, bars_alive=0)
    lf = LimitFill(participation=0.1)
    f_small, *_ = lf.match(o_small, bar, _NOCOST)
    f_big, *_ = lf.match(o_big, bar, _NOCOST)
    assert f_big <= f_small                      # 排队越靠后,成交不增(保守单调)


def test_match_sell_mirror_through():
    lf = LimitFill()
    order = RestingOrder("x", -1, -100, limit_price=101.0, queue_ahead=1e9, bars_alive=0)
    filled, price, c, q = lf.match(order, _bar(102, 103, 100, 101.5), _NOCOST)
    assert filled == -100
    assert price == pytest.approx(102.0)         # max(open=102, limit=101)


def test_match_applies_slippage_and_fee():
    lf = LimitFill()
    order = RestingOrder("x", 1, 100, limit_price=99.0, queue_ahead=1e9, bars_alive=0)
    cost = CostModel(fee_bps=1.0, slippage_bps=2.0)
    filled, price, c, q = lf.match(order, _bar(98.0, 100, 97.0, 99.5), cost)
    assert price == pytest.approx(98.0 * (1.0 + 2.0 / 10_000.0))   # 买:滑点加价
    assert c == pytest.approx(abs(filled) * price * (1.0 / 10_000.0))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd H:/code/QUANTOS && PYTHONPATH=. python -m pytest tests/research/test_limit_orders.py -q`
Expected: FAIL —— `AttributeError: 'LimitFill' object has no attribute 'match'`

- [ ] **Step 3: 写最小实现**(追加到 `limit_orders.py` 的 `LimitFill` 类内)

```python
    def match(self, order: RestingOrder, bar, cost: CostModel) -> tuple[int, float, float, float]:
        """对单根 bar 撮合一张挂单。返回 (filled_signed, fill_price, fee_cost, new_queue_ahead)。
        不修改 order;filled 可为 0。买看 bar.low,卖看 bar.high(镜像)。"""
        L = order.limit_price
        new_q = order.queue_ahead
        if order.side > 0:                                   # 买
            if bar.low > L:                                  # 未触及
                return 0, 0.0, 0.0, new_q
            if bar.low < L:                                  # 穿透 → 全成
                filled = order.qty_remaining
                base_price = min(bar.open, L)
                new_q = 0.0
            else:                                            # 触及 → 部分
                vol_at = self.participation * bar.volume
                fillable = max(0.0, vol_at - order.queue_ahead)
                filled = min(order.qty_remaining, int(fillable))
                new_q = max(0.0, order.queue_ahead - vol_at)
                if filled == 0:
                    return 0, 0.0, 0.0, new_q
                base_price = L
        else:                                                # 卖(qty_remaining 为负)
            if bar.high < L:
                return 0, 0.0, 0.0, new_q
            if bar.high > L:
                filled = order.qty_remaining
                base_price = max(bar.open, L)
                new_q = 0.0
            else:
                vol_at = self.participation * bar.volume
                fillable = max(0.0, vol_at - order.queue_ahead)
                filled = -min(-order.qty_remaining, int(fillable))
                new_q = max(0.0, order.queue_ahead - vol_at)
                if filled == 0:
                    return 0, 0.0, 0.0, new_q
                base_price = L
        sign = 1.0 if filled > 0 else -1.0
        fill_price = base_price * (1.0 + sign * cost.slippage_bps / 10_000.0)
        fee = abs(filled) * fill_price * (cost.fee_bps + cost.impact_bps(abs(filled), bar.volume)) / 10_000.0
        return filled, fill_price, fee, new_q
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd H:/code/QUANTOS && PYTHONPATH=. python -m pytest tests/research/test_limit_orders.py -q`
Expected: PASS(全部 match 测试通过)

- [ ] **Step 5: 提交**

```bash
git add services/research/backtest_engine/limit_orders.py tests/research/test_limit_orders.py
git commit -m "feat(backtest): LimitFill.match 单 bar 撮合 + 排队消耗 (#3 限价单)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 引擎集成 —— 挂单簿分支 + 生命周期 + 零回归

**Files:**
- Modify: `services/research/backtest_engine/event_engine.py`
- Test: `tests/research/test_limit_orders.py`

**Interfaces:**
- Consumes: `LimitFill`、`RestingOrder`(Task 1/2);现有 `Book.apply_fill`、`EventBacktestResult`、`CostModel`。
- Produces: `run_event_backtest(..., limit_fill: Optional[LimitFill] = None)` —— 新增末位关键字参数;`None` 保持现有市价路径,非 `None` 改走挂单簿。

- [ ] **Step 1: 写失败测试**

```python
from services.research.backtest_engine.event_engine import run_event_backtest


def _aaa(o, h, l, c, v=100000):
    return Bar(instrument_id="AAA", event_time=0, knowledge_time=0,
               open=o, high=h, low=l, close=c, volume=v)


def test_limit_none_path_reconciles():
    # limit_fill=None 仍走现有市价路径,对账恒等式成立(零回归基线)
    timeline = [(t, {"AAA": _aaa(100, 101, 99, 100)}) for t in range(4)]
    targets = [{"AAA": 0.5}, {"AAA": 0.5}, {"AAA": 0.5}, {"AAA": 0.5}]
    res = run_event_backtest(timeline, targets, initial_capital=1_000_000, limit_fill=None)
    assert res.reconciliation_residual() < 1e-6


def test_limit_through_fills_and_reconciles():
    # t0 决策买 → 挂限价 100;t1 low=98<100 穿透 → 全成;对账成立
    timeline = [
        (0, {"AAA": _aaa(100, 101, 99, 100)}),
        (1, {"AAA": _aaa(100, 100, 98, 99)}),
        (2, {"AAA": _aaa(99, 100, 98, 99)}),
        (3, {"AAA": _aaa(99, 100, 98, 99)}),
    ]
    targets = [{"AAA": 0.5}] * 4
    res = run_event_backtest(timeline, targets, initial_capital=1_000_000,
                             limit_fill=LimitFill())
    assert res.final_positions.get("AAA", 0) > 0      # 成交了多头
    assert res.reconciliation_residual() < 1e-6


def test_unfilled_limit_expires_no_position():
    # 限价远低于市场(offset 50%)→ 永不触及 → 反复挂/到期 → 无持仓
    timeline = [(t, {"AAA": _aaa(100, 101, 99, 100)}) for t in range(6)]
    targets = [{"AAA": 0.5}] * 6
    res = run_event_backtest(timeline, targets, initial_capital=1_000_000,
                             limit_fill=LimitFill(limit_offset_bps=5000.0, expire_bars=2))
    assert res.final_positions == {}
    assert res.reconciliation_residual() < 1e-6


def test_target_reversal_cancels_buy_before_it_fills():
    # t0 挂买(limit 100);t1 low=100.5 未触及,决策翻成 -0.5 → 撤买;
    # t2 low=97<100 若买单还在会成多头,但已撤 → 不成多头;t2 决策挂卖;
    # t3 high=103>卖限价 → 成空头。最终为空头,证明买单确被撤。
    timeline = [
        (0, {"AAA": _aaa(100, 101, 99.5, 100)}),
        (1, {"AAA": _aaa(100, 101, 100.5, 100)}),
        (2, {"AAA": _aaa(100, 100, 97, 100)}),
        (3, {"AAA": _aaa(100, 103, 100, 100)}),
    ]
    targets = [{"AAA": 0.5}, {"AAA": -0.5}, {"AAA": -0.5}, {"AAA": -0.5}]
    res = run_event_backtest(timeline, targets, initial_capital=1_000_000,
                             limit_fill=LimitFill())
    assert res.final_positions.get("AAA", 0) < 0      # 空头,非多头
    assert res.reconciliation_residual() < 1e-6


def test_no_overshoot_under_constant_target():
    # 恒定目标 + 部分成交;持仓不越过 desired(抑制重发,无双重计数)
    timeline = [(t, {"AAA": _aaa(100, 101, 99.0, 100, v=20000)}) for t in range(8)]
    targets = [{"AAA": 0.5}] * 8
    res = run_event_backtest(timeline, targets, initial_capital=1_000_000,
                             limit_fill=LimitFill(participation=0.05, queue_fraction=0.0))
    desired = round(0.5 * 1_000_000 / 100)            # 5000 股上限
    assert 0 <= res.final_positions.get("AAA", 0) <= desired
    assert res.reconciliation_residual() < 1e-6


def test_limit_path_is_deterministic():
    timeline = [
        (0, {"AAA": _aaa(100, 101, 99, 100)}),
        (1, {"AAA": _aaa(100, 100, 98, 99)}),
        (2, {"AAA": _aaa(99, 100, 98, 99)}),
    ]
    targets = [{"AAA": 0.5}] * 3
    kw = dict(initial_capital=1_000_000, limit_fill=LimitFill())
    a = run_event_backtest(timeline, targets, **kw)
    b = run_event_backtest(timeline, targets, **kw)
    assert a.equity_curve == b.equity_curve and a.final_positions == b.final_positions
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd H:/code/QUANTOS && PYTHONPATH=. python -m pytest tests/research/test_limit_orders.py -q`
Expected: FAIL —— `TypeError: run_event_backtest() got an unexpected keyword argument 'limit_fill'`

- [ ] **Step 3: 写实现**(修改 `event_engine.py`)

3a. 顶部 import 增加:

```python
from dataclasses import dataclass, replace
```
(`replace` 追加到现有 `from dataclasses import dataclass`)

3b. 函数签名增加末位参数:

```python
def run_event_backtest(timeline: Sequence, targets: Sequence, *, initial_capital: float,
                       cost: CostModel = CostModel(), fill=None, lot_size: int = 1,
                       financing=None, corporate_actions=None, limit_fill=None) -> EventBacktestResult:
```

3c. 在 `pending: dict = {}` 之后增加挂单簿状态:

```python
    pending: dict = {}            # orders decided at i-1, to fill at i open (市价路径)
    resting: dict = {}            # inst -> RestingOrder(限价路径,limit_fill 非 None 时使用)
```

3d. 用下面的整块替换"OPEN of bar i: execute orders queued"那一段(从 `# 2) OPEN of bar i` 到 `pending = {}`):

```python
        # 2) OPEN of bar i: 成交。limit_fill=None 走现有市价 pending;否则走挂单簿撮合。
        if limit_fill is None:
            for inst, qty in pending.items():
                if inst not in bars:
                    raise ValueError(f"no bar for queued instrument {inst} at ts {ts}")
                filled, price, c = fill.fill(qty, bars[inst], cost, adv_shares=bars[inst].volume)
                if filled != 0:
                    book.apply_fill(inst, filled, price, c)
                    turnover += abs(filled) * price
            pending = {}
        else:
            for inst in sorted(resting):
                if inst not in bars:
                    raise ValueError(f"no bar for resting order instrument {inst} at ts {ts}")
                order = resting[inst]
                filled, price, c, new_q = limit_fill.match(order, bars[inst], cost)
                if filled != 0:
                    book.apply_fill(inst, filled, price, c)
                    turnover += abs(filled) * price
                    rem = order.qty_remaining - filled
                    if rem == 0:
                        del resting[inst]
                    else:
                        resting[inst] = replace(order, qty_remaining=rem, queue_ahead=new_q)
                else:
                    resting[inst] = replace(order, queue_ahead=new_q)
```

3e. 用下面的整块替换收盘决策那一段(从 `tgt = targets[i]` 到循环体末尾 `pending[inst] = delta`):

```python
        tgt = targets[i]
        for inst, w in tgt.items():
            if inst not in bars:
                raise ValueError(f"target names instrument {inst} absent at ts {ts}")
            if not math.isfinite(w):
                raise ValueError(f"non-finite target weight for {inst}")
            close = bars[inst].close
            desired = int(round(w * equity_i / close / lot_size)) * lot_size
            current = book.positions.get(inst).qty if inst in book.positions else 0
            delta = desired - current
            if limit_fill is None:
                if delta != 0:
                    pending[inst] = delta
            else:
                if inst in resting:
                    order = resting[inst]
                    reversed_dir = delta != 0 and (1 if delta > 0 else -1) != order.side
                    if desired == current or reversed_dir:
                        del resting[inst]            # 已达成或方向反转 → 撤单(纯状态移除)
                    # 否则保留:抑制重发,防双重计数
                elif delta != 0:
                    side = 1 if delta > 0 else -1
                    resting[inst] = RestingOrder(
                        instrument_id=inst, side=side, qty_remaining=delta,
                        limit_price=limit_fill.limit_price(side, close),
                        queue_ahead=limit_fill.initial_queue(bars[inst]), bars_alive=0)

        # 5) bar 末:挂单计龄 + 到期撤销(仅限价路径)
        if limit_fill is not None:
            for inst in list(resting):
                aged = replace(resting[inst], bars_alive=resting[inst].bars_alive + 1)
                if aged.bars_alive > limit_fill.expire_bars:
                    del resting[inst]
                else:
                    resting[inst] = aged
```

3f. 顶部 import 增加 `RestingOrder`:

```python
from .limit_orders import RestingOrder
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd H:/code/QUANTOS && PYTHONPATH=. python -m pytest tests/research/test_limit_orders.py -q`
Expected: PASS(全部 Task 3 测试通过)

- [ ] **Step 5: 跑现有事件回测套件确认零回归**

Run: `cd H:/code/QUANTOS && PYTHONPATH=. python -m pytest tests/research/ -q -k "event or backtest or limit"`
Expected: PASS(现有 test_event_*.py 全绿,无回归)

- [ ] **Step 6: 提交**

```bash
git add services/research/backtest_engine/event_engine.py tests/research/test_limit_orders.py
git commit -m "feat(backtest): 限价挂单簿引擎集成 + 生命周期(到期/反转/抑制重发) (#3 限价单)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 全量门禁 + 收口

**Files:** 无新增(验证 + 记忆更新)。

- [ ] **Step 1: 跑全量门禁**

Run: `cd H:/code/QUANTOS && python scripts/validate.py`
Expected: 治理扫描 0 违规 + 全部 pytest 套件绿(测试数 = 577 + 本计划新增的限价单测试)。

- [ ] **Step 2: 同步 main**

```bash
git branch -f main master
```

- [ ] **Step 3: 更新进度记忆**

更新 `program-state-complete.md`:#3 限价单/排队位置子项 DONE,REMAINING 仅剩 #3 多币种、数据湖 sp2-4、agent 进化。

## 自检

- **Spec 覆盖:** §架构/接缝→Task 3;§LimitFill 配置→Task 1;§挂单状态→Task 1;§撮合(穿透/触及/未触及/卖镜像/排队消耗/滑点)→Task 2;§生命周期(撤单/到期/反转/抑制重发)→Task 3;§确定性→Task 2(match)+Task 3(引擎);§测试矩阵 11 条→Task 1-3 全覆盖;§错误处理→Task 1(构造)+Task 3(缺 bar)。无遗漏。
- **占位符:** 无 TBD/TODO;每个代码步骤含完整代码。
- **类型一致:** `RestingOrder` 字段、`LimitFill.match` 四元组返回 `(int,float,float,float)`、`limit_price(side,close)`/`initial_queue(bar)` 签名在 Task 1-3 间一致;引擎用 `replace` 更新冻结 dataclass、`sorted(resting)` 确定性遍历。
