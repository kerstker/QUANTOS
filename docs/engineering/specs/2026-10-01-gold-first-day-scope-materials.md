# 首次黄金24小时范围材料与原bundle校验

承接[完整十环节目标](../plans/2026-09-28-ai-factory-closed-loop.md)和[未批准预算准备](2026-10-01-gold-first-day-budget-preparation.md)。本增量解决首次范围材料没有独立类型的问题；后续仍须接回原本机操作员、安装和只读观察流程，再由真实决策产生精确policy。全系统、多资产、现场运行与独立经济准入目标保持，不计整厂完成。

原bar周期需要pilot配置，原pilot安装需要已绑定精确决策的策略policy。这一启动依赖不能用虚构constraints摘要、假pilot或延长旧授权解除。新增`DEMO_GOLD_STRATEGY_BUDGET_SCOPE_V1`为独立的不可执行材料，签名有专用域；没有execution_constraints。原`validate_policy/current_key/review_submission`继续拒绝它，原issuer在拒绝前不预占任何grant。

`tools.gold_strategy_budget_materials.assemble`只有纯材料装配接口，没有CLI、采集、安装或服务入口，也不认证人类。调用者未来须沿原sudo/TTY与保护目录工作流提供真实独立操作员记录。记录明确批准86400秒试运行与300秒安装余量、原request及cycle摘要、用途、操作员和原时刻；记录不批准任何订单、不声明经济权威。旧所有键须已到期，另一个Ed25519键追加登记，旧登记逐份保留，拒绝复用旧公钥。旧一小时期限不改写；新的范围有效期固定为批准时刻起86700秒，不在重启时续期。

300秒余量必须被显式人控确认，不能从原未批准request推导为已有授权。`pilot_window`只审查拟起止，不落基准：开始须不晚于批准后300秒，连续时长仍86400秒，结束不超过原范围截止；过期启动拒绝。后续实际安装还必须重新审查原执行状态与当前观察，单独保留真实起点，不能拿此纯时间审查替代安装或持续运行证据。

原预算、scope、grant TTL及native limits逐份复制，保留更严格数量、金额、频率、日亏损、回撤、现金、杠杆与容量门。新mandate只改变独立操作员、身份和获批窗口，原金额与品种限制不扩大。人控functional attestation只用于此有限Demo范围，原独立经济来源未接入的拒绝不变。无黄金历史时保留原profile损失、峰值和换手基线；有历史时用原context及当前账户重建风险，旧BTC完整前缀、USDC钱包和USDT钱包不相加或折算。

`DEMO_GOLD_BUDGET_SCOPE_BUNDLE_V1`沿原`demo_operating_bundle`消费，tasks必须为空，文件集合固定。另存原request、操作员记录、原policy/profile/origin，用原预算prepare重算完整引用，核对新profile、范围和签名；私钥与登记公钥继续由原检查核对。原SQLite停机历史只读核验，签名同时绑定原停机image精确摘要。JSON和停机image使用原reader大小界限，不返回原reader无法读取的材料。

保存后的材料复核使用签名中的原确认时刻验证30秒request证据，当前范围键仍须有效；不重生成request、key、批准、窗口或风险基线。这是保留材料校验，不是当前账本审查。原loader的保护目录/属主/无符号链接及前镜像门保持；实际Scope安装、观察端接线、pilot安装和首个真实精确决策policy仍未完成。

行为回归先保留接口缺失失败，再覆盖旧公钥复用、签名/原账户/范围/操作员/停止image替换和有界安装余量。原生黄金历史沿隔离签名、mock HTTP、原查询/私有回执、OMS和V2重建，不表示现场成交。所有操作员、账户、密钥及身份/属主代替均为明确隔离夹具；未访问实际凭据或修改保护运行状态。专项和最终冻结身份见[PROJECT_STATE](../../PROJECT_STATE.md)，本版完整验收不能继承祖先PASS。

最终同版七组联合162项通过/245.55秒，包括新材料、原operating bundle、原operator setup、首次黄金材料、原budget review/prepare和原共享issuer预算。初始三接口失败、后续时效/旧公钥失败、夹具误用已替换键/未创建SQLite的失败均保留。SQLite尾页替换在逻辑历史仍有效时先复现未拒绝；签名绑定精确image后纳入最终联合通过。Ruff E9/F821、20域52边无环图、相对链接及源码卫生通过；这些是增量证据，不是本版完整canonical、现场运行或经济准入。
