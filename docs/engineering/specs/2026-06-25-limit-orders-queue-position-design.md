# 限价单 + 排队位置(事件驱动回测,#3 子项)

**日期:** 2026-06-25
**状态:** 已通过头脑风暴确认 → 转 writing-plans
**所在层:** `services/research/backtest_engine/`(事件驱动回测器)
**就绪等级:** L2_DRY_RUN_ONLY —— 仅研究回测,无券商、无实盘面。

## 目标

在**只有 OHLCV bar 数据、没有 L2/逐笔**的前提下,把限价单真正能表达的两件事
建模出来,以提升回测真实度:

1. **非执行风险** —— 限价单可能永远不成交(逆向选择);策略不会无声地拿到它想要的价。
2. **排队位置** —— 你在该价位队列里的位置,决定了本 bar 的成交量是否轮得到你。

这是 bar 数据能诚实支持的最大真实度。我们**不**伪造 L2 订单簿,也不做逐笔撮合。

## 非目标(YAGNI)

- 不做与现有 target-weight 引擎并行的显式订单级 API(`submit_limit_order(...)`)。
  订单由目标权重决策导出。
- 不做策略逐标的提供的限价(target-weight 接口只发权重)。限价由"决策收盘价 ± 配置偏移"导出。
- 不做多币种(独立的 #3 项,延后)。
- 不改 `Book`、成本模型、公司行为,或对账恒等式。

## 架构与集成接缝

新增模块 `services/research/backtest_engine/limit_orders.py`。引擎新增一个
**可选**参数 `run_event_backtest(..., limit_fill=None)`:

- `limit_fill=None`(默认)→ 行为与今天**逐字节相同**(现有的 `pending` 市价 delta
  路径,经 `NextOpenFill`/`ParticipationFill`)。纯加法、零回归。
- `limit_fill=LimitFill(...)` → 目标权重 delta 被路由进一个**跨 bar 存活的挂单簿**,
  取代一次性的 `pending` 字典。

成交始终走未改动的 `book.apply_fill(inst, filled, price, cost)`。因此全局对账恒等式

```
final_equity − initial == realized_pnl + final_unrealized + dividend_income − total_cost
```

由构造保持:未成交/已撤销的订单从不触碰现金或持仓,故无法扰动恒等式。换手仅在
真实成交时累加(`abs(filled) × price`),与今天一致。

### `LimitFill` 配置

```
LimitFill(
    limit_offset_bps: float = 0.0,   # 限价 = 收盘价 × (1 ∓ offset):买在下方,卖在上方
    queue_fraction:   float = 0.25,  # 初始 queue_ahead = queue_fraction × 下单那根 bar 的 volume(保守)
    participation:    float = 0.10,  # 在被触及价位上、本 bar 可成交的 volume 比例(与 ParticipationFill 一致)
    expire_bars:      int   = 3,     # GTC 存活期;未成交订单超过此 bar 数后撤销
)
```

所有旋钮默认偏保守:`queue_fraction > 0` 意味着我们假设自己起步就排在一部分挂单之后,
这会**低估成交、高估非执行风险** —— 对安全关键研究平台是正确方向(绝不高估策略可执行性)。

## 挂单状态

```
RestingOrder(
    instrument_id: str,
    side: int,            # +1 买,−1 卖
    qty_remaining: int,   # 仍待成交的有符号股数
    limit_price: float,
    queue_ahead: float,   # 必须先于我们成交的流动性;随该价位成交量递减
    bars_alive: int,      # 每 bar 递增;超过 expire_bars 则撤销
)
```

每标的同一时刻只有一张挂单(target-weight 每标的只有单一目标持仓,无需叠两张)。

## 每 bar 处理顺序(确定性)

每个 `(ts, bars)` 按固定顺序处理:

1. **公司行为**作用于持有仓位(与今天一致)。
2. **限价单撮合**(新)—— 挂单按 `sorted(inst)` 顺序遍历:

   ```
   挂买单于限价 L(卖单为镜像,看 bar.high / +offset):
     if bar.low > L:            # 未触及
         不成交;queue_ahead 不变
     elif bar.low < L:          # 价格穿透限价 → 队列被清空
         filled = qty_remaining
         fill_price = min(bar.open, L)        # 跳空穿透按更优的开盘价成交
     else:                      # bar.low == L → 仅触及未穿透 → 部分成交
         vol_at_level = participation × bar.volume
         fillable = max(0, vol_at_level − queue_ahead)
         filled   = min(qty_remaining, fillable)
         queue_ahead = max(0, queue_ahead − vol_at_level)   # 本 bar 成交量消耗我们的队列
     若 filled ≠ 0:
         fill_price ×= (1 ± slippage_bps/1e4)               # 滑点入价(符号按方向)
         cost = abs(filled) × fill_price × (fee_bps + impact_bps(abs(filled), bar.volume)) / 1e4
         book.apply_fill(inst, filled, fill_price, cost); turnover += abs(filled) × fill_price
         qty_remaining −= filled
   ```

   卖单镜像:触发看 `bar.high`(`bar.high < L_sell` → 未触及,`bar.high > L_sell` → 穿透,
   `==` → 触及/部分),跳空穿透 `fill_price = max(bar.open, L_sell)`。

3. **市价 `pending`** 路径 —— 仅当 `limit_fill is None` 时启用(与挂单簿互斥,同一决策绝不并用)。
4. **收盘标记 + 融资 + 记录权益**(不变)。
5. **目标决策**(新增:与挂单簿对账,见下)。

## 生命周期与 target-tracking 交接

收盘决策时,对每个有目标权重 `w` 的标的:

```
desired = round(w · equity / close / lot) · lot
delta   = desired − current_position
若该 inst 有在挂单:
    若 desired == current_position:        已达成 → 撤单
    否则若 sign(delta) != resting.side:    方向反转 → 撤单
    否则:                                  保留(抑制重发 —— 防双重计数)
否则若 delta != 0:
    新挂 RestingOrder(side=sign(delta), qty_remaining=delta,
                      limit_price = 买? close×(1−off) : close×(1+off),
                      queue_ahead = queue_fraction × bar.volume,
                      bars_alive  = 0)
```

每 bar 末:每张挂单 `bars_alive += 1`;`bars_alive > expire_bars` 者撤销(移除)。
部分成交的订单保留剩余量与已累积(递减)的 `queue_ahead`,继续挂着直到成交/到期/反转。

撤单是纯状态移除 —— 无任何现金或持仓影响。

## 错误处理 / 失败关闭

- 挂单标的在当前 bar 缺失 → `ValueError`(与现有 `pending` 路径"队列标的无 bar"同策略)。
- 目标权重非有限 → `ValueError`(不变)。
- `queue_fraction < 0`、`participation <= 0 或 > 1`、`expire_bars < 1`、
  `limit_offset_bps < 0` → 在 `LimitFill` 构造时 `ValueError`。

## 确定性

无 RNG、无墙钟。挂单按 `sorted(instrument_id)` 遍历;整数股;定点运算。同输入 →
逐字节同输出(由测试断言)。

## 测试矩阵(`tests/research/test_limit_orders.py`,测试先行)

1. `limit_fill=None` 路径与 `NextOpenFill` 基线逐字节相同(零回归)。
2. 未触及(`low > limit`)→ 不成交,订单存活到下一 bar。
3. 穿透(`low < limit`)→ 全成,价 `min(open, limit)`。
4. 触及(`low == limit`)→ 部分成交,同时受 `participation` 与 `queue_ahead` 约束;
   `queue_fraction` 越大成交越少(保守性单调)。
5. 排队跨 bar 累积:连续触及多 bar,`queue_ahead` 递减直至后续 bar 成交(核心真实度主张)。
6. `expire_bars` 到期未成交 → 撤单;持仓不变。
7. 目标反转 → 立即撤单。
8. 在挂期间抑制重发(无双重计数:持仓不越过 desired)。
9. **对账恒等式残差 ≈ 0**,在"部分成交 + 全成 + 未成交/撤单"混合场景下 —— 平台核心不变量。
10. 确定性:同输入两次跑结果相等。
11. 卖单镜像:挂卖限价单在 `bar.high` 穿越时成交。

## 涉及文件

- `services/research/backtest_engine/limit_orders.py` —— `LimitFill`、`RestingOrder`。
- `services/research/backtest_engine/event_engine.py` —— 加法式 `limit_fill` 参数 +
  挂单簿分支(受 `None` 守卫;保持现有路径)。
- `tests/research/test_limit_orders.py` —— 上述测试矩阵。

## 安全审查

- 无券商 / FIX / CTP / 任何下单;这是对历史 bar 的研究回测成交模型。
- 无实盘面、无凭证、不改变 agent 权限。
- 纯加法、opt-in;默认路径不变;对账恒等式由构造保持。
