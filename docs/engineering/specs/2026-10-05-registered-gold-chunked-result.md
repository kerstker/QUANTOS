# 原黄金分块报告登记任务与冷恢复

父提交：`031731deda5975f12c2a2a227d351dff1f757422`。
推进[既定工厂 F1 交接](../plans/2026-09-28-ai-factory-closed-loop.md)，
复用[完整分块结果读者](2026-10-05-perpetual-result-reader.md)。完整系统目标继续。

## 明确版本与原登记

新增 `QUANTOS_REGISTERED_GOLD_CHUNKED_RUN_V1` 和
`QUANTOS_REGISTERED_GOLD_CHUNKED_FORWARD_RUN_V1`。
只支持原 `GOLD_BAR_FORWARD_V1`，后者继续绑定原事前 forward_design 与政策摘要。
新 `result_storage` 必须声明固定 schema、源字节、派生索引字节与块字节预算；
源与索引各最多 32 GiB，每块最多原 16,000,000 字节、最多 4096 块。
原 plan 65,536 字节、input 16,000,000 字节及全 run metadata 64,000,000 字节预算保留；
新报告子树独立检查私有目录/文件、成员、块数量与总字节，不将旧入口上限整体提高。
旧计划、默认 `prepare_gold` 和原单文件报告路径保持原格式。

原 `_native_run` 仍先登记 search/candidate/provenance，然后写原 trial ledger 的
PLANNED/RUNNING、invocation，调用原 Gold 生产者一次。没有新现金账本或试验数据库。
三原候选与可选第四 net_portfolio 沿原各自账户和原共享组合；
不把黄金策略转换为技术直接目标仓位，不制造新成交或收益。

完整原 Gold 比较 JSON 写入 `archive/report-parts/{manifest.json,blobs}`；
`archive/report.json` 在新版本中是显式小 reference，绑定全部原字节、原结果身份、
原输入 SHA 和源码身份 SHA。原结果 hash 仍从所有原报告字段计算。
归档 manifest 新标识 `QUANTOS_REGISTERED_GOLD_CHUNKED_EXPERIMENT_V1`，包含冻结原计划。
共同读者核固定 manifest SHA、全部有序块、完整报告 SHA/identity、
Gold 根 schema/非授权标志/源码绑定，并在消费前后核同一物理集合。
共享读者的原 perpetual 入口仍严格要求原 V3 根形状，没有泛化它的准入范围。

## 原队列与恢复

新增原 JobQueue 的 `registered_gold_chunked` 与 `registered_gold_chunked_recover`。
参数只有固定 `native_invocation_id` 摘要；不能传路径、代码、回调、时钟、trust 或预算。
预算只来自可信离线准备者的冻结原计划，调用原项目/原 ExperimentRegistry/原 TrialLedger。
原 registry 与治理白名单一起显式加入两个 DIAGNOSTIC_ONLY 类型，未开放 selection-bearing。
队列镜像显示每个原候选的原 reconciliation，核完整 cohort、有限 Decimal 与现金恒等式。
publication 中断后的原报告可在同 invocation 完成原 trial/search 终态；
报告未完整持久化则保留 UNKNOWN/RUNNING，不再次执行生产者，也不删 trial 或失败原件。
真实生产者失败沿原 FAILED 试验与 search 历史保留。
已经完成的报告损坏则恢复拒绝，不能通过重算或覆盖修复原记录。

`submission_enabled`、`economic_authority`、`promotion_approved` 全为 false。
镜像如实报告 CHUNKED_EVIDENCE_ADAPTER_PENDING、NOT_INDEPENDENTLY_REPLAYED、
INDEPENDENT_REVIEW_REQUIRED、NOT_APPROVED；登记成功不等于独立研究准入。

## 验证范围

使用独立 Python 3.12 ARM64 环境、任务专属输出与前后全 Python 文件 SHA。
最终同版新协议/队列、原完整读者/原登记/技术队列/runner/分段/评估/投影及治理/DGX入口
278 passed / 90.91 秒，driver 91.234238 秒 exit 0，3081 Python 文件前后完全一致。
此前 130 项以及声明追加检查为各自版本记录，不与本次计数相加。
本版未运行或宣称 canonical ALL、ARM64 发布及现场验收通过。
保留缺接口、缺 registry 白名单、治理诊断类型清单漂移等红例。
新/改七个无旧 Ruff 债文件全 Ruff 通过；其余六个修改 Python 的 29 条父诊断不变。
全仓 F821/E9、卫生与 diff 通过。导入图按原生成器更新后 20 域/52 边、零循环且 current。

实际原生产者→原登记→真实 JobQueue→完整分块消费→冷恢复已验证：
一次生产者调用，恢复原报告/原 trial 库逐字节不变，准备与执行保持三原 provenance。
追加 report/outcome 发布中断、字节预算失败、损坏拒绝、原生产者失败保留，
声明前向/两原连续段/实际结算输入/三候选或第四组合，政策保持且资金费 -.3、残差 0。
声明用例首次错误复用夹具旧输入/时钟，被原 PLAN_BINDING 拒绝；只修测试准备者，未放松门。

大报告验证在原实际生产者结果中添加明确的合成传输负载，重新核完整身份，
全部原现金/持仓/订单字段不变；报告超过旧 16,000,000 字节单文件预算，
沿新显式 24,000,000 字节源预算/128,000,000 字节索引预算成功封存并完整读取。
这只证明新格式与登记消费，不是长市场样本、真实盈利或有界 Gold 生产者性能证据。

输出：`gold-project-closure-20261003-3pr5mrly/registered-gold-chunked-result-b99dq9gv`。
保留全部各版日志/XML、失败原件与全源码冻结，测试计数不跨版/重叠累加成完整门。

## 仍须接通

现有 `ProjectResearchEvidenceReader` 的旧 archive member 合同不消费 report-parts，
旧 retained-cut、独立评审材料与 Gold 统计消费者也未消费新 reference。
包含新格式 run 的完整 project cut 目前会被旧成员检查拒绝；
不能过滤掉这些 run、仅看选中 trial、把 reference 当完整报告或将拒绝折算成 PASS。
下一步必须沿同一完整 population/cut 版本化接通这些消费者；本批不能部署成完整工厂。

Gold 原生产者仍保留内存历史；本批没有解决长期输入、完整 bar/trace 落盘、跨日环境、
真实 30 天单账户与统计 population/全局试验控制。独立源/结果/时间与真实成本风险、
锁定 OOS/候选资格、正式整组同 cut 停写及跨角色业务现金恢复、
最终同版 ARM64/canonical 全门、获准现场安装升级恢复及实际 24 小时 Demo—现金—再研究
仍按原 F1–F5 验收。机构异机按原决定延期，其他市场单列，盈利策略随后研发。
本批未部署、切服务、写保护状态、安装现场 trust、接触凭据或发单。
