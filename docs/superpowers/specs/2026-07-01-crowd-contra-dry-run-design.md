# crowd_contra 走干跑流水线 设计

> L2_DRY_RUN_ONLY,一寸不让。把已过 8 项证伪的候选第二边 `crowd_contra`(拥挤反向 OI 因子,截面 IC
> t-stat 4.32)送进 carry/trend 都走过的那条**策略无关**干跑流水线,看统计显著的 IC 在**执行现实**
> (整手 + 门 + 成本 + 换手)之后还剩多少。读本地库、无网络、无真单/真钱/真凭证。

## 1. 背景与目标

`crypto_oi_verify.py` 对 `crowd_contra = cs_rank(neg(global_ls))`(「反着做拥挤的散户多空仓位」)跑了 8 项
对抗性证伪(per-year / 成本扫 / 分位 / 子集 / 执行滞后 / 与 carry 合并 / embargo OOS / 方向 sanity),verdict:
**SURVIVES 4/5 → proceed to paper-track**。轨道2 的 IC 面板又给了它横向对比证据(t-stat 4.32、对 carry 增量 IR
1.13)。但它**从未经过执行现实**——整手化、逐周过门、平均成本+换手叠加后的可复现净值曲线。

**本项目:** 一个新驱动器让 crowd_contra 走 `run_multi_period_dry_run`(carry/trend 已验证的策略无关执行骨架),
产出可复现、审计留痕的净值曲线,落 `dry_run.db` 与 carry/trend 并列,并如实记录结论。

**双重价值:** ① 对 crowd_contra 的执行现实检验;② 第三条**结构完全不同**(截面拥挤反向,非 carry 非 trend)的
策略过同一流水线,再证流水线策略无关。

## 2. 架构(新驱动器,gate 外工具)

新建 `tools/dry_run_crowd_contra_multi.py`。**不进** `services/`、**不进** `research_runner` 白名单;墙钟/seed
仅此边界。紧凑平行 `tools/dry_run_carry_multi.py`(流水线接线)+ `tools/crypto_oi_verify.py`(OI 面板与信号)。

### 2.1 宇宙 / 数据 / 信号

- 复用 `tools.crypto_oi_research.build_panel()` —— 权威 OI 大币面板(crowd_contra 就在其上验证):`close` /
  `funding` / `global_ls` 矩阵,PIT 对齐、流动大币宇宙。
- 信号:`CROWD_CONTRA = ("cs_rank", ("neg", ("field", "global_ls")))`(复用 `crypto_oi_verify.CROWD_CONTRA`),
  `ax.evaluate(CROWD_CONTRA, panel)`。
- 逐周(≥ `_WEEK_MS`)取样;每周 `_legs(sig[r], QUANTILE)`(复用 `crypto_oi_research._legs`/`QUANTILE`,分位 0.3)
  → 美元中性截面多空权重(0.5 多 / -0.5 空,index 键)。

### 2.2 流水线接线(照抄 carry_multi 的装配)

- `periods` / `weights_by_period` / `prices_by_period` 逐周构建(权重 index→instrument 名,价格取 >0)。
- `cash_flow_fn`:资金费现金流 `sum_i(-rate_i * qty_i * price_i)`(持永续,funding 是真实现金流——诚实全经济版,
  与 carry driver 一致);rate 取该周 `funding` 矩阵值。
- `PreTradeRiskGate(limits, kill_switch=KillSwitch())` + `ctx_factory`(paper 账户、`strategy_id="crowd_contra"`)
  照抄 carry_multi(研究用宽 limits;门仍 fail-closed 逐意图过)。
- `run_multi_period_dry_run(..., strategy_id="crowd_contra", aum=1_000_000, cost_bps=10.0, cash_flow_fn=...,
  vol_target={target_ann:0.10, lookback:8, lev_cap:3.0, ppy:52})`。
- **成本**:平 10 bps(大币偏保守;对上证伪里「net@10bps 扛过」那条证据)。

### 2.3 落库(追加,不清库)

- `DryRunStore(_OUT, create=not os.path.exists(_OUT)).save_run(run)` —— **追加**(照抄 trend_multi),与
  carry + trend 共存于 `data/cache/dry_run.db`;console 干跑流水线历史表三条并列。
- `dry_run.db` 靠 carry → trend → crowd_contra 顺序跑齐(carry driver 会先清库再写,故 crowd_contra 最后追加)。

## 3. 数据流

```
build_panel() -> {close, funding, global_ls}
  每周 r: sig[r]=evaluate(CROWD_CONTRA) -> _legs(q=0.3) -> 美元中性 L/S 权重
run_multi_period_dry_run:
  逐周调仓单 -> PreTradeRiskGate.evaluate(fail-closed + kill-switch) -> 干跑适配器纸面成交(绝不真发)
    -> reconcile(价格 MTM + 资金费现金流) -> raw + vol-target 净值曲线 -> DryRunRun
  -> DryRunStore.save_run(append) -> dry_run.db(carry + trend + crowd_contra)
```

## 4. 产出与诚实定性

打印 + 落库:`total_return` / `sharpe`(周→年)/ `max_drawdown` / `total_funding` / 对账残差(≈0)/ 门批准·拒绝数 /
raw·vol-target 曲线点数。**如实记录结论**:统计显著的 IC(t-stat 4.32)在整手 + 门 + 10bps + 换手之后的净值表现——
证伪已过 **≠** 执行现实必过;数字是多少就是多少,写进 CLAUDE.md/PROJECT_STATE/记忆。

## 5. 安全与约束

- 读本地库、无网络、无真单/真钱/真凭证;五个 LIVE 标志由 `SafetyConfig()`(RiskContext 内)恒 false。
- 驱动器在 `tools/`(gate 外),不进 `services/`、不进 `research_runner`;不改流水线核心、不改 carry/trend 驱动器。
- 墙钟/seed 仅驱动边界;核心确定性不变。

## 6. 非目标

不接前向 paper-track(那是另一条路,缓存上是留尾回放,已明确不选);不动 `run_multi_period_dry_run`/门/adapter;
不做新 console 端点(复用现有 dry-run 端点自动多一条);不真上实盘。

## 7. 文件清单

- 新建 `tools/dry_run_crowd_contra_multi.py`。
- 改 `CLAUDE.md`、`docs/PROJECT_STATE.md`、记忆 `crowd-contra-and-ic-panel.md`:记 crowd_contra 干跑结论。
- (可选)若现有 dry-run 驱动器有轻量 smoke 测试模式则复用;否则驱动器为 gate 外工具,以真跑输出为验证。

## 8. 自审(spec self-review)

- **占位符扫描:** 无 TBD;每步引用真实符号(`build_panel`/`CROWD_CONTRA`/`_legs`/`QUANTILE`/`run_multi_period_dry_run`)。
- **一致性:** cash_flow=funding(含)与 carry 一致、与 trend(None)区分;追加不清库与 trend 一致。
- **诚实:** 不预设结论;明写"证伪已过≠执行现实必过";driver 在 gate 外不破 services 无网络不变量。
- **范围:** 单文件驱动器 + 文档,单一计划可覆盖;paper-track / 流水线改动显式划出。
