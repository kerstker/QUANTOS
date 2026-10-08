# BTCUSDC 因果订单块研究结果 — 2026-09-06

结论：按用户报告的 maker 0、taker 0.036%（单边 3.6bp），本批重建的订单块规则未通过筛选。24 项情景计算完成，账本和退出时序独立复核通过；没有候选晋级，没有部署或下单。

原包缺少结构引擎源码，用户确认无法提供。本研究按预先冻结的明确规则重新实现 swing/BOS/FVG、首次回踩与 EMA100 过滤，不能称为原策略的精确复现。原包针对 BTCUSDT，本批针对用户指定的 BTCUSDC 永续，旧 USDT 实现及记录保留。规格见 `docs/engineering/plans/2026-09-06-causal-orderblock-study.md`。

## 研究口径

- 评估区间：2025-01-01 00:05 至 2026-08-01 00:05 UTC，初始权益 1500 USDC。之前的数据只作预热。这段历史已用于研究，不称未见 OOS。
- 完整 4h K 线生成信号，决策结束后延迟 5 分钟，以分钟开盘价加不利滑点代理执行。所有成交按 taker 3.6bp；没有 maker 排队成交证据，未使用免费成交假设。账户费率来源为用户报告，未读取私人账户。
- 保证金目标为权益 50%，2x/5x 对应约 1x/2.5x 权益名义敞口。初始保证金率 1/杠杆、维持保证金率 10% 是冻结情景假设，不代表币安历史阶梯规则。
- 价格止损为实际入场价的 0.5%；仅在一分钟完成后检查其高低价，触发后下一分钟开盘代理退出。最长持仓 80 小时。计入实际历史资金费、手续费、滑点与数量约束。
- 2 个杠杆版本 × 3 档单边滑点（2/5/10bp）× 带/不带累计亏损停机，共 12 项策略情景；现金与 25% 被动持有另 12 项。全部 24 项事先登记，不把两个杠杆版本当独立 Alpha。

## 全部策略情景

净损益单位 USDC；每笔成交另收单边 3.6bp 手续费。

| 版本 | 单边滑点 | 带 150 累计亏损停机 | 不截断研究路径 |
| --- | ---: | ---: | ---: |
| 2x | 2bp | -125.48 | -125.48 |
| 2x | 5bp | -140.25 | -140.25 |
| 2x | 10bp | -152.95 | -167.16 |
| 5x | 2bp | -148.87 | -312.80 |
| 5x | 5bp | -152.81 | -347.09 |
| 5x | 10bp | -155.50 | -401.57 |

中心情景固定为 5bp 滑点、带停机：

| 指标 | 2x | 5x |
| --- | ---: | ---: |
| 最终权益 | 1359.75 | 1347.19 |
| 净收益率 | -9.35% | -10.19% |
| 分钟标记权益最大回撤 | 14.87% | 22.69% |
| 2025 净损益 | -28.35 | -87.54 |
| 2026 评估段净损益 | -111.90 | -65.27 |
| 完整交易数 / 盈利交易数 | 29 / 1 | 20 / 1 |
| 净交易利润因子 | 0.429 | 0.627 |
| 手续费 | 29.81 | 53.23 |
| 资金费现金流 | +0.83 | +2.17 |
| HAC Alpha t 值 | -1.135 | -0.430 |

2x 的 29 笔交易中 28 笔保护止损、1 笔超时退出；5x 为 18 笔保护止损、1 笔超时、1 笔累计亏损退出。30 次评估期信号机会并不等于 30 笔成交：持仓期间信号不加仓。两版本中心情景期末均无持仓。中心现金基准为 0，25% 被动持有为 -112.53 USDC；其风险敞口与策略不同，不能把收益差直接称为 Alpha。

150 USDC 是相对初始本金的累计亏损触发阈值，最大回撤相对历史权益峰值，两者不同。触发后下一报价退出允许越过阈值，也可能反弹后最终亏损略小于 150；不会据最终权益自动重启。5x 中心情景触发停机，关闭停机后的 -347.09 显示未截断风险。

本轮失败不仅来自费用：中心 2x 仅一笔盈利，净损失也明显超过总手续费。没有证据支持继续提高杠杆或调参挽救这条曲线。结论只适用于本次冻结重建规则，不能推断缺失源码的原结构引擎一定产生相同信号。

## 系统接入与验证

沿用唯一 `services/research/perpetual_alpha.py` Decimal 永续回放及风险账本，增加显式费率、决策间隔、保证金参数、因果保护止损与持仓超时。新信号核为 `services/research/orderblock.py`，固定研究驱动为 `tools/causal_block_research.py`。通过既有 JobQueue / RunRegistry / TrialLedger 和技术结果页接入 `orderblock_research`，没有新增平行账本或放宽权威准入。

执行命令及结果（Python 使用 DGX 锁定虚拟环境）：

- `python -m pytest tests/research/test_orderblock_execution.py tests/execution tests/risk -q`：480 passed。
- `python -m pytest tests/research/test_orderblock_runner.py tests/research/test_perpetual_alpha_runner.py tests/governance/test_service_import_graph.py tests/governance/test_research_runner_boundary.py tests/research/test_console_api_contract.py -q`：71 passed、1 skipped。
- 新信号/执行与原回放核心测试：早期一轮 10 passed；新增退出回归随后纳入上述 480 项。
- 前端 `npm run lint`、`npm run typecheck`、`npm run build`：通过；前端测试 37 文件、264 passed。
- `PYTHONPATH=. OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python /home/kerstker/quantos-dev-output/orderblock-research-20260906/run.py 1`：24 项完成、0 计算失败、0 通过筛选。
- 同目录 `independent_reconcile.py 1`：Fraction 独立复核全部 24 项、13,872 个每日净值/持仓检查，通过。
- 同目录 `verify_stops.py`：318 笔完整交易逐笔验证数量、费用、资金费、首个已完成分钟触发后的退出时间、超时和停机后不入场，通过。
- 持久化真实 run 经 `_technical_replay_detail` 来源校验后可读，24 trials、authoritative=false；结果存 `console-verification.json`。
- 显式运行仓库 pre-commit 治理扫描：通过。原始失败测试日志保留，未删除失败测试。

本轮为增量验证，没有重新宣称全系统验收通过。上一轮完整门禁仍 FAILED，供应链/发布证据缺口未在本任务解决。

## 可追溯记录与剩余边界

- 代码提交：`a4d5f4f9a0c91a20ce621b7806097e3e5fdad10e`。
- Run：`run_a955088bcee8af1a458c0af7ff56cb3f`。
- Result hash：`3c37522c6b42e4d183cd5908dbd3cae1326b997d65a37d35eceb8b0494ecb840`。
- 数据、规格、信号和代码摘要：`/home/kerstker/quantos-dev-output/orderblock-research-20260906/results/1/result.json`；同目录保留逐项结果，父级保留 trials.db。运行库、复核脚本、明细和测试日志在该输出根目录。

正式历史交易规则、历史保证金阶梯与真实挂单成交证据仍缺；分钟代理不能证明盘口成交。结果为 `TECHNICAL_VALIDATION_ONLY`。独立签名权威、Paper 准入及真实 USDC 执行恢复等前述系统缺口仍在，不启动 Paper 或真实资金验证。可重复计算与独立对账通过，不等于全自动 Alpha 到实盘系统已经完成。
