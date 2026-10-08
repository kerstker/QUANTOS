# XAUUSDT 工厂到 Demo 的实际交接缺口

2026-09-28只读源码核对和公开接口实测；没有读取凭证、生成批准、发送订单或修改运行服务。

## 场所证据

使用仓库 `quantos/contracts/binance_demo.py` 的固定 Demo 域名访问 `/fapi/v1/exchangeInfo`，HTTP 200，唯一 XAUUSDT 行为 TRADIFI_PERPETUAL、TRADING、XAU/USDT、USDT 保证金。原响应及有时间戳摘要保存在本机 `quantos-dev-output/gold-demo-handoff-audit-20260928/`。响应原字节 SHA-256 为 `229211985caa877de74d9e5f873b7d551566a92a21ced004007588877f24c5c7`。这是当前公开品种支持证据，不证明账户权限或历史规则。

现有公开 profile 解析成功，但仍明确缺少合约乘数、账户费用及风险档位，且 MAX_NUM_ORDERS/PERCENT_PRICE/POSITION_RISK_CONTROL 未建模；execution_eligible=false。不能直接把这个 profile 当作完整执行规则。

## 实际消费者与缺口

| 交接 | 已有实现 | 尚需完成 |
|---|---|---|
| 黄金信号到成本后实验 | gold_replay 输出 RESEARCH_SIGNALS_ONLY；GoldV2Strategy 可驱动原 V2 模拟器 | 在研究任务中绑定可核验规则、报价、资金费和成本，运行唯一原账本；当前净收益为空，不能将信号计数作为收益 |
| 研究到独立准入 | ai_factory_verification 生成核验包；selection_authority 仍拒绝全部准入 | 独立签名的工程验证与经济资格分开，不能自行签发或把 COMPLETED 当批准 |
| 研究组合到执行意图 | live/execution/demo_portfolio_task.py 的 strategy_id 只接受 BTCUSDC | 黄金信号协议不同于现有组合预测，需有明确身份和规则的转换，不能只扩展币种字符串 |
| 预测和资金费来源 | tools/demo_portfolio_sources.py 固定 BTCUSDC 行情、资金费路径及组合 forward freeze | 黄金必须绑定自己的公开输入、时间和策略版本，不能复用 BTC 预测或资金费 |
| 原订单校验 | services/validation/demo_submission.py 只允许 BTCUSDC/BTCUSDT | 连同账户观察、合约规则、签发范围和风险语义一起验证黄金支持，保留原批准门 |
| 原协议及观察身份 | quantos/contracts/binance_demo.py、binance_demo_orders.py、demo_issuer_policy.py、demo_market_inputs.py、demo_execution_origin.py 均限制 BTC 两品种；合约类型只接受 PERPETUAL | 黄金实测为 TRADIFI_PERPETUAL，需明确版本化的类型/规则支持；签发范围不能由代理自动扩权，旧 BTC 回归必须保留 |
| 成交到资金反馈 | demo_portfolio_prepare.reconcile 复用 settle 与原现金对账 | 绑定黄金策略/执行批次，再向已有有界 campaign 反馈；不能建立另一份现金账本 |

## 后续实现顺序

先完成黄金成本后实验所需规则和输入交接，再完成独立核验及黄金 Demo 意图转换。Demo 品种存在已经实测，因此不必要求用户重新创建账户来解决源码不支持的问题。账户或签发材料的实际配置仍须通过原独立进程核验，公开查询不能代替。

开发每个交接时先建立正常路径和错市场、过期、缺规则、无批准、中断恢复的回归；不以新增状态面板代替该交接。研究端保持无 broker/凭证访问，实测提交留在原独立执行边界。

## 本次验证

黄金 V2 策略、公开合约 profile、参考规则、Demo 组合任务和组合来源五组现有测试：51 passed（8.54秒）。这只证明这些现有组件的测试通过，不是黄金端到端验收，未新增执行支持。

另行运行的冻结维护版本4d8261ade完整验收已完成后端9373 passed、19 skipped，执行/tools阶段仍在运行；不可将该结果归入黄金闭环通过。
