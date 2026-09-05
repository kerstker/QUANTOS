# DGX 订单准入与额度预留增量

依据：[已授权路线](../plans/2026-09-05-dgx-engine-foundation-roadmap-review.md)。
承接 [首批实施](2026-09-05-dgx-engine-foundation-increment1.md)。
边界仍为 `L2_DRY_RUN_ONLY` / `TECHNICAL_VALIDATION_ONLY`。

## 本次行为

`services/execution/perpetual_simulation.py` 增加 `OrderRuleProfile`，通过
`run_linear_perpetual_simulation(order_rule_profiles=...)` 显式启用。每个标的
恰有一个覆盖整段模拟且开始前已摄入的规则 epoch，绑定 `HistoricalContractRule.rule_id`
和 funding venue；记录 observed/available/ingestion 时间与 source identity。
空集合、缺失标的、场所不一致、绑定错误、过期或未来才可知的规则均拒绝运行。

原订单检查价格 tick/上下界、市价与限价的数量上限、最大名义额及允许订单类型。
数量步长、最低量与最低额复用所绑定的 `ContractSpec`。限价按声明限价检查最低额，
市价按当前可执行价加模型滑点检查；这是一项明确的模拟约定，不声称等同场所的
所有动态过滤器。执行时不把小额成交切片重新当作原订单检查最低额，但重新检查
成交价格范围及按原订单总量计的最大名义额，不能通过切小片绕过上限。
滑点价格买入向上、卖出向下取 tick，随后仍受限价约束。非法报价网格拒绝输入。

最低额政策有两个显式版本选择：

- `ORIGINAL_ORDER`：按原订单总量检查。例如已有 `.1` 多仓，卖出 `.11` 的合格
  原订单可以留下 `-.01`；不能因为剩余新仓低于最低额而拒绝整个反手。
- `OPENED_POSITION`：保留旧保守规则，反手后的新仓也需达到最低额。

两者都进入输入身份。reduce-only 的原订单最低额豁免仍必须满足真实减仓；
成交时继续检查库存，不能在另一订单平仓后开出反向库存。

## 预留合同

政策：`CROSS_GROSS_PENDING_MARGIN_FEES_ADVERSE_BUFFER_V1`。
本批新规则模式仅支持全仓；逐仓意图明确返回 `UNSUPPORTED_ORDER_MARGIN_MODE`，
未把全仓公式套到逐仓钱包。逐仓订单预留及场所特有规则仍属后续工作。

- 所有非 reduce-only 挂单都预留额度，买卖不互抵，多标的竞争同一个全仓钱包。
  同一标的以现仓绝对量加所有挂单绝对剩余量构成保守数量上界。
- 预留初始保证金按 canonical `ContractSpec.margin_requirement` 的档位增量计算，
  不复制保证金/PnL 公式；价格取 mark、含滑点 ask、挂单限价的保守上界。
  另加剩余订单费用和不利成交相对 mark 的损失缓冲，不预支有利成交收益。
- 可用额度使用 canonical 全仓权益与当前可平仓权益中较低者，扣除保守现仓
  保证金和挂单预留。挂单归属按接收顺序分配档位增量，总额不依赖买卖互抵。
- 额度是 OMS 承诺，不是第二本现金账。不会扣减 ledger cash 或制造账本事件；
  实际费用、资金费、成交及保证金仍只由 canonical ledger 记账。
- 准入不足直接拒绝。拟成交在账本事务内部重新估值，并将本次已成交数量从
  待预留数量剔除；剩余挂单预算不足时撤销拟成交，再拒绝订单、释放承诺。
  深度/容量和实际现金均不被失败尝试消耗。
- 部分成交仅释放已成交部分；撤单、到期、拒绝或完成后不再占用额度。
  同一个 callback 中撤单后提交替代订单可以立即使用释放的预算。
- reduce-only 不预留新开仓额度，真实减仓不因别的挂单出现预留缺口被拦；
  已发生的损失照常记账，其余挂单在后续成交前重新检查。普通非 reduce-only
  平仓意图仍按潜在开仓预留，策略应显式声明 reduce-only 来保证退出语义。

策略上下文增加不可变的 `reserved_collateral` 与 `available_collateral`。
订单事件保留转换后的预留快照；决策保留当时策略可见的预留；拟成交风险报告保留
事务内预留。每次市场变化后按当前信息重估，未冻结价格，也不承诺以后一定可成交。

## 身份与兼容

结果 schema 为 `LINEAR_PERPETUAL_FORWARD_SIMULATION_V3`。订单规则内容、
预留政策和配置进入输入 hash，结果增加 `order_rule_profiles` 与 `ending_reservation`。
同输入重复运行一致；追加不同未来价格不会改变已发生的准入及预留事件。

旧调用不传 profiles 时仍可运行既有技术诊断，但明确标记
`UNRESERVED_LEGACY_DIAGNOSTIC` 和 `ORDER_RULE_PROFILE_MISSING`。这不是新规则
验收路径；D1/I1 新研究入口必须要求完整 profile，不能静默走兼容路径。
所有结果继续非权威，候选晋级、paper-fill 持久化与实盘门保持封锁。

## 验证与剩余工作

先新增回归并运行：12 failed，确认旧实现缺少该能力。扩展后的 23 项新回归
覆盖 tick/数量/金额/订单类型、反手政策、双向及跨标的争用、档位跃迁、部分成交、
撤单/到期释放、同 callback 替代、价格跳变、失败后深度与容量复用、未来前缀和
真实减仓；相关模拟测试最初一轮 54 passed。

最终 DGX 独立入口执行/风险/开发入口专项 **458 passed**，原有测试未删除。
受影响治理边界 **56 passed**；有一条既有 Starlette 测试依赖弃用警告。
专项原始输出 `/tmp/quantos-dgx-development-zland25j`，治理输出
`/tmp/quantos-order-governance-it0qunph`；持久副本放在独立开发输出根的
`increment2-verification/` 下。提交前继续执行原有治理与卫生 hook。

本批没有冻结真实 Binance 历史规则和公共数据，也没有证明场所完全等价。
D1 数据合同/样本、逐仓预留、I1 定频统计/研究入口、V1 外部差分与 R1 发布仍待完成。
M1/M2 尚未验收；专项通过不替代完整 canonical gate。未写运行数据库、改变依赖或
启动/切换服务。回退本次代码后，V3 结果必须与其规则/政策身份一起隔离。
