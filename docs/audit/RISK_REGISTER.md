# QUANTOS 风险登记册(RISK_REGISTER)

> 审计日期:2026-07-06 · 最近增量复核:2026-07-15 · P0 最先。**ACTIVE**=当前可触发;**LATENT**=现被 L2/沙盒/人工门挡住,Phase 3A 放行当天转 ACTIVE。

## 2026-07-15 软件供应链制品准入 V3 增量复核

- **已关闭的本地缺口**: policy v3 严格类型与时间上限、重复键拒绝、运行时 verifier
  三方绑定、证书实际 SAN/OIDC 抽取、已知撤销优先、验签完成后取准入时间、固定路径
  SQLite 权威、commit-before-output、事务内时钟回拨拒绝，以及稳定外部 authority/key
  钉住 + 动态 Ed25519 receipt 验签均已承重并测试。
- **仍开放的高风险边界**: 生产 receipt-authority registry 为空，尚无真实外部签名回执；
  checkpoint/revocation 的 source-native proof 仍未验证；
  本地 SQLite 可被有文件权限者整库替换且无外部链头封存；无真实有效 Sigstore bundle
  集成夹具、跨主机复现、独立 admission 部署或 Rekor v2/TSA 支持。
- **真实环境状态**: policy registry 为空且固定生产 authority DB 未配置，因此所有真实
  请求失败关闭，不能把代码就绪写成运行中服务。`PLATFORM.SUPPLY_CHAIN` 保持 M3/PARTIAL
  与红线开放；详见 `docs/audit/2026-07-15-artifact-admission-v3-review.md`。
- **验收**: 准入聚焦 80 tests、供应链/评估/备份 59 tests 与 Ruff 全绿；最终统一门禁
  18/18：主套件 2420 passed / 1 skipped，live/trading/tools 972 passed，前端 68 tests +
  typecheck/build，治理扫描 313 files / 0 violations。机器结论仍为 `NOT_TOP_TIER`，
  15/22 PASS、5 PARTIAL、2 MANUAL、0 FAILED。
> 每条:描述 → 触发场景 → 后果 → 修复建议 → 验收标准。所有条目均有 file:line 代码证据支撑。

## 本轮自主修复进度(2026-07-06,全部 TDD + 全量门禁全绿 + 独立提交)

用户 2026-07-06/07 授权自主推进"让系统更 fail-closed/更诚实/更严格"的加固(不触碰人工门)。已落地
**10 项**(均只让系统更严,永不放松风控)+ 把 live 执行栈与 reproducibility 纳入 CI 门禁,门禁总覆盖
**2129 测试**全绿:

| 风险 | 修复 | commit |
|---|---|---|
| P2-1 接入乱序死检查 | `file_ingest` 传原始到达顺序而非 sorted,乱序在接入可检出、strict fail-closed | `99093e6` |
| P1-3 client_order_id 同数量碰撞 | 把下单口径前仓位纳入订单身份(可选参,默认逐字节不变),同数量不同增量不再撞 ID 静默吞记账,保留崩溃重建幂等 | `1d0a40b` |
| P1-2 mandate gross/net 只算当批 + 多策略超发 | 新增账户级投影 gross/net(现有敞口 + 本批 delta),ADD-only 不放宽当批检查;因 live 传账户级仓位,同时挡住多策略共享 mandate 超发;无法定价的持仓 fail-closed | `c96f285` |
| P3-1 aiokafka 网络边界洞 | `_NETWORK_ROOTS` 补齐消息/异步网络/DB 客户端 + 精确豁免隔离模块,新增同类 import 现会被守卫挡下 | `6ca9aff` |
| P1-4 place-后-未落库孤儿窗口(主路径 + 紧急通道) | 主 pipeline 在 `place_order` 前强制落 `PENDING_NEW`；ID 冲突或数据库异常均审计并停止后续批次，失败订单零 venue 调用；place 后写失败仍如实计 submitted 并进入对账。紧急 reduce-only 通道保持独立 best-effort | `04a890a`/`4bca401` + 2026-07-13 hardening |
| P1-5 live ctx 风控占位与撤单率缺口 | 真实 ADV/波动、24h 提交频率、5 分钟重复指纹和 24h 撤单率均由持久 LiveLedger/真实市场证据回填；未知状态 fail-closed，纯减仓不被撤单率阻断 | 2026-07-13 hardening |
| 证据摘要漂移 + reproducibility 纳入门禁 | crowd_contra t_stat 样本软化(0.6554→0.6204,核实良性)重生成摘要,`tests/reproducibility` 进 CI | (见 git log) |
| **P1-1 验证门作 live 前置**(接线) | `live/governance/validation_attestation.py` 策略级准入门(PASS 验证或可审计人工 override,fail-closed)+ 接进驱动器 preflight(preview 只告警、--submit fail-closed 跳批)+ 签署模板;**crowd_contra 现真提交被挡,直到你签 `key/live_validation_attestations.json`** | `(见 git log)` |
| P1-6 firm 敞口硬门(**已接线**) | `evaluate_firm_gate` + 用户签署上限(gross 20000/net 10000/单名 20%)接进哨兵(算门+写 `firm_gate_status.json`+BLOCK 告警)与驱动器(读状态→BLOCK 停止新增、自清除,不 arm kill-switch);auto-减仓留后续 | `(见 git log)` |
| **P1-10(审计中新发现)门禁从不跑 tests/live** | `scripts/validate.py`(即 CI)硬编码测试目录,遗漏 `tests/live`(36 文件,最安全关键的下单/风控/对账代码)+ trading/tools——live 栈回归静默过 CI。作为独立子进程纳入(避 basename 冲突),门禁 1567→1567+527=2094 全绿 | `(见 git log)` |

> **P1-10 是本轮审计中途实测发现的真实缺口**(初版 GAP_MATRIX 维度 15 因此偏乐观):此前 CLAUDE.md 里
> "全量门禁 N passed" 来自对整棵树跑 pytest,而 CI 实际跑的 `validate.py` 从不包含 live 执行栈。已修复。
> **附带发现(未修,交还用户)**:`tests/reproducibility/test_evidence_snapshots.py` 有预存在的证据摘要漂移
> (crowd_contra t_stat live 0.6204 vs digest 0.6554,即 CLAUDE.md 记录的样本延长软化)——是否重生成摘要
> = "漂移是否 intended" 的研究判断,故 `tests/reproducibility` 暂不纳入门禁,留用户定夺
> (`python -m tools.build_evidence_digests` 可重生成)。

**剩余项(卡在需要用户的数字/拍板)**:
- P0-1/P0-2(Phase 14 + mandate 签名,需真人)。
- ~~P1-6 接线~~:**2026-07-07 已签署接线完成**(firm max_gross=20000/max_net=10000/max_single_name_weight=0.20,
  用户签)——`evaluate_firm_gate` 真实 BLOCK,详见下方 P1-6 条目更新。
- **P1-5 频率引擎**:✅**已接线**(2026-07-07)——改名 `rate_window_s`→`rate_window_ms`(消除"秒当 ms"
  死代码陷阱)+ 从持久账本回填 recent_timestamps + runaway-guard(24h 窗/上限 500 **工程占位待你确认**)。
  ⚠ 但"24H 中高频"的**大方向**是一次**周度→日内 cadence 转型**,牵动实时数据层(审计维度 2 标记 MISSING)、
  调度、新鲜度、成本——见专门文档 [`CADENCE_1H_SHIFT.md`](CADENCE_1H_SHIFT.md)。**关键裁定:现有信号类 1H
  大概率无边**(crowd_contra 持有期越短边越弱)。基础设施(CadenceConfig / 1H 抓取器 / 频率护栏)已建为纯加法,
  但真启用 1H 执行需整组危险单点一起改 + 过验证门,是需一起设计的决定。
- **P2-3 峰值权益合理性上界**:2026-07-13 采用无需新阈值的证据上界闭合——持久峰值不得高于
  append-only 权益观测历史最大值；没有替用户选择倍率、确认时延或修改全平策略。是否对**证据有效的真实回撤**
  再加人工确认/延迟一轮，仍是独立风险政策决定。见 [`NEXT_CLAUDE_CODE_PROMPTS.md`](NEXT_CLAUDE_CODE_PROMPTS.md)。
**结论:所有无需风险阈值、无需人工拍板的 fail-closed 加固已落地；剩余项要么等待外部/运营证据，要么是需共同
设计的策略/cadence 决定。**

## 一句话安全结论(先说最重要的)

**当前(L2)不存在可被立即利用的 ACTIVE P0。** 8 组红队对你列的 15 个 P0 问题逐条逆向追踪代码后:风控绕过、无 kill-switch 执行路径、fail-open、重复下单、账实不一致、实/模混淆、mock 误用、密钥泄露、断线状态错误、回测不可复现、前视泄漏、非人到 APPROVED、kill-switch 无人工复位——**全部为 MITIGATED 或 LATENT**。真正的风险集中在**放行真钱当天集体转正的 LATENT 项**,其中治理面(mandate 完整性)是最关键的一条。这正是 L2 pin + 五旗帜 + Phase 3A 人工门的设计价值:把所有"放手"都挡在真钱之外。

---

## P0 风险(可致真实资金损失 / 风控绕过 / 不可恢复)

### P0-1 · mandate 完整性:无签名 JSON，post-L2 放行仍缺密码学授权 · LATENT / AUTOMATIC SCHEDULE TRIGGER REMOVED (2026-07-14)
- **描述**:mandate 是纯 JSON,`load_mandate` 只做字段存在性 + 有限性校验(`live/governance/mandate_store.py:37`),**无签名、无多方、无哈希链**;`approved_by` 字段明确"是审计留痕、非任何权限校验"(`mandate.py:34`)。任何对 `key/` 有写权限的受信本机进程都能改限额。2026-07-14 现场发现旧 Windows tasks 仍以 `--submit`/`--act` Enabled 运行，且该 `live/` 路径不消费 canonical `SafetyConfig`；两条提交、主动哨兵、ops API 与其 live watchdog 已全部可逆禁用。`scripts/register_schedules.ps1` 现由治理测试锁定为 L2-only 白名单，不再创建任何 live action。
- **触发**:只有未来 post-L2 人工重新启用旧任务、手工调用 live CLI，或建立新的 live deployment 后，本风险才可到达；当前调度入口已移除。届时任何有 `key/` 写权限的进程放大 mandate，仍可能在后续显式提交中扩大授权。
- **后果**:分级授权的核心承诺("系统自主执行不超过签署额度")被绕过;真钱下可致超授权杠杆/敞口。
- **现被挡住**:五个 legacy tasks 均现场复核为 Disabled；L2 注册表只含 research/paper/maintenance/event-ops/report 模块且 action 无 shell；mainnet 适配器 L2 构造仍会 `raise SafetyViolation`。测试网/demo 的历史仓位制品仍存在，但当前没有计划任务连接 venue 或采取动作。
- **修复**:①保持 L2 调度治理门，不允许以恢复旧任务替代 readiness 升级；② mandate 加载最终须验证独立签名并收紧 agent 对 `key/` 的写权限；③根治仍是 Phase 14 出进程签名与独立部署，任何 live 调度必须作为新 Go/No-Go 项重新批准。
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

### P1-1 · 验证准入作为 live 提交结构性前置 · CODE MITIGATED / EXTERNAL TRUST OPEN (2026-07-13)
- **已修复**:`tools/live_track_advance.py` 在 `--submit` 前加载 `key/live_validation_attestations.json`，缺文件、坏文件、缺策略、空签名或缺 PASS hash/override reason 均设置 `skip_reason`，即使 mandate 已签也不调用 pipeline。2026-07-13 再把同一门下沉到 `live.pipeline.submit_live_batch` 的**必填** keyword-only `validation_attestation`；pipeline 在 gate、mandate、exchange filters 和任何 venue 调用之前校验凭证与 `strategy_id` 一致，失败返回 `NEEDS_HUMAN_REVIEW`、写 `LIVE_VALIDATION_ATTESTATION_DECISION`，零订单提交。治理 AST 扫描所有 `live/services/tools` 生产调用点，禁止漏传。
- **剩余风险**:`validation_result_hash` 当前仍是 key 文件中的引用，没有向不可变 ValidationResult 制品库解析并验证内容/verdict；`attested_by`/human override 仍是自报标签。它能防止遗漏和错策略接线，不能抵抗受信代码伪造凭证；真实身份、出进程签名和外部制品权威仍由 P0-2/P1-9 负责，不能把本地结构门冒充 M4。
- **验收**:缺凭证与错 strategy 的 pipeline 对抗测试证明 `--submit` 下在 venue 调用前拒绝；合法 fixture 保持 MARKET/LIMIT/slicer/TCA/账本语义。`tests/live` 738 passed；`tests/governance/test_live_execution_boundary.py` 12 passed。

### P1-2 · mandate gross/net 只算当批 + 多策略共享额度超发只告警不硬停 · 部分已修复(2026-07-08 复核)
- **描述**(2026-07-08 更新,替换下方已过时的原始描述):`c96f285` 已把 `evaluate_mandate` 的 gross/net 从"只算当批订单"升级为**账户级投影**(`account_positions`=`ledger.all_positions()` 全量现有敞口 + 本批 delta 二次校验同一签署上限,ADD-only),**已堵住"多批各自达标、账户总敞口 N× 超发"这条原始风险场景**。**但** per-strategy 子限额分配业务决定显式推迟未做——`multi_strategy_mandate_alert`(`live_track_advance.py:388`)自称"提醒而非硬停",只 append 告警字符串不设 skip_reason,残留缺口:①无公平子额度分配,先到策略可耗尽全部共享额度;②两策略真并发提交的竞态未覆盖。当前 `_STRATEGIES` 仅注册 crowd_contra 一条,该场景 **dormant**(未被触发),**在第二条策略注册前必须解决**。
  <details><summary>原始描述(2026-07-06,保留存档)</summary>`evaluate_mandate` 的 gross/net 累加只遍历当批订单(`live/governance/mandate.py:118`),不含账户已有敞口、也不感知同 venue 其它策略的并发敞口。`multi_strategy_mandate_alert`(`live_track_advance.py:388`)只返回提示字符串不阻断。多策略共享 mandate(或单策略多次增量)时账户总 gross/net 可达 N× 签署上限而各批仍判 AUTO。per-instrument cap 已用 account 级投影修过,gross/net 未同等处理(memory 记为 disclosed-not-fixed)。</details>
- **触发**:Phase 3A arm 真钱后,>1 策略在同 venue 共用同一 mandate 且均 `--submit`;或单策略同周多次增量(后者已被账户级投影堵住)。
- **后果**:第二条策略上线前无实际后果(dormant);上线后,先到策略可能耗尽共享额度,后到策略被迫升人工(非资金越限风险,是可用性/公平性风险)。
- **修复**:mandate 文件加 per-strategy 子限额字段(业务决定,需用户签);`_STRATEGIES` 注册第二条策略前必须补上,否则 `multi_strategy_mandate_alert` 从 WARN 升级为硬停(skip_reason)作为过渡缓解。
- **验收**:两条策略各提交使账户 60% gross 上限的批次,第二批返回 NEEDS_HUMAN_REVIEW(账户级投影已满足);子限额分配公平性另需专门测试覆盖(未做)。

### P1-3 · client_order_id 同数量碰撞与账本静默吞冲突 · CODE MITIGATED (2026-07-13)
- **已修复**:订单身份已包含 `strategy_id + decided_ts + instrument + side + rounded quantity + prior_qty [+ limit_price]`；同决策下 `0 -> +5` 与 `+5 -> +10` 两笔相同 delta 因前仓不同而得到不同 ID，同一前仓/同一目标的崩溃重建仍得到同 ID。2026-07-13 进一步删除 `live_orders/live_fills/live_tca` 三条承重写路径的 `INSERT OR IGNORE`：主键冲突后读取既有不可变 payload，完全相同才幂等 no-op，任一字段不同抛 `LiveLedgerConflictError`。
- **执行闭环**:主 pipeline 在 pre-journal 阶段遇到 ID 冲突，写 `LIVE_LEDGER_ID_CONFLICT` 并在任何 `place_order` 前停止**整批**；普通数据库 IO 故障仍沿用既有“减小漏单风险、订单后人工对账”语义，二者不混淆。成交后 fill/TCA 冲突显式进入 ledger/TCA 写失败审计，不再静默改写或吞没。
- **剩余风险**:client ID 仍是截断 SHA-256，密码学碰撞概率非零但已由 payload 对账转为显式拒绝；多进程真正同时提交仍依赖交易所 ID 去重与 SQLite 锁等待，后续 HA/单写者架构另行解决。
- **验收**:同 delta 不同前仓双批均入账；订单/成交/TCA 同 ID 异 payload 均拒绝；BTC 冲突时同批 ETH 也零 venue 调用。`tests/live` 747 passed，治理测试 13 passed。

### P1-4 · place-后-未落库的孤儿订单窗口 · CODE MITIGATED (2026-07-13)
- **已修复**:主 pipeline 对每个订单先持久化 `PENDING_NEW`，成功后才允许 `venue.place_order`。`LiveLedgerConflictError` 写 `LIVE_LEDGER_ID_CONFLICT`，普通存储异常写 `LIVE_PREJOURNAL_FAILED`；两者都进入 `ledger_write_errors` 并停止失败订单及后续批次，不再吞异常继续下单。进程若在 place/confirm 之间终止，恢复器能从 pending 行发现并回查订单。
- **place 后边界**:venue 已受理后 `update_status`/fill/TCA 写失败不能把真实订单倒算成失败；仍返回 `submitted`，写 `LIVE_LEDGER_WRITE_FAILED`，留给恢复与对账处理，防止调用方误重试造成重复加仓。
- **独立紧急通道**:`live/execution/emergency.py` 的 reduce-only 全平不依赖主 pipeline，账本仍为 best-effort，避免数据库故障反向阻断减仓；该例外由 reduce-only 结构约束和独立测试守住。
- **剩余风险**:SQLite 仍是单机单写者，无主备复制/FIX drop-copy；批次顺序执行意味着后续订单预记账失败时，之前已成功预记账并提交的订单不会回滚，但失败订单及其后续订单均不触达 venue。HA/单写者服务化仍属后续机构化工作。
- **验收**:pre-journal 数据库失败时 BTC/ETH 整批零 venue 调用且产生专用审计；post-place 状态写失败时订单仍计 submitted；ID 冲突走独立审计；治理 AST 断言预记账调用位于 `place_order` 之前。

### P1-5 · live ctx 流动性/波动/重复/频率 + 撤单率 · CODE MITIGATED (2026-07-13)
- **已修复的审计漂移**:真实逐标的 ADV/已实现波动早已从策略 evidence 注入 `MarketContext`，缺标的值时 liquidity/volatility fail-closed；持久 `LiveLedger` 的 24h `submitted_at` 也早已注入 `recent_timestamps`。旧描述把这三项仍写成占位符，现已按代码事实纠正。
- **本轮补齐**:`build_recent_order_gate_inputs` 从同一账本快照生成 24h 时间戳/订单终态和 5 分钟稳定指纹；指纹复用 `RiskContext.fingerprint` 的同一 canonical helper，并同时保留 pre-pricing MARKET 与已持久化 LIMIT 形式。未来时间戳或非法数量直接抛错，使驱动器在 submit 前 fail-closed。
- **撤单率门**:共享 pre-trade registry 从 23 增至 24 个检查。24h 样本达到 20 单后，`CANCELED/CANCELLED/EXPIRED` 比例超过 80% 返回 canonical `TURNOVER_LIMIT_EXCEEDED`；未知状态同样拒绝。经真实仓位证明的纯减仓单豁免，避免高撤单率反向阻断退出。
- **阈值诚实边界**:24h/500 单 runaway 上限、5 分钟重复窗、80%/20 单撤单率都是 L2 工程基线，并非生产微观结构校准结论；`max_participation_rate=1.0`/`max_instrument_vol=5.0` 仍偏宽。上线前必须用授权 order-book/执行样本按策略类别校准，尤其做市与方向策略不可共用撤单率阈值。
- **验收**:窗口内等价 ledger 订单可重建出与 gate 一致的指纹；高撤单率/未知状态拒绝；阈值等于上限放行、样本不足放行、纯减仓放行；`tests/risk` 78、`tests/live` 753、治理边界 14 全绿。

### P1-6 · firm 跨账户/跨 venue 敞口聚合硬门 · 已接线(2026-07-07 用户签署,2026-07-08 复核确认真实 BLOCK)
- **描述**(2026-07-08 更新,替换下方已过时的"只诊断不阻断"描述):`evaluate_firm_gate`(`services/portfolio/firm_gate.py:30-67`)+ 用户签署上限(`gross=20000`/`net=10000`/`single_name_weight=0.20`,2026-07-07)已**真实接线**,三文件交叉确认:①`tools/live_risk_sentinel.py:59-61,245-250` 算门 + 写 `firm_gate_status.json` + BLOCK 告警;②`tools/live_track_advance.py:887-912` 读状态,越限时对**新增仓位**批次整体设 `skip_reason`、结果 `SKIPPED_DATA_QUALITY`(仅 `--submit` 生效,preview 只告警);③自清除(限内恢复自动解除)。`aggregate_firm_books`(`firm_aggregation.py:8`,原诊断函数)仍保留为纯诊断展示,但**决策路径已不再依赖它**——firm_gate 是独立的、真实生效的硬门。
  <details><summary>原始描述(2026-07-06,保留存档,已被上方取代)</summary>`aggregate_firm_books`(`firm_aggregation.py:8`)在 `live_risk_sentinel` 里聚合两所净/毛敞口 + overlap,但明确"只诊断不阻断"(`live_risk_sentinel.py:168`),越限判断仍由各 venue 独立 mandate 负责。同 symbol 两所同向持仓形成的 2x 集中不被任何 per-venue 检查捕获,也无 firm 级硬门。</details>
- **触发**:多 venue 实盘且两所跑相关/相同 book 时同向叠加超过已签上限——**现在会被硬门拦下**,不再是"只告警"。
- **后果**:已消除原描述的资金风险(2007-08 quant 拥挤踩踏模式的自动阻断已就位)。**残留缺口**:越限时是"停止新增",不是"自动按比例减仓"(auto-de-risk 留后续,与 P1-7 同类)。
- **修复(残留部分)**:越限时从"停止新增"升级为"按比例主动减仓"(可选,当前"停止恶化+需人工介入"已是合理的保守默认)。
- **验收**:✅ 构造两所同向持仓触发 firm 级集中上限,系统产生阻断(已在 §4.4 风控层审计中通过读代码 file:line 三方交叉验证,未来可补一条端到端集成测试固化)。

### P1-7 · 多 alpha 衰退检测与 live de-risk · CODE MITIGATED / AUTO-REDUCTION AUTH OPEN
- **已接线**:`tools/alpha_maintenance_sweep.py` 从真实 `paper_track.db` 经 `paper_health_by_alpha` 自动构造 live-vs-backtest 健康证据，以恒非人 actor 调 `run_maintenance`，经审计生命周期降至 DEGRADED/PAUSED/RETIRED；`scripts/register_schedules.ps1` 注册周度任务。快照由 `services/alpha/live_linkage.py` 翻成策略状态，`live_track_advance.py` 对 PAUSED/RETIRED 在 submit 模式停止新增敞口。
- **安全边界**:无 paper/backtest 参照即零动作；非人到不了 APPROVED；DEGRADED 只告警；维护路径无 broker/order import。现有仓位不自动减仓，避免 agent 擅自选择风险比例。
- **剩余授权**:DEGRADED/PAUSED 要减到多少、RETIRED 是否全平属于资本风险参数和人工批准；近实时 cadence 也未完成。当前完成“自动发现→审计降级→停止恶化”，尚未完成“自动下减仓单”。
- **验收现状**:衰退指标→自动 sweep→状态降级→live 停止新增已有端到端/结构测试；仓位实际下调验收等待人签 de-risk 比例与 full-flatten 规则。

### P1-8 · 承重数据质量与前向证据隔离 · CODE MITIGATED / ARTIFACT GATE BLOCKED (2026-07-13)
- **已修复**:严格接入现在先完整解析并按 instrument 检查拒绝行、空批、重复、原始到达顺序、未来时间、显式 cadence/边界缺口,失败时零写入；SQLite 批写在单事务内完成,中途错误整批回滚。喂 `crypto_funding.db`、1h/1m 验证数据和周度前向证据的四个 K 线驱动器由 AST 测试钉死 `strict=True + expected_interval`。funding 原始观测不再静默跳过畸形/非有限/乱序记录,日序列须有限、UTC 日网格连续且不越过期望边界；共享缓存改原子替换,部分抓取失败不会发布半份文件。
- **前向闭环**:`realize_due` 接受 `blocked_instruments` 与 `quality_evidence_current`;任一 pending 权重命中失败 ticker 时保持 pending 并告警。`--no-refresh` 明确表示“无本轮质量证据”,同时阻断新决策和兑现,不再把未检查冒充无失败。普通网络抓取失败也通过结构化 `failed_tickers` 关联到权重。兑现价格已从现货 close 改为 Binance USD-M 永续 mark price；质量门检查最近 8 个已收盘日,默认尝试保留 16 日定价覆盖。前向驱动器现同时要求 raw archive integrity PASS 且 mark/funding/OI dataset 齐备；lineage 缺口与数据质量缺口同样暂停证据钟。
- **raw lineage**:`services/data/raw_archive.py` 保存 SHA-256 对象 + 不可变 observation sidecar(source URI/provider/instrument/observed_at/representation/解析接受/拒绝/重复计数)，并验证对象、sidecar digest 与 schema；daily/1h/1m、mark/OI/funding 和批量 funding 研究入口均已接线。ZIP 保存精确 body；JSON 源诚实标 `CANONICAL_JSON_UTF8`。`raw_data_lineage_backfill.py` 只读重取并与 canonical cache 对账，不改缓存；vendor transient 可从已验证 event-time observation 重放，原错误保留为 warning。evidence 绑定当前三 cache SHA、scope、8 日窗口与 archive aggregate，cache 字节变化即失效。
- **刷新运行可靠性**:四源现在各自在独立 `spawn` 子进程运行，父进程按 source 15/20/30 分钟与全局 30 分钟绝对 deadline 终止并回收超时进程；`BUDGET_EXCEEDED`/子进程崩溃结构化阻断该源完整 ticker 宇宙。`RefreshHistoryStore` 以 SQLite WAL/FULL、禁止 UPDATE/DELETE trigger、canonical status hash 与 `prev_hash -> entry_hash` 持久化每轮状态，并在查询时重算主状态及规范化 source 行，篡改后拒绝续链。SLO 同时检查 30 轮成功窗口、99% instrument coverage、连续 3 次失败、最新水位和全局耗时；`NOT_FOUND` 且缓存质量通过只记 warning，不伪装 source failure。
- **当前制品实测(2026-07-13,三轮真实 backfill + 两轮真实刷新)**:最终 mark 192/192(192 refetch)、OI 192/192(112 refetch + 80 archive replay；80 transient warnings)最近 8 日逐 ticker/day 对账一致；archive 384 unique objects / 1065 observations、0 invalid、integrity PASS。funding 0/192，含 24 `TRANSIENT` + 96 canonical 尾部缺失 + 96 refetch 缺失，无 funding observation。两轮刷新总耗时均约 635.5 秒，链 2/2 完整；最新轮 prices 114/115 coverage=99.13%、mark/OI 100%，funding 0/115 且连续失败 2 次、水位 5 天，故 `data_refresh_slo.json` 为 `BREACH/BLOCK`。`data_quality_admission.json` schema v2 仍因 funding quality、dataset 缺失、backfill verdict/source 失败而 BLOCK；没有跨 venue 静默填充。`--no-refresh` 对抗运行前后两条 forward decision 均保持 1 pending / 0 realized / 0 forward period。
- **剩余风险**:只有 funding 源恢复、补齐 observation 并由同一 manifest 双门通过后才能关闭。刷新机制已完成，但只有 2/30 轮真实历史，尚不具备长期成功率/值班响应证据；本地哈希链不能替代外部 WORM/对象锁、异地副本和保留策略。最近 8 日 mark/OI 已 backfill，但历史缓存和 spot/1h/1m 旧制品尚未回填；canonical JSON 不是 wire body。非承重 exploratory/FX 抓取器尚未全部迁移。`crypto_daily.db` 的 P2-2 双乘消费 bug 已闭合；旧 CoinGecko daily 制品仍缺逐请求 raw archive，由本条 lineage 缺口继续追踪。
- **证据**:`evidence/{data_quality_admission,raw_data_lineage_backfill,data_refresh_slo}.json`、`services/data/{raw_archive,refresh_history}.py`、`tools/{raw_data_lineage_backfill,data_quality_admission,data_refresh_slo_evidence,live_data_refresh,binance_perp_mark_fetch,source_failures}.py`、`tests/data/test_{raw_archive,raw_lineage_backfill,refresh_history,data_refresh_slo_evidence,market_provider,persistent_store,load_bearing_ingestion,binance_funding_fetch,cache_io,data_quality_admission}.py`、`tests/tools/test_{binance_perp_mark_fetch,binance_oi_metrics_fetch,source_failures}.py`、`tests/research/test_{live_data_refresh,forward_track,forward_track_gate,crypto_alpha_search_funding_cache}.py`、`docs/engineering/plans/2026-07-13-data-quality-enforcement.md`。

### P1-9 · promotion_api 自报身份绕过 · M3 MITIGATED / M4 EXTERNAL EVIDENCE OPEN
- **已修复(2026-07-12)**:生产 API 删除共享治理 token 与自报操作者；OIDC access token 必须通过 allowlist 非对称验签、issuer/audience/时效/MFA、RFC 8705 mTLS 证书绑定和动作 RBAC。actor/audit ID 只能来自 `issuer|subject`，旧 header 全部拒绝。浏览器使用 Authorization Code + PKCE S256，事务一次性消费，access token 只驻内存。13 个攻击案例、真实依赖注入和三方 subject 分离测试已进门禁。
- **M3 增量(2026-07-15)**:token role 不再是最终授权事实。精确 `issuer|subject` 应用注册表承载 HUMAN/WORKLOAD、enabled/revoked、granted roles、token epoch 与 access-review expiry；注册表和 authorization decision 共用 append-only SQLite CAS authority。WORKLOAD 到不了人类治理动作，production authority 缺失时 API 失败关闭。
- **剩余风险**:外部 OIDC issuer 运营、可信 mTLS 终止器、密钥托管轮换、真实 joiner/mover/leaver、独立访问复核及外部 workload attestation 仍无部署/运营证据，因此 `GOV.IDENTITY.STRONG_AUTH` 只到 M3/PARTIAL，红线不关闭。
- **证据**:`docs/audit/2026-07-15-strong-identity-authorization-review.md`、`services/governance/identity_authorization{,_store}.py`、`config/identity_authorization_registry.json`、`evidence/strong_identity_baseline.json`、`apps/research-console/src/lib/api/governance-pkce{,.test}.ts`、`tests/governance/test_{strong_identity,identity_authorization,identity_authorization_store,promotion_api_boundary,promotion_api_endpoints}.py`。

### P1-10 · 通用 restore 证据误报 L2 M4 · CODE MITIGATED / M4 OPERATIONS OPEN (2026-07-15)
- **原问题**:`OPS.RESTORE.DRILL` 读取旧 `evidence/restore_drill_latest.json`；该文件只恢复 live/testnet 通用文件，`mode=ENCRYPTED`，没有 L2 authority source contract、manifest 或 SQLite 证明，却因弱 checker 获得 M4 PASS。
- **已修复**:单次 backup 证据继续由 `l2_backup_evidence` 严格验证且固定封顶 M3；restore 红线已切到专用 `l2_restore_history_evidence`。新 SQLite authority 以 STARTED/OUTCOME 双事件、WAL/FULL、全局 CAS、哈希链和全表不可更新/删除保留每次 PASS/FAIL/超时；健康查询动态检查 latest terminal、clean lookback、open timeout、freshness、gap、history span、success count 和 exact contract。外部 checkpoint 原语只验 Ed25519，精确绑定当前链头、issuer/key、对象版本、seal age 与 retention。legacy checker 从顶级 registry 删除，generic 工具默认输出改名。治理边界钉死 10 required + 6 optional 的 path/role/requiredness，新增 restore-history authority 为 required。
- **剩余风险**:当前没有真实 external L2 batch/history/checkpoint；trust registry 默认空。代码已能在完整 30 天 history + 16 successes + 当前签名 checkpoint + 90 天 retention 时验证 M4，但仓库没有伪造这些运营事实。managed secret rotation、WORM/object-lock 的真实对象版本、独立调度/heartbeat 漏跑收据仍缺，因此红线保持 M3/PARTIAL。
- **证据**:`tools/l2_backup.py`、`services/governance/l2_restore_{history,checkpoint}.py`、`services/governance/top_tier_assessment.py`、`config/{top_tier_requirements,l2_restore_checkpoint_trust_registry}.json`、`tests/tools/test_l2_backup.py`、`tests/governance/test_{top_tier_assessment,l2_operations_boundary,l2_restore_history,l2_restore_checkpoint,l2_restore_top_tier}.py`、`docs/audit/2026-07-15-top-tier-continuation-review.md`。

---

## P2 风险(质量 / 可维护性 / 扩展性)

### P2-1 · 接入核心乱序/缺口检查失效 · MITIGATED (2026-07-13)
- **原问题**:`file_ingest` 曾把 event time 排序后再查乱序,使检查恒为 0；缺少期望 cadence/边界时 completeness 也无法发现缺日。
- **已修复**:`build_ingestion_report` 保留每个 instrument 的原始到达顺序并独立检查,避免多标的交错被误判；承重调用方传入真实 cadence/边界。strict 失败发生在任何 store mutation 之前,且原子批写失败整批回滚。
- **验收**:乱序、内部/边界缺口、空批、拒绝行、跨 instrument 交错、SQLite trigger 中途失败均有失败→零写入测试；P1-8 记录现有制品仍需刷新重验的独立状态。

### P2-2 · quote volume 被二次乘价导致 ADV/容量失真 · CODE MITIGATED (2026-07-13)
- **根因更正**:原审计把 BTC ~$3.7Q vs EOS ~$20k 归因于 `crypto_daily.db` 跨币单位混乱。逐链复核证明数据库由同一 CoinGecko `total_volumes` 字段生成，语义统一为 `QUOTE_USD / ROLLING_24H`；真正错误在 `scenario_tail_research._trend_capacity` 又算了一次 `close * volume`，把 USD volume 变成无意义的 price×USD，BTC 被高币价放大、EOS 被低币价缩小，制造出约 17 数量级假象。CryptoCompare `volumeto` 同样是 quote USD；Binance kline volume 才是 base asset。
- **修复**:`VolumeUnit`/`VolumeSemantics` 进入 canonical contract；任何映射 volume 的 `SourceSpec` 未声明单位/周期语义即构造失败。`quote_notional_volume` 仅对 `BASE_ASSET` 乘价格，对 `QUOTE_USD` 原样使用；scenario trend 容量改走该 helper。原“比值证明单位错误”检查更名为 `volume_dispersion`，仍可保守拒绝极端异质容量输入，但不再伪称单位证据；旧 API 只作兼容别名。
- **真实制品复核**:`crypto_daily.db` SHA-256 `5ffd275ff0e417c02db27e12eb81150b365c845d03233845d20af93d2d421b42`，15036 行/42 币。修复后中位 quote ADV:BTC `$43.53B`、ETH `$23.13B`、EOS `$119.65K`、MKR `$310.39K`；18 币容量宇宙 max/min `3.64e5 < 1e6`，trend 诊断恢复为 `OK`，参与率绑定容量约 `$47,860`(46 周)。该数仍是短样本/未校准诊断，不是部署额度。
- **剩余边界**:旧 daily DB 没有逐请求 raw archive/sidecar，单位语义由生产代码与制品哈希而非 vendor wire evidence 回证；这留在 P1-8 lineage。`Bar.volume` 本身不携带单位，承重消费必须从对应 `SourceSpec` 带入；shares/contracts 在缺 multiplier/FX metadata 时 helper fail-closed。
- **验收**:quote volume 不再二次乘价、base volume 仍正确转 quote notional；5 个 K 线/聚合 source spec 均显式声明语义；极差只报 `EXTREME_VOLUME_DISPERSION`。定向 40 tests 通过。

### P2-3 · 紧急减仓 drawdown 全平依赖峰值权益持久化正确 · CODE MITIGATED / SOURCE ANOMALY OPEN (2026-07-13)
- **已修复**:`live/execution/equity_state.py` 把账户级 `live_equity*.jsonl` 升为峰值上界证据。周度驱动器与小时哨兵均先追加本轮真实权益，再要求 `live_risk_state*.json` 的峰值为有限正数且不高于全历史有效观测最大值；状态缺失可从历史重建，合法状态写 schema v2 + 最近观测/证据最大值。损坏、非有限、非正或高于历史的状态返回不可用峰值 `0`，现有风险引擎据此产生 `peak_equity_unknown`、闩锁 kill-switch、硬停常规执行并高声告警，但 `flatten_target_for_breach` 对纯数据质量故障返回 `None`，不会单轮误全平。异常原文件不覆盖，保留取证。
- **未改政策**:20% 回撤阈值、真实回撤全平语义、敞口越限 90% 目标均未修改；没有新增倍率或确认时延。当前 Binance/Bybit demo 缓存实测峰值分别与各自历史最大值精确一致；无历史且 peak=0 的未配置 Bybit 状态 fail-closed。
- **剩余风险**:本地 JSONL 不是哈希链/WORM，删除/篡改会造成拒绝服务；若上游本身返回异常高权益并同时进入历史与峰值，两份数据会一致，本门不能识别，仍依赖已签 15% 权益跳变告警与人工核查。现金流/入出金调整后的 high-water mark 尚无独立账本，权益历史也只按 venue 分文件、未绑定不可变 account identity，换账户凭据前必须人工归档/重建基线。是否对证据有效的真实回撤全平增加人工确认/一轮延迟仍需用户决策。
- **验收**:异常峰值注入只产生 `peak_equity_unknown` 且 auto-flatten=`None`；两个承重入口的 AST 治理测试禁止回退到旧标量加载；P2-3 聚焦 243 tests、完整 `tests/live` 764 tests 全绿。最终统一门禁：主套件 2102 passed / 1 skipped，live/trading/tools 882 passed，前端 68 tests + typecheck/build，治理扫描 246 文件零违规。

### P2-4 · 包级循环依赖 + god-package/god-module · GRAPH ACYCLIC / ADAPTER BOUNDARIES MITIGATED / RESEARCH PACKAGE OPEN (2026-07-14)
- **已完成**:第一步以 `ResearchSearchRuntime` 注入删除 research→intelligence，正式组合入口上移 intelligence。第二步新增确定性生产 service AST 图：绝对/相对/top-level-from/字面量动态 import 全覆盖，测试目录排除，语法错误失败关闭；非字面量动态点也需精确基线。`config/service_import_policy.json` 与当前循环边完全相等，`evidence/service_import_graph.json` 精确快照进入 `validate.py`、供应链 manifest 和顶级统一门要求。任何新增循环、图漂移、篡改或陈旧权限均拒绝。
- **第二条拆边**:`services/portfolio/signal_adapter.py` 原来只为 `CostModel` 反向 import research。现将同一不可变值对象上移 `quantos/contracts/cost.py`，研究旧路径只兼容重导出；类身份、默认值、冲击和异常语义均由测试锁定。portfolio→research 从图中消失，portfolio 与 intelligence 均已退出循环 SCC。
- **第三条拆边**:把 `PaperFill/OrderSide/FillMethod` 上移 `quantos/contracts/paper.py`，平均成本对账和 impact/calibration 正本迁到 execution；research 旧路径仅重导出且身份相同。干跑 pipeline 与正式 driver 使用 execution 正本，execution→research 零命中。旧 policy 因两条陈旧循环权限失败后才收紧，证明门真实承重。
- **第四条拆边**:research console 通过一次性 `RiskConsolePort` 注入风险诊断，正式只读组合根上移到 `services/console`；research→risk 零命中，risk 退出 SCC。组合端口未配置时失败关闭，运行时禁止替换。
- **第五条拆边**:mutating promotion HTTP 组合根从 `services/governance/promotion_api` 迁到独立 `services/promotion_api`。治理域只保留审批、强身份和策略原语，不再反向 import alpha/research；旧 policy 先真实拒绝 4 条陈旧循环权限，随后才收紧。promotion API 的 OIDC+mTLS+MFA+RBAC、三方状态机、审计和 L2 安全语义不变。
- **第六条拆边**:把性能指标、IC、regime、DSR 与验证门的纯实现抽到中立 `services/validation`，research 旧路径只做身份保持兼容；`factor_set` 归回 alpha，CostModel 全部使用 contracts 正本。alpha→research 零命中，validation 对其他 service 域零依赖。旧 policy 先拒绝最后两条陈旧权限，随后收紧为空。
- **第七条拆边**:验证编排通过必填 `BacktestExecutionPort` 调用具体 engine；规范化 `BacktestOutcome` 拒绝空/非有限曲线，pipeline 对返回类型及 OOS 长度失败关闭。确定性 adapter 与 port-double 都有行为测试，validation 不再 import concrete engine。walk-forward/CPCV 正本归入中立 `services/validation`，旧路径只保留对象身份兼容。
- **第八条拆边**:mutating HTTP/tool runner 上移独立 `services/research_runner`；15 个 read-only FastAPI routers 连同 data/risk ports 上移 `services/console/api`。旧 research 路径不留 shim，避免 `research↔console/research_runner` 回环。正式启动入口、供应链 manifest 和测试均迁移；44 paths、GET-only、772 行 root 与 payload 契约不变。
- **当前事实**:16 个域、36 条跨域边，`cyclic_sccs=[]`、`cyclic_edges=[]`。`services/research` 对 FastAPI/uvicorn/tools/data 零 import；四类 persistence ports 和四个 SQLite adapter owner 仍被机器锁定。边数增加来自把 console/runner 的真实依赖归到 adapter owner，不是新增反向环。backtest/validation/API-tool 本轮切片已闭合，research 的进一步子域/进程模块化仍开放。
- **下一步**:保持本条边界不回流，优先推进 P2-5 单机可靠 consumer/outbox/offset/DLQ；之后再评估跨主机 HA。console 不得把实现塞回 composition root，registry/paper core 不得重新 import concrete stores。
- **验收**:回测端口/output contract、validation 兼容身份、runner 安全白名单与 console 44-path 契约均有测试；service 图、Python 0 漏洞与 320 组件供应链制品已刷新。本增量最终统一门禁：主套件 2204 passed / 1 skipped，live/trading/tools 902 passed，前端 68 tests + typecheck/build，治理扫描 296 文件零违规。机器结论仍为 `NOT_TOP_TIER,16/22 PASS`。

### P2-5 · 无事件总线 / 服务紧耦合(直接 import + 共享 sqlite) · L2 SCHEDULED SINGLE-NODE EVENT OPS MITIGATED / HA OPEN (2026-07-14)
- **已承重**:`DurableEventBus` 从孤立 SQLite 表升级为覆盖 canonical Event + correlation/causation + 前序摘要的 SHA-256 追加链；WAL/FULL、跨连接 `BEGIN IMMEDIATE`、UPDATE/DELETE 拒绝触发器、全链重放校验、旧库迁移和 `publish_once` 同内容幂等均已测试。绕过触发器改元数据/删中间事件后，回放和继续发布失败关闭。
- **真实 producer**:`services/execution/events.py` 为单期/多期干跑产生 started、逐意图 pretrade approved/rejected、completed；七个正式 dry-run driver 共用 `domain_events.db`。Alpha maintenance 产生 DEGRADED/PAUSED/RETIRED transition；human-only promotion 产生 `research.alpha.promoted`；kill-switch arm/reset 与 deployment evaluation 也有严格 schema 的 fail-closed producer。市场数据刷新产生 `data.market.refresh.recorded`；数据质量/lineage 准入产生 `data.quality.admission.decided`；已真实运行的 dry-run 风险子集产生 `research.risk.dry_run.evaluated`，并逐字段绑定精确 run。started/pretrade 在纸面动作前持久化，写失败阻断 pipeline。
- **持久 consumer**:`event_consumer_journal` 是独立哈希追加链，承载 consumer/worker identity、caller-supplied 租约、跨连接 claim、过期重领、owner/lease ack、offset、retry 与 DLQ。错误原文不落库，只留类型与 SHA-256 指纹；达到 `max_attempts` 后追加 DEAD_LETTERED、推进该 poison offset 并抛 `EventDeadLetteredError`。语义是 at-least-once；崩溃后重复 delivery 由下游幂等处理，不宣称 exactly-once。
- **承重 projection**:`DomainEventProjection` 以 WAL/FULL、append-only trigger、完整 Event+causal metadata 哈希链和 seq/ID/content 精确幂等承接 consumer。正式 7 driver + maintenance 都推进它；projection commit 后/offset ack 前崩溃重投不会重复写。console `/events` 首选该真实源并做事件类型+字段白名单，损坏/缺失时诚实回退。
- **真 outbox 范围**:① dry-run `completed` + research-risk event 与不可覆盖的 `DryRunRun` 同事务并绑定完整 run SHA-256；② `APPROVED` catalog snapshot 与 promotion event 同一 `experiment_registry.db` 事务；③ maintenance snapshot 与 transitions 同一 `alpha_maintenance.db` 追加记录；④ kill-switch ARM/RESET 与权威状态同一 `live_kill_switch.db` 追加记录；⑤ deployment evaluation 与 canonical event 同一 `deployment_state.db` 追加记录；⑥ market-data refresh 主记录/规范化 source 行与 redacted event 同一 `live_refresh_history.db` 事务；⑦完整 data-quality admission evidence/decision 与 redacted event 同一 `data_quality_admission.db` 事务。七条 dispatch 均为 `publish_once` 后追加不可变 ack，故 publish/ack 崩溃窗可重放。started/pretrade 保持 evidence-first。
- **schema / migration**:`EventSchemaRegistry` 对已知 type/version、严格 payload/source/domain/criticality 和显式 consumer compatibility 失败关闭；`dry_run.completed` 从 1.0→1.1 保留新消费者读取旧历史。event bus 首次迁移以 `BEGIN IMMEDIATE` 串行，WAL 并发打开竞态已有回归。
- **时间单位 / schema catalog**:canonical 最新版本统一使用 Unix epoch microseconds；历史上真实写入 milliseconds 的 kill-switch、alpha promotion、deployment v1.0 保留显式单位，新 producer 升至 v1.1。任何 consumer/projection 只能按 `(event_type,event_version)` 归一化，禁止数值量级猜测。确定性 Catalog 覆盖 12 types/17 definitions，记录 timestamp unit、consumer compatibility、payload JSON Schema + SHA-256 并绑定 20 个实现文件；缺失、陈旧或篡改会同时触发 catalog gate 与 supply-chain digest 失败关闭。
- **consumer group 协调**:`event_consumer_group_journal` 是与 Event/consumer journal 同库的第三条 append-only SHA-256 链。固定 partition topology + session timeout，heartbeat membership，排序 round-robin eager rebalance，generation fencing，跨重启 partition offset 均已承重；`correlation_id` 优先的稳定哈希保护同一因果 key 的分区内顺序。内部 offset ID 采用保留前缀 + group hash，时间回拨、旧 generation、非 owner、过期 member 和逆序 ack 均拒绝。正式 projection 固定 1 partition 保护全序，运行前 heartbeat、正常退出 leave、崩溃后 timeout 接管。
- **deployment evaluation authority**:`DeploymentDecisionStore` 将 gate 裁决与 event 原子写入，同步保留 actor/reasons/version IDs/approval causation。状态只允许 `DENIED` / `ELIGIBLE_NOT_APPLIED`；L2 没有 `DEPLOYED`。authority 写失败把允许降为 `DEPLOYMENT_STATE_ERROR`，同毫秒不同内容有不同 ID。正式 drain 工具仅搬运+投影，console 只读白名单字段；无部署执行能力。
- **lag / retention health**:`operational_snapshot` 逐 partition 计算真实 pending/high-watermark/offset 与最老事件年龄，并覆盖 group 所有内部 consumer ID 的 DLQ；三条链完整性、event/consumer/coordination 行数和 allocated bytes 进入显式阈值策略。console 与离线调度工具均能展示/投递结构化告警。retention 是 `APPEND_ONLY_ALERT_ONLY`，没有自动清理、压缩或改写哈希历史。
- **独立恢复 drain**:`tools/domain_event_outbox_drain.py` 不依赖原 producer 重跑，按固定顺序打开七个 owner store（均 `create=False`），逐源验证/dispatch、错误只留类型、一个坏源不阻断其余源，最后只推进一次 shared projection。event authority 已存在时也必须先通过只读三表 schema preflight 再 `create=False` 打开；缺 schema 的 SQLite 字节不变并 `BREACH`，只有 authority 路径不存在且至少一个 source 存在才可创建。全缺失不创建空 authority；部分缺失报 `PARTIAL`。它没有策略评估、domain-state mutation、kill-switch reset、deployment/order 或 retention mutation 能力。
- **L2 调度闭环**:`tools/domain_event_ops.py` 固定 drain→health 顺序，任一异常只留类型且不阻断另一半，写统一状态和本地告警；PASS/BREACH/其他分别退出 0/1/2。`register_schedules.ps1` 用独立 executable/arguments/working-directory、`IgnoreNew` 和 L2 module allowlist 注册每小时任务，治理测试禁止 live module、action flag、硬编码路径和 shell action。首轮计划任务按时执行并诚实返回 1：shared projection/health PASS，但 dry-run/experiment/data-refresh 三个现存 owner store 严格校验 BREACH，另外三个 owner store UNAVAILABLE。
- **现场纠错**:首次手工探针暴露 drain 曾对 shared authority 使用 `create=True`，把一个 4 KiB、无 `event_log` 的现存 SQLite 初始化为空 authority。未猜测回滚该用户文件；修复后 drain 与 health 都有“现存缺 schema 文件逐字节不变”反例。该事件作为工具自身发现并修正的 non-mutation 缺口保留在案。
- **owner-store staged migration**:`tools/domain_event_owner_migration.py` 对三份真实 legacy DB 执行 plan→停调度→immutable/integrity/protected-row digest→SQLite logical backup→同目录 staging owner migration→链验证→源 SHA 二次确认→atomic replace→再验证。dry-run 7 行、experiment 2 trials/2 alphas/8 evaluations、refresh 5 history/20 source rows 的 protected digest 前后逐项一致；三份本地 rollback DB 保留。dry-run/experiment 不伪造旧事件；refresh 按既有 deterministic legacy contract 回填 5 条 `data.market.refresh.recorded`，已全部 publish/ack/project，pending=0、DLQ=0。`RefreshHistoryStore(create=False)` 也改为缺 schema 时在 WAL 前 immutable preflight，失败不再制造 sidecar。
- **kill-switch 原子性**:`KillSwitchStateStore` 以 WAL/FULL、`BEGIN IMMEDIATE`、CAS、transition/dispatch 双哈希链和 UPDATE/DELETE trigger 承载状态机；ARM 只允许 CLEAR→TRIPPED，RESET 必须精确引用 activation。周度 driver、小时 sentinel、三方 reset CLI 与 console 已迁移，legacy TRIPPED 仅在事实完整时重建 activation outbox。并发重复 ARM、状态/事件不一致、损坏权威库、stale mirror 与构造失败句柄泄漏均有反例测试。恢复演练 v2 把 `kill_switch.db` 纳入重启哈希与故障注入。
- **L2 backup + scheduler watchdog**:`tools/l2_backup.py` 对 10 个必需、6 个可选 authority store 使用 SQLite online backup、AES-GCM、加密 manifest、原子批次和发布前隔离 restore/SHA-256/integrity_check；v2 exact source contract 绑定 path/role/requiredness，恢复端验证 digest、required/optional 完整分区，并对 restore-history DB 做语义验链。记录式 CLI 在 preflight 前追加 STARTED，PASS/FAIL 后才更新非权威 status/evidence mirror；普通失败只留稳定脱敏码，硬中断留下 open attempt。默认拒绝 workspace/同卷目标，source allowlist 不含 `key/`。`tools/l2_scheduler_watchdog.py` 不 import 被监控 worker，每 15 分钟核对 Task Scheduler action/result/cadence，并以 `create=False` 合并同一 restore-history policy；即使 task exit=0，history 缺失/损坏/空/失败/陈旧/超时/断 cadence 仍为 BREACH。未配置 backup 继续标 `UNAVAILABLE`。当前仍无合格 external batch/checkpoint；代码机制承重不等于 M4 运营事实。
- **三链外部锚 + heartbeat 接缝**:`EventAuthorityCommitment` 覆盖 event/delivery/coordination 三条链的 count/max-seq/head 与 overall hash；extension verifier 在重验全链后检查锚定前缀，允许追加、拒截断/整体替换。`domain_event_seal.py` 只从 read-only snapshot 导出 content-addressed create-once 文件，同名冲突拒绝覆盖，永不 DELETE/VACUUM，且固定标 `PARTIAL_EXTERNAL_WORM_UNVERIFIED`。`external_heartbeat.py` 只发送枚举/计数/链健康白名单，拒绝非 HTTPS、userinfo、fragment、loopback 和 redirect，URL 只留 fingerprint。两任务仅在 external config 合法时注册；当前现场为 6 PASS/1 PARTIAL/3 UNAVAILABLE/0 BREACH。
- **worker assurance**:新 coordination/delivery 行把调用方 worker 名编码为固定 `PROCESS_DECLARED_UNAUTHENTICATED` 内部身份，直接进入既有两条 SHA-256 链；保留前缀禁止调用方伪造。public health 解码名称并显式显示 assurance，external heartbeat 只发枚举不发 worker 名。旧 raw 行不重写并标 `LEGACY_UNLABELED`，同名旧活跃 member 先追加 LEFT 再切换。真实 authority 演练链有效、health PASS。此处只消除“无标签字符串看起来像强身份”的误报，不是认证。
- **2026-07-15 最终验收**:data admission + worker assurance 聚焦回归、真实 BLOCK authority publish/ack/project 与现存 authority heartbeat/leave 演练均通过；最终统一门禁 18/18 全绿：主套件 2353 passed / 1 skipped，live/trading/tools 971 passed，前端 68 tests + typecheck/build，治理扫描 312 files / 0 violations。Catalog 12 types/17 definitions/20 bindings，服务图 16 domains/36 edges/0 cycles，供应链 320 components；机器结论仍为 `NOT_TOP_TIER, 15/22 PASS, 5 PARTIAL, 2 MANUAL, 0 FAILED`。
- **data admission authority 增量**:`DataQualityAdmissionStore` 对完整 evidence JSON 独立复核 scope/verdict/readiness/price-basis 语义，保存 evidence/decision SHA-256、追加式主链与 dispatch 链；决策和严格 redacted event 同事务。周度 forward track 现在同时要求刷新检查和权威 admission PASS，authority 故障或 BLOCK 均暂停 record/realize。真实 24 币运行仍为 quality/lineage 双 BLOCK；一条事件已 publish/ack/project，pending=0。该库已列入 L2 backup 必需源。
- **仍开放**:当前四个本地 owners PASS、三未启用 authority UNAVAILABLE，所以 event ops 保持 `PARTIAL/exit 2`；备份实现已承重但尚无合格 secret/destination 与真实 external batch，迁移 backup 仍只是同机 rollback。heartbeat 与 seal 接缝已承重但没有 operator-owned endpoint、missed-heartbeat 告警收据、WORM/object-lock 或外部签名证明，不能宣称带外 SLA/外部 seal 已完成。funding quality/lineage 仍 BLOCK；部署实际 APPLIED/DEPLOYED 状态与执行器不存在；更广 data/risk producer 尚未接。无跨主机 membership/consensus、broker/复制/HA；worker identity 已明确为哈希绑定的进程自声明但仍未外部认证，协调时钟仍由 caller 注入。当前没有安全 compaction。SQLite 单节点不等于 Kafka。
- **本增量验收**:备份/看门狗/调度/成熟度 checker 与事件 console 契约聚焦 58 tests 全绿；Ruff、PowerShell parser、治理扫描、Catalog、service graph、supply chain、assessment 和 diff checks 全绿。最终统一门禁 18/18：主套件 2334 passed / 1 skipped，live/trading/tools 954 passed，前端 68 tests + typecheck/build，治理扫描 311 files / 0 violations。机器结论 `NOT_TOP_TIER`：15/22 PASS、5 PARTIAL、2 MANUAL、0 FAILED。
- **外部锚/heartbeat 增量验收**:事件/consumer/projection/ops/调度/外部边界 128 tests 全绿；真实 authority 诊断 commitment 为 5 event/10 delivery/33 coordination，extension verify PASS，临时锚已清理；heartbeat 无配置探针只写 `UNAVAILABLE`，未发网。首次全门禁唯一失败来自 Windows spawn 冷启动挤占测试的 2 秒健康预算；生产 hard deadline 不变，测试健康/global budget 调为 5 秒、hang 仍 200ms，四路并发复证 4/4。最终门禁 18/18：主套件 2338 passed / 1 skipped，live/trading/tools 969 passed，前端 68 tests + typecheck/build，治理扫描 311/0。
- **验收**:租约竞争/过期、旧 owner 拒 ack、跨重启 offset、retry→DLQ、错误脱敏、schema 兼容、并发迁移、六类 outbox 同事务回滚、快照/链篡改、publish-before-ack 重放、data legacy migration、risk/run 精确绑定、partition lag/age、DLQ/容量告警、console 权威读取和正式 driver 接线均有测试。第五增量以 172 tests 覆盖版本化时间单位；第六增量以 234 tests 覆盖 consumer group；第七增量以 209 tests 覆盖 deployment authority；第八增量增加 data/risk producer 与 alert-only health；第九增量新增 recovery drain；第十增量以 event/monitoring 79 tests、governance/supply-chain 76 tests 覆盖 L2 schedule allowlist、真实计划任务、统一 ops、显式退出码、异常脱敏及 drain/health non-mutation。Ruff、PowerShell parse 与 service graph/Catalog/供应链 `--check` 全绿。Catalog 为 11 types/16 definitions/18 bindings，服务图 16 域/36 边/0 SCC，供应链 320 components。本增量最终统一门禁：主套件 2327 passed / 1 skipped，live/trading/tools 938 passed，前端 68 tests + typecheck/build，治理扫描 311 files / 0 violations，18 阶段全绿。机器结论仍 `NOT_TOP_TIER`：16/22 PASS、4 PARTIAL、2 MANUAL、0 FAILED。

### P2-6 · 实盘新鲜度门宽至 7 天且陈旧时跳过跨源核价 · CURRENT-PRICE MITIGATED / CADENCE OPEN (2026-07-13)
- **已修复**:删除 `_CROSS_CHECK_MAX_STALENESS_MS` 与按决策价年龄放宽偏差的路径。驱动器现在每轮从真实 venue `book_ticker` 读取 `{bid,ask,as_of_ms}`，从 CoinGecko `/simple/price?include_last_updated_at=true` 读取 `{price,as_of_ms}`，以 venue midpoint 做固定 5% 偏差检查，并分别限制源年龄与跨源时间偏差为 5 分钟。`decided_ts` 不再能跳过核价；盘口缺失/交叉/非正/无时间戳、独立源缺失/无时间戳、任一源陈旧或时间偏差过大均逐标的冻结。CoinGecko 整源异常或空响应会冻结本轮全部目标，已有仓位原样不动，不会把“核不上价”放大成主动平仓。
- **兼容边界**:Binance/Bybit 适配器只在响应带合法源时间时增加 `as_of_ms`；无时间戳的盘口仍保留 bid/ask 供旧 LIMIT anchor 回退使用，但新同点核价会拒绝它。旧 `fetch_simple_prices` 请求形状与返回类型未改。
- **仍开放**:`_MAX_STALENESS_MS=7 天` 仍服务当前周频信号门。没有用户签署的新调仓 cadence 前，本轮不擅自把它收紧到小时级。5%/5 分钟是明确标注的 `UNCALIBRATED` L2 工程基线；逐标的 REST 采样不是原子快照，通过观测也尚未形成长期校准数据集，需用 testnet/demo 留存盘口与独立源观测后再校准。
- **验收**:P2-6 聚焦 157 tests 全绿；结构测试钉死驱动器使用 `real_venue.book_ticker`、带源时间戳 CoinGecko 接口且无年龄跳过常量/告警；拥挤证据因驱动器实现哈希变化已重生成并重放通过（234 周、21 个 signed-z blocks）。随后统一门禁全绿：主套件 2113 passed / 1 skipped，live/trading/tools 901 passed，前端 68 tests + typecheck/build，治理扫描 247 文件零违规。剩余验收是 cadence 决策后收紧 7 天 feed 门，并积累源观测校准证据。

### P2-7 · live-wired 拥挤门校准证据 · SIGNED Z-GATE MITIGATED / GENERIC DEFAULTS OPEN (2026-07-13)
- **已闭合**:用户 2026-07-07 签署 crowd_contra 的 BLOCK 权威为 short-leg own-history `global_ls` z-score `z*=3.0`，rank 平均路径 `average_warn_only=True`。live、基准干跑、signal-adapter 干跑三条承重入口常数一致并有 drift-lock。`tools/crowding_metric_research.py` 现冻结 234 周每期真实门输入，确定性 gzip bundle + manifest 绑定四个原始制品 SHA-256；`--check` 重放真实 `evaluate_crowding_gate` 并核对实现哈希、签署常数、汇总和制品完整性，已接入统一门禁。
- **实测**:`z*=3.0` 在 234 周阻断 21 周(8.9744%)；2.0/2.5/3.0/3.5/4.0 分别阻断 61/38/21/9/5 周，单调合理。dry-run trailing rank 平均恒 0.500；live extremity rank 平均 0.500–0.655，但两者均 WARN-only。两种 rank 语义下 21 个 BLOCK 周逐项一致，证明真正阻断只来自共同 z 证据；当前 book max 单名/gross 仅 0.0833，集中度门不触发。
- **剩余风险**:历史敏感性不是因果损失证明；L2 无 venue order-book、强平/踩踏标签或生产成交样本。通用旧调用方的 average threshold=0.60/warn=0.45/max_name=0.25/max_exit_days=5 继续保持兼容，但只标 `LEGACY_GENERIC_DEFAULTS_SCANNED_NOT_PRODUCTION_CALIBRATED`；其中 exit-days 维度当前 live 未传 ADV/book_notional，不能冒充已承重。carry/combo 也未用相同拥挤证据覆盖其宇宙。
- **验收**:`evidence/crowding_gate_calibration.json` 状态为 `SENSITIVITY_EVIDENCE_COMPLETE_SIGNED_Z_GATE`；bundle/manifest/实现或阈值任一篡改使 `--check` 失败；聚焦 86 tests 全绿。最终统一门禁：主套件 2110 passed / 1 skipped，live/trading/tools 882 passed，前端 68 tests + typecheck/build，治理扫描 247 文件零违规。

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
| 3 | P2-1 乱序死检查 | 真 bug修复 | ✅ 已完成 | |
| 4 | P1-1 验证门作 live 前置 | 安全加法 | ✅ | |
| 5 | P1-4 place-后-未落库孤儿窗口 | 安全加法 | ✅ | |
| 6 | P1-5 回填 ctx 历史指纹/时间戳 + 撤单率 | 安全加法 | ✅ | |
| 7 | P3-1 aiokafka 边界洞 | 治理加固 | ✅ | |
| 8 | P2-2 volume 语义/容量双乘修复 | 真 bug 修复 | ✅ | 旧制品 raw lineage 归 P1-8 |
| 9 | P2-3 峰值权益合理性上界 | 安全加法 | ✅ 2026-07-13 证据上界已承重 | 真实回撤确认/延迟策略仍需人工决定 |
| 10 | P1-6 firm 级硬门 | 机制可做,阈值需校准 | ✅(机制) | 阈值人工签 |
| 11 | P1-8 数据质量/lineage 双门 | 安全加法 | ✅ 机制完成/已重验 | funding observation + manifest PASS；历史/off-host archive + 30 轮 SLO/值班证据 |
| 12 | P2-6 同点跨源核价 | 安全加法  | ✅ 当前价路径已闭合 | 7 天 signal staleness 待 cadence 决策；阈值待观测校准 |
| 13 | P0-1 mandate 完整性签名 | Phase 14 前置 | | 人工签 + Phase 14 |
| 14 | P0-2 出进程签名/进程隔离 | Phase 14 | | Phase 14 |
| 15 | P1-7 维护调度器 + 自动 de-risk | 需 forward 指标就绪 | | 部分 Phase 14 |
| 16 | P2-4/P2-5 架构解耦/事件总线 | 结构性大改 | ✅ 生产 service 图无环 + 关键 producer | console/research god-module、consumer/outbox/HA 分批排期 |
