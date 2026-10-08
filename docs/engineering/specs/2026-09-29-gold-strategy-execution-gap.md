# 黄金策略到Demo：身份关联不等于策略执行

## 2026-09-29：开仓IOC零成交结束后自动研究续轮

复现原循环已判定STRATEGY_EPISODE_SETTLED，但仅一批零成交入场因反馈要求至少
两批而失败。策略导出现允许一批，沿用原build_feedback、原私有回执重建和现金
链；仅当订单EXPIRED/CANCELED/REJECTED、成交量严格为零、原仓位归零且现金残差
为零时，才作为完成的技术复盘。正常开平仓仍使用原roundtrip检查，不放宽部分
成交、持仓未关闭或未知回执。

发布器、自动反馈选择及FactoryStore事务使用同一完成条件；继续绑定前一轮试验、
原输入、冷却、候选排除和次数预算。复用既有单批反馈格式，无新账本或盈利标签。
合成整链实际执行原策略/签发/session/终态回执/OMS/现金采集/导出/发布/同campaign
下一轮JobQueue，只有一笔模拟发送、零成交，第二轮候选0→1。重算摘要后部分成交、
未知终态、非零仓位标记、余额残差及布尔数量仍被发布拒绝。

此前独立准入接口复核确认源码公开收据注册表keys为空；原gold_cost_receipt继续
只核验外部收据，不生成签名或经济准入。本增量只解决零成交复盘交接，不能替代
独立批准生成、实际部署或真实Demo，亦不修改238e在途全量验收候选。

## 2026-09-29：当前决策精确交接与原有效期内批准等待

发现统一tick调用dispatcher时未传当前结果身份：原自动模式只按当前policy查找
历史结果，可能在新退出观察之后返回旧开仓任务的QUERY_ONLY。现tick将原留存
结果摘要传入dispatcher，直接复核该原件与runtime/account/strategy/constraints，
不扫描历史目录，也不将其他历史决定替换为当前交接。

独立policy尚未绑定当前约束时，仅对原已配置同策略预算组返回
WAITING_INDEPENDENT_APPROVAL，task/claim尚未创建。tick只重查同一摘要，等待
受原valid_until与30秒单调时钟上限共同限制；超时停止，不延期或生成新决定。
任何其他返回（包括领取后响应丢失）立即结束，不重新提交。原显式引用及历史
policy选择CLI保留，新增--result-sha256只允许与--use-approved-policy一起使用。

原任务领取/丢失响应、旧批准不替代新决定、不扫描历史、批准随后到达、过期停止、
预算变化拒绝及controller/worker/合成开平仓联合45项通过。没有生成独立批准、
改policy或密钥；独立批准生成端仍是未完成交接。代码未部署，真实三秒策略仍待
完整时效验证。此增量在9b35184e之后，不修改238e在途全量验收候选。

## 2026-09-29：统一循环复用同一策略工作进程

原gold_strategy_tick.run_loop每轮调用一次controller.run，导致每轮重新启动
worker并重新完整预备真实归档（此前单次约17秒）。现循环调用一次有界controller
session，在留存并核验每次观察后回调原批准消费器，再向同一worker请求后续观察。
进程内PreparedStrategy及全部逐次原件核验沿用，未保存或恢复外部“已核验”断言。

独立精确policy允许在轮次之间轮换，但registry、账户及runtime仍固定；每一轮
前后仍复核同一policy，提交仍经原dispatcher/task/supervisor/issuer。维护共享锁
覆盖会话，原下单去重及风险检查保持。工作进程最多61帧（一次预备及60轮），
会话内等候下一控制帧上限450秒，systemd总时长按轮数计算；这不是策略报价/下单
时限延长。收到无信号、反馈完成或错误时停止；异常保留先前交接结果，不重发。

真实子进程管道验证一次预备、同进程两次新模式观察、轮间policy轮换、账户变化
拒绝及提前停止；原worker预备复用、dispatcher、现金、恢复及合成开平仓联合46项
通过。系统身份/systemd和网络仍有隔离替身，未部署、未实际Demo，不能据此声称
实际三秒路径或全天无人值守成立。此增量在70b07a7e之后，不修改238e在途候选。

## 2026-09-29：完成现金窗口支持延迟反馈恢复

复现了已CAPTURED窗口在一小时后重启仍被OPENING_EXPIRED拒绝的问题，
这会阻断已经结算的策略继续发布研究反馈。open_window现仅在原观察已存在、
原关闭报告也存在时，调用原close_window的完整来源/账户/期初绑定核验后复用。
没有补采期初或关闭网络请求；尚未关闭的过期期初仍拒绝，新观察不能借用旧余额，
错绑关闭报告仍拒绝。现金/worker/tick/策略开平仓联合22项通过。
这是原现金组件及调用链的隔离恢复验证，尚未证明现场后台重启和实际Demo；
增量不在238e5498验收候选中。

## 2026-09-29：版本核验并行增量实测

demo_issuer.verify_source保留原固定发行路径和全部祖先权限核验，随后并行运行
原identity与evidence只读检查，两者均结束且成功才继续。每次调用重新检查，
不缓存成功、不取消失败后的另一项检查，不改变策略截止时间或批准范围。
重复调用及任一分支失败回归、原supervisor/controller/dispatch联合38项通过。

同一已验收694e16d5产物交替执行12组串行/并行测量，串行中位362.416ms、
并行321.634ms；各12次累计4173.836/3811.314ms。存在后台构建负载，
这里只证明本次组件测量的小幅改善，不是完整信号到下单测量或三秒可执行证明。
原始结果保留于
`/home/kerstker/quantos-dev-output/gold-release-parallel-measure-20260929-pcp877ow/comparison.json`。
该开发增量不在正在验收的238e5498候选中，未部署。下一关键验收仍是完整
后台策略进程交接及真实时效，不能用此局部优化关闭整厂执行缺口。

## 2026-09-29：已验收产物的信号后版本核验耗时

使用81c0已验收证据产物694e16d519ffaf699a6dc264b75c25c41b41c2d3的干净
verification-clone，只读运行原_verify_release_identity/_verify_release_evidence。
三次新进程核验内部耗231/274/302ms，进程墙钟455/348/558ms；未读取凭证或请求网络。

正常信号后路径有八次显式verify_source：controller后检查1、dispatch配置前后2、
task配置1、request_prepare启动1、supervisor配置1、issuer配置前后2。在同一进程
连续运行八次原版本核验组件实测2124ms，单次186–323ms。此为组件测量，不是端到端
订单时延：未包含进程/systemd启动、受保护路径检查、policy/账户/行情、风险、签发
和发送；当时另有构建负载，不能推导所有运行必然超时，也不能将此值当作稳定P99。

三秒真实入场尚未证明，不能通过延长既有策略截止时间或跳过原检查来宣称完成。
后续时效交接应测完整阶段并评估可在信号前完成的工作，或以新研究身份研究可执行
的策略时限；旧策略继续按原时限拒绝。证据目录：
`/home/kerstker/quantos-dev-output/gold-release-verification-latency-20260929-67vjsxv0`。

## 2026-09-29：策略后台接原订单恢复

统一循环存在实际断点：发送ACK后回执尚未投影，原策略_state返回RECOVERY_REQUIRED，
原入口会直接结束。worker现于新账户捕获之前、原preparation lease之外调用原
RecoveryLoop的一次tick，仅当原recovery store仍有pending时执行。原cycle锁、
持久查询预算/间隔、原订单ID查询和回执投影保持；没有新发单或清除pending。

原恢复报告随worker结果传回，统一tick仅对WAITING_QUERY_INTERVAL及RESIDENT_BUSY
继续有界只读等待。其他恢复未知/缺口仍停止，不能将未知订单当作成功成交。
原RecoveryLoop与真实隔离stores验证两次调用只查询一次，未决记录保留、wire仍空；
七组联合58项通过，追加原预算联调3项通过。网络查询/进程边界仍有替身，未生产运行。

## 2026-09-29：观察与已批准任务的统一有界编排

核对发现controller与dispatch没有共同调用入口。新增tools.gold_strategy_tick，
先调用原controller（包含现金关闭与反馈发布），只有DECISION_REQUIRES_APPROVAL
且显式--execute-approved时才调用原dispatcher的execute/use_approved_policy。
默认不请求执行；不安装policy、不签名、不新建执行账本或变更任务防重。

入口支持1–60次有界tick及1–60秒间隔；继续观察持仓，反馈送达或无信号后停止，
观察失败、恢复需求、其他仓位需对账、批准消费失败均停止并保留原返回结果。
不存在隐含无限重试；独立精确决策批准仍必须提前由原独立端提供，本入口不解决
批准生成或真实三秒时效。没有安装timer或运行生产命令。

原观察/消费器联合31项通过，包含模拟开仓→持仓→退出→反馈发布的连续编排及
未知状态不放行。原38e8948d仍固定不变，此增量待整合。81c0在途canonical的
后端阶段已实际完成9604通过/19跳过；执行侧阶段仍在运行，不是完整验收通过。

## 2026-09-29：原现金采集进入同一合成研究循环

原strategy_roundtrip不再手工构造最终cash报告：先经gold_strategy_cash及原GET
collector保存opening，再沿同一研究归档的信号开平仓，关闭时用原collector采集
期末余额与两遍流水。期初原件保持、重复关闭不再请求。采集结果直接进入原现金
账本和反馈导出、发布，再由原campaign/JobQueue完成下一轮研究；相关整链2项通过。
GET传输、模型、批准与成交仍为模拟，控制器systemd与worker完整进程生命周期尚需
统一演练；不能将函数级整链与分段进程检查称为真实Demo。

## 2026-09-29：新行情策略的现金窗口生命周期

原binance_demo_cash_window.collect新增显式opening_only：完成原clock/wallet两次GET后
返回OPENING_CAPTURED，默认行为保持。gold_strategy_cash按runtime内容地址持久化
source/account/collector绑定及该原始opening；后续tick直接复用。新行情worker在
observe_inputs监听前完成opening，归零决策留存后调用原collector的opening_prefix
恢复协议，等待原30秒保护窗口并取得期末余额及两遍流水，保存到原反馈导出器消费
的strategy-cash目录。不存在跨控制进程存活的采集线程，也不在恢复时替换期初余额。

观察已存在但opening缺失时拒绝追补；过期/错source/account拒绝。关闭采集中断
可从同一期初继续，失败报告另存，完成窗口重复读取不再联网。新行情控制器将单tick
等待扩到450秒、含预备的子单元总预算630秒，覆盖原collector 30+220秒预算；
这不延长策略入场或报价有效期。旧留存观察模式未自动补造现金开端。

原GET模拟传输实际两阶段采集、恢复/重复关闭、监听前顺序、worker/controller和
原策略反馈整链六组56项通过，卫生PASS。凭证/网络均为合成替身，未生产读取、
部署或实际Demo。仍须整合整个worker/controller现金生命周期为同一运行验证，
再冻结新增源码并完成验收；在途81c0不包含此增量。

## 2026-09-29：后台发布与同一策略反馈后续轮

gold_strategy_controller 已在归零决策及反馈留存后调用原独立发布进程的
`--strategy` 模式。该进程沿用原发布配置并永久降到研究UID；控制进程不降权、
不读取密钥、不接受执行侧提供的任意路径。发布收据核对runtime和feedback摘要后
单独留存，原worker结果与其内容地址不因增加发布状态而改写。发布失败保留原决策，
后续可重试原不可覆盖发布，不重发订单。

整链夹具现以原Factory campaign/JobQueue的第一轮归档作为策略输入，原信号、
签名入场、目标退出、现金窗口对账、执行侧反馈原件选取/留存、研究端发布之后，
同一个campaign自动预留并完成第二轮原研究任务，候选0→1，新trial身份和执行反馈
引用均经原Store核验。模型、市场、成交和批准为合成资料；队列、策略逻辑、账本和
反馈构建使用原实现。控制端systemd/发布子进程边界另以替身验证，不表示现场运行。

六组相关44项通过，原件错误绑定与发布失败拒绝保留。尚缺开仓前现金窗口的后台
生命周期接线；独立准入、实际部署、真实策略Demo与真实现金反馈均未完成。

## 2026-09-29：策略周期接原现金反馈导出

新增 `tools/gold_strategy_feedback.py`：重放原观察与研究归档，核对原信号的入场
client ID、数量、各批策略定义约束和研究身份，再调用原 receipt/cash exporter。
执行侧导出从原 reservation、position context、resolution plan 和 proposal 复原
prepared batches，接受固定 `strategy-cash/<runtime digest>` 中已留存的现金窗口；
支持原 exporter 的 2–32 批次范围，不创建 functional-cycle 或第二套现金账本。

原 gold_strategy_worker 在已留存 STRATEGY_EPISODE_SETTLED 决策后、原准备租约内
调用导出。现金窗口不存在时返回 WAITING_STRATEGY_CASH_WINDOW；存在但不匹配时
拒绝，持仓归零不自动变成现金已核验。导出原件保存在
`strategy-feedback/<runtime digest>/research-feedback`。原 demo_feedback_publish
新增显式 `--strategy` 固定目录模式，沿用原独立发布配置、降权及不可覆盖写入。

同一合成策略开平仓已通过实际原件选取、原资金对账、反馈留存与重复导出；原入口
缺失回归先失败，改后联合19项通过，最后新增错策略/缺约束拒绝后的整链2项通过。
仓库卫生PASS。没有运行实际Demo、读取凭证或改动生产；本增量不在81c0候选中。

**仍缺实际编排：** 开仓前自动启动该策略现金窗口、平仓后完成窗口、控制端调用
策略模式发布器以及同一反馈触发下一研究任务。当前新增代码完成已有窗口的导出，
不能据此标记自动现金采集、整厂闭环或经济准入完成。

## 2026-09-29：原请求准备的两组独立采集并行

原demo_request_prepare.capture_inputs依次等待账户16-GET完整结束，再启动
公共风险行情4-GET。在三秒入场窗口下，这额外叠加独立网络等待。现使用固定
两worker同时调用原demo_executor_origin.run和原protected collect_bounded；
各组内部请求顺序、时钟区间、账户前后读、原子性限制、进程超时、源身份和
风险检查均不变。两组都结束后才加载精确账户原件和留存公共行情，仍不借用
latest移动后的另一份账户结果。任一异常会收集两侧完成结果后拒绝，无重试。

真实线程Barrier回归先在原串行代码失败；改后原输出逐字段一致，并通过原
request build。两侧分别失败的场景验证不会加载/发布半份输入，也没有残留
collector继续运行。四组31通过，包含原task、黄金executor输入、策略task和
组合准备。此是并行机制验证，没有读取现场凭证或实测交易所延迟，不能据此
宣称3秒入场成立；重复账户采集、独立批准时延及真实完整Demo仍待验证。
证据目录：`/home/kerstker/quantos-dev-output/gold-parallel-request-inputs-20260929-_ssul95t`。

## 2026-09-29：已独立批准决策自动交接

新增显式 --use-approved-policy 模式，读取既有root控制目录下固定
strategy-dispatch.json：schema=GOLD_STRATEGY_APPROVED_POLICY_DISPATCH_V1、
source_commit、account_ref_sha256、budget_group_binding_sha256、kill_switch。
该文件只委托消费既有批准；必须匹配原预算组不可变绑定，不能创建或更换批准。
默认仍使用原strategy-task-approval.json逐决策引用方式。

自动模式先沿原supervisor.configuration核对当前有效的独立policy，再从原
strategy-decisions查找唯一execution_constraints摘要匹配项。只接受明确共享
预算policy；检查原文件归属/名称/内容摘要，扫描最多1000文件和64MiB。后续沿
原worker结果/账户/运行输入核对、草稿时限、profile、task_check和task claim，
配置在物化前重读。显式--execute才交给原demo_task，不自行签发或写policy。
多个独立政策可在同一固定消费配置范围内交接，不能用policy_id变化重置原预算。

回归覆盖相同固定配置消费两个不同精确批准、丢失响应后原claim阻止重发、错
批准无任务、歧义结果拒绝及共享预算绑定变化拒绝；原显式模式保留。五组联合
32项通过，静态检查通过。测试使用临时原task文件/claim与模拟执行返回，没有
真实凭证、生产配置或交易。本功能解决每个已批准信号再手填消费引用的步骤，
不解决独立批准生成服务、实际安装、3秒内真实执行或后台调度。
证据目录：`/home/kerstker/quantos-dev-output/gold-approved-policy-dispatch-20260929-d436qh13`。

## 2026-09-29：批准到期不停止只读持仓观察

策略控制器此前沿用supervisor.configuration，其中current_key要求签发许可
有效。原入场许可到期后，同一观察runtime无法再进入账户/持仓观察，即使完全
不签发或发送。现控制器采用与既有组合reconciliation_configuration相同的
历史身份绑定：核对root控制、已验证发行源码、双registry一致、原policy结构/
registry/key身份与源码绑定、状态目录归属。每次交换前后仍重读同一配置。

只读控制器不能创建批准或调用任务执行；dispatch、demo_task、supervisor和
issuer原current_key路径完全不变。验证真实子进程管道中，过期policy仍接收并
持久化HOLDING；随后的原supervisor提交在配置阶段拒绝，没有新child或claim。
发行源码错配和registry镜像不一致仍拒绝。联合32项通过，加入真实控制器与原
提交入口对照后控制器6项通过；静态检查通过。所有配置/身份/进程均隔离替身，
没有读取生产批准文件、密钥或修改服务。此不解决独立连续批准生成或实际Demo。
证据目录：`/home/kerstker/quantos-dev-output/gold-strategy-observation-expiry-20260929-op309ois`。

## 2026-09-29：新行情模式后续持仓周期复用原观察

发现FRESH模式初次采集后，同一runtime后续调用一律ALREADY_ATTEMPTED，阻断了
原入场信号的持仓退出管理。失败回归已复现。worker现在只对不存在的观察路径
调用原采集器；存在时先核对归属、原plan和protected网络模式，再完整重放原
观察。仅COMPLETE_OBSERVATION可复用；不改写任何原记录、不重新注册时间或
延期入场。缺失/残缺/错plan直接拒绝，不把失败目录当作重新采集的许可。

后续仍使用原worker账户采集和原cycle/OMS进行持仓判断，同一runtime无需改成
另一个观察编号即可重新运行。完整合成开平仓已改为每个周期重新建立进程内策略
预备，经这个保留观察入口后执行原入场、恢复屏障、退出与已平仓判断；原签发/
成交/资金链保持。五组15项通过，显式验证新worker预备后复用不产生第二次网络
采集，错plan与不完整原件不触发重采集。此前测试曾因局部helper误替换导致一项
NameError，已修正并完成上述联合重跑。

此为d3cc4689a之后开发增量，未部署，不证明真实持仓运行或时延。独立连续批准、
真实执行、持续调度与完整现金反馈仍未完成。证据目录：
`/home/kerstker/quantos-dev-output/gold-strategy-held-observation-20260929-xu8o1g71`。

## 2026-09-29：精确决策批准共用原签发账本预算

新增明确DEMO_GOLD_STRATEGY_BUDGET_POLICY_V1，保留精确execution_constraints绑定，
额外声明budget_group：固定策略定义、组ID/有效期、签发/订单次数、名义金额及
取消次数。原policy检查和每份policy的预算继续生效；新增组策略须独立安装，
未自动生成、替换或放宽旧policy。原supervisor识别该明确策略协议。

原IssuerStore沿用现有RESERVED/SIGNED事件和BEGIN IMMEDIATE事务，无新数据库
或资金账本。预留即消耗共享组，签名失败不返还；快照重放也核对组累计预算。
组ID绑定账户、部署/registry/执行源码、签发身份、scope、原mandate/验证/风控/
策略批准以及组期限和上限，唯独不同精确决策与各policy自身限制可以变化。
同组变大额度、改变绑定或续期均拒绝；切换另一独立组仍需新的受保护批准，
本实现不允许AI自动创建组或决定组额度。

先复现新schema不被识别的四项失败，实现后验证不同精确policy共用次数/订单数/
名义金额，重开原账本仍拒绝超额；同组改大额度拒绝，签名中断留下的RESERVED仍
消耗，两个独立SQLite连接竞争最后一个额度仅一个签发成功。完整合成开平仓
已改为开仓和退出使用不同policy_id、同一budget_group，原签名/OMS/现金链继续
通过。七组联合64通过；追加并发及更新整链后两组8通过，静态/卫生/治理通过。
没有生产账本写入、真实签发或发单；连续独立批准服务、策略准入和部署仍未完成。

## 2026-09-29：执行侧行情代理交接

直接核对原child_command：executor允许AF_INET/AF_INET6，但使用env -i清空代理
环境；issuer仍PrivateNetwork。新观察此前使用WebSocket库默认路由，不会自动
接到执行侧network.json。回归先证明protected请求缺少proxy/ssl参数导致观察
失败；修正为worker显式选择protected_network，由原demo_network读取受保护的
loopback代理与TLS上下文，再连接固定原公共XAU URL，不接受请求内代理/主机/TLS覆盖。
默认研究观察方式保持。注册资料标记PROTECTED_DEMO_NETWORK_V1，并绑定网络
适配器源码；原回放复核支持该明确模式，未知模式拒绝，不宣称来源已认证。

六组23通过；补充配置错误不回退环境代理后，观察/新输入两组11通过，静态、
卫生和治理通过。测试明确断言证书/主机名验证开启。只读尝试读取现场network.json
得到READ_PERMISSION_REQUIRED，没有尝试提权、读取凭证、变更服务或发单；实际
protected网络连通仍UNVERIFIED，不能把模拟connect通过写成现场已联通。

同时核对原demo_issuer_store：同policy_id已严格禁止重绑定，名义金额累计当前
按该ID过滤历史。下一累计预算工作须在原签发账本内增加明确、不可改绑的共享
预算范围，保留每个精确决策policy的原检查，不通过更换policy_id重置额度。
独立准入服务和实际部署仍未完成。

## 2026-09-29：同一执行进程先预备再观察新行情

新增GOLD_STRATEGY_FRESH_OBSERVATION_INPUT_V1，固定源码、trial manifest及原显式
前瞻plan。控制器识别后即使ticks=1也先执行warmup，再向同一worker发送新观察
请求，原监管时长显式覆盖预备/有限观察/账户准备；该模式只允许一次观察尝试。
worker要求预备对象存在且归档匹配，再调用原bounded observe。原计划绝对时间、
区域和规则保持，冷启动导致计划过期时由原观察器拒绝，不偷偷重排信号时刻。

原始观察直接写在executor固定strategy-observation-work/<bundle摘要>目录，
仅检查完整终态后用相同预备对象复核，再接原账户捕获和原cycle决策。避免信号后
重复复制整份策略资料；已有工作目录禁止重新采集，成功/失败原始记录均保留，
目录数量和总字节有界。后续既有精确批准消费者同步支持该runtime格式，全部
风险/批准/订单领取检查保持，未自动生成policy或批准。

先复现缺少新观察入口，随后用原观察器+模拟网络验证无预备即拒绝、同一归档
生成新前瞻记录、计划时间不变、重复观察拒绝。再接原cycle和空执行存储，产生
待批准入场草稿、原订单库仍空。控制器真实管道验证新模式先warmup再观察单帧。
六组联合19通过，追加新观察到cycle测试通过；静态/卫生/治理通过。
网络、生产身份和systemd是测试替身，尚未测真实完整3秒路径或部署；执行侧
公共WebSocket连通/服务出站权限、受控策略资料安装、独立范围批准与累计预算
仍需完成。不是持续多轮真实交易或整厂闭环完成。

## 2026-09-29：控制器复用一条有界执行会话

控制器新增显式ticks（1至4，默认原单次行为）。多次模式只启动一个原executor
子进程，发送一次warmup并核对回执，随后逐次请求/响应，不向原单帧reader堆叠
请求；仅最后一帧等待子进程结束。原systemd总时长按有限帧数增加，每帧仍有
150秒上界；任意错误终止，不重启或重发。预备策略摘要绑定后续非恢复决策。
每帧前后重核原policy/registry及固定runtime，配置改变停止，已保存引用保留。

真实子进程管道回归先复现旧run不接受ticks，然后验证一次启动、warmup、两次
决策、正常进程退出与相同结果只留存一次。追加第二次前配置变化，控制器拒绝
继续并关闭子进程，前一结果仍在。六组17通过，静态/卫生/治理通过；systemd与
生产配置为测试替身，未启动真实服务或执行交易。

这是同一既有观察资料下的连续账户/策略决策会话，尚非新信号前预备：当前入口
仍先要求strategy-runtime指向已有完整观察，第一次预备17秒仍可能错过入场。
下一必要交接是独立的策略准备配置/资料先到执行侧并确认预备完成，随后原观察
流程才开始新市场监听；新观察产生后送入已存活会话。不可把当前两次回调当成
已完成该阶段顺序、常驻研究调度或连续独立批准。未部署。

## 2026-09-29：真实研究窗口与执行侧连续会话

只读核对两份真实研究的留存定义：entry_timeout_ms均3000，max_quote_age_ms均5000，
holding_ms均30000。此前50/20ms是合成夹具，不能据此推断实际策略也要求50ms。
对第二份原件的逐字节副本执行完整PreparedStrategy核验，实测16994.279ms；随后
三次原字节复核16.033/16.268/16.612ms。原件未变、未采集新信号或访问账户；说明
真实归档更需要在监听前准备，尚不能证明网络/签发的完整3秒路径可用。
证据`/home/kerstker/quantos-dev-output/gold-real-strategy-preparation-20260929-m_q8dwgr/summary.json`。

worker新增GOLD_STRATEGY_WARMUP_INPUT_V1，仅固定source_commit与trial_manifest_sha256，
先检查执行侧文件归属并完整核验，再保留进程内对象。预备阶段不采集账户、生成
任务或授予资格。serve通过原严格单帧请求/响应协议处理最多60帧，首次预备后
后续原观察请求复用同一策略；换策略摘要或重复预备拒绝，错误终止会话、不重试。
不从外部JSON恢复“已核验”缓存，重启后必须重做预备。每帧身份/源码/存储与原
资料检查保持。原CLI默认一帧行为保持，连续会话需显式frames参数和逐次响应。

测试用真实OS双向管道完成预备→决策→决策，核对构造只一次、预备不采集账户、
同决策不重复留存；五组9通过、静态与卫生通过。生产身份/网络为测试替身。
控制器仍按单次子进程运行，尚未接连续会话或服务安装；不能声称常驻已完成。
下一主线是控制端维持该会话、在新观察出现前预备，并接独立策略范围批准与
同一预算下逐次决定，保持原精确决策policy和人工门不被隐式放宽。

## 2026-09-29：固定研究核验前移与真实计时改善

PreparedStrategy是进程内固定资料对象，构造必须执行原完整研究核验，不能从
外部传入的JSON或“通过”标志恢复。保存六份原归档字节和定义；每次使用仍读取
全部归档并逐字节核对、拒绝活动数据库旁路和本模块源码改变，返回新解码副本。
原forward review仍重放前瞻原始数据并核对注册/源码/终态，未缓存新信号或批准。

observe_and_publish在监听前准备该对象，原observe、复核、发布副本和最终校验
共同复用；entry/exit/cycle支持同一进程上下文。worker在账户采集前准备，后续
决策和返回前复核复用，原实时deadline检查保持。worker目前仍是单次进程，启动
核验不等于已在信号产生前完成；真正长驻进程与跨进程批准仍未实现。

回归验证warm路径与原完整复核结果相同、不重复执行完整核验、调用者改返回值
不能改固定定义、归档字节改变即拒绝。七组37通过，静态通过。独立本机计时：
准备125.770ms；原本地流程三次368.341–387.923ms，复用后三次78.690–88.000ms。
复用后复核6.513–6.689ms，资料发布54.801–64.495ms，入场16.741–17.200ms。
证据`/home/kerstker/quantos-dev-output/gold-prepared-latency-20260929-tt841lg0/summary.json`。
这些仍是合成输入且未计网络/批准，不满足该夹具50ms入场窗，不能标实时可执行。
下一步需将文件发布移出即时路径，保留固定定义和原始观察审计；未部署、未全量验收。

## 2026-09-29：真实本地计时揭示即时执行缺口

使用明确合成归档/观察、真实perf_counter_ns计时三次原复核→发布→入场准备，
总耗时457.776、367.740、362.222ms；其中复核55.151–146.463ms、发布240.797–
246.289ms、入场66.274–70.447ms。该夹具entry_timeout=50ms、max_quote_age=20ms，
所以即使没有网络/签发/人工等待，这条文件交接也不能满足它的即时执行窗口。
这只是本机合成输入测量，不是所有实际策略/负载的延迟上界或交易所性能数据。
原证据：`/home/kerstker/quantos-dev-output/gold-strategy-latency-20260929-vmzbn3la/summary.json`。

追加回归先证明worker在持久化期间跨过期限仍返回成功，后修正在复核后留存前
以及留存后返回前重新读取当前时钟并验证原约束。失效返回明确DECISION_DEADLINE
或HANDOFF_DEADLINE，不返回可继续交接的决策；已写入原件保留，期限不重写。
五组联合10通过、静态与卫生通过；整厂仍未完成，未部署。

下一步架构优先级因此是：将完整研究核验/资料准备和策略范围独立批准前移到
监听新信号之前，常驻进程持有已验证的固定定义和原累计预算；即时路径只做
新信号、当前账户/报价、独立逐次约束及原发送检查。必须新建明确的受保护
策略范围批准协议，不能把当前“精确决策摘要”policy偷偷放宽为任意决策、换ID
重置预算或延长已注册策略期限。现有文件包保留作审计/离线交接，不宣称其为
已经满足毫秒级期限的实时执行路径。实际运行前还需测完整网络/签发与持久化耗时。

## 2026-09-29：原观察自动生成执行侧资料包

新增gold_strategy_publish，仅复制四个观察文件及六个研究文件到worker已经消费的
固定摘要目录，并生成strategy-runtime.json与最终清单。原资料和副本均通过
原forward review，复制前后逐字节复核来源，活动SQLite旁路文件拒绝；完成清单
最后写入，文件/目录fsync。已完成相同包可复核复用，不同目标/内容和不完整包
拒绝覆盖。未列出的其他文件不复制，不修改原件，不安装到执行状态目录。

observe_and_publish及可选plan入口调用原bounded observe，只有COMPLETE_OBSERVATION
才自动发布；失败观察保留原结果，无资料包。原worker管道测试已改为直接消费
发布器输出，未手工构造复制目录。目标commit是指定部署目标，明确标记
source_commit_binding_verified=false，仍必须由保护侧verify_source核验实际版本；
本地清单/摘要不是独立来源认证、策略准入或安装授权。

六组联合18通过；随后补充未完整观察不发布回归，发布器最终四项通过，静态/
卫生/治理通过。输入为合成数据，未真实联网采集或运行账户。此处完成本地发布
交接，不关闭受控安装、常驻计划、独立连续批准、跨批准预算和真实Demo缺口。
特别是合成链固定了时钟，尚未证明真实采集/复核/发布/批准延迟能够满足策略
报价有效期；不得延长原策略期限来伪造真实执行可用性。

## 2026-09-29：留存决策到原已批准任务消费者

新增gold_strategy_dispatch单次入口：读取root控制的strategy-task-approval.json，
只含source_commit/result_sha256/issuer_policy_sha256/kill_switch及schema。
其引用不是签名或资格来源；必须匹配当前已独立安装的原issuer policy，精确约束
摘要仍由原task_check校验。固定profile沿用/etc/quantos/demo-issuer/profile.json。
消费者核对当前strategy-runtime、保存的worker结果与决策摘要、账户、源码及原
草稿约束，复用gold_strategy_request创建任务数据；不重新生成决策或改写批准。

复用原demo_portfolio_checkpoint.materialize的任务写入/预算/领取屏障，适配参数
仅提供该函数实际消费的source_commit。任务保持原canonical字节，无换ID或重置
领取记录；默认只materialize，显式execute才交给原demo_task.run，后续原采集、
风险/签发/发送前校验完整保留。消费前再次核对配置未变，失败不自动重发。

隔离回归使用明确合成批准，原task claim持久化后注入返回丢失；第二次消费
QUERY_ONLY且执行回调仅一次。改变已声明约束后拒绝，原dispatch订单存储仍空。
controller/worker/原任务/签发/合成整链及原materializer八组联合38通过；
静态、卫生与治理通过。真实系统身份、签发服务、网络和发单未操作。
该入口不负责生成独立批准、累计跨批准预算或安装文件；连续准入、发布与定时
调度仍未完成。所有本次改动在28f7279b8之后，未部署或全量验收。

## 2026-09-29：执行侧策略工作进程与控制器留存

新增gold_strategy_worker：沿用固定executor身份、私有管道、原账户捕获、原存储
及原preparation lease。只消费executor根目录下strategy-observations/<result摘要>
和strategy-trials/<manifest摘要>；检查目录/文件归属、冻结试验无SQLite旁路文件，
原forward review复核全部策略绑定。输入错误在账户采集前拒绝，账户必须匹配固定身份。
一次决策调用gold_strategy_cycle；原_retain先持久化，之后才返回决策摘要与内容。
重复相同输入/时间的结果复用同一文件，不生成任务、批准或订单。

新增gold_strategy_controller单次tick：读取受保护strategy-runtime.json，持有原
controller lease，通过原executor子进程入口调用worker，核对来源、账户、配置和
决策摘要，保存控制端引用。配置中仅有schema/source_commit/observation_sha256/
trial_manifest_sha256，不接受任意路径或凭证。控制器仍受既有有效运行配置约束。

先由缺失入口失败，再以合成归档和账户、真实OS管道验证工作进程落盘/重复调用/
输入篡改前置拒绝，以及实际controller.run正确持久化和错配拒绝；systemd、
生产身份/配置和网络是明确测试替身。联合原任务/控制器lease/整链开平仓七组38通过，
E9/F821、仓库卫生及治理通过。没有调用真实账户、安装配置或启动服务。
仍需资料受控发布、定时调度交付、独立决定准入与累计预算、批准结果到原任务的
持续交接；本单次worker/controller开发不能标成整厂常驻或真实Demo完成。

## 2026-09-29：原执行状态驱动下一步策略决策

新增gold_strategy_cycle.prepare作为一次只读调度决策：先读取原dispatch/recovery/OMS
恢复屏障，再复核原信号；未处理的发送返回RECOVERY_REQUIRED，不根据空仓快照重开。
以原稳定entry client ID定位本信号是否已消耗，沿用原持仓重建和账户复核；
持仓阶段交给原策略退出草稿，无退出条件返回HOLDING；本信号已终态且空仓返回
STRATEGY_EPISODE_SETTLED，包括未成交IOC，不为同信号自动重试。其他仓位不自动接管。
入场身份计算由入场、退出和调度共同复用，未增加订单/资金或cycle账本。

整链合成回归改为由新入口选择初次入场、发送后恢复、成交后退出及平仓后终态。
再次调用从原存储重建相同终态，无内存完成标志；无信号不写入原执行存储。
六组联合65通过，E9/F821、仓库卫生与治理714文件零违规通过。
此入口返回待批准草稿和精确约束，不生成policy、签发或发送；仍缺受保护运行配置、
worker/常驻调度接线、决定留存和独立批准服务。不能把该函数称为已部署常驻策略。
本次改动在28f7279b8之后，未包含于其固定版本或1048完整验收。

## 2026-09-29：入场报价时钟与联合版本

入场草稿曾直接将交易所book时间与本地信号可用时间比较。新增正负100ms
场所偏移回归，原实现两项均拒绝有效报价；修正为与退出相同的两侧观测
时钟区间，使用报价可能的最早本地时间检查信号之后和到期边界，不选中点。
报价早于信号仍拒绝。该区间基于当前捕获的偏移观测，不提供独立时钟认证。
入场、退出、原签发约束、完整合成开平仓及后台任务五组34项通过。

以干净3ff2f15b42f7ea3f658750820c7a7c1385fd7f8c为基线，显式整合24文件，
在独立源码重新生成依赖图。额外的三个demo_feedback_upgrade维护工具未带入，
上述策略模块无其引用；不把主工作区其他未提交改动混入策略版本。
整合位置：`/home/kerstker/quantos-dev-output/gold-strategy-integration-20260929-f46ouezm`。
独立源码十五组联合182项通过，E9/F821通过；日志与JUnit留存在整合目录。
范围为TARGETED_INCREMENT_VERIFIED，未运行本版canonical全量验收。
本增量仍待常驻策略计划、独立连续批准与累计预算、实际Demo反馈及部署。

## 2026-09-29：策略任务进入原后台交接

gold_strategy_request.task_from_draft生成原后台可消费的
DEMO_GOLD_STRATEGY_REQUEST_TASK_V1，完整保留已独立绑定的约束、声明和订单，
任务时间窗口取批准范围与草稿窗口的交集。只产生数据，不安装policy或任务。
原task_check、executor准备器、root准备结果接收和原supervisor已支持该明确
格式；旧配置仍不能接受新请求，约束被删除/更改不能作为原声明交接通过。

执行器仍由原入口取得新账户/市场观察；采集后重新核对任务及策略期限，过期
不产生request。原root任务领取/请求领取和失败恢复机制不变，没有第二套调度账本。
新增真实管道协议回归验证准备器输出与root接收；故障回归验证原supervisor
领取后子进程启动失败，第二次调用不重复启动。

先复现新task不被接受、准备交接只认旧schema、supervisor只认旧schema三个断点，
逐项修复后六组35通过，E9/F821通过。外部网络、生产systemd、真实凭证均未调用。
此处只接通既有后台入口；还没有常驻策略计划、独立登记/后续批准服务、安装或
真实Demo运行。已有精确决策policy不可原地改绑，不能靠换policy_id无限重置预算。

## 2026-09-29：跨模块完整合成开平仓与资金复核

`tests/live/test_gold_strategy_roundtrip.py`把原研究归档/前瞻信号、入场草稿、
原风险请求、隔离签名会话、模拟ACK、私有成交导入、原OMS/唯一V2账本重建、
真实规则退出草稿、第二份隔离决策批准、减仓成交导入和终态账户复核串接起来。
模型回答、行情、场所规则、凭证/签名材料及成交全部是明确的合成测试输入；
没有真实模型调用或交易所交易。ACK后不假设成交，只有原回执投影才更新仓位。
最终两笔成交、零仓位、原费用账本和合成账户余额相符；额外0.01余额差异拒绝，
cash_reconciled/source_verified仍为false，不能作为盈利或真实Demo证据。

联调暴露策略开仓可能通过、后续原保守减仓容量却不够的问题。新增原
demo_pre_submit._strategy_exit_capacity，以原观察的场所卖价上限检查整个开平仓
名义金额预算，原提案、独立issuer复核、最终发送预检均消费；不另建容量账本，
不放宽原减仓规则。反例先DID NOT RAISE，修复后拒绝且无订单持久化。

测试还确认原policy_id不可重新绑定：退出使用另一个明确的合成审批配置，
没有原地改写开仓授权或生产配置。这揭示持续自动登记仍须提供独立、可追踪的
后续决策批准与累计预算管理；测试中的第二份配置不等于这条运行服务已实现。
七组联合105通过，E9/F821、治理714文件0违规通过。未部署，不在1048验收中。

## 2026-09-29：策略草稿接原提案、签发和发送前约束

新增gold_strategy_request把入场/退出草稿转换为原风险请求；核对草稿摘要、
原账户观察、确切订单及本地有效窗口。DEMO_GOLD_STRATEGY_SESSION_INPUT_V1
经原decode/proposal/prepare进入原执行会话，不新增订单、成交或现金存储。
约束包含策略定义、完整草稿决策、确切订单摘要、起止时间，绑定到原签名context。

独立签发采用DEMO_GOLD_STRATEGY_ISSUER_POLICY_V1，其受保护bindings必须预先
固定整份execution_constraints_sha256。旧黄金功能测试policy拒绝策略约束；
新policy也拒绝无约束的旧请求。issuer沿用原风险/账户/mandate/预算复核，并将
签名到期截在策略窗口内。原prepare和最终executor预检再次检查约束摘要、订单
及时间，包含读取凭证之后、真正HTTP发送之前的原二次检查。

隔离新路径先复现6项缺失接口失败；实现后覆盖有效签名至一次mock ACK、
签名后读取合成凭证期间过期且HTTP调用为零、旧scope拒绝、订单/定义/时间
替换拒绝，以及草稿进入原RISK_CHECKS_PASSED。未使用真实凭证或发送交易。
十一个相关组首跑175通过、依赖图过期1失败；保留该失败并用原生成器更新依赖图。
更新后依赖图与新策略会话两组17项通过；治理714文件0违规、E9/F821与卫生检查通过。

这是“原协议能表达并执行已独立批准的具体策略决策约束”的工程进展，
不是研究独立准入已完成。没有代码自动生成/安装新issuer policy，没有扩展
既有人工授权或把AI设为批准者；当前现场的功能测试配置不具有新scope。
持续策略登记、独立核验至签发配置的交接、驻留调度和真实黄金开仓/退出/对账
仍未完成，未部署；这些新增源码不能借用1048已通过的完整发行验收。

## 2026-09-29：原持仓到退出草稿

新增`tools.gold_strategy_exit.prepare`消费前瞻决策复核、原dispatch/recovery/OMS，
调用原load_context和validate_origin重建持仓并核对当前账户。只接受同账户、
同策略定义/决策派生的开仓客户编号及其原reservation；持仓必须为正且不超过
原策略数量。未知开仓起点或不同策略轮次拒绝。平仓返回FLAT、无触发返回HOLDING。

回测的止损/目标/期限条件抽出为原gold_signal.exit_condition，两条路径复用。
退出期限用同一场所时钟的首次成交与当前bid时间比较；用账户捕获的两侧clock
观测保守计算报价本地时间区间，过期或未来报价拒绝，不把场所时间直接当本地时间。
触发时生成剩余数量的SELL/LIMIT/IOC/reduceOnly草稿，复用原场所规则与账户上限。
相同账本状态反复生成同一客户编号；原账本确认部分减仓后以新剩余状态生成后续编号。

新草稿测试使用显式合成review/context和边界替身；原持仓重建另由整链回归验证，
不能合称真实黄金成交到退出的端到端证据。尚未实现策略授权、签发后的时效/持仓
复核、发送与恢复编排，也没有服务调用此入口。risk_status仍NOT_RUN。
源码中gold_signal变更意味着旧观察的源码摘要不再匹配当前开发目录；不得改写
旧登记或借用旧观察充当新版本执行依据。1048发行验收和旧真实观察原件均未修改。

## 2026-09-29：原账本提供当前持仓开仓轮次

`live.execution.demo_position_context.load_context`在原受保护成交/OMS前缀复核中，
逐笔沿用原V2账本推进，增加position_episode派生字段。零仓变为持仓时保留
首次成交的场所时间、原客户订单、receipt摘要和reservation/plan来源；同向后续
成交与部分减仓保持原起点，全部平仓清空，反向穿零重新起算。没有第二套数量账本。
有原时钟观测才提供本地时间区间；否则明确VENUE_TIME_ONLY，本地区间为null。
账户恢复前缀已有持仓但无首次成交分解时标START_UNAVAILABLE，不能从恢复时刻计时。

先由三个现有整链回归复现缺失字段；实现后持仓/报告恢复/跨日检查点/黄金请求
四组57项通过。追加两笔部分开仓回归核对首次成交、最终数量和原钱包不变。
该字段仍为reported事实，source_verified和cash_reconciled不变；原状态存储不写入。
尚未实现依据此字段的实际止损/止盈/超时退出控制器，也未部署或纳入1048验收。
以后发送前必须从原账本重建并复核轮次归属，不能信任调用者自填的context。

## 2026-09-29：信号派生Demo入场草稿

新增`tools.gold_strategy_entry.prepare`先调用前瞻决策复核，再将唯一有效信号、
原数量/退出参数与新Demo账户观察绑定。无信号返回NO_ENTRY_SIGNAL，不生成订单；
信号过期/未来、报价早于信号、非空仓/未决账户、价格超出退出区域和不满足原场所
规则时拒绝。复用原订单/场所校验，数量不按最小名义金额自动放大。
草稿采用当前卖一LIMIT IOC，按账户/策略/决策稳定生成原QD格式客户订单号，
有效期同时受信号入场期限和原报价时效限制；退出计划完整保留。

输出GOLD_DEMO_STRATEGY_ENTRY_DRAFT_V1，原session decoder明确拒绝直接消费。
尚无策略专用签发、最终发送时的期限核验、真实持仓退出、原dispatch去重消费
或队列/页面接线；risk_status=NOT_RUN、submission_enabled=false。
该草稿只是交接材料，不能当作原功能测试授权下的可发单请求。尚未部署。
新增反例先缺失接口失败，随后四组29项通过；保留合成测试与真实Demo的区别。

## 2026-09-29：前瞻决策交接复核消费者

新增`tools.gold_forward_review`读取原策略归档及前瞻观察四文件，复核源码、
完整策略、登记、原始逐笔数据和结果摘要；按每次决策当时可见的数据推进原
ForwardObservation/GoldSignal，逐项比较保存的窗口和决策，而非只相信摘要。
完整观察的信号携带实际退出/数量参数和可用时间，缺口观察不交出信号。
命令为`python -m tools.gold_forward_review --observation-dir <观察目录> --strategy-trial <原试验目录>`。

新增反例先因缺失消费者失败；实现后四组33项通过，包含无卖压不产生信号、
改写决策或原始交易且重算摘要仍拒绝、无绑定拒绝和错过决策期限不交出信号。
E9/F821与治理扫描通过。用此前真实公开观察原件复核仍为INPUT_INCOMPLETE，
signals为空；不回填历史窗口、不连接网络、不变更原归档或账户。

输出仅为RETAINED_DECISION_REVIEW_ONLY，不证明独立时间/数据来源、当前可交易性
或经济准入。尚未接到工厂周期调度、受保护策略登记、Demo提案或真实OMS退出。
该开发增量不在冻结1048验收中，不能据此关闭F2或宣称策略自动交易完成。

## 直接检查结论

`tools/demo_operator_setup.py::opening_order`从当前卖一价和固定偏移计算限价，
从交易所最小名义金额计算数量，返回BUY/LIMIT/IOC。它不读取候选的吸收阈值、
确认窗口、价格区域、止损或目标价格。`assemble`把此订单放入初始任务。

带`research_identity`的黄金spec仅把研究派生的strategy_id绑定到profile、
attestation、issuer policy和反馈归属。原身份包含trial/input/report摘要，
但没有可执行策略参数；`tools/demo_functional_cycle.py`沿用
OPEN_IOC→REDUCE_ONLY_IOC→RECONCILE的功能测试流程。
其批准用途明确为DEMO_GOLD_FUNCTIONAL_TEST_ONLY，不能将该路径描述为
“AI候选在Demo中按研究规则运行”。即便完成签发、成交、对账、反馈，
它也只能证明带研究身份关联的功能测试链。

真实研究的入场/退出行为目前在`services/research/gold_v2_strategy.py`中，
消费者是`tools/gold_cost_replay.py`及原V2模拟器。模块明确只接受
SimulationContext；不可把真实账户状态伪装成模拟上下文来接单。
`GoldSignal`已有连续窗口、确认、过期及决策链语义，应复用这些规则，
不能再建一套与回测不同的黄金信号算法。

此前“下一步完成登记即可贯通”的判断不充分：实际策略决策交接尚未实现。
现有功能测试无需改成策略模式；其原用途、批准范围和历史证据保留。

## 主线缺口与实现约束

1. 从已审查试验导出可复核的完整策略定义，绑定候选阈值、区域生成配方、
   窗口/确认、退出规则、数量政策和原输入/报告。研究编号本身不是策略定义。
2. 在新数据到来前冻结本轮定义与区域登记规则。用真实可用时间推进原信号状态，
   无信号输出明确等待/淘汰；数据过期或有缺口禁止产生新开仓意图。
   不能把旧回测中的已确认信号当作当前可交易信号。
3. 原策略意图经独立风险/签发消费，订单数量、精度、报价和账户上下文仍由
   原执行边界检查。签发材料必须绑定本次策略定义和新决策，功能测试批准
   不能借用到策略模式，也不能只换strategy_id复用固定买单。
4. 退出意图消费原OMS已确认持仓/成交与新报价，遵守研究中的止损、目标和
   持有期语义。部分成交、重启、未知订单和数据中断继续由原恢复机制处理；
   不增设第二套现金或持仓账本。
5. 同一策略定义、决策、订单、成交和费用贯穿反馈。下一轮模型应收到实际
   策略行为的诊断；功能测试成交不能作为该候选策略的收益或有效性标签。

## 验收必须覆盖的行为

- 同一原规则在无信号的新窗口上不产生开仓，在合法确认后才产生一个意图。
- 候选阈值/确认/区域改变会按规则改变决策，不仅改变名字；原材料篡改拒绝。
- 过期、重复、缺口、重启与部分成交不造成重复开仓，退出绑定真实剩余持仓。
- 签发范围或策略定义不匹配时拒绝；不生成AI自签、人工身份替身或经济资格。
- 隔离流程、真实Demo执行、经济准入分别留证，只有真实策略链才能关闭F2/F3。

本记录为代码审计和后续实现要求，没有修改现场、账户权限或冻结1048版本。
当前1048验收及价格反馈研究继续；后续开发不能借用其尚未完成的发行结论。

## 首个实现：原研究队列保留实际策略参数

`tools.gold_execution_identity.retain_strategy_definition`从原TrialLedger核验、
研究来源和选定输入派生GOLD_RESEARCH_STRATEGY_DEFINITION_V1，保留实际入场规则、
历史区域、数量、止损、目标、入场等待、持有期与报价时效参数，以及完整原研究身份。
参数来自原输入字节而不是根据候选名字另行猜测。生成前后重新核对原归档身份，
写入原trial下strategy-definition.json；已存在内容必须相同，拒绝静默替换。

原gold_factory_research完成成本报告核对后自动保留该定义；恢复调用沿用同一内容，
原result.json和外部返回合同不改。定义明确prospective_registration_required=true，
历史区域不重设时间、不当作当前信号；未接执行器、未注册受保护策略或增加权限。

新增两个反例先因缺失导出接口失败；实现后原队列双轮检查实际候选参数及恢复不变，
身份/工厂队列/AI恢复/进程崩溃四组27项通过，依赖图9项通过，治理713文件0违规，
修改文件E9/F821通过。Starlette弃用警告保留。修改仅在开发目录，不在固定1048验收中。
下一项仍是前瞻定义登记与新数据决策消费者；本增量不能关闭策略执行缺口。

真实归档复核已完成：复制已结束a247双批的六份原归档文件到独立审查目录，
两份定义重复导出均一致；候选0/1保留各自confirm_windows=2/3，其余原参数不改。
逐文件确认原input/report/manifest/ledger/origin/request保持原字节。
摘要位于`/home/kerstker/quantos-dev-output/gold-strategy-definition-review-20260929-m8fkb3kf/summary.json`，
状态ARCHIVED_STRATEGY_DEFINITIONS_RECHECKED。仅写审查副本，无实验重跑、账户操作或准入。

## 新数据消费者：绑定原策略的前瞻信号观察

原`tools.gold_forward_observe.observe`新增可选strategy_trial，CLI对应
`--strategy-trial`。先重读归档并核对实际定义，再取得登记时间；研究必须在登记前
完成，未来计划的原规则与价格上下界必须逐项匹配。新的区域登记/到期时间由明确
前瞻计划给出，不改写历史区域；目前只支持保持原价格水平的观察，不支持自动重设区域。
原ForwardObservation验证未来窗口、时间预算和时效，原GoldSignal消费新收到的逐笔数据。

完整策略定义在联网前进入registration.json，结果绑定登记摘要和定义摘要。
退出参数仅保留，尚未驱动账户持仓退出；输出为研究信号，没有构造或提交订单。
该入口尚未接入周期工厂调度及前端，也不是受保护Demo策略登记。

四个新回归先因缺失参数失败；后补同规则、不同卖压数据的对照，证明满足条件才产生
LONG_RESEARCH_SIGNAL，无卖压则无信号。参数/区域替换及未来研究在联网前拒绝。
前瞻工具/原引擎/策略定义/依赖图联合30项通过，E9/F821通过。

真实公开观察已执行一次，输出位于
`/home/kerstker/quantos-dev-output/gold-bound-forward-real-20260929-f2ohi0ck`。
使用第二轮真实候选1的原定义，显式登记新的固定价格区域观察窗口，真实时钟与新WebSocket数据；
没有回放旧成交充当新数据。首个实际决策比计划晚2ms，原因MISSING_WINDOW_COVERAGE，
decision=null、终态INPUT_INCOMPLETE。原六份归档摘要复核未变。
证明策略到观察入口的绑定和缺口拒绝，不证明真实信号完成、策略有效性或执行闭环。

首窗缺口进一步只读复核：共190个收到的包，187个前置包、3个窗口内包、0个右边界包。
已收到交易的传输延迟min/median/max为97/105/254ms，决策调度晚2ms。
因此本次失败具体是当时无法取得完整窗口右边界，不能直接归因代理或调度性能。
未收到的右边界交易何时发生/到达仍未知；不能据已收到包的低延迟排除后续网络问题。
原四文件摘要绑定的coverage-diagnostic.json保存在同输出根，原文件未变。
下一轮窗口/等待期限必须在采集前根据既有可用性诊断设计，旧窗口不回填为成功。

## 独立增量固定

以上六个代码/测试文件和本记录已从固定1048基线整理到
`/home/kerstker/quantos-dev-output/gold-strategy-forward-increment-20260929-y2jvy267/source`，
分支codex/gold-strategy-forward，提交3ff2f15b42f7ea3f658750820c7a7c1385fd7f8c。
逐文件基线/新摘要保存在scope.json；独立七组54项通过，暂存后实际pre-commit通过，
提交后工作树干净、文件摘要一致。validation.json明确TARGETED_INCREMENT_VERIFIED，
本增量未运行完整canonical、未安装、未授权发单。原1048完整验收和真实价格反馈运行
不修改、不重启，其结论不能自动覆盖本增量。
# 同一策略交易到资金反馈的交接复核（2026-09-29）

本次扩展原 `test_gold_strategy_roundtrip.py`，不再借用固定买卖功能测试作为
策略资金反馈证据。原归档策略的新信号、签名入场、成交投影、目标退出与归零之后，
同一组 prepared batches 进入原 `build_roundtrip_feedback`：手续费
0.0045045 + 0.00477、已实现盈亏 0.59、钱包 5000 → 5000.5807255，
原现金账本残差为零。反馈保持原研究身份，经原发布器重复发布后，调度器读取内容一致；
额外无法解释的 0.01 钱包变化被拒绝。最终相关两项通过（6.80 秒）。

这些数字全部来自合成交易输入，不能作为收益或真实 Demo 证据。没有改动运行逻辑、
批准、生产状态或在途 81c0 源码。尚未在此链运行下一轮研究任务。

**下一功能缺口已定位：** `tools/demo_functional_worker.py::export_feedback`
当前只接受 `resident-recovery/functional-cycles`，依赖其 cycle_scope、cash 目录，
且限定 2–3 批次。策略 worker 的持仓归零并不自动创建这些功能周期材料。
因此，现金计算与研究端交接可复用，但仍须实现策略周期的原件选择、现金窗口采集、
受控导出及后续研究调度；不能仅将 STRATEGY_EPISODE_SETTLED 标为全闭环。
# 2026-09-29 当前决策命令行交接修复

`gold_strategy_dispatch.main` 原来解析 `--result-sha256` 后没有传给run，
使显式目标退回原历史扫描。现在将其完整传递。新增真实CLI路径进入原
dispatcher/task claim的隔离回归先失败于CONFIGURATION，修复后联合tick
25项通过：同一目标直接读取、等待当前批准、领取后丢响应仅查询、下一
批准消费及预算组变化拒绝都通过原消费者。没有生成独立批准或提交实际
Demo订单。此增量不在正在验收的b81源码中，不能借用其完整验收。
