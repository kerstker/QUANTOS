# 黄金风险提案到持久化意图及模拟发送

## 实现

demo_batch_risk.screen 显式选择黄金精确批次时，复用同一 PreTradeRiskGate、
微单位转换、单侧独立填充检查、精确名义金额限制、换手和原风险历史。
黄金风险结果/输入快照使用 DEMO_GOLD_EXACT_BATCH_RISK_V1，
版本进入 risk_sha256；默认BTC协议不接受黄金。

demo_pre_submit 从版本化账户观察选择批次协议，原观察模型继续核对其品种、
合约和账户身份。订单摘要、风险检查与冻结提案一致；原策略批准、validation
attestation、mandate、kill-switch、账户和行情绑定均保持。
demo_prepared_execution 发送前复查从原授权信封取协议重算同一风险快照，
不使用研究状态推定批准。原表/状态机负责持久化意图、预留和未知恢复。

## 实测范围

新增合法黄金集成回归修改前失败。八项黄金用例验证：

- 临时测试密钥签名的冻结提案，经过真实风险/mandate/签名检查，写入原派发、
  recovery及OMS意图，再经过原执行协调器取得一次模拟ACK。
- ACK不产生OMS成交；再次execute拒绝重复发送。
- 策略未批准、旧BTC mandate、缺validation、风险超限、账户错配在意图写入前拒绝。
- 凭证fixture返回后改变日损益，final_check拒绝HTTP；原预留与未知意图保持，
  回传QUERY_RECOVERY_REQUIRED，再调用不会重新读取凭证或发送。
- 黄金风险必须显式选择协议，其版本纳入输入摘要。

八组联合116 passed：gold_demo_preparation、demo_batch_risk、demo_pre_submit、
demo_prepared_execution、demo_position_context、demo_issuer、
gold_demo_exact_transport、gold_demo_dispatch。新测试完整Ruff，
三个实现E9/F821通过，治理698文件零违规。

全部使用合成账户、批准fixture、临时Ed25519密钥和模拟HTTP；部署目录custody
在测试中被替换，其他原门实际执行。60000价格等是fixture数值，不是黄金现价。
未读真实凭证、未生成真实批准、未修改已安装策略、未部署或实际下单。

## 剩余交接

这证明受控提案和执行骨架的黄金联通，不是策略资格或生产完整闭环。
独立issuer policy/replay/store仍需适配黄金协议；研究候选到黄金执行意图、
运行期请求配置和系统成交到原现金账本的完整链路尚未完成。
完整canonical、真实Demo验收及获准生产运行均未完成。
