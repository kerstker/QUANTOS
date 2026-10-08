# 黄金bar精确批准消费接回原dispatcher

承接[十环节交付目标](../plans/2026-09-28-ai-factory-closed-loop.md)、[持续pilot](2026-09-29-gold-demo-continuous-pilot.md)及[首次黄金安装](2026-10-01-gold-first-activation.md)。原worker/controller已支持 retained、fresh、auto 三类bar episode，但 `gold_strategy_dispatch.run` 仍只接受旧吸收策略的两种runtime。黄金bar决策即使已有独立安装的精确policy，也在 CONFIGURATION 阶段拒绝，无法进入原任务交接。

原dispatcher现按五种已有runtime的精确字段读取。bar输入仍只接受既有候选、1–900秒预算、固定40/64位身份及原时间类型；无新增shell、路径或动态代码输入。auto通过原rotation的read-only加载当前episode，不创建轮换记录、不消费旧episode决策。原result摘要、runtime摘要、account、source、策略definition和精确constraints均继续核验；未获当前精确批准仍返回 WAITING_INDEPENDENT_APPROVAL，不读取bar前缀或创建任务。

对获准bar决策，retained通过原archive/worker loader重建definition和原entry signal；fresh/auto复用独立审批侧的原 `review_bar_prefix`，只读还原原Frozen EpisodeTail snapshot。两次复核一致后再读配置、批准、runtime、原result、profile及rotation；不重新采集、选策略、制造信号或延长期限。前缀读取可能耗尽短信号期限，最终task仍用当前时刻校验原deadline，再由原checkpoint材料化和原claim/issuer/risk/ledger链消费。响应丢失后同一task仅QUERY_ONLY，不生成第二次发送。

隔离回归先复现三种bar runtime均拒绝。后续覆盖原rotation实际读写和旧episode拒绝、未知输入/越界字段、原始前缀冲突、配置/result中途变化、读取后过期，以及三种bar交接响应丢失后的原claim防重发。原Frozen EpisodeTail的真实记录/摘要重建接入dispatcher，后续公开bar追加不替换已批准snapshot；archive/source/custody为明确隔离替身。另从新材料化task进入原native risk→ExecutionSession.propose→独立issuer→签名检查，未调用实际exchange/send或读取凭据。合成测试密钥和账户材料不证明现场批准或经济来源可信。

原三项失败/4.50秒保留。先修复输入消费，原dispatcher及新三项共7通过/7.42秒；新增边界后新22项通过/14.39秒，加入原tail和过期episode消费验证后，八组140通过/55.23秒。另四组原bar worker/fresh/rotation/definition合同22通过/3.82秒。这些结果不累计为不同用例总数，也不替代完整canonical ALL。源码/原输入读取与发行验证分别记录。

本增量关闭bar决策到原任务消费者的代码断点。首次24小时shared-budget scope、独立经济来源、实际操作员安装、持续pilot/监控恢复、真实资金反馈、现场升级回滚与多资产验收仍未完成。不得用占位constraints摘要生成24小时权限；不得将局部测试或祖先完整PASS计作本版完整验收，也不声称真实五秒内可执行。未部署、切服务、改保护运行状态、读取实际凭据、下单或新增策略研发。
