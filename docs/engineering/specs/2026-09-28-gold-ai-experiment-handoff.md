# 黄金 AI 提案到正式成本试验

新增 gold_ai_research Runner 任务，固定 attempt 1–1000；输入为统一状态根下
`data/cache/gold_ai_research/requests/<attempt>/input.json`（原成本输入编码）及
`request.json`（仅 objective）。每次明确尝试消费一次 SINGLE_V1 模型调用，复用
原 bounded_proposal 预留、失败和中断记录；不发送历史收益或锁定 OOS 给模型。

输出位于 `results/<attempt>`，先独占预留目录并留存原请求与输入，再选择黄金目录中的
一个假设。本增量将原成本归档逻辑提取为 archive_experiment，复用原 TrialLedger、
账本、核验请求及收据检查，不建立另一套资金或试验状态机。

research-origin.json 保存原始成本输入、角色计划、模型提案和所选输入摘要。
归档与独立材料复核均重建候选变更，核对目录、提案、预算和输入的实际关系；
改费用、行情、候选编号或目录后即使重算外围摘要，也必须拒绝。正式 manifest 与
independent-request 均绑定 origin 文件摘要。来源材料仍是本机技术记录，不是独立签名证明。

失败调用有明确结果和原角色记录；中断保留未完成预留。重复尝试拒绝并保留原数据，
不隐式再次调用模型或重跑实验。尚无自动中断恢复机制；需要后续显式恢复流程，
不能用新 attempt 隐藏消耗或规避研究预算。

## 验证

四个协调器回归和真实队列入口回归先失败，随后实现。
黄金协调器、候选、成本任务、核验材料、收据、角色协作、Runner 边界和 Console API
八组联合 **159 passed，1 skipped**；Ruff、治理与仓库卫生检查通过。
变更 run_kinds.py 仅声明 E9/F821 通过，既有完整 Ruff 问题不被本次结果掩盖。

DGX 本地 `/v1/models` 实际返回 qwen3.8-27b，随后通过原 LOCAL_HTTP 传输执行
一次真实提案。复用旧公开成交/报价档案，在全新隔离目录为当前源码建立独立输入身份，
原档案未修改；不是新的前向观察，也不是事前预登记或 OOS。

- 实验目录：本机 `quantos-dev-output/gold-ai-research-20260928/1790579380172423115`。
- 队列：`run_b581bd1d1406aeaa7214cec3b3979e02`，done。
- 试验：`559dc6dc0899c97a0c4bff1590453c11354e3312d8f3f2d3189bfa77f42eeeb9`。
- 模型选择：0，提案摘要 `91f7d537feb1ae1be23c168fc20ca977c23d407b55b1886539af78dfb69c9e8d`。
- 结果：NO_SIGNAL，初末模拟权益 1000，全部现金流及残差为 0。
- 回放结果摘要：`5ade178495749c3de0682453ab0627d8b2c0d61d840a271f98a9a462e1f8b3f1`。
- 核验：INDEPENDENT_AUTHORITY_UNCONFIGURED，authoritative=false，submission_enabled=false。

真实运行后再次读取原角色记录、重建候选输入并重算完整成本报告，与留存报告比较。
该复核是本地技术复算，不是独立核验；现场摘要与复核记录分别为 summary.json、verification.json。

## 剩余闭环

这一增量完成单次真实 AI 假设到正式技术试验交接，未完成多轮预算化研究、
独立 OOS、全局试验预算、自动恢复、独立准入或黄金 Demo。
没有部署常驻服务、没有下单。F1 和整个工厂闭环继续保持未完成。
