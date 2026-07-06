# QUANTOS 风险登记册(RISK_REGISTER)

> 审计日期:2026-07-06 · P0 最先。**ACTIVE**=当前可触发;**LATENT**=现被 L2/沙盒/人工门挡住,Phase 3A 放行当天转 ACTIVE。
> 每条:描述 → 触发场景 → 后果 → 修复建议 → 验收标准。所有条目均有 file:line 代码证据支撑。

## 一句话安全结论(先说最重要的)

**当前(L2)不存在可被立即利用的 ACTIVE P0。** 8 组红队对你列的 15 个 P0 问题逐条逆向追踪代码后:风控绕过、无 kill-switch 执行路径、fail-open、重复下单、账实不一致、实/模混淆、mock 误用、密钥泄露、断线状态错误、回测不可复现、前视泄漏、非人到 APPROVED、kill-switch 无人工复位——**全部为 MITIGATED 或 LATENT**。真正的风险集中在**放行真钱当天集体转正的 LATENT 项**,其中治理面(mandate 完整性)是最关键的一条。这正是 L2 pin + 五旗帜 + Phase 3A 人工门的设计价值:把所有"放手"都挡在真钱之外。

---

## P0 风险(可致真实资金损失 / 风控绕过 / 不可恢复)

### P0-1 · mandate 完整性:无签名 JSON + 自动 --submit 调度 = 放行日的唯一门却无密码学保护 · LATENT
- **描述**:mandate 是纯 JSON,`load_mandate` 只做字段存在性 + 有限性校验(`live/governance/mandate_store.py:37`),**无签名、无多方、无哈希链**;`approved_by` 字段明确"是审计留痕、非任何权限校验"(`mandate.py:34`)。任何对 `key/` 有写权限的本机进程(**含 AI agent**)都能改限额设任意大值。同时 `scripts/register_schedules.ps1` 注册了每周一 `live_track_advance --submit` 常驻自动提交,`deploy_aum = min(资金, 0.95*mandate.max_gross)`(`live_track_advance.py:736`)——mandate 就是资金授权真源。
- **触发**:Phase 3A 放行接真实 venue + 签署 mandate + 计划任务在位;此后任何有 `key/` 写权限的进程放大 mandate,下一次调度即在新限额内自动扩仓,当轮无人工。
- **后果**:分级授权的核心承诺("系统自主执行不超过签署额度")被绕过;真钱下可致超授权杠杆/敞口。
- **现被挡住**:mainnet 适配器 L2 构造即 `raise SafetyViolation`(`binance_futures.py:78`),交易所仅 testnet/demo 零真实资本;mandate 缺失/异常仍 fail-closed 到 NEEDS_HUMAN_REVIEW。
- **修复**:①短期——mandate 加载时校验 detached 签名字段(HMAC/本机私钥),文件系统权限收紧,agent 运行环境结构性不得持有 `key/` 写权限;②根治——Phase 14 出进程签名(`services/governance/signing.py` 已备拒绝式接缝),mandate 变更须携带出进程权威签名;③放行前把"自动 --submit"作为独立 Go/No-Go 项,上线初期改人工每周执行。
- **验收**:被篡改/无签名的 mandate 被 `load_mandate` 拒绝 fail-closed;测试证明 agent 环境无 `key/` 写路径;Go/No-Go 检查清单含"mandate 逐字段人工签署 + 自动提交演练"签字。

### P0-2 · 终极信任边界(进程隔离/出进程签名)未实现,只有拒绝式 stub · LATENT
- **描述**:全库反复的同一诚实注脚——单进程内挡不住受信代码凑三个 `HUMAN` Actor、直接调 `AlphaLifecycleManager`/`KillSwitch`/`catalog._set_state`/`venue.place_order` 绕过 chokepoint。真防御(`SignedDecisionRef` + 进程隔离)是 Phase 14,当前 `NullSigningAuthority.sign()` 恒 raise、`verify_signed_ref` 恒 False(`services/governance/signing.py`),只是拒绝式制品 + 设计文档(`docs/PHASE_14_PROCESS_ISOLATION.md`)。
- **触发**:任何移出 L2、或引入未经审查的受信进程内代码。
- **后果**:受信代码路径(非 agent 路径)可在单进程内绕过所有 runtime 风控 chokepoint 直接下单——真钱下灾难级。需要有人写并合入绕过代码(受 code review + 治理扫描器约束)。
- **修复**:Phase 14 实现真出进程签名器 + 部署/进程隔离,mutating 决策须携带出进程权威签名;把 `verify_signed_ref` 接进 live 执行路径。
- **验收**:mutating 状态转换/下单需 `SignedDecisionRef` 且由独立进程签发验证;测试证明进程内伪造 `is_out_of_process=True` 的权威仍验不过。

> **说明**:P0-1/P0-2 都是"AI agent 不能绕过风控"这一保证在 build-time(code review + 扫描器)而非 runtime-密码学层面的体现。当前 L2 下 agent 路径本身走硬化的 `ResearchAgentLoop`、结构性到不了 APPROVED / reset kill-switch / 触实盘(`agent_loop.py:34`、`guardrails.py:36`、`alpha.py:192` 已核实),这两条 P0 是**受信代码**(非 agent)的 build-time 绕过面,Phase 14 收口。

---

## P1 风险(阻塞进入模拟盘 / 实盘)

### P1-1 · 验证门禁不是 live 部署的结构性前置——会动钱的路径不检查 ValidationResult/APPROVED · LATENT
- **描述**:`live/pipeline.py` 与 `tools/live_track_advance.py` 全文无 `ValidationGate`/`AlphaState`/`APPROVED`/`verdict` 任何引用(grep 已证),只过 PreTradeRiskGate + mandate + kill-switch + parity。实际部署的 crowd_contra 靠 IC 面板/干跑/现实主义(全"诊断非门禁")+ 人工判断选出,**从未经形式化验证门到 APPROVED**。
- **触发**:Phase 3A 放行日,人工选一个 strategy_id 去 live,而该策略从未取得 PASS 的 ValidationResult。
- **后果**:一个未经形式化验证(可能 overfit/泄漏/单 regime)的策略动真金,验证门在货币路径上不提供任何结构性阻断。
- **修复**:live 驱动器 preflight 加 fail-closed 前置:每个将 live 的 strategy_id 必须有在案的 PASS ValidationResult(或显式记录、可审计的人工 override),否则即便 `--submit`+已签 mandate 也拒绝。
- **验收**:`live_track_advance` 对缺 PASS 验证 artifact 的 strategy_id 结构性拒绝提交并落审计;有测试证明拒绝路径。

### P1-2 · mandate gross/net 只算当批 + 多策略共享额度超发只告警不硬停 · LATENT
- **描述**:`evaluate_mandate` 的 gross/net 累加只遍历当批订单(`live/governance/mandate.py:118`),不含账户已有敞口、也不感知同 venue 其它策略的并发敞口。`multi_strategy_mandate_alert`(`live_track_advance.py:388`)只返回提示字符串不阻断。多策略共享 mandate(或单策略多次增量)时账户总 gross/net 可达 N× 签署上限而各批仍判 AUTO。per-instrument cap 已用 account 级投影修过,gross/net 未同等处理(memory 记为 disclosed-not-fixed)。
- **触发**:Phase 3A arm 真钱后,>1 策略在同 venue 共用同一 mandate 且均 `--submit`;或单策略同周多次增量。
- **后果**:账户真实 gross/net 超签署授权而不升人工。
- **修复**:`evaluate_mandate` 增账户级敞口参数,gross/net 对(现有全量敞口 + 本批 delta)投影;跨策略共享时聚合;子限额缺失且 >1 策略共享 → 硬降 NEEDS_HUMAN_REVIEW。
- **验收**:两条策略各提交使账户 60% gross 上限的批次,第二批返回 NEEDS_HUMAN_REVIEW;单策略连续两批累计超上限时第二批升人工。

### P1-3 · client_order_id 同数量碰撞 → 静默吞记账 → 账实漂移 / 真钱重复执行 · LATENT
- **描述**:`client_order_id = decision_prefix(strategy,decided_ts,inst) + hash(side|quantity)`(`live/execution/filters.py:44`),不含 now/nonce;`decided_ts` 取自数据事件时间(`live_track_advance.py:352`),同数据两次调用恒等。同 `(strategy,decided_ts,inst,side,quantity)` 的两笔逻辑上不同的调仓增量 → **完全相同 ID**。`record_submission`/`record_fill` 均 `INSERT OR IGNORE`(`position_ledger.py:181/203`)静默吞掉第二笔记账;Binance 允许复用已成交 clientOrderId → 真实再执行、账本无痕漂移(Bybit 侧则被去重不执行还误报成功)。a359aeb 修复只把碰撞从"必然"收窄到"同数量",未消除。
- **触发**:提额后同日重跑调仓(`deploy_aum` 放大)产生与前批某单同数量的新增量。
- **后果**:真钱下静默重复下单 + 账本与交易所漂移且本地不可追踪;preflight 下周期抓 drift 硬停(有界可恢复),但不阻止本周期这一次。
- **修复**:`client_order_id` 加 per-batch 单调序号/run nonce(仍保留同批重试幂等);或记账用 `INSERT` 撞主键即 raise(不静默 IGNORE)让碰撞显式失败。
- **验收**:两笔同数量、逻辑不同的增量得到不同 client_order_id;记账不再静默吞没;同数量碰撞有测试覆盖。

### P1-4 · place-后-未落库的孤儿订单窗口 · LATENT
- **描述**:订单只在 `place_order`(`pipeline.py:349`)与整段确认/撤单轮询**之后**才第一次写账本(`record_submission` 在 `:399`,中隔 `_confirm_fill` 真实 sleep,LIMIT 默认 30 轮≈30 秒)。若进程在该窗口被杀,订单已在交易所、账本无该行;`resolve_unconfirmed_orders` 只遍历 `ledger.orders()`(`:158`)——从未记账的订单看不到,崩溃恢复结构性发现不了它。对 MARKET 单由下周期 preflight 仓位漂移抓到硬停;对 LIMIT 挂单(未成交无仓位)preflight 看不到 → 真正的僵尸单。
- **触发**:Windows 定时任务在 place 与 record 之间被系统重启打断。
- **后果**:交易所有单、账本无痕;LIMIT 僵尸单可在后续周期成交造成不可追踪漂移。
- **修复**:place_order **之前**先写一条 pending intent 账本行(意图日志),使崩溃恢复看得见;或 `resolve_unconfirmed` 额外拉取 venue open orders 与本地对账。
- **验收**:在 place 与 confirm 之间注入进程终止,下次启动崩溃恢复能发现该订单并补记/撤销;测试覆盖 LIMIT 僵尸单场景。

### P1-5 · 实盘 ctx 流动性/波动/重复/频率引擎被占位符架空 + 无撤单率检查 · LATENT
- **描述**:`live_track_advance.py:234-267` 给 RiskContext 喂 `est_daily_volume=1e12`/`instrument_vol=0.8` 占位符,`gate_limits` 又把 `max_participation_rate=1.0`/`max_instrument_vol=5.0`,导致 `check_liquidity`/`check_volatility`(`checks.py:191-202`)数学上永不触发;`recent_order_fingerprints`/`recent_timestamps` 默认空 → `check_duplicate`/`check_rate_limit` 在 live 永不触发;撤单率检查引擎根本不存在。
- **触发**:放行真钱后某笔订单占真实 ADV 比例过高、或标的极端波动、或驱动器被重复调度。
- **后果**:gate 声明的流动性/波动/去重/限频四个引擎对真实订单不设防(实际去重靠幂等 client_order_id 兜底,受 P1-3 影响)。
- **修复**:接入真实逐标的 ADV/已实现波动喂 MarketContext,设校准阈值;从 LiveLedger 回填最近窗口 fingerprints/timestamps;新增撤单率检查引擎。
- **验收**:超 ADV×max_participation_rate 的订单返回 LIQUIDITY_LIMIT_EXCEEDED;窗口内重复 intent 返回 DUPLICATE_ORDER;撤单率超阈值被拒。

### P1-6 · firm 跨账户/跨 venue 敞口聚合只诊断不阻断,隐藏拥挤无自动限额 · LATENT
- **描述**:`aggregate_firm_books`(`firm_aggregation.py:8`)在 `live_risk_sentinel` 里聚合两所净/毛敞口 + overlap,但明确"只诊断不阻断"(`live_risk_sentinel.py:168`),越限判断仍由各 venue 独立 mandate 负责。同 symbol 两所同向持仓形成的 2x 集中不被任何 per-venue 检查捕获,也无 firm 级硬门。
- **触发**:多 venue 实盘且两所跑相关/相同 book 时同向叠加。
- **后果**:firm 级净敞口/单名集中可超预期风险,唯一反馈是需人工阅读的告警(2007-08 quant 拥挤踩踏模式)。
- **修复**:为 firm 聚合结果接入组合级硬门(firm 净/毛/单 symbol 跨账户集中上限),越限升人工或按比例减仓。
- **验收**:构造两所同向持仓触发 firm 级集中上限,系统产生阻断/减仓决定而非仅告警。

### P1-7 · 多 alpha 同时失效无自动检测与自动 de-risk 闭环 · LATENT
- **描述**:`degradation_monitor`(live-vs-backtest→建议)、`retirement.sweep`、`maintenance.run_maintenance` 已建已测,但 `run_maintenance` **无墙钟调度器、需调用方手工供证据、无自动 live 指标喂入**(`maintenance.py:14` 自认"the HOOK a Phase-14 scheduler will call"),且只驱动 alpha catalog 研究状态机,不自动收缩 live 仓位。周度调度器只跑挖矿 + IC 面板,不跑维护。
- **触发**:实盘中一条或多条 alpha 边衰退/翻负,无人手工跑 sweep。
- **后果**:"多 alpha 同时失效"不被自动识别/降权/退休/de-risk,失去实时风控意义。
- **修复**:Phase-14 调度器周期调用 `maintenance` + 自动从 forward paper-track/live 账本算 live-vs-backtest 指标喂入;退休/降权信号联动 live 仓位收缩。
- **验收**:端到端测试:注入衰退 live 指标→自动 sweep→alpha 状态降级并触发 live 仓位下调,无需人工带证据。

### P1-8 · 数据质量在接入路径 computed-but-not-enforced,喂前向证据钟的坏数据不被拦截 · LATENT
- **描述**:`strict` 默认 False(`market_provider.py:45`),只有 `yahoo_equity_fetch` 用 `strict=True`;所有喂已部署 crowd_contra 与 8 周前向证据钟的 crypto/FX 抓取全 `strict=False`。质量报告被算出但接入不 fail-closed。加之乱序检查因先排序而失效(见 P2-1)、volume 守卫未接线,除重复(PK 兜底)与未来时间戳(`reject_if_future` 兜底)外的缺陷在接入层不阻断。
- **触发**:某抓取器某轮返回乱序/单位错乱/含缺口的坏数据,进库并被前向 realize 兑现。
- **后果**:用被污染的证据做真钱 Go/No-Go 决策;或干跑/回测结论失真而无告警。
- **修复**:喂承重决策的单标的抓取器统一开 `strict=True`;修复乱序死检查;`check_volume_units` 接进批量校验;质量不过 → fail-closed 留 pending 并告警。
- **验收**:注入乱序/单位错乱坏 fixture 时接入 raise 或该标的被排除且告警;前向兑现前有一道质量门可拒。

### P1-9 · promotion_api 把三方批准的"不同真人"退化为 runtime 可冒充的自报标签 · LATENT
- **描述**:promotion_api(:8460)经 X-Gov-Token 认证后,用自报 X-Operator 头铸造 `Actor(kind=HUMAN)`(`promotion_api/main.py:249`,`_operator` 只 strip 字符串零认证 `:131`)。持 gov token 的任何本机进程(含 AI agent)可对同一 request 依次打三个不同 X-Operator 走完三方批准到 APPROVED,零 code-review 痕迹。
- **触发**:用户手动拉起 promotion_api 且 gov token 对某非人/被攻陷进程可读。
- **后果**:L2 下 APPROVED 只是生命周期状态、不解锁 LIVE(五旗帜 + readiness pin 兜底)、且未接进 live 执行栈(`live_track_advance.py:342` 用硬编码信号不读 AlphaCatalog),故当前无资金后果;但破坏"agent 结构性到不了 APPROVED"不变量,Phase 3A 后若 APPROVED 成为真钱前置则完整性被单进程绕过。
- **修复**:保持默认不启动;agent 环境永不持有 gov token;Phase 14 前置真出进程身份认证;或 L2 将 execute 端点限为仅记录不推进。
- **验收**:Phase 14 认证接入后,同进程三个 X-Operator 打满三方批准 + execute 必须被拒(签名校验失败);tests/governance 钉死 agent 环境无 token 读路径。

---

## P2 风险(质量 / 可维护性 / 扩展性)

### P2-1 · 接入核心乱序检测结构性失效(先排序再查乱序) · ACTIVE
- **描述**:`file_ingest.py:66` 用 `sorted(b.event_time...)` 作为 `build_report` 输入,而 `count_out_of_order`(`quality.py:37`)只比相邻回退——输入已排序则恒 0,乱序永远检不出;`missing_sessions` 因 `expected_days` 未传也恒 0。给出"质量已检查"的假象。
- **修复**:排序前用原始顺序跑乱序检查;接入时传真实 `expected_days` 让缺口检查生效。
- **验收**:对乱序 event_time 序列 `build_report` 报出 `out_of_order>0`;缺口场景 completeness 能失败。

### P2-2 · crypto_daily.db 跨币 volume 单位不一致使 ADV/容量不可靠 · ACTIVE
- **描述**:`scenario_tail_research` 用 `check_volume_units` 抓到 EOS ~$20k vs BTC ~$3.7Q(~17 数量级)不可能同单位;该库 ADV/容量数字无意义。已诚实标注 `UNRELIABLE_VOLUME_UNITS` 并 spawn 修复,但 bug 数据仍在库里。
- **修复**:修正 volume 单位标准化,或消费端强制 `check_volume_units` 门;修好前禁止用该库出容量结论。
- **验收**:`check_volume_units` 对修复后的库通过;容量工具在数据不可靠时 fail-closed 拒绝出单点数字。

### P2-3 · 紧急减仓 drawdown 全平依赖峰值权益持久化正确 · LATENT
- **描述**:`emergency.py` 刻意绕过共用 gate/kill-switch(结构性只减敞口已核实),`flatten_target_for_breach` 在 drawdown 越限时返回 target 0(全平),依赖 `live_risk_state.json` 峰值权益正确;`--act` 每小时自主运行。峰值被错误持久化(异常高)会触发非必要全平(仅减仓、方向安全,但清空真钱仓位有机会成本/滑点)。
- **修复**:对 peak-equity 持久化做合理性上界校验;可选全平动作要求人工确认或延迟一轮 + 大声告警。
- **验收**:注入异常峰值使回撤越限时,全平路径要求人工确认或不在单轮内执行;结构性 reduce-only 网格测试保持全绿。

### P2-4 · 包级循环依赖 + god-package/god-module · LATENT
- **描述**:research↔alpha/risk/portfolio/intelligence/execution 双向,alpha↔governance 双向;research 13869 LOC 巨型域、`console_api/main.py` 单文件 5288 行 42 端点。1564 测试全绿说明无 import-time 死锁,但"干净分层"叙事与实际纠缠不符,Phase 14 进程隔离时难拆分。
- **修复**:引入 import-linter 在 validate.py 钉死域间无环(禁回指 research 的反向边);research 拆子包;console_api 按 router 拆分。
- **验收**:validate.py 内自动 import-cycle 检查通过;console_api 拆成多个 <800 行 router。

### P2-5 · 无事件总线 / 服务紧耦合(直接 import + 共享 sqlite) · LATENT
- **描述**:canonical `Event` 信封(`quantos/contracts/events.py:14`)零 service 使用,console_api 自认缺 durable event bus(`main.py:3633`)。跨组件通信 = 直接函数调用 + 各自读写 `data/cache/*.db` + 内存 GlobalAuditLog。
- **修复**:用已有 Event 信封实现持久事件总线(至少关键事件流经并可重放),或显式把"事件总线"从架构叙事降级为待办。
- **验收**:关键事件经持久总线流转且可重放;Event 被 ≥2 个 service 实际 import。

### P2-6 · 实盘新鲜度门宽至 7 天且陈旧时跳过跨源核价 · LATENT
- **描述**:`_MAX_STALENESS_MS=7 天`(`live_track_advance.py:196`)、`_CROSS_CHECK_MAX_STALENESS_MS=3 天`(超 3 天跳过 CoinGecko 跨源核价)。存在"决策价 3-7 天旧、跨源核价关闭、仅单一新鲜度门把关"的窗口。
- **修复**:按策略调仓频率收紧 staleness;接入 venue book_ticker 同点跨源核价替代滞后决策价对比;新鲜度/覆盖率门纳入干跑主路径常态验证。
- **验收**:陈旧超阈值 feed 下真实下单被拒;跨源核价不再因决策价旧而整体跳过。

### P2-7 · 唯一 live-wired 组合级控制(拥挤门)阈值未校准 · ACTIVE
- **描述**:`evaluate_crowding_gate` 的 threshold=0.60/warn=0.45/max_name=0.25/max_exit_days=5 均自标注 UNCALIBRATED 工程占位(`crowding_gate.py:85`),却是当前实盘路径唯一真阻断的组合级控制。
- **修复**:用历史拥挤/踩踏数据校准,或放行前敏感性扫描并文档化。
- **验收**:阈值有校准记录或敏感性分析支撑,契约测试锁定边界。

### P2-8 · 现实主义冲击/容量系数 UNCALIBRATED · LATENT
- **描述**:`impact_calibration.py:79` 自认 not venue calibrated,L2 无 tick;`capacity_band` 给区间带非单点。诊断非门禁,但用于真金 sizing 时口径失真(引擎首跑即抓到冲击-only ~300x 高估容量)。
- **修复**:真金 sizing 前用真实成交校准冲击系数、赋 CALIBRATED provenance;校准前保持诊断-only。
- **验收**:用于 live sizing 的冲击系数带 CALIBRATED provenance 且由真实 venue fills 支撑。

---

## P3 风险(体验 / 展示 / 文档 / 优化)

### P3-1 · "services/ 零网络 import"不变量有覆盖洞(aiokafka 逃逸) · LATENT
- **描述**:边界测试 `_NETWORK_ROOTS`(`test_crypto_data_boundary.py:68`)不含 aiokafka/kafka/redis/pymongo,而被隔离的 `services/trading/pre_trade_risk/main.py:24` 确实 import aiokafka。模块已隔离不运行,当前无实害,但守卫弱于文档所称"零网络 import"。
- **修复**:把 aiokafka 及常见消息/网络客户端加入 `_NETWORK_ROOTS`(显式排除被隔离文件)。
- **验收**:在 services/(隔离文件外)新增 aiokafka import 会让边界测试失败。

### P3-2 · console_api 同端点真/合成数据混杂 · ACTIVE
- **描述**:artifact 缺失回退 `math.sin/cos` 合成因子并标 `SYNTHETIC_DETERMINISTIC`(诚实,前端有 `assertConsoleSnapshot`),但真/合成在同端点混杂,消费方不看 provenance 会误读。L2 只读,只影响展示。
- **修复**:前端对合成面板灰显/角标区隔,或纯合成端点单独归类。
- **验收**:每个合成 payload 带 provenance 且前端明确 badge 标记合成来源。

### P3-3 · 99 个空 DDD 脚手架目录 + gateway/learning 空壳误导结构 · LATENT
- **描述**:services/ 下 99 个无 `.py` 的六边形脚手架叶目录 + `services/{gateway,learning}` 空目录,虚增架构成熟度观感。
- **修复**:删除空脚手架,或每个占位目录放 README 标注 PLANNED。
- **验收**:services/ 下除显式 PLANNED 外无空叶目录。

### P3-4 · alpha lineage 未接线 / 富 AlphaAsset 无持久层 · LATENT
- **描述**:`make_child_asset`(`lineage.py:17`)已测但零非测试调用方,机器育种不落 genealogy;`AlphaAssetRegistry` 是内存 dict,富元数据跨会话丢失(持久的只有 catalog 状态 + evaluation artifact)。
- **修复**:育种处调用 `make_child_asset` 记 parent_ids/mutation_op;`AlphaAssetRegistry` 落 sqlite。
- **验收**:一次工厂运行的育种子代带非空 parent_ids;`AlphaAsset` 可磁盘往返、重启后一致。

---

## 修复优先级(工程排期)

| 序 | 风险 | 类型 | 我可自主修复(fail-closed/bug) | 需人工/大改 |
|:-:|---|---|:-:|:-:|
| 1 | P1-2 mandate 账户级聚合 + 多策略硬停 | 安全加法(只会更严) | ✅ | |
| 2 | P1-3 client_order_id 同数量碰撞 | 真 bug 修复 | ✅ | |
| 3 | P2-1 乱序死检查 | 真 bug 修复 | ✅ | |
| 4 | P1-1 验证门作 live 前置 | 安全加法 | ✅ | |
| 5 | P1-4 place-后-未落库孤儿窗口 | 安全加法 | ✅ | |
| 6 | P1-5 回填 ctx 历史指纹/时间戳 + 撤单率 | 安全加法 | ✅ | |
| 7 | P3-1 aiokafka 边界洞 | 治理加固 | ✅ | |
| 8 | P2-2 volume 单位守卫接线 | 安全加法 | ✅(部分) | 数据修复需人工 |
| 9 | P2-3 峰值权益合理性上界 | 安全加法 | ✅ | |
| 10 | P1-6 firm 级硬门 | 机制可做,阈值需校准 | ✅(机制) | 阈值人工签 |
| 11 | P1-8 数据质量强制 strict | 安全加法 | ✅(部分) | 需验证不破抓取 |
| 12 | P0-1 mandate 完整性签名 | Phase 14 前置 | | 人工签 + Phase 14 |
| 13 | P0-2 出进程签名/进程隔离 | Phase 14 | | Phase 14 |
| 14 | P1-7 维护调度器 + 自动 de-risk | 需 forward 指标就绪 | | 部分 Phase 14 |
| 15 | P2-4/P2-5 import-linter/事件总线 | 结构性大改 | | 排期 |
