# 多期滚动干跑(P&L 曲线)—— 设计 spec

> 状态:设计已与用户确认(2026-06-30)。下一步:writing-plans。
> 约定:中文叙述,代码标识符英文。安全边界见 §6,不可让。
> 由来:用户选「多期滚动干跑 + P&L 曲线」(端到端干跑流水线的深化:从单次重平衡 → 滚动跑一段时间,
> 出一条门强制、可审计的真实净值曲线)。覆盖范围:**全历史 + 周度调仓**(用户确认)。

## 1. 目标(一句话)

把 [`run_dry_run_pipeline`](../../../services/execution/dry_run_pipeline.py) 从「单次快照」升级为「滚动跑一段
历史」:逐周重平衡,**每周的增量调仓单都过真预交易门(fail-closed)** → 纸面成交 → 平均成本对账 + **资金费
现金流** → 累计成一条 **raw 与 vol-target 两条净值曲线**,落成一条可复现、门强制、哈希链审计的多期 `DryRunRun`。
这是「系统能持续运转(而非一次性)」最有力的证明,也是给资金方看的真实曲线。

## 2. 已确认决策

- **覆盖**:全历史(carry 数据 ~5.5 年)+ **周度**调仓(~280 期,审计链/算力可控)。
- **carry 的边在资金费,不在价格**:carry 组合市场中性、价格腿≈0;真收益是持仓每期收取的资金费现金流。
  必须显式建模,否则曲线误导(近乎平)。orchestrator 收一个 per-period `cash_flow_fn` 钩子,驱动器供给 carry
  的 funding 现金流。
- **复用**:`reconcile`(已测、恒等式残差≈0)算每个点的价格 MTM;`screen_intents`/`simulate_fills`/
  `GlobalAuditLog`/`DryRunStore`/干跑页 全部复用。单次快照模式 `run_dry_run_pipeline` 保留不动。
- **两条曲线**:raw + vol-target(对收益序列的廉价尾随杠杆标量,直接显示尾巴被压平)。

## 3. 组件与接口

### 3.1 `services/execution/dry_run_pipeline.py::run_multi_period_dry_run`
```
run_multi_period_dry_run(*, periods: list[int], weights_by_period: dict[int, dict[str, float]],
    prices_by_period: dict[int, dict[str, float]], gate, ctx_factory, adapter, strategy_id: str,
    aum: float, cost_bps: float = 0.0, cash_flow_fn: Callable | None = None,
    vol_target: dict | None = None) -> DryRunRun
```
`ctx_factory(intent, prices, period) -> RiskContext`(比单期版多带 prices/period,以便每期用该期参考价/时间戳装配
RiskContext)。每期内部包成 `screen_intents` 要的 `ctx_factory(intent)` 闭包:`lambda i: ctx_factory(i, prices_t, t)`。
逐期 t(确定性,墙钟在驱动器边界):
1. `targets_t = plan_target_quantities(weights_by_period[t], prices_by_period[t], aum)`(aum 为固定名义本金,
   收益由曲线复利体现;v1 用固定名义以避免复利反馈进手数,简单+可复现)。
2. `intents = build_intents(targets_t, held_book)` → **增量调仓单**(held_book 由上期成交累计而来)。
3. `screened = screen_intents(intents, gate=gate, ctx_factory=ctx_factory_at(t))` —— **每期都过门,fail-closed**。
4. `filled = simulate_fills(screened["approved"], prices_by_period[t], adapter=adapter, run_id=..., timestamp=t, cost_bps=cost_bps)`;
   把成交并进 `all_fills`;按成交更新 `held_book`(signed qty)。
5. **权益_t = `reconcile(all_fills, marks=prices_by_period[t], initial_capital=aum).equity + cumulative_cash_flow_t`**,
   其中 `cumulative_cash_flow_t += cash_flow_fn(held_book, prices_by_period[t], t)`(carry: Σ_i −rate_{i,t}·qty_i·price)。
6. `ret_t = equity_t / equity_{t-1} − 1`;进哈希链审计(每期一个 PERIOD 事件 + 拒绝/成交可选汇总)。
7. 累计 `equity_curve.append({t, equity, ret, approved, rejected, fills})`。
- 收尾:`returns = [pt.ret ...]`;`equity_curve_vt` = 对 `returns` 施 vol-target 杠杆(同 `crypto_carry_voltarget._vol_target`)
  再复利;`pnl` 摘要 = `{total_return, sharpe, max_drawdown, final_equity, total_costs, total_funding, n_periods,
  total_rejected, residual}`(residual 来自最后一期 reconcile,价格腿恒等式仍≈0)。

### 3.2 `DryRunRun` 复用(不改 store schema)
- `n_intents/n_approved/n_rejected` = 全期累计;`pnl` = 上面摘要 dict;`audit_root` = 全期链根。
- `detail = {"mode": "multi_period", "n_periods": N, "equity_curve": [...], "equity_curve_vt": [...],
  "cadence": "weekly"}`。单次快照仍写 `detail.mode` 缺省(单期)——页面据 `mode`/`equity_curve` 存在与否分支。

### 3.3 驱动器 `tools/dry_run_carry_multi.py`
- 全历史周度:取周度时间点(每 7 日或每周一);逐周 COMPOSITE 信号 → `_legs` → 权重;价格取该周 close;
  `cash_flow_fn` 闭包 carry funding(`−rate·qty·price`,rate 取自 `crypto_funding_rates.json`);
  permissive-but-real `RiskLimits` + `ctx_factory`(LIVE 全 false);跑 `run_multi_period_dry_run` → `DryRunStore.save_run`。
- 打印:期数、总收益、夏普、最大回撤、累计被门拒、raw vs vol-target 对比。墙钟仅此边界。

### 3.4 console(复用干跑页)
- `DryRunPipeline.tsx`:`latest.detail.mode === "multi_period"` → 渲染净值曲线(纯 SVG sparkline,raw + vt 两线)
  + 多期 KPI(总收益/夏普/最大回撤/期数/累计被门拒)。单期仍走现有渲染。类型 `DryRunRunRow.detail` 加可选
  `mode/n_periods/equity_curve/equity_curve_vt/cadence`。端点 payload 顶层 key 不变 → **契约测试不变**。

## 4. 数据流
逐周:权重 → 目标手数 → 增量意图 → 【过门 fail-closed】→ 纸面成交(不真发)→ 价格 MTM(reconcile)+ funding 现金流
→ 权益点 → 哈希链审计 → 累计曲线 → 多期 `DryRunRun` 落库 → 干跑页画曲线。

## 5. 测试(TDD,门绿)
- `run_multi_period_dry_run`:
  - 良性多期:N 期、equity_curve 长度 N、final_equity 与最后 reconcile+funding 一致、residual≈0。
  - **每期过门**:在第 k 期前 arm 传入 gate 的 kill-switch(latched 是永久的)→ 第 k 期起所有期 0 成交、持仓书不变。
  - **funding 现金流**:无 cash_flow_fn 时曲线≈价格腿(carry≈平);给 carry funding 后曲线随 funding 累积上行(用
    合成正 funding 验证方向)。
  - 确定性:同输入 → 同 equity_curve + 同 audit_root + 同 pnl(逐字段)。
  - vol-target:`equity_curve_vt` 与 raw 期数一致、由相同 returns 经杠杆标量推出(用已知序列验证)。
- 驱动器:import + 真跑(全历史周度)→ 落 `dry_run.db`,打印摘要;曲线非空、夏普/回撤有限。
- console:typecheck/build;浏览器验证曲线渲染真数据。
- 治理:无网络/券商 import;`research_runner` 边界不动。`scripts/validate.py` 全绿。

## 6. 容器化 / 安全边界(机器强制,不可让)
- 每期 `screen_intents` **fail-closed**;kill-switch latched → 该期全拒、零成交。五个 LIVE 标志恒 false。
- 干跑适配器**无券商 SDK、不真发**;纸面账户/资金,无真凭证。
- 核心无 RNG/无墙钟(periods/价格/funding/seed 由驱动器边界注入);确定性可复现。
- **orchestrator 绝不进 research_runner**;新代码无网络/券商 import(`services/` 仍机器强制)。
- 实盘 = 同接口换真券商适配器 + 人工批准 = **显式非目标**。

## 7. 非目标(YAGNI)
- 不做复利反馈进手数(v1 固定名义本金);不做日内/多频;不做组合优化器接入(权重来自 carry 信号直接成腿)。
- 不改 `DryRunStore` schema(多期塞进 `detail`);不改干跑端点 payload 顶层形状(契约不变)。
- 不接实盘/真券商/真单/真钱(L2);funding 现金流是**研究建模**,非真实结算。
