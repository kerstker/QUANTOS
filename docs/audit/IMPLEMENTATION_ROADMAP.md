# QUANTOS 实施路线图(IMPLEMENTATION_ROADMAP)

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
  2. **client_order_id 同数量碰撞修复**(P1-3):`client_order_id` 加 per-batch 单调序号;同批重试仍幂等,不同增量不再撞 ID。
  3. **接入乱序死检查修复**(P2-1):`file_ingest` 在排序前用原始顺序跑乱序检查;传真实 `expected_days`。
  4. **验证门作 live 部署结构性前置**(P1-1):`live_track_advance` preflight 强制每个 strategy_id 有在案 PASS ValidationResult 或可审计人工 override,否则拒绝提交。
  5. **place-后-未落库孤儿窗口**(P1-4):place_order 前先写 pending 意图账本行,崩溃恢复看得见。
  6. **实盘 ctx 回填历史指纹/时间戳 + 撤单率引擎**(P1-5):从 LiveLedger 回填 `recent_order_fingerprints`/`recent_timestamps`;新增 cancel-rate 检查。
  7. **aiokafka 网络边界洞**(P3-1):把消息/网络客户端加进 `_NETWORK_ROOTS`(显式排除被隔离文件)。
  8. **峰值权益合理性上界**(P2-3):`live_risk_state.json` 峰值持久化加上界校验,防虚高触发非必要全平。
- **验收标准**:每项都有对应的失败→通过测试;全量门禁保持全绿;新增拒绝路径可审计。
- **测试要求**:TDD——先写复现缺口的失败测试,再修复;不删除/不弱化任何现有测试。
- **风险**:低。全部是 fail-closed 加法或真 bug 修复;主要风险是接线时误改现有行为,由 1564 回归门禁 + 逐项独立提交控制。

---

## Phase 2 — Data Quality Gate + Feature/Alpha 持久化生产化 ✅ 已完成(2026-07-07)

- **落地**:P1-8 前向决策门(`services/data/panel_quality.py`,坏/停更数据暂停证据钟留 pending)✅;
  P2-2 volume 单位守卫接进容量引擎(`capacity_curve` fail-closed 拒绝混单位出容量数)✅;
  P3-4 AlphaAsset sqlite 持久化(`services/alpha/asset_store.py`,fail-closed + 篡改检测 + containment
  守卫经 restore 复用)+ cs_genome 育种谱系接线(`make_child_asset` 记 parent_ids/mutation_op)✅。
  全 TDD、门禁全绿。诚实边界:幅度价格突刺不硬挡(会误杀 LUNA 真实尾部)、gp/evolutionary 谱系未接
  (无 asset 挂载点)、生产驱动器 save/load 注入留一行 open question。
- **目标**:数据异常自动阻断承重决策;研究资产跨会话可查询。
- **改造内容**:
  1. **数据质量强制门**(P1-8):喂承重决策(前向证据、可部署 book)的单标的抓取器统一开 `strict=True`;前向兑现前加一道质量门,不过 fail-closed 留 pending。
  2. **`check_volume_units` 接线**(P2-2):接进多标的接入后的批量校验;修正 `crypto_daily.db` volume 单位或消费端强制门。
  3. **富 AlphaAsset 持久化 + lineage 接线**(P3-4):`AlphaAssetRegistry` 落 sqlite;育种处调用 `make_child_asset` 记 parent_ids/mutation_op。
- **验收标准**:注入坏数据被接入层拦截并告警;`AlphaAsset` 磁盘往返一致;育种子代带非空谱系。
- **测试要求**:坏 fixture 拦截测试;持久化往返测试;谱系链查询测试。
- **风险**:中。开 strict 可能让某些抓取器开始拒绝历史脏数据——需先验证不破坏现有数据刷新,分抓取器灰度。

---

## Phase 3 — Validation Gate 强制化 + Paper/前向生产化 + firm 硬门 ✅ 已完成(2026-07-07)

- **落地**:①firm 硬门(P1-6)已接线(用户签 20000/10000/0.20)✅;②验证门作 live 前置(P1-1)已在 Phase 1
  接线✅;③组合优化器 sizing——**诚实裁定不硬接线**(单策略下优化器恒等 no-op,硬接会用未验证 book 替换已验证
  book 造回归),交付默认关闭 opt-in hook `sleeve_sizing.py`(证明恒等)+ 文档化风险预算等价物✅;④拥挤门校准——
  **实测 233 周发现平均分结构性钉死 ~0.50(错误统计量,默认阈值失效)**,暴露 `CALIBRATED_DEFAULTS`(值字节不变)
  + 敏感性发现文档化 + 契约锁,行为变更(WARN-only/短腿口径/签数字)待用户拍板✅。
  设计:`docs/superpowers/specs/2026-07-07-phase3-optimizer-and-crowding.md`。全 TDD、门禁全绿。
- **目标**:未经验证的策略结构性无法进入货币路径;组合级风险有硬门。
- **改造内容**:
  1. **firm 级敞口硬门**(P1-6):`aggregate_firm_books` 结果接入组合级硬门(firm 净/毛/单 symbol 跨账户集中上限),越限升人工/按比例减仓。**阈值由用户签署**(机制我建,数字人工定)。
  2. **组合优化器接实盘 sizing**(P1-6 相关):把 `min_variance/risk_parity` + 毛杠杆/换手/相关性约束接入 live sizing(mandate 之前的账户级组合层),或明确文档化 per-strategy 隔离的风险预算等价物。
  3. **拥挤门阈值校准**(P2-7):用历史拥挤数据校准或敏感性扫描并文档化。
- **验收标准**:两所同向持仓触发 firm 硬门产生阻断/减仓;live 下单前目标权重经组合优化器且受约束;阈值有校准记录。
- **测试要求**:firm 硬门触发测试;live sizing 经优化器测试;拥挤门契约测试锁定校准值。
- **风险**:中。把优化器接进 sizing 会改变实盘仓位口径——需在干跑充分验证,且保留"裸信号 + mandate"作为可回退路径。

---

## Phase 4 — OMS/EMS 订单完整性 + Pre-Trade 引擎全承重 + Kill Switch 增强 ◑ 部分完成(2026-07-07)

- **落地**:④紧急减仓对抗式属性/网格测试(P2-3)✅ 穷尽证明"任意输入只减敞口"(65 用例);①ADV/波动承重
  **机制** ✅(`live/execution/market_stats.py` fail-closed + `make_ctx_factory` 接口零回归)——**激活待用户签
  `max_participation_rate`/`max_instrument_vol` + 注册表穿线**(NEEDS_USER_SIGNED_NUMBER)。**诚实延后**:②OMS/EMS
  大部分已做(撤单/崩溃恢复/确定性 ID/preflight 满足"不丢单不重单")或需 cadence/post-L2(改单/主备/长连接);
  ③同点跨源核价(P2-6)= CLEAN_TDD 但触碰核价路径,留专门 follow-up;staleness 收紧绑 1H cadence(已推迟)。
  设计+四项裁定:`docs/superpowers/specs/2026-07-07-phase4-oms-and-pretrade.md`。全 TDD、门禁全绿。
- **目标**:live 执行栈达到可受控小资金实盘的订单完整性与风控承重。
- **改造内容**:
  1. **live 风控引擎全承重**(P1-5 深化):接入真实逐标的 ADV/已实现波动喂 MarketContext,`max_participation_rate`/`max_instrument_vol` 设校准值。
  2. **OMS/EMS 强化**:主备连接/断线重连/交易日切换;撤单/改单完整;Bybit 真实联调收口。
  3. **实盘新鲜度门收紧 + 同点跨源核价**(P2-6):按调仓频率收紧 staleness;用 venue book_ticker 同点核价替代滞后决策价。
  4. **紧急减仓对抗式属性测试**(P2-3):证明任意输入下只减敞口。
- **验收标准**:超 ADV×参与率订单被拒;断线重连不丢单不重单;紧急通道属性测试全绿。
- **测试要求**:live-ctx 引擎触发测试;断线/重连/恢复集成测试;紧急减仓网格属性测试。
- **风险**:中高。触碰订单执行路径——每步过全量门禁 + testnet 真实联调,不在 mainnet 验证。

---

## Phase 5 — Portfolio 生命周期 + Post-Trade 对账 + 退休调度 ◑ 部分完成(2026-07-07,机制承重 + 只读报告已上;减仓数字/异常阈值留签)

> 落地结果 + 诚实裁定:`docs/superpowers/specs/2026-07-07-phase5-lifecycle-and-monitoring.md`。
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

> 落地结果 + 诚实裁定:`docs/superpowers/specs/2026-07-07-phase6-eventbus-decoupling-container-scaffold.md`
> (容器化细节:`2026-07-07-containerization-design.md`;脚手架:`docs/PLANNED_SCAFFOLDS.md`)。**核心张力:本 Phase
> 核心价值项(架构解耦、真实容器化)本质是大重构 / 需真实部署环境,直接撞"不做大重构"红线 + 本机 Docker 不通。**
> 每项拆成纯加法半区(已 TDD 做完、过全量门禁)+ 大重构/不可验证半区(设计文档 + 回归网锁记录、留用户拍板):
> ① 独立落盘事件总线 `services/monitoring/event_bus.py`(publish/replay/subscribe,correlation/causation 存列不改
> Event 契约);② 域 import 边界测试(AST 钉今天已成立的 leaf 不变量,回归网);③ 镜像 L2 安全不变量测试 + 标注
> UNBUILT 的 Dockerfile + .dockerignore;④ 空脚手架守卫测试 + PLANNED 诚实清单。全量门禁绿(validate.py 三检 PASS)。
> **留用户拍板(大重构 / post-L2)**:事件 producer 穿线让因果图长出来、research 拆子包 + console_api 拆 router 让
> "域间无环"能绿(有环今天不成立,回归网已就位)、真实容器 build + staging/prod 隔离 + 配置管理 + 监控告警/灾备。

- **目标**:全链路可追溯/可回放;可安全迭代的运维基建。
- **改造内容**:
  1. **统一事件总线**(P2-5):用 `quantos/contracts/events.py::Event` 实现持久事件总线,关键事件带 correlation/causation ID,支持回放。
  2. **架构解耦**(P2-4):import-linter 钉死域间无环;research 拆子包;console_api 按 router 拆分 <800 行。
  3. **容器化 + 环境隔离**(dim15):Docker + staging/prod 隔离 + 配置管理 + 监控告警基建。
  4. **清理误导结构**(P3-3):删空 DDD 脚手架或标 PLANNED。
- **验收标准**:关键事件经总线可回放;import-cycle 检查通过;Docker 起停 + 环境隔离测试。
- **测试要求**:事件回放测试;import-linter 进 validate.py;容器冒烟测试。
- **风险**:中。大范围结构调整——分模块渐进,每步过全量门禁。

---

## Phase 7 — Phase 14 信任边界 + Phase 3A 小资金实盘前验收门

- **目标**:补齐真钱前的最后结构性前置;人工 Go/No-Go 放行。
- **改造内容**:
  1. **出进程签名 + 真身份认证/RBAC**(P0-2):实现真 `SigningAuthority`(独立进程签发/验证);把 `verify_signed_ref` 接进 live 执行路径与 kill-switch reset;promotion_api 的 X-Operator 升为认证身份。
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
