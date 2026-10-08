# 黄金 AI 实验恢复与排他

新增受限 gold_ai_recover Runner 任务，使用原 attempt 及留存目录。恢复不会重新调用模型。
原执行和恢复共用既有 acquire_runtime_lock 实现的进程内锁及 OS 文件锁；保留锁文件，
不以删除文件的方式解除锁。持锁期间的第二个操作返回 GOLD_AI_BUSY。

支持两种有明确证据的继续方式：

1. 模型提案与角色记录完整、trial 尚不存在：重新核对原请求、提案、目录和所选输入，
   源码身份仍一致时执行一次原成本试验。
2. trial 已有完整终态及 manifest：复核原账本、报告、origin 和独立材料，只重建缺失摘要，
   或在原摘要存在时比较其全部稳定字段。原模型和回放均不重跑。

收据时效每次重新核对，结果追加到 recovery-checks；原完成摘要不被后来的验签结果覆盖。
成本归档模块新增只读终态重建入口，复用原 review_request 与 check_receipt，不产生另一个试验。

以下情况仍拒绝自动继续：模型调用未完成、试验目录已存在但终态未知、失败终态、
原摘要与材料矛盾、源码变化后尝试执行尚未开始的试验。没有把文件存在或观察超时当作
进程终止证据，也没有把 RUNNING 账本改为 PASSED。其余中断位置需要后续明确恢复设计。

## 验证

六个恢复回归及队列入口回归先失败，随后实现；另覆盖源码变化拒绝。
恢复、AI 交接、成本任务、核验材料、收据、Runner 边界及 Console API 七组
**151 passed，1 skipped**。包括模型完成后中断、试验完成后摘要缺失、终态未知、
未完成模型预留、并发竞争、摘要错配、源码变化与真实 JobQueue。
新改工具/测试完整 Ruff、注册文件 E9/F821、治理与仓库卫生通过。
本次不是 canonical 全量验收，未部署。

实际对已完成的本地 Qwen 试验执行恢复队列：

- 原试验：559dc6dc0899c97a0c4bff1590453c11354e3312d8f3f2d3189bfa77f42eeeb9。
- 恢复队列：run_5368f9861f29e6390f2c307d6fd8dbf4，done。
- 原 result、全部角色记录、成本 input/report/manifest/ledger/origin/核验请求摘要均不变。
- 返回原 NO_SIGNAL 结果；独立核验仍为 INDEPENDENT_AUTHORITY_UNCONFIGURED。
- 现场摘要：本机 quantos-dev-output/gold-ai-recovery-20260928/1790579855289035870/summary.json。

这是对真实完成试验的重验；故障注入恢复仍是隔离测试，不能称作真实生产中断恢复验收。
多轮研究预算、全局试验历史、独立 OOS/准入、黄金 Demo 和现金反馈闭环继续未完成。
