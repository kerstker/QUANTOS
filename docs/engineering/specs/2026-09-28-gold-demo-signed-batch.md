# 黄金 Demo 精确批次签名协议

## 已实现范围

复用 services/validation/demo_submission.py 的原精确Decimal订单检查、上下文
摘要、部署公钥登记、Ed25519签名、有效期和吊销检查。
新增显式黄金批次/信封/授权正文/核验结果版本：

- DEMO_GOLD_EXACT_ORDER_BATCH_V1
- DEMO_GOLD_SIGNED_SUBMISSION_V1
- DEMO_GOLD_SUBMISSION_GRANT_V1
- DEMO_GOLD_VERIFIED_SIGNATURE_V1

黄金签名域为 application/vnd.quantos.demo-gold-submission-grant.v1+json。
批次与订单校验须显式选择黄金 schema，仅接受 XAUUSDT。默认仍是原 BTC
版本，旧签名字节和批次摘要不变。核验从严格验证的信封选择协议，正文版本
必须匹配；完整原上下文维度仍逐项绑定。函数没有 signer 或派发能力。

## 消费者兼容性

追查发现，仅扩展纯签名核验会让原 DemoDispatchStore.reserve 接受黄金授权，
但其旧批次重放器无法再次打开记录。新增测试真实复现该问题。
现在 reserve 在事务写入前按当前日志支持的批次格式检查订单与摘要，
和读取验证保持一致；未升级的日志拒绝黄金且不消耗授权或写记录。
这不是黄金派发完成。后续必须共同迁移日志、线协议、恢复查询和独立
issuer policy，不能只让一次签名核验通过。

## 验证

黄金新增19项先因缺失协议接口失败；加上跨签名域重放、有效旧签名的黄金
拒绝、旧字节兼容、派发持久化边界，共23项。
八组联合134 passed：gold_demo_submission_verification、demo_submission_verification、
demo_dispatch_store、demo_authority_registry、demo_issuer（live和tools）、
demo_prepared_execution、gold_demo_market_inputs。
测试使用临时生成Ed25519密钥、隔离SQLite和合成进程身份；无真实密钥、
无部署状态或信任根修改、无网络下单。
新测试完整Ruff、两处实现E9/F821通过，治理698文件零违规。

完整工厂目标仍未完成；本增量只提供可独立核验的黄金签名协议。
未部署、未运行canonical全量、未签发真实批准。黄金策略/分配、受控提案、
独立签发策略、派发与终态恢复尚未完成联合接入。
