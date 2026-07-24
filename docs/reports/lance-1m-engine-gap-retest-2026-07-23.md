# Lance 1m Engine Gap 重测报告

**日期：** 2026-07-23  
**对象：** `I:/qqFILE/lance-1m-engine-gap-analysis.md` 指出的 Aetos/ Python 实现差异  
**状态：** 研究重测，仍处于 `L2_DRY_RUN_ONLY`  
**结论：** 修正后的 OHLC 近似没有通过成本稳健性准入

## 1. 重测目的

此前的 Python 回测结论只否定了“Python 即时收盘成交实现”。Gap analysis 指出 Aetos 可能使用了不同的操作化规则：

- 首根收盘价初始化 EMA，而非前 20 根 SMA 初始化；
- 只有至少 50 根 K 线才允许信号；
- 止损参考前一根 K 线，并且与入场价止损取更保守者；
- Trusted、Operational Health、Tick 止损和 Pending→Fill 延迟会改变实际信号与成交。

本次把前三项做成隔离的“Go-like”重放，重新测试该策略是否可能因引擎差异而恢复正 Alpha。

## 2. 无法重放的部分

仓库中没有 Aetos Go `engine.go`、原始 trade/quote/funding 事件流或健康门历史，因此以下差异没有被伪造：

- Trusted 数据门；
- stream、DB、lease、recency 健康门；
- Tick 级止损先后与实际成交价；
- Pending→Eligible→Quote fill 的 1–3 秒延迟；
- 8 轨并行的资本分配含义。

Go-like 情景因此是假设“所有历史 K 线都 trusted 且健康、无延迟、无滑点”的乐观上界，不是 Aetos 原生回测。

## 3. 重放情景

| 情景 | EMA | 预热 | 止损 | 出场 | 撮合 |
|---|---|---:|---|---|---|
| Python professional | SMA20 seed | 20 根 | 入场 K 线 | 0.15% | 保守 OHLC |
| Python previous-stop | SMA20 seed | 20 根 | 前一根 K 线 | 0.15% | 保守 OHLC |
| Go-like best-case | 首收盘 seed | 50 根 | 前一根与入场价取更保守者 | 0.15% | 保守 OHLC |
| Go-like literal | 首收盘 seed | 50 根 | 同上 | 0.15% | 文档字面 OHLC 顺序 |

所有情景：固定基础仓位 40%、入场偏离 0.3%、最大持仓 20 根、初始资金 100,000 USD、滑点 0、无资金费、无冲击模型。

## 4. 结果

### 4.1 文档精确窗口：2025-12-17 至 2026-06-29

| 情景 | 1bp 收益 | 1bp Sharpe | Profit Factor | 最大回撤 | 交易数 |
|---|---:|---:|---:|---:|---:|
| Python professional | -8.32% | -2.14 | 0.931 | 12.55% | 3,038 |
| Python previous-stop | -10.53% | -2.93 | 0.914 | 14.35% | 3,268 |
| Go-like best-case | **-10.53%** | **-2.93** | **0.914** | **14.35%** | **3,268** |
| Go-like literal | -6.01% | -1.61 | 0.951 | 10.41% | 3,268 |

即使选用对结果更有利的字面 OHLC 顺序，精确文档窗口仍然为负。

### 4.2 全部可用区间：2025-07-01 至 2026-07-09

| 情景 | 1bp 收益 / Sharpe | 2bp 收益 / Sharpe | 4bp 收益 / Sharpe |
|---|---:|---:|---:|
| Python professional | -9.39% / -1.40 | -38.14% / -6.90 | -71.17% / -17.46 |
| Go-like best-case | **+1.30% / +1.76** | **-4.33% / -5.81** | **-14.67% / -20.17** |
| Go-like literal | **+1.65% / +2.22** | **-4.00% / -5.34** | **-14.38% / -19.70** |

Go-like 修正只把长区间 1bp 结果从负值推到轻微正值，不能通过 2bp 成本检验；这不是稳定的成本后 Alpha。

### 4.3 时间外区间

| 区间 | Go-like best-case，1bp | Go-like literal，1bp | Go-like literal，2bp |
|---|---:|---:|---:|
| 窗口前 2025-07-01 至 2025-12-01 | -0.20% / Sharpe -0.06 | +3.01% / Sharpe 1.33 | -8.12% / Sharpe -3.66 |
| 窗口后 2026-06-01 至 2026-07-09 | +1.30% / Sharpe 1.76 | +1.65% / Sharpe 2.22 | -4.00% / Sharpe -5.34 |

1bp 下短窗口的正值与 2bp 下的负值同时出现，说明收益主要处于手续费断点附近。

### 4.4 5,000 根 K 线滚动压力测试

全样本共 107 个非重叠窗口：

| 情景 | 费率 | 正收益窗口 | 平均 Sharpe | 中位 Sharpe | 平均窗口净 PnL |
|---|---:|---:|---:|---:|---:|
| Go-like best-case | 1bp | 46.7% | -0.42 | -0.73 | -$118.69 |
| Go-like best-case | 2bp | 29.0% | -6.41 | -6.76 | -$492.70 |
| Go-like literal | 1bp | 48.6% | +0.40 | -0.38 | -$44.36 |
| Go-like literal | 2bp | 30.8% | -5.59 | -6.50 | -$419.07 |

即使采用更宽松的字面 OHLC 顺序，1bp 滚动窗口的中位 Sharpe 和平均窗口净 PnL 仍为负。

## 5. 结论

1. Gap analysis 指出的 EMA、预热和止损锚点差异确实会显著改变交易路径，原 Python 实现与 Aetos 不能直接视为同一实现。
2. 这些差异没有恢复文档精确窗口的正收益；精确窗口在所有重放情景下 1bp 仍为负。
3. 全样本 Go-like 近似在 1bp 下只有小幅正收益，但 2bp 已失败，且滚动窗口中位数仍为负。
4. Trusted/Operational 门、Tick 止损和订单延迟尚未重放；它们不能在当前证据中被假设为“会改善结果”。健康门可能减少交易，也可能错过有利成交，方向必须由真实事件流证明。
5. 因此本次重测不会把 Lance 1m 加入纸面三 Alpha，也不会解除 `PAPER_BLOCKED`。

## 6. 下一步准入条件

要继续验证，必须取得 Aetos 原生材料并完成同源回放：

1. 提供 Aetos `engine.go`、配置快照和 commit hash；
2. 提供同一时间范围的 raw trade、quote、funding、kline、Trusted/Operational 状态事件；
3. 使用事件时间重建 Pending→Fill 和 Tick 止损，不从 OHLC 事后猜测；
4. 对同一数据同时输出 Go 原生、Python 参考和逐笔差异账本；
5. 在 maker/taker、资金费、滑点和延迟分布下，至少通过完全未触碰的 OOS 与前向纸面门。

在这些证据到位前，当前最强的诚实结论是：**引擎差异已被确认，但没有证据证明该策略因此变成可部署 Alpha。**

## 7. 证据入口

- Gap analysis：`I:/qqFILE/lance-1m-engine-gap-analysis.md`
- 隔离重放驱动器：`tools/lance_1m_engine_gap_research.py`
- 机器结果：`data/cache/lance_1m_engine_gap_validation.json`
- 原 Python 引擎：`services/research/backtest_engine/single_asset_rule_backtest.py`
- 原策略报告：`docs/reports/lance-1m-alpha-validation-2026-07-23.md`
