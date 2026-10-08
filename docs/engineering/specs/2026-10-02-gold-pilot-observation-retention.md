# 黄金 pilot 原窗口内的决策留存与独立委托登记

承接[全天基础](2026-10-02-gold-pilot-delivery-foundation.md)和[十环节交付目标](../plans/2026-09-28-ai-factory-closed-loop.md)。父源码为6431f87b9a7fbe6461090cb189a40e587b50897f，开发目录为`/home/kerstker/quantos-dev-output/gold-pilot-retention-20261002-62qtd5px/source`。本记录不代表实际24小时运行、经济准入、委托执行或最终交付。

## 原窗口与完整原件

原worker/controller使用的1000条/64MiB决策目录不足以支撑默认10秒采样的一天。新的GOLD_PILOT_OBSERVATION_RETENTION_V1绑定原request、已完成configuration、源码、账户和基础runtime。三种原生bar输入均复用原enrollment校验，AUTO仅允许原episode身份沿已有轮换变化。已有活跃旧目录必须先独立审查，不自动迁移、清空或重新计算期初。

持久claim约束最短1000ms采样间隔，窗口使用原started_at_ms、ends_at_ms及已有900秒只读收尾。87,302是保守存储槽数；严格一秒间隔、右端开区间内至多87,300帧。短会话和进程重启共享原claim历史。时钟回拨、未完成上一帧、身份变化或窗口结束不能得到新额度。收尾不延长授权或信号时限。

SQLite原件、独立audit、marker与head分别核对；事务累计计数及ordinal索引避免每条记录重新扫描历史求和。写入持有排他租约，只读持有共享租约和稳定读事务。数据库、页/索引、journal、审计文件及目录开销分别有有限预算。单条完整原件保留16,000,000字节上限，总payload上限4GiB；不截断V1恢复报告，不宣称任意合法大结果均能覆盖全天。

默认冷开审查全部元数据及独立见证；payload完整hash在读取具体原件时核验。精确只读模式只证明当前binding/schema/head及所选payload、claim和对应audit，不能当全历史审计。worker冷审与claim在新行情捕获前；controller在启动worker前冷审已有历史。结果输出和独立审批回调之前释放存储租约，避免短信号期限内做整库冷审或阻塞独立消费者。

## 实际调用链

worker按同一claim保留完整账户origin、decision和result，每帧最多三份；controller每帧最多保留result及一个approval或delivery附件。维持原内容与摘要；controller必须从固定executor目录、按登记UID读取一致原件后采用同一claim。审批请求和反馈交付凭据附着于该帧，不再各自遇到旧决策目录的1000条门。审批review、待批Scope、显式/定向/未指定结果的dispatch均使用新读端并复读原件；未指定结果的查找在材料化前再次核验唯一性。原风险、任务claim、issuer和防重发门保持。

controller中断后可以从执行端补齐已经完整保存的原result，采用原claim，不重采行情、不重放批准或订单。执行端缺少result的半帧继续拒绝新采样；SQLite/audit/head部分写入保留供审查，不自动删除或重新建库。已有controller见证但executor整套留存丢失时，在启动新worker前拒绝。双方尚无历史的首次controller初始化延后到执行端已有原件，避免制造空controller孤儿。

交叉审查补充拒绝三类缺陷：伪造original_pilot_window的request或起止时间；已有native历史回退非bar旧runtime；无效retained归档提前触发pilot登记。retained输入先验证原归档再登记/claim；fresh输入仍先登记再等待新bar。

账户采样现复用原16 GET隔离采集、115秒子进程期限及完整账户验证，经固定账户内部接口写入同一claim。已持久的成功或失败原件只读复用，不更新观察时间，不再按帧追加旧1000文件/64MiB目录。首次期初登记仍使用原入口及原限额；旧目录原件和latest.json保持原样。新路径验证固定root、账户、UID、源码及原持久claim，仍持有原origin-capture.lock。

GET结束而原件尚未提交时中断，真实worker重启因上一帧不完整拒绝新claim；本接口没有独立capture-start见证，不宣称网络exactly-once，也不自动重新采样或清理半帧。账户采集失败原件占用原槽并导致该帧终止。

8,732帧实测覆盖默认10秒全天加原收尾，共26,196条origin/decision/result，账户原件每份7,254字节。完整payload为81,775,180字节，SQLite为113,524,736字节，独立audit共34,928份；实际磁盘分配260,624,384字节（约249MiB）。写入约243.44秒，冷启动全元数据审查约1.06秒，精确origin/result读取约2.03毫秒。此为分包记录的原生落盘负载，子进程传输为合成输入，不是实际网络、撮合或24小时驻留证明。4GiB仅为payload上限，SQLite、journal、audit及目录预算另计；不保证任意合法大报告均能覆盖全天。

精确结果读端无需全库payload扫描；未指定结果的查找仍需扫描并验证相关结果，不能保证最大历史下全部在五秒内完成，原期限仍拒绝迟到材料。恢复、reservation、order journal和dispatch wire等条件性原限额继续保留；持续恢复异常可能提前触及限额，默认supervisor转RECOVERY_REQUIRED。它们未被增加或解释为全天真实交易容量已经通过。

## 有限委托登记

新增只读原件审查和独立用途的本机sudo/TTY登记。材料限定在固定公开材料目录，核验专用签名、操作员材料、实际enrollment、runtime、策略定义、profile、既有setup/cycle、原issuer RESERVED消耗及唯一账本上下文。原context helper禁写day checkpoint，并要求原cycle目录已存在，防止审查顺带初始化目录。

仅接受当前相同EXIT_BUDGET策略及group；旧Scope、reserve、mandate或group改变保持CONTINUITY_REQUIRED，不能建立新组清空历史。人工确认前后、最终写入前后复核原件；使用原排他维护租约及不可覆盖/fsync/readback存证。登记明确consumer_enabled=false，不创建grant/task、不签发逐信号权限。测试中的执行context子进程传输是合成替身，不宣称现场原生context重放或委托消费者完成。

## 验证进度与剩余范围

保留缺失接口、重复加锁、窗口替换、跨目录丢失、旧runtime回退、无效retained提前登记及缺账户同claim接口的失败日志。首轮33组联合为527通过/18失败：旧测试未映射固定executor目录或仍只准备旧留存副本。随后只修改相应隔离夹具，补真实native持久claim/decision/result、原签名窗口、固定临时目录和正确0600权限，原人工/TTL/租约/异常退出断言保留，不放宽生产门。

最终同源码38组联合605项通过（627.517秒），0失败/错误；命令、冻结前像及XML位于独立输出根joint-final-command.json、joint-final-preimages.json和logs/joint-final.xml。分包数字与此重叠，不累加。实际worker管道和controller留存/回调已联调，完整worker测试中的账户采集仍为明确替身；真实capture_for_pilot原父校验器、合成子进程响应及实际存储在独立原生测试中验证，不能把这两种证据合称真实网络端到端运行。独立只读复核未发现新增阻断。

实际pre-commit通过：仓库卫生与728文件治理扫描0违规。24个变更Python文件E9/F821通过，10个生产文件追加F811通过，20域52边无环导入图和本记录相对链接通过。扩大到旧测试文件的F811检查仍报告两个导入case fixture与同名参数的重定义；已用父6431原字节复现相同诊断，日志保留，没有删改fixture或将该扩大检查记作通过。

父6431和685各自canonical ALL32/32 PASS、0失败/错误/超时、21跳过及retention退出0已复核终结。这些结论只属于各自父版本；本次源码须冻结后单独执行ARM64、canonical ALL及留存，不继承父PASS。

真实24小时采集/成交/费用/资金费/现金反馈、独立经济来源、逐信号委托消费和历史连续迁移、监控/恢复、现场安装升级回滚及Kraken xStocks/XAUT专项验收均未完成。机构异机基础设施继续按研究优先决定延期。未部署、切服务、改保护运行状态、使用实际凭据或下单；策略研发仍单列。
