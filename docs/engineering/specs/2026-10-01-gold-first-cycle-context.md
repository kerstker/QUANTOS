# 首次黄金周期消费原保护分区

承接[首次登记材料](2026-10-01-gold-first-enrollment-materials.md)与[pilot登记恢复](2026-10-01-gold-pilot-enrollment-recovery.md)。原bar和absorption周期按全账户存在reservation判断黄金context必然存在；保留已结算BTC前缀、尚无黄金历史时，原load_context按设计拒绝 `PARTITION_TARGET_HISTORY_ABSENT`，正常开仓草案和NO_SIGNAL均在周期入口终止。

两个周期改用原 `demo_market_partition_runtime.resolve_context`。安装记录、同源activation、旧登记前缀、新黄金独立key、全账户观察及原V2前缀均先复核；合法的首次黄金context保持None。只有存在黄金context时才调用原持仓观察校验；否则从当前已验证空仓观察读取独立USDT权益。完整旧执行状态摘要和 `execution_history_empty=false` 保持，不制造黄金context，不删除BTC记录，不改变旧损失、峰值和容量，也不折算抵押资产。

周期结果仍仅为原草案/NO_SIGNAL/停机或结构化拒绝。条形策略继续要求已有pilot；原持仓周期、exact constraints、独立审批、risk/mandate/issuer及实际提交边界继续使用原路径。没有新增策略、签名、key、调参或自动安装动作；摘要不构成经济权威。不存在合法安装时，原未分区路径仍按原账本重建，混合历史不能绕过保护解析。

隔离回归使用真实SQLite、原收据重建和V2账本保留的已结算BTC前缀，配合明确合成的黄金观察、既有策略定义和独立key。原五个BUY/SELL/NO_SIGNAL周期均复现拒绝；修复后核验原4999.6801 USDC钱包、5000原权益与239.8容量以及完整账本不变。缺activation/新key、source变更、旧前缀或双钱包变化、未拥有持仓及过期观察须在pilot与草案前拒绝。已有聚焦bar周期夹具增加本地保护记录路径隔离，不访问宿主保护目录；原原生持仓roundtrip另行验证。

此增量修复首次黄金运行消费者。实际首次黄金独立权限安装、24小时shared-budget批准、真实持续运行和资金反馈、多资产及同版完整交付验收仍待完成。局部绿色检查不替代canonical ALL，未部署或改动现场服务。
