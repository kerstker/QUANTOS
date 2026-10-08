# 黄金研究记忆与假设空间：功能缺口核对

检查当前实际worker、模型适配器和真实首批归档，确认有界周期调度没有完成研究记忆闭环。

## 已证实行为

- services/research/gold_factory_candidates.py 的 GOLD_ABSORPTION_V1 为六个固定选项，仅组合sell_threshold=10/25/50与confirm_windows=2/3。不是任意策略生成。
- tools/gold_factory_research.py只向请求写入objective及remaining_candidates；执行现金反馈按RESEARCH_TRIGGER_ONLY留存。
- tools/gold_ai_research.py把空history传给bounded_proposal；tools/gold_ai_origin.py明确要求history=[]，options仅candidate_catalog。
- 真实目录gold-periodic-real-20260929-44ph40wj的首批research/plan.json确认为SINGLE_V1、history=[]、maximum_calls=1，remaining=0..5。不能从首批空历史推断第二批结果，但源码已表明该调用同样不提供跨批实验摘要。
- 原ResearchContext只允许同实验、同快照、同分区、同INNER_SEARCH且知识时间/市场时间均已到达的记录。不能把新批次或执行现金直接伪装成同一内层成绩。

## 下一功能工作

复用原试验档案、模型调用和不可变来源，不建第二本试验账。新增明确版本的技术研究记忆交接，先限定可用事实（原试验身份、数据窗口、候选、技术终态与失败原因），建立研究截止时点和用途；不把独立OOS、账户现金、原来的PASSED技术状态作为模型收益标签。历史来源需要绑定原试验与报告并持久冻结在新请求中，恢复时读取原冻结材料，不随当前资料变化重选。

后续同时需要扩展可表达的研究假设范围；六个固定阈值选项不能被标为“AI策略生成完成”。该改变属于后续独立源码增量，不能修改正在运行的原目录真实研究或e29cbb6验收候选，也不能把当前周期自动化重新标为已具备研究学习。

本记录是功能核对，不代表上述记忆或扩展策略空间已实现。已有周期交付继续，整体工厂目标保持未完成。
