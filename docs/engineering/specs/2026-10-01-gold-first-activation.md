# 首次黄金人控安装与同一批准的中断恢复

承接[首次登记材料](2026-10-01-gold-first-enrollment-materials.md)、[首次周期上下文](2026-10-01-gold-first-cycle-context.md)及[十环节交付目标](../plans/2026-09-28-ai-factory-closed-loop.md)。首次提案与原组装器已有接线，但原 successor 的普通续接仍要求同市场历史；黄金尚无 context 而全账户保留 BTC 历史时，不能据此执行实际首次安装。公钥发布后原首次登记审查也会正确拒绝非 legacy 注册表，原来没有该阶段的专用恢复复核。

新增 `demo_successor_setup --enroll-first-gold`，只能由本机 sudo/TTY 人员进入原 operator gate。它与其他提案、登记、运行和迁移选项互斥，不调用策略周期或启动服务。首次明确确认的用途仍为 `DEMO_FIRST_GOLD_FUNCTIONAL_TEST_ONLY`：一小时、最多三笔、累计750 USDT，以及原逐单/数量、risk、mandate、stop 与签发限制。此授权不能代替24小时 shared-budget policy，不能扩展已过期 BTC 权限。

原 controller 排他维护租约与 cycle inode 锁包围全过程。在任何实际配置写入前，完整原 spec、确认、legacy 注册表、原组装时刻/观察/审查、同一合成或人控私钥及原 stop image 均排他落盘/fsync，之后持久化绑定全部 seed 和原 manifest 的 intent。续接通过原组装器在原时刻重建字节，仅用于完整性校验；当前原审批期限与同一 operator 仍须有效。不生成第二次确认、密钥、基准或授权窗口。

新只读 activation 审查沿原 private worker 管道、preparation lease、原 Native SQLite 和完整 V2 读取。仅接受原 legacy 注册表或精确追加一个新黄金 key 的 staged 注册表；新 key 不得早于原分区安装。原 cut、全账户执行摘要、BTC 钱包/损失/容量、USDT 空仓观察及 None 黄金 context 保持。报告明确 `configuration_activation_verified=false`，不是已安装证明；approval/source authority/submission/economic 字段均 false。同一 UTC 日原 context 与 day checkpoint 必须精确不变；跨日须从原完整账本重建，不重置风险。

完整 seed 可以补齐缺失的 bundle 文件，并复核所有已存在字节；截断、篡改、部分 seed 无 intent、冲突配置、新交易历史、过期确认、source/account/钱包/stop 变化均拒绝，不删除旧材料。复核历史追加留存并校验摘要及原绑定，最多32份。配置替换、公钥发布、active pointer 与 activation marker 继续复用原 `install_successor`，保留原 `previous-files`；不改 issuer allowance、OMS、执行历史或 kill-switch。已保留 pending 的恢复失败保持安装状态未知，不误报没有安装。完成后重复调用仅验证原完整材料与实际配置，明确不声称授权当前仍有效。

隔离验证使用真实原 SQLite/V2、fsync/目录锁、材料编译器及独立签发器；source/UID/custody 和本机操作员为明确替身，账户观察与密钥均为合成。真实新 Python operator 在公钥 durable publication 后 exit73；另一新进程复用原 seed、窗口、密钥和 worker framing 恢复，未清零或重放原账本。另核验原安装材料可以进入原风险检查、unsigned proposal、独立签发及签名校验，不调用 dispatch。

新原生接口先复现两项缺失失败；联合检查另发现新 key 可早于分区安装、同日旧 context/day checkpoint 可变及部分恢复误报 false，原失败日志均保留后逐项修复。跨进程恢复夹具首次未传递恢复时钟，导致 worker 正确拒绝未来观察；只修复夹具时钟传递，原时效门保持。最后11组176通过/184.15秒，覆盖上述新路径和原首次材料、提案、只读复核、cycle checkpoint、successor 安装/续接与保护分区运行合同。此数字是增量证据，不是完整 canonical PASS。

实际首次黄金安装、24小时共享预算批准、现场持续运行/监控恢复、现金反馈再研究、现场安装升级回滚与多资产验收仍待完成。完整验收必须绑定本增量自己的冻结版本；祖先完整 PASS 和隔离组件测试均不能替代。未部署、切换服务、读取实际凭据、下单或新增策略研发。
