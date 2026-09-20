# 上传订单块策略的 QUANTOS 回测报告

**报告日期：** 2026-09-11  
**结果状态：** `TECHNICAL_VALIDATION_ONLY`  
**研究结论：** 两个候选均未通过筛选，不进入 Paper、Testnet 或真实资金验证。

## 结论

本次用修复后的 Decimal 永续账本和因果信号重建，对上传包中的两个版本做了完整的 24 项情景计算。两个版本实际上是同一套订单块规则的 2x 和 5x 杠杆变体，并不是两个独立 Alpha。

中心情景采用单边 5bp 不利滑点、用户报告的 taker 3.6bp、累计亏损 150 USDC 停止新增风险：

| 版本 | 期末权益（USDC） | 净损益（USDC） | 收益率 | 最大回撤 | 完整交易 / 盈利交易 | Sharpe | HAC Alpha t |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2x | 1,359.75 | -140.25 | -9.35% | 14.87% | 29 / 1 | -1.083 | -1.135 |
| 5x | 1,347.19 | -152.81 | -10.19% | 22.69% | 20 / 1 | -0.436 | -0.430 |

2x 的 29 笔完整交易中有 28 笔保护止损、1 笔超时退出。5x 有 18 笔保护止损、1 笔超时退出和 1 笔累计亏损停机退出。5x 中心情景触发了 150 USDC 停机阈值；停机后的未截断研究路径仍显示较大的尾部损失。

因此，这次结果没有显示可接受的成本后 Alpha。`worth_further_review=false`、`authority_admitted=false`，且所有情景都保持 `live_eligible=false`。

## 上传包与可复现范围

上传文件为 `策略验证包-订单块.tar.gz`，SHA-256 为：

`f9c535bfea873131f432ca8f3ff74df978030fea13104276ba271eb4b7a9d50a`

包内的 `orderblock_strategy_2x.json` 与 `orderblock_strategy_5x.json` 只有杠杆不同，核心描述均为订单块、FVG、BOS/结构过滤、EMA100、0.5% 止损、80 小时超时、单仓位和 50% 保证金目标。包的说明文字使用 4 小时，DSL 的 timeframe 字段却为 1 小时；包中没有生成订单块所需的 swing、BOS、CHoCH、FVG 精确定义和确认时间源码。因此本报告不能把当前结果称作上传策略的精确复现。

包内历史结果声称 2x 约 `+306.46%`、5x 约 `+2573.56%`（2024 起的两年记录）。这些数字是附件自带的声明，不是本次独立验证结果。此前审计还发现附件记录数量与声明数量不一致、重复 backtest 编号、缺少清算模拟，以及部分数量不符合当前交易规则；本报告不复用这些盈利声明。

## 本次运行口径

- **市场：** Binance USDⓈ-M `BTCUSDC` 永续。上传包写的是 `BTCUSDT`，所以本报告的市场与附件不同。
- **数据：** 官方 Binance USDⓈ-M BTCUSDC 1 分钟 trade Kline、mark-price Kline 和历史资金费记录。数据用于信号前的 4 小时预热，评估段为 2025-01-01 00:05 至 2026-08-01 00:05 UTC；这段历史已经用于研究，不能称作未见 OOS。
- **数据质量：** 1,354,287 个对齐分钟；2 个分钟缺少 trade 记录，2,191 个分钟缺少 mark 记录，4 个信号日不完整。所有输入归档保留来源与官方校验摘要；历史修订状态仍为 UNKNOWN。
- **信号：** 仅使用完整 4 小时 K 线；严格 5-bar swing 在右侧两根 K 线完成后确认；确认后才允许 BOS/FVG 和订单块；订单块只接受后续第一次重叠；EMA100、方向过滤和持仓状态均按因果时序计算。共生成 123 个订单块，评估段有 30 次信号机会。
- **执行：** 信号确认后下一个 4 小时边界加 5 分钟，以该分钟开盘价作为 taker 成交代理，叠加不利滑点并按 tick 舍入。没有假设挂单一定成交，因此 maker 0 没有被计入收益。
- **成本：** maker `0`、taker `3.6bp`；费率来源是用户报告，未读取账户。每个候选分别跑 2bp、5bp、10bp 三档不利滑点。
- **账本：** 初始权益 1,500 USDC；单仓、不加仓；50% 权益保证金目标，2x 约 1.0x 权益名义敞口，5x 约 2.5x；0.5% 保护止损、80 小时最长持仓、实际资金费、手续费、数量和最小名义约束全部计入。150 USDC 累计损失触发停止新增风险并在下一可执行报价退出，允许越过阈值。
- **清算边界：** 使用当前规则情景中的初始/维持保证金假设；它不是历史 Binance 保证金阶梯的证明。原始包没有足以重建其清算规则的资料。

本次结果来自唯一的 `services/research/perpetual_alpha.py` Decimal 永续账本和 `services/research/orderblock.py` 因果重建。订单块重建是为了在缺少原始结构引擎时给出可审计的情景初筛，不代表原引擎的隐藏实现。

## 全部策略情景

下表净损益单位为 USDC。`停机=是` 是启用 150 USDC 累计亏损停止；`停机=否` 是不截断的研究路径，用于观察尾部风险。每笔成交均按单边 taker 3.6bp 计费。

| 版本 | 单边滑点 | 停机=是 | 停机=否 | 带停机最大回撤 | 带停机完整交易数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2x | 2bp | -125.48 | -125.48 | 13.85% | 29 |
| 2x | 5bp | -140.25 | -140.25 | 14.87% | 29 |
| 2x | 10bp | -152.95 | -167.16 | 15.44% | 28 |
| 5x | 2bp | -148.87 | -312.80 | 22.32% | 20 |
| 5x | 5bp | -152.81 | -347.09 | 22.69% | 20 |
| 5x | 10bp | -155.50 | -401.57 | 22.37% | 18 |

作为同一数据和成本口径的参考，现金基准净损益为 0；25% 被动持有在 5bp、带停机情景为 -112.53 USDC。被动持有有更高市场敞口，不能把两者的差额直接解释为 Alpha。

中心情景的费用和资金费如下：

| 版本 | 成交事件数 | 手续费 | 资金费现金流 | 2025 净损益 | 2026 评估段净损益 | 平均权益敞口 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2x | 58 | 29.81 | +0.83 | -28.35 | -111.90 | 2.24% |
| 5x | 40 | 53.23 | +2.17 | -87.54 | -65.27 | 4.55% |

资金费为正但金额很小，不能抵消价格损失和交易成本。5x 不启用停机时，净损益降至 -347.09 USDC；这说明停机阈值限制了损失，但没有形成策略优势。

## 筛选结论

两种杠杆版本的筛选结果完全一致：

| 检查项 | 2x | 5x |
| --- | --- | --- |
| 两个年度均为正 | 未通过 | 未通过 |
| 所有成本和停机情景均为正 | 未通过 | 未通过 |
| 至少 20 笔完整交易 | 通过 | 通过 |
| 分钟标记权益最大回撤不超过 10% | 未通过 | 未通过 |
| 市场调整后的 HAC Alpha 为正 | 未通过 | 未通过 |
| 去掉最大 5 个日收益后仍保持结论 | 未通过 | 未通过 |
| 值得进一步复核 | 否 | 否 |
| 允许作为权威记录 | 否 | 否 |

交易数量满足最低观察条件，但这不是盈利或准入条件。由于两个版本都无法在成本和风险约束下通过，其当前结论为淘汰/保留失败证据，而不是继续加杠杆或调参。

## 独立一致性核验

独立核验脚本没有调用回测结果中的净值字段，而是使用 `Fraction` 重新计算现金、数量乘 mark、手续费、资金费、分钟内止损触发和退出时序：

```text
status: PASS
cases: 24
equity_checks: 13,872
completed_trade_checks: 318
exit_counts:
  PROTECTIVE_STOP_NEXT_QUOTE: 303
  HOLDING_TIMEOUT: 12
  LOSS_BUDGET_NEXT_QUOTE: 3
scope: artifact_consistency_only
```

结果摘要 SHA-256 为：

`912c5206f70462ca63fe93e8eb6c2df208fcb9a96237c6add6b19169bff45ab0`

本核验确认产物内部的账本数学和退出顺序一致，不确认市场成交可得性、历史规则真实性或策略经济盈利能力。

## 可复现产物与运行身份

- 最新摘要：`/home/kerstker/quantos-dev-output/strategy-backtest-20260911/results/1/result.json`
- 信号产物：`/home/kerstker/quantos-dev-output/strategy-backtest-20260911/results/1/signals.json`
- 输入身份：`/home/kerstker/quantos-dev-output/strategy-backtest-20260911/results/1/input-identity.json`
- 运行日志：`/home/kerstker/quantos-dev-output/strategy-backtest-20260911/run-current-engine.log`
- 独立核验：`/home/kerstker/quantos-dev-output/strategy-backtest-20260911/independent-verification.json`
- 运行提交：`1950808c6cc277c37305849b60ab33d3b6d35c23`
- Python 源码 SHA-256：`666f254727b5fbaf283207e7ec54f75d67b6640a4b6f6b4b8638443fdbbab063`
- 计划 SHA-256：`162d7715a59954db57ff8b15041e28a038e547a1e9c87bf9202ac35fbf73abf6`
- 数据 SHA-256：`105719635a185366c037b42f1edb073918d9478e0131435a6c15ea38977018a6`
- 信号 SHA-256：`b9d521e51cd6dddf9b6caed9259be436456b722ee0b479e7b00581d33aed868b`
- 运行状态：`TECHNICAL_VALIDATION_ONLY`，`trial_ledger_verified=true`，`authoritative=false`，`live_eligible=false`

## 验证命令

本次运行使用 DGX 锁定的 Python 3.12.3/aarch64 环境。研究计算直接调用 `tools.causal_block_research.run_study`，输出写入独立目录，不覆盖用户现有状态。

已执行：

```bash
/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905/venv/bin/python \
  -m pytest tests/research/test_orderblock_execution.py \
  tests/research/test_orderblock_signal.py \
  tests/execution/test_perpetual_backtest_regressions.py -q
# 18 passed in 1.41s

PYTHONPATH=. OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905/venv/bin/python \
/home/kerstker/quantos-dev-output/strategy-backtest-20260911/verify_current.py
# exit 0; independent verification PASS
```

研究日志显示 24 项情景均完成、没有计算失败。上述是本次策略研究的定向验证，不等同于 `scripts/validate.py` 的全仓库验收；本次没有重新运行前端构建或完整门禁，也没有改变用户当前工作区的未提交修改。

## 尚未消除的风险项

1. 上传包缺少原始结构引擎，因此无法证明当前因果重建与原策略完全相同。
2. 上传包是 BTCUSDT，而本次可复现数据是 BTCUSDC；不能把本结果改名为 BTCUSDT 结果。
3. 评估历史已被研究使用，不能作为冻结后的新 OOS；正式准入仍缺少独立控制的签名 OOS 证据、DSR/PBO 和前向观察。
4. 4 小时信号后的分钟开盘是执行代理，没有盘口排队、部分成交、拒单、断线重连或真实 maker 成交证据。
5. 当前规则、保证金和数量约束是明确的研究情景；缺少附件所声称历史期间的完整交易所规则和清算阶梯证据。
6. 本报告确认回测产物一致，不代表底层系统已获得经济权威，也不解除项目的 Paper/live 人工门。

在补齐原始结构算法、同品种 BTCUSDT 永续数据和独立冻结的 OOS 之前，这两个上传策略都不能被当作可交易 Alpha。
