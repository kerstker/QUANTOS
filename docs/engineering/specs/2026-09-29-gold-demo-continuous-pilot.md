# 黄金Demo持续策略试运行：用户范围与实现缺口

## 2026-10-01 持续pilot受保护安装交接

新增`tools.gold_pilot_install`：默认只读审查原`render_gold_pilot_unit`的review原字节摘要、两份文件摘要和完整模板字节。原supervisor核验已接受发布、注册表/签发policy，要求已有有效黄金shared-budget policy、同账户/源码，批准剩余窗口覆盖24小时；旧一小时或过期BTC配置不能转换成新权限。同步读取原root custody策略输入的身份摘要，深层策略材料仍由原executor在启动时核验，明确不声称剩余预算已验证。

`--install-approved`只供独立本机sudo/TTY操作员确认完整候选摘要，在原controller排他维护租约内重新核对材料、配置和期限。仅首次安装，拒绝已有或部分request、已有pilot基准、已加载/活动/未知的同名单元。原生成器及其测试此前未进入5f39冻结源，现按工作区原字节一并纳入新候选；没有修改正在验收的5f39源码。

确认与审计留存后再复核，在原目录保存候选、操作员记录、原公开registry/policy及精确两文件，然后以原持久排他写入口创建executor custody的request和root单元文件。通过不跟随符号链接的有界描述符读取检查目标属主、单链接、权限、时间/内容，并复核配置。任意可能持久写入后的失败保留installed=UNKNOWN及失败审计；不删除、不重放、不覆盖基准。禁用服务没有Install段，入口不daemon-reload、不启用、不启动；独立启动操作另行授权。

这项能力不生成策略批准、签名或经济资格，不读凭据，不改注册表/预算/停机/账本。仍未完成首次24小时黄金shared-budget policy、过期BTC到黄金的受审查迁移、现场安装/连续运行以及真实成交/资金反馈。短授权拒绝是保持原范围的结果，不能自动续期。未部署。

初始三项检查发现冻结源遗漏旧生成器；原失败记录保留。五组相关67项通过，新入口Ruff与原服务图检查通过（20域/52边/无环）；最后的描述符读回加固后24项通过。隔离回归保留虚拟操作员/属主前提，使用真实目录租约、排他写/fsync、文件读回及原执行登记；验证重启保留起点和重复安装拒绝。相关结果、实际hook和冻结源码单独记入本机gold-pilot-install-20261001-gzdze4pk，完整发布验收须由新候选自身完成，不能沿用5f39的结果。

本增量五文件冻结为`df0c3f5f0f5b6f620c9ef6418a9c06a8cb0e80a4`，实际提交hook通过；未完整验收、未现场安装。当前系统查询同名单元为not-found/inactive，仅为只读现场快照。

## 2026-10-01 精确决策的独立操作员安装入口

`tools.gold_strategy_policy_install` 接收原留存请求摘要，默认只复用
`review_retained` 生成未批准候选。显式 `--install-approved` 必须由本机
sudo 操作员通过 TTY 确认候选完整摘要；研究进程不能调用该安装路径。
入口在原 controller 独占维护租约下重验已安装源码、原注册表、policy、
运行输入、决策及固定行情前缀。确认和临时文件写入后再次检查原期限。

仅原有效 `DEMO_GOLD_STRATEGY_BUDGET_POLICY_V1` 可更新。允许的差异只有
精确决策的 policy ID 和 execution constraints 摘要；账户、签发身份、
mandate、风险限额、授权窗口和共享 budget group 完全保留。不读取签名或
交易所密钥，不修改注册表、停机状态或任何执行账本，不签发、不下单、不启服务。

原 policy 字节、候选与操作员确认先持久留存，再原子替换唯一 policy 文件；
成功另存结果。中断资料不删除、不自动重放；替换后核验失败返回 installed=UNKNOWN，
管理员须核对原件与当前 policy，不能把它当作未安装重新操作。

此入口仅补齐已安装黄金策略授权内的精确决策交接，不负责首装、过期 BTC
授权迁移、新增24小时策略批准或经济准入。运行时仍须通过原独立签发、风险、
停机和发送前门控。隔离测试使用操作员/属主替身与临时目录；它们不证明真实
独立批准或现场安装完成。尚未部署。

## 批准候选交接原签发链验证（隔离，非现场批准）

新增交接集成验证：留存请求→policy候选→测试夹具显式提供独立批准结果→原demo_request_inputs.build风控→ExecutionSession.propose→原issue_submission→原demo_submission.verify签名核验。没有HTTP、生产配置安装、真实密钥或交易所订单；dispatch/OMS仍空。候选自身approval_granted继续false，测试夹具不证明真实独立批准已具备。该验证与原共享预算跨policy拒绝及完整开平仓回合共32项通过，仓库卫生通过。

目前原demo_operator_setup仍是首用/有限功能测试入口，不能对已安装过期BTC配置直接重跑以替代黄金24小时策略登记。受保护的持续策略批准安装路径仍待实现，未部署本增量。

## 精确决策策略配置候选（未批准、未部署）

接收CLI增加显式--prepare-policy-candidate，复用同一原始材料读取和前后复核。policy_candidate在review成功后只替换policy_id与bindings.execution_constraints_sha256；保留原授权窗口、scope、账户、定义、mandate/validation/strategy摘要及共享budget，原validate_policy和budget_group_binding重新核验。新policy_id由原请求摘要确定，避免原IssuerStore拒绝同ID换绑定；不会重置共享预算，不声称剩余额度。输出包装为POLICY_CANDIDATE_REQUIRES_INDEPENDENT_APPROVAL，包含被替换policy摘要，批准/发送标志仍false。没有写policy、签发或发送入口。

候选经过原策略约束检查，预算绑定不变；CLI在两种模式均拒绝存储篡改和复核中配置变化。相关请求/controller/dispatcher三组42项通过，新模块ruff及仓库卫生通过。未部署；独立批准方安装候选的职责与保护路径尚未完成，实际24小时配置和Demo仍未运行。

## 独立批准材料接收复核入口（未部署）

在同一gold_strategy_approval_request模块增加review与review_retained/CLI。接收侧从原root custody固定目录按存储摘要加载请求，再分别读取原留存结果、运行配置和原supervisor核验的当前registry/policy，不信任请求自带的decision副本。重建请求逐字段比对、重验到期/预算/策略约束，并在返回前复读配置和三份材料，拒绝中途变动。返回REQUEST_REVIEWED_NOT_APPROVED，明确approval_granted/submission_enabled/economic_authority=false；不生成授权、不调用签发器、不写任务或claim。

六项接收侧回归先失败；请求/CLI/原controller/dispatcher三组38项通过，包含改后重新哈希的预算、原决策改动、到期、policy变化及复核中runtime变化拒绝。新模块ruff与仓库卫生通过。尚未独立发行、部署或连接真正批准方的授权生产；该复核是材料一致性检查，不是独立批准或真实Demo完成。

## 持续决策的独立批准请求留存（未部署）

新增tools.gold_strategy_approval_request.prepare，并接入原controller决策留存之后。仅在既有GOLD_STRATEGY_BUDGET_POLICY、有效期内、账户/版本/运行输入/定义和精确草稿约束均一致时生成INDEPENDENT_APPROVAL_REQUIRED材料；包含原decision、原policy摘要和不可变共享budget绑定，不声明剩余容量。原私有目录、内容寻址与不可覆盖留存路径保存到strategy-approval-requests；重复观察同一请求不重复产生文件。材料不生成批准、策略policy、任务或claim，不更改预算、签发或发送路径。

六项新增回归先因缺模块失败；相关controller/dispatcher/worker/pilot及请求留存五组39项通过，新模块ruff与仓库卫生通过。仅隔离合成输入，未部署；真正独立批准方消费请求、保护配置安装、24小时批准与真实Demo运行仍未完成。

## 现场授权接入核对与依赖纠正

2026-09-29：实际旧授权为已过期BTCUSDC。进一步核对demo_issuer_policy.validate_policy允许最长7天策略窗口，因此24小时不需要放宽签发器时间上限；一小时/三单限制来自demo_operator_setup的功能模板。gold_execution_identity刻意仅为TECHNICAL_RESEARCH_IDENTITY_ONLY/economic_eligible=false，原功能Demo可由独立人工批准建立技术验证资格，研究核验公钥为空不是所有Demo技术验证的绝对前置条件。不得将技术Demo重新表述为经济准入或实盘资格。

持续策略仍需真正接入独立批准生产与配置流程：gold_strategy_request按每个entry/exit草稿冻结decision/orders/definition/time约束，已安装policy须固定该精确摘要；它本身不构造policy或批准。当前缺口不只是延长旧授权，需要有限总预算及逐决策独立批准的运行接线、真实策略输入和保护安装。原更严格风险限额保持，不得复用旧BTC数量或伪造人工签发。

## 持续pilot启动材料生成器（待实际绑定）

新增scripts.render_gold_pilot_unit，显式固定版本、公开账户引用和研究状态路径，生成disabled的quantos-gold-pilot.service与gold-pilot-request.json及摘要清单。监督进程仍为原root controller，经原systemd隔离执行/签发；只在显式execute-approved模式消费已有独立批准。不包含密钥、策略批准或一小时预算放宽，不安装/启用/启动，也不覆盖既有pilot。Restart=no，停止不使用强杀，原执行侧持久基准保持。

生成请求接原enroll_requested的隔离验证与相关拒绝合计12项通过。systemd-analyze verify在gold-pilot-unit-review-20260929-e8l7ynkx通过；该语法探针使用合成账户，并仅将尚未安装的目标解释器路径替换成/usr/bin/python3，没有加载或运行服务。尚需真实账户/验收产物绑定、原独立policy范围核对、保护安装与完整systemd子进程/反馈写入演练，不能据此称已可现场启动。

## 启动链实查：研究服务交付不等于Demo服务部署

当前完整验收后队列准备的是源码、前端和quantos-next三项研究服务；没有把gold_pilot_run装入Demo常驻单元，也没有安装gold-pilot-request.json。仓库现有demo_operator_setup仍固定一小时/三笔功能模板，不能以该模板替代本轮24小时配置。gold_strategy_controller要求root监督身份并通过原systemd执行侧隔离单元采集，启动材料须沿用此边界；不能作为研究runner普通任务直接启动。

下一现场准备须绑定验收后的实际artifact commit、原账户公开引用、已有独立policy/批准范围，生成disabled的持续pilot服务及精确登记请求，并通过原受审查安装路径；不扩权或自动放宽原一小时授权。此项目前尚未完成，不能将整合源码通过或研究服务升级视为Demo已部署。

## 有限持续调度入口（未运行）

新增tools.gold_pilot_run，默认86400秒观察调度，每轮复用原tick/controller、强制require-pilot，仅显式execute-approved时消费原独立精确批准。无信号/等待批准/原查询间隔继续观察，原恢复或退出异常停止并保留待处理状态。每次重读原已安装策略输入，不产生新批准、晋升候选或重写策略。实际结束与累计权益仍由执行侧持久配置决定，重启此进程不延长pilot。

达到进程调度预算只报告SUPERVISOR_BUDGET_EXHAUSTED，不声称空仓；已有执行历史须完成原研究反馈才计结束。无执行历史、核验空仓且pilot停止才可正常结束。预算在原有界调用之间检查，不强杀正在对账的子流程，单个原调用可延续到自身限时。此为调度代码，未安装常驻服务、未运行24小时或真实Demo，也不代表获得独立研究准入。

## 持久停止接原持仓退出（开发状态）

原gold_strategy_exit在原仓位归属、成交起点与新鲜报价核验之后复读持久pilot控制；亏损或到期直接产生PILOT_LOSS_LIMIT/PILOT_DURATION_COMPLETE减仓触发，不必等待策略自身止盈止损或持有超时。仍仅生成原剩余持仓的reduceOnly草稿，保留原报价、交易所规则、容量、独立精确批准、签发及风险门。原更严格风险限制仍可能拒绝退出，未绕过或宣称保证平仓。cycle标明position_flat，tick仅在核验空仓时将停止认作正常终态，否则明确PILOT_EXIT_REQUIRES_ATTENTION。

六组59项通过，含无策略退出触发时的亏损/到期草稿与到期减仓→原独立合成签发→回执→原账本/资金反馈→同研究campaign续轮。测试为合成账户、行情、批准与成交，未真实Demo或部署。持续24小时调度与独立授权现场配置仍未完成。

真实双批研究34d进程3832456已结束，原session82656返回FRESH_TWO_BATCH_INCOMPLETE。actual-cg0uxcs6/validation-summary.json记录两批均invalid receive window、program EXHAUSTED、results为空；不再报告等待或运行中，不重启原件。须修复公开行情采集稳定性后再采新数据，不能放宽回放时间界限。

## 执行进程首次登记接线（开发状态）

原gold_strategy_worker在原账户捕获和账户绑定之后、原preparation lease内部调用enroll_requested。只有执行侧受保护的gold-pilot-request.json存在且源码/账户、86400秒和0.05范围一致时才登记；不从策略管道接受登记请求或手填余额。新鲜来源与原未决/账本检查通过后，以排他目录及不可覆盖记录保存configuration.json和enrollment.json。重复调用保留原起点，部分写入拒绝恢复成新基准，原观察检查也拒绝无登记收据的半份配置。此文件不提供订单权限、独立批准、重置或放宽原风险门。

原worker合成完整决策/重复调用/持久化交接已覆盖有登记请求和旧模式两支，登记、停止控制、worker、tick四组39项通过。来源捕获在该测试中是合成替身；未现场登记、未安装请求或部署，不能声称实际来源认证或24小时运行已完成。下一步仍需具体受审查请求的安装、持续调度及亏损后主动退出的原授权流程。

## 期初权益准备入口（未安装）

新增 `live/execution/demo_gold_pilot_enrollment.py::prepare`，复用原账户来源评估、执行存储未决检查和已有历史的账本重建/账户核对。仅接受新鲜黄金Demo空仓观察，基准直接取原核对钱包，不接受外部余额参数；输出绑定来源、执行状态和24小时/5%配置摘要的待登记材料。不会写入pilot目录或赋予下单权限。空仓正常、过期、余额观察变化及未决已准备订单拒绝回归与原停止控制合计9项通过。

该准备入口尚未接受保护的安装流程；公开的结构校验不代表来源认证。实际登记仍需原执行侧验证来源归属并在写入时复核新鲜度/执行状态，不能直接把准备输出当作已启动配置。未部署、未实际Demo，调度及主动退出仍缺。

独立冻结版本2186754a1558122a9203efcf2b9e32443c3f5b0a：十组112项通过，实际pre-commit通过；未部署，真实期初登记与主动退出尚缺。证据位于 `/home/kerstker/quantos-dev-output/gold-persistent-pilot-20260929-436h1er7/validation.json`。

## 续接实现：固定配置绑定、持久停止与运行消费者

`demo_gold_pilot` 在执行侧已有状态根的gold-pilot目录读取已安装配置；不提供
自行登记、配置修改或解除停止接口。配置固定账户、起始权益、期初来源摘要、
24小时起止时间和5%界限；首读保存配置摘要绑定，到线/到期追加不可覆盖的
stopped.json。后续权益恢复或重启仍返回原停止记录，改配、缺失部分配置或
损坏记录拒绝继续。它只记录控制状态，不计算或替代原成交/资金账本。

策略观察在无信号时也检查控制；开仓提案将控制摘要放入原state并纳入原
签名上下文，独立签发拒绝已停止的新增风险，发送前重读固定配置和停止记录。
已准备订单的配置目录若丢失，不能回退成旧模式发送；原未决意图留给原恢复。
tick新增显式require-pilot模式，观察缺少控制记录就停止交接，不把旧模式
当作持续试运行；停止后的已完成回合仍允许原研究反馈交付。

隔离验证已覆盖亏损/到期、权益恢复、重复调用、改配拒绝、无信号到期、
已准备订单到期及整个配置目录丢失时不领取发送/不访问凭证。原完整合成
开平仓、worker和tick等七组63项通过，后续签发复核与强制pilot入口重验
38项通过；本地fixture代替执行侧权限与账户，不是现场运行证明。

剩余：独立受审查的配置安装与真实期初来源重验尚未实现。配置中的期初
摘要当前是声明，不能据此声称已核验实际启动权益。持续24小时调度、亏损
触发后的主动退出规则及相应独立授权仍未完成；原更严格风险门保持。
未安装、未改变生产状态，也没有实际Demo订单。本节替代上一检查点中
“尚无持久化触发状态”的开发状态，不替代剩余现场验收。

## 首个实现：原账本累计亏损新增风险检查

`demo_position_context.require_gold_cumulative_loss` 已读取原重建上下文中的
`initial_equity`及绑定账户当前权益，以Decimal在95%边界及以下拒绝任何
包含新增风险的黄金批次。原提案生成、独立签发复核、发送前复核均已接入。
日盈亏、跨日开盘权益或峰值不能替换该基准；该检查不重写账本或批准资料。
只减仓批次仍须通过所有原风险/mandate/签发检查，没有新增发送权限。

九个边界回归先失败，再补实现；四组55项通过，原策略完整合成开平仓及
发送前检查另三组24项通过，卫生与治理通过。尚未独立冻结或完整发行验收。
这是额外保守检查，**不是已完成的24小时试运行控制**：现用原账本最初
抵押权益，尚未绑定本次运行起点；首次无历史订单时也没有独立的试运行基准。
尚无持久化触发状态，价格恢复后不能靠此检查独自保持停止，持仓监控和
授权退出也尚未接完。现有更严格的日亏损/峰值回撤门仍可能阻止退出，必须
为退出准备独立可审查的风险处理，不能绕过原门控。未部署或真实Demo。

用户已明确：单笔规模由系统结合黄金市场评估确定，累计亏损5%停止，持续
运行一段时间。当前准备采用24小时的有限试运行；不是主网交易授权，也
不代替既有独立策略批准、执行签发或具体部署操作。没有启动实际Demo。

## 拟实施的运行语义

- 仅Binance普通USDⓈ-M Demo的XAUUSDT。复用已有独立执行/签发设施。
- 在实际启动前以原已对账的Demo测试权益冻结基准E0；账户、源码、策略
  身份、起止时间及基准来源摘要纳入同一受审查配置。未取得真实基准时不
  填造余额或计算批准后的金额。24小时从实际启动算起。
- 累计净损益计入原账本手续费、资金费、已实现及按新鲜标记价计量的
  未实现盈亏。权益减至E0的95%或更低时触发。跨日/重启不重置E0；
  不用每日盈亏或滚动峰值回撤冒充累计亏损。划转或对账不明时先停止新增
  风险，不能通过重设基准掩盖损益。
- 达线后停止新增风险，未决订单走原恢复/撤单流程；持仓只允许原授权
  的减仓退出。停止状态须持久化，后续价格恢复不自动重启；退出仍须通过
  原风控/授权，不能将停止指令转成绕过门控的发送能力。
- 单笔规模须同时满足实际Demo过滤器、策略止损距离、费用与滑点、已有
  mandate/issuer数量及名义金额限额。不能为满足最小订单而超过风险预算；
  不能拿主网的过滤器替代Demo实际规则。无有效信号则不下单。
- 5%为新增外层上限，原有更严格限制保持，不能据此自动放宽原批准预算。

## 已核实的缺口

`tools/demo_operator_setup.py` 的FIXED/spec_check当前只允许一小时、三笔、
日亏损10；assemble还固定1%峰值回撤与数量/名义金额限制。该功能测试
模板不是本持续运行配置，不能修改提示文字后称作5%试运行已生效。

`gold_strategy_tick.run_loop` 目前是最多60次观察的有界会话，消费已有精确
批准；未提供本次24小时持续调度、独立批准生成或持久化的5%停止状态。
`demo_position_context` 可从原账本重建初始权益及当前钱包、跨日上下文，
应复用其依据，不创建第二套成交/资金账本。

当前真实研究首批在bootstrap exchangeInfo请求上耗46816ms，超出原回放
30000ms接收界限；原记录未修改，拒绝有效。第二批仍由原程序按真实冷却
等待。此为数据采集稳定性缺口，不是可用信号或策略表现结论。

实现顺序：固定运行配置及原账本基准绑定；将累计亏损检查接入每次新增
风险前以及持仓监控/原退出流程；持久化停止及原有恢复；有界后台持续
调度；独立批准/实际Demo联调。真实5%边界、重启、跨日、未决/非零仓位
退出及最终现金反馈都必须验证，不能只凭配置文件或纯计算测试宣称完成。

## 官方规则核对

合约最低订单门槛会调整，应从当次交易环境的API过滤器读取，参见
[Binance合约规格](https://www.binance.com/en-AE/support/faq/detail/360033161972)。
市场机制参考[Binance TradFi永续说明](https://www.binance.com/en/learn/binance-futures-tradfi-perpetual-contracts)，
不将机制说明当作方向预测或提高杠杆的理由。
