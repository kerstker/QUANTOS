# 同一黄金 campaign 的自动执行反馈续轮

## 实现

原黄金 campaign 新增显式 `execution_feedback_mode: after_first_round`，不能与固定 execution_feedback_sha256 混用。第一轮按原有调度运行，后续每轮等待本 campaign 上一轮试验的整轮执行反馈。没有新建第二套预算或自动新建 campaign；原 max_cycles、同输入跨 campaign 六轮总预算、冷却、暂停/停止、候选排除和事务预留继续适用。

原 scheduler 扫描固定已交付反馈目录，最多1000项/16MB总大小，每文件2MB。只选择上一轮 attempt 的成熟整轮反馈，验证原消费者摘要/身份/输入规则；确定性选择后将摘要传给原 reserve_campaign。已预留轮次直接使用 session 内原摘要，保持队列 invocation 身份及恢复语义。读取拒绝链接、多硬链接、FIFO、超大及读取中变更，临时发布文件不作为反馈。

store 在事务内再次验证反馈必须属于上一轮、原 completed trial 和候选；预留的新 session 固定 feedback 摘要，verify_campaigns 重验逐轮绑定与跨 campaign 不重复消费。错轮、缺摘要、单批次反馈不消耗预算。用尽原轮次后，scheduler 关闭 campaign，不要求先取得另一份反馈。

控制台返回新的可选配置字段，TypeScript 同步；只读状态显示 EXECUTION_REVIEW_SCHEDULER_CHECK_REQUIRED，不把等待反馈当作普通 schedule 已就绪。控制台本身不授予执行资格，也不代替 scheduler 的实时文件检查。

## 验证

新同 campaign 回归先因未支持策略字段失败。修复后研究/队列/预算/边界六组41 passed；追加完整自动模式/文件边界测试8 passed。原黄金两批及三批执行场景与新自动场景联合12 passed，其中自动场景使用原第一轮研究队列、隔离签发、实际原开平仓/现金/反馈导出与交付函数，随后同一个 campaign 自动排入第二轮新候选和试验，不再由测试手动 create_campaign(2)。

控制台合同两项通过，保留 Starlette/httpx 弃用警告；前端 typecheck 和改动文件 Ruff E9/F821 通过。实际签名/模型/私有传输仍为隔离 fixture。

## 未完成

没有修改现场 campaign 配置、后台服务或发布版本。尚需部署登记与后台反馈重试交付、实际独立进程/真实 Demo 验收、持续采集与长期运行、独立经济准入和合格策略。现有纯定时/pinned campaign 不自动改变语义；新增显式模式也不代表允许 AI 改实盘参数或保证盈利。F4 的开发联通更完整，但现场工厂目标仍未完成。
