# 黄金正式试验与执行策略身份

## 实现

新增 tools/gold_execution_identity.py::from_archive，复用 review_request、TrialLedger核验和verify_origin重读正式AI试验。候选、trial/definition、选定输入、报告、模型来源、manifest与核验请求摘要进入GOLD_RESEARCH_EXECUTION_IDENTITY_V1。声明的可用时间来自原试验终态事件；本地结构核验不证明独立时间或经济有效性。

services/research/gold_execution_identity.py由完整内容摘要派生gold-research-策略标识，符合原80字符签发标识范围。该接口不写profile、mandate、attestation、独立policy或注册表；不把PASSED技术计算解释为经济准入，NO_SIGNAL也不构成合格策略。

原执行反馈导出器接受可选身份材料，只在XAUUSDT、内容摘要正确、材料声明可用时间不晚于原派发预留、且策略标识等于原签名证明strategy_id时关联。candidate_attribution=CONTENT_BOUND_TO_SIGNED_STRATEGY仅表示内容与签名策略身份匹配。研究触发、经济资格和下单权限仍为false，strategy_pnl仍null。

## 验证

新身份接口先取得缺失模块失败回归，再实现。正式档案导出稳定性、候选篡改、原模型来源篡改、未来材料和错误策略ID均验证。四组23项通过；追加正向执行联调后黄金专项6项通过。

正向场景实际运行原AI档案生成器（模拟模型）、原成本试验与TrialLedger，再把所得标识配置进仅用于测试的原独立签发fixture，之后才生成提案和签名。模拟执行、私有流、原日志、现金、OMS及反馈可关联原trial/candidate。生产批准未修改；该测试不证明独立部署或真实市场适用性。错策略声明被拒绝，未归属旧订单继续保持UNRESOLVED。

## 未完成

尚未接运行配置导出/安装、真实策略批准或原campaign反馈消费。尤其没有自动把研究结果升级为可执行订单、没有独立OOS/经济准入。下一步需要在原调度预算、冷却、暂停和模型提案来源校验下消费反馈；不能靠追加字符串目标绕过证据边界。未部署，未读真实凭证或发单，未完整canonical。
