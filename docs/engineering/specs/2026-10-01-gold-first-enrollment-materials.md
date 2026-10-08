# 黄金首次登记：独立提案与原安装材料组装

承接[首次只读审查](2026-10-01-gold-first-enrollment-review.md)。旧操作员入口将“没有目标持仓 context”等同于“注册表为空”，因此完整保留已过期 BTC 登记的黄金首用流程会被拒绝。新增的显式首次黄金提案接受原受保护 worker 的审查报告；普通首用和同市场续接仍遵循原条件。

`tools.demo_operator_setup.propose(first_gold_review=...)` 要求 XAUUSDT、目标 context 为 None、旧登记全部已到期，沿原 `validate_first_gold_review` 重验账户、源码、完整 registry、候选切分点、旧风险、独立 USDT 钱包及观察时效。原完整报告进入黄金 spec 并受 spec 摘要绑定；不清空旧 registry，不折算 collateral，不伪造黄金零账本。spec 有界为 65536 字节，批准、下单和经济权威仍为 false。

该范围的确认用途独立为 `DEMO_FIRST_GOLD_FUNCTIONAL_TEST_ONLY`。`assemble` 要求新的匹配确认、确认时间不早于首次审查，并重新消费当前账户审查。新报告的时间和观察摘要可以变化，但安装记录、原 candidate、旧 state/context 摘要、风险值、两币种余额及 false 权限必须与已审查范围完全相同；不能利用新观察更换切分点、重设损失/容量或转换旧批准。缺报告、过期、旧批准用途、早期确认、变动风险或替代钱包均拒绝。

通过这些条件后继续使用原 assembler、原 native risk/mandate、原停机历史校验、原 key/registry 扩展和 `demo_operating_bundle.validate_materials`。原 BTC key 前缀不变；新黄金 profile 的初始权益来自独立 USDT，原 BTC 现金与风险保留在原 book，并由分区运行门再次核验。任何签发或执行仍须原风险、独立签名和保护角色边界。

原 successor CLI 增加 `--prepare-first-gold-enrollment`，通过同一源码/cycle 绑定的受控 context 管道获取审查及其原观察，输出 `UNSIGNED_FIRST_GOLD_PROPOSAL`。入口仍核验 root、原 BTC spec/result 和没有 pending；不能混用 enroll、run-cycle、symbol、迁移、组合或研究身份参数。不调用确认、生成密钥、安装配置、启停服务或下单。原 `--review-first-gold-enrollment` 的返回合同保持。

隔离回归使用真实原账本、SQLite、V2、组装器、风控、签发和签名核验。宿主身份、属主、源码接受记录及操作员确认仅为明确的测试替身；使用测试新生成的密钥及合成账户/行情，不读取真实凭据、账户接口或发送订单。首次缺接口回归和夹具时钟/分钟线修正记录留存，最终检查结果记入同名开发输出目录及 PROJECT_STATE。

本增量是登记材料交接，实际人控首次安装及其部分安装恢复仍未接入。固定范围仍为一小时、最多三笔和累计 750 USDT，不能满足[持续 pilot](2026-09-29-gold-demo-continuous-pilot.md)所需的独立新 24 小时范围/shared-budget 批准，不能续期或转换旧 BTC 权限。现场安装、连续运行、现金反馈、跨版本交接及同版本完整门仍分别待验；不构成经济准入、盈利或整厂交付结论。策略研发未新增。
