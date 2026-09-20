# 研究优先评估：接入已保存的RF2结果

TOP22的7项核心、9项缩小范围、6项延期映射见[优先级决定](../engineering/specs/2026-09-20-dgx-research-first.md)。本入口只读展示，不修改原评分、实验或交易权限；没有总分或完成百分比。

使用已验证Python3.12（本机`/home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python`）：

```bash
PYTHONDONTWRITEBYTECODE=1 /home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python -m tools.research_first_assessment --rf2-run /home/kerstker/quantos-dev-output/rf2-log-slope-20260920-attempt1
```

不传`--rf2-run`时保留原范围视图，六维UNKNOWN。传入时，读取固定16个文件：summary、identity、registration、12个案例与只读trial ledger。核对方案原文摘要、试验身份及顺序、完整性、案例与汇总一致性、成本归因、逐笔4bp费用和注册滑点，不重新运行原始行情。

输出`run_evidence`包括完整12个案例（不只赢家）、筛选结果、逐文件绝对路径和摘要、试验事件与耗时。失败计算保留为FAILED；原候选亏损也不能因对照盈利被改写为通过。已失效、缺失、篡改或不一致的输入以非零退出拒绝，不退回旧成功报告。

## 六维含义

| 维度 | 此类归档可提供的证据 | 仍然缺少 |
| --- | --- | --- |
| 数据可信 | PARTIAL：保存了数据身份 | 原始数据可知性、缺口和修订独立复核 |
| 回测正确 | PARTIAL：已完成案例的保存账目和成本可核对 | 从原始输入独立重放、场所成本校准 |
| 实验有效 | PARTIAL：方案内容绑定、全部试验先登记再计算 | 独立登记时钟、未见样本外结果 |
| 盈利证据 | INSUFFICIENT：展示已见历史净收益 | 独立盈利证据，不能用历史赢家代替 |
| 前向与测试网 | UNKNOWN：本输入没有此类证据 | 自然前向和逐单测试网账户核对 |
| 研究效率 | PARTIAL：各案例报告的耗时 | 加载耗时、冷/热基线、内存、缓存及性能预算 |

总体保持`RESEARCH_EVIDENCE_INCOMPLETE`，原22项不加PASS。PARTIAL是证据范围，不是通过；本地摘要也不是外部签名或经济权威。本批尚未接前端。

## 开发与实测

- Claude Code独立委派限时300秒，exit124，留下读取器与测试，未交回完成报告；Codex接线并独立复核。
- 原测试夹具手工拼造了账目；Codex改为真实V2引擎生成合成输入，并新增反例：同时删手续费、改平汇总账，不能继续宣称4bp场景；数字风险标志与内层权限矛盾必须拒绝。
- 读取器和范围视图联合56项通过（29.63秒）；另加直接脚本CLI验收，核对运行前后全部输入文件内容和目录不变。不是全项目验收。
- 实际只读核对原RF2归档成功：16文件、12案例、36账本事件；简单趋势中心净收益`82.797499834148047672` USDC，回归候选筛选false。案例耗时之和32.263178秒，不含加载、身份检查或冷启动。
- 未重跑或改写原实验，未启动前向观察或测试网，无新增真实盈利结果。前向离线评估入口另见[RF3操作说明](rf3-daily-forward.md)。
