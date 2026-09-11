# QUANTOS 项目状态 / 上下文交接(活文档)

> **2026-09-12 Demo 持续事件增量（开发验证中）**：新增有界持续私有连接、租约续期、不可变输入日志、断线/重启缺口与 Studio 时间线；实际隔离 120 秒 1 连接/8 心跳/0 消息，正常停止。相关测试通过，完整新源码验收与常驻运行待完成。独立普通提交、实际新订单、现金闭合、恢复指标与自然盈利尚未验收。见[实现与实测](engineering/specs/2026-09-12-demo-user-stream.md)。

> **2026-09-12 Demo增量已验收并运行**：源码 `ed97775ca`、证据/运行 `482e69240`，完整 **28/28 PASS**，ARM64两次冷构建、扫描、断网只读启动通过。15911研究入口已实际切换；五个研究组件及三个Demo采集定时器运行，连续两轮真实账户/连接记录、桌面/手机/暗色浏览器核验通过。18合约规则/费率、63笔既有成交、2789笔资金流水可见。原P3/P4与P6冻结工作者开机恢复、计划不变。自动提交与现金闭合仍未完成；全规模恢复932.389秒超过300秒，官方深度归档质量失败，自然盈利仍待证据。见[完整交付与剩余条件](engineering/specs/2026-09-11-demo-statements-and-runtime.md)。

> **2026-09-11 Demo流水与运行增量**：实际14合约规则/档位/费率、84天46笔历史成交及2776笔资金流水已取得；补查4个已平仓合约，全部失败保留。Console/Studio增加实际费用与流水归因，未把转账作为利润。此前已验收244研究栈已启动并启用15911入口；原f378自然窗口迁移开机恢复服务，保持计划。新增Demo源码尚待整体验收；自动提交、现金对账、旧备份/归档质量与自然盈利仍未完成。见[本轮记录](engineering/specs/2026-09-11-demo-statements-and-runtime.md)。

> **2026-09-11 Demo 起点与回执日志接线（增量，未部署）**：新版冻结输入已把账户起点、同币种全部仓位、精确成本声明接到原日志/V2技术投影/恢复与前端；旧空仓版本不原地升级。真实12.627秒采集成功，14个已有仓位导入隔离日志；14份精确成本和完整规则未绑定，0订单回执，现金投影明确未知。合成带仓位、去重、重启、逐仓和跨币种验证通过；主后端308通过/1跳过、最终相关108通过/1跳过（重叠），前端381通过及真实桌面/手机/暗色通过。真实成本/规则来源、完整流水、独立提交集成和实际成交现金对账仍待完成。见[本批接线与后续](engineering/specs/2026-09-11-demo-opening-journal.md)。

> **2026-09-11 Demo 账户起点增量（未部署）**：真实有界账户采集两次通过（8.026/13.080秒），均观察到14个已有非零仓位、0个普通挂单；四组前后数值一致，非原子快照。V2新增精确带仓位启动，起点浮盈亏不计入本次收益；真实均价差额保留，尚未自动导入现金账本。Console/Studio接通持仓成本、保证金模式、采样核对和历史状态。后端856通过/1跳过、前端380通过及类型/lint/构建；实际逐笔成交、现金对账、独立提交集成与新源码完整发布验收仍待完成。见[实现与依赖顺序](engineering/specs/2026-09-11-demo-account-opening.md)。

> **2026-09-11 Demo真实私有连接增量验证完成（未部署）**：用户明确提供Demo凭证并授权DGX安装/自动测试；凭证已写入Git忽略的受限key目录，真实账户签名GET成功。有界自动检查三次通过：每次8次REST、两次私有WebSocket连接和心跳，18.177/35.106/24.889秒；两个BTC合约挂单/近期成交及私有事件均0，事件投递与现金对账未知。账户已有14个非零持仓，BTCUSDT为0.0097 BTC，不能套用空仓回放。普通订单现有提交校验实测BLOCKED，未绕过。前后端接入私有检查、历史与失败；最终后端161通过/1跳过、执行补充76（重叠），前端378通过，类型/lint/构建及真实桌面/手机/暗色通过。全链路下单、真实成交补拉/对账和新完整发布验收仍未完成。见[实际结果与边界](engineering/specs/2026-09-11-demo-private-connectivity.md)。

> **2026-09-11 Demo 查询恢复第三批（增量验证，未部署）**：固定订单/账户引用/日志身份→操作员只读查询入口→精确差异→Console/Studio已接通；默认零网络，真实私有模式未执行。原OMS快照可达性规则共用，旧Binance -2013不再转成确认不存在，现有恢复保持未确认。主相关233通过/1跳过、查询补充24、恢复补充93（有重叠），前端377通过；桌面/手机/暗色与四种合成场景通过。缺回执不补造成交，不更新live OMS或现金账；用户流、实际来源/补拉、现金对账和MFT生产接线仍待完成。完整28项未运行。见[第三批实现与后续](engineering/specs/2026-09-11-demo-order-query-recovery.md)。

> **2026-09-11 Demo 回执与恢复第二批（增量验证，未部署）**：冻结规格→原始回执追加日志→既有 V2 精确技术回放→Console/Studio 已接通；重复导入/重启不重复扣费，冲突、缺费用及模型不匹配保留原始事实并显示未知。仅合成离线证据，未接实际私有流或取得账户现金对账；不把浮点订单记录转成现金权威。相关后端362通过/1跳过、前端373通过，真实桌面/手机/暗色接线通过；完整28项未运行。下一步仍是真实 Demo 身份/来源、私有流恢复、账户流水和现有 MFT 接线，独立提交权威未变。见[第二批实现与边界](engineering/specs/2026-09-11-demo-execution-recovery.md)。

> **2026-09-11 Demo 接入第一批完成增量验证（未部署）**：当前币安 Demo 地址、公共请求不携密钥、操作员账户只读接口、已有提交阻断提前检查及 Studio 模拟盘接入工作台已接通。公共接口实际成功读取 BTCUSDT/BTCUSDC；网络超时补 35 秒总时限，缺失/失败/过期分别可见。相关后端303通过/1跳过，前端全量368通过及类型/ESLint/构建通过；桌面/手机/暗色真实只读接线、键盘滚动通过，零写请求。本批未运行完整28项验收；真实账户、订单/恢复/账本闭环、独立提交权威及盈利均未因此验收。下一批继续冻结目标→订单回执→恢复→唯一账本与MFT展示；既有自然观察保留冻结版本。见[实现与后续顺序](engineering/specs/2026-09-11-binance-demo-integration.md)。

> **2026-09-11 配套研究运行配置已核验（未安装/未启动）**：基于已验收3f源码与244证据发布，生成五个同版本/同新任务目录的用户服务及独立target，拟用15911入口；systemd语法、源码启动前检查、目录/代理/只读空间绑定通过。本地QWEN清单可读，零生成调用，既有14911及正式服务未变。09:00自然批次两种计价各3/504行、无缺口，12请求2.949秒；保留f378冻结版本。见[具体配置与后续运行验收](engineering/specs/2026-09-11-research-runtime-handoff.md)。

> **2026-09-11 冻结研究空间接线完整软件验收（未部署）**：运行源码 `3f65322c5`、证据 `244dd6fbe`，完整 **28/28 PASS**；后端 7,893 通过/19 跳过，执行栈 3,442 通过/2 跳过，前端 364 通过，类型、ESLint、构建通过。两次 ARM64 冷构建、扫描、断网只读启动和干净检出绑定通过。工厂与生命周期已可切换查看各自冻结研究，额外空间只读，当前空间保留研究操作；桌面/手机真实导航通过，零只读写请求。最新发布包已核验，未执行安装。自然窗口08:01实况为各2/504行、无缺口、0新试验，保持f378来源。见[接线与验收](engineering/specs/2026-09-11-research-workspaces.md)和[合并剩余条件](reports/2026-09-11-merged-roadmap-status.md)。

> **2026-09-11 第16版备份清单兼容性完整软件验收（未部署）**：运行源码 `e5cc570f9`、证据 `964394714`，完整 **28/28 PASS**；后端 7,881 通过/19 跳过，执行栈 3,442 通过/2 跳过，前端 358 通过，类型、ESLint、构建通过。两次 ARM64 冷构建、扫描、断网只读启动及干净检出绑定通过。准备度检查现可读取已发布第16版；旧运行材料仍按实际版本/恢复历史问题拒绝。既有自然观察继续使用各自冻结源码，本次未切换服务或改变交易准入。见[实现与完整记录](engineering/specs/2026-09-11-backup-contract-compatibility.md)。

> **2026-09-11 新自然信息窗口已取得首批预热数据**：固定已验收源码f378cf554的有限P3/P4任务在06:00完成12个官方公共请求，用时1.726秒，无失败或重试；两种计价各一份观察，原始摘要及接收/行情时钟核对通过。06:01状态COLLECTING，各0/504行有效特征、无缺口、0新试验；首行特征时点为07:00。原504小时/总20候选计划、旧窗口和1,499份继承材料保持原样，0新QWEN调用。最早成熟条件为10月2日10:06，尚无新盈利结论；没有安装或切换常驻服务。见[登记与恢复说明](engineering/specs/2026-09-11-causal-window-renewal.md)。

> **2026-09-11 账户与原始回执合并软件验收完成（未部署）**：运行源码 `f378cf554`、证据 `cf2c11a92`，完整 **28/28 PASS**；后端 7,879 通过/19 跳过，执行栈 3,442 通过/2 跳过，前端 358 通过，类型、ESLint、构建通过。两次 ARM64 冷构建、扫描、断网只读启动与干净检出绑定通过。 离线账户条件、亏损仓位部分减仓、原始订单回执去重与费用诊断、桌面/手机图表已接通。中间 c8165ec12/1f78ec7bc 亦有独立完整检查点；34首轮主动取消及全部失败保留。真实私有来源和场所校准、自然盈利、独立经济准入、正式运行验收仍未完成，异机恢复搁置。见[合并进度与剩余条件](reports/2026-09-11-merged-roadmap-status.md)。下方标有“验收中”的旧检查点仅描述当时源码。

> **2026-09-11 账户条件验收中补修部分退出**：34d713fc8的真实桌面/手机接线、六次合成回放和独立费用/档位核验通过；首轮ARM64构建A通过后，补查复现亏损仓位因初始保证金不足无法部分减仓，主动停止B并保留全部材料，未运行canonical。已将新敞口初始保证金与减仓维持保证金检查分别版本化，原亏损/费用及逐仓破产边界保留；旧结果不继承。修正源相关验证继续，尚不能将34标为完成验收。见[记录](engineering/specs/2026-09-11-account-terms.md)。

> **2026-09-11 冻结账户条件增量（开发验证中，未部署）**：离线费率/账户配置/有限档位、接收时间与摘要绑定已接 P5 输入及 Studio；区分已选杠杆与档位最大杠杆，有限新风险上限覆盖挂单、实际切片与直接账本，越界仓位继续估值和减仓。相关后端266通过/1跳过、后续限价压力78通过，前端354通过及类型/ESLint/构建通过。完整新源码验收继续，最近完整运行验收仍为134ff89d7；实际账户来源、费用及场所校准未知。见[实施与预注册](engineering/specs/2026-09-11-account-terms.md)。

> **2026-09-11 场所规则与分角色计费完整软件验收（未部署）**：运行源码 `134ff89d7`、证据 `28996756c`，完整28/28 PASS；两次ARM64冷构建、扫描、断网只读启动与干净检出绑定通过。后端7,792通过/19跳过、执行栈3,442通过/2跳过、前端352通过。价格/数量规则与maker/taker模拟计费、预留及前端均纳入本次源码。真实公共材料仍保留11a679282来源，合成费率操作保留3a2e65584来源；失败、取消和输入历史全部保留。实际账户费用/保证金、私有回执、自然盈利与实盘经济准入仍未验收。见[完整记录](engineering/specs/2026-09-10-liquidity-fee-accounting.md)和[统一路线](engineering/plans/2026-09-09-profitability-validation.md)。

> **2026-09-10 场所规则与分角色计费合并（开发验证中，未部署）**：计费源码 `3a2e65584` 已接入主动/被动共享账本、P5不可变输入、Console与Studio；扩展相关378通过/1跳过、兼容19通过、前端全量352通过及类型/ESLint/构建通过。实际桌面/手机登记两版合成输入、六次情景，十八份文件和六份引擎摘要核验通过，四条成交费用独立一致。此前 `616a4b721` 首次原生构建在60分钟超时，第二次扫描数据库下载失败；均未运行canonical，完整验收仍为 `35062eb67`/`d1055d75f`。两批合并源码重新完整验收，不重贴旧研究来源；真实费用/保证金、场所回执和自然盈利仍未验收。见[计费实现](engineering/specs/2026-09-10-liquidity-fee-accounting.md)、[规则记录](engineering/specs/2026-09-10-venue-filter-semantics.md)和[统一路线](engineering/plans/2026-09-09-profitability-validation.md)。

> **2026-09-10 场所价格与数量网格增量（开发验证中，未部署）**：不同数量起点/步长、非网格上界、独立关闭价格条件和偏移价格取整已接共享账户及公开适配器；市价单按明确的普通/附加数量组合政策检查，实际场所仍未验证。前后端合同与规则表同步，相关后端 573 通过/1 跳过，过滤器专项 37 通过，前端相关 10 通过及类型/ESLint 通过。旧测试报文共享对象的失败保留并修正。新源码完整验收继续，最近完整验收仍为 `35062eb67`/`d1055d75f`。见[实现与范围](engineering/specs/2026-09-10-venue-filter-semantics.md)。

> **2026-09-10 独立策略与 P5 合并源码完整验收（未部署）**：运行源码 `35062eb67`、证据 `d1055d75f`，完整 28/28 PASS；两次 ARM64 冷构建、扫描、断网只读启动及干净检出绑定通过。后端 7,731 通过/19 跳过、执行栈 3,442 通过/2 跳过、前端 350 通过，类型/ESLint/构建通过。前一源码 27/28 的 Runner 诊断任务清单遗漏已修正，全部失败保留。独立策略 286 次实测仍无稳定净增量，停止扩搜；两种计价六次真实公共材料 P5 操作与十八份封存文件核验通过，均零成交。19:12 只读状态为 P6 12 预测/0 成熟日，信息面板各 20/504 行、各两处旧缺口。真实场所校准、自然盈利与实际交易准入仍未完成，异机恢复搁置。详见[交付记录](engineering/specs/2026-09-10-public-order-constraints.md)、[实际组合结果](reports/2026-09-10-independent-sleeves-comparison.md)和[统一路线](engineering/plans/2026-09-09-profitability-validation.md)。

> **2026-09-10 冻结实测与订单边界收尾（未部署）**：修正源码 `e9ca09b51` 的 286 次独立策略比较完成，独立 Fraction 复核 768,956 个账户状态、63,119 次成交精确一致；1,330 文件恢复及真实桌面/手机操作通过。没有稳定成本后增量，停止扩大同类搜索。两次 ARM64 冷构建镜像一致，但后续基础镜像拉取返回 401，完整原生材料和 canonical 未完成，旧 `fce9c6c7a` 仍是最近完整验收版本。已补公开动态订单边界、前后端及固定基础镜像预检，相关后端 513 通过/1 跳过、前端 9 项与类型/lint/构建通过、采集器 58 项通过；合并源码重新验收。15:17 既有 P6 为 8 预测/0 成熟日，信息面板每计价 16 行/2 缺口。见[实测报告](reports/2026-09-10-independent-sleeves-comparison.md)与[订单边界](engineering/specs/2026-09-10-public-order-constraints.md)。

> **2026-09-10 独立持仓语义修正（验收暂停后修复）**：源码 `40379cd5d` 的真实回放中进一步发现“一个组件持仓延续、另一个空仓”仍换算浮点目标，可能误减一手。六个多空/价格回归复现，已修正空仓无变化的合并规则；真实退出不被抑制。该源比较与 ARM64 验收已停止并保留全部材料，账本对账通过不消除策略意图错误。修正源码另行重跑和验收，见[记录](engineering/specs/2026-09-10-independent-strategy-sleeves.md)。

> **2026-09-10 独立策略持仓增量（开发验证中，未部署）**：逐组件成本后决策、真实净数量归属、统一账户、有限内层组合、冻结预测三折复用及独立前向账户已接线。Studio 增加实际持仓阶梯图、配对成本、三折与独立结论；前端全量 349 通过、类型/ESLint/构建通过。修复容量零成交误报为成交，以及已启用模型在无新预测时漏验的问题；完整记录保留。新源码真实冻结材料和完整 ARM64/canonical 验收继续推进，不继承旧 28/28，也不改变 QWEN 无增量停止扩搜结论。见[实施与范围](engineering/specs/2026-09-10-independent-strategy-sleeves.md)。

> **2026-09-10 P5 双时钟回执增量（开发验证中，未部署）**：规范化回执导入、三组前缀校准、不可变选择、后段路径/数量/双时钟误差已接入工作流和 Studio；六次仿真全部计数，同分、缺口和失败保留未知。相关后端 522 通过/1 跳过、导入边界 23 通过、前端全量 344 通过，类型/ESLint/构建通过。当前为合成回执的软件证据，没有真实私有来源认证或场所验收。独立策略袖套、自然盈利与新源码完整 ARM64/canonical 验收继续推进；旧完整 28/28 不继承。见[实现与验证](engineering/specs/2026-09-10-passive-receipt-calibration.md)。

> **2026-09-10 P5 统一账户增量（开发验证中，未部署）**：被动队列与主动回放共用订单/预留/风险及唯一 Decimal 账本，接通公开规则、容量和资金费日程采集、不可变研究输入与 Studio 成本/权益/回撤。修正普通队列错误消费包含 RPI 的总量，改用明确的 `nq`；旧总量诊断不作为校准依据。两种计价各三组实际材料回放零成交、零对账差额；相关最终回归 535 通过/1 跳过，后续输入等 60 通过，前端全量 338 通过。私有回执校准、独立策略袖套、自然盈利及本源码完整验收继续推进，旧完整 28/28 不继承。见[实现与验证](engineering/specs/2026-09-10-passive-canonical-account.md)。

> **2026-09-10 P5 队列增量（开发验证中，未部署）**：新增官方原始深度/聚合成交采集、独立场所与客户端时钟、固定动作共享队列及 Studio 诊断。两种计价实际采集和三组队列回放已运行，短窗口内订单均在成交前撤销。被动成交尚未接唯一成本账本，真实回执校准、完整 P5 与新源码完整门禁未完成。上一条完整验收只属于上一源码。见[实现与剩余工作](engineering/specs/2026-09-10-passive-execution-input-and-queue.md)。

> **2026-09-10 本轮完整验收通过（未部署）**：运行源码 `fce9c6c7a`、证据 `6a0ee4a20`，完整 28/28 PASS；两次 ARM64 冷构建、扫描、断网启动及干净检出绑定通过。后端 7,516 通过/19 跳过，执行栈 3,442 通过/2 跳过，前端 334 通过，类型、ESLint 与构建通过。首次存储声明遗漏的失败保留并已修正。源码 `876f381eb` 的实际本地 QWEN 流程耗时 826.75 秒，36 次候选比较、39 次模型调用未证明稳定成本后增量，停止扩搜；真实桌面/手机接线与恢复通过，120 份调用材料未重写。本轮含采集可靠性、有限组合、配对前向、研究上下文/记忆/结论及角色过程展示。旧常驻保留；09:27 P6 为 2 条预测/0 成熟日，信息面板每计价 10 行/2 缺口。自然盈利、真实场所与后续软件扩展仍未完成，异机恢复搁置。文档收尾与已验收运行源码分别记录。见[实际报告](reports/2026-09-10-qwen-research-protocol-comparison.md)、[统一路线](engineering/plans/2026-09-09-profitability-validation.md)和[操作手册](runbooks/profitability-validation.md)。

> **2026-09-10 采集与组合增量（开发验证中，未部署）**：官方采集有限重试、共享截止时间、请求记录与恢复已完成专项验证，源码 `f8f4da815`。继续补有限内层组合选择、三折整体账户、配对前向、成本账户与 Studio；专项证据不替代完整验收。真实净盈利尚未建立，研究时间上下文、证据结论与有限 QWEN 协作仍待后续增量。见[组合记录](engineering/specs/2026-09-10-inner-portfolio-and-forward.md)与[统一路线](engineering/plans/2026-09-09-profitability-validation.md)。

> **2026-09-09 R6 快照停滞保护（未部署）**：真实公共冻结库复现 STANDARD 无限重复读取，新增 60 秒无进展退出、SNAPSHOT 失败分类与前端说明。129 项后端专项通过；真实 906 MB 数据显式 WAL 策略三次恢复 12.45 / 11.57 / 11.18 秒。前一源码 `ec02a770d` 已完整 28/28 PASS、ARM64 通过，证据 `7687ac00c`；本次新源码另行验收。生产 RTO、异机 DR、真实成交及自然成熟盈利仍未验收。见[记录](engineering/specs/2026-09-09-sqlite-snapshot-progress.md)。

> **2026-09-09 R6 恢复诊断增量（未部署）**：逐文件与数据库/历史校验耗时接入失败 evidence 和前端运维页；历史摘要匹配，手机卡片展示；修复符号链接恢复目标误接受。相关后端 205 通过/1 跳过、前端 327 通过，合成隔离测量与实际 HTTP 验证见[记录](engineering/specs/2026-09-09-restore-performance-diagnostics.md)。未跑新完整门禁；生产 RTO、异机恢复与盈利证据仍未验收。

> **2026-09-09 研究检查点发布增量（未部署）**：修复已有损坏归档误认、损坏下载副本及半成品发布；常驻与工厂入口统一实际恢复复核，前端区分旧存在记录和新复核状态。专项与真实 1518 文件隔离验证见[记录](engineering/specs/2026-09-09-research-checkpoint-publication.md)。本增量未跑新的完整门禁，前一源码 `4674ffe59` 完整 28/28 PASS、证据 `854f26a09` 保留；生产恢复及盈利未验收。

> **2026-09-09 P3/P4 自然信息闭环（开发验收中，未部署）**：补上新数据不能进入旧历史实验的缺口：504 小时固定自然窗口、实际接收时间、OI 预热、分币种面板与独立 USDC/USDT 账本、自动有限研究、恢复和前端缺口显示。特征留存不冒充提前预测。前一源码 `521da5e1a` 完整 28/28 PASS，证据 `b09349630`；本次新源码单独验收。见[实现](engineering/specs/2026-09-09-causal-information-workflow.md)。实际盈利、场所回执和旧生产恢复证据仍未完成。

> **2026-09-09 盈利能力验证增量（开发验收中，未部署）**：P0–P6 已增加受限研究接线；本次补齐不可变输入版本、固定动作三组延迟校准、十四天前向成本账户、恢复检查点与前端工作区。真实 HTTP 导入→队列→只读结果以及前向登记通过，桌面/手机无脚本错误或横向溢出。冻结 #4 成本前已亏，P1 外层零成交；P3/P4 因果历史输入、P5 真实回执、P6 自然成熟成本证据及旧生产恢复验收仍缺，不能宣称路线图全部验收或盈利。见[实现与实测](engineering/specs/2026-09-09-profitability-validation-increment.md)与[统一路线](engineering/plans/2026-09-09-profitability-validation.md)。本增量未切换运行服务。

> **2026-09-09 R6 已部署与时间竞态修复**：源码 1e54fd7a3 完整门禁 28/28 通过，正式服务已切换 b43ad4f7b，研究工厂使用 1e54fd7a3；十个常驻服务和两个入口的桌面/手机验收通过。归档失败已实际发布新 FAIL，备份恢复超时及数据质量仍为健康失败。现场另发现请求开始后写入的新回执会被误判为未来时间；已改为读取后取实时参考时间，83 项回归通过，补丁待独立发布验收。详见[实现记录](engineering/specs/2026-09-09-operational-health-workbench.md)。

> **2026-09-08 生命周期收尾（完整验收中）**：新增真实预测校准、残差与方向诊断，修复重复标签比较、日度时间单位和中文标签 ESLint 遗漏；计划选择开放全部 36 来源。详见[收尾记录](engineering/specs/2026-09-08-lifecycle-calibration-delivery.md)。外部经济证据与生产恢复仍未完成。

> **2026-09-08 中文操作记录接线增量（未发布）**：浏览器客户端、同源代理与 Runner 统一采用显式 UTF-8 编码，修复中文名称无法提交任务；英文标签保持兼容，研究令牌认证独立。相关后端及启动器 92 通过、前端全量 312 通过，实际浏览器经代理创建/暂停/恢复/停止计划成功。详见[实现记录](engineering/specs/2026-09-08-operator-unicode-transport.md)。本增量未跑完整 canonical，研究常驻仍使用 db676c2e3。

> **2026-09-08 36 对象对标与生命周期开发**：新增公开来源目录、V2 可组合搜索、有限持续研究计划、模型挑战者诊断和生命周期工作台；首期源码 db676c2e3 已通过完整门禁 28/28，并在用户级研究服务常驻；后续增量独立验收。详见[路线与范围](engineering/plans/2026-09-08-alpha-factory-lifecycle.md)。20 家机构 + 16 个系统，不声称私有机构审计；实际场所与经济准入仍未闭合。

> **2026-09-08 工厂端到端状态增量**：整体完成同时核实主研究、后续流程和 Runner 结果登记；明确显示未知/失败与恢复入口。详见[实现记录](engineering/specs/2026-09-08-factory-workflow-status.md)。不改变经济准入，增量检查不替代完整发布门禁。

> **2026-09-08 DGX 本地 QWEN 接入增量**：用户选择现有 SGLang `qwen3.8-27b`。补充显式回环 HTTP 接入，无代理/重定向/凭证，真实受限提案调用成功；Console 显示绑定当前配置的近期调用证据，前端默认 AI 编排。详见[接入记录](engineering/specs/2026-09-08-qwen-local-factory.md)。不改变交易准入，旧版完整验收不覆盖本增量。

> **2026-09-08 AI 工厂整链增量（发布验收中）**：接通成本后真实持仓决策、六情景压力、共享账户、三折完整搜索、独立验证包、自然小时前向、检查点和常驻心跳。任务 4 外层亏损 -12.226896 USDC；实际预测已开始，成熟样本与外部权威仍等待。前后端及操作入口已同步，详见[交付与 A0–A6 验收矩阵](engineering/plans/2026-09-08-ai-factory-roadmap-delivery.md)。不声称生产 RTO、真实执行和整体经济路线已完成。

> **2026-09-07 AI 研究工厂增量（未发布）**：接入小时级规则/Ridge/GBM 自动实验、受限模型提案、预算及暂停恢复、独立外层验证、模型材料和实验登记，新增 Studio 工厂工作区。真实 BTC 任务完成三模型比较，外层为亏损技术结果；模型服务尚未配置。新小时模型持续前向与完整路线图仍未完成，详见[实施记录](engineering/plans/2026-09-07-ai-factory-implementation.md)。不把此前完整门禁当作本次源码验收证据。


> 2026-09-07 持续研究与可视化增量：新增公开行情和当前冻结观察只读接口；Studio 增加行情、研究分析、模型诊断、运行监控，九个主入口、58 个模块。BTC 已冻结观察加入两分钟调度，未冻结实验保持 NOT_CONFIGURED；修复调度锁与单实验锁嵌套冲突。此增量验证与运行约束见 [实施记录](engineering/plans/2026-09-07-studio-operations.md)。下方既有完整门禁证据只覆盖各自源码锚点，不能覆盖本次未验收源码。常驻服务仍为 1cbf196；系统切换需要管理员执行发布事务。


> **2026-09-07 Research Studio重建：** 按用户要求先修复Runner/Console状态根和
> 新任务诊断接线，再用全新Studio入口与样式替换旧前端。五个工作区，完整研究表单、
> 实验/工厂/前向详情、A/B对比和56只读模块，保留执行哈希增量及断流隐藏。旧UI不再
> 进入生产入口，历史回归保留。已有全量前端282通过、E2E15通过，衔接/对比尾修复另验；
> 最终源码、验收及截图见[实施记录](engineering/specs/2026-09-07-research-studio-rebuild.md)。
> 无常驻切换或交易操作，不把旧完整验收当成本次源码证据。


> **2026-09-07 在线月度工厂增量：** 接通真实接收时间训练、不可变月度四模型版本、
> 60/90共同前向误差选择、旧版本标签结算与每日预测组中断恢复。前端可启用并查看
> 训练进度、版本、模型误差及工厂观察。实际隔离HTTP已通，训练样本仍0；未安装
> 调度或切换服务。相关204通过/1跳过、前端274通过，源码绑定与恢复尾修复18通过。
> 本批未跑完整canonical；上一完整28/28 PASS属于源码42825b8a7，不能替代本批验证。
> [实施与操作说明](engineering/specs/2026-09-07-btc-online-factory.md)。



> **2026-09-07 BTC 工厂回放与冻结观察：** 新增四模型成熟误差月度选择器、冻结PRICE基线，
> 八政策×三成本×两风险共48情景共用唯一Decimal账本；全部登记，支持显式中断恢复。
> 前后端已接通工厂结果、成本切换、月度选择解释和冻结观察。真实48情景完成，独立
> Fraction复核27744权益/持仓及费用/资金费通过。四模型冻结，实际观察WAITING_WINDOW、
> 0前向样本。相关157通过/1跳过，前端273通过；浏览器真实隔离API检查通过。
> 最新历史完整验收报告为2e79c094的PASS，不能替代本次提交验证。没有切换常驻服务。
> 在线月度工厂重训/额外数据机制/真实执行恢复仍待开发，不宣称全部方案完成。
> [详细实施记录](engineering/specs/2026-09-07-btc-factory-outer-and-forward.md)。

> **同日验收补充：** 提交409e03f25后的第二次48情景运行与第一次账本/指标/拟合完全一致，
> 再次独立27744条检查通过。该提交的ARM64两次冷构建、扫描、断网启动通过。修复材料
> 迁移被绝对检出目录错误阻断的问题：仅在明确绑定同一源码提交时允许原始构建目录，
> 命令/配方/镜像验证保留，467专项通过。完整验收首轮失败及各版本后续结果分别保留，
> 不把旧版本验收或0前向样本改写成新版本/经济证据，详见上述实施记录。


> **2026-09-07 BTC 机制实验工作流：** 接通四模型脚本→JobQueue/RunRegistry→技术接口→
> 前端误差/增量表，新增实验编号并修复永续/订单块入口缺 attempt。缺数据保留四个失败试验，
> 同编号不能覆盖。冻结公开数据重放577标签，预测文件与前批字节一致，统计一致、HTTP 200。
> 当前常驻进程已核实为1cbf196；本次仅独立开发与验证，没有切换服务。
> 仍仅预测探索，小时级/外层工厂回放/前向和经济执行未由此完成。详见
> [实施记录](engineering/specs/2026-09-07-alpha-factory-workflow.md)。

> **2026-09-06 六方向 Alpha 证据图谱：** 扩展核查22条来源并引用前轮10条，覆盖价格结构、
> 持仓/订单流、carry、做市、跨所、期权；形成18张待冻结实验卡，全部未运行。
> 重点区分扫掠延续/回归、OI报告一致性、maker返佣与零手续费，以及跨所信息与双腿执行。
> 固定SMC公共源码只读审查发现未来柱依赖，未安装/执行。主报告和机器材料见
> `docs/reports/2026-09-06-alpha-all-directions-evidence-atlas.md`。
> 本轮仅文档与证据材料；没有新增盈利证明、经济权威、引擎变更、部署或下单。
> 原日频信息实验、订单块失败结果及完整门禁缺口继续有效。

> **2026-09-06 BTC机制研究工作论文：** 10条来源/7张机制卡，冻结四个日频OLS信息增量模型。
> 577个24h标签，FLOW相对PRICE的MSE改善0.943%，但全期仍不及零预测、探索校正p=0.0822。
> 已结算资金费该形式无增量；没有已验证Alpha或账户PnL。OI两日官方归档各288行校验通过，
> 全窗OI与时间语义未验收。两次重放共8条试验；预测字节一致，独立2920预测和12统计通过。
> 专项29通过，未改经济引擎/风险/部署；既有金融边界与完整门禁缺口仍在。
> 详见 `docs/reports/2026-09-06-btc-mechanism-academic-report.md` 及其复现材料。

> **2026-09-06 订单块重建研究：** 用户最新报告 maker 0 / taker 0.036%（3.6bp）。
> 缺少原结构引擎源码，冻结因果 swing/BOS/FVG 重建规则，在 BTCUSDC 永续完成 24 项情景。
> 中心 5bp 滑点：2x 净亏 140.25 USDC；5x 触发150预算停机、净亏152.81，未截断亏347.09。
> 三组成本全亏，0候选过筛；不能称原包精确复现。沿用唯一Decimal账本和技术结果页。
> 独立13872净值/持仓检查、318完整交易时序复核通过；专项480及71通过，前端264通过。
> Run run_a955088bcee8af1a458c0af7ff56cb3f；仍TECHNICAL_VALIDATION_ONLY，原完整发布门禁仍失败。
> 无部署、下单或晋级。详见 `docs/reports/2026-09-06-causal-orderblock-results.md`。

> **2026-09-06 QUANTOS 有限自动永续研究：** BTCUSDC、单边taker4bp，
> 接通JobQueue/RunRegistry、TrialLedger、固定假设语法/UCB、唯一Decimal账本及既有技术结果页。
> A/B/C失败后自动生成8个新假设；每轮78项情景，两轮156项均保留，独立各45084净值检查通过。
> 11候选全未过筛；14日资金费反向中心情景+145.73 USDC但HAC t=0.62、剔除最佳5日后-3.73，
> 不算已验证Alpha。两次数据预检失败保留，补齐官方缺失mark日；反手新风险及资金费窗口检查已修复。
> 最终研究运行run_998b025d60feb3ddda98c9bfbdb41760。LLM提议、正式统计准入、Paper签名权威、
> USDC真实执行恢复、真实费用/外部现金流、持久150预算及发布验收仍未贯通；没有部署或下单。
> 完整验收：后端7152通过/4失败（其中导入图1项已尾修复，供应链3项仍缺证据），
> 执行栈3388通过，前端263通过；尾修复36通过，完整门禁仍FAILED。
> 详见 `docs/reports/2026-09-06-perpetual-autonomous-research-and-gaps.md`。

> **2026-09-06 用户纠正主线为 USDC 永续：** 用户确认按 taker 4 bp；官方普通用户
> USDC 永续活动费率 maker 0 / taker 4 bp，实际账户费率未查询。研究驱动显式传费率，
> 默认4，旧5可重现；原三个永续候选45次重算，独立13245净值/持仓检查一致，仍均未过筛。
> 专项70通过。现货A/B/C记录保留，但不能据此判断永续表现；A/B/C合约版仍未完成，
> 后续恢复BTCUSDC永续主线，USDT独立对照。没有免费maker成交假设、晋级或下单。
> 详见 `docs/reports/2026-09-06-usdc-perpetual-fee-review.md`。

> **2026-09-06 公开现货 Alpha 首批验证：** 保留 USDT/USDC 永续实现与旧结果，
> 独立现货现金模型完成 A(28日动量)/B(55/20突破)/C(Ridge)及三基准，
> BTC/ETH × USDT/USDC × 三滑点 × 带/不带150累计停机，共144次情景。
> 用户允许冻结当前规则的历史初筛；结果仍为技术诊断，不是历史规则或准入证据。
> 原始分钟归档经官方校验；Fraction独立复核11887成交、83088净值点全精确一致。
> BTC三个候选均不支持推进；ETH趋势全期盈利但2026转亏、Alpha统计弱，未晋级。
> 专项1936通过，新增独立复核对抗测试另跑5通过；前端262通过，lint/typecheck/build通过。
> 150停机只实现于本批离线模型；真实执行累计预算、完整费用净值、USDC恢复、
> 权威/发布/告警/前向仍缺。没有部署、下单或放宽门禁。详见
> `docs/reports/2026-09-06-public-spot-alpha-screen.md`。

> **2026-09-05 BTCUSDC 最小闭环复核：** 用户指定币安 BTC/USDC 永续、尚无冻结策略。
> 默认对账新增非有限数值/非法容差拒绝；唯一 Decimal 账本支持显式 USDC 线性技术
> 模型并拒绝 USDT/USDC 混合钱包，尚不代表真实 USDC 执行适配完成。专项1874 passed。
> 三候选（20/60趋势、20/10突破、资金费反转）预注册后完成45次技术回测，独立
> Fraction 逐日对照13245项一致；三候选均未通过筛选，不能宣称存在可实盘 Alpha。
> 真实费用/资金费/外部现金流账本、累计150预算、USDC适配/恢复、签署权威、发布告警
> 与策略前向是剩余关键交付。运行版本仍cd7698f，只读L2；无部署或下单。详见
> `docs/reports/2026-09-05-btc-usdc-readiness-and-alpha-screen.md`。

> **2026-09-05 DGX 发布检查修正：** 两轮 ARM64 冷构建摘要一致，但实际漏洞扫描
> 拒绝发布。基础镜像 libuuid 2.42-r0 新漏洞已定位；当时可用的补丁为 2.42.3-r0，
> 当前 Alpine v3.24 索引已滚动到 2.42.3-r1，运行层须跟随该修订版本重建重扫。
> 捕获器新增失败扫描报告留存，
> 新技术接口补齐精确只读路由清单，相关 79 tests 通过。最终 canonical 状态以
> `evidence/validation_report.json` 为准；本记录不宣称发布完成。详见
> `docs/engineering/specs/2026-09-05-dgx-engine-foundation-release-check.md`。

> **2026-09-05 DGX 第三批技术回放：** 冻结 Binance 公共 REST 原始响应与时钟，
> 接通白名单 `perpetual_replay`、定频 Decimal 净权益、registry 与独立控制台技术页。
> Fraction / VectorBT 各 7 个共同模型案例通过；真实样本回放对账残差为零。
> 14 标的 bookDepth 原始归档倒挂已复现，新增逐标的拒帧诊断，隔离阈值不放宽。
> ARM64 BuildKit 构建增加 4 CPU / 16 GiB 上限。完整 M1/M2 尚未验收；公开样本
> 无法证明完整场所保证金规则，R1 与运维验收仍需完成。用户明确延期异机备份恢复，
> 不再将提供异机目标作为本地开发前置条件。详见
> `docs/engineering/specs/2026-09-05-dgx-engine-foundation-increment3.md`。

> **2026-09-05 DGX 第二批订单准入与额度预留：** `OrderRuleProfile` 显式绑定
> 合约 epoch/场所/可用时间，检查价格 tick、数量上限、最大名义额和订单类型；
> 反手最低额可明确选择原订单或新仓政策。全仓挂单按 canonical 保证金档位增量、
> 费用及不利成交缓冲预留；反向挂单不互抵，跨标的竞争同一钱包。拟成交事务内
> 重新检查剩余承诺，失败释放深度/容量/预留；部分成交、撤单、到期均有审计。
> schema 为 `LINEAR_PERPETUAL_FORWARD_SIMULATION_V3`。新规则模式暂不支持逐仓，
> 无 profile 的旧调用明确为未预留的技术诊断。专项 458 passed，治理边界 56 passed。
> 尚未冻结真实场所 profile/公共数据，D1/I1/V1/R1 与 M1/M2 验收仍待完成。
> 详见 `docs/engineering/specs/2026-09-05-dgx-engine-foundation-increment2.md`。

> **2026-09-05 DGX 底层路线首批增量：** 按用户提供的
> `docs/engineering/plans/2026-09-05-dgx-engine-foundation-roadmap-review.md`
> 开始 B0/E1/E2。建立独立 ARM64 锁定开发环境和 `scripts/dgx_develop.py`，
> 显式校验 HEAD/Python/依赖并隔离测试输出。拟成交复用账本原子事务，在容量截断和
> 费用入账后的状态上复核风险；超限不消耗深度/容量，也不删除过去的市场损失。
> 原订单准入与执行切片分离，合法 10 美元订单可十次各成交 1 美元；手续费直接按
> 实际成交量计算。另修复无限小数平均成本导致的结算误拒：总成本精确记账，
> 部分平仓尾差留在剩余持仓，舍入策略随结果显式版本化。专项 435 passed。
> E2.1 场所 profile/预留、D1 冻结真实数据、I1 研究入口与定频统计、V1 差分验证、
> R1 发布尚未完成；M1/M2 均未验收。运行服务与旧主目录未提交成果保留。
> 设计、范围与验收见 `docs/engineering/specs/2026-09-05-dgx-engine-foundation-increment1.md`。

> **2026-09-05 DGX 回测引擎修复：** 分离决策时风险与成交时重估；合约数量校验统一
> 处理开仓、减仓和反手，显式零持仓不再被集中度多样性条件阻止平仓；账本回滚改为
> 当前状态快照 + 追加日志截断 + 单容量项撤销，事件时间以游标推进。新增
> `services/execution/perpetual_simulation.py`，策略提交订单意图，模拟器处理挂单、撤单、
> 拒单、部分成交与同时间多腿，权益和风险历史来自同一 Decimal 永续账本。
> 成交价格区分显式费用与已嵌入的价差/滑点；重复报价不能补回已消耗流动性。
> DGX 执行/风险相关 407 项测试通过；2,000 个 mark 事件约 12.1s→0.335s。
> 结果仍为 `TECHNICAL_VALIDATION_ONLY`；旧 V1 经济路径继续隔离，不产生盈利或准入声明。
> 完整设计、API 和支持范围见
> `docs/engineering/specs/2026-09-05-backtest-engine-repair.md`。发布级全量验证与外部数据权威
> 是独立要求，不以本次引擎测试替代。
> 全量回归：core 7055 passed / 19 skipped / 4 failed（依赖图过期一项，本次修正；
> 供应链旧证据三项，已在修复前版本独立复现）；live/trading/tools 3317 passed / 2 skipped；
> 前端 typecheck、252 tests 和 build 全过。ARM64 发布证据仍绑定旧代码，完整发布门禁未绿。

> **2026-08-31 DGX 容器发布链改为 ARM64 原生：** `deploy/console.Dockerfile`
> 现在以 ARM64 单平台基础镜像为默认，并在 builder/runtime 两阶段都以
> `TARGETARCH=arm64` fail-closed；正式验证、供应链 baseline 和发布证据清单改为绑定
> ARM64 双冷 OCI 构建、ARM64 Trivy 扫描及无网络只读健康冒烟三件套。AMD64/QEMU
> 构建不再属于本机发布路径，迁移时临时注册的 `qemu-x86_64` 已注销。
> ARM64 runtime 层显式固定 `libcrypto3/libssl3=3.5.8-r0`，消除固定基础镜像
> 中 OpenSSL 3.5.7 的已修复漏洞；发布门禁仍要求最终镜像零已知漏洞。

> **2026-08-31 容器上游补丁滞后策略：** 外部固定基础镜像可能短暂落后于其包仓库；
> 容器审计仍对基础镜像任意 CRITICAL fail-closed，并且仅在 Trivy 给出明确
> `FixedVersion`、最终运行镜像独立扫描为零已知漏洞时，才接受已在构建中消除的基础
> 镜像 HIGH。无修复版本的 HIGH 和运行镜像任何等级发现仍阻断发布。本次变化用于处理
> 上游 Python/Alpine 镜像中的 OpenSSL `CVE-2026-14456`（包仓库已有
> `3.5.8-r0`），没有放宽最终运行镜像的零漏洞门禁。

> **2026-08-26 借贷成本机制实验已完成本地开发、尚未在 DGX 冻结：** 新增的不是
> 单因子 Sharpe 排名，而是“谁付钱”的联合合同：OKX 14 币公开借贷成本处于截面前
> 20% 且继续上升，同时 Binance 永续资金费为负、mark/index 基差不高于 -2bps、
> 过去 24 小时下跌至少 2% 时，假设拥挤空头与受跨场所融资约束者的平仓为 24 小时
> 反弹付费。主候选与反号、borrow-only、funding-only、低借贷成本四个控制一次冻结；
> 每币七天 lockout，正式前向至少 120 天/75 个有效日/100 个独立事件。OKX 已抓取历史
> 只在实际 fetch knowledge time 后可知，公开 amount 字段废弃，因此借贷可用数量仍为
> `UNAVAILABLE`，绝不拿 rate 冒充 borrow capacity。最终只按增量净利润、正交性、成交
> 可信度、永续盘口容量、换手和尾部风险评审；即使 PASS 也只进入人工 L2 paper review。
> 当前 27 项最小相关测试通过，尚未生成 DGX append-once preregistration，尚未访问结果，
> `alpha_eligible_count=0`，实盘边界不变。设计见
> `docs/engineering/specs/2026-08-26-borrow-pressure-forward-experiment.md`。

> **2026-08-26 DGX Binance 全链证明扩展性故障与本地修复：** 真机游标权威已约
> 45.9M 事件；90 分钟隔离全审计超时。随后五分钟 realtime-feature worker 在源证明
> 过期后自行从创世扫描，约 45 分钟达到 9.7 GiB 峰值并被 systemd 限时终止，timer
> 重启后仍零进展。失败全程 fail-closed，没有发布未验证特征或产生 Alpha 资格。现已把
> source/cursor 全审计唯一归属 resident stream：24 小时证明到期前四小时启动、隔离
> verifier 三小时硬上限；短期 feature worker 只验证可信 batch checkpoint 后缀，若
> checkpoint 缺失/过期则验证 resident 的完整证明、绑定状态文件 SHA 并独立验证其后缀，
> 永不回退到创世扫描。定向回归 51 项通过。当前仍需形成不可变 ARM64 release、等待两条
> 真机全证明完成并取得新 feature PASS 才能关闭事故；O(n) 全审计成本仍诚实保留，未来
> segment/Merkle 迁移须独立评审，不能以延长证明期限或跳过 payload 校验替代。系统继续
> `L2_DRY_RUN_ONLY`，凭证、账户、broker、下单、testnet/live 均未开放。设计见
> `docs/engineering/specs/2026-08-26-binance-resident-full-audit-renewal.md`。

> **2026-08-25 双场所强平机制的盲化成熟度与 DGX 发布已收口：** 新增的
> outcome-blind health worker 只把预注册后的 Bybit `allLiquidation` 原始打印按
> 同币种/同方向、5 秒接收时钟间隔聚成已静默闭合的 burst，再施加 30 分钟币种级
> lockout；它不读取价格、数量、标签、收益或 Sharpe。每 30 分钟的 L2 timer 只检查
> 冻结预注册 SHA、可信本地 checkpoint/后缀哈希链、事件日和独立 episode 数，且
> `unblind_allowed=false`、`outcomes_accessed=false`、`trial_registry_mutated=false`、
> `alpha_eligible_count=0`。最新 DGX receipt 为 operational `PASS` / readiness
> `COLLECTING`：394 条前向强平打印形成 122 个闭合 burst、12 个独立 episode、1 个
> 事件日，BTC/ETH/SOL 各 4 个；90 天、60 事件日、500 episode、每币 75 episode
> 四道成熟度门全部仍为 false，最早揭盲时间仍是 2026-11-23 19:28:34.273 CST。
>
> 公共多场所 paper SOR 在旧 release 上连续被 Bybit public REST 的瞬态 TLS EOF
> fail-closed。新实现只对同一个公开 GET 的 `URLError`/timeout 做 0.25 秒、0.75 秒
> 两次有限重试；HTTP 响应、JSON/业务错误不重试，第三次传输失败仍 `BLOCKED`。
> source commit `1e86dd7c...` 经 canonical gate 27/27 PASS（core 6685 passed /
> 19 skipped，live/trading/tools 2886 passed / 2 skipped，frontend 199，Phase 13
> 24）；证据 release `fd5f31c4d5c53587aec3687c6a95a8c0cc655861` 在 ARM64 上
> 305/305 passed。DGX 随后的手工 canary 与正常定时 canary 连续两次 PASS：14 币双
> 场所真实公开盘口、4 intents、4 approved、0 rejected，且
> `strategy_edge_claim=false`、凭证/私有流/broker/下单/live 全为 false。
>
> 39 个非 Attention worker 的 78 个 systemd 文件和 4 个 special 文件已绑定
> `fd5f31c...` 并通过 `systemd-analyze verify`；Binance/Bybit resident stream
> 受控重启后分别以 PID 3462027 / 3462077 运行在该 release。冻结的 Wikimedia
> Attention 仍固定 `af41c762...`，service/timer SHA-256 保持
> `6f4a00e7...` / `6f65417e...`。一次手工 SOR 与 21:31 的 domain-event 投影重叠
> 触发 SQLite `OperationalError` 并留下 1 个待处理事件；append-only 链、consumer
> 链、coordination 链均有效，幂等 recovery 补跑后 pending=0、dead-letter=0、
> `domain_event_ops=PASS`。最终 watchdog 为 0 BREACH / 34 PASS / 9 PARTIAL /
> 3 UNAVAILABLE；三个 UNAVAILABLE 仍是 operator-owned heartbeat/event seal/
> artifact seal 未配置，backup 仍按自然时间积累纵向成熟度。系统保持
> `L2_DRY_RUN_ONLY`、`NOT_TOP_TIER 14/22`，本轮形成的是可审计的盲化研究过程，
> 不是已经盈利或可实盘的 Alpha。
>
> **2026-08-25 首个双场所强平机制候选已盲化预注册：** 研究对象不是新公式，而是
> “Bybit 被强平账户以急迫成交消耗本场所深度、受资本约束的 Binance/Bybit 跨场所资金
> 不能立即补平价差，强平静默与盘口恢复后超调回归”的可证伪机制。独立下注定义为一次
> instrument-side 强平簇加 30 分钟币种锁定；主候选与反号延续、去跨场所条件、无强平
> 急单、六小时错位时钟四个控制一次冻结。阈值、方向、60 秒持有期和控制均禁止事后替换。
> source commit `3c6c9d0d874128a966f209d5778d89bd710ef5e8` 的 canonical gate 为
> 27/27 PASS（core 6662 passed / 19 skipped，live/trading/tools 2878 passed /
> 2 skipped，frontend 199 tests + typecheck/lint/build，Phase 13 24 passed）；证据
> release `764e5b52dcc35755d36349e1f0b0d08c1f2ddec7` 在 ARM64 上的聚焦套件为
> 35/35 passed。该一次性 release 没有切换或重启已稳定运行的采集 fleet。
>
> DGX 于 `1787657314273` 只写一次
> `evidence/cross_venue_liquidation_preregistration_764e5b5.json`；preregistration ID
> `efc72d046b926fb47ace4c0b8b68fc40d8fe5116bf99508075ba65bde7ef1d02`，合同哈希
> `4a398eb8d95315a2110385eaec3ac944bfeaf6c5de7243c5ee59f38bec10681e`，artifact
> SHA-256 `e86ffcb6d44448b5034e5550fb6fb15124809749a78e847981c2577d8db72428`，
> 权限 `root:quantos 0440`；第二次写入由 `FileExistsError` 拒绝。最早揭盲时刻固定为
> `1795433314273`（2026-11-23 19:28:34.273 Asia/Shanghai），且还必须同时达到
> 60 个事件日、500 个非重叠簇、每币至少 75 个簇。揭盲前仅允许检查时钟、哈希链、覆盖率
> 和事件计数，禁止计算收益/Sharpe/候选比较；数据不足只能按可用性延长，不能按结果延长。
>
> 终局必须经过 `executable_value.v1`，相对最佳现有 L2 paper book 的增量净收益为正、
> 双倍成本存活、相关性不高于 0.60、执行置信度至少 0.80、容量至少 USD 1M，并胜过全部
> 四个控制；缺版本化费率表直接阻断 PASS。冻结后 global trial count 仍为 2860，
> `outcomes_accessed=false`、`return_statistics_computed=false`、eligible=0。Binance/Bybit
> resident streams 继续运行在 `23ca452d...`；检查时 Bybit authority 已有 194,750 条、
> 其中 32 条强平、重连 0、拒绝 0、状态 PASS。没有 Testnet/live、凭证、broker 或下单授权。
>
> **2026-08-25 机制优先 Alpha 第二切片已在 DGX 形成双场所前向权威：**
> 新增的 Bybit 公共 linear WebSocket 采集器只订阅 `orderbook.200`、逐笔成交、
> `allLiquidation` 与 ticker；它不读取凭证、账户或私有流，也没有 broker/委托能力。
> snapshot/delta 盘口重建、删除档位、成交 ID、清算方向/破产价/数量、source sequence
> 以及 event/receive/process 三时钟都进入批次原子、append-only 的 SQLite 哈希链。
> 历史回填信用被明确禁止，200 GiB 剩余空间门在启动前 fail-closed。
> source commit `083c1f21bf518376a0163ea82e78f518499f89f4` 的 canonical gate 为
> 27/27 PASS（core 6661 passed / 19 skipped，live/trading/tools 2865 passed /
> 2 skipped，frontend 199 tests + typecheck/lint/build，Phase 13 24 passed）；证据
> release `23ca452d5f8abc76bf289912d3ea1be66c4694f2` 在 ARM64 上的聚焦套件为
> 211/211 passed。76 个非 Attention worker 单元与 4 个 special 单元经
> `systemd-analyze verify`；79 个既有非冻结单元忽略 release SHA 后差异为 0，
> 安装后的 80 个渲染单元逐字差异为 0。Wikimedia Attention collector/timer
> 未被覆盖，SHA-256 仍为 `6f4a00e7...` / `6f65417e...`，并继续固定在
> `af41c762f82d14cd6ccebf00ec9408df21a2ea5f`。
>
> 首次切换因新 root-owned release 未加入 system Git `safe.directory` 而由入口身份
> 校验正确 fail-closed；这段失败 journal 与被波及的定时任务结果均未删除。补入这个
> 确切只读 release 路径后，Binance/Bybit 两个 resident stream 均恢复 active/running，
> 实际进程环境再次证明四个实盘开关为 false、人工审批为 true，release 精确绑定
> `23ca452d...`。新鲜 watchdog 为 35 PASS / 6 PARTIAL / 1 BREACH /
> 3 UNAVAILABLE；唯一 BREACH 仍是主机重启造成的备份恢复历史洁净窗未自然成熟，三项
> UNAVAILABLE 仍是 operator-owned 外部 heartbeat / 两个 seal 未配置。
>
> 部署观测时 Bybit authority 已从 6,094 条增长到 33,378 条（orderbook 15,134、
> trade 7,689、ticker 10,548、真实 liquidation 7），DB 51,372,032 bytes、重连 0、
> 拒绝 0、状态 PASS；创世链在 6,094 条处全量复核有效，33,378 条检查点/链头精确一致
>（head `6235e70a...9af066d9`）。DGX 审计
> `evidence/mechanism_alpha_audit_23ca452d.json` SHA-256 为
> `5c244ec2c3ad07674b2ea001c71842b6bbb2c270844bebfa7ff6652b0015c0c0`：数据面现在为
> 1 `HISTORICAL_READY` / 4 `PARTIAL` / 2 `FORWARD_ONLY` / 1 `MISSING`；
> `multi_venue_trade_l2` 与 `positioning_liquidation` 均达到两个场所，但正式
> `alpha_eligible_count` 仍为 0。下一阶段只在自然积累的盲化双场所事件上研究“被迫清算者、
> 急单与跨场所资金约束者如何付钱”的机制候选，并以增量净执行价值和独立下注次数判定；
> 不把短期连通性、公式 Sharpe 或刚上线数据冒充可盈利 Alpha。
>
> **2026-08-25 机制优先 Alpha 第一切片部署收口：** 研究入口不再允许只报公式与
> Sharpe；12 类方法现在必须在搜索前声明持续付钱者、持续性假设和独立下注单位，缺一
> 即拒绝。新增 `executable_value.v1` 以双倍成本后的增量净利润、与现有组合正交性、
> 成交可信度和容量作为正项，以换手和尾部损失作为惩罚；缺成本/容量/尾部/来源证据
> 一律 `INCOMPLETE`，评分只分配研究注意力，不授予 lifecycle、Testnet 或交易权限。
> 同时建立 8 个正交数据面能力审计，并把 Binance public USDM WebSocket 从事实上的
> 双时钟升级为 event/receive/process 三时钟；book/depth/RPI/mark/kline、成交窗口和
> 清算窗口都保留接收时钟，新写入行由 store fail-closed 校验，旧历史仍可读。
>
> canonical gate 在 source commit `91c00465cb87973bb5b6ee3fee80c743407761ce`
> 上为 27/27 PASS、0 failed（core 6648 passed；live/tools 2846 passed；frontend
> 199 tests + typecheck/lint/build；Phase 13 为 24 passed）；证据绑定后的不可变 ARM64
> release `7f40dbd0aaad107195878db983e7b8945459690e` 已部署 DGX，聚焦 ARM64 套件
> 137 passed。DGX 实际审计 artifact
> `evidence/mechanism_alpha_audit_7f40dbd.json` SHA-256 为
> `fb87b018a82f223212ec80e7a4fe6b0f78ae68f0edace2a9c681d9200cc6081b`；8 个数据面
> 只有 funding/basis 为 `HISTORICAL_READY`，5 个 `PARTIAL`，positioning/liquidation
> 为 `FORWARD_ONLY`，borrow/survivorship 为 `MISSING`，正式 Alpha eligible 仍为 0。
> 真实流尾行已证明 `event_time_ms <= received_at_ms <= processed_at_ms ==
> observed_at_ms`，成交窗口亦含首尾 event/receive 时钟；stream 于 17:23:07 CST
> 以 PID 3218170 在新 release 上运行，14/14 complete、双公共连接与追加链通过，
> 凭证/私有流/broker/下单/live 全为 false。
>
> 38 个普通 worker 与 resident stream/watchdog 已换到新 release；79 个渲染单元经
> `systemd-analyze verify` 且归一化差异为 0。冻结的 Wikimedia Attention 仍固定在
> `af41c762f82d14cd6ccebf00ec9408df21a2ea5f`，service/timer SHA-256 逐位保持
> `6f4a00e7...` / `6f65417e...`。新 watchdog 为 31 PASS / 9 PARTIAL / 1 BREACH /
> 3 UNAVAILABLE：唯一 BREACH 仍是重启后的备份恢复历史洁净窗未成熟；外部心跳和
> 两个外部 seal 仍未配置。下一切片不是继续堆公式，而是补第二交易场所的事件级逐笔/
> L2 前向权威，再研究跨场所资金约束者和急单付费者所支付的可执行弱优势。

> **2026-08-25 DGX 统一 L2 release 与风险包原子发布收口：** 不可变 ARM64
> release `bcea7a9a9cd351c18acd563791fcec484233d26a` 已部署；其父提交
> `09f408479e1dd4f7ec0ac30b21837cbbdb50eefe` 的 canonical gate 为 27/27 PASS、
> 0 failed，DGX 实际 `quantos` 用户的风险包/调度入口/watchdog/systemd 治理集合为
> 168 passed。38 个普通 worker、resident public stream 与 watchdog 已精确换到
> `bcea7a9a...`；冻结中的 Wikimedia Attention collector 仍单独固定在
> `af41c762f82d14cd6ccebf00ec9408df21a2ea5f`，service/timer SHA-256 分别保持
> `6f4a00e7...` / `6f65417e...`。79 个实际安装单元与审阅后渲染逐文件相同，39 个
> worker timer、watchdog timer 和 resident stream 均保持 enabled。stream 首次切换因
> 新 release 漏入 system Git `safe.directory` 而 fail-closed 并进入重启；补齐只读 release
> 身份验证后于 15:45:49 CST 以 PID 3113709 成功启动，最新语义健康为 PASS、14/14
> complete、双公共连接正常、0 reconnect、链与 cursor publishability 均通过；凭证、
> 私有流、broker、下单和 live 仍全为 false。启动失败 journal 未清除。
>
> 23:08 的旧 `paper_research_risk_bundle` 与多个大写 worker 并发，11GB
> `experiment_registry` 在 source-before/source-after 之间变化，attestation 正确失败；
> 但旧实现会在验证前覆盖 canonical，使现存 `f48cb16...` 不等于上次成功证明的
> `bbc61f99...`。现实现改为同目录唯一 staging，只有 `attestation.complete=true` 才
> `os.replace` 原子发布，失败必保留旧 canonical 并清理 staging。旧 canonical 已从消费
> 路径可恢复地移到 `evidence/quarantine/2026-08-25-paper_research_risk_inputs-unattested-
> f48cb16bec244b10.json`（完整 SHA-256 `f48cb16bec244b10e57d31ece77d726d2d1aa54a8b07a807e2eeb37aa55dbfa9`，
> `root:quantos 0440`），不是删除。随后只运行一次新 release：receipt
> `1787644126498-3140772-paper_research_risk_bundle.json` 证明 release verified、
> `worker_attested=true`、source-before=source-after
> `2810a7238beae4fad8a998493e4506a8674260330f5670c4080426c05cfa1a05`，发布输出
> SHA-256 为 `f48cb16...` 并与新 canonical 一致。
>
> 新 watchdog 已无 ACTION_DRIFT，汇总为 32 PASS / 8 PARTIAL / 1 BREACH /
> 3 UNAVAILABLE。唯一 BREACH 仍是备份恢复历史：65 次尝试、10 次成功、最新终态 PASS、
> overdue open=0，但 8 月 23 日 03:00 的备份被主机重启 SIGTERM，洁净窗仍含 1 次失败，
> 且 cadence/history span/success count 尚未自然成熟；不得 reset 或改阈值伪装健康。
> 三个 UNAVAILABLE 仍是 operator-owned HTTPS heartbeat、domain event seal 与 artifact
> admission seal 未配置。系统因此仍为 `L2_DRY_RUN_ONLY` / `NOT_TOP_TIER`，不构成盈利、
> Testnet 或实盘就绪声明。

> **2026-08-25 DGX ICT 流动性扫单前向协议冻结：** 把 ICT 的 practitioner liquidity
> sweep 语言与 Cont--Kukanov--Stoikov 的订单流失衡机制严格拆开：前者不是盈利证据，
> 后者也不验证 ICT 规则。系统冻结了 30 秒 PIT、60 分钟前高/低、同侧 taker imbalance、
> displayed-depth absorption、两分钟 reclaim、15 分钟持有、DST-safe 纽约时段及四个控制，
> 共 5 个候选，禁止阈值/时段/方向/持有期扫描。不可变 ARM64 release
> `c7fb7d8e34ff35f93560149c675534c426f9ae07` 通过 38 项聚焦测试；DGX 在
> `1787637742099` append-once 记录合同，preregistration ID 为
> `a735078a00fbc0510087db37394089f09b4d905be193a94d1d3cf999f60bbece6`，artifact
> SHA-256 为 `2e2fbb946e4b2f0d9e6969f09962db96c0ed7cefc8197068049279d5de07877d`。
> 冻结前最大知识时钟早 83.204 秒，旧帧全部不合格；二次记录被 `FileExistsError` 拒绝，
> 全局 trial 仍为 2860，未读收益、未算统计、未改 registry，eligible=0。最早时间门
> `1795413742099` 之后仍须同时满足 60 个事件日和 500 个非重叠事件；即使终局 PASS 也只
> 能进入人工 L2 paper 评审，不授予 Shadow/Testnet/live、凭证、broker 或订单权限。

> **2026-08-25 DGX lottery/MAX 低风险异象证伪：** 在 115 币、305 个非重叠周期间冻结
> 低 MAX 主假设、Top60/Top30、反号与双倍成本控制，并只运行一次正式研究。全局 trial
> 从 2855 精确增至 2860；主候选净 Sharpe -0.254、总收益 -48.18%、最大回撤 68.92%、
> DSR `1.43e-10`、PBO 0.40，末段也为负；高 MAX 反号同样亏损，Top30 的弱正收益在双倍
> 成本下转负，全部 evaluator verdict 为 FAIL。不可变 artifact
> `crypto_weekly_lottery_max_34d5792.json` SHA-256 为
> `d8ad393e9b3565efcc29b9717a5984490483d8eeab11505c59998df76f318eea`，二次运行被拒绝。
> 方法论已标记 `EXISTING_FALSIFIED_IMPLEMENTATION`，不得降门槛或重复挖掘；这轮结果缩小
> 了错误搜索空间，不是盈利 Alpha，也没有触碰任何实盘边界。

> **2026-08-12 实时特征双时钟新鲜度修复（Task 39 继续锁定）：** 运行观察发现实时特征
> worker 在重负载周期约需 13--17 分钟，但旧状态把周期开始时间写成 `generated_at_ms`，
> 导致刚完成的 PASS 可能立即被下游按 15 分钟阈值判旧。现以完成时钟发布状态，同时由
> 共享 fail-closed 契约独立要求公开源 `last_observed_at_ms` 也在同一新鲜窗口内；因此刷新
> 状态文件绝不能掩盖过期行情。工程 Shadow 和 Execution Paper 都消费这一契约并报告
> 机器原因码，过期源保持 `SAFE_BLOCKED`、不产生决策或执行。状态新增九段只读耗时证据，
> 用于在下一不可变发布运行中定位余量偏小的真实阶段。71 项实时 Alpha 链路回归与治理
> 扫描（508 文件、0 违规）通过。开发分支尚未部署；正式 eligible 仍为 0，Testnet intent、
> 执行服务和七天 soak 均未启动，未读凭证、未连私有账户、未提交订单。

> **2026-08-12 book-depth 盲确认终局（Task 39 继续锁定）：** 丢失的确认数据权威没有被
> 伪造回填。系统从冻结的 97 天 Binance Data Vision 公共、无密钥、checksum-verified
> 源重建一条明确不同于旧链的新权威：97/97 天处理、91 天完整、冻结的 6 天质量排除、
> 30,576 条观测、84 个相邻 24h 决策，链校验通过；Alpha 结果在重建 PASS 前从未访问。
> 结果前又冻结了正式 validation DAG 的全局试验计数、负向控制和阈值，并修复 DAG
> 对缺失可比收益历史的错误要求：缺失历史现在仍按独立 trial 全额计入 DSR 惩罚，绝不
> 因缺证据获得折扣。随后唯一一次确认得到 `FAIL`：净 Sharpe 0.637、双倍成本净 Sharpe
> -0.389、rank-IC t=0.469、仅 1/3 时间段盈利；只有净均值、IC 符号和单币集中度通过。
> 结果已不可变落在 `evidence/binance_book_depth_alpha_confirmation.json`，再次运行被拒绝；
> 正式 DAG 没有启动，formal eligible 仍为 0，未生成 Testnet intent、未启动执行服务或
> 七天 soak，未读凭证、未连私有账户、未提交订单。既有 calendar-basis、3d positioning
> consensus、volume-acceleration 与本次 book-depth 均已诚实失败，不能重跑或降门槛。
> 下一条研究路径必须来自新的独立观察/数据面；当前优先方向是 Binance 官方公开、无密钥
> 的 RPI/主动买卖订单流，但其历史尚需从预注册后的自然时间积累，不能事后制造前向信用。

> **2026-08-10 Alpha 正式准入复核（Task 39 保持锁定）：** 本轮按预注册口径完成三条
> 相互独立的候选路径，均未取得正式 PASS。① Binance 交割合约/永续的 calendar-basis
> 收敛假说在 26 个合约、156 周上为 `FALSIFIED`（净 Sharpe -0.177，双倍成本后仍负）；
> ② 三日 positioning consensus 修复了候选间 PBO 日历不一致后，在 458 个统一周期上
> `VERIFIED/FAIL`（候选净 Sharpe 2.395、DSR 0.00227、PBO 0，正式样本 n=457）；
> ③ 从旧搜索冻结的 14 个 volume-acceleration finalist 以同一 2206 日历、6bps 成本、
> 完整 leakage/regime/walk-forward/DSR/PBO 门重放，14/14 `FAIL`；预选 subject 的净
> Sharpe 0.724、DSR 4.23e-46、PBO 0.257，当前全局试验数 2751。旧高 DSR 不能在
> 完整家族和当前 multiple-testing 权威下复现。本轮没有调低阈值、成本或改窗追结果，
> 也没有为失败候选补造前向信用。正式 eligible 仍为 0：未生成 Testnet intent，未启动
> 执行服务或七天 soak，未读取凭证、连接私有账户或提交订单。下一条诚实路径是取得新的
> 独立观测/数据面并预注册新假说；在此之前 `OPERATIONAL_SOAK_PENDING` 和
> `L2_DRY_RUN_ONLY` 保持不变。设计与复核记录见
> `docs/engineering/specs/2026-08-10-binance-calendar-basis-alpha.md`、
> `docs/engineering/specs/2026-08-10-positioning-consensus-3d-admission.md`、
> `docs/engineering/specs/2026-08-10-volume-acceleration-confirmatory-admission.md`。

> **2026-08-09 Binance public realtime chain verification scaling:** production
> observation on the 1.85M-row public snapshot authority showed the stream
> worker repeatedly starting from genesis, consuming a CPU core for minutes and
> delaying five-second flush/recovery work enough to aggravate depth sequence
> gaps. The source store already supports checkpointed suffix verification and
> the append-only realtime feature-batch authority already carries a full-audit
> checkpoint with a 24-hour expiry. The stream now verifies that batch authority,
> anchors the exact source row hash, and checks only the new suffix; missing,
> malformed, expired, or mismatched checkpoints still force a full fail-closed
> audit. The same production probe exposed a second full scan in the 1.2M-event
> durable depth-cursor chain. Its initial genesis audit now runs in an isolated
> headless subprocess so it cannot starve WebSocket ingestion; after bootstrap,
> each flush verifies only the exact suffix and requires the verified seq/hash to
> equal the current cursor frontier before publication. Audit pending, invalid,
> malformed, or stale-frontier states remain blocked. A real-state read-only source
> probe verified 42 new rows after seq 1,850,202 in 0.017s with a valid chain.
> This changes only credential-free public market-data
> integrity throughput: private access, broker connectivity, order submission,
> and live trading remain false. Production may consume the fix only through a
> newly validated immutable release.

> **2026-08-09 Binance depth recovery restart continuity:** a production
> acceptance probe found two durable depth cursors left in `RECOVERING` after a
> worker restart. The flush loop retried `GAP_DETECTED` and `BLOCKED` cursors but
> omitted this persisted in-flight state, so those cursors could remain
> unpublished indefinitely. A restarted worker now resumes `RECOVERING` through
> the existing idempotent recovery journal path. Snapshot failure, an incomplete
> bridge, or any cursor inconsistency still returns the cursor to `BLOCKED`; no
> unverified depth state becomes publishable. The change is limited to public
> market-data continuity and does not enable credentials, broker access, order
> submission, or live trading.

> **2026-08-09 Realtime feature archive constant-time retained count:** runtime
> acceptance measured each realtime-feature cycle spending roughly 7--8 minutes
> reopening and exactly counting the 13.5M-row `feature_values` archive. The
> store now seeds one exact count for a legacy archive under an atomic write
> transaction and installs an `AFTER INSERT` counter in the same database.
> Successful inserts advance the counter transactionally; `INSERT OR IGNORE`
> duplicates do not. Subsequent opens take a read-only fast path and
> `row_count()` reads one singleton row rather than rescanning the archive.
> Missing or invalid metadata blocks both reads and inserts, including after a
> restart; it is never silently rebuilt once initialized. PIT values, append-only
> retention, drift policy, and all L2 trading boundaries remain unchanged.
> Immutable release `3eb0b7f` completed the one-time 13.6M-row migration in
> 652s; the immediate 510s follow-up was still processing accumulated catch-up
> work. Subsequent five-minute scheduled cycles became the representative
> steady state: 5,712--6,384 new observations per cycle in roughly 17--18s. A
> direct real-state probe confirmed
> 13,624,530 rows open in 1.5ms and count in 9ms, so the counter itself is
> deployed and the recurring archive rescan bottleneck is closed. Read-only stage timing
> measured watermarks at 46.3s, trailing 98-series PIT reads at 0.33s,
> calibration at 0.23s, and source verification/projection below 1.3s. Batches
> are also stably ordered by the shared primary-key prefix before `executemany`,
> preserving equal-key first-value-wins semantics while reducing random page
> churn. Immutable release `76a8007` completed its first fully attested sorted
> production cycle with 5,928 new observations in 17.1s. This is not a material
> incremental speed-up over the post-counter steady state; the persistent exact
> count is the measured fix, while ordering is a bounded locality safeguard.
>
> **2026-08-09 governed completeness evidence restored to the console:** the
> converged console keeps `/research/system-completeness` mapped to the global
> governance drawer, but the drawer now consumes the read-only
> `/api/v1/console/system-completeness` assessment directly. It displays the
> governed verdict, exact requirement counts, red lines, evidence paths and next
> controlled actions without calculating a completion percentage. Any missing
> backend snapshot or provenance other than `TOP_TIER_EVIDENCE_ASSESSMENT`
> fails closed as `SYSTEM_COMPLETENESS_UNVERIFIED` and shows no counts or verdict.
> Frontend unit tests (198), typecheck, lint, production build, focused browser
> accessibility/route acceptance, 24 governance composition tests, and the
> 499-file governance scan pass. A clean-source assessment now marks
> `FRONTEND.EVIDENCE.SINGLE_SOURCE` M4/PASS and raises the repository result from
> 14/22 to **15/22 PASS**; remaining non-pass items require external authority,
> licensed/provider evidence, longer operating windows, or post-L2 scope.

> **2026-08-09 L2 备份并发修复：** 当前 11.2 GB
> `experiment_registry.db` 使用 DELETE journal；旧备份一次性调用 SQLite online
> backup，正式独立评估在提交搜索尝试时耗尽 5 秒 busy timeout 并 fail-closed。
> `ca67097` 现把在线复制限制为每步 256 页，并在未完成步骤间主动让出写入窗口；
> 真实 DELETE-journal 并发回归证明备份过程中 writer 可提交，恢复完整性和 source-contract
> 校验保持不变。40 项备份/恢复聚焦回归、干净工作树顶级评估复核和治理扫描均通过。

> **2026-08-09 WP0 Release Evidence Same-Run Binding:** `scripts/validate.py`
> now publishes `evidence/l2_release_evidence_manifest.json` only after the full
> canonical gate and Phase 13 pass. The manifest binds the stable source anchor,
> full validation report, top-tier assessment, SBOM, container vulnerability and
> reproducibility proofs, plus every currently present canonical generated artifact.
> L2 scheduler receipts are schema v3 and verify this manifest before importing any
> worker; source/artifact/cross-link drift fails closed. This closes the locally
> achievable container/SBOM and derived-evidence binding item. External signing,
> WORM retention, and independent witness remain explicit WP5 external boundaries.
> The final `paper_operational_evidence` immutable run from `c1e8eb8` retained a
> complete 5-input/2-output worker attestation and valid chain. Its outer exit 2 is
> the expected `MATURING` business result, not a release-verification failure.
> The first detached-checkout rehearsal correctly exposed Windows CRLF-to-LF
> normalization in three tracked text artifacts. A fresh-worktree regression now
> requires manifest identity to survive checkout; text hashes use the repository's
> declared LF form while semantic supply-chain cross-links and binary bytes remain exact.

> **2026-08-09 WP0 Paper Operational Evidence 发布运行证明第十一增量：**
> `paper_operational_evidence` 已接入强制 release-run attestation。证明绑定唯一 Shadow 候选全集、
> 预注册 5 分钟/30 分钟 smoke/24 小时 Production 窗口合同、实际消费且运行前后稳定的最新
> `paper_shadow_realtime` release receipt + Shadow 制品、6 项隔离故障演练的逐项结果，以及运行前后
> 追加式运维证据链权威；输出绑定原子报告文件。上游回执/制品在收集中变化、任一演练失败、链损坏或
> 安全声明漂移都会使证明不完整。真实 `H:\QUANTOS_STATE` 隔离开发冒烟写入 1 个周期 + 6 项演练，
> 链有效、窗口诚实为 `MATURING`、5 input + 2 output hashes，安全五项通过；146 项关联回归通过，
> 首个不可变发布 `959f66c` 实跑暴露并拒绝跨 release 重放缺口：同一上游 receipt 的事件 ID 不变，
> 但 collector release 字段变化，追加链以“同 ID 不同内容”阻断。现已补失败测试并修复为周期不重复计数、
> 首次观察者审计字段不改写、新 release 仅追加自己的隔离演练；比较只忽略 collector commit，制品/对账/
> 安全/业务字段仍全部承重。仍须关联回归、全量门禁和新不可变发布正式复验。上一风险包增量已在不可变发布 `c6b3c8c` 正式运行：receipt
> `1786255743055-52584-paper_research_risk_bundle.json` 为
> `COMPLETED/exit=0/worker_attested=true`，37 个候选、4 input + 2 output hashes，13 类源权威
> 前后哈希相同；业务 37 个仍全部 `PENDING`，执行授权/订单/live/凭证/broker 均为 false。

> **2026-08-09 WP0 Paper Research-Risk Bundle 发布运行证明第十增量：**
> `paper_research_risk_bundle` 已接入强制 release-run attestation。证明绑定唯一候选全集、固定九引擎/
> Paper AUM/FX 新鲜度/前向成熟度合同、运行前后逐字节一致的 13 类源权威（前向账本、静态与独立评估、
> 前向状态、FX、维护、PIT 行情/OI/资金费/宏观库、实验注册表，SQLite 同时绑定未 checkpoint 的 WAL），
> 以及最终逐候选风险输入视图和原子发布文件；任一源在计算中变化都会使证明不完整并被 launcher 拒绝。
> `PENDING` 是可证明的业务阻断而非生产作业失败，缺失输入仍逐项保留，绝不由默认金额或合成风险数据
> 填充。真实 `H:\QUANTOS_STATE` 开发冒烟完成 37 个候选、`PENDING`、4 input + 2 output hashes，安全
> 五项全通过；253 项关联回归及 27/27 全量门禁通过，并已由上方 `c6b3c8c` 完成正式周期复验。上一增量 Paper Preview
> 已在不可变发布 `b81335d` 正式运行：receipt
> `1786253517162-58444-paper_order_preview.json` 为预期 `FAILED/NONZERO_EXIT/exit=2`（业务
> SAFE_BLOCKED 的统一外层记录）、`worker_attested=true`，9 input + 2 output hashes；37 个候选均被
> 工作流/证据门阻断，0 意图、0 订单，Paper 账本前后权威哈希一致且无凭证、broker 或 live 权限。

> **2026-08-09 WP0 Formal Paper Order Preview 发布运行证明第九增量：**
> `paper_order_preview` 已接入强制 release-run attestation。证明绑定唯一候选全集、固定
> Paper sizing/risk/时钟合同、实际 mark 视图、工作流准入、逐候选前向决策查询、九引擎 bundle
> 消费视图、实际 Binance 公共合约过滤器、辅助状态文档，以及运行前 Paper 账本权威；输出绑定
> 运行后追加式 Paper 账本权威和最终报告。业务 `paper_preview_ready=false` 仍是诚实阻断，证明完整
> 只表示“本轮输入和阻断可复核”，不等于候选、九引擎或模拟成交通过。公共实时行情已在不可变
> 发布 `49de569` 连续两轮正式通过：14/14 标的、0 失败、39,433+ 行且链有效，3 input +
> 3 output hashes、`worker_attested=true`。Paper Preview 增量已通过 327 项相关回归，并已由上方
> `b81335d` 不可变发布完成正式 SAFE_BLOCKED 周期复验；始终无凭证、broker、订单或 live 权限。

> **2026-08-09 WP0 Binance Public Realtime Market 发布运行证明第八增量：**
> `binance_realtime_market` 已接入强制 release-run attestation。输入绑定完整公共 REST
> 响应 bundle（含 URL 与逐标的响应）、固定采集/新鲜度/归档合同，以及运行前已验证行情账本
> 链头；输出绑定本轮 content-addressed raw-archive 回执、运行后账本链头与状态文件。既有链只在
> 周期入口完整验证一次，追加后从可信 checkpoint 验证后缀，避免证明机制把每分钟任务变成两次
> 全链扫描。Formal Blinded Shadow 已在不可变发布 `55aa07d` 正式运行通过：
> `COMPLETED/exit=0/worker_attested=true`，24 个候选、208 个 formal 账本事件、链有效且
> selector feedback=false。行情增量已通过聚焦回归，仍须新不可变发布的正式公共数据周期复验；
> 全程仅公共数据、无凭证、无 broker、无订单、无 live 权限。

> **2026-08-09 WP0 Realtime Alpha Formal Blinded Shadow 发布运行证明第七增量：**
> `realtime_alpha_formal_shadow` 已接入独立的强制 release-run attestation。
> 它复用确定性的帧/标签实现，但只读取自己的 formal authority 证明并写入物理隔离的
> `realtime_alpha_formal_shadow.db` 与报告；证明绑定精确 discovery/feature-status 文件、
> 固定周期与 formal authority 政策、运行前 formal-shadow/family 权威、实际消费的每段
> realization/decision PIT 帧，以及运行后两条权威和 formal 报告文件。launcher 现在对
> formal 模块 fail-closed，且不能借用 engineering-shadow 的证明。Forward Shadow 已在
> 不可变发布 `535b22e` 正式运行通过：`COMPLETED/exit=0/worker_attested=true`，24 个候选、
> 192 个账本事件、哈希链有效。Formal 增量已通过聚焦回归，仍须新不可变发布的正式调度实跑；
> 两条路径均严格 paper-only，live/order/credentials/broker 全 false。

> **2026-08-09 WP0 Realtime Alpha Forward Shadow 发布运行证明第六增量：**
> `realtime_alpha_forward_shadow` 已成为第六个强制 release-run attestation worker。输入绑定
> discovery/feature-status 文件、固定周期/authority 政策、运行前 engineering-shadow 与 family
> 权威，以及实际消费的每段 realization/decision PIT 帧；输出绑定运行后两条哈希链权威和报告
> 文件。WAITING/SAFE_BLOCKED 同样只能证明本轮精确输入，异常入口先清旧证明；formal shadow 仍用
> 独立物理账本和独立模块，未被本增量混入。paper-only 且 live/order/credentials/broker 全 false。
> 实现已完成聚焦回归，仍须新不可变发布的正式调度实跑才能记为 runtime 通过。
>
> **2026-08-09 WP0 Realtime Alpha Discovery 发布运行证明第五增量：**
> `realtime_alpha_discovery` 已成为第五个强制 release-run attestation worker，补上首个
> selection-bearing feature→candidate 接缝。输入绑定解析后的 feature 状态、精确 coverage、
> bounded executable-depth 原始视图、实际 PIT 决策帧、固定研究/统计门政策，以及在单一 SQLite
> 读快照中冻结的 family identity/engineering-feedback 视图；持久化试验时还绑定同一快照取得的
> 全局 trial/历史收益上下文。输出绑定运行后 family-registry 权威、当轮 search/candidate/
> provenance/evaluation 制品和报告文件。成功探索与诚实 collection/block 都只能证明本轮输入，
> 入口先清空旧证明；paper-only 且 live/order/credentials/broker 全 false。WP0 仍未整体闭合。
> 首次正式发布实跑同时暴露并拒绝了一个 30 分钟 runtime 超时：尾部帧 SQL 在选出完整快照后
> 又无界自连接 1340 万行特征表。现改为单次条件聚合，新增 SQLite 指令预算回归；真实 10 分钟
> 尾部查询约 60s→0.006s，24 候选无持久化整轮约 82s→21s。新不可变发布 `63ffedf` 已在正常
> L2 调度负载下正式复验：receipt `1786245639004-33012-realtime_alpha_discovery.json` 为
> `COMPLETED/exit=0/worker_attested=true`，精确 release，7 input + 3 output hashes，约 107 秒完成；
> 本增量 runtime 验收现已通过。

> **2026-08-09 WP0 Binance 实时特征发布运行证明第四增量：**
> `binance_realtime_features` 已成为第四个强制 release-run attestation worker，并补上
> 实时 Alpha 链的首个上游 data→feature 接缝。本轮证明绑定实际读取且已验链的公共 WebSocket
> 快照 bundle、源链验证视图、运行前 feature-batch 权威、固定物化/漂移合同，以及校准实际消费的
> bounded PIT 特征视图；输出绑定追加式 feature-batch 权威、特征投影前沿和状态文件。worker
> 在读取任何可变权威前先清空旧证明，失败不能沿用上次 PASS；release commit、paper-only 和
> live/order/credentials/broker 全 false 由 launcher 逐项 fail-closed。其余承重 worker 仍按真实
> 消费合同逐个接入，WP0 尚未宣称整体闭合。

> **2026-08-09 全量原始归档审计吞吐闭合：** 现场换版验收暴露
> `QUANTOS_raw_archive_audit` 随归档增长已逼近并在最新一轮触及 2 小时任务上限；
> 根因不是校验弱化需求，而是 62 万条 observation 重复重型解析同一批公共 source URI，
> 随后又串行读取/哈希约 46 万个唯一内容对象。现保留每条 observation digest/schema/
> source URI/size 与每个 object digest 的完整验证，只对完全相同且已验证的 URI 使用 4096 项
> 有界缓存，并把唯一对象校验改为 8 路、2048 项批次的有界并行；错误输出仍按 observation
> 路径确定性排序，索引锚只在零错误时刷新。真实 `H:\QUANTOS_STATE` 全量运行
> **624452 observations / 0 invalid / 746703ms（约 12 分 27 秒）PASS**，相对上一完整成功轮
> 5996164ms（约 100 分钟）约 8 倍提速，重新落入 2 小时硬上限内；仍是全量审计，未改成抽样。

> **2026-08-09 实时特征 bounded-clock-skew PIT 接缝修复：** 新不可变 release 的
> Execution Paper 运行证明验收正确暴露前置特征 worker 被
> `snapshot violates PIT ordering` 阻断。根因是 WebSocket collector 与权威存储已允许并记录
> 30 秒内交易所时钟领先，并把 `knowledge_time_ms=max(observed,event)` 作为保守可知时刻；特征
> projector 却忽略该字段，错误地把本机 `observed_at_ms` 当 knowledge time。现 projector 消费
> 权威 `knowledge_time_ms`，仅在 `exchange_clock_skew_ms` 与固定 30 秒 source contract 逐项匹配
> 时接受领先事件；缺少证明、超界或 knowledge 仍早于 event/observed 均 fail-closed。该修复不
> 放宽新鲜度、PIT 或任何 L2 安全边界。

> **2026-08-09 WP0 Realtime Execution Paper 发布运行证明第三增量：**
> `realtime_alpha_execution_paper` 成为第三个强制 release-run attestation worker。Signal
> Shadow 的 admission/decision 现在在单一 SQLite 读事务中物化，证明绑定完整 source authority
> hash，避免同一轮多次查询看到不同追加进度；同时绑定本轮实际解析的 feature/contract 状态、
> 执行账本运行前 authority、固定执行合同和实际消费的公共 routing snapshot。输出绑定运行后
> Execution Paper 双链 authority 与报告文件。`RUNNING` 和诚实 `SAFE_BLOCKED` 都只能证明本轮
> 输入，启动即清空旧证明；live/order/credentials/broker 继续全 false。其余 worker 尚待逐个
> 接入，WP0 不宣称整体闭合。

> **2026-08-09 WP0 Paper SOR 发布运行证明第二增量：**
> `paper_multivenue_sor` 现与唯一 Alpha 工作流同列为不可变 L2 launcher 的强制证明
> worker。每次成功运行必须把完整公共跨场所盘口 bundle、独立可重算的 book snapshot、
> 固定路由输入合同（全量/金丝雀 universe、notional、新鲜度/偏差上限、费用假设）绑定为
> 输入哈希，并把状态文件和 dry-run reproducibility hash 绑定为输出哈希；release commit
> 与 `paper_only=true`、live/order/credentials/broker 全 false 逐项校验。新运行开始前先清空
> 旧证明，失败重试不能沿用上次 PASS。缺失、畸形、commit 不符、快照不可重算或安全声明
> 漂移均由 launcher fail-closed。其余尚未逐个接入的 worker 仍诚实标记
> `worker_attestation_required=false`；WP0 尚未宣称整体闭合。

> **2026-08-09 Paper SOR 校验后时钟重锚修复（开发分支已验证，待不可变发布）：**
> 现场 `QUANTOS_paper_multivenue_sor` 在大型公开行情权威库上先做完整哈希链复核，
> 却继续用复核开始前的 `now_ms` 选择 Binance 快照，再以复核结束后的墙钟标记 Bybit
> 快照，稳定制造 `cross-venue quote skew is too large`。现于链复核成功后重新锚定同一
> 注入时钟，不放宽 10 分钟新鲜度/跨场所偏差阈值。回归 20/20 PASS；对真实
> `H:\QUANTOS_STATE` 公开行情库完成一次只读验证（14 标的、28 books、235.6 秒、
> `binance_rest_failover_used=false`、PASS），全程无凭证、账户或订单能力。当前调度器仍
> 锁定不可变 release `1116c7bda144b620c622eb9d69a1b970181ccfa1`，必须在本修复通过
> canonical gate 并形成新的 clean release 后才能重注册，不能原地改 release。
> 同期 Binance systemic-risk 公开 REST 收到 HTTP 418 并进入约 73 分钟持久 cooldown，
> 必须等待而不能绕过或重试放大；`alpha_production_workflow` 当前 37 个候选、0 个
> Execution-Paper eligible，因此没有可诚实生成的 Task 39 intent/runtime 金额提案。

> **2026-08-08 Wave 3 专业工作台 L2 实施验收：** Tasks 40–50 已形成五对象工作区
> （Market Monitor、Alpha Control Plane、Validation Evidence、Portfolio & Risk、
> Execution & Orders）和治理/审计/健康/任务全局抽屉。源提交
> `f29f69be64073efd49c7f3bcc7966196e77fb410` 的 canonical run
> `1da6170a3f25c7719c10fd0c3a14125f13c1fa37b56c57379d9bdf92e61789f1`
> 为 **27/27 PASS**（0 failed / 0 errored / 0 timed out，964437ms），包含全后端、
> live/tools、前端 typecheck/eslint/Vitest/build、供应链、top-tier 与 Phase 13。
> Playwright 为 14/14，其中专业工作台 11/11；unknown schema、missing field、stale、
> cursor gap、429、timeout、SSE 断流和空结果均显式失败关闭。另以
> `VITE_MOCK_MODE=false` 实际启动 `services.console.main:app` 与 Vite：当前开发
> worktree 缺少 MFT/workflow 权威库时，五工作区全部诚实显示 `UNAVAILABLE` 并隐藏运营
> 数字，治理抽屉验证 L2 契约且显示 `EXTERNAL IDENTITY PENDING / COMMANDS DISABLED`。
> 因外部 OIDC/mTLS/MFA/应用授权未配置，真实晋级命令未执行；Task 39 Testnet 运维 soak
> 也未启动，继续保持 `OPERATIONAL_SOAK_PENDING`。readiness 仍为
> `L2_DRY_RUN_ONLY`，五个真钱安全旗帜逐项不变。机器证据：
> `evidence/workbench_wave3_acceptance.json`。

> **2026-08-06 WP0 每轮输入/证据原子性第一增量：** 不可变 L2 launcher 的
> release-run receipt 从 schema v1 升为 v2，新增可强制的 worker attestation
> 协议；`alpha_production_workflow` 作为唯一候选权威成为首个强制模块。每次成功或
> 安全阻断的调和运行现在必须同时绑定 15 个实际消费的逻辑来源哈希（候选身份集、
> attested 前向兑现窗口及 13 类证据文档）、2 个预注册政策哈希、工作流 Population/Event
> 双链头和最终 projection 文件 SHA-256；缺失、畸形、release commit 不一致或 L2 safety
> 声明漂移均由 launcher fail-closed，不能留下“代码已验证但输入不明”的成功回执。对
> `H:\QUANTOS_STATE` 的只读输入/临时输出冒烟得到 37 个候选、`VERIFIED`、17 个输入哈希、
> 3 个输出哈希且 attestation complete；`paper_only=true`、`live_allowed=false`。本切片不
> 宣称 WP0 闭合：其余 worker 仍明确记录 `worker_attestation_required=false`，需按实际消费
> 合同逐个接入；外部签名、WORM 和独立见证仍属后续外部边界。

> **2026-08-06 Paper P0 调度契约与工程 Shadow 连续性修复：** 现场 watchdog
> 的两个 `UNEXPECTED_ENABLED_TASK` 根因是 `QUANTOS_alpha_lifecycle_cockpit`
> 与 `QUANTOS_realtime_alpha_formal_shadow` 已由统一注册脚本启用，但未同步进入
> `EXPECTED_TASKS`；现已按真实 5 分钟/1 分钟 cadence、模块名和安全阻断退出码绑定，
> 未知任务仍保持 fail-closed。另一个现场 `SAFE_BLOCKED` 根因是正式盲化 Shadow
> 引入显式 `authority` 后，既有工程 Shadow 的不可变 admission 记录没有该新增字段。
> 兼容边界只接受“`authority` 字段确实不存在、paper class 精确为旧工程 Shadow、且
> 不含任何 formal-blinded 权限字段”的旧记录；正式盲化账本仍必须显式绑定独立
> authority，不能借此兼容。真实 `H:\QUANTOS_STATE` 账本 22/22 admission 通过该边界，
> 现场周期从 `SAFE_BLOCKED` 恢复为 `RUNNING`，新增 1 个决策和 1 个实现；全程仅公开
> 行情、无凭据、无 broker、无下单、无晋级或 forward-evidence credit。

> **2026-08-05 independent formal blinded shadow:** the realtime candidate
> handoff now has a second L2-only forward authority. It reuses deterministic
> public-frame/label primitives but writes a physically separate append-only
> `realtime_alpha_formal_shadow.db` and JSON artifact. The canonical workflow
> consumes this artifact for the realtime `SIGNAL_SHADOW` gate, while the
> adaptive selector filters strictly to `ENGINEERING_SHADOW_REALIZED` events;
> formal outcomes cannot steer candidate mutations. The console SSE exposes a
> `formal_shadow` sub-report per factor and the detail view shows its live
> RUNNING/MATURE state. No maturity, promotion, credential, broker or order
> authority is inferred from the new track.

> **2026-08-05 SOR freshness self-coordination:** the one-minute realtime execution-paper worker now refreshes the append-only public cross-venue order-book snapshot when the five-minute SOR artifact is missing or stale, then reloads and revalidates the full venue/timestamp/hash contract against the refresh-completion clock (so a slow public fallback is not misclassified as future). Its scheduled execution window is 5 minutes with single-instance `IgnoreNew` overlap protection. Refresh failure remains `SAFE_BLOCKED`; the refresh never runs a canary, touches credentials, or submits orders.

> **2026-08-05 realtime feedback structural challenger:** when a positive-UCB
> frozen root already has both adjacent horizons registered, the bounded
> exploration allocator now emits a deterministic `SIGN_FLIP` challenger at
> the same horizon instead of ending with an ambiguous `NO_NEW_TRIAL`. The
> challenger keeps generation-1 root lineage, receives the latest realized
> feedback cutoff, re-enters the append-only registry and forward-shadow
> handoff, and remains exploration/paper-only; it cannot alter formal gates,
> promotion, credentials, or order authority. Existing horizon challengers
> and adaptive-child feedback consumption remain unchanged.

> **2026-08-05 主纸面逐因子生命周期报告（前端实时接缝）：** 主纸面 `paper_realtime` 的每个因子现在都可以在纸面驾驶舱中点击展开生命周期报告，逐项显示研究回测、验证、纸面标记、前向成熟度、实时净 PnL、成交/持仓、资金费/成本、保留权益曲线和阻断原因；默认选中已实现亏损因子。报告通过现有 SSE 只读快照刷新，`PAPER_PENDING` 且无成交的因子保持 `NO_REALIZED_PNL`，不再把初始权益或 0 PnL 误显示为盈利。主纸面与工程 Challenger 继续分栏，所有 live/order flags 仍为 false。

> **2026-08-04 实时纸面生产 v18（缓存端点覆盖修复）：** 决策帧缓存命中条件现在同时要求缓存覆盖本次请求的 `end_ms`；即使缓存仍在 15 分钟允许滞后窗口内，只要 Binance 特征推进了新的决策桶，就会执行有界尾部刷新。此前“缓存未过期但落后当前端点”会让候选只有 admission、没有新决策；新增回归测试锁定该行为。刷新仍只消费 PIT 完整七特征，缓存不成为晋级或下单权限。

> **2026-08-04 实时纸面生产 v17（影子纸面复用决策帧缓存）：** forward-shadow 的未来标记和最新决策读取统一复用 `realtime_decision_frames.db`，不再对 22 个实时候选逐个重新扫描数百万条原始特征。缓存缺失、历史不全或超过新鲜度窗口时仍回源 SQLite 重建；缓存仅为派生加速层，不是晋级证据。该接缝把“发现已完成、影子纸面迟迟不出结果”的运行时瓶颈纳入全自动流水线，并保持 PIT、append-only 账本、成熟度门和 live/order flags=false。

> **2026-08-04 实时纸面生产 v16（首次启动竞态修复）：** 新 release 首次并行触发 forward-shadow 与 execution-paper 时，执行侧可能在 shadow 账本 schema 提交前打开一个 0 字节 SQLite 文件，导致两条链都安全阻断。现在读侧不会为缺失账本制造空文件；生产侧只恢复 0 字节的中断初始化产物，非空损坏账本仍 fail-closed。执行纸面账本采用同一恢复边界，并以测试覆盖冷启动竞态。该修复不删除或覆盖任何已有事件，也不改变纸面隔离、成熟度门或 live/order flags=false。

> **2026-08-04 实时纸面生产 v15（决策帧缓存与发现提速）：** `realtime_alpha_discovery` 现在由 SQLite 在存储侧选择每个交易对/决策桶的最新完整 PIT 快照，只把选中的 30 秒决策帧送入 Python；新增可替换的 `realtime_decision_frames.db` 派生缓存，热读不再重复扫描数百万条原始特征。真实状态库实测热读约 0.35 秒、66,770 帧；24 候选的临时评估约 78 秒。缓存不是证据，不驱动晋级或下单；缺历史、过期或不完整快照仍会 fail-closed 回源重建，`event_time <= knowledge_time` 与完整七特征约束保持不变。此次只改善全自动探索的吞吐和新鲜度，不改变 L2 纸面边界及 live/order flags=false。

> **2026-08-04 实时纸面生产 v14（全链审计不再被周期性强杀）：** 真实解释器重绑后进一步
> 暴露 `binance_realtime_features` 的 10 分钟 `ExecutionTimeLimit` 小于 526 万行每日全链审计
> 的现场耗时；Task Scheduler 会在每轮即将完成前硬终止 worker，留下永久 `STARTED` 回执，
> 下一 cadence 又从头审计。该任务现保留 10 分钟触发频率与 `IgnoreNew` 单实例策略，但硬上限
> 调整为 45 分钟：日常增量仍按原 cadence 秒级执行，每日全审计可连续完成且期间不重叠。
> 纸面驾驶舱同时把 canonical workflow 阶段、因子家族、定义种类、成熟度和 blocker 合并进
> 34 个主纸面账户的实时 SSE 业绩行；最新权威实况为 1 PROFIT / 2 LOSS / 31 PENDING 或零成交，
> 初始权益与 0 PnL 不再显示为盈利。所有 live/order flag 继续保持 false。
> **2026-08-04 实时纸面生产 v13（调度器真实解释器绑定）：** 现场根因审计确认 Windows
> Task Scheduler 原先绑定的是 `WindowsApps` Python 执行别名；短生命周期 shim 退出后任务会被
> 误判为 `Ready`，从而绕过 `MultipleInstances=IgnoreNew`，让耗时的实时特征与 SOR worker
> 在下一 cadence 重叠。注册器现要求 Python 自报绝对且存在的 `sys.executable`，并把后台 action
> 固定到该真实解释器同目录的 `pythonw.exe`；解析失败即拒绝注册。治理测试钉死这一边界。
> 生产状态根的唯一工作流实况为 **34 个 canonical 候选 = 10 日频 + 24 实时，0 个正式
> Execution-Paper eligible**；开发工作区验收时出现的 2 个 adaptive 候选尚未在生产反馈条件下
> 合法生成，因此不迁移、不补造。该修复只恢复 L2 公共数据/纸面流水线的单实例与新鲜度，所有
> live/order flag 继续保持 false。
> **2026-08-04 实时纸面生产 v12（唯一 Alpha 漏斗 + 冻结实时策略）：** 关闭了
> `AlphaProductionWorkflow` 10 条日频候选与实时自主发现 26 条候选分属两套身份/漏斗的
> 结构缺口。实时 discovery 现在发布完整 `realtime-alpha-research-policy.v1` 及内容哈希，
> 数据版本同时绑定 decision interval、标签 horizon/latency、quantile、费用、滑点、成熟度和
> feature/evaluator 版本；追加式注册链、manifest、config hash、安全边界及 adaptive lineage
> 任一不一致均 fail-closed。唯一工作流新增 `REALTIME_MECHANISM` 冻结定义，现有
> `ALPHA_EXPRESSION` 哈希逐位不变；真实 L2 对账为 **36 个 canonical 候选 = 10 日频 + 26
> 实时，0 个正式 Execution-Paper eligible**。工程 Signal Shadow/Execution Paper 只作为可见
> 运行证据，明确不能取得正式盲测成熟或晋级信用。Console API/SSE 与纸面驾驶舱显示 36/26
> 漏斗摘要、RT 身份和按 blocker 聚合的异常队列。设计与验收见
> `docs/engineering/specs/2026-08-04-canonical-realtime-candidate-handoff.md`。

> **2026-08-04 实时纸面生产 v11（因果反馈 + 全量生命周期可观测）：**
> 实时 Alpha 流水线现可消费追加式 `ENGINEERING_SHADOW_REALIZED` 结果，按候选与家族计算
> mean/SE/95% exploration UCB，在全局最多 8 条的预算内生成 half/double-horizon
> Challenger。每条动态候选冻结 `feedback_cutoff_ms`，搜索只使用 cutoff 后数据，且新的
> 30d OOS 未成熟时由 `ADAPTIVE_FEEDBACK_FRESH_OOS_BELOW_30D` 强制阻断正式 gate；动态
> manifest、parent、mutation、cutoff、generation 在 discovery→forward-shadow 交接中独立
> 哈希复核。权限始终为 exploration-only，不晋级、不接凭证、不接 broker、不下单。
> Console API 新增全量逐 Alpha lifecycle 投影（研究评估→前向实现 PnL→模拟执行），前端经
> SSE 实时显示 `PROFIT/LOSS/FLAT/NO_REALIZED_PNL`。同时明确分栏 `paper_realtime` 主纸面
> 账户与 `paper_shadow` 非晋级 Challenger，缺失收益不再默认显示为绿色。设计与边界见
> `docs/engineering/specs/2026-08-04-realtime-alpha-causal-feedback.md`。

> **2026-08-04 实时纸面生产 v10（并发链头原子化）：** 运行态验收发现
> `realtime_alpha_registry.db` 第 1397 条发生链分叉：两个 worker 可在 SQLite deferred
> transaction 外同时读取同一 head，再以不同 event id 各自成功插入。注册表 append 现改为
> `BEGIN IMMEDIATE` 内完成幂等复检、读取链头、计算哈希、插入和提交，跨进程 writer 被数据库
> 串行化；8 writer 并发回归测试和 discovery/forward-shadow 套件通过。损坏库不得原地改写，
> 必须完整隔离保留并从可重放 discovery/shadow 来源重建活动注册表。实现与并发测试均纳入
> 供应链显式哈希清单；live/order 权限不变。

> **2026-08-04 实时纸面生产 v9（自主执行纸面闭环）：** 22 条已评估实时 Alpha
> 轨道不再停留在 Signal Shadow。新增物理隔离的 Engineering Execution Paper：首次
> 决策只作启动基线，后续决策自动经过交易所 step-size 整数 lot 归一化、现有 pre-trade
> intent/gate、Binance USD-M + Bybit Linear 公共订单簿 simulated SOR、模拟成交、持仓、
> PnL、route TCA 与对账，并写入独立追加式 SQLite 哈希链。真实首轮为 22 个账户、20 条
> 新决策成交、120 个父订单、138 个场所成交切片、零错误；两条 1h 轨道处于 ARMED，等待
> 下一次自然决策，不回填旧决策。信号 1m 与订单簿 5m 的节拍差通过最多 16 条、按时间顺序
> 的每轨每轮 64 条有界追赶恢复，仍执行 12m 延迟上限；超窗决策追加为
> `MISSED_EXECUTION_WINDOW` 独立哈希链，绝不拿当前订单簿伪造历史成交。执行账本已纳入 L2 备份源合同
> v4，调度、watchdog、API/SSE 和前端均投影真实执行状态。该路径无 broker/私有流/凭证，
> 不计正式前向证据、不具晋级或部署权限，所有 live/order flag 保持 false。

> **2026-08-04 实时纸面生产 v8（特征流水线有界增量）：** 现场审计发现
> `binance_realtime_features` 每五分钟对 526 万行特征库重新读取 98 条序列全部历史，
> 并重算约 1,018,416 个经验 KS 窗口；同时公共行情哈希链每轮从创世块全量复核，导致
> 任务执行时间接近或超过自身五分钟 cadence。现改为双水位线：每个交易对取八项特征的
> 最小 `(knowledge_time,event_time)` 游标，只投影游标后的公共快照；漂移仅读取每序列最新
> 240 个 PIT 观测，保留 60+20 当前窗和 1,960 个校准置换窗，完整 530 万行历史不删除。
> 公共行情链头由追加式 feature-batch 哈希链承载，日常只复核可信链头后的新增后缀，
> 每 24 小时仍强制从 genesis 做一次全链审计；链头缺失、格式非法、锚点不匹配均
> fail-closed。首次真实 bootstrap 全链约 87 秒；紧接的真实增量周期只验证 280 条新增
> source rows，投影 2,240 个新特征值、跳过 3,220 条已物化快照，worker 内部耗时
> 2,192ms，batch chain valid。Data Catalog 动态显示本轮新增、增量快照、物化耗时与
> 漂移输入。该优化只改变 L2 公共数据诊断吞吐，不改变 Alpha 统计/前向门、正式 Paper
> 准入或任何 live/order 权限。

> **2026-08-04 实时纸面生产 v7（实时 Alpha 无等待双轨）：** 修复了实时 Alpha
> discovery 与纸面观察之间的流程倒置。此前 24 个预注册实时候选即使已经达到 3 天探索
> 可评估状态，也必须先满足 30 天正式验证和 90 天历史成熟才允许进入一个本身明确
> `forward_evidence_credit=false` 的隔离账本，导致工程观察无意义地等待。现在 discovery
> 单独保留 `evaluation_status`，工程影子 worker 在 `EXPLORATION_READY + EVALUATED + 正的
> 保守容量定额` 后立即建立追加式实时 Paper 轨道。30 天 DSR/PBO/locked holdout/成本压力门
> 与 90 天成熟度被明确移到 `graduation_blockers`，仍决定正式晋级，不决定开始纸面观察。
> 该轨道继续是公共数据、无凭证、无 broker、无订单、不可晋级、不可充当前向证据；正式
> Execution-Paper 仍只服从 `ALPHA_PRODUCTION_WORKFLOW`。

> **2026-08-04 实时纸面生产 v6（唯一工作流承重正式 fill）：** 审计发现正式
> `paper_order_preview` 虽有待决策、九引擎、模拟执行和对账门，却未直接消费唯一候选工作流的
> Execution-Paper 资格。现已 fail-closed 接入 `ALPHA_PRODUCTION_WORKFLOW`：新正式 fill 必须来自
> `execution_paper_eligible_ids`/`execution_paper_ids`，并把工作流原文哈希绑定进 preview；缺失、非法或
> 未授权候选在构造订单前阻断。旧不可变账簿只允许 `HISTORICAL_ROW_REPLAY`，legacy 不再可能凭旧路径
> 产生新正式 fill；工程 shadow 继续在独立非晋级账本实时运行。控制台新增 Workflow authority 与授权
> 候选数，真实前向/统计门槛未降低。
>
> **2026-08-04 实时纸面生产 v5（运营窗口与故障证据）：** WP2 新增
> `paper_operational_evidence.v1`：每个五分钟影子估值周期之后，从不可变 release receipt
> 绑定本轮语义快照，并写入独立的追加式 SQLite 哈希链。策略预注册为 5 分钟 cadence、
> 最大 12 分钟缺口、≥99% 成功率、30 分钟/6 次工程烟雾窗和 24 小时/274 次 Production
> Paper 窗；计划 release 重启必须在 15 分钟内恢复。每个 release 同时在临时数据库运行
> 6 项故障演练：重复消息幂等、冲突身份拒绝、链篡改检测、旧周期拒绝、broker 安全边界
> 拒绝和候选覆盖不全拒绝。故障注入不触碰运行账本。后端、SSE 和纸面驾驶舱新增
> Operating window / Fault drills / Restart recovery 动态投影。24 小时成熟度只决定 WP2
> 运营验收，不阻塞 WP3/WP4 继续开发，也不改变正式 Paper、Alpha 晋级或实盘权限。

> **2026-08-04 实时纸面生产 v4（本轮制品原子性）：** `paper_shadow_realtime`
> 不再忽略下游估值 worker 的退出码，也不再允许复用磁盘上的旧成功快照冒充本轮成功。
> 每轮现在要求输出制品文件身份发生变化，并重新验证 10/10 候选、paper-only 安全边界、
> mark 状态和上游结果。上游零退出可直接接受；仅因正式回测引用未齐而返回 1 时，只有新制品
> 明确 `paper_mark_ready=true`、`errors={}` 且正式准入仍关闭才可作为非晋级工程周期接受。
> 其他非零结果或未刷新制品立即覆盖为
> `SAFE_BLOCKED`，记录 `UPSTREAM_PAPER_REALTIME_FAILED` 或
> `PAPER_REALTIME_ARTIFACT_NOT_REFRESHED`，并把 paper mark、shadow running、正式准入和所有
> live/order 权限逐项置为 false。成功快照新增 `upstream_cycle`（起止时间、退出码、制品刷新）供
> watchdog、Console 与后续连续运行 SLO 复核。该修复关闭无人值守重启/异常路径上的“旧 JSON 假绿”，
> 不改变 8 周正式晋级证据门，也不开放任何真实下单路径。

> **2026-08-04 实时纸面生产 v3（风险输入全覆盖）：** 10/10 纸面 Alpha 的七类非时间型
> 九引擎输入已全部预计算完成。慢节奏 14d 候选在没有当前 pending 决策时，只使用最近一条
> 真实 attested 已兑现决策生成监控输入，并标记
> `LATEST_REALIZED_ATTESTED_DECISION_MONITORING_ONLY`；评估晚于旧决策的候选允许生成
> `POST_DECISION_MONITORING_ONLY` 模型风险输入。两条路径均保持正式 `status=PENDING`，并以
> `CURRENT_ATTESTED_PENDING_DECISION_MISSING` / `INDEPENDENT_EVALUATION_POSTDATES_DECISION`
> 阻断准入。没有补造或回填决策，8 周与独立 PASS 仍只决定正式 Paper 晋级。

> **2026-08-04 实时纸面生产 v2（开发与前向证据并行）：** 工程影子纸面不再等待
> 8 个前向周期才启动；8 周门只保留为正式晋级/真钱资格证据。`paper_multivenue_sor`
> 现在每轮为 14 个候选交易对保存 Binance USD-M WebSocket + Bybit V5 公共订单簿的
> 完整哈希链快照，验证场所、来源、费用假设、新鲜度、跨场所时钟偏差和全宇宙覆盖后，
> `paper_shadow_auto` 才允许把最新已证明身份的候选送入 deterministic simulated SOR。
> 新决策使用独立 `paper_shadow_routed_ledger_v2.db`，旧 v1 账本只读保留；重启按决策键
> 精确重放，不依赖重新获取快照。真实现场验证为 14/14 交易对、10/10 候选账户、
> 78 个父订单、892 个场所子成交切片、零错误、账本链有效；实时重估写入 10 个新快照，
> `cycle_completed=true`，资金费完整且对账残差约 `2e-10`。费用、队列位置、延迟和真实
> 成交仍未校准，因此这是 `REAL_PUBLIC_BOOK_SIMULATED_SOR_V2`，不是生产券商 SOR/TCA。
> 全程 public-data/paper-only，`live_allowed=false`、`order_submission_allowed=false`。
> **候选责任边界（2026-08-05）：** `ALPHA_PRODUCTION_WORKFLOW` 的 34 个身份被显式分成
> routed shadow scope 与 realtime handoff scope：当前 10 个静态/日频、已有 attested
> decision 的候选由 `paper_shadow_auto` → `paper_shadow_realtime` 负责并计入运营窗口；
> 24 个 `definition_kind=REALTIME_MECHANISM` 候选由 realtime forward-shadow /
> execution-paper worker 独立负责。shadow 工件只保留前者，同时记录完整 34 个 population
> 与 handoff 列表；实时候选的 pending 行不能再把日频 worker 或 Paper 运营窗口伪装成故障。
> **同日九引擎输入前置计算：** challenger 的 PIT 收益/参考收益、regime、容量、ADV、AUM、
> FX 与数据集身份不再被 8 周证据钟串行阻塞；有 attested decision 即预计算，并以
> `input_evidence_status` 与正式 `status/admission_blockers` 两条正交状态展示。真实首跑：
> 3 个 challenger 已齐全部输入但因独立评估非 PASS + 3/8 周保持 PENDING；2 个已齐
> 6 类输入但评估晚于原决策，缺合法 pre-decision metadata；2 个 14d 变体尚无 attested
> pending decision，明确阻断。前端现在显示 `Inputs READY · Admission PENDING`，不会把工程
> 进度误报成正式准入，也不会因等待周期停止证据生产。


> **2026-08-02 WP3 预注册揭盲 + WP4 组合承重接线：** **WP3 W3-1**——影子门从
> 「原始期数 ≥ 8」升级为预注册三重纪律:有效独立观察数(兑现窗口重叠只少计,
> 贪心区间调度)+ regime 覆盖(资金费 z × 30d 实现波动 z 的 expanding 三分位,
> 全 PIT、缺证据 fail-closed SEALED)+ 政策版本化(`unblinding_policy.v1` 内容
> 寻址进门证据哈希,不重置现有 10 候选身份/证据钟)。纯核
> `services/research/unblinding.py`(28 测试),接线
> `tools/alpha_production_workflow.py::_shadow_gate`(18 测试);揭盲判定只消费
> 计数与覆盖、绝不读收益——只会让 MATURE 更晚到。真实数据冒烟 VERIFIED:10 候选
> 全 SEALED/RUNNING,blocker 诚实(1/8 期 + regime 证据不足)。设计
> `docs/engineering/specs/2026-08-02-wp3-preregistered-unblinding-design.md`。
> **WP4 W4-1/W4-2**——五个此前纯诊断的维度做成 per 候选承重证据行:
> `services/research/portfolio_evidence.py`(纯核)+
> `tools/candidate_portfolio_evidence.py` → `candidate_portfolio_evidence.json`
> (边际组合贡献——incumbent = dry_run.db 正典 run 等权合成、共同暴露
> factor_decomp、容量带引用、拥挤汇总、SOR TCA 引用;任一不可得 → None +
> `missing`,绝不编造)。接线 **不开第六门**:RESEARCH_RISK READY = 九引擎包
> READY ∧ portfolio 行完备(容忍集仅 tca),缺 → PENDING +
> `PORTFOLIO_EVIDENCE_INCOMPLETE`/逐子项 blocker,行进门 content hash;
> `eligible_for_execution_paper` 谓词不动。**只查完备性,阈值 = W4-3 独立
> 预注册评审(全部 UNCALIBRATED,本周期不预断任何候选)**。设计
> `docs/engineering/specs/2026-08-02-wp4-portfolio-execution-gate-design.md`。
> 首跑实况:legacy 三条有行(crowd/top OK,R² 0.045–0.09 与 W3「无隐藏共同敞口」
> 互证;carry 诚实 INCOMPLETE 缺 crowding),7 challenger 无正典 run →
> MISSING_RUN(权威回测首跑 = 新 release 下运维步骤)。44 测试 + 治理扫描绿。
> **W4-3 已生效**:预注册 `portfolio_gate_policy.v1` merit 判定激活(阈值表 =
> 设计文档冻结节,全 UNCALIBRATED,TCA 容忍缺失)——缺子证据仍 INCOMPLETE
> (PENDING),完备未过阈 → 门 FAIL + `PORTFOLIO_GATE_<CODE>`,判定块与政策哈希
> 进门 content hash/调和摘要;实测 crowd/top merit PASS(仍被九引擎包挡)、carry
> 维持 INCOMPLETE、challenger MISSING_RUN。测试 37+18(核心/调和器)。
> **WP5 盘点:L2 内无可诚实编码项**——外部心跳 sink、持续外部封存(GitHub
> Actions 计费阻断)、WORM/异地留存、数据第二源 SLA、供应商退出全部绑定外部
> 资源/真人授权,保持 NEEDS_EXTERNAL 登记(top-tier 评估机器承载),不造假。
> **WP1 遗留对齐**:`top_tier_assessment._l2_source_contract_error` 仍只认
> source-contract v2,而 WP1 已把备份契約升 v3(新增 `alpha_production_workflow.db`
> required)——校验器已对齐接受 v2/v3(结构校验不变,附 v3 验收测试;
> `tests/governance/test_top_tier_assessment.py`)。当前两项
> (OPS.BACKUP.ENCRYPTED/OPS.RESTORE.DRILL)诚实报 FAILED(v3 契约已声明、
> v3 证据未产出):最近一次调度备份跑在任务重绑新 release 之前,今晚 03:00
> 的备份会产出 v3 证据,届时两项恢复可验证。

> **2026-08-02 WP2 机器层收口第一刀(26/27 根因 + 变体兑现回归 + challenger 回测
> provenance)：** ① 上一轮验证 26/27 的唯一失败(后端全套 pytest)根因定位为**宿主句柄
> 失效、非代码回归**:托管/分离 shell 里继承的控制台句柄在长跑中失效,之后子进程创建即
> `WinError 6`,tests/governance 247 个子进程用例批量失败(同文件单跑全过、`< /dev/null`
> 全过可复现)。修复:新建根 `conftest.py`(测试派生子进程默认 stdin=NUL,显式 stdin/input
> 不受影响)+ `scripts/validate.py::_run_check`(stdin=DEVNULL;继承句柄失效时降级为管道
> 捕获,detail 强制 ASCII/区域无关保证据可哈希,失败尾部 4KB 进机器报告)。② `f23713b0`
> 回归:`tools/forward_track_advance.py` 重写 `realize_due` 调用时丢了变体
> `min_gap_ms`/兑现网格/参照 quantile——3d 变体决策每 3 天一条、兑现却等 6.5 天,稳态积压
> 2 条 pending,结构性堵死风险包/preview 的「恰好一条 attested pending」契约;且全部变体
> `backtest_reference_sharpe` 误用周度网格+默认分位而逐位雷同。已修复并以 2 个钉死测试
> 防回归(`tests/research/test_forward_track_gate.py`)。**调度系统跑不可变 release
> `5d7c2dc1`,此修复随下一 release 生效;上线后 3d 积压由日常 forward 运行按 2.5d min_gap
> 自然兑现消化。** ③ challenger 回测 provenance:新驱动器
> `tools/dry_run_candidate_multi.py` 把 7 个预注册变体跑过与官方 crowd 驱动器同一条
> `run_multi_period_dry_run`(10bps/拥挤门/资金费/vol-target 按节奏 ppy)并幂等持久化进
> `dry_run.db`(复用 `_existing_crowd_run` 同语义);`three_alpha_paper_flow` 新增
> `candidate_authoritative_dry_runs` 阶段(legacy 三条的 verdict 语义一字不动),
> `paper_realtime._backtest_summary` 双源消费——候选可赚
> `REAL_AUTHORITATIVE_L2_DRY_RUN` 行,健康门 `BACKTEST_PROVENANCE_INVALID` 的机器阻塞
> 消除;无行候选仍诚实回退 `REAL_PAPER_CANDIDATE_RISK_INPUTS`,**不降低任何晋级门**
> (workflow eligibility 仍需 verified PASS 评估 + 8 前向期 + READY 九引擎包)。首跑真实
> 落库是新 release 下的运维步骤,幂等。本轮后全量 `validate.py` **27/27 全绿**(含
> frontend + phase13)。证据钟项(1/8 前向期、独立评估 verdict=FAIL、容量带积累)保持不变
> ——WP2「变绿」的剩余部分靠时间与前向证据,不用代码豁免。

> **2026-08-02 WP1 唯一候选工作流：** `AlphaProductionWorkflow` 与
> `CandidateEvidenceBundle` 已落地为追加式 SQLite 双哈希链，统一冻结候选身份、材料变化后继关系，
> 以及 Historical OOS / Signal Shadow / nine-engine risk / Execution Paper / lifecycle 五类证据。
> 调和器每五分钟从不可变 release 幂等运行；权威 JSON 存在后，forward、独立评估、风险包、preview、
> realtime、paper health 和 SSE 前端只消费其 active population；现有但无效的权威会 fail-closed，缺失仅
> 保留首次引导兼容。该 DB 已成为 L2 backup source-contract v3 的 required source。对当前 release
> `c90836d8...` 的只读烟雾调和结果为 10 个候选、0 个当前 paper eligible、0 个非 legacy execution
> candidate；全部仍为 1/8 前向周期，三本既有实时标记账户继续明确隔离为
> `LEGACY_DIAGNOSTIC_PAPER`。因此 WP1 接线完成并没有伪造 Alpha 通过，下一承重目标仍是 WP2：让
> 候选通用九引擎、preview/fill 和逐周期对账在真实新候选上变绿。设计见
> [Alpha 生产工作流实施记录](engineering/specs/2026-08-02-alpha-production-workflow.md)。

> **2026-08-02 固定开发路线：** 从研究到生产的工序排序已冻结为
> [`QUANTOS_LEAN_INSTITUTIONAL_PRODUCTION_ROADMAP.md`](QUANTOS_LEAN_INSTITUTIONAL_PRODUCTION_ROADMAP.md)
> `FROZEN_V1`。当前实施顺序固定为 `WP0 发布/证据原子性 → WP1 唯一候选工作流 →
> WP2 生产级 Paper 变绿 → WP3 并行盲化前向与自动交接 → WP4 组合/执行承重 →
> WP5 外部生产边界 → WP6 Testnet/Operational Canary → WP7 Alpha Canary/扩容`。
> 该路线删除重复手工审批和串行等待，但不删除 PIT、Trial Ledger、锁定 OOS、独立挑战、
> 风险/kill switch、对账、不可变发布或真实资本的人类授权。

> **2026-08-02 WP0 发布身份与纸面身份第一增量：** L2 调度注册现在只接受干净 Git
> `HEAD`，把精确 40 位 commit 写入每个 Task Scheduler action；每次 worker 启动前重新
> 校验 HEAD 与完整 cleanliness，并拒绝 release/state 同根或嵌套。launcher 向 worker
> 绑定 `QUANTOS_RELEASE_COMMIT`、`QUANTOS_RELEASE_ROOT`、`QUANTOS_STATE_ROOT`，每次运行在
> state root 的 `.runtime/l2-release-runs/` 写哈希凭证，分别记录 release 验证结果与 worker
> 退出结果。实时 Alpha 发现、前向 shadow、前向证据基线与控制台纸面投影已切到该 state
> root，避免数据库回写 clean release。该边界已在真实 Windows Task Scheduler 上激活：首个迁移
> 基线为 GitHub commit `2d4ca1227ddecc96eb39a2799cc6d8404f564466`，release root 与
> `H:\QUANTOS_STATE` 完全分离；状态迁移排除 `pytest-basetemp` 后逐路径核对 447,339 个文件、
> 10,963,519,184 bytes，0 缺失/多余/大小漂移，5 座关键 SQLite 库只读 `quick_check=PASS`。
> 33 个 QUANTOS 任务中 28 个 L2 action 已固定 release commit/state root，5 个旧实盘/运维任务
> 保持 `Disabled`。首次真实 watchdog 运行进一步暴露 worker `main()` 调用 `sys.exit(code)` 会绕过
> launcher 最终回执；launcher 现已把 `SystemExit(None|int)` 规范化为 worker 退出码，布尔/字符串/
> 浮点仍 fail-closed，从而保证非零结果也落成 `FAILED + NONZERO_EXIT` 完整回执。旧同根调度缺口已
> 关闭；**WP0 整体仍不宣称闭合**，剩余是把每轮输入数据快照/证据集合进一步绑定进 release receipt，
> 以及外部 WORM/独立回执权威，而不是再次回退到开发工作树运行。
> 同一 release/state 契约也已扩展到本地控制台三进程：显式状态根必须与精确 clean release
> commit 成对提供，console API、research runner 和静态前端均以 state root 为 cwd，并通过
> `python -P` 与 release-first `PYTHONPATH` 固定代码来源；manifest/log 改写入 state root。
> 因此浏览器、runner 与调度器消费同一运行状态，同时状态目录中的代码副本不能遮蔽 release。
> Uvicorn 另以 `--app-dir=<release>` 固定应用导入，静态服务直接执行 release 中的绝对脚本；Git
> 忽略的前端 bundle 不再写入 release，而位于 `state/.runtime/frontend-builds/<commit>/dist`，
> 通过 `QUANTOS_FRONTEND_DIST` 与发布身份一一绑定。
> `carry_oi_majors`、`crowd_contra`、`top_contra` 同时被明确标为
> `LEGACY_DIAGNOSTIC_PAPER`：仍可接收公共实时标记与产出 PnL，但不计入新候选执行 Paper，
> 永远不具部署资格。前端漏斗已拆分 Candidate Paper 与 Legacy Paper，避免把三条历史 book
> 误报为全自动候选晋级成功。全链仍为 L2，实盘与订单权限没有变化。

> **2026-08-01 系统内生自治实时 Alpha 搜索与动态纸面驾驶舱：**
> 实时 Alpha 发现已不再依赖开发 AI/IDE 手工触发：`tools.realtime_alpha_discovery`
> 由 L2 调度器每小时运行，固定预注册 4 个经济机制家族（order-flow、order-book、
> liquidation、funding/basis）× 2 个机制 × 3 个 horizon，共 24 个不可挑选性改写的
> 试验；每轮统一消费 PIT Binance 公共微观结构特征，写入全局 experiment ledger、
> DSR/PBO/时序 holdout/压力检验和哈希链家族注册表。2026-08-01 现场证据为 14 个
> USD-M 交易对、核心共同历史 3.64 天、24/24 已评估、diagnostic lead 0、formal pass 0、
> 全局 trial count 2577；系统没有为了展示数量伪造 Alpha。
>
> 容量已改用 top-5 双边可执行深度而不是成交量代理；新容量特征从接线时开始独立积累，
> 当前仅约 0.04 天，保守深度约 `$2.16k`，10% 参与率/每边两腿下支持 AUM 约 `$866`，
> 影子建议规模约 `$433`。该数值可用于诊断但在满 30 天前 `evidence_ready=false`；
> `$1m` 目标容量不通过不会抹掉统计 Alpha，只会限制可测试规模。正式统计评估仍需 30 天
> 共同历史，准入独立前向影子仍需 90 天共同历史。
>
> `tools.realtime_alpha_forward_shadow` 每分钟消费发现快照。只有 90 天成熟、正式门通过且
> 可执行深度可定规模的候选才写入 append-only/hash-chained 影子账本；准入时冻结候选身份、
> manifest、策略参数、交易宇宙、dataset/search 版本与 shadow AUM，先记决策再等结果，读取
> 上界显式截断到本轮墙钟，重启后继续已准入轨道，即使下一轮搜索排名回退也不丢失 OOS
> 观察。硬安全异常写新 `SAFE_BLOCKED` 证据，普通未成熟写机器原因。当前真实状态为
> `WAITING_FOR_VALIDATED_CANDIDATE`、账本链有效、0 admission/decision/realization；这与既有
> 3 个 `LEGACY_DIAGNOSTIC_PAPER` 自动诊断 book 相互隔离。
>
> 控制台 `/api/v1/console/stream/dry-run` 已改为基于耐久证据变化的 SSE（15 秒 heartbeat），
> 纸面页不再 10 秒轮询或要求手动刷新；浏览器现场确认同一页面自动从 fail-closed 占位切换为
> `SSE LIVE`，并显示 24 路漏斗、容量、前向状态、3 个纸面 book、权益/对账/TCA。通用 Panel
> 同时改为窄视口纵向头部与可换行状态，IDE 内置浏览器不再出现标题竖排和 Chip 溢出。
> 全链保持 `paper_only=true`、`live_allowed=false`、`order_submission_allowed=false`、
> `allow_ai_direct_trading=false`，没有凭证、broker 或自动晋级权限。
>
> Binance 低频系统风险采集也已按共享公网 IP 约束内化为缺口优先的轮转计划：14 个标的中
> 每 30 分钟只刷新最缺失/最旧的 7 个 constituent 快照，其余 7 个只在哈希链、PIT 时间和
> 新鲜度复验通过后复用；basis 仍按最旧闭合小时限额补齐。真实调度任务从 20 请求/IP 权重
> 30 降为 13 请求/IP 权重 16，并在冷却后完成 14/14、`refresh_errors=0`、任务退出 0。
>
> 同轮全量门禁暴露并修复了真实灾备容量缺口：旧 `QBK1` 单消息 AES-GCM 无法处理已增长到
> 2.77GB 的 `experiment_registry.db`。文件级备份现使用向后兼容的 `QBK2` 分块认证容器，
> 将总长度、块数、块序号和块长度绑定进每块 GCM AAD，篡改、截断、换序和多余尾部均
> fail-closed；源快照、加解密、清单哈希和批次摘要均改为流式，不再整库读入内存。
> 2026-08-01 真实 `QUANTOS_backup` 已在独立卷完成 14 个权威源的加密备份与隔离恢复：
> Task result 0，manifest/source-contract/restore-history/hash-chain/SQLite integrity 全 PASS，
> 总备份 419.450 秒，恢复 16.794 秒（RTO 300 秒内），RPO 满足。旧 QBK1 小文件仍可恢复。
>
> **2026-08-01 自动 Alpha 候选交接与真实漏斗可视化：**
> “只有 3 个 Alpha”经现场追踪确认是展示与执行集合混淆，不是研究候选丢失：官方前向台账实际包含
> 10 个已证明身份的 OOS challenger，而实时估值/模拟成交账本仍是 3 个。现已把 factory 输出升级为
> schema v2；只有实验注册表中逐项验签、结果为 PASS、且 search/dataset/definition 身份完全一致的幸存者，
> 才会自动写入新的追加式、哈希链接、禁止 UPDATE/DELETE 的 L2 `PaperCandidateRegistry`。前向调度器每轮
> 合并静态候选与该动态注册表，注册表缺失按空集合处理，链损坏或身份冲突则 fail-closed。当前 factory
> 真实运行在 24 币、9 数据维、2 轮、248 次评估上，没有发现新的正交 PASS Alpha，因此没有伪造新增候选；
> factory→forward 接缝已验证，自动晋级、实盘、订单和凭证权限仍全部关闭。纸面驾驶舱现在按
> `factory survivors / forward paper / execution paper / forward-only` 分层显示，现场为 `0 / 10 / 3 / 7`，
> 并逐项显示前向周期、待兑现决策和累计收益。剩余唯一流水线接缝是动态候选的独立 evaluator 元数据、
> 九引擎风险输入与执行纸面消费；7 个变体目前仅 1/8 前向周期，不能为了数量绕过多重检验和成熟门。
> 设计与边界见 `docs/engineering/specs/2026-08-01-automatic-alpha-candidate-handoff.md`。

> **2026-07-31 OIDC 验证密钥轮换/撤销承重化：**
> 强身份验证器不再接受单个静态 `QUANTOS_OIDC_PUBLIC_KEY_FILE`。新 `OidcKeyRegistry` 要求 JWT
> `kid` 精确选键、算法与注册项一致、Ed25519/≥2048-bit RSA 公钥类型正确、显式有效期且状态为 ACTIVE；
> 缺/未知 `kid`、撤销、算法错配、未生效或过期均在解 JWT 前 fail-closed。注册表代际连续且不可回滚，旧
> `kid` 不可删除、换材料/算法/有效期或撤销后复活；运行时同时要求文件 SHA-256 与最低 generation 两个
> operator pin，拒绝 symlink、digest/issuer 漂移和低代际。强身份基线由 20 增至 23 个对抗案例，服务仍不
> 含 JWKS 网络抓取、私钥或签名能力。该增量使本地轮换/撤销接缝承重，但外部 IdP、托管 HSM/证书生命周期
> 与独立运营证据仍不存在，因此 `GOV.IDENTITY.STRONG_AUTH` 保持 M3/PARTIAL、总矩阵保持 15/22。

> **2026-07-31 OIDC 验证密钥轮换/撤销承重化：**
> 强身份验证器不再接受单个静态 `QUANTOS_OIDC_PUBLIC_KEY_FILE`。新 `OidcKeyRegistry` 要求 JWT
> `kid` 精确选键、算法与注册项一致、Ed25519/≥2048-bit RSA 公钥类型正确、显式有效期且状态为 ACTIVE；
> 缺/未知 `kid`、撤销、算法错配、未生效或过期均在解 JWT 前 fail-closed。注册表代际连续且不可回滚，旧
> `kid` 不可删除、换材料/算法/有效期或撤销后复活；运行时同时要求文件 SHA-256 与最低 generation 两个
> operator pin，拒绝 symlink、digest/issuer 漂移和低代际。强身份基线由 20 增至 23 个对抗案例，服务仍不
> 含 JWKS 网络抓取、私钥或签名能力。该增量使本地轮换/撤销接缝承重，但外部 IdP、托管 HSM/证书生命周期
> 与独立运营证据仍不存在，因此 `GOV.IDENTITY.STRONG_AUTH` 保持 M3/PARTIAL、总矩阵保持 15/22。

> **2026-07-31 强身份访问复核证明承重化：**
> 治理授权不再把 `access_review_expires_at_ms` 当成可单独手填的充分证据；所有启用且未撤销的
> `IdentityAuthorizationGrant` 现在必须携带内容寻址的 `IdentityAccessReviewAttestation`。证明精确绑定
> `issuer|subject`、HUMAN/WORKLOAD、完整角色集、独立复核人、OIDC+mTLS+MFA assurance、token ID、
> 客户端证书摘要、复核时刻和最长 90 天到期；复核人必须持 reviewer 角色、令牌在复核时有效且不可自审。
> 授权决策保留 review ID/content hash/reviewer actor，注册表快照则把整份证明纳入既有 CAS 头与追加式
> 哈希链。无证明、角色/主体/到期漂移、超期、过期 reviewer 或自审均 fail-closed；紧急撤销刻意允许无新鲜
> 复核，避免复核系统故障阻止撤权。强身份基线现含 20 个对抗案例。该增量只闭合本地承重证据，外部 OIDC、
> 可信 mTLS 终止、托管密钥/证书轮换、真实 joiner/mover/leaver 与独立组织复核仍未提供，故矩阵诚实保持
> `GOV.IDENTITY.STRONG_AUTH=M3/PARTIAL`、全局 15/22 与 `L2_DRY_RUN_ONLY`。

> **2026-07-31 实盘前阻断队列与高级证据分层：**
> `SystemCompleteness` 默认视图不再把 22 项 Tier A/B 证据表、域成熟度、红线、仓库清单和
> L2 边界同时平铺。页面直接消费 `TOP_TIER_EVIDENCE_ASSESSMENT`，只投影机器判定的未完成项，
> 并按 `L2_SAFE_NOW → NEEDS_EXTERNAL → POST_L2` 固定排序为 `WORK NOW / EXTERNAL / AFTER L2`；
> 每项仅显示当前→目标成熟度、真实 detail 和 registry 的 next_action。完整 22 项矩阵、证据路径、
> 域清单和安全边界仍保留在 `Advanced evidence matrix` disclosure，未删除审计信息，也未引入
> 手写完成率。固定 Chromium 门禁新增该页面：7 项真实形状 blocker、1440×1000 零横向溢出、
> WCAG 2.0/2.1 A+AA 零违规；纸面驾驶舱视觉基线保持不变。现场外部探针再次使用仓库外
> `key/key.txt` 执行无业务数据 echo，Provider 仍返回 HTTP 401，未生成 PASS 证据；最新 GitHub
> Actions `30628839808` 两个 job 均为 0 steps，官方 annotation 明确是账户付款/额度在调度前拒绝，
> 不是代码失败。`crowd_contra` 虽有正 embargoed OOS Sharpe，但全局 2539 trials 校正后的 DSR
> 仅 0.0203、正式 evaluator=FAIL，因此未冒充正向 challenger。矩阵继续诚实保持 15/22、
> `NOT_TOP_TIER`。完整门禁同时抓到一个只在实时运行时出现的 Phase 13 竞态：生成审计制品后，
> 5 分钟 paper worker 更新输入会令静态逐字段测试失败。现把 `paper_health_10k.json` 与
> `shadow_status.json` 明确列为动态输入并纳入 `input_hash`；检查器只允许这两个声明输入的快照漂移，
> 同时继续逐位核对代码、配置、其余静态输入、安全标志、派生状态和内部哈希，篡改
> `ALLOW_ORDER_SUBMISSION` 的负控制会立即失败。实时纸面无需停机即可稳定跑统一门禁，L2 边界不变。

> **2026-07-31 正式纸面前向证据时钟与成熟态可视化：**
> 现场审计确认自动 canary SOR 持续增长至 692 runs，三条正式策略也持续盯市、资金费与
> 对账，但九引擎 pre-fill 风险门按设计拒绝新 fills：crowd/top 各过 8/9，carry 过 7/9；
> 共同未满足项是前向置信度仅 `1/8=0.125`，carry 另有真实容量约束。官方前向任务无漏跑，
> 三条策略各有一个 admission-bound、attested pending decision，决策锚点 2026-07-28，
> 6.5 天不可回看持有期决定下一次最早可兑现为 `2026-08-03 12:00 UTC`。`paper_health`
> 现仅在三策略待兑现决策齐全、质量/血缘三门全 PASS、无前向告警且九引擎输入包完整时
> 输出 `PAPER_FORWARD_EVIDENCE_MATURING`，并提供逐策略 decision/eligibility、wait_ms、
> 剩余周期；缺任一条件仍 fail-closed 为 BLOCKED。指挥中心与纸面驾驶舱展示同一真实时钟，
> 不再把正常时间成熟误写成“修复风险”，也不把成熟中冒充 READY。正式下单、broker、凭据、
> 私有账户与自动晋级仍全部 false；直到真实 8 个周期及剩余风险门通过前不会制造正式 fills。

> **2026-07-31 机构级纸面驾驶舱与跨平台前端门禁：**
> 纸面控制面已收敛为 5 个一级工作区（指挥、纸面、风险、数据、健康），完整长尾页面
> 保留在 Advanced 菜单与搜索中；顶部单行情状态不再复制滚动，1440×1000 下零横向
> 溢出。新增精确固定的 Playwright 1.62.1 + axe 4.12.1 门禁：固定 UTC/locale/viewport，
> WCAG 2.0/2.1 A+AA 零违规，Windows/Linux 双平台视觉基线，失败制品由 SHA 固定的
> upload-artifact 保留；内嵌 Noto Sans SC variable font 消除 Linux CJK 方框。治理测试
> 把配置、CI、双基线与依赖钉入 top-tier requirements，矩阵不再把 E2E 误列为待办。
> 真实 `DryRunStore` route-TCA 投影也由小标签升级成纸面执行质量面板：现场连续增长至
> 666 routed runs / 2,664 verified records / 2 simulated venues，加权 shortfall 6.08bps，
> 并逐位标注 `SIMULATION_ONLY · verified on read`。本机 canonical fail-closed gate 全绿，
> Windows 与本机 Playwright Linux 容器 E2E 均通过；独立 GitHub Codespace
> `animated-yodel-pwqxjjg755c94rr`（`basicLinux32gb`、Linux x86_64）已在精确功能提交
> `f6238fe83e04177337ebc2a7bd0800c00468ecd0` 上完成 `npm ci`（447 packages、0 vulnerabilities）、
> typecheck、16 files / 131 tests、ESLint、production build、Playwright 1.62.1 Chromium
> E2E 2/2 与前端治理契约 4/4；云端 checkout 保持 clean。GitHub hosted Actions 当前仍因
> 账户额度/付款状态拒绝启动 job，因此本次 Codespace 结果是独立 Linux 复验，不冒充受保护
> 分支上的 hosted Actions required check。
> 全自动影子纸面继续读取 Binance 公共 mark，实盘、下单、凭据、私有账户访问均为 false；
> 能力矩阵仍诚实为 15/22、`NOT_TOP_TIER`，外部身份、供应链 WORM、30/90 天历史、
> 真实授权数据/场所与正向 OOS challenger 不因前端完成而提前关闭。

> **2026-07-31 跨主机 deployable-container 复现切片：**
> 新增配方绑定的 OCI 单构建声明与跨主机聚合校验器；GitHub 外部工作流现由
> `ubuntu-22.04` / `ubuntu-24.04` 两个独立 hosted runner 使用 full-SHA 固定的
> setup-buildx action、digest 固定的 BuildKit 与应用基础镜像做无缓存 OCI 构建。
> 每个 runner 在本机完整校验 archive/blob/manifest/config/layer/diff-id/重写时间戳，
> 仅上传自哈希声明；OIDC 作业要求 archive 字节与完整 image graph 逐位一致，再用
> Sigstore/Fulcio/Rekor 签署聚合证明并保留 90 天 GitHub artifact。当前代码与契约测试
> 已落地，必须等外部 workflow 真跑成功并回收签名制品后，才把
> `CROSS_HOST_CONTAINER_REPRODUCIBILITY_NOT_PROVEN` 从能力矩阵移除。独立组织、
> 外部 receipt authority 与 WORM/object-lock 仍明确开放；L2/实盘门禁零变化。

> **2026-07-31 机构控制面导航收敛：**
> 顶部外壳不再同时平铺 7 个阶段的实时指标、自动/人工计数和当前分区全部页面。
> 一级生命周期改为固定 7 等分 rail，只保留阶段、一个真实读数和当前态；二级只呈现
> 当前页、前后页和按需展开的完整页面菜单。1280px 下零横向裁切，390px 下收敛为
> 带可访问名称的图标 rail；真实浏览器确认菜单、实时纸面首屏和 10 秒轮询不受影响，
> console 0 error。完整审计入口仍在菜单与全局搜索中，信息没有删除，只降低默认噪声。
>
> **2026-07-31 前向数据血缘并发一致性与单实例锁修复：**
> 前向刷新曾把回填证据绑定到持续增长的整库 raw-archive 聚合；实时 Binance depth/trade
> 在回填与 admission 之间追加合法观测时，会把有效的必需数据误判为
> `BACKFILL_ARCHIVE_MISMATCH`。现回填与 admission 共同绑定明确的数据集 × 14 标的
> scope，整库摘要仍独立承担全归档完整性门，既消除无关实时流竞争也不放松 fail-closed
> 校验；前向编排复用同一轮已验证摘要，避免重复全库扫描。单实例锁同时改为 PID 存活
> 权威：活进程无论运行多久都不可被 10 分钟阈值驱逐，死亡 PID 立即恢复，只有不可解析
> 的旧锁才按时限回收。研究风险、前向周期和真人生产门均未绕过。
>
> **2026-08-30 A 股迁移数据纳入恢复权威 v14：**
> DGX 生产态的 `stocks.db` / `research.db` 与迁移副本 SHA-256 逐位一致，SQLite
> `quick_check` 均为 `ok`，249 只股票、317,641 根日线及三件只读研究快照都已在场；
> 但旧 v13 加密备份合同遗漏了整个 `data/stocks/`。现 v14 将市场权威库、冻结研究
> 副本和 alpha/klines/paper-track 三件快照全部列为 required source。2026-08-26
> 11:11 至 2026-08-28 21:58 的 58.79 小时真实备份尝试缺口仍完整保留为旧 epoch
> `BREACH`，不删除、不降级；v14 只有在完成一次真实外置加密备份和隔离恢复后才建立
> 新 epoch，随后按 30 天/16 次成功继续以 `RESTORE_HISTORY_MATURING` 诚实积累。
>
> **2026-08-31 新上传 TDX A 股与恢复性能纳入 v15：**
> 新上传的 5,549 只股票、6,267,665 根日线 SQLite 已作为 required source 纳入
> 外置加密备份，派生 panel 作为可重建 optional cache 同步纳入；`.sqlite` 不再被
> 当普通文件复制，而走在线 SQLite snapshot + 恢复完整性校验。恢复过程把明文摘要
> 融入认证解密流并最多并行四文件，保留 5 分钟 RTO 不放宽。备份权威同时开始记录
> `RESTORE_RTO_EXCEEDED` 等稳定脱敏原因，避免长跑失败被统一掩成
> `BACKUP_ATTEMPT_FAILED`。Bybit resilience 链新增独立持久检查点，首次全链核验后
> 的每次服务重启只验证后缀，消除链随历史增长而不断拉长的启动等待。
>
> **2026-07-31 恢复历史合同 epoch 与运行健康分层修复：**
> 恢复权威本来允许 source contract 升级后开启新的 cadence epoch，但 clean-lookback
> 失败统计误扫了所有历史合同，导致旧合同首次配置失败持续污染当前合同。现只统计当前
> 合同绑定的 attempt；真实权威复核为当前合同 9 次成功、0 次失败、0 个超时开放尝试、
> 最新恢复 PASS，旧失败仍完整保留在 append-only 链中。Watchdog 同时把仅由
> `HISTORY_SPAN_INSUFFICIENT` / `SUCCESS_COUNT_INSUFFICIENT` 构成的状态标为
> `PARTIAL / RESTORE_HISTORY_MATURING`，而当前失败、陈旧、间隔超限、链损坏和超时仍为
> `BREACH`。计划任务真实上下文复跑为 20 PASS、0 BREACH；M4 顶级门仍诚实等待 30 天、
> 至少 16 次成功、独立 Ed25519 checkpoint 与 90 天 WORM 回执，不宣称提前完成。

> **2026-07-30 实时纸面控制面与公共行情运行闭环修复：**
> Data Catalog 每 5 秒读取真实 console API，默认只呈现实时流核心指标；API/WS 全目录、
> systemic/RPI、漂移、限流、数据集、特征与血缘保留在可展开审计区。本地 SPA 对
> `index.html` 强制 `no-store`，不再长期持有旧 mock 构建。163,240 行实时库的全链复验
> 单次已耗 18.6 秒，现移入独立线程每 5 分钟执行，5 秒快照不再被阻塞，并保留最近验证
> 时刻。Binance `insuranceBalance` 会在保留粗粒度 `updateTime` 时修改余额，现以接收
> 时刻作为可追加快照身份并保存 `source_update_time_ms`；basis 越过 `startTime` 返回的
> 已封存旧行也会被过滤。真实复验：5 分钟触发器自动拉起 stream；stream 14/14、age
> 0.8 秒、链有效；systemic 14/14、0 error、任务返回 0；纸面 SOR 计数持续增长。全链仍
> 仅为无凭证公共行情与 L2 纸面交易，`live_allowed=false`、
> `order_submission_allowed=false`。

> **2026-07-30 Binance 长驻流自愈调度修复：**
> 真实运行发现 `binance_realtime_stream` 固定 23 小时正常退出，但旧触发器仅为
> AtStartup + 每日 00:05；当 00:05 到来时旧实例仍在运行，
> `MultipleInstances=IgnoreNew` 会吞掉当天唯一重启触发，实例随后白天退出后便可留下
> 近 24 小时空窗。实况中 Binance book 自 13:42 UTC 起过期，paper SOR 连续
> fail-closed，手工重启公共 WebSocket 后立即恢复 4/4 intents、8 fills、4 条 TCA、
> residual=0。现将第二触发器改为每 5 分钟轮询：运行中仍忽略重复，正常退出或崩溃后
> 最多一个 paper cadence 自动拉起；watchdog 对已退出 stream 的 freshness 也由 2 天
> 收紧到 15 分钟。全程公共行情、paper-only，不增加凭证、broker 或订单权限。
>
> **2026-07-30 外部 GitHub OIDC Sigstore 与跨主机源码复现实证：**
> GitHub Actions run `30540314233` 已在固定的 `ubuntu-22.04` 与
> `ubuntu-24.04` 托管执行器上，从同一 Git commit 的 blob 独立构造 canonical
> PAX+gzip 源码包并逐字节比对通过；随后由最小权限的 GitHub ambient OIDC
> workload identity 通过 Fulcio 签发、SCT 与 Rekor v1 透明日志签入
> `sigstore==4.4.0` DSSE/SLSA v1 bundle。仓库保留 receipt、manifest、bundle、
> predicate 与 signer 使用的 trusted root，离线 verifier 独立复验 GitHub
> identity、证书链、SCT、Rekor inclusion proof、DSSE subject、源码 commit、
> 两个 builder 和所有哈希绑定；GitHub artifact `8758408888` 的 ZIP digest 与
> 90 天到期时间也被固定。供应链成熟度因此由 M2 升至 M3，但仍不宣称 M4/PASS：
> 跨主机证明目前是源码包而非 deployable container，GitHub artifact 不是 WORM，
> 且尚无独立准入部署、外部回执权威、撤销源或外部权威链头封存。该工作流无仓库写、
> secret、部署、broker、订单或实盘权限，L2 边界不变。
>
> **2026-07-30 Binance systemic 限流域真实收敛（14/14 已复验）：**
> 30 分钟 systemic worker 的间歇性 418 经端点级遥测定位到
> `/futures/data/basis`，不是 ADL、保险基金或指数成分。按 Binance 当前官方契约，
> `symbolAdlRisk` 与 `insuranceBalance` 均改为每轮一次的无 symbol 全市场共享快照，
> 再 fail-closed 映射到 14 个目标合约；计划 IP weight 从 56 降为 30，请求间隔由
> 250ms 放宽到 3 秒。旧实现还会请求尚未闭合的当前小时 basis，再由解析器全部丢弃；
> 现只补 `next_start + 1h <= now` 的不可变小时，并按最旧水位每轮最多 4 币，30 分钟
> cadence 在 2 小时内轮转 14 币且满足 3 小时 freshness gate。429/418 现在解析正文
> `IP banned until <epoch-ms>`，并把真实
> 截止时间和触发端点跨调度进程持久化；截止前的新轮次零网络请求，避免旧实现仅凭
> 缺失的 `Retry-After` 猜 120 秒后反复撞限流。解禁后首轮又暴露一个独立 PIT
> 时间问题：3 秒串行轮次后半段的指数成分响应发生在轮次启动时刻之后，旧代码却把
> 启动时刻误作知识时间，因而错误判为“来自未来”。现以每个 HTTP 响应真正接收完成
> 的时刻记录 `knowledge_time`，同时继续拒绝晚于接收时刻的交易所事件时间。34 项
> 聚焦回归通过；S4U 后台真实复验为 14/14 PASS、0 失败、哈希链有效、计划 IP weight
> 30、实际 `X-MBX-USED-WEIGHT-1M=131`，Console Data Catalog 总体 PASS。全部仍是
> 无凭据 USD-M 公共市场数据，不增加账户、broker、订单或 live trading 能力。
>
> **2026-07-29 Windows 后台任务彻底隔离交互桌面：**
> 复查确认所有 L2 任务虽然已使用 `pythonw.exe`，但注册时仍继承默认
> `InteractiveToken`，因此并未从结构上排除未来子进程触达当前桌面会话。
> `scripts/register_schedules.ps1` 现统一使用 S4U 非交互 principal，并保持
> `pythonw.exe` 与 IDE 内日志；`tools/l2_schedule_entrypoint.py` 另在 pythonw
> 模式为任何后续 `subprocess.Popen` 强制追加 Windows `CREATE_NO_WINDOW`，显式
> `CREATE_NEW_CONSOLE` 请求会 fail-closed。全部计划任务已按新 principal 重新注册，
> XML 实况为 `<LogonType>S4U</LogonType>`；真实 Binance REST 轮次在该身份下
> 14/14 PASS，长驻 WebSocket 状态水位继续更新。该变更只隔离窗口与日志呈现，
> 不改变 L2 数据、纸面执行、凭证或实盘权限。

> **2026-07-29 Binance systemic worker 限流合同与 Console 可观测性：**
> 真实 30 分钟 worker 暴露了 HTTP 418（旧实现 8 并发，轮次停在 11/14），现按官方
> IP 限流合同改为单并发、250ms 请求间隔，并采集每个响应的
> `X-MBX-USED-WEIGHT-*`；HTTP 429/418 会读取 `Retry-After`、打开共享冷却并阻止
> 同轮后续网络请求。四端点每币计划权重为 4，14 币整轮为 56。真实恢复轮次已
> 14/14 PASS、零失败、链有效；API/Console 现展示计划/观测权重、并发、节奏、
> 状态码与冷却状态。全部仍是无凭据 USD-M 公共市场数据。
>
> **2026-07-29 paper SOR TCA 精确对账修复：**
> 连续 143 个 PASS 周期后，XRPUSDT 930 数量的序列化 VWAP 除法舍入被数量放大，
> 导致 verifier 在 `1e-24` 边界误报 `route TCA record is invalid`。校验器现复用已经
> 独立验证的逐场所原始成交名义额与成本重建 exact all-in，而不再用已序列化 VWAP
> 反推总额；真实任务重跑返回 0，回归样本固定该数量级。该修复只提高纸面 TCA
> 恒等式稳定性，不改变路由、费用假设或任何实盘能力。

> **2026-07-29 Binance 低频 worker 的 watchdog 契约收口：**
> 已正式注册的 `QUANTOS_binance_systemic_risk`（30 分钟）与
> `QUANTOS_binance_rpi_depth`（1 小时）此前遗漏于
> `tools/l2_scheduler_watchdog.py::EXPECTED_TASKS`，因此会被每 15 分钟健康检查误报为
> `UNEXPECTED_ENABLED_TASK`。现已按各自真实调度频率固定 module/action 与最大新鲜度
> （90 分钟 / 3 小时），并新增回归测试防止计划任务注册表与 watchdog 允许清单再次漂移。
> 该修复只纠正 L2 公共市场数据 worker 的运行监控，不改变数据权限、纸面逻辑或实盘边界。
>
> **2026-07-29 Binance 官方 USD-M WebSocket 市场流 20/20 收口：**
> 新增 `config/binance_usdm_ws_stream_coverage.json` 与纯验证器
> `services/data/binance_stream_coverage.py`，逐项登记官方 Public 5 类 + Market
> 15 类流；当前 20/20 accounted、8 load-bearing、9 redundant、3
> not-applicable、0 GAP。现有双路长驻流新增 14 币
> `{symbol}@rpiDepth@500ms` 和全市场 `!contractInfo`：前者逐事件校验
> `U/u/pu` 连续性并把 RPI 更新/删除窗口写入 5 秒哈希链快照，明确不重建完整盘口、
> 不假设纸面可成交；完整 RPI 恢复基线仍由小时 REST 1000 档快照承担。后者在真实发生
> 上市、结算、状态或 notional bracket 变更时保留事件，稀疏无事件不冒充故障。
> 真实 20 秒 14 币烟测 PASS：28,155 事件，其中 RPI 515、14/14 完整、0 连续性断点、
> 0 拒绝帧、双连接与存储链有效；后台长驻任务已无窗口切换到新版本并持续 PASS。
> Data Catalog API/页面新增 Official WS Catalog 20/20、承载流 8、缺口 0 与契约
> VALID。全链仍为公开市场数据、L2 研究/纸面专用，不读取凭证、账户或用户流，不连接
> broker，也不提交订单。

> **2026-07-29 Binance 官方 USD-M 市场目录 34/34 收口：**
> 新增独立低频公开数据 worker `tools/binance_systemic_risk.py` 与
> `tools/binance_rpi_depth.py`。前者每 30 分钟保存 14 币官方 ADL risk、完整可用
> 30 天小时 perpetual basis、指数成分价格/权重/离散度和保险基金资产余额；后者因
> 官方 RPI endpoint 固定 1000 档且单币 IP weight 20，隔离为小时级完整快照。
> 两条通道均使用 append-only SHA-256 SQLite 链、原始响应归档、源事件时间与接收知识时间，
> 无凭证、无账户、无 broker、无订单能力。真实首轮均为 14/14 PASS；systemic basis
> 回填 10,080 条小时记录，RPI 保留交易所实际返回档数（例如 ATOM bid 340/ask 1000，
> 不补造）。官方 34 项 REST Market Data 覆盖契约现为 34/34 accounted、23 load-bearing、
> 0 GAP；这只证明公开市场数据工程覆盖，不代表实盘或生产成熟度。

> **2026-07-29 Binance 官方市场目录覆盖与参考价面补齐：**
> 5 分钟 USD-M 公共快照新增 `/fapi/v1/fundingInfo`、`/fapi/v1/markPriceKlines`、
> `/fapi/v1/indexPriceKlines`、`/fapi/v1/premiumIndexKlines`。每个标的保存最近闭合
> 1 分钟 mark/index/premium OHLC、同分钟 mark-index basis，以及交易所明确返回的资金费
> cap/floor/interval 调整；adjustment-only 接口未返回某标的时保留 unknown defaults，
> 不猜测。真实 BTC smoke PASS，三套参考价 bar 的 close time 完全对齐，观测到当前
> cap/floor 为 ±0.003、interval 8h。新增机器可读
> `config/binance_usdm_market_data_coverage.json`，逐项登记官方 Market Data 34 个 REST
> 接口并由纯校验器 fail-closed；该阶段当时为 29/34 已解释、18 个承载、5 个真实缺口，
> 现已由上方 34/34 增量收口。Data
> Catalog 已显示总覆盖、承载数及缺口路径。全链仍是无凭证公开数据、L2 研究/纸面专用；
> Account/Trade/User Data Stream 未纳入，实盘权限没有扩大。
>

> **2026-07-29 终端弹窗根因收口：**
> L2 计划任务虽然已统一为 `pythonw.exe`，但 `QUANTOS_scheduler_watchdog`
> 每 15 分钟仍会用普通 `powershell.exe` 查询 Task Scheduler，形成周期性终端闪窗；
> 现已对该子进程强制 Windows `CREATE_NO_WINDOW`。8440 Ops API 的只读
> run/preview/demo-funds 子进程也采用同一无窗口约束。旧的可见 `cmd/python.exe`
> 常驻前端、Console API、Runner 与 Ops API 进程已按精确 PID 停止，并分别通过
> `tools/local_console_stack.py` 的无窗口管理器和 `pythonw.exe` 重启；3105/8400/
> 8420/8440 均恢复监听。前三项日志与 PID manifest 在
> `.runtime/console-stack/`，Ops 与计划任务日志在 `.runtime/logs/`，可直接从 IDE
> 查看。手动触发 watchdog 已正常回到 Ready；退出码 1 来自既有
> `RESTORE_HISTORY_FAILURE_IN_CLEAN_LOOKBACK` breach，不是进程启动故障。

> **2026-07-29 Binance 合约规则与 24h 市场统计进入自动纸面执行：**
> 5 分钟公共 USD-M 快照新增 `/fapi/v1/exchangeInfo` 与
> `/fapi/v1/ticker/24hr`，对 14 个前向标的逐一验证 `TRADING`、`PERPETUAL`、
> USDT quote、`PRICE_FILTER`、`MARKET_LOT_SIZE/LOT_SIZE` 和
> `MIN_NOTIONAL`，并保存合约生命周期、tick/step/min/max quantity、最小名义额、
> 24 小时 OHLC、加权均价、base/quote volume 与成交笔数。任一标的缺失、规则异常或
> 24h 行情超过 5 分钟即阻断整轮完整性。`paper_order_preview` 在完整九引擎研究风险
> 获批后，必须读取 15 分钟内的该快照，并把真实 step/minQty/minNotional 传入同一
> 干跑量化器；规则缺失、过期、停牌或非永续均不产生纸面成交。真实 Binance 首跑
> 14/14 PASS、14 条新哈希链记录、链有效；BTC 当次规则为 tick 0.1、step/minQty
> 0.001、minNotional 50 USDT。全程为无凭证公开数据，不扩大实盘权限。

> **2026-07-29 L2 自动任务改为无窗口后台运行：**
> `scripts/register_schedules.ps1` 不再让 Windows Task Scheduler 直接调用会弹控制台的
> `python.exe`，而是固定调用同一运行时旁的 `pythonw.exe`。承重入口
> `tools/l2_schedule_entrypoint.py` 在 pythonw 模式下把 stdout/stderr 逐 worker 追加到
> `.runtime/logs/<module>.log`，并记录 START/END/exit code；因此桌面不再被终端框打断，
> 日志可直接在 IDE 内查看，Task Scheduler 仍保留真实退出码。现有 L2 任务已全部重新
> 注册，实时流已切换到无窗口实例；实测 `binance_realtime_features.log` 记录完整 PASS
> 与 exit_code=0，实时数据总状态恢复 PASS。该变更只改变进程呈现与日志接收，不扩大
> 任何网络、凭证、券商、订单或实盘权限。

> **2026-07-29 Binance 公共实时仓位面补齐：**
> `tools/binance_realtime_market.py` 的 5 分钟全量快照新增官方公开
> `/fapi/v1/openInterest` 当前 OI 与
> `/futures/data/topLongShortAccountRatio` 大户账户多空比；原 5 分钟历史
> OI/OI 名义价值字段保持兼容，不改变既有消费口径。两项均执行 symbol、数值域、
> source time 与新鲜度 fail-closed 校验，并与盘口、mark/index/funding、K 线、
> 全市场/大户持仓比、taker 流一起进入原 append-only SQLite 哈希链和原始响应归档。
> 真实 Binance 首跑 14/14 完整、14 条新记录、链有效；Data Catalog API/页面展示
> 当前 REST 字段清单。实时 WebSocket 重启还暴露并修复了一个运营缺口：启动初期的
> incomplete-core 快照会诚实留在源哈希链，但特征 worker 现在只投影其中
> `complete_core=true` 的行，并在任何标的没有完整行时继续 fail-closed，避免一条保留的
> 重启诊断记录永久堵塞后续特征物化。真实恢复运行写入 3,213 个新特征值，98 条漂移
> 序列完整，特征批次链有效。数据仍为无凭证公共行情，只服务 L2 研究与纸面交易，不接私有
> 账户、券商或订单。外部模型 Provider 探针也已真实执行，但现有本地凭证返回 401，
> 因而未生成 PASS 证据，`AI.MODEL.REGISTRY.REDTEAM` 仍诚实保持 PARTIAL。

> **2026-07-29 Rank #13 收口——实时漂移经验校准 + 独立运营告警：**
> `services/data/distribution_drift_calibration.py` 现在把每条实时序列的最新 60+20
> 评估窗完全隔离，只在更早的 PIT 块内做确定性 60/20 置换，汇总经验同分布 KS
> 空分布的 95/99 分位；历史不足、非有限值、阈值退化均 fail-closed，不回退成伪校准。
> 首次真实运行覆盖 98 条序列、8,232 个校准窗，得到 Warn=0.3167 /
> Block=0.3833，内容寻址 calibration id
> `401fb51176389cb70dda2f2ab98a711aceb0370e0947810064195c44bbb82230`；
> 当前独立评估为 41 PASS / 8 WARN / 49 BLOCK，完整保留。新增独立 5 分钟 worker
> `tools/binance_realtime_drift_alerts.py`：只读特征状态，按 OPENED/ESCALATED/
> DEESCALATED/RECOVERED 去重，事件与投递结果写入禁止修改/删除的 SQLite 哈希链，
> 同时投递既有 operator JSONL；本地投递失败时任务非零退出并持续重试，成功后才去重。
> 首条 BLOCK 已 `DELIVERED`，重复运行
> `DEDUPLICATED`，链 2/2 有效。Telegram 未配置则明确 `UNCONFIGURED`，不冒充外部
> 送达。Data Catalog 与 AI Agent 页显示校准阈值、校准窗、独立 worker、本地/外部
> 投递和告警链。该闭环完成 Rank #13 的 L2 范围，但始终 diagnostic-only，
> 不驱动 Agent 晋级、仓位、订单或实盘。

> **2026-07-29 Binance 实时 FeatureValue 与 Agent 输入可视化：** 新增纯投影
> `services/features/binance_realtime_projection.py`，把 14 币 WebSocket 完整快照物化为
> mid、spread、5 档深度不平衡、mark basis、funding、5 秒 taker imbalance、5 秒
> liquidation imbalance 共 7 类、98 条 PIT `FeatureValue` 序列。物化缓存 append-only，
> 每批同时绑定源快照摘要并写入独立禁止更新/删除的哈希链
> `binance_realtime_feature_batches.db`。`tools/binance_realtime_features.py` 每 5 分钟重放
> 最近 3,500 个真实快照、幂等补写特征并消费 60/20 KS 窗口；首次真实运行写入 24,500
> 个值，98/98 序列结构完整，结果为 30 PASS / 19 WARN / 49 BLOCK。BLOCK 是未校准阈值下
> 的真实诊断，不是 worker 故障，也没有被删改。Data Catalog 与 AI Agent 页均显示特征
> 新鲜度、保留值、漂移分布和批次链；Agent 只能把它视为只读诊断上下文，自动动作与晋级
> 权限恒为 false。该首次未校准状态已由上方经验校准与独立告警增量收口。

> **2026-07-29 真实公共订单簿到多场所纸面 SOR 已自动化：** 新增
> `tools/paper_multivenue_sor.py`，从现有 Binance USD-M WebSocket 哈希链读取
> DOGE/XRP/ADA/DOT 的真实 20 档深度，并从 Bybit V5 公共接口取得对应 linear
> order book；两场所都必须新鲜、完整且时间偏差不超过 10 分钟，否则 fail-closed。
> 跨场所快照写入 append-only、禁止更新/删除且逐行哈希链保护的
> `cross_venue_order_books.db`，再通过已有 pre-trade gate、`DryRunAdapterEngine`、
> deterministic SOR 和 `DryRunStore` 生成逐场所纸面成交及 route TCA。5 分钟 Windows
> 调度、watchdog、只读 API 和干跑页状态均已接入。真实烟测连续两次 PASS，每次 4 个
> route plan / 4 条 TCA，均覆盖 `binance_usdm + bybit_linear`，纵向投影已达到
> `LONGITUDINAL_READY`。这是执行质量金丝雀，不是 Alpha，不声明策略边；费用为
> `public-paper-fees.v1 / UNCALIBRATED_RESEARCH_ASSUMPTION`，延迟惩罚为 0（未伪造测量）。
> 全链无凭证、无私有账户、无 broker 连接、无订单提交，绝不升级为生产 SOR/TCA。

> **2026-07-29 证据修复与纵向纸面 TCA 增量：** 上一条状态中列出的 npm、Trivy
> 与 artifact-admission 阻断已完成修复：前端生产依赖审计为 0 漏洞，运行镜像
> Trivy 证据为 0 漏洞，制品授权注册表已按不可变历史轮换至 generation 1；强身份
> 实现摘要重生后恢复为 M3/PARTIAL，供应链检查器也已改为绑定当前注册表 generation、
> 当前 seal 实现与测试，不再硬编码旧 generation 0。Top-tier 当前实况为
> **15 PASS / 6 PARTIAL / 1 FAILED**；唯一 FAILED 是恢复演练的自然历史仍未满足
> 30 天干净回看与成功次数，既有失败没有被删除或伪造。
>
> 多场所纸面执行新增 `services/execution/paper_tca_history.py`：直接读取 append-only
> `DryRunStore` 中已有的逐路由 TCA，逐条复验 SHA-256、路由绑定与算术恒等式，再生成
> 跨运行的 route/venue 时间序列、名义额加权 implementation shortfall 和场所归因。
> 一个时间点只标 `SINGLE_SNAPSHOT`，至少两个不同运行时间才标
> `LONGITUDINAL_READY`；普通单场所干跑明确显示 `NO_ROUTE_TCA`。该只读投影已进入
> `/api/v1/console/dry-run` 和干跑页的“多场所纸面 TCA”面板。它只证明 L2 仿真工程
> 准备度，不连接券商、不提交订单，也不冒充生产多场所成交证据。

> **2026-07-29 Binance USD-M 公共实时数据流与 AI Agent 实况界面：** 新增
> `tools/binance_realtime_market.py`（14 个前向主币、每 5 分钟完整 REST 快照）和
> `tools/binance_realtime_stream.py`（按 Binance 新路由拆分 `/public` 与 `/market`
> 双 WebSocket，采集 bookTicker、20 档深度、1 秒 mark/funding、aggTrade、1 分钟
> kline 与全市场强平）。高频帧每 5 秒归并为逐币完整快照，写入追加式 SQLite 哈希链；
> 连接重试、24 小时前主动轮换、事件时间/知识时间、覆盖率、延迟、拒绝帧与断线状态均
> 显式记录。真实烟测为 14/14 核心覆盖、10,968 个事件、0 拒绝帧，Windows 长驻任务
> 随后已启动并持续写入；REST 首轮同为 14/14 完整。两条路径只使用公开市场数据，
> 不读取凭据、不连私有流/券商、不提交订单、不改变 L2 干跑边界。
>
> Console 数据目录新增 Binance 实时健康面板，只有状态文件在 30 秒内新鲜且两条连接
> 都健康时才显示 `LIVE`，已结束烟测不会被误报在线；后端可通过仓库外
> `QUANTOS_STATE_ROOT` 读取隔离运行目录。原 `/agents` 的三位虚构人格、固定运行次数和
> 演示预算已删除，替换为真实 `RunRegistry` 活动、唯一 guardrailed
> `ResearchAgentLoop`、四个已测试搜索引擎能力和硬隔离边界；Agent 自动批准恒为 0，
> 无凭据、券商、下单或 live 能力。
>
> 本增量聚焦验证为后端 92 项、前端 124 项与生产构建全绿；Python audit（0 漏洞）、
> event schema catalog 和 service import graph 已重生并通过 current 检查。全仓统一门
> 当时仍被独立的供应链/运营证据阻断；这些供应链项已由上方更新修复。恢复历史仍保留
> 真实失败且未达到 30 天，上述实时数据与 Agent 功能不据此冒充全仓绿色。

> **2026-07-29 前向 Paper 九引擎输入自动化（保持 fail-closed）：** 新增
> `tools/paper_alpha_evaluation.py`，为 `carry_oi_majors`、`crowd_contra`、
> `top_contra` 铸造内容寻址的研究定义，并把评估契约、完整全局 trial 分母、DSR/PBO
> 与精确 OOS 收益绑定进生产 experiment registry；三份评估均可独立重放验证，但在
> 2539 次全局试验分母下 verdict 均为 `FAIL`，不会被伪装成可部署 Alpha。修复了
> `panel_backtest` 跨 `PYTHONHASHSEED` 的浮点累加次序漂移，并将旧 16-hex trial token
> 一对一命名空间哈希为完整 SHA-256 证据 ID，原始追加账本与试验数不变。
> `tools/paper_research_risk_bundle.py` 已从真实 PIT ADV、Frankfurter/ECB CNY 汇率、
> 对齐历史 Alpha 收益、VIX regime、维护状态、前向账本和已验证评估生成同决策绑定包；
> 当前三条决策的六类输入中，容量、成交量、AUM、收益和 regime 已真实填充，只剩
> `alpha_metadata`。这是刻意的时间因果门：当前决策早于新评估，禁止事后证据回填。
> Windows 调度已增加每周日 20:45 评估，并在 21:00 前向推进前完成；风险包、预览、
> 10k/健康和 watchdog 继续接受安全的 partial exit code。当前仍为 `PAPER_BLOCKED`：
> 前向周期为 0/8、三份验证 verdict 不达门槛，且不增加网络、凭据、broker、下单或
> live 能力。

> **2026-07-21 路线图校正：** Rank #9「全局多重检验预算账本」已经完成，不再是待开发项。机器评估与能力矩阵均为 `ALPHA.TRIAL.GLOBAL_LEDGER = M4/PASS`；可移植历史链、封存快照、精确评估绑定和追加保护证据见 `evidence/alpha_trial_ledger_history.json`、`evidence/alpha_trial_ledger_snapshot.db`、`evidence/alpha_trial_ledger_snapshot_seal.json`。后续工作仅是持续维护证据，不把内部已完成能力冒充外部成熟度。

> **2026-07-21 组合级统一约束门落地（L2 可用增量）：** 新增纯函数
> `services/portfolio/portfolio_constraints.py`，把经验 CVaR 尾部预算、firm gross/net 与单名集中、
> 容量瓶颈、拥挤证据合并为带 provenance 的 `PASS/WARN/PENDING/BLOCK` 结果；缺少已请求证据为
> `PENDING`，非法值、覆盖不足或超限为 `BLOCK`，只有 `PASS` 可作为运行时放行。门已在
> constrained optimizer、post-rebalance construction、signal adapter 三个组合边界可选承重，
> signal adapter 只用调用方提供的 AUM/ADV 推导名义，不伪造缺失 ADV。49 项聚焦组合/治理测试通过；
> 仍是 L2 研究/干跑控制，不是 venue 校准的保证金或盘中风控服务，机器顶级判定保持 **15/22、
> NOT_TOP_TIER**，不增加网络、凭据、券商、下单或 live 能力。

> **2026-07-21 Rank #13 数据分布漂移诊断落地（部分升真）：** 新增纯函数
> `services/data/distribution_drift.py`，用不重叠的 60 条基线/20 条近期窗口和 exclusive
> `end_index` 计算两样本 KS 距离，非法策略、非有限输入、历史不足全部 BLOCK；默认 WARN=0.20、
> BLOCK=0.35，明确标 `UNCALIBRATED_PRINCIPLED`，只读不改数据/权重/晋级。`DefaultDataConsolePort`
> 从真实 bitemporal `crypto_daily.db` 提取 PIT close-to-close 序列，监控页在数据存在时新增
> `DATA_DISTRIBUTION_DRIFT` 行；实证根数据 42 个序列，24 PASS / 18 WARN / 0 BLOCK（样本长度
> 111 或 363）。无数据库时不伪造漂移行，旧 `FEATURE_DRIFT` 仍显式标 synthetic，直到特征值缓存
> 真正填充。该增量不改变 **15/22、NOT_TOP_TIER**，不增加网络、凭据、券商、下单或 live 能力。

> **2026-07-21 Rank #13 特征级漂移证据已补齐（仍为诊断）：** 新增
> `tools/feature_distribution_drift_research.py`，从已封存并验证的 FX PIT bundle 重放三个因果
> close 特征，以截面中位数形成时间序列，再复用 60/20 窗口 KS 内核。证据制品为
> `evidence/feature_distribution_drift_baseline.json`，当前三个特征均为 `BLOCK`；这是真实的
> 数据警告，但明确 `load_bearing=false`，不驱动晋级、仓位、干跑或订单。实时/纸面 feed、定时
> `FeatureValue` 缓存、阈值校准和运营告警仍未完成；机器结论保持 **15/22、NOT_TOP_TIER**。

> **2026-07-20 动态相关漂移检测完成，自动缩放被执行现实证伪并保持关闭：** 新增纯函数
> `services/portfolio/correlation_drift.py`，以互不重叠的 52 周基线/8 周近期窗口、exclusive
> `end_index` 和逐对 Pearson 相关计算 `PASS/WARN/BLOCK`；错位、非有限值、历史不足、零方差和
> 非法策略全部 fail-closed。`capital_allocator` 在可选策略下返回明确命名的
> `correlation_gross_recommendation`，默认配置与归一化 sleeve 权重不变。真实
> `carry_crowd_risk_parity` 驱动器已接入评估并把 143 PASS / 68 WARN / 23 BLOCK、最新窗口与
> provenance 写入干跑 detail，但实际缩放固定为 0 期/1.00 倍。原因不是少做：严格同样本控制中，
> 无条件缩放把 RAW/VT 夏普从 0.6098/1.1173 降至 0.5591/1.0227；预先定义的“相关告警 + 上期
> 双 sleeve 同亏”虽在 70/30 近似 OOS 降回撤，却在完整门+整手+成本+资金费+vol-target 管线只把
> VT 回撤从 17.51% 微降至 17.46%，同时夏普降至 1.0922、RAW 回撤仍为 55.77%，故冻结为
> `DISABLED_BY_EXECUTABLE_CONTROL`，不继续调参追结果。21 项聚焦测试已通过；该研究增量不改变
> **15/22、NOT_TOP_TIER**，也不增加网络、凭据、券商、下单或 live 能力。

> **2026-07-20 Rank #19 复现底座完整收口：** 承重数字指纹已从 dry-run、IC 面板、
> 容量扩展到 `scenario_tail` 与 `macro_regime`。新增共享提取器固定两条 book 的样本数、
> 历史危机收益、最差回撤、CVaR、Hill 尾指数、BTC 下尾依赖，以及 VIX/收益率曲线
> regime Sharpe、信用覆盖期和 ALFRED 缺席标记；来源不是
> `REAL_HISTORICAL_REPLAY` / `REAL_FRED_MACRO` 会失败关闭。两个版本化 digest 已对正式运营根的
> 离线快照逐项重放通过；干净 checkout 缺缓存时继续诚实 skip，不把“不可核验”报成通过。
> 同时修复 linked Git worktree 下 `code_version=unknown`：版本提取现在解析 `.git` indirection、
> `commondir`、loose refs 与 packed refs，并由合成 worktree 测试钉死。Rank #19 不再有剩余项；
> 机器顶级判定仍为 **15/22、NOT_TOP_TIER**，因为外部身份、连续灾备历史、外部供应链签名、
> 持牌数据和 post-L2 多场所执行不因研究证据扩面而假升级。
>
> **2026-07-20 Rank #12 验证 provenance 已完整收口：** 共享冻结契约
> `CatalogEvidence` 与 `BatchPboEvidence` 现在分别保留 catalog 的精确收益序列/Sharpe 总体/
> 可比较相关矩阵/有效试验数/DSR，以及 CSCV 的精确 block×candidate 矩阵/PBO。只有
> `AlphaCatalog.evidence()` 能从完整试验账本铸造目录证据；无法比较的历史试验按一项一个独立试验
> 保守计数，不会被少量相关序列间接折减。完整候选池必须先冻结并写入 catalog，再产生第一个裁决。
> evaluator、严格验证门和 standalone pipeline 已删除裸 DSR/PBO/trial-count 信任路径；
> `ExperimentRegistry.verify_evaluation` 会独立重放 DSR、PBO 与 exact-float reproducibility hash。
> `VALIDATED`、`PAPER_TRADING` 和最终人工晋级均绑定完整 catalog snapshot，目录收益即使只变化
> `1e-11` 也会使旧证据失效。console 只公开哈希、计数和相关性摘要，不泄露保留收益/矩阵。
> 该增量不增加网络、凭据、券商、下单或 live 能力；机器顶级判定仍为
> **15/22、NOT_TOP_TIER**，未把内部证据硬化冒充外部数据、身份或运营成熟度。
>
> **2026-07-20 外部 AI provider 治理接缝已落地，但 live 凭据未通过：** 新增版本化
> `config/model_provider_registry.json`，默认仍为 `offline_registered`，仅显式选择
> `sushuo_gpt_5_6_sol` 才会访问固定的
> `https://vip.sushuo.biz/v1/chat/completions` / `gpt-5.6-sol`。网络只存在于
> `tools/external_model_provider.py`；`services/`/`quantos/` 继续零网络。外部调用必须先通过注册
> `ModelCard`/`PromptTemplateCard`/`ToolManifest` 和 `GovernedModelRuntime`，唯一动作
> `EVALUATE`，审批、kill-switch、凭据、订单和 live trading 全部禁止。证据设计只保留请求/响应/
> 内容哈希、模型、用量、TLS leaf 指纹和运行时事件链，不保存 key、key 路径、原始 body 或模型正文；
> 独立 verifier 会重算注册、lineage、event/self/source/test hashes 和 7 项 provider 红队。
> 本轮本地 35 项聚焦测试通过；随后用用户指定 KEY 分别通过 Python stdlib HTTPS 和原始 Node
> `fetch` 形式做无业务数据 echo probe，两者均由服务返回 **HTTP 401**，所以没有生成或提交
> `evidence/external_model_provider_baseline.json`，`AI.MODEL.REGISTRY.REDTEAM` 继续诚实保持
> M2/PARTIAL。未来只需更新有效 `NEW_API_KEY` 或仓库外 key 文件并重跑 probe；只有 live 证据通过
> 独立 verifier 后该项才自动升 M3。`VALIDATION.CHALLENGER.MODEL_RISK` 仍另需强外部人类评审身份，
> 不随 provider 接入假升级。

> **2026-07-20 多资产研究证据从伪绿补成 M3 真基线:** `ALPHA.MULTI_ASSET.RESEARCH`
> 不再读取未跟踪的本机 equity cache。现把已版本化的 Frankfurter/ECB FX PIT bundle
> （18 个可交易货币、126,113 行、1999-01-04 至 2026-06-26）正式接入 `fx_tradable`
> IC 宇宙；同一研究核重放 `fx_momentum_120` 与 `fx_low_vol_60` 在 1/5/20 日的非重叠
> 截面 IC。治理侧不 import research/tools，而用独立 stdlib 实现逐行复核 PIT 身份、
> `knowledge_time <= event_time`、bundle/manifest/实现/测试摘要、自摘要及全部统计结果；
> 干净 checkout 无需网络或 `data/cache/fx.db` 即可复算。
>
> **研究结论仍诚实为 null:** momentum mean IC/t-stat 为 h1 `-0.0003/-0.0665`、
> h5 `-0.0050/-0.5233`、h20 `-0.0094/-0.5035`；low-vol 为 h1
> `-0.0049/-1.1136`、h5 `-0.0050/-0.5235`、h20 `-0.0063/-0.3351`。
> 这次关闭的是“可版本化、可重放的非加密研究能力”缺口，不是发现了 FX alpha；证据固定
> `DIAGNOSTIC_ONLY_NO_PROMOTION`，不触发晋级、部署或交易。机器矩阵由 **14/22、1 FAILED**
> 恢复为 **15/22、0 FAILED、5 PARTIAL、2 项外部/人工证据、3 条红线开放**，总 verdict
> 仍为 `NOT_TOP_TIER`。

> **2026-07-19 Pass M 精确家族证据收口 + 供应链实证刷新:** 便携 ALPHA 试验账本的
> exact-v3 家族模式已落地为 33 张家族表 / 401 个 SQL 对象 / 124 个命名绑定 /
> 228 条结构规则 / 9 个受控 UDF；静态实现封印为
> `c1a61a41daa67f58e0d7ede45dff0fe127fc36dba3a8ab3bc4a22657bab98f07`。
> 运行时只允许 17 条精确状态迁移，未列明调用一律拒绝；v2→v3 激活覆盖 405 个故障注入点，
> 13 张继承表非空迁移保持字节/逻辑不变，7 个已填充回滚点在关闭重开后仍精确恢复，33 张新表的
> UPDATE/DELETE 不可变性已做真实 SQLite 验收。C5 已拆为 proposal registration + 唯一 terminal parse
> result，消除二次链与未终结解析歧义；调用方 UDF 残留、伪造 owner token、同连接重入和双连接竞态均
> fail-closed。当前持久激活仍禁止，`CodeManifestEvidence` 只证明哈希绑定，不冒充外部批准或来源证明。

> 同轮修复了模型风险证据“实现已变但复用 artifact version”的真实治理缺口：生成器现在保留历史制品与
> runtime event 前缀，并通过新 release 追加；当前证据为 39 个制品、6 条运行事件、4 个哈希链修订，
> 模型/挑战者仍诚实停在 M2（负 OOS、无外部模型提供方/强审阅身份）。强身份本地基线重算为 M3。
> 供应链已用 Trivy 0.72.0 官方 SHA-256 校验包、2026-07-19 新数据库、两次真实 `--no-cache`
> OCI 构建重做：归档字节一致（SHA-256
> `1e584493f7d68e97dddd8842ef99179b49f01c840b4d387887d0c34a14897ade`），最终运行镜像
> 0 漏洞，基础镜像 5 项中低风险记录，534-component SBOM/基线检查通过；只读、无网络、UID 65532
> L2 冒烟通过。Docker Desktop 与临时扫描/归档文件已回收。

> 2026-07-20 03:00 的已注册 `QUANTOS_backup` 任务在正式运营根完成真实加密备份并返回 0；10 个
> required authority store 均已备份到独立卷、隔离恢复、摘要与 SQLite 完整性复验通过，权威恢复历史
> 追加到 event seq 26。该真实证据已同步进发布工作树，`backup-evidence:current` 恢复 PASS。
> 合并后最终统一门禁实跑 **19/19 阶段通过**：主后端 4227 passed / 5 skipped；live/trading/tools
> 1063 passed；前端 69 tests + typecheck/production build 全通过；治理扫描 351 文件 / 0 违规；漏洞、事件
> schema、服务图、供应链、模型风险历史、拥挤校准、顶级评估、备份新鲜度和风险恢复演练全部通过。

> **2026-07-20 干净 checkout 证据审计更正（本轮补齐前基线）：** 当时机器结论为
> **`NOT_TOP_TIER`：14/22 PASS，1 FAILED，5 PARTIAL，2 项需外部/人工证据，3 条红线开放**。
> 唯一 FAILED 是 `ALPHA.MULTI_ASSET.RESEARCH`：此前仅凭未纳入版本控制的本地
> `data/cache/equity_universe_snapshot.json` 判 PASS，GitHub/Linux 干净 checkout 无法重放；现已改为必须提供
> `evidence/multi_asset_research_baseline.json`（版本化非加密 PIT bundle + 可重放因子结果），禁止本地缓存伪绿。
> 其余未完成项不是代码伪绿：外部模型提供方与独立强身份审阅、真实 OIDC/TLS/密钥与身份生命周期、30 天恢复
> 历史 + 外部 Ed25519/WORM、跨主机可复现及外部签名/收据、持牌数据深度、post-L2 多场所执行。单次
> L2 backup 已新鲜且真实，但不冒充 30 天运营历史或外部检查点；Phase S 与持久部署激活仍开放。

> **2026-07-16 Wave0后P2质量改进:** import-linter依赖治理已引入（4个合约，检测到
> research↔alpha 16处循环依赖，live/隔离与contracts清洁度已验证）；147个空DDD目录
> 已用PLANNED.md标记为架构占位；console_api已验证为按router拆分（7548行分布多视图）；
> L2备份调度代码完整但待积累30天运营证据。GAP_MATRIX维度1已更新反映改进。
> 最新提交：53f3715（docs更新）、45b72e3（import-linter）、b3eb210（DDD标记）。

> **2026-07-15 顶级续审与恢复证据纠偏:** 当前机器权威仍是
> `config/top_tier_requirements.json` + `services/governance/top_tier_assessment.py` +
> 两个生成制品；07-07 手工审计只作历史快照。制品准入已推进到 V4 的双 registry-head
> 事务 CAS，强身份已推进到本地 M3 的应用授权 authority，L2 backup 已推进到 v2 精确
> source contract（10 required + 6 optional，含 approvals/identity/restore-history authority）。续审发现并关闭
> `OPS.RESTORE.DRILL` 假绿：旧 generic live/testnet restore JSON 不再参与顶级计分，单次
> `l2_backup_latest.json` 固定封顶 M3。恢复红线现消费 STARTED/OUTCOME append-only history，
> 动态检查 30 天 cadence/freshness/失败窗口，并要求 exact current-head Ed25519 external checkpoint；
> 默认 trust registry 为空，故不会本地制造 M4。最终完整门禁实跑：backend 2571 passed / 1 skipped，
> 仅 service-graph 与 supply-chain tracked evidence 两项漂移失败；live/trading/tools 986 passed；
> frontend 68 tests + typecheck/build；治理扫描 318 files / 0 violations。
> strong-identity、service graph、supply-chain 与 assessment/matrix 制品仍待获准重生成，禁止沿用旧
> `15/22` 作为当前结论。详见
> `docs/audit/2026-07-15-top-tier-continuation-review.md`。

> **2026-07-15 制品准入 V3 增量:** 软件供应链准入已从调用方自报 policy hash
> 升级为仓库治理的空策略注册表 + 严格 policy v3；稳定 policy 现钉住外部
> receipt authority/key ID/Ed25519 public-key SHA-256，短命动态 receipt 可独立轮换并验签，
> 无需改 policy/registry。新鲜度/撤销/证书有效期/运行时
> verifier/重复 JSON 键/签名载荷空白/时钟回拨均失败关闭。CLI 固定使用
> `data/cache/artifact_admission.db`，policy 决策必须先写入 WAL/FULL、不可更新删除、
> 哈希链 SQLite 权威后才输出；该生产库当前未配置，注册表也无 ACTIVE policy，故真实
> 环境仍为零 `ALLOW`；receipt-authority registry 同样为空。诚实边界不变：尚无真实
> 外部 authority/签名 receipt，source-native checkpoint/revocation proof 未验证，
> 仅支持 Rekor v1 `integratedTime`，无真实有效 bundle 集成夹具，SQLite 非 WORM 且无
> 外部链头封存，也无跨主机复现或独立 admission 部署。详见
> `docs/audit/2026-07-15-artifact-admission-v3-review.md`。最终统一门禁 18/18 全绿：
> 主套件 2420 passed / 1 skipped，live/trading/tools 972 passed，前端 68 tests +
> typecheck/build，治理扫描 313 files / 0 violations；Catalog 12 types/17 definitions，
> 服务图 16 domains/36 edges/0 cycles，供应链 320 components。机器结论保持
> `NOT_TOP_TIER`：15/22 PASS、5 PARTIAL、2 MANUAL、0 FAILED。

> **2026-07-15 event worker assurance 增量:** consumer-group 与 delivery journal 中的新
> worker 不再是无标签字符串，而使用保留内部 ID 并固定标
> `PROCESS_DECLARED_UNAUTHENTICATED`，该值进入既有 coordination/consumer SHA-256 链；
> public status 解码可读名并显式展示 assurance。旧行不重写且标 `LEGACY_UNLABELED`；同名旧活跃
> member 会先追加合法 `LEFT` 再切换，避免双占分区。真实 authority 演练链有效、health PASS，
> external heartbeat 只允许上报固定弱保证枚举，不发送 worker 名。此增量防止把进程名误报成强身份，
> 并不提供外部认证；OIDC/mTLS workload identity、密钥生命周期与 caller-injected lease clock 仍开放。
> 详见 `docs/engineering/specs/2026-07-15-process-declared-worker-assurance.md`。
> 两个 2026-07-15 增量最终统一门禁 18/18 全绿：主套件 2353 passed / 1 skipped，
> live/trading/tools 971 passed，前端 68 tests + typecheck/build，治理扫描 312 files / 0 violations；
> schema Catalog 12 types/17 definitions，服务图 16 domains/36 edges/0 cycles，供应链 320 components。
> 机器结论保持 `NOT_TOP_TIER`：15/22 PASS、5 PARTIAL、2 MANUAL、0 FAILED。

> **2026-07-15 数据质量准入权威增量:** `data_quality_admission.json` 不再只是可覆盖文件。
> 新 `DataQualityAdmissionStore` 将完整 canonical evidence、独立 evidence/decision SHA-256、
> 追加式决策链与 redacted `data.quality.admission.decided` outbox 在同一 SQLite 事务提交；
> publish-before-ack 可幂等恢复，恢复 drain 现覆盖 7 个 owner，console 只显示 ID/verdict/count/hash
> 白名单字段。`forward_track_advance` 已把该权威 verdict 作为记录/兑现的必要条件，authority 故障或
> `BLOCK` 均暂停证据钟；新库也进入 L2 加密备份必需源。真实本地运行仍诚实为
> `quality=BLOCK / lineage=BLOCK`，ID `data-admission-8bb26fcafd7e6672ec8d66ceb6fcfff5`，
> 已 publish/ack/project 且 pending=0。它只关闭本地双写/旁路缺口，不关闭 funding、外部 WORM、
> 强 worker identity、跨主机 HA 或任何 live 红线。详见
> `docs/engineering/specs/2026-07-15-data-quality-admission-authority.md`。

> **2026-07-10 权威覆盖层:** 顶级系统目标、成熟度和缺口不再使用手工完成度百分比。
> 当前机器真源为 `docs/QUANTOS_TOP_TIER_STANDARD.md`、
> `config/top_tier_requirements.json`、`evidence/top_tier_assessment.json` 和
> `docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md`。运行
> `python scripts/assess_top_tier.py --check` 可检测证据漂移；`python scripts/validate.py`
> 已先执行该检查。当前基线: `NOT_TOP_TIER`, 22 项要求中 15 项 PASS、5 项 PARTIAL、
> 0 项 FAILED、2 项需人工/外部证据、2 条红线未关闭。控制台 `/research/system-completeness`
> 已直接渲染同一证据源，不再生成 `completion_score` 或虚构模块。
> `OPS.RESTORE.DRILL` 已于 2026-07-10 用一次性进程内密钥完成真实加密备份与隔离恢复演练:
> 24 个文件逐字节 SHA-256 校验，RTO 773ms / 300000ms，RPO 2ms / 86400000ms；
> 证据在 `evidence/restore_drill_latest.json`，恢复目标从不覆盖活动状态。当前基线升至
> `NOT_TOP_TIER`, 14/22 PASS。
> `PLATFORM.CI.UNIFIED_GATE` 已升级为 M4:同一个 `python scripts/validate.py` 现在按顺序覆盖
> 能力证据漂移、治理扫描、全部后端测试、live 执行栈、前端 typecheck、前端 68 项测试和
> 前端生产构建；任一失败整体非零退出。当前基线升至 `NOT_TOP_TIER`, 15/22 PASS。
> `RISK.RECOVERY.DRILL` 已于 2026-07-12 完成隔离离线重启演练：持久化 kill-switch、
> pre-trade gate 熔断拒绝、SQLite 账本重建、审计链承诺、损坏状态 fail-closed 与审计篡改
> 检测全部通过，最近一次完整门禁 RTO 55ms / 300000ms；证据在
> `evidence/risk_recovery_drill_latest.json`。当前基线升至 `NOT_TOP_TIER`, 16/22 PASS；
> 剩余红线为 `GOV.IDENTITY.STRONG_AUTH` 与 `PLATFORM.SUPPLY_CHAIN`。
> `PLATFORM.SUPPLY_CHAIN` 已于 2026-07-12 建立确定性 CycloneDX SBOM、Python 直接依赖
> 固定、npm integrity 覆盖、GitHub Action 提交 SHA 固定与输入 manifest 摘要校验，平台工程
> 从 M1 升至 M3。真实 npm Registry 审计先发现 5 项（critical 1 / high 1 / moderate 3），已将
> `vitest` 从 2.1.9 升级并精确固定为 4.1.10，复扫为 0；保留证据在
> `evidence/vulnerability/npm_audit_latest.json`，统一门禁首关校验报告、lockfile 与 package manifest
> 一致。Python 初次审计先暴露 `pytest 9.1.1` / `pytest-asyncio 0.23.8` 不可重建冲突，修复后发现
> Starlette/PyJWT/cryptography 共 19 条 advisory；已升级到 FastAPI 0.139.0、Starlette 1.3.1、
> PyJWT 2.13.0、cryptography 49.0.0、pytest-asyncio 1.4.0、httpx2 2.5.0，复扫为 0，证据在
> `evidence/vulnerability/python_audit_latest.json`。三个 GitHub Actions 固定提交已逐一通过 OSV commit
> 查询并保留 0-known 证据。容器侧固定并校验 Trivy 0.72.0 官方归档：原 `python:3.12-slim`
> 扫出 2 critical / 18 high，不能采用；最终改为摘要固定的官方 Python 3.12 Alpine 多阶段构建，
> builder 编译科学计算依赖、runtime 移除 pip 并以 UID 65532 非 root 运行。基础层 5 条 medium/low
> 在最终镜像全部清零，最终 Trivy 结果为 0，且只读根文件系统下正式 API、安全原语与 promotion 组合导入冒烟通过；
> 证据在 `evidence/vulnerability/{actions_osv_latest,container_trivy_latest}.json`。同一固定输入已完成
> 两次 `--no-cache` OCI 构建，BuildKit `v0.26.2` 重写时间戳后两份归档逐字节一致：共同归档 SHA-256
> `d1bdd50a483da77a9124d4f67b4bc8ed589c04b592543007626d6fcdf3dddd9a`，共同 manifest
> `sha256:4fce371aee715f289891b965068e52e7bf7ce6cfc7fc67932f294ff658e134e9`；正式证据与篡改校验在
> `evidence/reproducibility/container_build_latest.json`，且已接入统一门禁。供应链继续补上只验不签的离线 admission
> 边界：固定 `sigstore==4.4.0`，调用方必须提供摘要钉死的 policy 与 trusted root，精确匹配 OIDC issuer/identity，
> 并由 Sigstore 验证 Fulcio 证书链、SCT、Rekor inclusion proof、DSSE 签名及 SLSA subject/builder/buildType/source；
> 任何缺失、漂移或歧义均 `DENY`，且不产生部署/执行/交易权限。诚实边界仍是同主机/同 builder、
> **不是当前源码制品 attestation**，尚无跨主机复现、
> QUANTOS 的真实外部身份签名 bundle、独立 admission 部署或刷新/吊销监控，因此该项仍为 M3/PARTIAL，红线不关闭。
> `AI.MODEL.REGISTRY.REDTEAM` 与 `VALIDATION.CHALLENGER.MODEL_RISK` 已于 2026-07-12
> 从手工不可核验的 M1 升至机器可复算的 M2：新增冻结模型/提示词/工具/评估/红队/挑战者契约、
> append-only SQLite 注册表、真实 `ResearchAgentLoop` 四案边界攻击、注册 prompt/tool 的离线 runtime
> action 边界、敏感输出拒绝与哈希链调用历史，以及基于真实 FX walk-forward 快照的 3 份离线评估。
> 证据在 `evidence/model_risk_baseline.json`。真实结果仍是
> `REFUTED_NO_EDGE`（Ridge -0.4035、GBM -1.1173、单因子 -2.0389），两个挑战者均因绝对
> Sharpe 小于 0 被拒；当前 18 个可交易 FX 标的的 126,113 行 PIT 评估子集已作为确定性 gzip JSONL
> bundle + manifest 纳入版本证据并逐行/哈希验证；现已保留两次真实修订和两条允许调用事件，但没有外部模型提供方、审查身份仅进程角色，
> 因此两项保持 M2/PARTIAL，不虚报 M3。
> `evidence/model_risk_history.json` 已从首条基线追加到第二条真实修订：v1 的 12 个制品与调用事件保持不变，
> v2 真实执行 action override、敏感输出和禁用工具动作三类 runtime 攻击，新增第 2 份红队报告与第 2 条允许调用。
> 同版本改内容、丢弃旧制品、复用调用 ID 改写负载、丢弃/重排旧调用、历史回滚、链篡改或尾部落后当前证据均失败关闭；
> `scripts/validate.py` 首关强制 `model-risk-history:current`。当前为 `revision_count=2 / MULTI_REVISION_HISTORY`，
> `LONGITUDINAL_REGISTRY_HISTORY_NOT_RETAINED` 已按真实证据移除；外部 provider、强审阅身份与负 OOS 结果仍使成熟度停在 M2。
> `GOV.IDENTITY.STRONG_AUTH` 已于 2026-07-12 从共享 `X-Gov-Token` + 自报
> `X-Operator` 的 M1 路径升级为承重的 M2 强身份边界：promotion API 删除本地共享 token，
> 只接受 allowlist 非对称算法签名的 OIDC access token，强制 issuer/audience/时效/MFA、
> RFC 8705 `cnf.x5t#S256` mTLS 证书绑定及读取/申请/复核/批准/执行 RBAC；actor/audit ID
> 只能来自 `issuer|subject`。浏览器已接入 Authorization Code + PKCE S256：Web Crypto 生成
> state/nonce/verifier，10 分钟版本化一次性事务仅存 sessionStorage，access token 仅驻内存，
> 回调先清理并消费事务再换 token。13 个攻击案例与三方 subject 分离证据在
> `evidence/strong_identity_baseline.json`。外部 IdP 运营、可信 TLS 终止器、密钥托管轮换、
> 身份生命周期/撤销/访问复核及 AI 无法人类化的运维证据仍缺，因此该红线保持 M2/PARTIAL，不虚报 M4。
> 本增量完整门禁结果：治理扫描 244 文件零违规；后端 2003 passed / 1 skipped；
> live/trading/tools 844 passed；前端 typecheck、68 tests、production build 全部通过。
> `P1-8/P2-1` 数据质量对抗复核于 2026-07-13 完成代码收口：strict 接入改为写前全批校验 +
> SQLite 原子批写，承重日/1h/1m 驱动器强制 cadence，funding 原始观测与日缓存 fail-closed，
> JSON 缓存原子发布；前向 `record_decision/realize_due` 均要求本轮质量证据，失败 ticker 留 pending，
> `--no-refresh` 不再推进证据钟。随后完成真实四源刷新、永续 mark-price 经济口径修正与
> `evidence/data_quality_admission.json`：固定 24 币前向宇宙中 mark 24/24 PASS、OI 24/24 PASS，
> funding 24/24 BLOCK；全 115 ticker funding 请求均为结构化 `URLError` / `WinError 10060` /
> `TRANSIENT`，没有半份发布或跨 venue 静默替代。perp mark 默认尝试 16 日覆盖，已有两条 pending
> 决策的 14/14 起点/后续定价已补齐；`forward_track_advance --no-refresh` 实跑前后仍各 1 pending、
> 0 realized、0 forward period。随后落地 `services/data/raw_archive.py`(SHA-256 object + immutable
> observation sidecar + parser 计数 + 篡改/schema 校验)，接入 daily/1h/1m/mark/OI/funding 与批量研究入口；
> `raw_data_lineage_backfill` 三轮真实重取最近 8 日，最终 mark 192/192(全 refetch)、OI 192/192
> (112 refetch + 80 verified archive replay，vendor transient 保留 warning)；384 unique objects /
> 1065 observations、0 invalid。evidence 绑定当前三 cache SHA、scope、days 与 archive aggregate。
> funding 0/192 且无 observation，因此 admission schema v2 的
> quality / lineage 双门均 BLOCK，前向驱动器也结构性拒绝缺 lineage 的证据。唯一外部根因仍是 funding
> 源不可达。四源刷新现已具备独立 spawn 进程、source/global 硬 deadline、哈希链历史、覆盖率 SLO 与连续
> 失败告警；两轮真实运行均约 635.5 秒，链完整，但仅积累 2/30 轮且 funding 水位 5 天使 SLO 为
> `BREACH`。历史/spot/1h/1m 旧缓存 backfill、外部 WORM/异地副本仍 open；`crypto_daily.db`
> volume 双乘根因已于 2026-07-13 闭合。详见
> `docs/engineering/plans/2026-07-13-data-quality-enforcement.md`。本增量完整门禁：治理扫描
> 246 文件零违规；后端 2080 passed / 1 skipped；live/trading/tools 856 passed；前端 typecheck、
> 68 tests、production build 全部通过。顶级能力结论不变：`NOT_TOP_TIER`,16/22 PASS。
> `P2-3` 峰值权益状态误全平风险于 2026-07-13 完成无需新阈值的代码收口：新增
> `live/execution/equity_state.py`，把账户级 append-only `live_equity*.jsonl` 最大有效权益作为
> `live_risk_state*.json` 的证据上界；周度驱动器和小时哨兵均已承重。状态损坏、非有限、非正或
> 高于历史时返回 `peak_equity_unknown`，常规执行硬停、kill-switch 闩锁并告警，但纯数据质量
> breach 不进入紧急全平，异常原文件保留取证。现有 Binance/Bybit demo 峰值均与各自全历史最大值
> 精确一致；无历史的未配置 Bybit 状态 fail-closed。未修改 20% 回撤阈值、90% 敞口回撤目标或真实
> 回撤全平语义。剩余边界：本地 JSONL 非 WORM/哈希链，上游异常高权益若同时进入历史仍需 15%
> 跳变告警人工核查，现金流/account identity 调整 high-water mark 与真实回撤确认/延迟策略未实现。P2-3 聚焦
> 243 tests、完整 `tests/live` 764 tests 全绿；最终统一门禁主套件 2102 passed / 1 skipped、
> live/trading/tools 882 passed、前端 68 tests + typecheck/build、治理扫描 246 文件零违规。
> 顶级结论仍为 `NOT_TOP_TIER`,16/22 PASS。
> `P2-7` 拥挤门审计漂移于 2026-07-13 收口：旧风险登记仍称全部阈值 `ACTIVE/UNCALIBRATED`，
> 但代码现实是用户早已签署 crowd_contra `average_warn_only + short-leg z*=3.0`，live 与两条干跑均已承重。
> 本轮把手工 233 周说明升级为 234 周机器证据：确定性冻结 bundle/manifest 绑定四个原始制品哈希，
> `evidence/crowding_gate_calibration.json` 重放真实 gate、绑定实现哈希和三处签署常数，并进入统一门禁。
> `z*=3.0` 阻断 21/234=8.9744%；2.0/2.5/3.0/3.5/4.0 阻断 61/38/21/9/5 周；live/dry
> 阻断周逐项一致。新证据同时纠正旧概括：dry-run rank average 恒 0.500，live extremity-rank average
> 为 0.500–0.655；两者均 WARN-only，BLOCK 权威仍只来自共同 z-score。诚实边界：通用
> 0.60/0.45/0.25/5天默认值仍未获生产微观结构校准，L2 无 order-book/强平标签/生产成交样本。
> 本增量最终统一门禁：主套件 2110 passed / 1 skipped，live/trading/tools 882 passed，前端
> 68 tests + typecheck/build，治理扫描 247 文件零违规；机器评估仍为 `NOT_TOP_TIER`,16/22 PASS。
> `P2-6` 的 3–7 天跨源核价空窗于 2026-07-13 完成当前价路径收口：删除按 `decided_ts`
> 年龄跳过与按日放宽偏差，周度驱动器每轮改取真实 venue `book_ticker` 的 bid/ask/source time，
> 对 CoinGecko `price + last_updated_at` 做 midpoint 偏差、最大年龄与跨源时间偏差检查。缺盘口、
> 交叉盘、无源时间、独立源缺失、陈旧或错时均逐标的冻结；整源异常冻结全部目标，现有仓位不动。
> Binance/Bybit 无时间戳盘口仍可供旧 LIMIT anchor 使用，但不能通过新核价；旧
> `fetch_simple_prices` API 不变。5% 偏差与 5 分钟年龄/错时上限明确为 `UNCALIBRATED` L2 工程基线。
> `_MAX_STALENESS_MS=7 天` 仍服务周频信号，收紧必须先确认 cadence；逐请求 REST 非原子快照且尚无
> 长期观测校准集。P2-6 聚焦 157 tests、Ruff、diff check 全绿；驱动器实现哈希变化后拥挤证据已重生成并
> 重放通过（234 周、21 个 signed-z blocks）。顶级能力结论不因本项改变，仍为
> `NOT_TOP_TIER`,16/22 PASS。最终统一门禁：主套件 2113 passed / 1 skipped，
> live/trading/tools 901 passed，前端 68 tests + typecheck/build，治理扫描 247 文件零违规。
> 同轮机器评估发现供应链基线因 `scripts/validate.py` 摘要漂移短暂落为唯一 `FAILED`；本轮没有只做
> 一次性重生成，而是给 `scripts/generate_supply_chain_evidence.py` 增加确定性 `--check`，同时逐字节校验
> 320 组件 CycloneDX SBOM 与绑定 manifest 的 baseline，当前/篡改测试钉死 fail-closed，并在统一门禁中
> 排在 top-tier assessment 之前。重生成后 `PLATFORM.SUPPLY_CHAIN` 恢复 M3/PARTIAL，矩阵为
> `16 PASS / 0 FAILED / 4 PARTIAL / 2 manual-external`。M4 仍严格开放：跨主机复现、真实外部身份
> Sigstore bundle、独立 admission 部署及 freshness/revocation 证据均未提供，本地 current 门不替代它们。
> `P2-5` 于 2026-07-13 从“总线本体有测试、零真实 producer”推进到关键事件承重：
> `DurableEventBus` 现在以 canonical Event + correlation/causation + 前序摘要形成 SHA-256 追加链，
> 支持旧库迁移、跨连接串行追加、同内容幂等重跑和跨重启因果回放；更新/删除触发器阻断普通篡改，
> 即使绕过触发器修改元数据或删除中间行，全链校验也会拒绝回放和继续发布。干跑单期/多期 pipeline
> 在纸面 fill 前持久化 started + 每意图 pretrade approved/rejected，完成后写 completed；七个正式
> dry-run driver 共用 `data/cache/domain_events.db`。alpha maintenance 的 DEGRADED/PAUSED/RETIRED
> 同样以结构化事件进入该库。两个 service 域实际 import `Event`，聚焦 162 tests + Ruff/diff 全绿。
> 诚实边界：同步调用仍是权威业务路径，总线是证据旁路；kill-switch/live pre-trade/promotion 尚未接，
> 也没有跨进程 consumer、outbox、schema registry、死信或 HA broker，不能称全面事件驱动架构。
> 本增量最终统一门禁：主套件 2129 passed / 1 skipped，live/trading/tools 902 passed，
> 前端 68 tests + typecheck/build，治理扫描 249 文件零违规；机器评估仍为
> `NOT_TOP_TIER`,16/22 PASS。
> `P2-4` 于 2026-07-13 完成第一条真实反向依赖拆除：`services/research/session.py`
> 只保留确定性会话核心与 `ResearchSearchRuntime` 注入契约，不再 import
> `services.intelligence`；正式入口迁到
> `services/intelligence/research_session.py::run_research_session`，由 intelligence
> 固定组装 `ResearchAgentLoop`、guided/evolutionary/MCTS 搜索组件，并拒绝调用方覆盖
> `search_runtime`。仓库内正式工具与端到端测试均已迁移，治理测试逐个 AST 扫描
> `services/research/**/*.py`，禁止该反向边回归。Ruff 全绿，相关 research/intelligence/data/
> governance 套件 752 passed / 1 skipped。诚实边界：这只消掉 research→intelligence；
> research↔alpha/portfolio/risk/execution 等其余环、research 巨型域和 4,934 行
> `console_api/main.py` 仍开放，因此 P2-4 仍是 PARTIAL，不宣称全域无环。
> `P2-4` 第二增量于 2026-07-13 把上述“其余环”从文字债务升级为机器事实：
> `services/governance/service_import_graph.py` 对生产 `services/**/*.py` 建确定性 AST 图，解析
> 绝对/相对/`from services import ...`/字面量动态 import，排除测试目录，任何语法错误失败关闭；
> 两处非字面量动态工具加载按路径/行号/函数显式登记。`config/service_import_policy.json` 必须与
> 当前循环边和动态点完全一致，新增循环、图快照漂移、制品篡改和已拆边的陈旧权限均拒绝。
> `evidence/service_import_graph.json` 已进入 `scripts/validate.py`，并被 320 组件供应链 manifest
> 绑定及 `PLATFORM.CI.UNIFIED_GATE` 机器要求。初始事实为 12 域/26 边/一个 6 域 SCC/14 循环边；
> 随后把共享 `CostModel` 上移到 `quantos/contracts/cost.py`，研究侧兼容重导出，删除唯一的
> portfolio→research 生产依赖。当前为 12 域/25 边/一个 5 域 SCC/12 循环边，portfolio 与
> intelligence 均已退出 SCC。聚焦架构/供应链/顶级评估/成本与组合套件 76 tests 全绿；
> 仍开放的 SCC 是 alpha/execution/governance/research/risk，下一条按图拆 execution→research。
> `P2-4` 第三增量已继续兑现该排序：`PaperFill/OrderSide/FillMethod` 上移
> `quantos/contracts/paper.py`；平均成本对账与 Almgren–Chriss impact/calibration 正本移到
> `services/execution/`，research 原路径仅兼容重导出，类/函数身份与对账恒等式均由测试锁定；
> 正式干跑和 carry driver 改用 execution 正本。execution→research 零命中并由域边界测试钉死。
> 图门先拒绝两条陈旧循环权限，收紧 policy 后当前为 12 域/24 边/一个 4 域 SCC/10 循环边，
> execution 与此前的 intelligence/portfolio 均已退出。新增正本、契约与测试进入供应链 manifest；
> 聚焦 contracts/execution/research/governance/供应链套件 200 tests 全绿。剩余 SCC 为
> alpha/governance/research/risk；下一条高杠杆回边是 research→risk（仅 console 风险诊断消费）。
> `P2-4` 第四增量把 console 风险诊断改为一次性 `RiskConsolePort` 注入：research console
> 未配置时失败关闭且禁止运行时替换，真实 risk/safety 组合上移到 `services/console`，正式入口改为
> `services.console.main:app`。research→risk 零命中，risk 退出 SCC；契约、kill-switch 与 API
> 聚焦 65 tests 全绿。图更新为 13 域/26 边/alpha-governance-research 三域 SCC/6 循环边。
> `P2-4` 第五增量把 mutating promotion HTTP 组合根从 governance 内迁到独立
> `services/promotion_api`；治理域只保留审批、强身份与策略原语，对 alpha/research 零 import。
> OIDC+mTLS+MFA+RBAC、三方审批状态机、审计和全部 L2 安全语义未变。旧 policy 先按预期拒绝
> 4 条陈旧循环权限，再收紧为 alpha↔research 两条。当前机器事实为 **14 域/27 跨域边/
> 唯一 alpha-research 双域 SCC/2 条循环边**；governance、risk、execution、portfolio、intelligence
> 均已退出 SCC。P2-4 仍是 PARTIAL：下一刀是归位 alpha/research 双向消费的 metrics/validation/
> regime、panel/backtest 与 catalog 生命周期 contracts/ports，然后清零最后一个 SCC；约 4,900 行
> console API router 拆分仍在其后。
> 本增量最终统一门禁：主套件 2153 passed / 1 skipped，live/trading/tools 902 passed，前端
> 68 tests + typecheck/build，治理扫描 260 文件零违规；机器评估仍诚实为
> `NOT_TOP_TIER`,16/22 PASS。
> `P2-4` 第六增量清零最后一个循环：性能指标、IC、regime、DSR 与验证门的无状态正本抽到
> `services/validation`，该域对其他 service 域零依赖；research 原路径保留对象身份一致的兼容重导出。
> `factor_set` 归回 alpha，四处 CostModel 改用 `quantos/contracts/cost.py` 正本，alpha 对 research
> 零 import。旧 policy 先按预期拒绝 alpha↔research 两条陈旧权限，再收紧为 `known_cycle_edges=[]`。
> 当前生产 service 图为 **15 域/29 跨域边/0 SCC/0 循环边**，聚焦统计/验证/alpha/治理回归
> 185 tests 全绿。循环依赖半区已经机器闭合；P2-4 仍未整体完成，因为 `services/research` 仍过大且
> `console_api/main.py` 仍约 4,900 行，下一步按只读 router 拆到单模块 <800 行。
> 本增量最终统一门禁：主套件 2157 passed / 1 skipped，live/trading/tools 902 passed，前端
> 68 tests + typecheck/build，治理扫描 268 文件零违规；机器评估仍诚实为
> `NOT_TOP_TIER`,16/22 PASS。
> `P2-4` 第七增量开始拆 console god-module：新增 374 行
> `services/console/api/artifact_snapshots.py`，承接 FX、carry、dry-run、IC、研究发现、
> 1 分钟策略复核、前向/测试网状态与 GoNoGo 共 8 个只读 GET 路由；模块不 import `live/`、`tools/`
> 或 risk，所有缺失/损坏制品继续诚实 fail-closed。FastAPI 0.139 的延迟 router 组合使旧的
> `app.routes` 平铺假设失效，契约守卫改以 OpenAPI 实际 GET paths 为准，payload/provenance/read-only
> 校验一项未放宽。`main.py` **4,875→4,498 行**，新 router <800；架构边界测试锁定 URL、GET-only、
> 行数和实现已移出 composition root。相邻回归 110 tests 全绿，生产图仍为 **15 域/29 边/0 SCC/
> 0 循环边**。最终统一门禁：主套件 2158 passed / 1 skipped，live/trading/tools 902 passed，
> 前端 68 tests + typecheck/build，治理扫描 269 文件零违规。P2-4 仍未关闭：余下 4,498 行继续
> 按生命周期/数据、验证/回测、治理诊断、系统/运行态 router 分批拆到各模块 <800 行。
> `P2-4` 第八增量完成 console god-module 收口：15 个按业务簇划分的只读 router 模块承接全部
> 44 个 OpenAPI paths（含 `/health`），每个模块均 <800 行；`console_api/main.py` 从最初 4,875 行
> 降至 **772 行**，且不再包含任何 route implementation。FastAPI runtime 依赖均由 composition root
> 一次性注入，正式 `services.console.main` 仍提供真实 risk/safety port；治理测试现锁定 URL 集、GET-only、
> 模块与主文件行数、实现移出和 port fail-closed。PIT 裸读白名单随真实 owner 从 `main.py` 精确迁到
> `data_catalog.py`，旧条目同步删除，没有扩大权限。最终统一门禁：主套件 **2174 passed / 1 skipped**，
> live/trading/tools **902 passed**，前端 **68 tests + typecheck/build**，治理扫描 **284 文件零违规**；
> service 图仍为 **15 域/29 边/0 SCC/0 循环边**。机器评估仍诚实为 `NOT_TOP_TIER`：
> **16/22 PASS、4 PARTIAL、2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。P2-4 仍是 PARTIAL：console 半区
> 已闭合，下一步是收窄 oversized `services/research` 的 ports/adapters 与 backtest/validation/api 边界。
> `P2-4` 第九增量关闭第一条 research 数据所有权穿透：新增 research-owned
> `PointInTimeMarketDataPort/VersionedMarketDataPort`，data-owned
> `PITResearchDataAdapter` 保留原 PIT 查询与同一 raw-bar 内容哈希；正式 intelligence 组合层负责适配，
> research session 不再调用 `fetch_bars` 或 import data versioning。console 的真实数据目录枚举上移
> `services/console/data_console.py`，研究侧通过一次性 `DataCatalogRuntime` 消费，未配置失败关闭且不可热替换。
> volume-dispersion 纯检查归入 `quantos/contracts/quality.py`，data 保留同对象兼容导出。由此
> **`services/research` 对 `services.data` 零 import、research→data 边清零**；服务图变为
> **15 域/30 边/0 SCC/0 循环边**。边数 +1 是一条隐藏 research→data 被两条真实组合边
> `intelligence→data`、`console→data` 取代，不是新增反向耦合。最终统一门禁：主套件
> **2184 passed / 1 skipped**，live/trading/tools **902 passed**，前端 **68 tests + typecheck/build**，
> 治理扫描 **290 文件零违规**。机器评估保持 `NOT_TOP_TIER,16/22 PASS`；下一步收窄
> experiment/run/paper registry 的 persistence ports，再继续 backtest/validation/api 子域边界。
> `P2-4` 第十增量完成 registry persistence 隔离：`services/research/ports/persistence.py` 提供
> runtime-checkable `ExperimentStorePort`、`RunStorePort`、`PaperTrackStorePort` 与
> `ForwardLedgerPort`，并承载稳定的 fail-closed persistence errors。ExperimentRegistry、
> RunRegistry、paper advance/health/realize 领域核不再 import concrete store；原本和兑现逻辑混在一起的
> SQLite `ForwardLedger` 已迁到 `paper_trading/forward_store.py`。整个 research 域的 `sqlite3`
> import 精确收敛到四个 adapter owner：experiment/run/paper/forward stores，治理测试禁止回流。
> 旧 `ExperimentStoreError/PaperTrackStoreError` 导入仍与 port 正本对象身份一致；工具、live parity、
> promotion API 和 console 组合调用均已迁移并回归。Crowding calibration 因源码摘要变化曾正确拒绝，
> 重新生成后 policy/summary 逐字段不变（234 周、signed z=3.0、21 个阻断期），未改阈值。
> 最终统一门禁：主套件 **2190 passed / 1 skipped**，live/trading/tools **902 passed**，前端
> **68 tests + typecheck/build**，治理扫描 **292 文件零违规**；服务图仍为
> **15 域/30 边/0 SCC/0 循环边**，机器评估仍为 `NOT_TOP_TIER,16/22 PASS`。
> 下一步分离 backtest engine、validation orchestration 与 API adapters 的内部依赖。
> `P2-4` 第十一增量完成上述内部边界切片。验证流水线不再直连具体
> `backtest_engine.engine`：新增 runtime-checkable `BacktestExecutionPort`、不可变且拒绝空/非有限证据的
> `BacktestOutcome` 与 `DeterministicBacktestAdapter`；端口为必填依赖，返回类型或 OOS equity 长度不合约即
> 失败关闭。walk-forward 与 CPCV 纯正本迁入中立 `services/validation`，research 旧路径仅保留对象身份一致
> 的兼容导出。mutating runner 整体上移独立 `services/research_runner` adapter 域，read-only FastAPI
> router 整体上移 `services/console/api`；不保留会制造回环的旧 shim，`services/research` 生产代码对
> FastAPI/uvicorn/tools/data 均为零 import。15 个 router、44 个 GET-only OpenAPI paths、772 行无 route
> implementation 的 API root 与所有 payload/provenance/fail-closed 语义不变。服务图现为
> **16 域/36 边/0 SCC/0 循环边**；新增域是独立 runner，边数增加来自 console adapter 显式暴露其真实
> 依赖，不是反向环。Python advisory 重新扫描为 0 漏洞，供应链仍为 320 组件。最终统一门禁：主套件
> **2204 passed / 1 skipped**，live/trading/tools **902 passed**，前端 **68 tests + typecheck/build**，
> 治理扫描 **296 文件零违规**；机器评估仍诚实为 `NOT_TOP_TIER`：**16/22 PASS、4 PARTIAL、
> 2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。P2-4 的本轮排定切片已闭合，下一本地优先级转到 P2-5
> consumer/outbox/offset/DLQ；research 的进一步模块化仍是长期开放项，不等于 Phase 14 进程隔离。
> `P2-5` 第二增量完成单机可靠消费基线。`DurableEventBus` 新增独立 SHA-256 追加式 consumer journal：
> 稳定 consumer/worker identity、caller-supplied 时间租约、跨连接排他 claim、租约过期重领、owner/lease
> 校验 ack、持久 offset、失败次数与只存类型+SHA-256 指纹的错误证据；达到上限进入不可变 DLQ 并高声抛错，
> poison event 隔离后后续事件可继续。语义明确为 **at-least-once**，不冒充 exactly-once。
> `DomainEventProjection` 是 WAL/FULL、全链校验、同 seq+ID+内容幂等的真实 read model；7 个 dry-run
> driver 与 maintenance sweep 均推进同一 consumer。console `/events` 首选只读 projection，只公开 5 类
> L2 事件和 payload 字段白名单，缺失/损坏回退 run registry，再无数据才用诚实标注 synthetic。
> dry-run `completed` 进一步采用真 transactional outbox：`DryRunRun` 与 completion event 在同一个
> `dry_run.db BEGIN IMMEDIATE` 事务写入，outbox/dispatch ack 各自为不可变哈希链；dispatcher 先
> `publish_once` 再追加 ack，因此 bus 已提交/ack 前崩溃可安全重放。started/pretrade 仍保持动作前先落事件的
> fail-closed 语义。maintenance JSON 快照跨库 outbox、kill-switch/live pretrade/promotion/deployment
> producers、schema registry、consumer heartbeat/partition 与跨主机 HA 仍开放。拥挤证据因 7 driver 摘要
> 变化先正确拒绝，重生成后仍为 234 周、signed z=3.0、21 个阻断期。最终统一门禁：主套件
> **2225 passed / 1 skipped**，live/trading/tools **905 passed**，前端 **68 tests + typecheck/build**，
> 治理扫描 **297 文件零违规**；服务图仍 **16 域/36 边/0 SCC/0 循环边**，供应链 320 组件，
> 机器评估仍为 `NOT_TOP_TIER,16/22 PASS`。
> `P2-5` 第三增量收口 schema、生产端与跨存储诚实边界。新增不可变
> `EventSchemaRegistry`，对 type/version、payload、source/domain/criticality 与显式 consumer
> compatibility 严格失败关闭；`dry_run.completed` 升至 1.1 并绑定完整 `DryRunRun` SHA-256，1.1
> consumer 仍可读取登记过的 1.0 历史。event bus 旧库迁移改为 `BEGIN IMMEDIATE`，并发首次打开的
> WAL lock 竞态已有回归。除 dry-run outbox 外，human-only promotion 的 `APPROVED` catalog snapshot
> 与 promotion event 现同写 `experiment_registry.db`；maintenance snapshot + transitions 同写新的
> `alpha_maintenance.db` 追加记录，JSON 只作事务后镜像，live consumer 优先权威库且损坏/空库停止新增敞口。
> 三条 dispatcher 均为 publish-then-ack at-least-once，publish/ack 崩溃可幂等恢复。kill-switch arm/reset
> 与 deployment decision 也有严格事件 hook；reset 在清闩前写事件，写失败保持停机。诚实边界仍保留：
> 当时 kill-switch JSON 状态与事件并非同库事务（已由下述第四增量关闭）；deployment event 不是实际 deployment state，真实 live
> pre-trade producer、consumer partition/rebalance、跨主机 broker/复制/HA 均未完成；approval consume、
> catalog/outbox、audit 仍是多库 saga。源码绑定证据已重建并复核：服务图 16 域/36 边/0 SCC，供应链
> 320 components，拥挤证据 234 周/21 blocks，评估仍 `NOT_TOP_TIER,16/22 PASS`。最终统一门禁：
> 主套件 **2250 passed / 1 skipped**，live/trading/tools **908 passed**，前端 **68 tests +
> typecheck/build**，治理扫描 **303 文件零违规**。
> `P2-5` 第四增量关闭 kill-switch 状态/事件原子性缺口。新增 risk-owned、WAL/FULL 的 append-only
> `KillSwitchStateStore`：ARM/RESET transition、完整状态、严格版本事件、correlation/causation 与前序摘要在
> 同一个 `BEGIN IMMEDIATE` 事务提交，transition 与 dispatch ack 各有不可变 SHA-256 链；CAS 拒绝陈旧写入，
> 并发哨兵只保留一个获胜 incident，不能二次 ARM 改写或虚增 `trip_count`。事件必须与 actor、scope、原因指纹、
> 时间和 RESET 的 activation causation 精确一致。`live_track_advance`、`live_risk_sentinel` 与三方 reset CLI
> 均已迁入该权威库；JSON 降为原子兼容镜像，console 在 DB 存在时绝不回退镜像，DB 损坏即显示 TRIPPED。
> 完整 legacy TRIPPED 状态迁移时会同事务重建可验证 activation outbox，信息不足时只 fail-closed、不伪造历史。
> `risk_recovery_drill` 升为 schema v2，重启验证 `kill_switch.db` 哈希、pending ARM outbox、门禁拒绝、有效 JSON+
> 损坏/篡改 DB 故障，实测 RTO 128ms。聚焦回归 180 tests、风险/live/monitoring/关键治理 1009 tests 全绿；
> 服务图仍 16 域/36 边/0 SCC，拥挤证据仍 234 周/21 blocks，供应链仍 320 components。机器评估保持
> `NOT_TOP_TIER`：**16/22 PASS、4 PARTIAL、2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。仍开放：真实 live
> pre-trade producer、deployment 实际状态 outbox、consumer heartbeat/partition/rebalance、跨主机 broker/复制/HA，
> 以及 approval/catalog/audit 多库 saga；单机 SQLite 不冒充跨主机事件平台。最终统一门禁：主套件
> **2265 passed / 1 skipped**，live/trading/tools **916 passed**，前端 **68 tests + typecheck/build**，
> 治理扫描 **305 files / 0 violations**，全部 fail-closed gate 通过。
> `P2-5` 第五增量关闭事件时间单位歧义并把 schema 演进变成正式制品。canonical 最新版本统一为
> Unix epoch microseconds；历史上实际写入 milliseconds 的 kill-switch、alpha promotion、deployment
> v1.0 继续按登记单位解释，新 producer 升至 v1.1 microseconds。projection/console 只依据
> `(event_type,event_version)` 归一化，禁止按数值量级猜测，因此混合历史不会再把旧事件显示到 1970 年附近。
> 新增确定性 `evidence/event_schema_catalog.json`：覆盖 9 个 event types、14 个 schema definitions，逐版本
> 固定 timestamp unit、显式 consumer compatibility、payload JSON Schema + SHA-256，并绑定 12 个实现文件；
> `--check`、`scripts/validate.py` 与 320-component supply-chain manifest 会拒绝缺失、陈旧或篡改制品。
> 受影响 producer/consumer/legacy replay/console projection 聚焦 **172 tests** 与 Ruff 全绿；Catalog/供应链
> 陈旧性检查均通过。机器评估仍诚实为 `NOT_TOP_TIER`：**16/22 PASS、4 PARTIAL、
> 2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。当时下一本地增量是 consumer heartbeat/partition/rebalance、
> deployment state outbox 与更多 data/risk producer；跨主机 broker/复制/HA 仍不得由单机 mock 冒充。
> `P2-5` 第六增量完成诚实的单机 consumer-group 协调。新增
> `services/monitoring/consumer_group.py` 与同库 append-only coordination hash chain：group 的
> partition count/session timeout 创建后不可漂移；heartbeat 维护 membership，活成员按排序做确定性 eager
> rebalance，每次 assignment 变化递增 generation 并 fence 旧 claim/ack。partition key 优先使用
> `correlation_id`，否则使用 `event_id` 的稳定 SHA-256；内部 offset identity 使用保留前缀 + group hash，
> 不与用户 group 名碰撞。partition offset、retry/DLQ 和 publish/projection 仍复用原权威日志并跨重启恢复。
> 正式 `domain-event-projection-v1` 固定 1 partition 保护全序和旧 offset，处理前 heartbeat、正常退出 leave，
> 崩溃才等待 session timeout 接管；时间回拨、旧 generation、非 owner、过期 membership、逆序 ack、配置漂移、
> 双连接竞态、legacy DB 迁移和协调链篡改均失败关闭。聚焦 **234 tests** + Ruff + service graph/Catalog/
> supply-chain checks 全绿，仍是 16 域/36 边/0 SCC、320 components，机器评估保持
> `NOT_TOP_TIER`：**16/22 PASS、4 PARTIAL、2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。
> 诚实边界：这是 caller-clock、进程内 worker identity、单 SQLite authority 的协调，不是跨主机 consensus；
> 协调日志 retention/lag alert、跨主机 broker/复制/HA 仍开放。下一本地项转为 deployment state outbox 与
> 更多 data/risk producer。最终统一门禁：主套件 **2287 passed / 1 skipped**，live/trading/tools
> **916 passed**，前端 **68 tests + typecheck/build**，治理扫描 **306 files / 0 violations**，18 阶段全绿。
> `P2-5` 第七增量把 deployment 从可选同步 event hook 升为 L2 权威 evaluation ledger + 第五条
> transactional outbox。新增 `DeploymentDecisionStore`：evaluation 与 canonical v1.1 event 在同一个
> `BEGIN IMMEDIATE` 写入，evaluation/dispatch 各有 append-only SHA-256 链、UPDATE/DELETE trigger、
> 精确幂等和同 subject/environment 时间单调约束。状态枚举只有 `DENIED` 与
> `ELIGIBLE_NOT_APPLIED`，代码/表约束均不存在 `DEPLOYED`；LIVE 在 L2 只会记录 DENIED。
> 正式 `evaluate_and_record_deployment` 先走纯 gate，再原子落状态/outbox；authority 不可用时原本允许的
> PAPER 决策降为 `DEPLOYMENT_STATE_ERROR` 拒绝。事件 ID 绑定 actor/裁决/原因/版本/时间，同毫秒不同裁决
> 不冲突；grant/approval ID 进入 causation。`tools/deployment_events.py` 只做 publish-then-ack + projection，
> publish 后/ack 前崩溃可安全重放，不接受申请也不执行部署。console `/events` 仅白名单展示安全字段。
> 聚焦 **209 tests** + Ruff + service graph/Catalog/supply-chain checks 全绿，机器结论仍为
> `NOT_TOP_TIER`：**16/22 PASS、4 PARTIAL、2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。
> 诚实边界：本轮关闭的是 deployment evaluation state，不是实际 APPLIED/DEPLOYED 状态；真实部署执行器、
> staging/prod 隔离、live pre-trade/data producer 仍开放。下一本地项转为更多 data/risk producer 与
> consumer lag/retention 告警。最终统一门禁：主套件 **2305 passed / 1 skipped**，live/trading/tools
> **917 passed**，前端 **68 tests + typecheck/build**，治理扫描 **308 files / 0 violations**，18 阶段全绿。
> `P2-5` 第八增量补齐首个真实 data producer、第二类 risk producer 与单机事件健康告警。
> `data.market.refresh.recorded` 现在和防篡改 `RefreshHistoryStore` 的主记录、规范化 source 行在同一
> `BEGIN IMMEDIATE` 提交；第六条 outbox/dispatch 双追加链支持 publish-before-ack 崩溃重放，旧库仅在
> outbox 表完全不存在且原主链可验证时确定性回填，部分迁移或篡改拒绝修复。事件只含 run/outcome/counts/
> duration 与两条 SHA-256，不复制自由文本源错误。`research.risk.dry_run.evaluated` 则与精确
> `DryRunRun.reproducibility_hash` 和 run 内 `REAL_RESEARCH_RISK_SUBSET` 逐字段相等后，和 completed event
> 共用既有 dry-run 事务 outbox；它仍是 4 个诚实输入引擎、5 个显式 skipped 的诊断，不是晋级许可。
> Catalog 升为 **11 types / 16 definitions / 18 implementation bindings**。`DurableEventBus.operational_snapshot`
> 按真实 partition 分配统计 pending（不拿全局 seq 差冒充 lag），按版本 schema 归一化事件年龄，并汇总
> group DLQ、三条 journal 行数和 SQLite allocated bytes。`EventBusHealthPolicy` 输出 PASS/WARN/BREACH；
> console `/events` 与可调度的 `tools/domain_event_health.py` 分别只读展示和复用 alert sink 投递。
> retention 明确为 `APPEND_ONLY_ALERT_ONLY`，不自动 DELETE/VACUUM。自动归档/外部 seal、独立告警 SLA、
> 强 worker identity、真实 live pre-trade、跨主机 broker/复制/HA 仍开放，SQLite 仍不冒充 Kafka。
> 最终统一门禁：主套件 **2321 passed / 1 skipped**，live/trading/tools **920 passed**，前端
> **68 tests + typecheck/build**，治理扫描 **311 files / 0 violations**，18 阶段全绿；机器结论仍为
> `NOT_TOP_TIER`：**16/22 PASS、4 PARTIAL、2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。
> `P2-5` 第九增量补上独立 outbox recovery drain。`tools/domain_event_outbox_drain.py`
> 以固定顺序覆盖 dry-run、experiment/promotion、maintenance、kill-switch、deployment、data-refresh
> 六个 owner store，全部 `create=False`；缺库标 `UNAVAILABLE`，部分存在只报 `PARTIAL`，损坏只保留异常类型且
> 不阻断其他独立 source，event authority 打不开也会原子落 `BREACH` 快照。所有 dispatch 完成后只推进一次
> shared projection；若所有 source 与 event authority 都不存在，不创建空数据库。该工具只搬运 event + 追加 ack，
> 不评估策略、不改 domain state、不 reset kill-switch、不部署、不下单、不清理历史。调度注册与外部告警 SLA 仍是
> 独立运维工作，单机恢复 drain 不冒充 broker/分布式事务/exactly-once。
> 新恢复工具 7 项聚焦测试、跨 outbox/治理 77 项回归与 Ruff/diff 均通过。最终统一门禁：主套件
> **2322 passed / 1 skipped**，live/trading/tools **927 passed**，前端 **68 tests + typecheck/build**，
> 治理扫描 **311 files / 0 violations**，18 阶段全绿；Catalog 仍为 11 types/16 definitions/18 bindings，
> 服务图仍为 16 域/36 边/0 cycles，供应链仍为 320 components。机器结论保持 `NOT_TOP_TIER`：
> **16/22 PASS、4 PARTIAL、2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。
> `P2-5` 第十增量从一次现场安全冲突开始：本机两条 `--submit`、一条 `--act`、ops API 与其
> live watchdog 仍为 Enabled，且 `live/` 路径不消费 canonical `SafetyConfig`。五个任务已可逆禁用并逐项
> 复核 Disabled；`register_schedules.ps1` 重写为 L2-only module allowlist，运行时解析 Python/workspace，
> 用独立 executable/arguments/working-directory + `IgnoreNew` 注册六条 research/paper/maintenance/event-ops/report
> 任务，治理测试禁止 live module/action flag/硬编码路径/shell action。新 `domain_event_ops.py` 每小时固定
> drain→health，任一失败不阻断另一半，只留异常类型，写统一状态/本地告警并以 0/1/2 表达结果。首轮计划任务
> 按时运行并返回 1：projection/health PASS，dry-run/experiment/data-refresh 三 owner store BREACH，另三库
> UNAVAILABLE，未伪装成绿。首次手工探针同时抓到 shared authority 仍用 `create=True` 的缺陷：一个 4 KiB
> 无 `event_log` SQLite 被初始化为空 authority；未猜测回滚用户文件。现存 authority 现先经只读三表 preflight
> 再 `create=False`，drain/health 两条反例钉死缺 schema 文件逐字节不变。独立外部告警 SLA、安全归档/seal、
> scheduler watchdog 与工作的备份仍开放；单独 `QUANTOS_backup` 最近运行失败。
> 聚焦验证为 event/monitoring **79 passed**、governance/supply-chain **76 passed**，Ruff、PowerShell parse、
> Catalog/service graph/supply-chain checks 全绿。最终统一门禁：主套件 **2327 passed / 1 skipped**，
> live/trading/tools **938 passed**，前端 **68 tests + typecheck/build**，治理扫描 **311 files / 0 violations**，
> 18 阶段全绿；Catalog 仍为 11 types/16 definitions/18 bindings，服务图仍为 16 域/36 边/0 cycles，
> 供应链仍为 320 components。机器结论保持 `NOT_TOP_TIER`：**16/22 PASS、4 PARTIAL、
> 2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。
> `P2-5` 第十一增量关闭首轮 scheduler 暴露的三份 legacy owner-store BREACH。新增
> `tools/domain_event_owner_migration.py`，plan/apply 明确分离：拒 WAL/SHM，immutable SQLite + integrity check，
> 对 protected tables 的 schema/有序逐行摘要，SQLite logical backup，同目录 staging 上调用 owner migration，
> target chain + protected digest 双验证，替换前再验源 SHA，最后 atomic replace/失败 rollback；一个坏源不阻断
> 独立好源，异常只留类型。真实维护窗口先禁用 event task，三库均迁移 PASS：dry-run 7 行、experiment
> 2 trials/2 alphas/8 evaluations、refresh 5 history/20 source rows 的 protected digest 前后完全一致，三份本地
> rollback DB 保留。dry-run/experiment 不伪造历史；refresh 依既有 legacy contract 回填 5 条事件，全部
> publish/ack/project，pending=0、DLQ=0。`RefreshHistoryStore(create=False)` 也修为缺 outbox schema 时在 WAL 前
> immutable preflight，失败不再制造 sidecar。计划任务已恢复并现场返回 **2/PARTIAL**：三 migrated owners PASS，
> alpha-maintenance/kill-switch/deployment 三个未启用 authority 仍 UNAVAILABLE，未为空绿而造库。同机 migration
> backup 不冒充 off-host 灾备；失败的 `QUANTOS_backup` 与独立 scheduler watchdog 仍是下一优先级。
> `P2-5` 第十二增量关闭了“失败任务继续静默跑”和“备份源码存在即 PASS”两处运营假绿。旧
> `QUANTOS_backup` 的现场根因是用户级口令只有 11 字符，低于 12 字符 fail-closed 下限，且没有
> `QUANTOS_BACKUP_DEST`；旧任务现已禁用。新 `tools/l2_backup.py` 明确覆盖 10 个必需 L2 authority store，
> 对活动 SQLite 使用 online backup API，逐制品 AES-GCM 加密，生成加密 manifest，原子发布前执行隔离恢复、
> SHA-256 与 SQLite integrity_check；默认只接受 UNC/不同卷目标，key/ 与 broker credentials 不在 allowlist。
> `QUANTOS_scheduler_watchdog` 已每 15 分钟注册并真实运行，不 import 被监控 worker，校验 task state、精确 module
> action、working directory、last result 与 cadence。现场结果为 **PARTIAL/2：6 PASS、1 PARTIAL、1 UNAVAILABLE、
> 0 BREACH**；event ops 的 2 是已知未启用 authority，backup 因配置不完整保持 UNAVAILABLE。成熟度 checker
> 已从 `paths_exist` 升为 operational evidence，当前机器结论因此诚实回落为 `NOT_TOP_TIER`：
> **15/22 PASS、5 PARTIAL、2 MANUAL_EVIDENCE_REQUIRED、0 FAILED**。下一步需人工从受控 secret source 配置
> 12+ 字符口令及 off-host/不同卷目标并保留首份证据；本机 watchdog 仍不能发现整机/Task Scheduler 同时失联，
> 外部 heartbeat/WORM retention 继续开放。
> 本增量最终统一门禁 **18/18 阶段全绿**：主套件 **2334 passed / 1 skipped**，live/trading/tools
> **954 passed**，前端 **68 tests + typecheck/build**，治理扫描 **311 files / 0 violations**；Catalog
> **11 types/16 definitions**，服务图 **16 domains/36 edges/0 cycles**，供应链 **320 components**。
> `P2-5` 第十三增量补上带外失联与链截断防护接缝。`EventAuthorityCommitment` 统一承诺 event、
> delivery journal、consumer-group coordination 三条已验证链的 count/max-seq/head，并以 overall SHA-256
> 封装；`extends_authority_commitment` 允许正常追加，但对截断、整体替换、锚点位置 hash/行数漂移失败。
> `tools/domain_event_seal.py` 经 read-only SQLite snapshot 生成 content-addressed、create-once 锚点，已存在
> 同名不同内容时拒绝覆盖；明确标 `PARTIAL_EXTERNAL_WORM_UNVERIFIED`，不删除/VACUUM authority。真实诊断
> 锚定 5 event / 10 delivery / 33 coordination rows，随后 extension verify PASS，临时锚已清理。
> `tools/external_heartbeat.py` 只向 operator-owned HTTPS 端点发送白名单健康摘要，拒绝 userinfo、fragment、
> loopback 与 redirect，endpoint 只留 SHA-256 指纹；当前无 endpoint，手工运行诚实写 `UNAVAILABLE` 且未发网。
> 两项均为配置存在才注册；首次 reconciliation 暴露 `schtasks /query` 在 missing task 上被 PowerShell Stop
> 提前中止，现改原生 `Get-ScheduledTask`/`Disable-ScheduledTask` 并真实重跑成功。watchdog 当前为
> **6 PASS / 1 PARTIAL / 3 UNAVAILABLE / 0 BREACH**：backup、external heartbeat、event seal 均未配置，
> 不能冒充 off-host/WORM/带外 SLA 已完成。
> 本增量首次全门禁发现 `test_source_process_deadline...` 在 Windows 全负载 spawn 冷启动下把健康 worker
> 的 2 秒非目标预算耗尽；隔离复跑通过，生产 hard deadline 逻辑未改。测试把健康/global 预算调到 5 秒，
> 故意 hang 源仍为 200ms，四路并发压力 4/4 通过。最终统一门禁 **18/18 全绿**：主套件
> **2338 passed / 1 skipped**，live/trading/tools **969 passed**，前端 **68 tests + typecheck/build**，
> 治理扫描 **311 files / 0 violations**。机器评估保持诚实：**15/22 PASS、5 PARTIAL、2 MANUAL、0 FAILED**。
>
> `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md` 与旧机构补全计划保留为历史研究输入；
> 新开发排序必须以能力矩阵中的 `classification`、`red_line`、目标成熟度和
> `next_action` 为准，同时始终保持 `L2_DRY_RUN_ONLY`。

> **用途**:换新上下文/新会话时,**只读这一个文件就能接着干**。每当上下文快满时刷新它。
> 约定:本文中文叙述;代码标识符英文。最近更新锚点:见末尾"提交锚点"。

---

## 0. 一句话 + 安全边界(不可让)
QUANTOS = 安全关键的 **AI 量化研究平台**,就绪等级 **L2_DRY_RUN_ONLY**(只研究 + 干跑模拟,
**无实盘、无券商、无真凭证**)。权威规格:`QUANTOS_MASTER_BLUEPRINT.md`;开发计划:
`docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md`;因子方法论地图:`docs/factor-methodology-implementation-map.md`。
**当前活路线(2026-07-10 起):** `docs/QUANTOS_TOP_TIER_STANDARD.md` +
`docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md`;旧计划仅作历史输入。

**机器强制的不变量**(改动绝不能破):
- `LIVE_TRADING_ENABLED=false / REAL_BROKER_ENABLED=false / ALLOW_ORDER_SUBMISSION=false /
  ALLOW_AI_DIRECT_TRADING=false / REQUIRE_HUMAN_APPROVAL=true`。
- **纯 Python 标准库**(禁 numpy/scipy/torch/pandas/ccxt 等)、**确定性**(无 RNG、无 wall-clock;
  前端新代码无 Date.now/Math.random)、**失败关闭**、**离线门禁**、**可复现**。
- **容器化(神圣)**:非人类 AGENT actor **永不可达 APPROVED**;一切经未改动的 `ResearchAgentLoop`
  (PROPOSE/EVALUATE),`guardrails.assert_action_allowed`。
- 网络**只在门禁外** `tools/`;`services/` 整棵树**零网络 import**(机器强制)。
- 不提交密钥;密钥读自 `$COINGECKO_API_KEY` 或 gitignore 的 `key/coinkey.txt`,绝不打印/提交。

## 1. 单一门禁(改完必跑,绿了才提交)
```bash
python scripts/validate.py     # AST 治理扫描 + 全 pytest 套件(contracts/governance/research/trading)
```
隔离 pytest 需 `PYTHONPATH=/h/code/QUANTOS`(从仓库根跑)。`main==master` 手动同步:
`git branch -f main master`。提交信息中文;结尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

## 2. 工作节奏(已确立)
brainstorm(大改才需) → 先写测试(tests-first)→ 实现 → `validate.py` 绿 → 提交 → 必要时对抗式评审
(Agent/Workflow)→ 修 P0/P1 → 再提交。**最小安全改动;绝不藏/删失败测试。** 用户偏好:
聊天用**简单中文、少术语、术语用大白话解释**(见记忆 `reply-plain-chinese`);文档/注释也中文。

## 3. 已建成(本仓真实代码,按能力)
**数据**:`services/data/` —— PIT 存储 `persistent_store`、**`caching_store.py`(内存读缓存:回测从
小时级降到秒级,与 SQLite 逐位一致,接进所有驱动)**、survivorship-free `universe_store`、`versioning`、
质量/归一、`market_csv`、`market_provider`(多资产离线接缝 + ingest)、`adapters.py`(声明式 SourceSpec +
数据源注册表)、`calendar_store`、`fundamental_store`。
**加密数据(门外只读)**:`tools/coingecko_fetch`(OHLC 4h)、`tools/coingecko_volume_fetch`(真实成交量)、
**`tools/cryptocompare_fetch`(中立聚合商 histoday,多年日线·含 2022 熊市,免费 key 读自 key/cryptocompare*.txt)**;
治理边界 `tests/governance/test_crypto_data_boundary.py` 钉死**三**抓取器只含 stdlib+services/tools、无 SDK/无下单。
**信号/特征**:`signals.py`(8 价格算子)、`volume_signals.py`(4 量能算子)、**`alpha_expression.py`(截面 alpha 引擎:
PIT 面板上组合时序算子 + 截面算子 rank/scale/demean/zscore + 逐元,解锁 Alpha101 那类真因子;无前视测试钉死)**、leakage 检测。
**因子/评估**:`factor_engine`(IC/ICIR)、`alpha/evaluator`(DSR/PBO/regime 门、失败关闭、
**opt-in `compute_regime_evidence`**)、`catalog`(试验计数/var_sr)、`config`、`multiple_testing`、
`orthogonalize`、`panel_backtest`、`capacity`、`walk_forward`(**带 embargo 禁运**)。
**因子发现**:`gp_tree.py`+`gp_genome.py`(**GP 表达式树**)、`evolutionary_search.py`
(flat P/F/X + GP 'T';opt-in 奖励:`parsimony_lambda/mu`、`redundancy_lambda`(增量去冗余)、
`diversity_lambda`(**频繁子树规避**))、**`mcts_search.py`(MCTS/UCT 导航)**、`guided_search`(UCB)、
`search_space`、`interaction_factors`、`fundamental_factors`。
**主线编排**:`intelligence/research_session.py::run_research_session`（固定受控搜索 runtime；
`research/session.py` 只保留可注入的确定性核心）——模式 `evolutionary/gp/mcts`(+guided);
搜后 `synergistic_set`(去冗余协同集);**`oos_validate=True` 自验证(对自己挖的因子跑无窥视 OOS)**;
`regime_evidence`。
**验证/诚实**:`oos_validation.py`(**无窥视滚动选择 OOS**)、`validation/`(deflated_sharpe/walk_forward/
gate/pipeline)、`labeling.py`(三重门+元标签)、`regime.py`(波动率状态分档)、`information.py`(互信息)、
`factor_set.py`(协同集 + novelty)、`subtree_stats.py`、`sample_weights.py`、`factor_timing.py`、`frac_diff.py`。
**容器化/生命周期**:`agent_loop`(ResearchAgentLoop)、`lifecycle` 状态机、`guardrails`、Actor。
**组合/风险**:`portfolio`(risk_model/construction/attribution)、`research_risk` 门。
**通往实盘的安全脚手架(轨道3,2026-07-01)**:`services/trading/broker_adapter.py` —— 把「换适配器」形式化成
`BrokerAdapter` Protocol + `DryRunBrokerAdapter`(透明超集包装现有引擎、零改动、流水线 drop-in)+ `LiveBrokerAdapter`
(结构性拒绝:L2 下 `live_trading_armed` 恒 False → 构造即 raise,绝不 import 券商 SDK)+ `assert_dry_run_adapter`
fail-closed 守卫接进 `simulate_fills` 首行。治理 `test_broker_adapter_boundary.py` 钉死无 SDK/网络 + L2 下 live 不可
实例化。子项目2 `services/governance/approval.py` —— 多方人工批准状态机:不可变 `ApprovalRequest` + 纯转换
(`open_request/review/approve/reject/consume`,每步哈希链),硬不变量 `REQUESTED→REVIEWED→APPROVED→CONSUMED` +
提出/复核/批准三个两两不同真人 + 一次性绑 subject;`verify_multi_party_grant` fail-closed;接进 `KillSwitch.
reset_with_grant` 与 `evaluate_deployment(grant=)`(只做加法、旧路径零回归、LIVE 仍挡死)。治理钉死无 SDK/网络 +
非人到不了 APPROVED。子项目3 `services/governance/signing.py` + `docs/PHASE_14_PROCESS_ISOLATION.md` —— 出进程签名
权威接缝(收口):`SignedDecisionRef` + `NullSigningAuthority`(进程内无密钥、`sign()` 即拒)+ `verify_signed_ref`
fail-closed(无出进程权威恒 False,伪造权威也 False);威胁面文档列清所有「受信代码可绕过」面 × 出进程签名怎么堵 +
post-L2 迁移路径。诚实决定:**不接进 L2 执行路径**(强接要么死代码要么堵死干跑)。治理钉死无密钥材料 + 无 SDK/HSM
import。**轨道3 = 3 子项目全 landed:接缝(✓)、多步人工批准状态机(✓)、Phase 14 进程隔离设计(✓)。** 全程 L2 一寸不让。
**执行/干跑(端到端能跑的系统,2026-06-30)**:`risk/pre_trade`(23 检查电池 + latched `KillSwitch`,
**运行时 chokepoint**)、`execution/{intent_builder,dry_run_pipeline,dry_run_store}` —— `run_dry_run_pipeline`
把权重→目标手数→意图→**逐意图过门(fail-closed,latched→全拒)**→干跑适配器纸面成交(**绝不真发**)→
平均成本对账(恒等式残差≈0)→哈希链审计→可复现 `DryRunRun` 落 `data/cache/dry_run.db`。驱动器
`tools/dry_run_carry.py`(carry 最新腿 × vol-target 杠杆端到端;首跑流动 58 币→23 名组合,门批准 23/拒 0、
纸面成交 23、成本 9.9bps×换手=约 −$956、残差 0.0)。`trading/dry_run_adapters` 无券商 SDK。把预交易门从
"已测死代码"变成"运行时可证强制"。**`run_multi_period_dry_run` + `tools/dry_run_carry_multi.py` 升级为周度滚动**:
逐周增量调仓单过门(fail-closed)→纸面成交→`reconcile` 价格 MTM + 资金费现金流(carry 真边)→raw/vol-target
两条净值曲线→多期 `DryRunRun.detail.equity_curve`(首跑:流动 58 币×313 周,门批准 15908/拒 67,总收益 126.7%、
夏普 0.45、回撤 77%、累计资金费 $166k、残差 1e-9)。**已上 console**:`/api/v1/console/dry-run` + 干跑页
`DryRunPipeline.tsx`(单期 + 多期 SVG 净值曲线,nav「干跑流水线」,浏览器验证过)。诚实:周度滚动 carry 多数收益是
L/S 价差(非 beta 中性、回撤大),funding 较小稳定项——执行现实把理想化 carry 的尾巴暴露出来。**美元中性实验**(每期记
`net`/`avg_net_exposure_pct`,console 出 KPI):证伪"回撤=舍入净敞口"假设——$1M 时净敞口已仅 ~1.8% 且 77% 回撤
$1M↔$100M 规模不变,故回撤是 **L/S 截面价差本身**、非净市场敞口/舍入;funding(~16.6% 累计)是骑在波动价差上的
稳定边、无法干净隔离。**实盘 = 同接口换真券商适配器 + 人工批准 = 非目标。**
**持久化**:`experiment_registry`(stdlib sqlite)、**`paper_trading/{store,advance,health}.py` 前向 paper 追踪库
(append-only `data/cache/paper_track.db`,真 train/paper 切分,驱动器 `tools/crypto_paper_track.py`;喂 LifecycleMonitor
建议 / 衰退维护 / Alpha Zoo 时间维。`live_ic` 仍 null——离线源无截面 IC 面板;纸面夏普在 ~24 期尾巴是噪声非 edge)**。
**前端**:`apps/research-console`(React18+Vite6+TS+Tailwind,premium fintech;26 页;**Crypto Live 页**
显真实加密因子 + OOS 裁决 + 协同集面板)。**Console API**:`research/console_api/main.py`(只读快照 +
`/api/v1/console/crypto-live` 真实数据端点,provenance=REAL_CRYPTO_COINGECKO_OFFLINE_SNAPSHOT)。
**门外驱动(tools/)**:`crypto_research_run`、`crypto_gp_run`、`crypto_oos_validation`、`crypto_volume_research`。

## 4. 当前实证结论(诚实)
- **候选第二边 `crowd_contra`(持仓反向,2026-07-01 用截面 IC 面板重新确认)**:反着做拥挤的散户多空
  仓位(14 大币 OI 数据),mean_ic=0.035、**t-stat=4.32(统计显著)**、过 4/5 项证伪检验(滞后1天/10bps
  成本/子集/对 carry 增量存活,唯一没过的是"跨年广泛性"——2020-21 接近零、2022 年后才显著)。此前研究
  自己的结论就是"proceed to paper-track"。**2026-07-01 已把它送进干跑流水线**(`tools/dry_run_crowd_contra_multi.py`,
  第三条策略过策略无关的 `run_multi_period_dry_run`):14 大币 × 232 周 × 10bps × 含资金费——**总收益 +185.6% ·
  夏普(周→年)1.21 · 最大回撤仅 12% · 门批准 3066/拒绝 0 · 对账残差 7e-09**,三条里执行现实表现最好(夏普远高于
  carry 0.45/trend 0.40,回撤远低于 carry 77%/trend 37%)。**诚实边界:这是对已知历史的执行现实回测(整手+门+
  成本+换手,恒等式成立),非前向/实盘;宇宙仅 14 大币;证伪已过≠前向必赚。** 走干跑而非前向 paper-track——缓存上
  "前向 paper-track"只是留尾回放(已被 no-peek OOS 折叠覆盖);是否送前向 paper-track/加仓位仍是独立的下一步。
- **现实主义引擎 RE-1 首跑抓到自欺(2026-07-01,`services/research/realism/`)**:Almgren-Chriss 冲击 + 容量衰减带
  应用到 carry/crowd_contra。**关键发现:参与率上限(10% ADV/期)是绑定约束——真容量 carry ~$2M / crowd_contra ~$7M**;
  只看冲击会误报 $743M–$4.1B / $1.69B–$9.1B(**~300× 高估**)。这正是差距报告警告的「松现实主义 × 关卡抓海市蜃楼 =
  无 edge 信号在组合层看起来可部署」——引擎首跑即抓到,且指出**可部署性受限于*构造*(最小 ADV 名满权重),非仅信号**。
  诚实标 `UNCALIBRATED_PRINCIPLED`(冲击系数未校准 → 区间带)。
- **现实主义引擎 RE-2 情景重演 + 尾部(2026-07-01,`services/research/realism/{scenario,tail_risk}.py`)**:把 carry/crowd
  拉过真实危机(2021-05/LUNA/FTX/2023-03 银行)+ 尾部量化。**诚实发现:① 美元中性预判证实——崩盘 beta 极低(下尾依赖
  carry 0.13 / crowd 0.0),crowd_contra 在 LUNA(+10.6%)/FTX(+6.5%)崩盘里反赚——尾部风险是*截面价差击穿*非市场 beta;
  ② 真尾部内生(carry 最差 19% 在 2020-11 牛市,非崩盘;干跑 77% 是 vol-target 加杠杆口径);③ 尾偏肥 Hill ~2.4–2.7;
  ④ **2026-07-13 更正**:`crypto_daily.db` 是统一 quote USD volume，`$3.7Q/$20k` 来自容量消费端二次乘价；
  已加显式 volume semantics 并修复，trend 容量重算约 `$47.9K`(短样本/未校准)。** RE-2 剩 Copula 全拟合。
- **FRED 宏观数据方向第一增量(2026-07-01,`tools/fred_fetch.py` + `services/research/macro_regime.py`)**:股票免费源本环境被墙
  (Stooq bot-wall / Yahoo 429,不绕过)→ 转 FRED(可达)。抓 6 条市场型日频序列(VIX/DGS10/T10Y2Y/HY-OAS/DXY/DFII10,39271 行)
  填进已建但从未填的 `fundamental_store`(PIT kt=et)。**关键发现:carry 与 crowd_contra 宏观 regime 画像相反、互补**——carry
  爱平静/risk-on(低VIX SR+2.36 vs 高VIX+0.65,正常曲线+2.08 vs 倒挂+0.67),crowd_contra 爱压力/risk-off(高VIX+1.96,倒挂+1.76)。
  crowd 正好在 carry 走弱处走强 = 强分散故事,系统化了 RE-2 的 LUNA/FTX 危机发现。诚实:regime=全样本分位(回顾性非PIT择时)、
  gross 无成本、HY-OAS 覆盖 155/期;股票 + 基本面 + 经济发布类(需 BYO-CSV/ALFRED)= 待办。
- **组合分散验证兑现(2026-07-01,`services/research/book_combine.py`)**:regime 互补 → 组合 carry+crowd 收益流。
  **corr=-0.04(近乎不相关)→ 组合夏普 1.95 > 单条 1.44/1.30,最大回撤 8% = 单条一半,总收益~187% 保住,VIX regime 被抹平。**
  两条正交 crypto 边合成 Sharpe~2/回撤 8% 的强分散。诚实:收益流层面(不同宇宙/规模、231 共同周、gross 无成本 RELATIVE 比较);
  同一执行 book(统一宇宙 + 干跑流水线)= 自然下一步。
- **对标顶级路线图 Rank #1 已做 + 诚实更正(2026-07-01,`tools/dry_run_combo_riskparity_multi.py`)**:风险平衡统一执行
  book(rolling 逆波动 sleeve,PIT)+ portfolio-of-books 配置——**carry+crowd 分散不敌执行现实**:三种方式 VT 1.20/1.14/1.21
  均 < crowd_contra 单条 VT 1.38。1.95/8% 是 gross 理想化(carry gross~1.44),净-成本下 carry 仅~0.58 拉不动 crowd,
  净曲线相关 +0.04。执行流水线抓到理想-vs-真的差。**deployable = crowd_contra 单条。** 见 `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`。
- **对标顶级路线图 Rank #2 已做(2026-07-01,Barra-lite read-only 诊断第一版)**:`services/portfolio/barra_lite.py`
  新增纯函数 read-only 因子风险诊断核(`winsorize/zscore/corr_exposure/build_barra_lite_model/barra_lite_report`),
  复用现有 `FactorRiskModel` + `risk_decomposition`;`tools/barra_lite_diagnostics.py` 从本地缓存构建
  5 因子(`carry/crowding/liquidity/market_beta/volatility`)并输出 gitignored 的 `data/cache/barra_lite_snapshot.json`。
  首跑:58 symbols / 5 factors;carry total_var 0.6544、crowd_contra 1.3969、carry_crowd_equal 0.5733。
  诚实边界:诊断-only,不等同完整机构级 Barra 因子库,不改权重/晋级/闸门/干跑/券商路径;治理测试钉死无网络/券商/订单/凭证 surface。
- **对标顶级路线图 Rank #3 已做(2026-07-02,拥挤门 primitive)**:`services/portfolio/crowding_gate.py`
  新增纯函数 fail-closed crowding gate,输入候选权重 + caller-supplied crowding evidence,输出 `PASS/WARN/BLOCK`。
  score=按绝对权重的拥挤暴露均值;缺失覆盖不足、显式 None/NaN/inf、空 book、非有限权重均 `BLOCK`。
  诚实边界:primitive only,未自动接入 `build_portfolio`/干跑/预交易门/券商/生命周期;后续若要改变组合构建行为需单独 spec。
- **对标顶级路线图 Rank #4 已做(2026-07-02,协方差感知多 book 资本配置 primitive)**:`services/portfolio/capital_allocator.py`
  新增纯函数 fail-closed covariance-aware capital allocator,输入多策略收益流 + 可选容量/质量/前次权重,输出 `ALLOCATED/BLOCKED`
  研究 AUM 权重建议、turnover 与诊断(`book_stats`/capacity/effective caps/risk contribution)。默认使用 lower-confidence expected return
  (`mean - mean_shrink_z * vol/sqrt(n)`) + shrunk covariance + 现有 constrained mean-variance optimizer,并叠加 per-book cap、capacity cap 与 no-trade band。
  诚实边界:研究 allocation primitive only,不读 `DryRunStore`/`run_registry`,不写 DB,不自动调仓,不接 dry-run/pre-trade/broker/lifecycle/readiness/console;
  治理测试钉死无网络/券商/订单/凭证 surface。`quality_multiplier` 来源和容量衰减曲线仍是后续独立 primitive。
- **对标顶级路线图 Rank #5 已做(2026-07-03,Brinson-Fachler 归因 primitive)**:`services/portfolio/brinson.py`
  新增纯函数 benchmark-relative attribution,输入 caller-supplied portfolio/benchmark weights + asset returns + group map,
  输出 `ATTRIBUTED/BLOCKED`、逐组 allocation/selection/interaction/active return 与恒等式残差诊断。手算两组、benchmark=portfolio
  零主动收益、long-short 权重加性、缺 return/group、非有限值、权重和异常、确定性均测试钉死。
  诚实边界:研究解释 primitive only,不读写 DB,不接 `DryRunStore`/`run_registry`,不自动调仓,不接 dry-run/pre-trade/broker/lifecycle/readiness/console;
  多层 sector/style/macro 适配器仍是后续独立接线,当前只对 caller-supplied 单层 group map 做严谨恒等式归因。
- **对标顶级路线图 Rank #6 已做(2026-07-03,冲击模型校准 primitive)**:`services/research/realism/impact_calibration.py`
  新增纯函数 calibration set,把 RE-1 的文献默认/敏感性假设形式化为 aggressive/base/conservative 三情景
  (`temp_coef` 5/10/20bps,`perm_coef` 2.5/5/10bps,平方根 exponent=0.5),provenance=`UNCALIBRATED_PRINCIPLED`,
  并提供 `impact_band_from_calibration()` 可直接喂给 `capacity_band`。测试钉死情景排序、`AlmgrenChrissImpact`
  转换、capacity band 契约、非法参数 fail-closed 与治理边界。
  诚实边界:这不是 venue/tick 实测校准,不改 dry-run 成本/成交、不改 `CostModel`/组合构建/预交易门/券商/生命周期/readiness/console;
  真实成交校准仍需 tick/LOB/执行数据。
- **新前向路线图 Rank #7 已做(2026-07-03,压力门接真实历史情景重演)**:审计发现 `StressTestEngine` 用 4 个臆造固定倍数
  「编造」情景,而真危机重演 `realism/scenario.py` 就在隔壁闲置。现在调用方给逐期 timestamps → 真危机窗口(2021-05/LUNA/
  FTX/2023-03 银行)重演 + 内生最差回撤窗;无 timestamps → 旧倍数仅作诚实标注回退(`SYNTHETIC_MULTIPLIER_UNCALIBRATED`)。
  每份 `PortfolioStressReport` 带 provenance/scenarios_skipped/scenario_losses,`RiskApprovalGate.approve(timestamps=)` 透传,
  console risk-dashboard 透出 stress 块(契约测试钉死无 provenance 的压力数不上线)。诚实边界:console 路径暂无逐期时间戳,
  诚实显示回退标注;有时间戳的调用方(如干跑 book)同一字段拿到 `REAL_HISTORICAL_REPLAY`。
- **机构级路线图收官批(2026-07-03,PR #15 已合 main,续批在 `codex/firm-aggregation-and-followups`)**:
  PR #15(S1–S3 共 11 项)合并进 main 前**发现并修了一个 CI 隔离 bug**——3 个 lifecycle 测试依赖 gitignored
  `experiment_registry.db`(本地过、CI 红),加 `_require_real_registry` 守卫(有库跑、无库诚实 skip),两 CI 检查转绿后合并。
  续批(新分支)把路线图剩余项**逐个交付核心切片**、每项 tests-first + 门绿:**#25**(45ffb5e)`firm_aggregation`
  跨 book 真实暴露 + 重叠检测(primitive,9 测);**#26**(bb8afb8)system-health 真 `monitoring` 块(build_snapshot
  验 durable 审计链);**#27**(272fd71)`IncidentLedger` + 端到端 kill-switch 演练测试 + runbook;**#14**(e7ba04f)
  PIT regime 分类器(扩展窗分位 + 免估计,截断不变);**#15 采纳**(0e93684)物化桥 read==recompute bit-identical;
  **#12 BHY**(44a277d)BHY FDR 接进晋级守卫(比 BH 严);**#19 rebuild**(f831f4e)离线重算编排器 + digests 扩到
  ic_panel/capacity;**#16**(41151a0)ML 提议器 spec;**#28 pin**(7d2e035)`.python-version`=3.12 消解解释器漂移。
  **剩余(诚实分类)**:①**大重构宜专注做**——#24 console_api 5167 行拆路由组、#12 CatalogEvidence 封套 + effective_trials;
  ②**外部资源阻塞**——#17 股票历史成分/ALFRED、#22 volume 回填(coingecko 网络被墙);③**CI 迭代**——#28 lockfile/
  coverage/pip-audit;④**增量采纳**——#25 driver(dry_run.db 不存逐符号权重)、#15 驱动器读缓存。核心/基础全部已建已测。
- **机构级路线图 S3「溯源与持久」sprint 推进(2026-07-03,#12/#15/#19/#24 各一切片)**:
  **#12**(c7d0e5e)`ExperimentRegistry.verify_evaluation` 篡改检测(重算 content_hash 比对,改指标/改哈希/未知 id
  全 fail-closed,4 测)——补上「只算不验」缺的验证半边。剩 CatalogEvidence 封套 + BHY 接守卫 = 待做。**#15**(c5ae7a2)
  特征层持久化:`SqliteFeatureRegistryStore`(镜像 run_registry,含 leakage_passed+code_version 审计)+ `SqliteFeatureValueStore`
  (克隆 fundamental_store 双时点 + feature_version,PIT/截断不变/版本隔离/fail-closed,10 测)——特征层脱离「重启即失」。
  剩 rolling_feature 写通道 + 离线填充 + console 读 = 采纳待做。**#19**(9cc4c25)复现底座:`evidence/dry_run_books.json`
  指纹(5 book 承重数 + 残差界 + code_version)+ `tools/build_evidence_digests.py` 生成器 + `tests/reproducibility/` 门测试
  (缓存在场重算断言、缺席诚实 skip;drift 实测会红、rebuild 恢复绿)——repo 现能自证其引用的干跑结论。剩扩到其余快照 +
  完整 rebuild harness = 待做。**#24**(前端地板已在 a4e4350 IA 重构落地:30/30 冒烟 + 集成 + payload 守卫,47 测);
  console_api 5167 行拆路由组 = 大重构宜专注新会话做。**远端已推 origin/codex/impact-calibration。**
- **机构级路线图 S2「承重」Rank #22 强制机制做完(2026-07-03,数据质量从「算而不 gate」变机器强制)**:
  (e241c00)① `_ingest_records/ingest_bars_csv/ingest_provider_rows` 加 opt-in `strict=`:单标的数据集质量报告不过
  (重复/乱序/缺失/未来时间戳)→ raise `FatalIngestionError`(fail-closed,默认 False 零回归);yahoo fetcher 接 strict=True。
  ② `services/data/quality.py::check_volume_units` 跨币量纲一致性守卫(max/min > ratio → 非单一量纲,formalize
  crypto_daily.db bug 的 EOS $20k vs BTC $3.7Q ~17 数量级启发式);scenario_tail_research 改用它作单一真源。9 新测试。
  **诚实边界:volume 回填 + trend 容量重算需 coingecko 网络(本环境被墙)= 待网络;抓 bug 的强制机制已就位并测过。**
  **S2「承重」四项(#10/#11/#13/#22)全推进**(#22 强制机制 done、回填待网络)。远端已推:`origin/codex/impact-calibration`。
- **机构级路线图 S2「承重」Rank #11 + #13 做完(2026-07-03,执行现实主义 + 风控门承重)**:
  **#11**(cbf8609)冲击成本进干跑:`simulate_fills`/`run_multi_period_dry_run` 加可选 `impact`(Almgren-Chriss)+
  `adv_by_period`——commission 变参与率驱动的冲击成本(缺 ADV 逐笔回退平 bps,恒等式残差不破);cost_model/
  cost_provenance/TCA-lite 区间落 detail(透传 console)。carry 驱动用面板 close*volume 同源喂**冲击成本**(名义)
  + **参与率门** est_daily_volume(单位)+ 真实 max_participation_rate=0.10。**活跑:IMPACT_AC/6365 笔/TCA 计入 $8.8k
  区间[$4.4k,$17.6k] in-band/残差 4e-9,参与率门拒 95 笔(真容量约束,曾 0)**。诚实边界:crowd/combo(OI 无 spot
  volume)+ trend(volume 不可靠,#22)暂平 bps。**#13**(93fd065)研究风控门承重:新 `dry_run_guard.evaluate_dry_run_risk`
  只跑干跑接缝**有真输入的 4 引擎**(market/drawdown/concentration/stress,各 fail-closed;stress 用 timestamps →
  REAL_HISTORICAL_REPLAY),**其余 5 引擎 SKIPPED_NO_HONEST_INPUT——绝不喂编造数据伪造风控放行**(这正是平台不能做的);
  接进 crowd_contra 干跑落 detail(VT 序列 3/4 过,market-VaR 仅在 RiskConfig 默认阈值日频紧下未过,诚实标注 config_provenance)。
  晋级门 guard_to_paper 接线 + 5 引擎喂真输入 = 后续。**S2 剩 #22**(数据质量强制 + volume 量纲修复,含网络回填)。
- **机构级路线图 S2「承重」sprint Rank #10 做完(2026-07-03,组合原语从诊断变 load-bearing——全盘最大杠杆)**:
  三子项全落地、每项 tests-first + `validate.py` 绿 + 活驱动器端到端跑过。**10a**(a287e95)拥挤门加固:
  `crowding_gate` 加最大单名并行判据(外部评审 P0 稀释修复——集中且拥挤的单名独立触发,不被加权平均稀释)+
  可选 ADV/流动性维;接进 crowd_contra 干跑(逐周从真 global_ls cs-rank 评估、落 `DryRunRun.detail`、真 BLOCK
  跳调仓)——诊断非否决(crowd_contra 按构造持拥挤名 → 232 周全 WARN 记录不否决;集中判据在扩散 book 不触发,
  但集中 book 会 BLOCK);非-block 周字节等价(回归安全)。**10b**(55d7e17)资本配置器驱动 riskparity sleeve 权重
  (PIT 仅过去收益,BLOCKED/观测不足回退 rolling 逆波动;231/313 周用 allocator)+ `_clean_weights` 改有界迭代注水
  (显式收敛+fail-closed)。**10c**(0d14bb7/32d7755)优化器加 Rockafellar-Uryasev CVaR 尾部惩罚选项(尾部次梯度,
  非光滑路径取最优可行迭代;光滑路径字节等价)+ 经 allocator `cvar_penalty` 接进 riskparity(尾部感知 sleeve 定权,
  carry 0.47/crowd 0.53)。**52 新测试;三条既有 book(carry/crowd/trend/combo)未动,riskparity 按设计变(allocator 驱动)。**
  历史当时边界为拥挤阈值未校准；2026-07-13 已补 crowd_contra 签署 z-gate 的机器重放证据，
  但通用默认与 carry/combo 宇宙仍 open。**下一步 S2**:#11 冲击成本进干跑 → #13 风控门承重 → #22 质量强制。
- **机构级路线图 S1「守卫与留痕」sprint 全做完(2026-07-03,活路线 `docs/2026-07-03-institutional-completion-plan.md`)**:
  4 项 P1/S 全落地、每项 tests-first + `validate.py` 绿。**Rank #8**(6ca7d92)PIT 裸读机器守卫:
  `tests/governance/test_pit_read_boundary.py` AST 钉死 services/ 白名单外 0 裸 `fetch_bars` + tools/ 计数棘轮
  (35,只减不增),正负控制 + 实地探针证明守卫会红。**Rank #20**(0630f0c)治理决策留痕:新
  `services/monitoring/audit_store.py::DurableAuditLog`(append-only sqlite 哈希链,存 entry_hash 读时重算抓字段篡改、
  prev 链抓重排、seal/verify_against 抓截断/替换/擦除,镜像 run_registry 模式、无墙钟);`KillSwitch.arm/reset/
  reset_with_grant` 与 `evaluate_deployment` 加**可选** `audit_sink=`(默认 None 零回归,失败安全排序:arm 先停机后审计、
  reset 先审计后恢复、部署门审计在 try 内失败即关)。**Rank #18**(4e41851)runner 调用方身份:POST /run 强制
  `X-Operator`(缺→400),operator 落 run 行(走 payload_json 免迁移、不进 run_id)+ 审计链 `research.run.triggered`;
  **活系统端到端验证过**(无 header 400 / 有 header 200 回显 / 非白名单 kind 仍 400 / 审计链记实);前端 `triggerRun`
  带 localStorage operator 标签 + ResearchRuns 页输入框。诚实钉死:operator=自述式、未鉴权(非 RBAC)。**Rank #21**
  (5e2a64b)fetcher 治理收编:`yahoo_equity_fetch` 改走规范 `ingest_provider_rows` 接缝(只 stdlib+services、进
  `_FETCHERS`→全 10 网络 fetcher 入 AST 边界)+ 存 **RAW 未调价**(去掉 adjclose 回溯比例烘价的非-PIT-vintage 前视,
  完整分红调整留读取时 Rank #23),provenance=`YAHOO_V8_QUOTE_RAW`。**系统已启动并在跑**:console API :8400(真数据)、
  runner :8420(已重启加载 #18)、前端 :3100(连真后端、新 7 组导航)。**S1 剩清理批**:CLAUDE.md 计数刷新、
  跟踪 AGENTS.md。**下一 sprint S2「承重」**:#10 组合原语接线 → #11 冲击成本进干跑 → #13 风控门承重 → #22 质量强制。
- **前端信息架构重构已做(2026-07-03,用户指令「按顶级 AI 量化系统彻底重构前端」)**:42 页 → 30 页,
  7 个工作流导航组(总览→数据→因子研究→验证→组合与风险→执行与跟踪→治理与系统);砍 12 个纯合成蓝图/
  重复/演示页(旧路由语义就近重定向,蓝图页→SystemCompleteness 诚实完整度页——删页不删诚实);路由/导航/
  冒烟测试收敛到单一页面注册表 `src/app/routes.tsx`(新页一行自动进导航+测试);测试地板 14/42 → **30/30
  全冒烟 + 注册表一致性**(44 fe tests);后端端点零改动(契约锁定)。typecheck/build/test/validate 全绿。
  Rank #24 前端半边(全页冒烟)提前落地;console_api 后端拆分 + provenance 徽标断言仍待做。
- **新前向路线图 Rank #9 已做(2026-07-03,部署门强制 version content-hash 钉死)**:版本契约(ConfigVersion/StrategyVersion/
  ModelVersion)一直存在但 `evaluate_deployment` 从不校验——「version-pin gate」原是 overclaim。现在 PAPER+ 必须声明部署内容
  且逐版本与批准钉死表(id→content_hash)比对,fail-closed(`VERSION_PIN_REQUIRED/_NOT_PINNED/_MISMATCH/HASH_MISSING/
  STRATEGY_VERSION_NOT_APPROVED`);多方 grant 强路径不绕过(批准=谁可部署,钉死=部署什么);DEV/TEST 不受限;LIVE 仍被
  readiness pin + LIVE flags 结构性挡死。
- **统一执行 book 做了(2026-07-01,`tools/dry_run_combo_multi.py`,第 4 条策略)**:carry 面板加 OI global_ls,每周
  0.5·carry_legs+0.5·crowd_legs 过干跑流水线。RAW/VT 两条曲线:carry 0.45/77%→0.65/22%、crowd 1.21/12%→1.38/11%、
  **combo 0.58/56%→1.20/15%**。诚实教训:naive 权重 blend 是 carry-dominated(58币 vs 14大币半权)→ combo VT 1.20 improved
  carry 但仍 < crowd alone 1.38;equal-risk 收益流组合(1.95/8%)≠ naive blend——执行里兑现分散需 risk-balance sleeve。
- **短周期(4h/小时线、小样本)= null。** 价格/GP/量能因子都无稳健 OOS edge;加宽截面把样本内
  Deflated 夏普抬到 0.586,但**无窥视 OOS 证伪**(选择性过拟合,样本外 −2.01);量能因子净夏普全负。
- **日线周期 = 第一个正信号(2026-06-26,`tools/crypto_daily_research.py`,commit 1564bb6)。** 同一
  CoinGecko demo,`market_chart days=365`→日线(~364 根/币),24 币×364 期,日级窗口进化搜索。结果与
  4h **截然不同**:最优因子全是同一族 `dist_from_high`(距 N 日高点=趋势代理,7/14/30),IC≈0.065、
  **扣成本后** net Sharpe≈2.4、Deflated 0.95;**无窥视选择 OOS 这次没被证伪**(mean OOS 年化 +3.95 vs
  IS +2.90,3/4 fold 为正)。
- **但要克制(不是"找到 alpha"):** `sharpe()` 按 √252 年化(3.95≈日 Sharpe 0.25),OOS 池是**毛收益**;
  逐 fold 极噪(+7.43/−0.80/+6.26/+2.93,被两个暴涨 fold 拉高、一个为负);**整段样本=2024-25 单边牛市**,
  而"距高点"正是牛市机械获利、反转即崩的因子,**未经熊市/震荡验证**;可信的数字是 IC≈0.065(温和但真实)。
- 容器化始终守住:auto_approved=0、全 REQUIRES_HUMAN_REVIEW——系统**没有**自动放行它(正确)。
- **对抗验证(`tools/crypto_daily_verify.py`,离线)两项都过:**(1)成本压力:OOS 净 Sharpe 随成本近线性
  衰减(3→3.95、10→3.18、20→2.37、30→1.06、50→−0.44),**盈亏平衡≈35–40bps 单边**;中盘币现实 ~10–20bps
  → OOS 仍 +2.4–3.2,**不是一扣成本就死**(区别于 4h 量能)。(2)留一币:基准 2.448,去掉任一币落在 [2.22,2.75]
  → spread 0.53 = **宽基**,非一两个币带飞。
- **Regime 拆解(`tools/crypto_daily_regime.py`,离线)比担心的好:**"只在涨市赚?"的疑虑**不成立**。dist_from_high_14
  原始每日 Sharpe 在两种波动率(calm +0.219 / storm +0.215,中波约 0)和两种涨跌市(跌/横盘 +0.191@n=208 /
  涨市 +0.106@n=128,跌/横盘反而更强)里**都为正**——因为它是**美元中性的相对强度**因子,天然与大盘方向无关。
  (accel_14 是弱的:涨市 −0.014;momentum_7 跌 +0.146/涨 +0.045。)注意:"牛市内的跌/横盘日"≠真正多月熊市,
  趋势反转处的"动量崩溃"(Daniel-Moskowitz)正是一年牛样本装不下的——仍需出 regime 数据才能彻底关闭。
- **完美化一轮(全 42 币 + 合成,`tools/crypto_daily_composite.py`):** 两条诚实教训——(1)**更多数据=更诚实
  (更低)的数**:24→42 币把无窥视 OOS 从 3.95 降到 ~1.45–1.73(24 币那批是最干净大盘,偏乐观);但单因子
  `dist_from_high_14` 跟踪 OOS=2.53、**4/4 段全正**(挖到的最稳的东西)。(2)**简单胜过花哨**:4 因子 z-score 合成
  在每个维度都不如单个 dfh_14(full 1.60<2.05、OOS 1.89<2.53、75%<100%);周度调仓更差(0.56<1.60,信号次日就衰减)。
  成本盈亏平衡(42 币):合成 ~22bps;现实 ~10bps 仍明显为正。regime:涨/跌、calm/storm 都正(全宽更温和)。
- **出 regime 验证(2026-06-27,`tools/crypto_history_research.py`,CryptoCompare 5.5 年、23 币×2000 日线、
  2021–2026 含 2022 熊市)—— 日线趋势 lead 已被证伪、死亡:**
  - 全样本容器化裁决:dist_from_high_14 net Sharpe **−0.55**、IC **0.001**(≈0);8 个价格因子里最好的
    (dist_from_high_30)也只有 **+0.034**(≈0),IC 普遍近零或负。
  - **逐年净夏普正负乱翻、毫无稳定性**:2021 **−1.82**、2022 **+1.0**、2023 **−1.61**、2024 −0.05、2025 +0.79。
    不是"牛市赚熊市亏",就是**不稳定**;2024–25 那段是**运气窗口**。
  - 全样本成本曲线从负起步(−0.55@3bps),无盈亏平衡;regime 按涨跌市**两边都负**(与一年样本的正好相反)。
    长样本无窥视 OOS 仅 +0.42(选了 momentum_14),噪声级,非 edge。
  - 容器化全程 auto_approved=0:系统**从未放行**它。**这是平台在正常工作** —— 更长更全的数据 + 诚实机制,
    戳穿了一个"一年牛市幻觉"。量化第一课就这么活生生上了一遍。
- **截面 alpha 引擎 + Alpha101 实测(2026-06-27,`alpha_expression.py` + `tools/crypto_alpha_research.py`)——
  更丰富的因子,同样没信号:** 建好了因子语法扩展(时序×截面算子,解锁 Alpha101 那类真因子),在同一份
  5.5 年/23 币面板上跑 15 个精选截面 alpha:**仍无跨 regime edge**。最好 accel_cs 全样本 SR 0.60 但被 2021 一年
  带飞(负 3/6 年);所有 |IC|<0.03;**幸存者 0**。**这把"瓶颈是数据不是因子生成"从论断变成了实证**:可表达空间
  放大几个量级、照样挖不到东西,因为价+量在大盘币日线上被高效定价了——alpha 不在这份数据里。引擎是永久基础设施
  (将来有更好数据时即用)。
- 定性(钉死):**"挖更多花哨因子"是错的杠杆,已实证。** 当前没有任何经得起跨 regime 的加密因子(简单的、丰富的
  都≈0)。真正的杠杆 = **数据护城河(口子)**:把广度铺到**小盘币**(低效定价处)、另类数据、其它资产类别、或更高频/
  微结构。下一步要么用户拍板开数据口子,要么把 `dist_from_high_14` 这类候选挂纸面跟踪。详见记忆 `crypto-research-findings`。

## 5. "口子"(2026-06-27 用户已授权放开 —— 目标=可盈利机构级系统,见记忆 `goal-profit-open-kouzi`)
用户原话:「放开口子,不再局限于研究——研究是底层,赚钱是目的」。以下口子**我可主动用**(推进 Phase 时):
1. **数据**(最大,~80% 边际):更长历史/逐根成交量/资金费/链上/另类/**行业·板块分类映射**(用于中性化)。
   `tools/` 只读拉取,**适配器接口已就绪**,填 SourceSpec 即接入。
2. **LLM 点子生成器**:**门外生成 + 门内当裁判**(LLM 提议假设/DSL,确定性引擎 + DSR/OOS/容器化裁决);
   不进 services/ 确定性核心、不破容器化(LLM=非人类 actor,永不 APPROVED)。参考 arXiv 2505.11122。
3. **numpy 提速**:可在评估层引入向量化,但须隔离评估、量化对确定性/可复现的影响。
4. ⛔ **硬红线(我绝不自己跨,必须用户多步人工审批)**:真实实盘下单 / 接券商·CTP·FIX SDK /
   翻 LIVE_TRADING_ENABLED·REAL_BROKER_ENABLED·ALLOW_ORDER_SUBMISSION / 提升 readiness(L2→L3+)。
   我把系统一路造到这条线前(research→paper→shadow→容量→成本→生命周期就绪),go-live 留作用户人工动作。

## 6. 定位(对"做到最好"的诚实回答)
**在"纯-stdlib、确定性、安全可复现的研究系统"这一类别里,过程已接近顶级**:发现(GP+MCTS)→去冗余→
不自欺验证(DSR/PBO/Purged-CV/无窥视 OOS)→全生命周期,且"不自欺"比所借 SOTA 论文更严。
**没有静态"最优解"**(市场非平稳+反身性+数据有硬上限);"最好"=最好的**过程/适应机器 + 数据护城河**。
要"整体最好"必须开第 5 节的口子。

## 7. 提交锚点(resume 时 `git log --oneline -1` 对一下)
**★★★ 系统排查审计 + 完善路线图(2026-06-29,用户指令 "先排查整个系统→再完善功能→最后做前端")**:
9 智能体全量取证审计(`docs/engineering/specs/2026-06-29-system-audit-and-completion-roadmap.md`)。
**核心结论:后端比文档真实得多,缺口是"接线"不是"造引擎"**(~1014 测试过,`services/{alpha,data,
portfolio,intelligence,validation,...}` 全真且测过;CLAUDE.md "~10% built" 已陈旧并纠正)。36 界面真实度:
REAL 7 / PARTIAL 6 / MOCK 17 / NONE 6 / BROKEN 1。`l2_ok=true`(全绿,仅 1 个 P2 已修)。
已批准:仅加密数据、真缺子系统页诚实标注。
- **Phase 0 排查收口**(`839c1bb`):删 3 死文件+空目录;App.tsx 36 页 React.lazy 拆 546KB 首屏→
  28KB+vendor166KB(警告消失);LifecycleMonitor/SystemHealth 补诚实徽标;修治理测试 P2;纠正 CLAUDE.md。
- **Phase 1B 真序列升真**(`d2aa6fb`+`86d2c34`):审计的"一行换源"前提不成立(快照只存真指标无真序列),
  故 ① session 暴露 `factor_returns`(真逐期净回报)② `tools/crypto_research_offline.py` 离线再生器
  (不联网,从缓存 PIT 库 crypto.db 重跑确定性 session,18 币×120 期,写 `factor_series`)③ console_api
  `_load_real_factors/_real_factor/_input_provenance`,`_forward_returns`+`_computed_alpha` 有真序列时跑真
  (真 ic/turnover/state+真权益/夏普/回撤,IR≈IC·√breadth),无快照 fail-closed 回退合成。6 端点
  provenance→`_input_provenance()`(真=REAL_CRYPTO_SERIES);契约接受 real-or-synthetic;前端 6 页 realData 徽标。
  **实测**:alpha-factory REAL_CRYPTO_SERIES+真因子名;risk 真 9 引擎门跑真回报→REJECTED_CORRELATION_RISK(诚实)。
  门禁双路全绿(真路径本地+合成路径 CI),containment auto_approved=0。HEAD=`86d2c34`。
- **Phase 2 接真引擎升真 NONE 页**(`2db98ce`/`0774fe0`/`b3e8767`,复用 Phase 1B 真因子序列 + services/{research,alpha} 真引擎):
  ① **种群优化器**(`/population-optimizer`)factor_set.select_synergistic_set 协同前向选集去冗余 + inverse-vol 权重 +
  真边际 IR + 真边际回撤(组合 maxDD 增量,可负=分散) + diversity(1−均|corr|);② **Alpha 相关图**(`/alpha-graph`)
  services/alpha/alpha_graph.correlation_graph 真相关网络(节点/边/连通分量冗余簇 + 后端确定性布局);
  ③ **Alpha 资产库**(`/alpha-zoo`)研究快照全部真因子目录(真 state/DSR/净夏普);④ **DSL 浏览器**(`/dsl-explorer`,
  `ee63604`)真 dsl_checker/complexity/expression_leakage 计算裁决(非硬编码):合法表达式机器证明 PIT 安全、
  ts_lead 读未来被真拒绝(确定性面板仅作泄漏扰动基底)。每个新端点 fail-closed 回退合成(CI 无快照安全)、
  契约 real-or-synthetic、前端 useConsoleSnapshot + realData 徽标。门禁全程双路绿。
  **真实度更新**:Phase 1B+2 后 6 PARTIAL + 4 NONE 已升 REAL(Risk/Validation/ComputationLab/Portfolio/
  PaperTrading/AlphaFactory + PopulationOptimizer/AlphaGraph/AlphaZoo/DslExplorer)。
- **Phase 3 起步**(`0033f1a`):**事件流**(`/events`)从真实 run_registry 派生事件(每个研究运行=一条只读审计事件
  research.run.{status},真标题/裁决/头条;前端已通用渲染,后端单文件改);registry 空→合成回退,
  provenance 条件化 RESEARCH_RUN_REGISTRY/SYNTHETIC。本会话共升真 **11 页**(6 PARTIAL+4 NONE+Events)。HEAD=`0033f1a`。
  **Phase 3 余下页(Experiments/EvidenceLedger/Agents/LifecycleMonitor/Overview)的真持久层多为空**
  (experiment_registry/audit_log/agent 运行记录未被离线再生器填充)——需先"填数据"(如让离线再生器写
  experiment_registry 记录)再接端点,不是纯接线。
**★★★ 自主开发批次(2026-06-29,用户出门前审批 A→B→C→D→E,自主执行)**:
- **A 合规文档**(`f3eb921`):`docs/COMPLIANCE_AND_PROCESS.md`(机构 SDLC/职责分离 + QUANTOS 对照 + 单人落地 + PR DoD 清单)。
- **B 诚实标注**(`6594510`):ReviewerWorkflow/AccessControl/ReleaseCommittee/IncidentResponse 补"蓝图/未实现(需 Phase 14 身份层)"徽标。
- **C Phase 3 数据填充 + 升真**:离线再生器写 experiment_registry(60 真不可变评估,`b30643b`);**Overview**(真 n_evaluated/active_runs,`b30643b`)、**Experiments**(读真 experiment_registry,真 IC/夏普/reproducibility_hash,`0252223`)、**EvidenceLedger**(真重算 hash 链 + 第一条接真实验,`94094ab`)、**Events**(早先 `0033f1a`,真 run_registry 事件)全升真。
- **D 安全原语上台**(`ba6c002`):`GET /safety-primitives`(REAL_SAFETY_PRIMITIVES)只读真 KillSwitch/23 盘前检查/SafetyConfig;Risk 页安全 widget。只读不下单,治理扫描器仍 PASS。
- **E 前端指挥中心**(`caabfe9`):`CommandCenter` 单屏座舱,5 磁贴可拖拽重排 + 点击缩放 + localStorage 布局持久化,全真数据只读;根路径默认落地。实测 live 通(5 拖拽磁贴、缩放改 span、无控制台报错)。HEAD=`caabfe9`。
**本会话累计升真 ~15 页**(6 PARTIAL + 4 NONE + Events/Overview/Experiments/EvidenceLedger + 安全原语 + 指挥中心)。门禁全程双路绿。
**关键资产**:`tools/crypto_research_offline.py` 离线再生器(不联网,从 crypto.db 重跑 session 写 factor_series,
18 币×120 期);`crypto_research_snapshot.json` 是 gitignored 真数据源(CI 缺→合成回退)。重生真数据:
`python -m tools.crypto_research_offline`(后台跑~2-3min)。
**后续**(Phase 2 高价值项已完成,剩低优先 BacktestResults 改 run_registry 动态)→
**Phase 3**(Experiments 接 experiment_registry / EvidenceLedger+Events 接 monitoring/audit_log 真 hash 链 /
Agents+LifecycleMonitor 接 services/{intelligence,alpha} 真运行态——这批缺的是"真持久层"非引擎,引擎都在)→
**Phase 4**(kill-switch/pre-trade 门上台 + 真缺子系统页 RBAC/模型注册表诚实标注)→
**Phase 5**(前端 Grafana 自由网格指挥中心 react-grid-layout,默认磁贴指向已升真页 + 导航重组)。

**★★★ Phase 3 续批升真(2026-06-29,用户"继续开发";5 并行规划员产出接线规格→串行 TDD 实现,每页门绿+提交)**:
- **F MonitoringDrift 部分升真**(`fff1f1f`):ALPHA_DECAY 基线/裁决跑真 `degradation_monitor`,provenance 条件化
  `PARTIAL_ALPHA_DECAY_REAL`;实时漂移侧 L2 无源仍合成,加琥珀色诚实横幅说明。不伪造 live 漂移。
- **G LifecycleMonitor 升真**(`eeb531a`):新 `GET /lifecycle-monitor` 读真 experiment_registry(60 alpha 真
  state/backtest_ic/sharpe/pvalue/deflated_sharpe/verdict);**诚实** live_ic=null、recommendation=NO_LIVE_DATA
  (L2 无实盘/纸面 IC,不伪造 DEGRADED)、days_in_state=null、真态均 TESTING(auto_approved=0)。
- **H DataCatalog 数据集升真**(`ae78130`):`_enumerate_real_datasets()` 枚举 `data/cache/crypto_daily.db`(42 标的)
  为真数据集(真 content_hash/row_count/coverage/相对新鲜度/PIT 安全,模块缓存);features/lineage 仍合成并诚实标注;
  provenance `REAL_PIT_DATASETS`。MonitoringDrift 复用 catalog→DATA_FRESHNESS 限 top-8 防刷屏。
- **I BacktestResults 动态化**(`5b33883`):新聚合 `GET /backtest-results` 按 kind+recency 取最新 done run
  (carry/combo/oi_research/survivorship+factory),真 metrics/series/verdict 绑 tile/柱;无真源派生量(carry 逐年、
  R²0.5%、2.09 头条)保留 OBSERVED;crowd 逐年柱改用真幸存者 per_year(诚实修正)。
- **诚实徽标修复**(`c0b9c38`):升真页拉真数据但 ProvenanceRibbon 默认 realData=false → 误标"合成演示"。
  ConsolePayloadBase 加可选 `data_provenance`;LifecycleMonitor/BacktestResults/DataCatalog 接
  `realData={usingBackend && data_provenance===<真标记>}`(Overview/Experiments/Risk 早已有)。**浏览器 HMR 实测**:
  三页均显示绿"Real research results",无误标,无控制台错误;回测战果页截图确认实时取自 run_registry。
- **Agents 诚实留 mock**(feasible_now=FALSE):无真 agent 运行持久层(`ResearchAgentLoop` 无状态、experiment_registry
  无 agent 列、bandit_state 盘空、budget_spent_cny 无源)→ 需先建 `agent_run_registry`(Phase 11)。**勿硬造**。
- 批次后门禁全绿:**1020 py 测试** + 治理扫描 PASS;前端 24 fe 测试 + typecheck;:8400 console_api 已重启服务新端点。
  提交均在本地 `master`(无 upstream push——本仓既有模式)。HEAD=`c0b9c38`。


HEAD 在研究操作台 Task 1-7(`run_registry`/`research_runner`/前端研究中心)附近;`main==master`(rtk 下 `git branch -f main master`);后端 `validate.py` **全绿**(含新治理测试 test_research_runner_boundary);前端 `npm test` **23 passed**(含 CompareRuns smoke)+ typecheck + build 绿;远端 kerstker/QUANTOS 已同步(PR #14 OPEN)。
**★★★ 研究操作台(Research Ops Console,2026-06-29,用户指令:前端要能操作系统、不在 IDE 跑、动态结果区不一回测一页、顶级机构标准)= 前端从「看板」升级为「操作台」**。brainstorm→spec(`docs/engineering/specs/2026-06-29-research-ops-console-design.md`)→plan(`docs/engineering/plans/2026-06-29-research-ops-console.md`)→7 任务 TDD 全绿。架构:① `services/research/run_registry/`(sqlite 单一事实源,线程安全;`index_existing` 把现有 `crypto_*.json` 结果灌入→历史第一天全可见);② 查看只读=console_api GET `/research-runs[?kind=]`+`/research-run?run_id=`(契约守,provenance RESEARCH_RUN_REGISTRY),前端「研究中心 /research/runs」列表 + **一个通用数据驱动查看器** RunDetail(metrics→KPI/series→图/verdict/caveats,任何 kind 零新代码);③ 操作=独立 `services/research/research_runner/`(端口 8420,**唯一可变面**)`POST /run{kind,params}` 校验白名单(7 个只研究 kind)→异步队列→写 registry,前端按钮触发 + 3s 轮询 queued→running→done。**安全机器强制**:`tests/governance/test_research_runner_boundary` AST 扫描——无券商 SDK、不 import services.trading、无下单/转账调用,白名单入口全 services/tools 研究函数、⊆ registry ALLOWED_KINDS;console_api 仍 GET-only;auto_approved 恒 0、到不了 live/flag/APPROVED;墙钟只在 runner HTTP 边界取。**端到端实测**:前端点「双因子组合」→ 真算 combo(SR 1.503)→ registry done → 列表 7→9。启动见 CLAUDE.md Commands(console_api:8400 + research_runner:8420 + `research-console-live`)。记忆 [[research-ops-console]]。
**★★★ 操作台四能力补完(2026-06-29,用户指令 "都做,最终都要做到的";顺序 观测→参数化→对比→历史,每个 TDD+门绿+实测)**:
① **可观测**(`7621e19`)实时日志流(`_LogWriter` 重定向 run stdout 逐行进 `registry.append_log`,RunDetail 2.5s 轮询 + 日志面板)+ 软取消(标 cancelled 丢结果,不强杀线程,VALID_STATUS 加 cancelled)+ git-sha(纯读 `.git/HEAD` 无子进程)。
② **参数化**(`d3cc516`)`factory_orthogonal` schema `{generations:int,population:int}`;`_tool_then_json` 改 `main(**params)`;前端 RUNNABLE_KINDS 带 `RunParamSpec[]` 渲染 typed number 输入 → `triggerRun(kind,params)`;`validate_params` typed/fail-closed 复校。
③ **对比**(`c86476e`)`/research/compare?ids=a,b,c`(可分享 URL)`fetchResearchRunsCompare` 并行拉 N 个(各自 snapshot 校验)→ 指标矩阵(指标键并集 × 每运行一列,缺失补—)+ per-run 序列图;不做"最优"高亮(指标方向不同,诚实);列表行加勾选 + "对比所选(N)"(≥2 启用)。
④ **历史留存**(`830a960`)`_run_summary` 增 `params`+`code_version`;列表加「参数」「git」两列(短 sha,hover 全量);历史索引运行无 sha 诚实显示 —,runner 新跑显真实 sha。登记表=「参数+结果+时间+git」可检索历史。
**四能力实测**:runner 触发新 combo_book → `code_version=c86476e`(=HEAD)、SR 1.503 → 列表 git 列显示 c86476e + 参数;对比矩阵 combo SR 1.503 vs carry net@5bps 2.030 并排;门绿(1014 py + 23 fe),无控制台报错。HEAD=`830a960`。
**前序锚点**:`1ccc0c3` 第二条 edge crowd_contra;`4b10905` 日内挖尽;`a0a25d3` 回测战果页。
**★★★ 第二条 edge 候选——逆向散户持仓 crowd_contra(2026-06-28,用户指令 "接第二种免费持仓数据做正交第二条 edge")**:
- **数据墙绕过**:实时 REST 持仓接口只给 30 天,但 **Binance Data Vision metrics 归档**有多年免费每日 OI+多空比 ZIP。抓取器 `tools/binance_oi_metrics_fetch.py`(PIT 安全/并发13×提速/过治理边界,`4fb9d84`+`b8fa063`);研究 `crypto_oi_research.py` + 8 项证伪 `crypto_oi_verify.py`(`1ccc0c3`)。14 流动主流币 × ~2199 日。
- **发现**:`crowd_contra = cs_rank(neg(global_ls))`("散户太多头时做空")——价 SR **1.18**、与 carry **R² 仅 0.53%(几近完全正交)**、增量 IR **+1.13**、逐年 22-26 全正、**晚一天进场仍 1.03**(非时序假象)、净收 +0.52bps/天资金费、成本/子集/embargo OOS 全过。项目至今最强信号。
- **4 路对抗验证 Workflow(PIT/统计/经济/成本,各跑真代码,ultracode)= 无 kill-shot,但裁决 WEAK_LEAD 非确认**:统计维度在「多重检验+去2022」叠加后未过 p<0.01;利润尾部集中;**幸存者偏差结构性不可证伪**(仅 14 个活到今天的币,空头腿从未在死币上测);近端净值软化;部分崩盘凸性溢价。**= 第一个真·carry-正交的第二条 edge 候选,可挂纸面跟踪,非已确认。** 容器化不变(auto_approved=0)。全文 `docs/research/2026-06-28-crowd-contra-second-edge.md`、记忆 [[crypto-research-findings]]。
- **同日跟进**:① **双因子组合**(`crypto_combo_book.py`,真 `optimize_population`):carry×crowd_contra×top_contra → **组合 econ SR 1.50、maxDD 0.12**、净@10bps 1.02、比最佳单因子 +0.23(`<combo commit>`)。② **幸存者完整重测**(`crypto_oi_survivorship.py`,验证报告的 #1 风险,Data Vision 不过滤死币→可测):抓 17 个死币(LUNA/FTT/…),`oi_value/oi` 价格代理,**survivors-only 1.228 → 含死币 0.933 raw,幸存者折扣 ~0.30**;逐年仍全正、winsorize 非支柱、8/17 死币正确做空但做多了 LUNA/FTT。**裁决 WEAKENS 但 SURVIVES**——非幻象(~0.9 正 Sharpe),但 1.2 头条带 ~0.3 水分,deployable ~0.9,是截面持仓边非崩盘对冲。OI 抓取器加 `find_end_ms`/bounded(收敛死币范围)。
- **剩余决定性测试**:前向纸面跟踪(近端软化=噪声 or 衰减);对组合也做幸存者重测。
**★★★ 自主多维正交发现——机器自己挖出新边(2026-06-28,`tools/crypto_factory_orthogonal.py`,用户愿景:机器找多维多向甚至未知 edge)**:
- cs_genome 本就字段无关 → 传 8 维 fields(价/量/资金费/oi/oi_value/top_ls/global_ls/taker_ls)+ 多维 panel,并把搜索奖励改成**对已知边{carry,crowd_contra}正交的增量 IR**(逼机器找新边而非重收敛 carry)。6 代 ×132 个跨维度因子,跨维交互天然可生成。
- **机器自主发现 `cs_rank(ts_argmin(global_ls,30))`**(散户持仓 30 天最低点距今多久——用 global_ls 的全新结构,人没写)**与 carry/crowd 完全正交(R²0.001)**,过快速 gauntlet:SR 0.86、换手 0.16/天、**滞后 0.86/0.78/0.73(扛1天)**、净@10bps 0.56、逐年 22-26 全正。= 第三个正交 lead。
- 同时吐出量能变化诱饵(in-sample 好看),诚实机器精准毙之(滞后 0.70→0.21→−0.12、净@10bps −0.10)。**机器找候选 + gauntlet 分真假,如设计。**
- argmin_global_ls30 是 LEAD 非确认(主折扣=132 次搜索选择偏差,待完整对抗验证)。容器化不变。
- **已硬化进引擎 + 放大(`b99f723`)**:`run_alpha_factory(basis_returns=...)` 成为永久 opt-in 能力(给定已知边对齐流→奖励切到对 basis 的正交增量 IR,默认行为不变,已测、门绿 ~982);加 `alpha_expression.deserialize`(serialize 精确逆,round-trip 测)。`crypto_factory_orthogonal.py` 重构为引擎薄编排器。放大跑(8代×64,192 因子):机器自主长出**量×价跨维度交互** `cs_rank(add(cs_scale(volume),ts_rank(close,14)))`(SR 1.38、R²|known 0.011 完全正交、过 lag+cost)+ 新持仓结构 `ts_delay(ts_rank(global_ls,14),30)`;无窥视 OOS mean 1.16,机器发现被选 4/6 折。诚实:192 次选择膨胀,顶部 lead 比 crowd_contra 更脆,是 lead 非确认。**用户愿景(机器自主多维/跨维找正交边 + gauntlet 分真假)已成引擎能力并放大验证。**
- **+basis 维度 + loop-until-dry(`<basis/loop commit>`)**:加第 9 维 basis=perp标记价(oi_value/oi)/现货−1(与资金费 carry 正交 R²0.021)。loop 每轮把过完整 gauntlet(正交+PIT+滞后+成本)的新发现并入 basis 直到连续 2 轮枯竭。实测:机器挖到 **1 个** 新正交边 `cs_rank(sign(cs_rank(top_ls)))`(top-trader 持仓 SR 0.57)后枯竭。**诚实结论:免费加密日频的稳健正交 alpha 空间薄=carry+散户持仓+1 个弱 top-trader 持仓边到顶**;放大跑那个量×价 SR 1.38 没过更严 loop gauntlet(更脆=选择产物)。量×价 4 路对抗验证 = **REFUTED**(全 kill-shot,真实数据复现):滞后 0→1 SR 1.41→0.66(成本前崩)、lag1 净@10bps −0.001、截面 IC 每 horizon/6-7 年皆负(=做多偏置趋势 tilt 非截面边);对照 crowd_contra(lag1 净 0.498)/carry(0.516)都扛住→gauntlet 校准对。综合:同 bar 时序假象,加固"薄空间"结论。全文 `docs/research/2026-06-28-autonomous-orthogonal-discovery.md`。**平台如设计运转:机器自主找跨维度候选 + 诚实机器有牙齿地毙假阳性(哪怕纸面 1.38)。** 当前真边(深压测后修正):**只剩 2 条稳健边——carry(~2.0)+ crowd_contra(幸存者后 ~0.9 WEAK_LEAD)**。top_ls 经深压测**降级**(survivors-only 0.69→含死币 0.40、逐年熊年为负 2021 −1.23/2022 −0.79、t 仅 2.01=脆弱近年 lead 非稳健边);volume×price 已 REFUTED;intraday 0 幸存。要更多真边瓶颈=数据(微结构/链上/跨资产),非搜索。
- **richer-data 试验 #1:日内持仓动态 = 也被挖尽(`crypto_factory_intraday.py`)**:metrics 本是 5 分钟,提取 6 个日内派生日频特征(oi_range/oi_trend/gls_trend/gls_range/taker_mean/oi_surge),15 维自主搜索 230 因子 → **0 幸存者**(日内因子纸面 SR 0.86–0.97 但 lag1 全 ≈0/负、net@10bps 深负=同 bar 假象)。**诚实:同种持仓数据 finer resolution 不是杠杆,eod crowd_contra 已吃干;真增量得是另一种数据(盘口微结构/链上/跨资产)。** 抓取器加 `--intraday`(纯 stdlib,治理过)。全文见 `docs/research/2026-06-28-autonomous-orthogonal-discovery.md`。
**★★★ 前端补全 + 监控接真数据(2026-06-28,用户选定方向 "前端补全 + 监控接真数据")**:
- **前端补全 5 页**(`dddeab9`+`1687e20`,后端能力本就就绪):Alpha 资产库(可筛 generator/状态/簇/DSR)、种群优化器(选集/权重条/边际 IR·DD/剔除原因)、信号→组合(美元中性目标 book + 容量 + 被拒信号,**只算 book 绝不下单**)、DSL Explorer(表达式静态检查/复杂度/PIT 泄漏机器证明,含一条读未来被拒例)、Alpha 相关图(SVG nodes/edges/簇/近重复)。均 mock-first/只读,注册 nav+route,smoke 测覆盖(19 测试)。
- **监控漂移接真引擎**(`e34275e`+`41cb3f1`):console_api `_monitoring_drift_payload` 的 ALPHA_DECAY 一类改由真 `services.alpha.degradation_monitor.monitor_alpha_health` 实算(多指标 IC/return/slippage/turnover/drawdown→OK/DEGRADED/PAUSED/RETIRED),映射成面板 status,remediation 透传引擎裁决原文(`[degradation_monitor:<rec>] …`)。**只 RECOMMEND 不驱动状态,live 授权恒 False**(containment 不变)。`drift_score` 接引擎严重度并钳进与 status 一致的阈值带(OK<warn≤WATCH<breach≤BREACH)→ 条/色/阈值/status chip 四者自洽;新增自洽性测试。
- **对抗复核(2 子代理)+ 修复**(`84ea717`+`66740a1`):监控接线无 P0/P1(带钳设计代数证明无法掩盖破口);前端 5 页一个真 P1=缺合成数据来源横幅 → 5 页统一补 `ProvenanceRibbon`(⚠ 合成演示数据·非真实研究结果或盈亏);清理死导入/中英重复/mock 回退字面量。
- **真后端端到端验证**:uvicorn :8400 + Vite :3100(launch.json `research-console-live`)实测:监控页吃真引擎数据(BACKEND_COMPUTED/已连接,破口卡片显示 `[degradation_monitor:PAUSED] slippage 2.2x backtest` 等引擎原文),新页显示合成数据横幅。截图为证;服务器用后已停。
**★★★ 量产 + 见钱(2026-06-27,观察值)**:① `carry_pnl.py` 把完整资金费收取 P&L 接进管线(`economic_returns_by_id`/`economic_sharpe`):真实 115 perp×2661d,资金费 carry **eco_SR 1.8–2.18**(复现既有 ~2.0),CPCV 经济 OOS **100% 折正、p05 也正**。② `alpha_factory.py::run_alpha_factory`(cs_genome 进化+经济评分+末代跑管线):真实数据上**机器自主繁殖,前 5 名精英全是资金费 carry 变体 eco_SR ~2.0**(`cs_rank(neg(funding))` 等),OOS 100% 折正。全程容器化守住、leakage 全证明 PIT-safe、裁决诚实 REQUIRES_HUMAN_REVIEW(不自动放行)。tools: `crypto_pipeline_run.py`、`crypto_factory_run.py`。对抗审查各 0-2 发现已修。
**★★★ 机构级 Alpha 因子工厂补全升级 = 后端全 7 Phase 完成(P0+1-7,审计 10 缺失全补齐),前端 Phase 8 起步收官(vitest 基建 + Lifecycle 页)。** 每 Phase tests-first+门禁绿+多智能体对抗审查+验收;新模块共 +148 后端测试。计划全文见 `docs/ALPHA_FACTORY_UPGRADE_PLAN.md`。
**★★★ 机构级 Alpha 因子工厂补全升级 进行中(2026-06-27,用户大指令:把"因子发现器"升级为"Alpha 资产生命周期系统")。**
计划全文 `docs/ALPHA_FACTORY_UPGRADE_PLAN.md`(Phase 0 审计:67 能力→22 有/35 部分/10 缺;按规则 #9 适配现有架构:元组 DSL 不重写成类、纯 stdlib 不上 numpy、7态+研究态不碰 live)。**已完成**:
- **P0 安全地基**(`f682325`):①`services/research/expression_leakage.py` 截面引擎表达式级 leakage **机器证明**(扰动未来行→重算→断言 as-of 截面不变;compute_leakage_ok→True/False/None);②embargo 绑 label horizon(`session._inner_loop` 把 fwd_horizon→wf_embargo=h-1,h=1 时字节不变)。
- **Phase 1 算子+检查器+复杂度**(`a4d9453`+`40dce60`):新算子 `cs_winsorize`/**`cs_residualize`(中性化原语——对任意已算信号截面正交,无需行业数据)**/`ts_cov`/`ts_beta`/`sqrt`(接 `_CS`/`_CS2`/`_EW1`/`_TS_PAIR` + evaluate 分发);`dsl_checker.py`(Type+Dimension 静态检查,签名表从引擎注册表自动派生,可选 `fields=` 字段存在性);`complexity.py`(算子加权复杂度+node_count+depth,fail-closed)。
- **Phase 1 对抗审查修复**(`6198385`):14 智能体多维审查(9 确认/5 为正面确认),修 4 真问题(cs_genome._node_ok 改 `_TS_PAIR`、evaluate 拒 bool、checker 可选字段存在、complexity 未知算子 raise)。
- **决定推迟**:Phase1-1d(evaluate 节点级 CSE memo)——改热路径有别名风险且 CachingPITStore 已解主瓶颈,等 profiling 证明再做。
- **Phase 2 科学验证门**(`4a65c70`+`2ff0ba5`+`ff8ba01`):①`validation/cpcv.py` 组合 purged CV→OOS Sharpe 分布(替代单个 PBO 标量,暴露选择性过拟合;非空分布永远汇总、矩为 NaN 也不返 None);②`multiple_testing.py` 补 bonferroni/holm_bonferroni/benjamini_hochberg_yekutieli(任意相关 FDR)/bonferroni_tstat_threshold(t 阈 haircut);③`session._cpcv_validate` 把 CPCV 接进三模式输出("cpcv" 键);④`pipeline.run_research_validation` 的 leakage_ok 改【必传】(堵唯一非 fail-closed 漏洞)。对抗审查 4 智能体仅 1 发现 1 修复。
- **Phase 3 富 Alpha Asset 体系**(`b33771b`+`59f67d2`+`d347e32`):一次补掉审计 10 缺失里的 6 个。
  ① `quantos/contracts/alpha_asset.py`:AlphaAsset(~51 字段 frozen,组合现有 Definition+Evaluation)+ AlphaLineage;asset_hash=全 model_dump 哈希;from_definition 拒伪造 status(容器化)。
  ② `services/alpha/registry.py`:版本不可变注册表 + 血缘查询(children_of 扫所有版本)+ 审计;**add 拒绝无 lifecycle_event_ids 的 APPROVED(容器化红线 fail-closed)**。
  ③ `similarity.py`(AST 子树 Jaccard/收益 |corr|/holdings 余弦/exposure 余弦)、`alpha_graph.py`(显式 nodes/edges/clusters/near-dup,复用 AlphaCluster)、`zoo.py`(name/expr/universe/generator/status/cluster/tag/指标 检索)、`lineage.py`(make_child_asset perf_delta=子−最优父)。
  对抗审查 9 智能体抓出并修了 2 条 P0 容器化(registry/from_definition 能造 APPROVED 绕过)+ 3 条 P1/P2。
- **Phase 4 种群优化器**(`0418cfa`+`a7a6ea0`):`services/alpha/population_optimizer.py`——集合级联合优化。combine/IR/max_drawdown/三种权重(equal/inverse_vol/ir)/边际贡献(含 drawdown,drop-one);选择=greedy 增量 IR→alpha_graph 在 rho_cut 去近重复→max_k,method='evolutionary'=确定性局部搜索(只接受提升,≥greedy);目标=post-cost IR(钳[−10,10])+多样性−复杂度−拥挤;输出 Alpha Portfolio(selected/weights/excluded+reason/portfolio_ir/post_cost_ir/max_drawdown/diversity/objective)。对抗审查 6 智能体 4 发现全确认全修(marginal_ir inf−inf=NaN→0、目标钳制、DROPPED_BY_EVOLUTIONARY、tie-break)。成本/容量为代理(真 backtest 归 Phase5)。
- **Phase 5 Signal→Portfolio Adapter**(`435305e`+`607012e`):`services/portfolio/signal_adapter.py`——单期截面信号→可下单目标 book。清洗(missing/stale/INVALID_TYPE)→winsorize(k-MAD)→中性化(对 size 截面 OLS,fail-closed:缺因子名 MISSING_FACTOR 拒绝)→z-score→_project_constraints 迭代投影到 {net=0}∩{gross=target}∩{|w|<=cap}→成本(CostModel+单独 spread)→容量(participation vs ADV,缺 ADV=UNKNOWN)→报告。退化(单名/常数)=显式空 book。**研究/dry-run only,绝不下单**(安全审查零发现确认)。对抗审查 7 智能体 5 发现全确认全修。
**至此「信号→可交易组合」研究全链路打通**:因子 DSL(P1)→验证门(P2)→Alpha Asset/Zoo/图/血缘(P3)→种群优化器(P4)→Signal→Portfolio(P5)。只差人工 go-live 红线(不碰)。
- **Phase 6 生命周期监控**(`d9e475e`+`9fcd8e4`):①AlphaState 加 SHADOW/DEGRADED/PAUSED(dry-run 监控态)+ 转移表 + AGENT_REACHABLE(含三态,**仍排除 APPROVED**)+ lifecycle._run_guard 三态 permitted;**容器化红线守住**(对抗审查穷举:非人类经任何新路径到不了 APPROVED,0 发现);**刻意不建 small_live/production/live=L3**。②`degradation_monitor.py`:live-vs-backtest IC/return/slippage/turnover/drawdown/capacity 比对→OK/DEGRADED/PAUSED/RETIRED 建议(优先 RETIRED>PAUSED>DEGRADED);**只建议不驱动**,fail-closed flag 缺数据。
- **Phase 7 Research Memory + 自动 Brief**(`e7816ff`+`1667d77`):`research_memory.py`(ResearchNote: hypothesis/code_version(git sha,caller 传)/dataset_version/generator/verdict/reasons/sharpe/cluster/lineage/regime/data_deps/next_action + from_asset 工厂 + ResearchMemory in-memory latest-wins)+ `research_brief.py`(compose_brief 确定性合成:worked/failed/warned/needs_review 四分区完整、failure_reasons 直方图、top_performers、redundant_clusters、next_actions 启发式+引号归一去重、mutation_suggestions)。**无 LLM 也能跑**;LLM 门外提议=更多 notes 过同门禁。对抗审查 3 智能体 2 发现全修(WARN 分区丢失、引号去重)。无权限不驱动状态。
**🎉 审计 10 缺失全部补齐(Phase 2-7)**:CPCV、富 Alpha Asset、Alpha 血缘、AST 相似度、holdings/exposure 相似度、显式相关图、Zoo 检索、种群优化器、生命周期监控、Research Memory+Brief。
- **Phase 8 前端 console 收官(起步)**(`4e3db73`+`e453c97`):①**vitest 组件测试基建**——vite.config 加 test 块(jsdom)+ src/test/setup.ts + format.test.ts + pages.smoke.test.tsx(闭合审计"前端 UI 测试近零":既有 0 真组件测试→现 14 passed)。②**Lifecycle Monitor 新页**(展示 Phase6 状态 + 衰减建议,mock-first,只读,容器化说明带;注册 nav+route;浏览器实测渲染正常截图为证)。
**前端剩余页(Phase 8 续)= 已补全(2026-06-28,`dddeab9`+`1687e20`)**:Alpha Zoo 可筛 / Population Optimizer / Signal→Portfolio / DSL Explorer / Alpha Graph(DAG)五页全建,均 mock-first/只读/带 ProvenanceRibbon,smoke 测覆盖。剩 Validation Gate 详情可按同模式再补(非阻塞)。
- **主线接线管线**(`ba75774`):`services/research/alpha_pipeline.py::run_alpha_asset_pipeline`——把 P1-7 串成端到端:cs 表达式→**计算 leakage_ok**(P0,真计算非 None)→评估(P2 catalog DSR)→富 AlphaAsset+注册表(P3)→Research Memory(P7)→种群优化器(P4)→Signal→Portfolio 美元中性目标 book(P5)→自动 Brief(P7)。**不驱动 lifecycle(无 APPROVED 接口),容器安全**(对抗审查 0 发现)。端到端实测:leakage 全 COMPUTED、裁决诚实 REQUIRES_HUMAN_REVIEW(不放行合成假象)、book gross1/net0、Brief 诚实。
**真正尚未做的**:① 把这条管线接进 cs_genome 进化/MCTS 搜索循环(用 make_child_asset 写血缘、跑大规模量产);② ~~前端剩余 5 页~~ **已完成(2026-06-28)**;③ 真实数据上跑这条管线(price+volume+funding,验证能否量产 ≥carry 的稳健 alpha)。这些是"用起来/铺开",核心能力已全造好+接通。下一个未拓的边:接第二种免费持仓数据(open interest/多空比)做与价/资金费正交的第二条 edge,或把 carry 挂纸面跟踪看衰减。
**尚未接线(归到"cs_genome 接进容器化搜索"增量)**:搜索循环用 make_child_asset 写血缘 + 用 expression_leakage 算 leakage_ok 喂 evaluator + 把 AlphaAsset/registry/zoo 接进主线产出。
**★★★ 顶级化文档已补齐(2026-06-27,ultracode 多智能体工作流)**:`README.md` + `docs/{ARCHITECTURE,ROADMAP,RESEARCH_FINDINGS,DATA_SOURCES,GITHUB_SETUP}.md`,每条声明对真实仓库核查过(评审顺手发现 `CLAUDE.md` 的"~10%/空脚手架"已过时——实清 139 个 services .py、741 测试函数,待与用户一起校 CLAUDE.md)。
**★★ ALPHA 引擎开发中(从"一个"到"量产+动态",用户愿景)**:增量1 已成 = `services/research/cs_genome.py`(对 alpha_expression 截面树做确定性 mutate/crossover/seed,产物保证合法/根为 cs/深度≤6/PIT 安全/可评估/含 funding 叶子+二元交互;6 测试钉死,无 RNG/墙钟)。**待做**:增量2 = 把 cs_genome 接进一个进化/MCTS 搜索循环,经未改动的 `ResearchAgentLoop` 容器化评估(auto_approved 恒 0),DSR 给搜索去偏、无窥视选择 OOS 当裁判、去冗余协同;增量3 = 在 price+volume+funding 数据(crypto_funding.db + 资金费缓存)上跑,看机器能否量产出 ≥carry 的稳健 cs-alpha。参考现成:`tools/crypto_alpha_search.py`(固定网格版,可升级为生成式)。
**★ 资金费 carry = 第一个过关的真信号(2026-06-27,实测,`tools/binance_funding_fetch.py` + `crypto_funding_research.py`
+ `crypto_funding_verify.py`):** 引入**资金费率**(perp carry=杠杆/情绪,和价量正交;Binance 公开 fapi,只读,边界钉死)。
`fund_avg7`=cs_rank(neg(ts_mean(funding,7))),115 永续×2661 天:净 SR 1.18@3bps、换手仅 0.26;**单因子无窥视 OOS
mean +0.40、3/5 折正**(池子那个 −0.24 是被烂因子拖累);成本盈亏平衡 ~30bps(现实 ~10bps 下净 ~0.9);6/7 年正含 2022;
反向控 fund_lvl_rank 最差(方向对)。**是第一个同时过 OOS+成本+跨 regime 的因子。但仍是 lead 非已证实可规模化的 alpha**:
OOS 噪声大(2 折负、被 +2.57 一折拉高)、**近年衰减**(2020-21 大、2023-26 弱,carry 拥挤)、IC≈0(尾部)、幸存者偏差。
容器化 auto_approved=0、REQUIRES_HUMAN_REVIEW。**这是第一个正当的纸面跟踪候选**(区别于已证伪的 dist_from_high_14)。
下一步坐实/规模化:抗幸存者偏差的永续历史、真实 funding+fee 损益、并入其它免费持仓数据(open interest/多空比)、纸面跟踪看衰减。
**综合搜索印证(`tools/crypto_alpha_search.py`,261 个 cs-alpha):** raw 前 15 名 **15/15 全是资金费 carry 变体**
(decay/mean 各窗口,价/量一个没进),DSR ~0.98-1.0、6-7/7 年正;最稳 `rank_neg_decay14_funding` **7/7 年正**。
**但对全 261 的选择 OOS 是负的(−0.45)** —— 盲目"best-of-N"过拟合,机器诚实地拦了。结论:carry 真实且跨~10 种平滑稳健,
但要靠**经济先验 + 稳健平滑**用,**不能靠盲搜**(选择 OOS 已证盲搜过拟合)。这是"先验+诚实机器 > 蛮力"的教科书案例。
资金费历史已缓存 `data/cache/crypto_funding_rates.json`；2026-07-13 真实重刷时 Binance funding 公共端点
115/115 返回 `TRANSIENT` 超时，正式准入 manifest 因 24/24 前向 ticker 尾部不完整而 BLOCK；恢复外部源并
由同一 manifest 通过前不可新增前向证据。免费 OI/多空比仅 30 天 → 更多多年 alt-data 要付费/链上。
**★★ 真实损益 = carry 回测里真赚钱(`tools/crypto_carry_strategy.py`):** 之前只算价格;perp carry 还**实收资金费**
(多空两腿都收)。净 = 价格 + 收到的资金费 − 手续费,去冗余 carry 组合,115 永续×2186 持仓日:只价 1.39 → **+资金费 2.25
→ 净@5bps 2.03 → @20bps 1.36**;日均收资金费 **5.43bps**、换手 0.28;**7/7 年全正含 2022**(净@5bps:20+4.4/21+2.5/22+1.2/
23+1.1/24+1.9/25+2.0/26+2.3)。之前"衰减"是没算资金费的假象:价格预测力衰减、但**资金费 carry 稳**,所以收益越来越是
**carry 收益(yield)**而非价格 alpha。**诚实折扣(回测没建模,故 2.0 偏乐观):**幸存者偏差(退市极端资金费币缺失,偏高)、
小盘滑点(5bps 乐观,20bps→1.4)、carry 尾部风险(暴动/爆仓会崩,未建模)、容量有限。**=第一个有经济根基、回测真赚钱的效应,
但可部署夏普远低于 2.0、是拥挤的 yield 不是魔法 alpha。** paper_track_start 已记。下一步:1 纸面跟踪、3 真实滑点/爆仓压力、
2 最低成本 alt-data(见下分析)。
—— 以下为前序 null 记录 ——
**宽截面实测 = 最干净的 null(2026-06-27,实跑实读,无捏造):** 接入 **Binance 公开 K 线**(免费/无 key/只读 DATA,
治理边界钉死 4 抓取器;`tools/binance_klines_fetch.py`)→ 437 个 USDT 对,过滤后 **336 币 × 2309 日线、2020–2026 含 2022 熊市**
(比之前宽一个数量级)。跑 15 个截面 alpha:**最好全样本 SR 0.15(≈0)、|IC|<0.02、无窥视 OOS 均值 −0.67(1/5 fold 正)、
幸存者 0、auto_approved=0**。每个因子的正收益几乎全来自 2020(早期狂野数据),之后逐年转负。**更多宽度(最显眼的"数据
护城河"杠杆)并没有挖出 alpha** —— 加密价+量日线即便在 336 个小盘上、6 年里也被高效定价。诚实保留(明确不是 lead):
几个反转类因子(meanrev/low_vol/pv_corr)近 5 年为正但全样本仍负 + OOS 负,正是"子段幻觉",不信。幸存者偏差(只含活到
今天的对)偏上,即便如此也没东西活。**钉死的战略读数:缺的不是更多价+量宽度(已测到 336 币),是另一种数据(链上/资金费/
情绪/盘口微结构)——或承认这一段被高效定价。** 数据湖现有干净免费源:Binance 公开 K 线(data-api.binance.vision)。
⚠️ 历史教训:此前曾把**捏造的**小盘"结论"(argmax_cs20 等)写进文档+提交+推送,被 task 通知戳穿后已全数订正撤回——
**没亲眼在工具输出里看到的数字,绝不写。**
—— 历史锚点 ——
HEAD 曾在 `e0f7c57`(截面 alpha 引擎 Batch 1);`validate.py` 813 测试全绿。**本轮(2026-06-26→27)新增**:机构级开发流程 + 远端就绪(见下)、
因子工厂前端常驻页(流水线+成本曲线+regime,契约两侧守护)、CryptoCompare 多年数据接入、**出 regime 验证证伪
日线趋势 lead**(2022 熊市:dist_from_high_14 全样本 net −0.55、IC≈0、逐年乱翻)、**内存读缓存提速**(回测小时→秒、
逐位一致)、**截面 alpha 引擎(扩因子语法,解锁 Alpha101)+ 实测确认"更多因子也无跨 regime 信号"→ 瓶颈是数据**。
当前结论:**没有任何经得起跨 regime 的加密因子;真杠杆=数据护城河(口子,见 §5)**。下一步候选:开数据口子(小盘币广度/
另类/其它资产)或挂纸面跟踪。远端 = `https://github.com/kerstker/QUANTOS`(Private);本地两服务可能仍开着(uvicorn :8400 +
Vite :3100)。
—— 最新一轮(2026-06-30,补「脑」第一刀:学习型信号组合 ML stacker)——
对标差距报告头号杠杆(零 ML/DL→CRITICAL)。brainstorm→spec→plan→实现(工作流在 Task4 验证员漏发结构化输出处
中断,Task0-4 已提交、Task5 收尾内联补完)。新 `services/research/ml/{feature_panel,stacker,walk_forward_stack}.py`
= 确定性学习型信号组合(GBM+Ridge);依赖放宽引 numpy+sklearn 但**确定性使用**(线程在 import 前钉死、
`random_state=0`、Ridge cholesky、同入逐位相同测试);`services/` 无网络 import 仍机器强制(ML 库非网络)。训练
purged+embargo、裁决 no-peek OOS,关卡不弱化。驱动器 `tools/ml_stack_research.py` 真数据三方 OOS。**诚实结论:
学习型组合无可交易 edge**(净额 @5bps、apples-to-apples、FX 限可交易 18 币,commit bf73a83):crypto GBM −0.78 /
Ridge −0.24 / 单因子 +1.78(组合两路净负、稀释那条好因子);fx 可交易净:GBM −1.12 / Ridge −0.40 / 单因子 −2.04
(全负)。早先 fx 毛收益+全 46 币的 GBM +0.21 是**海市蜃楼**(毛收益伪装 + pegged/legacy 污染),净额化+限可交易后
翻成 −1.12——印证诚实修正的必要。**瓶颈是数据/信号类别,不是建模层**(同薄数据上加更花的模型只会更快过拟合)。门绿(~1050 py)。
诚实记忆 [[ml-stacker-findings]];差距报告 `docs/QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md`。

—— 上一轮(2026-06-29,断点1 关卡证据回喂——晋级链解冻)——
HEAD 在 `2507a67`。四道断点全收口(断点2/3/4 + 现在断点1)。**断点1 方案A**:leakage 证据的"真源"
原先缺失(没人给 `loop.run` 传 evidence → `leakage_ok` 恒 None=NOT_ASSESSED=fail-closed,晋级链冻在
leakage 门)。本轮在"有原始 bars+计算函数"的搜索/驱动层算真 `detect_leakage`:`services/research/
leakage_evidence.py`(signal/volume/任意 closes→标量 三种 ComputeFn);三通用搜索环(guided/evolutionary/
mcts)经 `services/intelligence/evidence.py::provider_leakage_evidence` 收集 provider 供证并传 `evidence=`;
provider 挂 `leakage_ok(defn)`:price/volume/combined-price/**GP 表达式树**全真验证,interaction/fundamental
诚实 None(独立 PIT 库/截面合成,bar 级检测器评不了,不伪造)。`crypto_history_research` 驱动器亦接齐
(regime 早开+PBO loop 自算→证据齐全)。容器化不变:证据永不授予晋级(auto_approved=0)。门绿(1047+ py)。
**价值边界**:门"修真"(leakage 不再因 None 形同虚设),不让垃圾因子晋级——加密因子照样过不了 OOS。
剩余(可选):其余单候选 crypto 驱动器的同款 1 行接线。

—— 上一轮(2026-06-29,断点2 前向 paper 追踪持久层)——
HEAD 曾在 `0eca544`。经 ultracode 工作流串行实现 6 任务(store→advance→驱动器→三消费方,每任务 实现+独立验证)+
收尾门绿(governance + **1047 py** + 前端 24)。**实测发现并修了工作流单测漏掉的真 bug**:LifecycleMonitor 把
backtest 夏普当 backtest "return" 去和累积收益比(单位错配)→ 伪 DEGRADED;改 apples-to-apples 夏普对比 +
诚实 `PAPER_RETURN_ONLY`(12 alpha 被追踪、live_tracked=0 因无 IC、DEGRADED=0)。浏览器实测确认(真后端 :8400)。
**诚实第一**:纸面夏普 +4.8..+6.4 是 ~24 期尾巴的噪声,非已证实 edge——和"无跨 regime edge"结论一致。
容器化不变(auto_approved=0、非人不晋级、无库 fail-closed)。本地提交,未推远端。

**本轮(2026-06-30)新增 —— 端到端干跑流水线**:`services/execution/{dry_run_pipeline,dry_run_store}` +
驱动器 `tools/dry_run_carry.py`(见 §3「执行/干跑」)。把预交易门从"已测死代码"接成"运行时可证强制的
chokepoint":权重→意图→**逐意图过门(fail-closed,kill-switch latched 全拒)**→干跑适配器纸面成交(绝不真发)
→平均成本对账(残差 0.0)→哈希链审计→可复现 `DryRunRun`。spec/plan:`docs/engineering/{specs,plans}/
2026-06-30-dry-run-pipeline*`。11 新测试 + 全门绿(1093 passed)。这是用户"系统要能用、最终通往实盘"
战略判断的落地;实盘永远 = 同接口换真券商适配器 + 人工批准(非目标)。

—— 历史锚点 ——
HEAD 曾在 `3bb6002`(机构级开发流程落地)附近;`main==master`;`validate.py` 全绿。
**远端已就绪**:`https://github.com/kerstker/QUANTOS`(Private),`origin/main` 与 `origin/master` 已同步;
CI(`.github/workflows/validate.yml`)在 push/PR 自动跑后端门禁 + 前端。搭建/分支保护手册见 `docs/REMOTE_SETUP.md`。
待用户手动开启:`main` 分支保护(要求 CI 两个检查 + 1 人评审);本地钩子 `make hooks`(可选)。
本轮主要新增:机构级开发流程(CI 硬化 / Dependabot / CONTRIBUTING / SECURITY / CODEOWNERS / PR·Issue 模板 /
Makefile / 本地 git 钩子 / 远端手册)+ 已建私有远端并推送。
前序新增:Crypto Live 前端 + 宽截面 + 无窥视 OOS + GP 树(+对抗评审硬化)+ 去冗余(协同集/增量奖励)+
Purged-Embargoed CV + 三重门/元标签 + Regime(+接评估器)+ 互信息 + 唯一性/择时/分数阶 +
数据适配器 + 真实成交量 + 量能因子 + MCTS(+接主线)+ 频繁子树规避 + 工厂自验证闭环。
> **2026-07-21 PIT feature-level drift diagnostic (L2 read-only increment):**
> `services/data/distribution_drift.py` 复用既有 trailing-window KS 内核，新增
> `evaluate_feature_distribution_drift` 对调用方提供的 PIT feature panel 做声明宇宙聚合。
> 缺失/多余 feature、无效值和历史不足均 fail-closed；报告保留逐 feature 证据，明确
> `PIT_FEATURE_TRAILING_KS_DRIFT_V1` / `UNCALIBRATED_PRINCIPLED`。它不读取数据库、不持久化告警、
> 不改仓位/晋级/交易路径，故机器顶级判定仍诚实为 **15/22、NOT_TOP_TIER**；后续仍需填充真实
> feature-value cache 并提供独立调度运营证据。

> **2026-07-30 local console stack is fully headless:**
> After scheduled workers moved to `pythonw.exe + S4U`, a process-tree audit
> still found `conhost.exe` attached to the local 8400/8420 APIs and the 3105
> Vite process. `tools/local_console_stack.py` now fixes both APIs to
> `pythonw.exe`, while `tools/local_frontend_server.py` serves the built React
> SPA through `pythonw.exe`; resident operation no longer needs Node/npm/cmd.
> Windows children also carry `CREATE_NO_WINDOW` plus
> `STARTF_USESHOWWINDOW/SW_HIDE`. The tracked `.env.localhost` and
> `npm run build:localhost` bind the bundle to the real 8400/8420 local APIs;
> the static server rejects a stale mock bundle instead of silently serving it.
> A real restart verified all three listeners as `pythonw.exe` with no
> `conhost.exe` children, and Data Catalog showed `REAL_API / CONNECTED` with
> Binance 14/14 LIVE/PASS.
> **2026-07-31 cross-host container source-input binding hardening:**
> GitHub Actions run `30584226012` completed cold OCI builds on independent
> `ubuntu-22.04` / `ubuntu-24.04` hosted runners, byte-identical complete image
> graph verification, and GitHub OIDC + Fulcio/SCT/Rekor signing. Full repository
> CI run `30584230979` also passed. A subsequent evidence audit found that the v1
> claim bound the recipe, toolchain and final image but did not let the local
> verifier explicitly bind the current `services/` / `quantos/` bytes to that
> external build. Schema v2 now binds a deterministic complete container-input
> content root through each claim, aggregate proof, signed receipt and offline
> verifier. Any source change makes prior proof fail closed. The blocker remains
> until the published v2 code completes a fresh two-runner run and the new signed
> artifacts are retained. L2 and live-trading gates are unchanged.
>
> **2026-07-31 signed cross-host deployable-container evidence closed:**
> GitHub Actions run `30599904036` completed cold OCI builds on independently
> provisioned `ubuntu-22.04` and `ubuntu-24.04` runners. The complete archives,
> manifests, configs, layers and diff IDs were byte-identical. Schema v2 also
> bound the exact Docker-context input root (`services/`, `quantos/`, recipe and
> dependency files) and rejected runner-local files excluded by `.dockerignore`.
> The aggregate was signed through GitHub ambient OIDC and independently verified
> against Fulcio identity, SCT and Rekor v1 inclusion. The signed bundle, receipt
> and aggregate are retained under
> `evidence/external_sigstore/2026-07-31-container/`; offline verification is
> load-bearing in the supply-chain generator and top-tier checker. The
> `CROSS_HOST_CONTAINER_REPRODUCIBILITY_NOT_PROVEN` blocker is closed. Independent
> admission deployment, external receipt authority, WORM/object-lock retention
> and external history seals remain open. L2 and live-trading gates are unchanged.
>
# 2026-08-05 operational-window cohort correction

The unattended Paper operational evidence window now evaluates the active
release cohort separately from retained predecessor cycles.  A release change
starts the current SLO cohort; predecessor failures remain append-only and are
reported as retained history rather than silently discarded.  The console
exposes the active release prefix, retained-cycle count, and excluded
predecessor count.  This keeps the current release's success ratio honest while
preserving restart and historical failure evidence.  The change is paper-only;
promotion, live deployment, broker connectivity, and order submission remain
fail-closed.

# 2026-08-10 L2 internal strong-identity design slice

With no existing OIDC service available, the internal identity topology is now
specified as Keycloak OIDC plus a private client-certificate CA and an Envoy
mTLS ingress. A strict, secret-free deployment contract rejects public
exposure, mutable image tags, grant downgrades, missing certificate-bound
tokens, role widening, TCP application ingress, and every runtime-authority
escalation. The Promotion API has an explicit `ENVOY_UDS_V1` mode whose trusted
adapter accepts exactly one Envoy `SANITIZE_SET` certificate envelope over a
Unix-domain-socket scope, validates the certificate/hash, strips forwarding
headers, and constructs the existing verified ASGI TLS extension. TCP spoofing,
duplicate or multi-hop headers, malformed certificates, and caller-fabricated
TLS extensions fail closed.

The new contract and transport boundary are bound into the deterministic
strong-identity evidence. This is design and local implementation evidence,
not proof of an operating identity authority: Keycloak, the CA, Envoy, distinct
human enrollment, key rotation, active certificate revocation, recovery, and
retained operational drills have not been deployed. Therefore
`GOV.IDENTITY.STRONG_AUTH` remains M3/PARTIAL, the aggregate remains
`NOT_TOP_TIER` at 15/22, and all L2/live/order/credential safety boundaries are
unchanged. Design:
[`engineering/specs/2026-08-10-l2-internal-identity-design.md`](engineering/specs/2026-08-10-l2-internal-identity-design.md).

# 2026-08-11 DefiLlama TVL forward-vintage accumulation

The independent DefiLlama chain-TVL channel now preserves one honestly
knowable, sufficiently broad completed-day cross-section per public fetch in
`data/cache/defillama_tvl_vintages.db`.  It stores the completed UTC day as
`event_time` and the actual observation time as `knowledge_time`; later
observations remain bitemporal restatements and the retrospective history is
never backfilled as if it had been known earlier.  Atomic batch writes,
idempotency, minimum breadth, source-contract checks, and early formal-gate
blocking are tested.

A real keyless/public smoke run retained 19 assets for the 2026-08-09 UTC
completed day.  The existing formal evaluator now stops on the accurate
`not PIT-admissible for formal trial registration` reason before loading other
price inputs or registering trials.  The historical JSON remains diagnostic;
this new forward store starts with one day and is not mature Alpha evidence.

The immutable L2 schedule contract includes a daily 09:30 local-time,
ten-minute-bounded `defillama_tvl_forward_refresh` worker.  Registration still
requires a clean, verified release; this development checkout does not bypass
that gate.  The irreplaceable forward store is optional in encrypted backup
source contract v6 so a new installation can exist before its first fetch, but
an existing store is included in restore verification.

Formal Alpha eligibility therefore remains **zero**.  No Testnet intent,
execution service, seven-day soak, credential access, broker connection, or
order submission was started.

# 2026-08-11 independent public-data Alpha expansion

Three public, keyless and mutually different data axes were acquired and
tested under fixed, pre-result search contracts.  None produced an admissible
Alpha, and the negative results are retained rather than direction/window
tuned after inspection.

- **Hyperliquid third-venue perpetuals:** 14/14 forward-universe assets have
  365 complete UTC funding/premium days (exactly 24 hourly settlements/day),
  plus current public mark/oracle/OI/notional context and three-venue predicted
  funding.  Six fixed Binance/Bybit/Hyperliquid variants produced zero
  candidates.  The strongest weak control was venue-spread continuation
  (non-overlapping h7 t=1.99, dry-run VT Sharpe=0.58), below every admission
  bar.  A discovered calendar-anchor defect in batch PBO was fixed: all family
  members now share one Monday-UTC grid, future-prefix leakage evidence is
  computed, and the formal evaluator retains PBO=0.657.  All six now return
  explicit FAIL (DSR/PBO/OOS/walk-forward/regime reasons as applicable), not an
  error or unassessed pass.
- **CFTC institutional positioning:** the official TFF Futures-Only feed
  yielded 434 CME BTC and 278 CME cash-settled ETH weekly observations after a
  conservative report-date + 7-day knowledge delay.  Six fixed leveraged
  money/asset-manager/relative-positioning variants produced zero diagnostic
  leads; the best was BTC leveraged-money reversal (t=1.10, net Sharpe=0.40).
  All six trials are in the durable global experiment population.  The formal
  evaluator sees the honest two-leg breadth and blocks on THIN_COVERAGE; no
  synthetic instruments were added to evade the gate.
- **Global stablecoin liquidity:** DefiLlama returned 3,176 completed daily
  global USD stablecoin supply observations.  Because the provider exposes
  retrospective revisions without historical vintages, six fixed liquidity
  impulse variants were association diagnostics only and were not admitted to
  the formal candidate registry.  All six failed; the strongest 90-day supply
  growth association had t=1.01 and net Sharpe=0.37.  The first real bitemporal
  forward supply vintage was retained separately so honest evidence can begin
  accumulating from now onward.

The immutable L2 scheduling contract now contains bounded refresh workers for
daily Hyperliquid incremental history, weekly CFTC TFF history, and daily
stablecoin forward vintages.  The Hyperliquid merge was exercised against the
real 365-day snapshot (14/14 assets, zero conflicts).  Schedule registration
still requires a clean verified release and was not bypassed from this checkout.
The irreplaceable stablecoin vintage database is optional in encrypted backup
source contract v7 and participates in restore verification once present.

Formal Alpha eligibility remains **zero**.  No Testnet intent, execution
service, seven-day soak, credential access, account query, broker connection or
order submission was started.

## 2026-08-11 — repository governance and operator experience

The repository shell was consolidated without changing trading behavior or
research results.  The GitHub landing page, contribution/security guidance,
deployment guide, local research-stack runbook, issue/PR templates, CODEOWNERS,
and documentation index now describe the same three-layer operating reality:
L2 research/dry-run is the default, Testnet exists behind explicit evidence and
human gates, and Mainnet remains structurally blocked.

Sixty distributed `PLANNED.md` placeholders were replaced by the central
scaffold register in `docs/PLANNED_SCAFFOLDS.md`; obsolete completion snapshots
and an accidental root text fragment were removed.  Historical decisions remain
available through Git history.  Repository hygiene is now machine-checked in CI
and the pre-commit hook, while the new workspace cleaner is dry-run by default
and explicitly excludes `key/`, authoritative `data/`, `.git/`, and dependency
directories.  The cleanup does not alter Alpha admission: formal eligibility
remains **zero**, so no Testnet execution or soak was started by this work.

## 2026-08-25 crypto idiosyncratic-volatility falsification on DGX

The peer-reviewed Zhang-Li crypto IVOL prior was translated into one frozen,
costed cross-sectional experiment instead of a parameter search. A DGX
preflight exposed and then fail-closed two data hazards before the experiment
registry opened: late-sample top-60 breadth collapse and a 46-name clustered
right-censoring event. The final contract excluded the systemic terminal
cluster, retained the singleton XMR termination as a forced exit, and produced
305 non-overlapping weekly periods for exactly five preregistered candidates.

The only formal run on immutable release `b9e7b45` registered five trials
(global count 2,850 to 2,855) and returned `REJECTED_HISTORICAL`. The high-IVOL
primary had net Sharpe `-0.350`, net return `-53.15%`, maximum drawdown `61.51%`,
DSR `3.87e-11`, PBO `0.0857`, and maximum participation `17.19%`; its final 20%
suffix Sharpe was `-0.972`. All robustness and economic-control bars except
full evidence verification and PBO failed. The 6.48 MB immutable artifact is
retained on DGX as
`/srv/quantos/state/evidence/crypto_idiosyncratic_volatility_b9e7b45.json`,
SHA-256 `29bc75421162bdc16d3b349051f85dccdebe40dfa500a7a5db6fc736a004294c`.
Overwrite was refused without changing the trial population.

This exact IVOL specification is now an
`EXISTING_FALSIFIED_IMPLEMENTATION`, not an open experiment gap. It cannot be
rescued by reversing the sign, lowering costs, reducing the universe, or
changing the window after seeing the result. Formal Alpha eligibility remains
**zero**; no forward admission, Testnet intent, credentials, broker connection,
order submission, or live authority was created.

## 2026-08-25 crypto weekly MAX falsification on DGX

Two conflicting peer-reviewed direct-crypto priors were resolved through one
frozen experiment rather than by choosing the winning sign after inspection.
The primary was Grobys-Junttila low prior-week MAX; Li-Urquhart-Wang-Zhang
high-MAX continuation was a non-rescuing sign control. Ordinary weekly
reversal, a fixed-hash placebo, and a nested top-30 investability control made
five preregistered candidates in total. The sample retained 305 non-overlapping
weekly periods, one XMR forced exit, and excluded the first 46-name clustered
right-censoring boundary without later sample re-entry.

The only formal run on immutable ARM64 release `34d5792` registered exactly
five trials (global count 2,855 to 2,860) and returned
`REJECTED_HISTORICAL`. The low-MAX primary had net Sharpe `-0.254`, net return
`-48.18%`, maximum drawdown `68.92%`, DSR `1.43e-10`, PBO `0.40`, and maximum
participation `18.79%`; its last 61 periods and double-cost replay were also
negative. High MAX also lost money (Sharpe `-0.194`, return `-41.56%`). The
top-30 low-MAX control's weak base result (Sharpe `0.193`, return `+7.43%`)
became `-23.38%` under double cost/impact and did not pass investability bars.

The 6.51 MB immutable artifact is retained on DGX as
`/srv/quantos/state/evidence/crypto_weekly_lottery_max_34d5792.json`, SHA-256
`d8ad393e9b3565efcc29b9717a5984490483d8eeab11505c59998df76f318eea`, with
`root:quantos` ownership and mode `0440`. An overwrite attempt failed before
registry mutation, leaving both the trial count and hash unchanged. This exact
MAX family is now an `EXISTING_FALSIFIED_IMPLEMENTATION`; neither literature
sign may be recycled through post-hoc tuning. Formal Alpha eligibility remains
**zero**, and no forward, Testnet, credential, broker, order, or live authority
was created.
