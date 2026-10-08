# Demo 执行到研究的技术复盘材料

## 接口与来源

live/execution/demo_research_feedback.py::build_feedback 是原执行侧内部导出器。它从 prepared 原证明、派发预留和私有流重新调用 prepare_resolution，再打开由原导入器生成的日志执行 project_cash_chain。调用者不能通过提交一份自称PASS的现金摘要替代这些来源。

材料绑定 strategy_id、账户、reservation/proposal、原日志 manifest/tail、查询恢复计划、当前派发快照、现金捕获及现金报告摘要。available_at_ms 取现金报告与查询恢复的最晚可用时间，晚于 as_of_ms 时拒绝；查询及现金事实都必须匹配。输出含精确持仓和现金分项，并以 result_hash 固定内容。

## 不授予的含义

DEMO_EXECUTION_RESEARCH_FEEDBACK_V1 的scope是TECHNICAL_REVIEW_ONLY。开仓尚未平仓时position_flat=false。执行策略到研究candidate/trial的映射没有证据，candidate_attribution=UNRESOLVED，research_trigger_enabled=false。source_verified、coverage_verified、economic_eligible、submission_enabled均false，strategy_pnl=null。它不是收入归因、OOS评价、批准或修改预算入口。

## 验证

扩展黄金同批执行/现金/OMS测试：同一manifest和reservation可生成稳定复盘材料；早于收入读取完成拒绝，换账户/成交/余额拒绝，后到UNKNOWN查询使原材料不能再次生成。四组既有执行/现金/OMS边界联合34项通过。Ruff E9/F821、现有治理扫描698文件零违规、仓库卫生通过。

## 未完成的消费者

原gold_factory_campaign只允许固定输入、有限轮次和候选排除；gold_ai_origin严格绑定当前模型提案的空历史，不能直接塞执行结果进去而保留旧收据版本。下一步需要候选/正式试验到执行策略与任务的可核验映射，以及原campaign的有界反馈消费和模型来源更新。当前函数尚未接运行导出或研究调度，不将接口完成记作自动复盘/再研究完成。未部署、未读实际凭证或发送订单，未完成canonical。
