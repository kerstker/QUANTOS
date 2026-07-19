# 特征交互复合信号(Agent 进化搜索 子项 1)

**日期:** 2026-06-25
**状态:** 已通过头脑风暴确认 → 实现
**所在层:** `services/research/`
**就绪等级:** L2_DRY_RUN_ONLY —— 仅研究;离线、确定性、失败关闭、PIT 防泄漏。

## 目标

让研究能表达**两特征的交互/复合因子**(如 动量×价值):每期把两条原子腿的每标的信号
**横截面 z-score 归一后组合**(sum=融合 / product=条件交互)成一个复合信号。这是 Agent
进化搜索(子项 2,交叉算子)的基础构件——交互由进化的"交叉"生成,而非枚举所有因子对。

## 关键约束

- 纯 Python stdlib;确定性;失败关闭;PIT 防泄漏(两腿、价格均 as_of t)。
- 复用 `SIGNAL_OPS`(价格腿)、`build_fundamental_periods` 的基本面腿逻辑、`PanelPeriod`、
  universe 门控、退市守卫、`panel_builder` 的 restatement 收敛。
- 纯加法;不改前端/后端合约/`*Payload`;agent 围栏不变。

## 非目标(YAGNI)

- 不做 3+ 腿;两腿够表达交互。
- 不做进化循环(子项 2)。本子项只交付交互构建器 + 定义/provider 桥,独立可测、待进化消费。
- combine 只做 sum / product(融合 + 条件);不做 diff/ratio。

## 架构(新建 `services/research/interaction_factors.py`)

### 腿(leg)表示

元组,两类:
- `("price", operator, window)` —— `operator∈SIGNAL_OPS`,window 整数/字符串。
- `("fund", metric, mode)` —— `mode∈{"raw","ratio_to_price"}`。

### `build_interaction_periods(price_store, fund_store, instruments, legA, legB, combine, *, event_times, fwd_horizon, universe=None) -> list[PanelPeriod]`

`combine∈{"sum","product"}`,否则 `ValueError`。每期 t(按位置取前向,复用 `_iso` 做 universe 门控):
1. 对每个 `sorted(instruments)`:universe 门控;取 `now_bar=price_store.latest_as_of(inst,t)`、
   `fwd_bar=price_store.latest_as_of(inst,t_fwd)`;退市守卫(`now_bar/fwd_bar` 非空、`now_bar.close>0`、
   `fwd_bar.event_time>now_bar.event_time`)。
2. 算两腿原始值 `rawA[i]`、`rawB[i]`(价格腿:`SIGNAL_OPS[op](_pit_closes(price_store,inst,t), int(window))`;
   基本面腿:`fund_store.latest_as_of(metric,inst,t).value`,`ratio_to_price` 则 `/now_bar.close`)。
   任一腿 None 或非有限 → 丢弃该标的。
3. 存活 <2 → 跳过该期。
4. **横截面 z-score**:`z(x)=(x-mean)/pstdev`,对存活集分别归一两腿;任一腿 `pstdev==0`(退化)→ 跳过该期。
5. **组合**:`sum`→`zA[i]+zB[i]`;`product`→`zA[i]*zB[i]`。复合值 = 该标的信号。
6. 前向收益 = `fwd_bar.close/now_bar.close-1`;emit `PanelPeriod(instruments, signals=复合, forward_returns)`。

`_pit_closes(store,inst,t)`:`store.as_of(inst,t)` → 按 event_time 收敛 restatement(取最新 knowledge 的 close)
→ 有序 closes 列表(同 `panel_builder` 现有逻辑)。

### `interaction_definition(legA, legB, combine) -> AlphaDefinition`(进化造 defn 的桥)

params 扁平字符串编码:`{family:"interaction", combine, legA_kind, legA_a, legA_b, legB_kind, legB_a, legB_b}`
(price:a=operator,b=window;fund:a=metric,b=mode)。`alpha_id=f"hx_{combine}_{legAid}_{legBid}"`(`:`→`-`),
`factor_ids=(legAid, legBid)`,`legid=f"{kind}:{a}:{b}"`。distinct hash → 诚实多重检验计数。

### `make_interaction_provider(price_store, fund_store, instruments, *, event_times, fwd_horizon, universe=None) -> Callable`

`provider(defn)`:从 `defn.params` 解出两腿 + combine → `build_interaction_periods(...)`。供进化/引导搜索直接用。

## 错误处理 / 失败关闭

- 未知 `combine` / leg kind / 价格 operator / 基本面 mode → `ValueError`。
- 腿参数非法 / 无数据 → 该标的信号 None → 丢弃(不崩)。
- 非有限原始值/复合值 → 丢弃。

## 确定性

无 RNG;`sorted(instruments)`;`statistics` 横截面统计确定;同输入同输出。

## 测试矩阵(`tests/research/test_interaction_factors.py`)

1. 价格×价格(sum):复合信号 = z(legA)+z(legB),构造期验证数值。
2. **价格×基本面(动量×价值,product)**:产出周期(旗舰用例)。
3. 横截面 z 正确:某期复合(sum)信号横截面和 ≈ 0(两腿 z 向量各和为 0)。
4. 退化期跳过:某期一腿横截面 `pstdev=0` → 该期不出。
5. universe 门控 + 退市守卫复用生效。
6. 未知 combine / leg kind → `ValueError`。
7. 确定性:两次同结果。
8. `interaction_definition`→`make_interaction_provider` 往返:params 编码两腿,provider 重建同样周期。

## 涉及文件

- 新建 `services/research/interaction_factors.py` —— `build_interaction_periods`、`interaction_definition`、`make_interaction_provider`、`_pit_closes`、`_zscore_xs`。
- 新建 `tests/research/test_interaction_factors.py`。
- 复用不动:`signals.SIGNAL_OPS`、`fundamental_factors`/`panel_builder`(`_iso`)、`PanelPeriod`、`universe.PITUniverse`。

## 安全审查

- 离线、纯 stdlib;PIT 防泄漏(两腿 as_of t);agent 围栏/权限不变。
- 纯加法;不改契约/前端/后端合约门。
