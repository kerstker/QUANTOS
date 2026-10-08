# DGX 第三批：冻结公共行情、技术研究入口和独立差分

日期：2026-09-05。范围：路线图 D1/I1/V1 的有限技术运行，以及 O2/O4 的
诊断与隔离增量。用户明确决定异机备份/恢复暂不实施；O1 外部验收延期，
不能算作 M2 通过。本文不授予部署、经济准入或实盘权限。

## 行为与合同

- `tools/perpetual_public_capture.py` 仅通过固定 Binance 公共 GET 端点采集
  BTCUSDT，拒绝重定向；每次原始响应字节、SHA256、解析内容、请求/接收时钟
  一同冻结。版本 `BINANCE_PUBLIC_REPLAY_CAPTURE_V2`，输出只创建不覆盖。
- `services/execution/perpetual_public_replay.py` 离线验证字节绑定、重复 JSON
  键、单调时钟、缺口、规则前后观测、30 个完整 UTC 日 quote-volume ADV。
  mark、可执行 top-of-book 和资金费日程分别建模。行情可用时刻决定决策；
  深度归档曲线不充当可执行盘口。单日、3–60 次观测、无样本内资金费结算。
- 公开价格/数量过滤器绑定本次观测区间；场所历史保证金档位与账户费率不能从
  此公共样本证明。IM=100%、MM=50%、清算费=1%、成交费=0.05%、单单最大金额
  1000 USDT 是明确的技术模型配置，**不是 Binance 完整规则声明**。前后规则
  一致也不能证明中途没有变更，因此该适配器仍不足以完成 D1 的完整场所验收。
- `services/execution/perpetual_results.py` 输出 UTC 定频净权益和收益：同一时刻
  取最后事件、无事件期间延用余额、不满一个频率的尾段明确排除。非正权益、
  少于 30 日或 30 期不报告 Sharpe；年化采用 365 日。外部出入金尚不支持。
- `perpetual_replay` 接到现有研究 runner 白名单、队列和 registry。只接受
  `strategy_id=0..2`、`frequency_ms=1..3600000`，不接受任意代码或路径。
  策略为不交易/最小量持有多仓/最小量持有空仓，供执行验证。失败为 error，
  invocation_id 重复不会重复执行；软取消丢弃结果，不承诺终止计算进程。
- 完整 Decimal 结果按结果 hash 只创建保存；绑定代码、依赖锁、数据、规则、
  成本、风险、策略、订单事件和对账。新增只读 `/api/v1/console/technical-replay`
  先验证 registry provenance；控制台只显示对应运行的技术配置、定频权益与
  订单审计。V1 投影继续隔离，不复活旧利润/晋级记录。

## 实际数据与独立对照

任务输出根：`/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905`。
公共样本在 `d1-public/capture-v2.json`，10 次观测；capture_id 为
`36644e05ede89aaeb41ba7377fb01798af78dc08c7d948cab5665c5d73be8f38`。
这是开发者采集的未签名技术回执，不是独立来源权威。

研究入口固定读取工作目录下 `data/cache/perpetual_replay/capture-v2.json`，
精确结果保存至 `data/cache/perpetual_replay/results/<result_hash>.json`。
这些文件仅在开发目录生成，不写 `/srv/quantos/state`。运行命令：

```bash
/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905/venv/bin/python -m tools.perpetual_replay_research
```

`tools/perpetual_reference_worker.py` 在独立进程运行 Fraction 手算账本或 VectorBT，
不导入 QUANTOS 会计内核。7 个固定案例覆盖空仓、多空往返、盈亏、费用、反手与
部分成交；逐时点对照权益、数量、累计费用，并核对逐笔实际成交量。
Fraction 要求精确相等；VectorBT 共同模型绝对容差为 1e-9。
资金费、保证金档位/强平、队列、限价过期、容量和迟到行情不属于 VectorBT
共同模型，明确列为不适用；其 QUANTOS 内部回归不冒充跨引擎证明。

独立环境为 `vectorbt-reference/bin/python`，VectorBT 1.1.0、Plotly 6.3.1；
Plotly 7 与该 VectorBT 的 scattermapbox 导入不兼容，兼容固定只影响隔离环境。
安装报告和 ARM64 wheel hash 锁在任务输出根与 `v1-reference/`，主依赖锁未改。
两个参考报告各 7 个案例通过；最终代码绑定的重跑报告在该目录另行保存，
不得把中间工作树的结果 hash 当作最终发布证据。

## 数据质量与资源隔离

对 2026-09-04 UTC 的 14 标的公开 bookDepth ZIP 逐个验官方 CHECKSUM，均触发
隐含买卖价格倒挂阈值。BTC 原始 ZIP SHA256：
`4663601e7d1230c20a8dc6d05c913e227b93a49811d12b258cb00b980e149a40`。
00:00:04 的 ±0.2% 原始记录为 bid depth=4557.095、notional=366253589.8575，
ask depth=12.449、notional=974562.5692。直接相除已倒挂，非浮点微小舍入问题。
这证明输入与本项目的同步累计盘口假设不相容；上游生成机制尚未获独立证明，
不能擅自交换两侧、修改数值或放宽 0.2% 拒帧比例来接纳。

解析异常新增原因码、总/拒绝快照数、逐小时计数及有限样例时间。质量诊断继续
处理其他标的，汇总明确 FAIL；校验和错误仍抛出。生产解析/隔离门禁不变，
没有读取候选信号、收益或确认实验结果。详细诊断在 `ops-readonly/`。

ARM64 冷构建的独立 BuildKit 容器固定 4 CPU、16 GiB 内存，memory-swap 同额，
两个冷构建串行；不停止其他项目，不修改常驻服务配额。这是构建边界，不能
替代 O4 在冻结采集 SLO 下的压力验收。

## 验证与剩余边界

先失败后修复日志：`/tmp/quantos-public-replay-before.log`、
`/tmp/quantos-replay-runner-before.log`、`/tmp/quantos-depth-diagnostic-before.log`、
`/tmp/quantos-build-limits-before.log`。深度解析/诊断专项 15 passed；构建隔离
测试 48 passed。整合专项 621 passed / 1 skipped（日志为
`/tmp/quantos-replay-final-xj6pwcjo/tests.log`）；前端 257 tests 与 typecheck
通过。canonical 发布日志以后续实际验收报告为准。

本增量保持 TECHNICAL_VALIDATION_ONLY。完整 M1 尚需真实场所规则范围证明和
适用黄金案例验收；M2 还需当前代码 ARM64 发布、恢复/回滚与运维验收。
异机目标按用户决定延期；外部告警端未提供。当前用户不能读取生产状态目录，
只读 console 健康报告中 scheduler watchdog 为 BREACH，不能将其改写为健康。
发布检查通过前不切换常驻 release。M3/M4 不属于首批完成声明。
