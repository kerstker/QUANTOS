# 首次黄金Scope到原24小时pilot的交接

承接[Scope人控安装与观察](2026-10-01-gold-first-day-scope-installation.md)及[完整十环节目标](../plans/2026-09-28-ai-factory-closed-loop.md)。本增量补齐已安装、专用签名的非执行范围到原pilot材料、原人控安装器和执行侧实际期初登记。没有实际安装、启动、账户操作或订单；整厂交付仍未完成。

## 原材料和安装器

`tools.gold_scope_pilot_materials`只读原配置与两侧公钥登记，在原排他维护租约下生成固定两份材料和review。原renderer的服务字节与隔离保持；新请求V2保留完整签名Scope及公钥快照，限制32768字节。review包含Scope和登记摘要，仍为PREPARED_NOT_APPLIED；没有策略准入、grant、签名key、基准或自动启动。

原`tools.gold_pilot_install`按材料schema选择原只读Scope配置消费者，重新核对原renderer的每一个字节、Scope签名/登记、source/account和签名内runtime摘要。原sudo/TTY、独立明确确认、排他维护租约、无既有pilot/请求/服务、不可覆盖写入与fsync/readback保持。Scope仅允许原确认时刻后的300秒内安装和开始登记，结束不得超过原86700秒范围；旧执行policy安装仍要求至少24小时剩余期限。安装和启动分别授权，不创建精确订单批准。

## 实际期初与恢复

原`demo_gold_pilot_enrollment`接受明确V2请求。首次登记须现场原公钥登记与留存快照一致、当前Scope有效且在原300秒内；仍由执行worker捕获新鲜账户事实，通过原pending恢复、preparation lease、保护分区与唯一V2审查。真实钱包、完整执行状态、原开始/结束时刻先绑定原完整intent，再写原configuration和receipt，不接收用户给出的期初权益。

fresh/auto bar worker先校验原白名单runtime，然后在首次长bar采集前沿原账户观察路径登记Scope期初，避免300秒采集先耗尽300秒启动余量。后续原周期再次调用时只核验已完成记录，不重置起点。原V1请求与原控制记录schema保持兼容。

部分写入可在300秒启动截止后续完原intent，但仍须在原pilot结束前、当前Scope及现场公钥登记有效、钱包/完整状态一致；不得续期或换基准。完整记录在到期或公钥变化后仅作历史完整性核验，使原到期/亏损停机可留存；这不是当前交易许可。原policy/current_key与issuer继续拒绝Scope，实际订单仍需独立精确决策批准与原风险门。

## 验证与剩余交付

缺renderer/native消费、缺材料接口及长bar采集前无期初的失败先复现并保留。新Scope、原worker/bar worker、原pilot安装/登记/恢复、renderer、pilot supervisor及tick九组联合132通过、94.99秒；追加部分登记后公钥吊销拒绝单独记录。数量重叠，不累加为完整门证据。

新边界包含超时新建拒绝、签名修改和公钥吊销拒绝、中断后保留原起点/期初/期限、到期后停机、不允许重复安装重置以及重新计算hash仍拒绝服务注入/runtime替换。原安装器不可覆盖写入、fsync、readback到原native账本登记已在同一隔离场景贯通；原BTC账本历史和独立USDT钱包不变。来源接受、操作员、UID/custody及账户事实为明确隔离替身，key合成，无现场证据。

本增量在独立clone开发，父3ba92acc7f9d982199059b928dee00e5e6090d71；提交、hook、DGX预检、逐文件父字节匹配/备份/主区整合及本版完整门状态见PROJECT_STATE和本机任务日志。最新专项绿色不继承前版canonical PASS。

仍需实际Scope/pilot人控安装、首个真实精确决策policy交接、持续24小时及真实成交/费用/资金费/现金反馈。独立经济来源、黄金监控恢复、安装升级回滚和Kraken xStocks/XAUT多资产验收也未完成；不新增策略研发，不部署、切服务、修改保护状态或触碰真实凭据。
