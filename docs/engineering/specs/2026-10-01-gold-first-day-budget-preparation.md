# 首次黄金24小时预算范围准备

承接[完整十环节目标](../plans/2026-09-28-ai-factory-closed-loop.md)、[持续pilot](2026-09-29-gold-demo-continuous-pilot.md)、[首次黄金安装](2026-10-01-gold-first-activation.md)与[精确批准消费](2026-10-01-gold-bar-approved-dispatch.md)。本增量准备首次24小时独立预算范围，不新增策略、不安装权限、不运行pilot或服务。整个系统仍未交付。

原`resolve_context`为执行消费者要求当前有效的安装后签发键。一小时功能授权过期后，不能借用它完成后续范围审查，也不能为准备材料把旧键续期。新增`gold_budget_scope_review`只用于原executor的诊断读取：保护分区、activation、执行源码、登记前缀、完整V2状态及新鲜全账户观察继续校验；只有该只读路径不要求当前有效键。原执行、issuer与签名路径继续要求有效权限。

复核要求已有安装后登记键及空仓。旧BTC账本、4999.6801 USDC钱包、5000期初权益、239.8已用容量及原损失保留，USDT不与USDC相加或折算。没有黄金历史时，完整状态必须仍等于原切分前缀，黄金context与risk保持None；有黄金历史时，原分区重放器产生完整黄金context，数量必须为零，钱包、期初、日损益、峰值、换手、容量及可选日checkpoint原值进入报告，不重置黄金基准。报告只具技术诊断意义，source_verified、authorization_current_verified、budget_enrolled、approval_granted、submission_enabled、economic_authority均为false。

原functional worker的`context`操作接受一个固定的`gold_budget_scope_origin_sha256`字段。原私有进程/源码/cycle身份、固定XAUUSDT观察目录、preparation lease和原停机库前后核验继续使用；没有caller路径、钱包或风险参数。已有cycle-scope不得指向其他品种，尚未运行周期时不补造scope文件。响应沿原65000字节预算和原结果留存，registry、观察、停机或账本变化则拒绝。

`tools.gold_strategy_budget_enrollment`沿原只读historical policy配置读取已有黄金功能授权，不接受BTC改名或已有shared-budget重建。固定runtime只接受原retained/fresh/auto三种bar输入；retained复用原loader，fresh/auto读取固定trial/source目录和原PreparedBarDefinition，不捕获新bar或创建rotation。原manifest、源码、依赖、语义文件、definition及归档摘要均核验，源文件/sidecar变化拒绝。准备前后复读配置、profile、原active enrollment和definition，原私有worker通道始终关闭。

输出`GOLD_DEMO_BUDGET_SCOPE_REQUEST_V1`为未批准材料：明确请求86400秒和5%pilot损失门，复制当前3 grants/3 orders/750 USDT及原每笔250、数量0.01等限制。原native limits逐字段保留，仍包含更严格的日亏损、回撤、现金、杠杆、容量与频率门；5%不替代它们。现有策略定义数量若超过原policy或更严格native数量门，或者不满足场所数量/金额规则，明确拒绝，不自动缩量或放宽风险。原期限只作为历史绑定保存，请求的材料时效仍最多30秒，不签发24小时有效期。

该请求没有execution_constraints占位摘要、policy、签发键、mandate、订单任务或pilot基准。后续仍须独立操作员批准新范围及新mandate，以真实合格决策产生精确policy，再满足原独立批准与人控安装。原pilot安装器要求至少24小时剩余授权，实际安装时间余量尚须单独设计和批准；不能把此准备命令算作已批准、已安装或24小时持续运行。独立经济来源未配置的拒绝边界不变。

行为回归先复现两个缺失入口失败，日志保留。数量0.01在原隔离观察的60000.1报价下超过250金额门，初版正确拒绝；仅将新测试中的独立合成归档假设设为0.002，未改变生产策略。另有过期观察、registry/cut/旧risk/钱包/源码替换、bool/数值零类型混淆、已有合法共享组及更严格已绑定限额拒绝测试。

固定归档→原worker的联调使用真实归档检查、子进程framing、SQLite、preparation lease与原V2重放；安装身份、接受源码、属主及账户观察为明确隔离替身。黄金新终态案例通过合成签名、mock HTTP、原查询/私有回执→OMS处理产生实际隔离黄金历史，再沿完整旧BTC前缀重放，黄金context不再被误当None。无实际账户、凭据、订单或现场服务操作。测试夹具目录权限、负trade id、journal关闭与store参数错误的失败日志保留，修正夹具后复跑；生产门未为夹具放宽。

本批专项、冻结提交与验收队列身份见[PROJECT_STATE](../../PROJECT_STATE.md)。祖先9f34f945的32/32完整PASS只属于该版本；本增量仍须自身适用验收。首次实际黄金安装、24小时共享范围批准、持续运行/监控恢复、真实成交与现金反馈、升级回滚和多资产验收仍在完整交付主线。
