# BTCUSDC 永续交易细节与 4 bp 费率重算

2026-09-06。用户确认：主要目标是合约，按 taker 4 bp 计算。

## 费率与合约

查到的[币安官方 USDC 永续优惠公告](https://www.binance.com/en/support/announcement/detail/03180947641b414ebcb9a6c407fe80e2)
列普通用户 maker 0、taker 4 bp，符合条件的 BNB 抵扣为 taker 3.6 bp，
活动延续至另行通知。用户已选择 4 bp。本任务没有查询账户专属费率。

BTCUSDC 公共合约快照为线性永续，USDC 报价/保证金，TRADING。
数量步长/最低数量 0.001 BTC，价格 tick 0.1 USDC，最低名义额 50 USDC；
市价数量上限 50 BTC。不能使用现货的数量步长与最低交易额。
当前资金费间隔 8 小时、费率上限/下限 ±0.003；实际历史结算仍需逐事件核对，
不能固定假设所有合约、所有时期都为八小时。

maker 0 不等于没有成交成本：限价单若直接吃单仍属 taker，挂单也可能不成交、
部分成交或在不利行情才成交。当前日线执行代理无排队证据，使用 taker 4 bp，
没有把成交全部改成免费 maker。

费用按成交名义额计算。300 USDC 的 taker 成交每边 0.12 USDC，等名义额往返
0.24 USDC，另计滑点及实际资金费。保证金金额不是手续费计费基数。

## 已完成的有界重算

`tools/btc_usdc_research.py` 仍为同一个永续研究实现：费率变成显式参数，默认
4 bp；原 5 bp 可显式重现。结果新增 taker 模型、账户未核实及已见历史标记。
旧输入、输出、USDT 路径和现货结果全部保留。

这次只改变手续费，原三个策略、日期窗口、名义规模和滑点不变：三策略、两基准、
三窗口、三滑点共 45 次技术计算，全部登记于追加式 `trials.db`。
以下均为单边 taker 4 bp + 滑点 2 bp，损益单位 USDC：

| 原永续候选 | 2025 损益 | 2026年1–8月损益 | 后段成交数 | 原筛选结论 |
|---|---:|---:|---:|---|
| 20/60 趋势 | +18.60 | +6.82 | 9 | 不通过：成交数不足、Alpha统计弱 |
| 20/10 突破 | -92.07 | +122.60 | 12 | 不通过：2025亏损、Alpha统计不足 |
| 资金费反转 | -1.35 | 0.00 | 0 | 不通过 |

后段 Alpha HAC t：趋势约 0.057、突破约 1.300，仍未达到原 2.4 条件。
旧 5 bp 对应后段损益分别 +6.40、+122.28、0；降低 1 bp 手续费没有改变结论。
这些旧策略**不是**前一批现货 A/B/C；没有宣称完成 A/B/C 的永续回测。

## 验证与限制

- 修改前现有工具测试通过；新增显式费用/非法数值测试先复现失败，再修正实现。
- `python -m pytest tests/tools/test_btc_usdc_research.py tests/execution/test_perpetual_ledger_v2.py -q`：70 passed。
- 独立 Fraction 从原始 mark、资金费和成交逐笔重算：45 次、13,245 个逐日
  权益/持仓检查点精确一致，逐笔核验手续费 = 成交名义额 × 0.0004。
- `git diff --check` 通过；提交前手动执行仓库实际 pre-commit 检查。
- 结果仍为 `TECHNICAL_VALIDATION_ONLY`，没有权威、晋级、部署或下单。
  日线成交代理、未证明的历史规则/保证金、尚未接通真实执行的累计亏损预算仍是缺口。

后续主线已改回 BTCUSDC 永续，USDT 独立保留。A/B/C 合约版需要重新准备本合约
数据、结算与风险模型；不能把“现货测试未通过”写成“永续 Alpha 已被否定”。
双向策略是新规格，不能把原 long/flat 负信号自动改成做空后继续沿用旧试验身份。

完整原始快照、manifest、追加式 trials、`screen-4bps.json`、
`fraction-verification.json`、测试与 hook 日志均在：
`/home/kerstker/quantos-dev-output/usdc-perpetual-fees-20260906/`。
