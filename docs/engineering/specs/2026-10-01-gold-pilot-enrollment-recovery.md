# 黄金持续 pilot：续完原期初登记

承接[24小时/5%持续控制](2026-09-29-gold-demo-continuous-pilot.md)。原 executor 先写 configuration 再写 enrollment receipt；进程在两者之间退出后，既有目录只会被拒绝。当前增量保留原保护请求和 preparation lease，增加完整原 intent 的持久化及缺失写入恢复，使原期初可以在进程重启后续完。

新登记先验证原账户观察、原生执行存储和已安装分区，再将完整 prepared 材料、原 source commit、原 request 摘要排他保存为 `GOLD_DEMO_PILOT_ENROLLMENT_INTENT_V1`。其中保存原 opening origin、权益、开始/结束时刻及完整执行状态摘要；file/parent fsync 完成后才写 configuration，最后写绑定 intent 摘要的 V2 receipt。原 source_authenticated/installed/submission_enabled 准备标记仍为 false，控制记录不生成交易权限。

有完整 intent 且缺收据时，当前 worker 在原排他租约内重新验证原 intent 和当前新鲜空仓观察：独立 USDT 钱包及完整执行状态摘要必须保持，原 request/source/account 不变，原 24 小时尚未结束。当前观察的时间可以前进，configuration 必须沿用原权益、opening origin 和起止时刻；不将当前 prepare 产生的新时刻写成新基准。原分区继续使用同一 V2 和完整 BTC 前缀，不折算 USDC、不重设旧损失/峰值/容量，也不创造黄金历史。

所有既有 configuration/receipt 在新审查或写入之前按 canonical digest 精确核验，区分整数与浮点数、false 与数值零。只排他创建缺失文件，已有不同内容或损坏/截断材料保持失败；空目录、缺完整 intent 的旧部分登记不能推测恢复。恢复审查按内容摘要追加留存，逐份核验已有历史，最多 32 份，达到资源界限保持拒绝。完成的 V1 configuration/receipt 仍只读兼容，不自动生成 intent 或重置基准。

原 `pilot.observe` 共用完成记录核验，V2 必须同时具备原 intent/request/configuration/receipt；删除 request、改写摘要或权限类型会拒绝。恢复后仍由原持久 stop 控制按原 5%界限与原结束时刻停止；已完成原记录可以在到期后被读取以记录结束，不会重新计时。基础记录读取默认仍为 32768 字节；只有完整 enrollment intent 明确使用 16000000 字节上界，容纳原已有 8000000 字节账户观察及包装材料，不放宽其他记录的默认大小。

原两项配置/收据缺失回归失败并留存；新增真实子进程退出与重启验证使用原 SQLite、排他 lease、receipt 和 V2，退出码 73 模拟收据落盘前的进程终止。下一进程仅续完原控制登记，原 4200 USDT 基准、24小时起点、4999.6801 USDC 旧钱包和原容量保持。身份、属主和源码接受检查是明确的隔离替身；测试使用合成观察，未访问真实账户接口、凭据或订单。数值类型替代和缺收据字段的独立失败记录也保留，最终相关检查与冻结版本写入 PROJECT_STATE。

此增量完善控制登记的中断恢复。首次黄金独立权限安装、24小时 shared-budget 批准、真实持续运行、资金反馈、多资产及整版本交付验收仍分别待完成；旧格式缺材料、物理截断与跨版本恢复不自动修复。记录摘要体现技术完整性，不构成独立签名来源或经济权威。未现场安装、部署或切换服务，策略研发未新增。
