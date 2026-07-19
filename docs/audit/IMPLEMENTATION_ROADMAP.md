# QUANTOS 实施路线图(IMPLEMENTATION_ROADMAP)

> **2026-07-12 排序覆盖:** 本文保留为 2026-07-06 实施历史。当前工作排序与验收以
> `docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md` 为准。最新机器评估为 15/22 PASS、5 PARTIAL、
> 2 manual/external；模型风险治理已建立可复算 M2 基线，下一阶段不能通过本地模拟伪造的两条
> 红线是 `GOV.IDENTITY.STRONG_AUTH` 与 `PLATFORM.SUPPLY_CHAIN` 的外部漏洞/签名证据。

> 审计日期:2026-07-06 · 目标:L4 执行自动化 + L3 风控边界 + L2 治理审批。
> 铁律(每个 Phase 都适用):**不绕过风控 · 不直接实盘 · 不删审计 · 不引入不可控自动交易 · tests-first · 最小安全变更 · 每步过 `python scripts/validate.py` 全绿再提交。**
> 每个 Phase 标注:目标 / 改造内容 / 验收标准 / 测试要求 / 风险。**所有 Phase 都在 L2 内进行,不解锁真钱**;真钱只由最后的 Phase 3A 人工评审 + Phase 14 信任边界共同放行。

---

## Phase 0 — 系统冻结与事实审计 ✅ 已完成(本轮)

- **目标**:建立不依赖幻想、基于仓库事实的基线。
- **改造**:15 维度多智能体审计 + 8 组 P0 红队 + 第一手核实,产出 `docs/audit/` 6 份文档。
- **验收**:✅ 全量门禁 1564 passed;✅ 治理扫描器 0 违规;✅ 每条结论钉 file:line。
- **产物**:`SYSTEM_ALIGNMENT_AUDIT.md` / `GAP_MATRIX.md` / `RISK_REGISTER.md` / `TARGET_ARCHITECTURE.md` / 本文件 / `NEXT_CLAUDE_CODE_PROMPTS.md`。

---

## Phase 1 — 让已建原语承重:P1 LATENT 工程收口(全 fail-closed 加法)

> 本 Phase 是"最短安全路径"的核心:不造新东西,把已建好的 engine 接到承重路径 + 修 2 个真 bug。**每一项都只让系统更严,永不放松风控。**

- **目标**:把 Phase 3A 放行日会集体转正的 P1 LATENT 缺口提前补好。
- **改造内容**(按优先级,每项独立提交):
  1. **mandate 账户级敞口聚合 + 多策略硬停**(P1-2):`evaluate_mandate` 增账户级敞口参数,gross/net 对(现有全量敞口 + 本批 delta)投影;多策略共享未分块 mandate → 硬降 `NEEDS_HUMAN_REVIEW`。
  2. **client_order_id 同数量碰撞修复**(P1-3,2026-07-13 闭合):订单身份纳入 `prior_qty`，账本订单/成交/TCA 改为同 payload 幂等、异 payload 显式冲突；pipeline 冲突时整批在 venue 前停止。
  3. **接入乱序死检查修复**(P2-1):`file_ingest` 在排序前用原始顺序跑乱序检查;传真实 `expected_days`。
  4. **验证门作 live 部署结构性前置**(P1-1,2026-07-13 本地结构闭合):`live_track_advance` preflight 与 `submit_live_batch` 必填参数双层强制每个 strategy_id 有在案 PASS ValidationResult 引用或可审计人工 override；缺失/错策略在任何 venue 调用前拒绝。外部身份/签名和不可变结果解析仍 open。
  5. **place-后-未落库孤儿窗口**(P1-4,2026-07-13 闭合):`place_order` 前强制写 `PENDING_NEW`；冲突/IO 异常专用审计并停止后续批次，place 后写失败仍计 submitted 并进入恢复对账。
  6. **实盘 ctx 回填历史指纹/时间戳 + 撤单率引擎**(P1-5,2026-07-13 闭合):同一 LiveLedger 快照回填 24h 时间戳/终态与 5 分钟 canonical 指纹；24h 撤单率门接入共享 registry，未知状态 fail-closed、纯减仓豁免。阈值保留 L2 未校准标记。
  7. **aiokafka 网络边界洞**(P3-1):把消息/网络客户端加进 `_NETWORK_ROOTS`(显式排除被隔离文件)。
  8. **峰值权益合理性上界**(P2-3,2026-07-13 代码闭合):周度驱动器与小时哨兵均以 append-only
     `live_equity*.jsonl` 最大有效观测验证 `live_risk_state*.json`；坏/虚高状态硬停、闩锁、告警但不自动全平，
     原文件保留取证。未新增风险阈值；上游权益异常与真实回撤确认/延迟策略仍 open。
- **验收标准**:每项都有对应的失败→通过测试;全量门禁保持全绿;新增拒绝路径可审计。
- **测试要求**:TDD——先写复现缺口的失败测试,再修复;不删除/不弱化任何现有测试。
- **风险**:低。全部是 fail-closed 加法或真 bug 修复;主要风险是接线时误改现有行为,由 1564 回归门禁 + 逐项独立提交控制。

---

## Phase 2 — Data Quality Gate + Feature/Alpha 持久化生产化 · CODE COMPLETE / FUNDING ARTIFACT BLOCK (2026-07-13)

- **落地**:P1-8 前向决策门(`services/data/panel_quality.py`,坏/停更数据暂停证据钟留 pending)✅;
  2026-07-13 对抗复核补齐此前遗漏的承重半边:strict 校验在写前完成 + SQLite 原子批写、显式日/时/分钟 cadence、
  funding 原始观测/日序列校验、原子缓存、`realize_due` 按失败 ticker 留 pending,以及 `--no-refresh` 无当前证据时
  同时阻断 record/realize✅。四源真实刷新 + `evidence/data_quality_admission.json` 已完成：永续 mark price
  24/24 PASS、OI 24/24 PASS、funding 24/24 BLOCK(全源 115/115 `URLError` / `WinError 10060` /
  `TRANSIENT`)；因此**代码完成与两源通过仍不等于整套制品已准入**。前向兑现改用永续 mark，不再用现货代理；
  16 日默认覆盖已补齐现有 pending 决策的 14/14 起点，`--no-refresh` 实跑证明账本零推进。
  内容寻址 raw archive + observation sidecar 已接 daily/1h/1m/mark/OI/funding 和批量 funding 入口；真实
  backfill 证明 mark/OI 最近 8 日各 192/192 与 canonical cache 一致；第三轮 OI 仅 112 个 refetch，另 80 个
  vendor transient 由 verified archive replay 补足并保留 warning。三轮共 384 unique objects / 1065 observations、
  0 invalid；funding 0/192。backfill evidence 绑定 cache SHA/scope/days/archive aggregate，admission schema v2
  与前向驱动器均把 quality + lineage 作为联合必要条件✅。
  P2-2 根因闭合:source spec 强制 volume unit/semantics；quote volume 不再二次乘价；容量极差只作 dispersion guard✅;
  P3-4 AlphaAsset sqlite 持久化(`services/alpha/asset_store.py`,fail-closed + 篡改检测 + containment
  守卫经 restore 复用)+ cs_genome 育种谱系接线(`make_child_asset` 记 parent_ids/mutation_op)✅。
  全 TDD；2026-07-13 最新完整门禁 `2080 passed, 1 skipped` + live `856 passed` + frontend `68 passed`。
  诚实边界:幅度价格突刺不硬挡(会误杀 LUNA 真实尾部)；最近 8 日 mark/OI raw 已回填，但 funding 与
  历史/spot/1h/1m 旧缓存未回填；本地 archive 无外部 WORM/对象锁/异地副本/保留策略，JSON canonical
  representation 不是 wire bytes；刷新进程级 deadline、哈希链历史和覆盖率 SLO 已落地并完成两轮真实演练，
  但 funding 当前 `BREACH` 且仅 2/30 轮，长期成功率/值班证据仍缺；gp/evolutionary 谱系未接
  (无 asset 挂载点)、生产驱动器 save/load 注入留一行 open question。
- **目标**:数据异常自动阻断承重决策;研究资产跨会话可查询。
- **改造内容**:
  1. **数据质量强制门**(P1-8):喂承重决策(前向证据、可部署 book)的单标的抓取器统一开 `strict=True + expected_interval`;前向兑现前质量失败或无本轮质量证据均 fail-closed 留 pending。
  2. **volume 语义与容量换算**(P2-2,2026-07-13 闭合):显式 `VolumeUnit/VolumeSemantics`；base volume 才乘价格，quote USD 原样使用；极端横截面差异只作 dispersion guard。
  3. **富 AlphaAsset 持久化 + lineage 接线**(P3-4):`AlphaAssetRegistry` 落 sqlite;育种处调用 `make_child_asset` 记 parent_ids/mutation_op。
- **验收标准**:注入坏数据在任何写入前被拦截并告警;pending 不兑现;缓存发布原子;刷新后的真实 mark/funding/OI 制品全部通过只读验收(当前仅 funding 未达成);`AlphaAsset` 磁盘往返一致;育种子代带非空谱系。
- **测试要求**:坏 fixture 拦截测试;持久化往返测试;谱系链查询测试。
- **风险**:中。开 strict 可能让某些抓取器开始拒绝历史脏数据——需先验证不破坏现有数据刷新,分抓取器灰度。

---

## Phase 3 — Validation Gate 强制化 + Paper/前向生产化 + firm 硬门 ✅ 已完成(2026-07-07)

- **落地**:①firm 硬门(P1-6)已接线(用户签 20000/10000/0.20)✅;②验证门作 live 前置(P1-1)已在 Phase 1
  接线✅;③组合优化器 sizing——**诚实裁定不硬接线**(单策略下优化器恒等 no-op,硬接会用未验证 book 替换已验证
  book 造回归),交付默认关闭 opt-in hook `sleeve_sizing.py`(证明恒等)+ 文档化风险预算等价物✅;④拥挤门校准——
  用户已签 WARN-only + short-leg own-history z\*=3.0；2026-07-13 将 234 周真实输入冻结成可重放 bundle，
  live/dry 21 个 BLOCK 周逐项一致，2.0–4.0 敏感性与实现/输入哈希进入统一门禁✅。通用默认值仍不冒充生产校准。
  设计:`docs/engineering/specs/2026-07-07-phase3-optimizer-and-crowding.md`。全 TDD、门禁全绿。
- **目标**:未经验证的策略结构性无法进入货币路径;组合级风险有硬门。
- **改造内容**:
  1. **firm 级敞口硬门**(P1-6):`aggregate_firm_books` 结果接入组合级硬门(firm 净/毛/单 symbol 跨账户集中上限),越限升人工/按比例减仓。**阈值由用户签署**(机制我建,数字人工定)。
  2. **组合优化器接实盘 sizing**(P1-6 相关):把 `min_variance/risk_parity` + 毛杠杆/换手/相关性约束接入 live sizing(mandate 之前的账户级组合层),或明确文档化 per-strategy 隔离的风险预算等价物。
  3. **拥挤门阈值校准**(P2-7):签署 z-gate 已完成历史敏感性 + 冻结输入重放 + 门禁；通用默认值等待生产微观结构样本。
- **验收标准**:两所同向持仓触发 firm 硬门产生阻断/减仓;live 下单前目标权重经组合优化器且受约束;阈值有校准记录。
- **测试要求**:firm 硬门触发测试;live sizing 经优化器测试;拥挤门契约测试锁定校准值。
- **风险**:中。把优化器接进 sizing 会改变实盘仓位口径——需在干跑充分验证,且保留"裸信号 + mandate"作为可回退路径。

---

## Phase 4 — OMS/EMS 订单完整性 + Pre-Trade 引擎全承重 + Kill Switch 增强 ◑ 部分完成(2026-07-07)

- **落地**:④紧急减仓对抗式属性/网格测试(P2-3)✅ 穷尽证明"任意输入只减敞口"(65 用例)，且
  峰值权益状态已用真实权益历史作上界：孤立坏峰值只硬停/告警，不触发单轮全平✅；①ADV/波动承重
  **机制** ✅(`live/execution/market_stats.py` fail-closed + `make_ctx_factory` 接口零回归)——**激活待用户签
  `max_participation_rate`/`max_instrument_vol` + 注册表穿线**(NEEDS_USER_SIGNED_NUMBER)。**诚实延后**:②OMS/EMS
  大部分已做(撤单/崩溃恢复/确定性 ID/preflight 满足"不丢单不重单")或需 cadence/post-L2(改单/主备/长连接);
  ③同点跨源核价(P2-6)已于 2026-07-13 CLEAN_TDD 闭合当前价路径：venue bid/ask midpoint +
  source timestamp 对 CoinGecko price + `last_updated_at`，缺失/陈旧/时间偏差/价格偏差均冻结且不动已有仓位；
  `decided_ts` 年龄跳过已删除。staleness 从 7 天收紧仍绑定 cadence 决策，未擅自改成 1H。
  设计+四项裁定:`docs/engineering/specs/2026-07-07-phase4-oms-and-pretrade.md`。全 TDD、门禁全绿。
- **目标**:live 执行栈达到可受控小资金实盘的订单完整性与风控承重。
- **改造内容**:
  1. **live 风控引擎全承重**(P1-5,代码闭合/校准待外部证据):真实逐标的 ADV/已实现波动、重复/频率/撤单率均已接线；用授权生产微观结构样本校准 `max_participation_rate`/`max_instrument_vol`/撤单率阈值。
  2. **OMS/EMS 强化**:主备连接/断线重连/交易日切换;撤单/改单完整;Bybit 真实联调收口。
  3. **实盘新鲜度门收紧 + 同点跨源核价**(P2-6):同点核价已完成；5%/5 分钟明确为未校准工程基线，整源缺失冻结全部目标。剩余按用户确认的调仓频率收紧 7 天 staleness，并用留存观测校准阈值。
  4. **紧急减仓对抗式属性测试 + 峰值证据上界**(P2-3):证明任意输入下只减敞口，且坏峰值状态不能触发单轮全平。
- **验收标准**:超 ADV×参与率订单被拒;断线重连不丢单不重单;紧急通道属性测试全绿。
- **测试要求**:live-ctx 引擎触发测试;断线/重连/恢复集成测试;紧急减仓网格属性测试。
- **风险**:中高。触碰订单执行路径——每步过全量门禁 + testnet 真实联调,不在 mainnet 验证。

---

## Phase 5 — Portfolio 生命周期 + Post-Trade 对账 + 退休调度 ◑ 部分完成(2026-07-07,机制承重 + 只读报告已上;减仓数字/异常阈值留签)

> 落地结果 + 诚实裁定:`docs/engineering/specs/2026-07-07-phase5-lifecycle-and-monitoring.md`。
> 三项机制半区大多已建但缺调度/接线;本轮把死代码 `run_maintenance` 接成周度承重驱动器 + live"停止加仓"
> 联动(item 1),补上真缺的权益异常检测 + margin 门(item 2,默认惰性),把已有 GoNoGo 报告补测试 + PnL/
> 成本归因 + 上架 console + 日调度(item 3,全只读)。全量门禁绿(validate.py 三检 PASS,live 栈 674;前端
> typecheck/build/61 tests 绿)。**留用户签**:降级→自动**减仓目标数字** + RETIRED→全平人工确认、异常跳变%/
> margin 地板阈值、近实时(<小时)cadence、维护 sweep 的 backtest 参照数据。自主动作严格 fail-safe 方向
> (只减不加 / 只加告警 / 只读报告),涉及风险数字一律建机制 + fail-closed 默认、不代签。

- **目标**:多 alpha 失效自动 de-risk;盘后对账/归因自动化。
- **改造内容**:
  1. **维护/退休自动调度**(P1-7):调度器周期调用 `run_maintenance` + 自动从 forward paper-track/live 账本算 live-vs-backtest 指标喂入;退休/降权联动 live 仓位收缩。
  2. **实时盘中监控**(dim10):把周/小时级对账升级为近实时持仓/权益/保证金/回撤监控 + 异常检测。
  3. **自动日终报告 + PnL/成本归因**(dim10):盘后自动 GoNoGo 风格报告。
- **验收标准**:注入衰退 live 指标→自动 sweep→状态降级→live 仓位下调,无需人工带证据;实时监控抓到注入异常。
- **测试要求**:端到端衰退→de-risk 测试;实时监控异常注入测试。
- **风险**:中。自动 de-risk 联动 live 仓位是自主动作——限定为"只减不加",保留人工确认全平选项(P2-3)。

---

## Phase 6 — 事件总线 + 可观测性 + 容器化 + 架构解耦 ◑ 部分完成(2026-07-07,纯加法半区已上;大重构/不可验证半区留)

> 落地结果 + 诚实裁定:`docs/engineering/specs/2026-07-07-phase6-eventbus-decoupling-container-scaffold.md`
> (容器化细节:`2026-07-07-containerization-design.md`;脚手架:`docs/PLANNED_SCAFFOLDS.md`)。**核心张力:本 Phase
> 核心价值项(架构解耦、真实容器化)本质是大重构 / 需真实部署环境,直接撞"不做大重构"红线 + 本机 Docker 不通。**
> 每项拆成纯加法半区(已 TDD 做完、过全量门禁)+ 大重构/不可验证半区(设计文档 + 回归网锁记录、留用户拍板):
> ① 独立落盘事件总线 `services/monitoring/event_bus.py` 已加固为可验追加链，并接入 dry-run
> pretrade/completion 与 alpha maintenance transition 两个真实 producer；七个干跑 driver + 维护 sweep 共用持久库。
> 2026-07-14 又补齐单机 at-least-once consumer journal（租约/offset/retry/DLQ）、幂等哈希 projection
> 与 console 真实读取；严格版本化 schema/显式 consumer compatibility 已承重。最新版本 event timestamp
> 已统一为 microseconds，受影响历史 v1.0 以登记单位兼容；12 types/17 definitions 的确定性 Catalog 已进入
> unified gate 与供应链清单。单机 heartbeat、固定 partition topology、确定性 rebalance、generation fencing
> 与跨重启 partition offset 也已接入正式单分区 projection。deployment evaluation authority 只保留
> `DENIED/ELIGIBLE_NOT_APPLIED` 并与 event 同事务；drain + console 只读投影已接。dry-run completed +
> research-risk、alpha promotion、alpha maintenance、kill-switch、deployment evaluation、data refresh、data-quality admission 分别与其
> `DryRunRun`、catalog snapshot、maintenance snapshot、权威 latch、evaluation ledger、refresh history
> 通过七条同库 transactional outbox 绑定；data admission 进一步成为 weekly forward-track 的 fail-closed 必要门；
> maintenance/kill-switch JSON 已降为可恢复镜像。
> ② 域 import 边界已进一步承重：research session 核心改为显式 runtime 注入，正式组合入口上移到
> intelligence；完整 service AST 图、循环/动态加载精确策略和快照证据已进入 validate/供应链/顶级统一门。
> `CostModel` 上移 contracts 后 portfolio→research 删除；paper contract/对账/impact 正本归位后
> execution→research 也删除；console 风险端口注入删除 research→risk；promotion API 组合根迁出 governance
> 后删除 governance→alpha/research；中立 validation 正本与 alpha factor-set 归位删除最后的 alpha→research。
> 当前已进一步推进到 16 域/36 边且无任何 SCC；③ 镜像 L2 安全不变量测试 + 标注
> UNBUILT 的 Dockerfile + .dockerignore;④ 空脚手架守卫测试 + PLANNED 诚实清单。全量门禁绿(validate.py 三检 PASS)。
> **仍留大重构 / post-L2**:kill-switch 已改为 append-only SQLite 权威且 ARM/RESET 与 outbox 同事务，
> JSON 仅为镜像；deployment evaluation 已权威化但没有实际 APPLIED/DEPLOYED 执行状态；
> data refresh 与 dry-run research-risk producer、partition lag/age、DLQ/容量阈值及 alert sink 已接；
> 仍需真实 live pre-trade/更广 data producer、外部 seal + 安全归档、独立告警 SLA 与跨主机 broker/复制/HA；
> production service 图已无环；research→data 已通过 PIT/version port、data-owned adapter 与 console
> data port 清零。回测验证现通过必填 port/output contract，walk-forward/CPCV 正本归入中立 validation；
> mutating runner 与 read-only HTTP routers 分别上移 `services/research_runner` 和 `services/console/api`，
> research 对 FastAPI/uvicorn/tools/data 零 import。当前 16 域/36 边/0 SCC；console API 的 15 个 <800 行
> routers、44 个 GET-only OpenAPI paths 与 772 行无 route implementation root 均保持。当前 import 图无环
> 与 adapter 边界闭合不等于 research 已完成所有长期模块化、Phase 14 进程隔离
> 或部署隔离已经完成。
> 真实容器 build + staging/prod 隔离 + 配置管理 + 监控告警/灾备同样开放。

- **目标**:全链路可追溯/可回放;可安全迭代的运维基建。
- **改造内容**:
  1. **统一事件总线**(P2-5):严格 schema、版本化 timestamp unit、12 types/17 definitions Catalog、单机 consumer offset/lease/retry/DLQ、heartbeat/fixed partitions/deterministic rebalance/generation fencing、幂等 projection，以及 dry-run/promotion/maintenance/kill-switch/deployment/data-refresh/data-admission 七条 transactional outbox 已承重；research-risk 精确绑定 dry-run，data admission 精确绑定完整证据并成为 forward-track 必要门，partition lag/age、DLQ 与 append-only 容量告警已进入 console + schedulable alert。worker ID 已以固定 `PROCESS_DECLARED_UNAUTHENTICATED` assurance 进入 coordination/delivery 哈希链并向 health/带外 heartbeat 诚实暴露。独立 recovery drain 能在原 producer 不重跑时验证并搬运七源 pending event、逐源脱敏失败、最后只投影一次。kill-switch reset 失败保闩，deployment 只记录 eligibility、不执行部署。剩余真实 live pre-trade/更广 data/risk producer、实际 APPLIED/DEPLOYED 状态、外部 seal + 安全归档、独立告警 SLA、外部认证 worker identity 与跨主机 HA。
  2. **架构解耦**(P2-4):完整 import 图与零新增循环门已承重；research→data 已清零，四类 persistence ports 隔离四个 SQLite adapters；backtest port/output contract、validation 正本及 HTTP/tool adapter 上移已闭合。当前全图 16 域/36 边、0 SCC/0 循环边，policy 为空；research 对 FastAPI/uvicorn/tools/data 零 import。任何新环、adapter 回流或 concrete-store 回流失败关闭。
  3. **容器化 + 环境隔离**(dim15):Docker + staging/prod 隔离 + 配置管理 + 监控告警基建。
  4. **清理误导结构**(P3-3):删空 DDD 脚手架或标 PLANNED。
- **验收标准**:关键事件经总线可回放;import-cycle 检查通过;Docker 起停 + 环境隔离测试。
- **测试要求**:事件回放测试;确定性 import 图/策略/篡改测试进入 validate.py;容器冒烟测试。
- **风险**:中。大范围结构调整——分模块渐进,每步过全量门禁。

> **下一执行顺序（2026-07-14）**:① 需人工/外部配置：从受控 secret source 注入 12+ 字符 backup passphrase，配置 external backup/seal destinations 与 operator-owned HTTPS heartbeat，保留 restore、object-lock/WORM、missed-heartbeat alert receipt；本地接口已实现但三项均 UNAVAILABLE，不得伪造证据。② 本地可继续：更多 data/risk producer、archive lifecycle/receipt verifier 与 worker identity；scheduler watchdog、三链 commitment/extension verifier、create-once anchor 和 heartbeat sender 已承重；
> kill-switch、deployment evaluation outbox、event timestamp unit、schema Catalog 与单机 consumer-group 已闭合；② 需外部证据：受治理的真实模型
> provider + 独立 reviewer + 正 OOS challenger；③ 需外部部署：OIDC/mTLS/托管密钥轮换与外部签名 admission；
> ④ 明确 post-L2：持牌 tick/order-book 数据 SLA 与 multi-venue SOR。后两类不得用本地 mock 伪造闭环。

---

## Phase 7 — Phase 14 信任边界 + Phase 3A 小资金实盘前验收门 ⛔ 人工门(agent 不可自行完成;唯一纯加法辅助=Go/No-Go 清单,2026-07-07)

> 诚实裁定:`docs/engineering/specs/2026-07-07-phase7-trust-boundary-and-go-live-gate.md`。**本 Phase 全部实质都在
> agent 安全边界之外**——真人签署、真实密钥、真实部署隔离(自主授权只覆盖 fail-closed 加固,不覆盖绕过代码强制的
> 人工门)。四项:① 出进程签名+RBAC(P0-2)= 拒绝式接缝 signing.py + 边界测试已在,真实现=post-L2/部署基建;
> ② mandate 密码学完整性(P0-1)= 单进程内签名校验是 theater(受信代码持同一把 key),真防御只能出进程;③ 进程
> 隔离(Phase 14)= 部署基建;④ Go/No-Go = 100% 人工。**唯一 agent 可做的纯加法诚实交付** =
> `docs/PHASE_3A_GO_NO_GO_CHECKLIST.md`(逐条列门槛 + 现状 + 显式"人工签此"字段,不下结论不设数字,与
> tools/gonogo_report 证据包互补)。**这标志系统性路线图里 agent 能做的部分收口(Phases 0–6 已落地过门禁);
> 真钱放行按设计交还给人。**

- **目标**:补齐真钱前的最后结构性前置;人工 Go/No-Go 放行。
- **改造内容**:
  1. **出进程签名 + 真身份认证/RBAC**(P0-2):promotion_api 的 OIDC+mTLS+MFA+RBAC 与浏览器 PKCE S256 M2 边界已落地；继续部署外部 IdP/TLS、密钥轮换及身份生命周期，并实现真 `SigningAuthority`(独立进程签发/验证)、把 `verify_signed_ref` 接进 live 执行路径与 kill-switch reset。
  2. **mandate 密码学完整性**(P0-1):mandate 加载校验 detached 签名;agent 环境结构性无 `key/` 写权限;文件系统权限收紧。
  3. **进程/部署隔离**(Phase 14):mutating 决策与研究/agent 进程隔离。
  4. **Phase 3A Go/No-Go 人工评审**:mandate 逐字段人工签署 + ≥8 周前向证据 + 自动提交路径端到端演练 + 全部 P0/P1 收口签字。
- **验收标准**:进程内伪造 `is_out_of_process=True` 权威仍验不过;篡改/无签名 mandate 被拒;Go/No-Go 检查清单全签字。
- **测试要求**:签名边界测试;mandate 完整性测试;端到端自动提交演练。
- **风险**:高(这是通往真钱的门)。**必须全部人工评审通过 + 逐字段签署,任何一项不过则不放行**;上线初期先禁用无人值守 `--submit`,人工每周执行,稳定后再启用自动。

---

## 阶段依赖与并行性

```
Phase 0 ✅ ─▶ Phase 1(收口,可立即做,全 fail-closed)
                  │
                  ├─▶ Phase 2(数据/持久化)──┐
                  ├─▶ Phase 3(验证门/组合/firm)──┤
                  ├─▶ Phase 4(OMS/EMS/引擎)──┼─▶ Phase 5(生命周期/对账)
                  └─▶ Phase 6(事件总线/基建,可并行)──┘
                                                        │
                                                        ▼
                                          Phase 7(Phase 14 信任边界 + Phase 3A 放行)
```

- **Phase 1** 现在就能全部做(fail-closed 加法 + 真 bug 修复),不依赖任何人工决定。
- **Phase 2-6** 是工程主体,大多可并行,不解锁真钱。
- **Phase 7** 是真钱前的最后结构性前置,含大量人工签署环节,**不可由代码/AI 自行完成**。

## 每完成一 Phase 的成熟度提升预估

| 完成 | 均分 | 说明 |
|---|:-:|---|
| Phase 0 | 3.67 | 事实基线 |
| Phase 1 | ~3.9 | P1 缺口收口,dim8/dim9/dim2 提升 |
| Phase 2-3 | ~4.1 | 数据/验证/组合承重,dim2/dim5/dim6 到 4.5 |
| Phase 4-5 | ~4.3 | 执行/对账生产化,dim9/dim10 到 4 |
| Phase 6 | ~4.5 | 事件总线/基建,dim1/dim12/dim15 到 4 |
| Phase 7 | ~4.7 | 信任边界闭环,具备真钱前置(仍待人工放行) |
