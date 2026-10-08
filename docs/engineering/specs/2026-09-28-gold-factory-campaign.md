# 黄金有界轮次接入原 FactoryStore 与调度器

原 campaign 使用 72 个小时级模型、每轮 3–18 个试验，不能直接用于黄金。
本增量为现有 config/session 合同增加显式 XAUUSDT 分支，复用 campaigns、
campaign_rounds、sessions、trials 和 events；正式试验仍由原 TrialLedger 留存。
没有新增资金账本或平行调度数据库。

黄金合同固定 ai、每轮一个 SINGLE_V1 提案、最多六轮、至少一小时间隔。
input_id 与 input_sha256 冻结原成本输入。已知候选（包括成本失败和可捕获中断后的选择）
从下一轮排除；模型未产生可核验选择时保留 candidate_id=null，失败仍消耗轮次预算。
不能保证未知模型响应曾选中的候选不被另一个明确轮次再次选择；不会重新发送原调用。

FactoryStore 负责持久预算、配置身份和轮次预留，原 campaign_tick 按市场派发
gold_factory_research。幂等 invocation 仍由原 Runner 管理。黄金 session 使用
`ai_factory/gold` 目录，不与独立 gold_ai_research 的尝试编号冲突。
worker 记录原试验编号、结果摘要、提案摘要；已完成材料可复核，失败不重跑。
暂停/停止控制和间隔政策沿用原实现。没有安装或启动常驻调度。

黄金任务不能进入原小时级执行器。冻结输入变化、候选范围/预算损坏、额外试验记录
必须在下一次预留或执行前拒绝。模型只收到剩余目录，不接收本轮/历史收益反馈，
因此本增量是有界假设遍历，不是已经完成结果驱动的自适应研究或独立 OOS。

## 验证

黄金轮次/真实队列集成先失败，随后实现。首次接线被原 Runner 的来源检查拒绝：
适配器错误指向 gold_ai_research.py，而入口为 gold_factory_research.py；已修正精确来源，
保留原检查。十一组联合 **183 passed，1 skipped**。补齐可捕获中断的已知候选消费后，
黄金与原小时级 campaign 两组 **17 passed**。这些是专项验证，不能合并为全量验收。

隔离测试覆盖两轮不同候选、失败计数、间隔、预算耗尽、暂停、错误市场入口、冻结输入变更、
额外记录与中断。两轮测试使用模拟模型和显式测试时钟，不冒充真实一小时观察。
新文件/工具完整 Ruff、既有修改文件 E9/F821、治理和仓库卫生通过。

实际调度验证使用先前公开成交/报价档案，在独立状态根为当前源码创建新输入身份，
调用本地 qwen3.8-27b 一次；配置 max_cycles=1，结束即耗尽预算，未加速真实时间：

- 目录：本机 quantos-dev-output/gold-factory-campaign-20260928/1790580969594853813。
- 队列：run_7b89d4d25ed87d677afd8286b2620e23，done。
- 试验：78d5ae607fa02ccfe96ad9aedbd1ba93e3e46654fd77a339aebc0c3b8c7c6d8b。
- campaign/round 均 COMPLETED；此状态仅代表计划的技术研究任务结束。
- 候选 0，NO_SIGNAL，模拟权益 1000→1000、残差 0，原公开输入不变。
- 独立核验仍 INDEPENDENT_AUTHORITY_UNCONFIGURED，authoritative=false、submission_enabled=false。

## 仍未完成

单次和有界轮次现能共用 AI 提案、正式试验和独立材料链，但独立核验方、锁定 OOS、
全局研究预算/跨 campaign 重复尝试、反馈驱动新实验、黄金 Demo 与现金闭环仍未完成。
进程遭强制终止时的预算对账与自动续派发仍需进一步验收，不能由捕获 KeyboardInterrupt
的测试推出任意崩溃安全。真实多轮持续运行和服务部署尚未执行。
