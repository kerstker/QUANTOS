# 黄金全天交接、原期初收尾与有限委托基础

承接[待批通知和租约交接](2026-10-02-gold-approval-handoff.md)及[有限委托设计](2026-10-02-gold-bounded-execution-delegation-design.md)。父源码6850824d89facd791e968c7554c40e5ca23add7b；独立开发目录为`/home/kerstker/quantos-dev-output/gold-bounded-delegation-20261002-xe6rh8vb`。目标仍为[十环节闭环](../plans/2026-09-28-ai-factory-closed-loop.md)，策略研发单列；本批不是实际全天运行、委托安装或最终交付。

## 委托声明和退出预留

新增DEMO_GOLD_EXECUTION_DELEGATION_V1公钥验证合同，使用独立签名域，明确未来固定bar规则用途、独立证明要求、人工确认材料摘要、源码/账户/策略/运行输入、pilot原配置、开仓截止、退出截止、持仓及原5000ms期限；禁止自动续期、改策略或标经济权威。旧Scope签名不能用于新声明。声明的确认时刻和退出截止还必须落在登记密钥的有效区间；新增过短密钥回归先失败、修复后委托30项通过。当前验证只证明提供材料内部绑定及当前登记密钥的签名，不能证明真人操作或所声明原件确为现场原件。

受保护人工安装、与原pilot/issuer历史的独立连续性核对、逐信号证明、派生精确授权及controller→issuer消费者仍未完成。原policy parser拒绝把委托artifact直接当作可执行policy；没有自动生成人工批准、策略晋级、密钥或安装记录。新mandate/validation/预算身份的迁移不能依靠重新命名group清零历史。

原issuer新增独立DEMO_GOLD_STRATEGY_EXIT_BUDGET_POLICY_V1，保留精确constraints和策略绑定，在共享group内声明退出所需的grants/orders/notional。每次开仓，在原SQLite事务内计入所有已预留提交（包括此前减仓和未完成签名），要求剩余额度至少保留退出份额。减仓仍消耗同一总预算，平仓不回补预算；重复claim、并发、重启及UNKNOWN保持原约束。新schema不重解释旧预算，改变同group预留被原binding检查拒绝。

退出预留只保证声明额度不会被新的开仓占用，不保证未来成交，也不替代原持仓、场所容量、逐笔风控、mandate、签名或有效期。委托的逐信号消费者仍需验证该额度足以支持实际受控退出。取消继续用原独立取消预算。

## 原账户期初和有限收尾

worker沿原executor custody读取request、completed_configuration及收据，输出独立original_pilot_window；与decision的配置摘要、账户和源码绑定，原decision内容和摘要保持。controller验证该附加证据。

默认全天supervisor使用原ends_at_ms，而非进程启动后86400秒；重启不重置期初，时钟回拨不延长已绑定的monotonic截止。最多追加900秒原观察/核对/反馈收尾，持仓或反馈未完成、缺失/变化窗口和收尾耗尽均不报告完成。显式短诊断仍受原max_seconds预算限制。

独立复核复现原结束后重启会重新得到900秒：迟到100秒、900秒和901秒三项回归均先失败。修复保留已过去的原结束时间投影，迟到100秒仅剩800秒，收尾期后重启首轮核对即停止，不再续给900秒；同组67项通过。已开始的单轮controller调用仍受原自身边界约束，不能把进程循环期限表述为强杀正在进行的操作。

900秒是进程观察余量，绝不延长signal、Scope、policy、key或mandate。若原权限在终点到期，仍按原门停止并保留未完成；不能据此声称所有实际到期退出已经可用。

## 原窗口推导的观察容量

新增GOLD_BAR_PILOT_STORAGE_CAPACITY_V1，使用原已完成pilot配置及900000ms bar间隔，按24小时推导98个episode（96个bar加起点对齐及终点观察）和97次轮换。容量摘要绑定原request、期初、源码、账户及基础runtime；V2另核对原签名runtime。重启读取原namespace，不分配新恢复额度，不改变原Scope、signal、风险或停止期限。

history每episode上限16MiB、总计1568MiB；observations每episode64MiB、总计6272MiB；每条轮换2000000字节、最多97条。它们是前后检查的有限资源上限，不是磁盘预留或生产实际测量。原非pilot的32周期/256MiB约束保留，已有活跃旧目录不静默迁移。

采集和轮换先以排他租约持久预留，独立于namespace的固定root级audit保存容量及allocation见证。缺失payload、allocation、marker、成对丢失或改变原身份均拒绝；部分写入保留供核对，不重置周期。所有本地保护存证同时被受信代码删除不具有独立恢复证明，不能据此宣称异机恢复。原enrollment校验提取纯records函数供按登记executor UID只读的controller复核，不伪造executor身份。

交叉复核另发现V1 request原先在FreshEpisode之后才登记，新容量检查会阻挡合法首轮。原worker的采集前登记现覆盖V1及V2，仍使用原请求、账户、分区、preparation lease、状态及期初校验；已有完成基线只读复用。V1回归先9失败/1通过，修复后七组86通过；新增10项也涵盖错身份、部分材料、过期账户、租约及完成基线不重置，全部使用隔离资料，实际策略准备边界明确停止。

首次pending request不能误建临时期初。实际原V2 enrollment、worker history namespace、原controller首条result留存再advance的组合已回归，合法首轮通过；缺原件仍失败关闭。

## 安装前反馈配置

原pilot安装审查现在消费固定`/etc/quantos/demo-observation/research-feedback.json`及原publisher校验器，核对source、executor/research身份、隔离GID和renderer研究根。账户仍由原policy/Scope绑定，未向旧profile添加账户字段。

以descriptor逐段只读检查实际研究目录，拒绝链接、错误属主、不安全或不可用的模式。bar与旧strategy分别使用原发布目标目录。候选绑定配置摘要及目录身份，人工确认前后、最终写入前后复核漂移，留存所审配置；不创建反馈目录、不切UID、不写测试反馈。delivery_verified仍为false，ACL、磁盘/挂载及后续变化仍须原实际发布路径核验。

## 验证及剩余范围

初次同源码32组联合527项通过（110.50秒），随后交叉复核发现并补充迟到重启和V1首次采集边界，最终同源码33组联合540项通过（109.85秒），覆盖上述两处修复；完整命令及XML见独立输出根joint-final-command.json和logs/joint-final.xml。根包初始退出预算回归9失败/7通过；新委托缺接口29项失败。审阅后另复现“先开仓再减仓，最后一次开仓耗尽全部退出余额”三项失败，修正为计入全部提交后再检查剩余预留。六组合同/原issuer108通过；扩大原生往返8项通过，包含空成交、持仓退出、pilot停止、原签名/OMS/V2、逐笔回执、现金反馈和下一轮研究，账户、行情、传输、密钥及身份均为明确隔离替身，无现场发送。

原期初包先16失败/5通过，原生相关联合173通过，最终新增25通过；反馈包先24失败，补旧strategy目录漏检一项失败后四组77通过。容量包先6项失败及后续缺失历史2项失败；13组187通过，另追加实际首次启动回归1项通过。数字有重叠，不累加成完整验收。独立审阅指出一个测试仅证明验证不修改输入，已据实改名，不再声称它证明外部历史连续性。

全天容量还必须包括每帧决策记录预算：原worker/controller各自strategy-decisions仍使用1000条/64MiB入口；默认10秒轮询忽略处理延迟约2小时46分可能触及数量限制，1秒模式更快，已有历史及字节上限会缩短。观察episode容量扩大本身不能关闭此缺口。后续须绑定原pilot的采样与保留合同、逐帧预算和恢复语义，保留原记录，不能仅调大常量。

独立经济来源、委托安装/逐决策证明/历史连续性、真实获准24小时订单—成交—费用/资金费—现金—研究反馈、监控恢复、现场安装/升级回滚及Kraken xStocks/XAUT专项验收继续待完成。没有切换服务、安装授权、修改保护运行状态、读取实际凭据或下单。
