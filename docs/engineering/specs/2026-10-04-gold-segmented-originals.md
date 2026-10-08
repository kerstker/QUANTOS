# 黄金公共原件分段与同一登记账户

承接[因果规则/资金费切换](2026-10-04-causal-account-environments.md)，继续
[工厂十环节闭环](../plans/2026-09-28-ai-factory-closed-loop.md)与
[DGX D2/S1](../plans/2026-09-05-dgx-engine-foundation-roadmap-review.md)。
本增量接通原 Gold 生产者与登记队列；完整系统交付目标保持进行中。

## 原输入与唯一执行路径

QUANTOS_GOLD_SEGMENTED_INPUT_V1 在同一次原 invocation 接收主段及最多63份
QUANTOS_GOLD_CONTINUATION_SEGMENT_V1。每份均保留原公共响应/可知时钟和六个
原 journal 文件。计划、warmup 与 warmup receipt 字节一致；messages 与
decisions 为不可改写的追加前缀。按原 restore 重建游标，并沿原
prepare_public_replay 核验每段市场、场所数量、规则、容量、报价与资金费。
二段不能重设本金、数量、费率、策略或源码；任意路径和代码不是输入。

固定请求 plan 新增 segmented_input(schema,segments_sha256)，只读取原请求
目录下固定 segments.json。全文 SHA 先核对，随后沿原 ASCII 输入、native plan、
候选定义和试验登记绑定全部内容。新中立合同保持原16MB输入/报告预算和
100000输入节点限额；没有增加任意载入器，也没有放大原长期读取预算。

三候选各自继续一个原账户；可选第四共享净目标组合继续原共享账户。原无订单
现金基准仍只运行一次，不新增第五试验。实际调用仍为原 _compare_bars、
GoldBarV2Strategy、GoldResearchLossControl、GoldNetTargetPortfolio 与 Decimal
账本；仅共享原 incremental bar/session 交接代码，不另算现金或策略收益。

## 因果边界

后续资料在其第一份原报价可知时切换快照。共同报价必须与旧原件完全相同，
共同尾部归入新阶段并只执行一次。移交在任何账户执行前确定，不能靠结果选择
边界；旧行情、K线可用时间和资金费事实不能被删除、改写或重复执行。
没有共同报价时从下一份真实报价开始；超过原30秒缺口或缺新边界报价拒绝，
不补行情/资金费。完整原件仍保留在 input 与分段 manifest，记录实际保留帧、
全部声明边界与完整 journal 追加事实。新 K线收据不得回填已冻结的旧输入区间；
共同尾部中的已知 bar 决策时钟仍保留，不制造 mark 或 quote。

合约/订单规则仍要求覆盖连续、新快照可知、标的与单位一致、库存可精确表达。
边界取消原挂单，保留库存、成本基准、现金、原本金、费用/资金费、容量消费与
累计损失停止。较旧的 bootstrap 不能覆盖后到且矛盾的场所 symbol 规则观察；
新 mark 源时钟倒退、共同报价冲突或遗漏拒绝。

原 Forecast 与 RollingForecast 的确切受支持类型现在可跨因果阶段。原构造、
覆盖、预测接收顺序、名义结算窗口与实际收据暂停校验均保持；每段原名义/实际
事件与可知时钟沿原执行路径处理。含预测的环境政策为
CAUSAL_ENVIRONMENT_CANCEL_PENDING_RETAIN_ACCOUNT_V2；全部原历史预测假设保留，
不能因后来切换到已知政策而删除 FORECAST_TIMING_BOUND_UNVERIFIED。原已知政策
V1 标记、固定周期输出及旧 checkpoint 语义保持。新 checkpoint 继续绑定全部
原输入并复算，不载入任意保存余额。

原冷读统计按各段 capture 身份核对资金费 response/hash/receipt 与实际账本；
共同结算只保留一次、矛盾拒绝。事前声明消费最终完整 journal，不能只验证主段
就跳过后续收盘。原登记库、归档四原件、只读完整 cut、镜像和恢复复用原路径。
恢复不得再调用 producer；PASSED 只指原技术计算完成，经济/晋级/下单仍false。
控制台 payload 未改，本增量没有新的前端验收声明。

## 验证

原10项分段回归均先失败：原生产者不接受该输入。第一轮旧/新相关23项通过。
预测入口两项先失败，修后与原 epoch/forecast 联合41项通过。真实队列首轮3通过/
2声明失败；原 observe 测试共享时钟停在第一份 bar 收据，明确把合成时钟移到
完整原输入后，生产时间门不改。另两次资金费夹具失败分别为未到名义时刻就
改 forecast、历史间隔留下遗漏结算；修合成资料，不放松原校验。
新跨段 mark 倒退真实先失败，补来源水位验证后通过。资金费早于下一新报价的
共同边界回归先失败，接原共同报价单次交接后通过。各红日志和原输入保留。
原核心联合67项、共同边界后48项分别通过，均原driver终态且全Python前后相同；
共同尾部遗漏报价的反例另先失败，补完整原报价/资金费事实保留后修复。
最后在同一源码重新运行完整 execution 树、原队列/登记/Gold/声明/统计与隔离
检查935通过/335.26秒，driver335.861秒exit0、3061 Python前后字节相同。
前轮911通过/331.01秒是最后反例修正前的版本，原日志/XML与原源码摘要保留，
不把重叠组相加或称canonical完整门通过。旧Starlette提示保留。
六个新Python全Ruff与全仓F821/E9通过；八个修改Python4条既有Ruff诊断无新增。
服务图原drift沿生成器更新，20域52边无循环；卫生与diff通过。实际pre-commit、
精确提交副本DGX基线、主区前像/递归依赖整合与复核按原开发流程继续，终态
以主区PROJECT_STATE头部及本目录外部整合记录为准。

## 完整目标仍待完成

原采集程序持续生成这些不可变分段、长期有界原件/输出协议与真实30天单账户
运行仍未交付；有限合成原件不能称自然前向成熟、真实覆盖、性能或盈利。原
Gold事前窗口仍最多四bar，整段embargo不缩短。实际最大持仓信息区间、统计规模、
跨窗全局显著性、独立风险/真实成本/时间与源结果信任、锁定OOS及候选准入继续。
正式全部writer同cut停写、跨角色业务/现金恢复、最终同版ARM64/canonical全门、
获准现场安装升级恢复、五秒链与实际24小时成交—费用—资金费—现金—再研究仍须
验收。最新完整门仍旧5ae版30 PASS/1 FAIL/1 TIMEOUT。其他市场逐项接受，机构异机
延期不记PASS，盈利策略单列；完整目标active。未部署、切服务、写保护状态、
安装真实trust、触碰真实凭据或下单。
