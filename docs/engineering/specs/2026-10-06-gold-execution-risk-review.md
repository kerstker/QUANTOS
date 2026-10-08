# Gold 原成交风险记录与损失控制复核

状态：原生结果消费者已实现，独立研究风险和完整交付尚未完成。
父源码为 `438f04c2d8e1081e7977d5dd949f08c19b877242`；独立输出目录为
`/home/kerstker/quantos-dev-output/gold-project-closure-20261003-3pr5mrly/gold-execution-risk-review-20261006`。
延续[九项风险完整性修复](2026-10-06-gold-risk-report-completeness.md)及
[预声明未来采集](2026-10-06-gold-prospective-collection.md)。

## 行为和证据边界

完整、已声明的 Gold 原生结果现在产生 `QUANTOS_PROJECT_RESEARCH_FACTS_V5`，
附带 `QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V1`。消费者沿原完整项目 cut、
试验选择、来源和报告摘要核对后，复核成本后风险谓词和实际成交覆盖；不再
只保留风险摘要。未完成或没有原评估声明的项目仍走原缺失数据分支。

风险权益必须等于原 marked/executable 权益的较小值，gross 必须等于原
各标的名义额之和；正权益、回撤、单名和总敞口检查必须逐项与记录值一致。
获准开仓必须满足原谓词与适用预留约束。合法减仓仍遵守原减仓政策，
合法风险拒绝保留为拒绝尝试，不能算成成交。每份获准记录以原 order/time/
quantity 逐项对应原账本 `ADMITTED_ORDER_FILL`；缺失、重复、错量或换单拒绝。
没有另算现金、费用、资金费或策略 P&L。

5% 政策核对原期初权益、完整有序历史、两份现金对账原件与终态权益。
非空 trigger 必须对应首次真实阈值穿越；ACTIVE、STOPPED_FLAT 和
STOPPING_UNRESOLVED 与原 trigger、实际持仓和挂单一致。触发之后的
退出费用和缺口仍保留。不能从历史穿越推断未留存的回调观察：空 trigger
不补造停止记录，观察覆盖仍未证明。原声明只证明已登记的单账户损失政策，
不冒称九引擎风险政策或独立时间证明。

复核在有界可重复 Sequence 上逐项读取，不展开完整分块历史；原 100,000
常规预算保持，流式消费者显式使用现有 100,000,000 上限。
`project_gold_trial` 默认仍返回原四项；只有项目消费者显式请求第五项。
新 facts 摘要覆盖新投影，不能用旧签名摘要接受新的 V5 内容或改写旧包。

全部复核结果仍为 INSUFFICIENT，mathematical_independence、经济权威、
下单和候选晋级均未开启。`RISK_REPLAY_ADAPTER_UNAVAILABLE` 保留；九引擎
回放、风险估值输入独立重放、回调覆盖、实际账户成本及部署等价性仍未满足。
锁定 OOS、独立来源/结果控制和现场恢复没有被本次复核替代。

## 验证

首次 18 FAIL 原件保留，其中一项夹具重复 order_id 已更正。随后完整红例
19 FAIL，原生项目确实缺少 execution_risk_review。最终三个模块 44 PASS/
15.51 秒，driver 15.952258912 秒；八组原统计、签名、分块和风险消费者
118 PASS/425.48 秒，driver 425.921800066 秒。3131 Python 在每组前后保持，
各组分别报告，不累加为完整门。

此前真实四个封存片段另经新消费者只读复核：四账户、原项目 cut 与全部
原生文件 hash/inode/mtime 保持。channel 和 net_portfolio 各一份获准
记录与真实成交对应，trend/reversion 无成交；全部原损失状态 ACTIVE。
该有限片段没有 forward_design，policy_preregistered=false，未补声明。
driver 4.896389947 秒仅为消费者耗时，不是整月、完整五秒链、独立风险或
现场现金恢复证明。原件为 `real-four-part-risk.json`。

三份新/无遗留诊断 Python 完整 Ruff通过；四文件父/当前各三项同一
E702 遗留诊断，无新增且原忽略策略不改，不称完整 owned Ruff 零问题。
全 E9/F821 和卫生检查通过。服务图首次因源码漂移拒绝，已沿原生成器更新
行号/摘要，20域52边零环保持；复验、实际 hook、干净提交及选择整合另留原件。

此候选尚无自身完整 ARM64/canonical 验收；438f 的在途验收属于438f，
不能借为本候选发布成绩。固定 F1–F5/四组、五秒链和实际持续验收保持。
未部署、切服务、写保护状态、安装实际 trust、读取凭据或下单。
