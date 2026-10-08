# 黄金私有回执导入原账本与查询恢复

## 实际断点

延伸既有黄金签名订单/模拟发送/私有流测试，合法导入先失败为 RECEIPT_IMPORT_VALUEERROR。修复期初绑定和原成交日志市场版本后，再延伸原日志生成查询计划，复现 QueryPlan 的 BTC Literal 拒绝。两处均先取得失败回归，再修改消费者。

## 实现

- 黄金使用 DEMO_GOLD_EXECUTION_REPLAY_V2 期初绑定日志，强制 XAUUSDT/USDT 与黄金原始回执版本一致。复用原 DemoExecutionJournal、期初账户核对和唯一 Decimal V2 账本，不创建新账本。
- 原观察轨迹增加 DEMO_GOLD_OBSERVED_TRACE_V1；黄金只经过观察校验，不进入 BTC 被动成交校准。V3 时钟区间、可用时间和原始费用语义保留。
- 黄金固定查询计划与捕获分别使用 BINANCE_DEMO_GOLD_QUERY_PLAN_V1、BINANCE_DEMO_GOLD_ORDER_QUERIES_V1。旧 QueryPlan 字段与默认捕获版本保持；交叉重标拒绝。原查询采集、内容寻址保存/加载及 compare_orders 共同消费新版本。
- 真实隔离 SQLite 日志导入合成黄金成交；精确费用 USDT 0.01、持仓等于原订单数量。重复导入和只读重启报告一致。查询编号、数量及终态匹配时可通过原 prepare_resolution 得到 READY_FOR_OMS_PROJECTION；缺少原查询仍拒绝。

## 验证与边界

四组 42 passed；五组 73 passed；追加 OMS 计划回归的黄金专项 5 passed。另有开发中六组 78 passed，后续测试延伸后以对应新结果为准。Ruff E9/F821、diff 空白检查、治理 698 文件零违规和仓库卫生通过。

所有输入为合成私有流、模拟 HTTP 与临时测试密钥。原 OMS 成交仍为空、恢复记录仍 pending；生成计划不等于受控执行进程已投影。venue_cash_reconciliation=UNKNOWN、cash_reconciled=false、source_verified=false。未部署、未实际读账户或发单，未完成完整 canonical。

剩余验收包括黄金实际 OMS 投影/重启一致性、后续平仓与期初连续性、费用和资金费/外部流入流出现金核对、反馈到研究、黄金运行部署和实际 Demo 端到端。不得把合成日志内平衡当作实际账户现金守恒或经济资格。
