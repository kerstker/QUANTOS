# 黄金私有成交回执与原意图绑定

## 断点与实现

实际集成回归先出现一失败四通过：黄金签名意图经原 prepare/execute 和模拟 ACK 留存后，私有成交编号匹配，但 normalize_order_receipts 的 BTC 限制导致 RECEIPT_INTENT_OR_CLOCK_CONFLICT。

新增 BINANCE_USDM_GOLD_ORDER_RECEIPTS_V2/V3，分别沿用原固定订单选项与回溯时钟区间语义。黄金版本仅允许 XAUUSDT，旧版本仍只允许 BTCUSDT/BTCUSDC。绑定器从原派发预留的签名信封选择协议，而不是凭任意输入标签扩展市场。

账户、原订单 client/venue ID、发送标记、选项、数量、消息摘要和缺口检查保持。黄金 V3 保留交易所时间和接收时间，不声称精确延迟或因果认证。费用使用精确字符串，source_verified=false、actual_paid_fees=null 保留。

## 验证

五组测试 89 passed：黄金绑定与黄金原始回执、旧绑定、BTC 原始回执及区间时钟。覆盖合法成交、错 venue ID、错合约、超量成交、流缺口、跨协议重标、选项缺失、错 client 和时钟缺失。使用临时签名密钥、合成私有流及模拟 HTTP；原派发日志不变，OMS 无成交，恢复记录仍待处理。

## 下游未完成

当前 demo_execution_journal.prepare、passive_receipt_calibration 的观察轨迹合同及 QueryPlan 仍有 BTC 范围限制。黄金私有回执绑定成功不代表 import_reserved_receipts、OMS 终态、现金终态或自动反馈已通过。后续需共同版本化这些消费者并验证原账本，不能仅扩大 Literal 或使用 BTC 实验替代。未部署、未接触实际凭证或账户，未运行完整 canonical。
