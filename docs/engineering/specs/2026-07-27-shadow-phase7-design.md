# SHADOW Phase 7 设计与 S 系列切片计划(727 §12.2)

- 日期:2026-07-27
- 依据:[`docs/QUANTOS_727.md`](../../QUANTOS_727.md) §12.2(SHADOW 层:连接真实
  市场行情/盘口/交易规则但不发送订单;必须比较理论成交/Paper fill/实时可成交价格/
  盘口深度/Spread/Slippage/Latency/Expected impact)、§3.2(DeploymentState:
  PAPER → SHADOW → MICRO_LIVE)、§18.2(provenance 标签 SHADOW)、§13(MEXC
  BTC 现货接入顺序)。
- 状态:**S1–S3 全部落地,Phase 7(SHADOW 侧)完成**——S1:三家 venue 适配器的
  订单簿深度端点(`depth()`,fail-soft)+ SHADOW 比较纯核
  `live/execution/shadow.py`(727 §12.2 八项比较的机器落地);S2:
  `live/execution/shadow_store.py`(append-only 哈希链观测账本 + SHADOW_OBSERVED
  事件 schema + transactional outbox 同事务)+ 第七 dispatch + drain 第 8 源;
  S3(本提交):`tools/shadow_track.py` 运行时驱动器(真实 testnet 首跑 14 观测
  落账 + 14 事件经 drain 入共享事件日志)+ shadow_ready 全条件门 +
  `evidence/shadow_status.json`。727 Phase 7 验收对照见 §6;§12.3/12.4
  MICRO_LIVE/LIVE 是 Phase 9+,非本方案。
- readiness:L2_DRY_RUN_ONLY 不变。本方案不改五个安全旗标,不新增任何下单路径
  (深度端点是只读公共端点,不签名;SHADOW 结构性不发单——727 §12.2「但不发送
  订单」由构造保证,不是靠纪律),不触碰凭证;网络 import 只在三个 venue 适配器
  文件内(AST 治理钉死);`services/` 不 import `live/`。

## 1. 问题:SHADOW 是 Paper → Micro-Live 之间唯一全缺的层

727 §3.2 的部署阶梯是 OFFLINE → PAPER → **SHADOW** → MICRO_LIVE → LIVE。
今天的现实:

1. **状态位存在,但没有任何代码运行它**。三正交状态机(lifecycle C 系列)的
   deployment 机器有 SHADOW 的位置,事件清单有 SHADOW_STARTED,provenance 清单
   有 SHADOW 标签——但「shadow 跑什么、观测什么、和谁比较」在代码里**一个字
   都没有**。PAPER 有 paper-track 持久层和 forward_track 驱动器,MICRO_LIVE
   有 testnet 执行栈 + mandate + runbook,中间的 SHADOW 是空的。
2. **「paper fill = 决策价」是一个未量化的语义缺口**。当前 paper/干跑语义把
   成交价钉死在决策价上:不含点差、不含冲击、不看深度。W1 衰减引擎
   (`live/execution/degradation.py`)量化了 live vs replay 的漏损,但它需要
   **真实成交账本**——在还没有资格发单的 alpha 上,没有任何机器回答「纸面说
   的价,盘口此刻到底给不给」。SHADOW 观测正是量化这个缺口的机器(诚实注记:
   它**不修复** paper 语义,只把缺口变成一行可审计的数字;
   `ShadowObservation.paper_fill_price` 显式记录 = decision_price 的当前语义)。
3. **§12.2 的八项比较没有任何载体**。理论成交 / Paper fill / 实时可成交价格 /
   盘口深度 / Spread / Slippage / Latency / Expected impact——前两项在 paper
   语义里,后六项需要**真实盘口深度**,而三家适配器此前只有 top-of-book
   (`book_ticker`,一档,无深度)。
4. **读得到深度的能力没有 venue 出口**。`services/execution/impact.py` 的
   Almgren-Chriss 冲击模型存在但只在离线研究里消费,实时侧没有任何「拿真实盘口
   走一遍这条腿」的组件。

## 2. 设计裁定

1. **venue 选择裁定:Phase 7 = Binance futures(testnet/公共端点)优先;MEXC
   现货留 Phase 8 的 §13 单列任务**。依据:§13 本身把 MEXC 现货接入拆成了独立
   的 A–D 阶段序列(Public Market Shadow → … → Broker Adapter),它的 shadow
   化有自己的开发顺序与毕业路径(无 testnet → 小额真实 + 人工 mandate),不该
   被 Phase 7 的 Binance 主线吞掉。但 `depth()` 端点三家一起落(适配器层能力
   对称、成本极低、MEXC 侧 §13 阶段 A 的 Public Market Shadow 正好直接消费),
   驱动器(S3)只在 Binance testnet 上跑。
2. **depth 不进 VenueAdapter Protocol——沿用 premium_index/funding_rates 的
   hasattr 可选能力惯例**。Protocol 是「一个完整 venue 必须有什么」的最小集合
   (契约测试 `tests/live/test_venue_base.py` 钉死);只读诊断/行情能力按 venue
   各自提供,调用方 `hasattr(venue, "depth")` 探测、缺了干净跳过(既有先例:
   `tools/live_track_advance.py` 对 premium_index 的同款惯例)。把可选方法塞进
   Protocol 会把「缺它的 venue 不算完整实现」的错误语义机器化。
3. **深度解析/钳档抽成单一共享实现 `live/exchange/depth.py`,venue 专属常量留
   在各适配器**。三家响应形状各异(扁平 dict / 扁平 dict+E/T / V5 信封),但
   「[[price, qty], …] 档位解析」「limit 钳到合法档」「时间戳候选提取」一字不差
   ——三份平行拷贝只会漂移。本模块无网络 import(治理钉死),`clamp_depth_limit`
   **向上取档绝不少给**(少给会让 INSUFFICIENT_DEPTH 判定失真);MEXC 文档的
   5000 档与 Bybit spot 的 25 档保守不承接,注释钉死。
4. **fail-soft 读、fail-closed 比较,两层纪律分开**。适配器层(book_ticker
   同型):非 2xx/非 JSON/形状不符/任一侧空簿/任一非有限档 → None,绝不抛异常
   打断调用方。比较核层(degradation.py 同款 UNKNOWN 纪律):盘口 None →
   BOOK_UNAVAILABLE;过旧 → BOOK_STALE;来自未来 → BOOK_FROM_FUTURE;深度不足
   → INSUFFICIENT_DEPTH + unfilled 拆账,**绝不为未成交部分编造价格**
   (WalkBookResult.vwap 只在全额成交时给出);expected_impact_bps 只在
   impact_model + adv 双给时计算(UNCALIBRATED 输入绝不编造)。
5. **复用边界:滑点语义直接 import `live/execution/tca.py::slippage_bps`**
   (同树,买贵/卖贱为正 = 成本,非正价 None);staleness 政策镜像
   `tca.validate_arrival_snapshot` 与 `price_check.flag_noncontemporaneous_instruments`
   的 fail-closed 姿态但**不共用代码**(那两个是 arrival/book_ticker 一档口径,
   SHADOW 是全深度簿口径,硬套会扭曲两边);冲击模型**鸭子类型注入**
   (`temporary_bps` + `permanent_bps`),`shadow.py` 不 import `services/`——
   `AlmgrenChrissImpact` 满足该接口,live/execution 纯核保持零外依。
6. **walk_the_book 消费 venue 原序,不重排**。重排会掩盖 venue 报序异常;
   确定性核心只消费 venue 报的事实。瞬时交叉簿(best_ask < best_bid)是
   交易所快速行情下的真实存在,快照契约**不**在构造期拒绝它——可执行性判定
   (CROSSED_BOOK)归 walk_the_book,分层单一职责。
7. **确定性核心,墙钟只在驱动器边界**。`observe_leg` 的 now_ms/latency_ms/
   max_book_age_ms 全部调用方 int ms 注入;latency 是 IO 边界实测(驱动器在
   `depth()` 调用前后打点),核心永不自读墙钟;provenance 恒 "SHADOW"
   (§18.2),构造期 fail-closed 校验。

## 3. S 系列切片计划

| 切片 | 内容 | 状态 |
|---|---|---|
| **S1** | `live/contracts.py`:`BookLevel` + `OrderBookSnapshot`(fail-closed 校验,as_of_ms 缺失诚实 None,刻意不拒瞬时交叉簿);`live/exchange/depth.py` 共享解析/钳档;三适配器 `depth()`(MEXC `/api/v3/depth` 档 5–1000、Binance `/fapi/v1/depth` 档 5–1000 取 `E`→`T`、Bybit `/v5/market/orderbook` linear 档 1/50/200/1000 取 `result.ts`,全 fail-soft);`base.py` 可选能力惯例文档化;`live/execution/shadow.py` 纯核(ShadowLeg/WalkBookResult/walk_the_book/ShadowObservation/observe_leg/observe_batch) | **本提交**(适配器 6+6+6 测试 + shadow 27 测试) |
| **S2** | `live/execution/shadow_store.py`:append-only SQLite 哈希链观测账本(观测行与其 canonical 事件**同一事务**提交,克隆 refresh_history 结构);SHADOW_OBSERVED 事件 schema(v1.0,µs,脱敏摘要 payload)进 `quantos/contracts/event_schemas.py` + catalog 证据(12→13 types / 17→18 defs);第七 dispatch(`dispatch_shadow_event_outbox`,与六店同款依赖注入签名)+ drain 第 8 注册源 | **本提交**(17 store/dispatch/drain 测试 + 治理/schema 测试更新) |
| **S3** | `tools/shadow_track.py` 驱动器(复用 `_STRATEGIES` 信号注册表,绝不重算信号;venue 从 `_VENUES` 按 base_url 白名单结构性筛 testnet/demo,空凭证构造只调无签名方法;逐标的 `depth()` 实测 latency → observe_batch → shadow_store 落账;`--once`/`--follow` 真实时间推进)+ `build_shadow_readiness` 五 blocker 全条件门(NO_OBSERVATIONS/CHAIN_INVALID/OUTBOX_CHAIN_INVALID/LATEST_OBSERVATION_STALE/NO_EXECUTABLE_OBSERVATION)+ `evidence/shadow_status.json`(provenance SHADOW + honesty_note) | **本提交**(18 驱动器测试 + 真实 testnet 首跑) |

切分原则:每片独立可测、独立入库、不依赖后片——S1 的深度端点与比较核无
任何持久化,S2 的账本消费 S1 的纯值对象但不改其语义,S3 的驱动器是唯一带
网络/墙钟的层(IO 边界)。

## 4. S1 实施记录(2026-07-27)

### 4.1 契约与共享解析

- `live/contracts.py`:`BookLevel`(price/size 有限且 > 0,REST 快照里的零量档
  是垃圾数据,构造期拒绝)+ `OrderBookSnapshot`(instrument_id/bids/asks 元组/
  as_of_ms 可空/source 非空;**刻意不拒瞬时交叉簿**——判定归 walk_the_book;
  两侧允许空簿,EMPTY_BOOK 判定同样归 walk)。校验风格镜像既有
  `PremiumIndexSnapshot`/`ArrivalSnapshot`。
- `live/exchange/depth.py`:`clamp_depth_limit`(向上取最近合法档,绝不少给)+
  `parse_depth_levels`(任一档不可解析 → None 整簿 fail-soft)+
  `extract_as_of_ms`(候选序取第一个合法正整数,全无 → None,绝不自造)。
  无网络 import,治理边界内。

### 4.2 三适配器 `depth()`

- **MEXC**:`GET /api/v3/depth`,档 (5,10,20,50,100,500,1000)(文档另有 5000,
  保守不承接);**该响应不带时间戳 → as_of_ms 恒 None**(诚实,调用方据此无法
  做 staleness 判定,如实缺失);source="mexc"。
- **Binance**:`GET /fapi/v1/depth`,档 (5,10,20,50,100,500,1000);as_of_ms 取
  `E`(event time)回退 `T`(transaction time),全无 None;source="binance"。
- **Bybit**:`GET /v5/market/orderbook?category=linear`,档 (1,50,200,1000)
  (linear 合法集;spot 的 25 档不承接,本适配器只做 linear);as_of_ms 取信封
  `result.ts`(快照时刻,非外层 `time`);source="bybit"。
- 三家全部:不签名公共端点;fail-soft None(非 2xx/retCode!=0/非 JSON/形状不符/
  任一侧空簿/任一非有限档);绝不在下单路径上。`base.py` 注释钉死可选能力
  惯例(depth 与 premium_index/funding_rates 同列)。

### 4.3 `live/execution/shadow.py` 纯核

- `walk_the_book`:BUY 吃 asks、SELL 吃 bids,venue 原序不重排;EMPTY_BOOK /
  CROSSED_BOOK / INSUFFICIENT_DEPTH 三 reason,全额成交才给 vwap。
- `observe_leg`:book None → BOOK_UNAVAILABLE(全部盘口派生字段 None);
  as_of 在未来 → BOOK_FROM_FUTURE、age > max_book_age_ms(默认 60s,
  UNCALIBRATED 工程默认,调用方可覆盖)→ BOOK_STALE(可执行字段关死,旧簿
  mid/spread 仍如实报告);spread_bps = (ask−bid)/mid;slippage 复用
  `tca.slippage_bps`(executable_vwap vs decision_price,带方向);impact 鸭子
  类型双给才算;paper_fill_price = decision_price(当前 paper 语义,如实);
  provenance 恒 "SHADOW" 构造期校验。
- `observe_batch`:输出序 = 输入序;books 缺标的 → 该腿 BOOK_UNAVAILABLE
  (per-leg fail-closed 不拖垮整批);latencies_ms 缺标的 → ValueError(latency
  是承重字段,宁可整批不开跑);adv 按标的键取,缺失 → 该腿 impact None。
- 测试 `tests/live/test_shadow.py`(27)+ 三适配器各 6 个 depth 测试(假
  transport,零真网络)。

### 4.4 S2 实施记录(2026-07-27,本提交)

- **包位置裁定(落地无偏差)**:S2 计划曾预留「dispatch 若 import owner store 模块
  则 shadow dispatch 需放别处」的偏差备案——细读六个现有 dispatch 后确认它们
  **本就全部依赖注入**(store 实例作参数,`services/monitoring/
  domain_event_runtime.py` 不 import 任何 owner store 模块),故
  `dispatch_shadow_event_outbox` 与六店签名逐位同构,直接放进同一文件,
  无偏差可登记。store 本体放 `live/execution/shadow_store.py`(消费
  ShadowObservation;services/ 永不 import live/,live 侧 store 先例
  position_ledger.py)。
- **store 结构(克隆 `services/data/refresh_history.py`,全新库无 legacy
  迁移分支)**:表 `shadow_observations`(obs_seq 自增主键 / observation_id
  内容派生 `"shd_"+payload_sha256[:16]` UNIQUE / run_label / payload_json
  全字段 canonical / payload_sha256 / provenance CHECK='SHADOW' / as_of_ms
  可空 / recorded_at_ms / prev_hash / record_hash)+ `shadow_event_outbox`
  (obs_seq UNIQUE FK / event_id UNIQUE / event_json / correlation_id=
  run_label / causation_id="" / event_repro_hash / 双哈希链)+
  `shadow_event_dispatch`(确认链);三表全部 no-update/no-delete
  触发器。**观测行与其 canonical 事件同一 BEGIN IMMEDIATE 事务提交**;
  create=False 缺库即拒(ro&immutable 预检,不产生 -wal 副作用)+ 打开即
  全链校验;append 前先全链校验(篡改存量即拒续写)。
- **幂等与身份**:`observation_id` 由 payload 内容派生——同 id 同 payload →
  (id, False) 不产生新行/新事件;同 id 不同 payload → ShadowStoreError
  (按构造不可达,纵深防御分支,测试经 stub 钉死)。`run_label` 是批次元数据
  **不进身份**(同一观测跨批次重放仍是同一观测,与 PaperRealtimeStore 剔除
  generated_at 同款裁定)。
- **事件 schema(SHADOW_OBSERVED = `execution.shadow.observed`,v1.0.0)**:
  domain=execution,source=`live.execution.shadow_store`,criticality C2,
  timestamp **µs**(新类型现行约定,catalog 机器钉死 latest 一律 µs;
  `event_timestamp_from_milliseconds(recorded_at_ms)`)。payload 是**脱敏
  摘要**:observation_id/run_label/instrument_id/side/quantity/decision_price/
  executable_vwap/slippage_bps_vs_decision/book_age_ms/as_of_ms/provenance
  ——**不含盘口簿、不含 walk 明细**(完整观测在 store 行 payload_json;
  与 refresh 事件不含源错误详情同款纪律)。strict payload:`observation_id`
  钉 `^shd_[0-9a-f]{16}$`,provenance 钉 Literal["SHADOW"]。
- **drain 接线与「第七」计数诚实注**:`tools/domain_event_outbox_drain.py`
  新增第 8 个注册源 `shadow`(`data/cache/shadow_observations.db`,
  create=False,失败脱敏不堵其他源)——S2 条目的「第七 outbox」是计划语言;
  落地时 data_quality_admission 已先于本切片成为 drain 第 7 源,故 shadow 实为
  **第 8 个注册源**(事件 outbox 机制本身排第 7 套)。治理测试
  `test_unified_outbox_recovery_has_eight_sources_...` 同步更新,并对 drain 的
  `"live."` 禁令做**精确开洞**(唯一允许引用 = 纯 sqlite 持久化的
  `live.execution.shadow_store` import;其余 live. 引用仍全面禁止,开洞由
  测试钉死而非删除规则)。
- **catalog 变化**:13 types / 18 definitions;
  `IMPLEMENTATION_PATHS` + `live/execution/shadow_store.py`(它是 canonical
  事件生产者);`evidence/event_schema_catalog.json` 已重生成,
  `tests/governance/test_event_schema_catalog.py` 计数与实现集合同步。
- **测试**:`tests/live/test_shadow_store.py`(17:打开语义/幂等/冲突/链/
  两类篡改拒开拒续/provenance CHECK/过滤查询/同事务事件形状(µs+脱敏)/
  schema 拒绝畸形 payload/mark 语义/重开持久化/publish-before-ack 恢复/
  真 sqlite drain→bus→projection e2e)+ 既有治理/drain/schema 测试更新
  (全绿)。

### 4.5 S3 实施记录(2026-07-27,本提交)

- **驱动器 `tools/shadow_track.py`**(IO 边界,墙钟/网络只在这里):
  `build_shadow_legs`(权重×名义/决策价 → ShadowLeg,零权重跳过、缺价
  fail-closed ValueError、按标的排序确定性)+ `run_shadow_pass`(venue 注入;
  `depth()` hasattr 探测——无能力/异常/None 一律 BOOK_UNAVAILABLE 不炸整轮;
  latency = depth 往返实测,latency_clock 可注入,默认 time.perf_counter;
  observe_batch → 逐条 `store.append`,run_label=`{strategy_id}:{now_ms}`)+
  `build_shadow_readiness`(纯核只读 store,五 blocker,镜像 paper_health
  「全满足才 true」)+ `build_shadow_status`(证据快照,honesty_note 写明
  「观测是只读盘口比较,非成交、非 paper fill 证明」)。CLI:`--strategy`
  (crowd_contra 默认)/`--venue`(binance_testnet 默认)/`--notional`
  (10000)/`--once`/`--follow --interval-seconds`(300)/`--db`/`--evidence`/
  `--max-obs-age-ms`。
- **信号复用裁定**:`_STRATEGIES[strategy]["signal"]()` 五元组 → weights/prices,
  与 live preview 同一信号核,绝不重算;面板来自本地 PIT 库(离线),唯一
  网络面 = venue 适配器的无签名公共 GET。
- **venue 结构守卫**:`shadow_venues()` 从 `_VENUES` 按 **base_url 白名单**
  (Binance TESTNET / Bybit TESTNET / Bybit DEMO)筛出三个别名
  (binance_testnet/bybit_testnet/bybit_demo)——mainnet_only/spot 项结构性
  进不来(不是按名字挡,`_VENUES` 演进自动继承);适配器**空凭证构造**
  (无签名方法不需要 key,绝不加载凭证文件/env)。`build_shadow_venue`
  对 mexc/binance_mainnet/未知名一律 SystemExit。
- **latency 诚实缺省**:venue 无 depth 能力时未发起任何请求,latency 记 0
  且该腿计入 unavailable——绝不编造一个「测得的」往返;BOOK_UNAVAILABLE
  观测的 latency=0 在模块 docstring 钉死语义。
- **_FETCHERS 登记**:shadow_track.py 是网络承载的 out-of-gate 驱动器,
  按边界自身纪律登记进 `tests/governance/test_crypto_data_boundary.py`
  `_FETCHERS` + `_EXTRA_ALLOWED_BY_FILE`(math/argparse/typing/live;
  live 是 venue 适配器 + shadow 核复用,与 verify_key_permissions/
  binance_readonly_smoke 同款)。诚实注:`tools/live_track_advance.py`
  本身不在 _FETCHERS(与 fred_fetch 同型的既有清单缺口)——本切片不扩大
  范围,仅记录。
- **真实首跑(2026-07-27,binance testnet,crowd_contra,$10k 名义)**:
  14 腿全观测落账(appended=14,chain/outbox 链全过),drain 真数据
  e2e 14 事件 dispatched/0 pending/8 源 PASS。**诚实发现:testnet 盘口
  稀疏——14 腿中 11 腿的 depth 快照 as_of 超 60s(BOOK_STALE,testnet
  簿更新极少),仅 3 腿可成交,mean_abs_slippage 439bps**——这正是
  SHADOW 要量化的缺口:testnet 深度极薄,paper 的「决策价成交」假设与
  真实可成交价差数百 bps;readiness 按预注册五 blocker 判定 ready=true
  (stale 腿计数如实呈现在 last_pass.stale,门规则不含 stale 比率——
  是否加 STALE_RATIO blocker 是独立的校准决策,本切片不加未预注册的规则)。
- **测试 `tests/tools/test_shadow_track.py`(18)**:legs 数学/缺价 fail-closed/
  名义校验;pass 落库计数/幂等重跑 appended=0/注入 clock 实测 latency/无 depth
  能力/depth 抛异常/返回 None 全 fail-soft;readiness 五 blocker 逐条 + 全绿
  ready=true + 链篡改双 INVALID;venue 注册结构守卫/mainnet 与未知名拒/
  空凭证构造;CLI 拒未知 venue/strategy;run_once 注入假 venue 假信号写证据
  端到端(文件与返回逐位一致)。

## 5. 非目标(本切片刻意不做)

1. **不发单**。深度端点是只读公共端点,`observe_*` 只产观测值;SHADOW「但不
   发送订单」由构造保证(没有任何下单调用路径被引入),不是靠审查纪律。
2. **不接 DeploymentState.SHADOW 的状态机写路径**。进入/退出 SHADOW 态是
   lifecycle C 系列(三正交状态机)的后续切片;本切片只生产「观测机器」,
   不驱动任何部署态转换。
3. **不做 depth 历史抓取/落盘研究数据**。depth 是实时观测输入(SHADOW 层的
   「现在」),不是研究数据集;若未来需要盘口历史做冲击校准,那是 tools/
   fetcher + `_FETCHERS` 治理白名单下的独立切片(本切片经核实:`_FETCHERS`
   只覆盖 tools/ 抓取器,适配器 depth 方法是 venue 适配器能力,无需进白名单)。
4. **不修复「paper fill = 决策价」语义**。SHADOW 观测量化这个缺口;改 paper
   语义是独立的、需要预注册评审的论文级决策。
5. **不接 console**。设计文档 S3 计划曾含 console 只读端点,落地时裁定**不做**
   (证据快照 `evidence/shadow_status.json` 已可读;console 展示是独立切片,
   需要契约测试双侧同改,不为收官而扩大面)。
6. **不改 paper_ready / 不碰 §12.3/12.4**。§12.1 PAPER 全条件门已存在
   (tools/paper_health.py);MICRO_LIVE/LIVE 是 Phase 9+,非本方案。

## 6. 727 Phase 7(§12.2)验收对照表

| 验收项 | 状态 | 承载 |
|---|---|---|
| 连接真实市场行情/盘口但不发送订单 | ✓(S1+S3) | 三适配器 `depth()` 只读公共端点 + S3 驱动器真实 testnet 轮询(空凭证,结构性无下单路径) |
| 理论成交 vs Paper fill vs 实时可成交价 | ✓(S1 核心) | `ShadowObservation`:decision_price / paper_fill_price / executable_vwap 三列并置 |
| 盘口深度 | ✓(S1) | `OrderBookSnapshot` 全深度 + `WalkBookResult.levels_consumed`/`filled`/`unfilled` |
| Spread | ✓(S1) | `book_spread_bps`(top-of-book,任一侧空簿诚实 None) |
| Slippage | ✓(S1) | `slippage_bps_vs_decision`(tca 同语义,带方向) |
| Latency | ✓(S3 实测) | `latency_ms` = 驱动器 depth 往返实测(可注入 clock;无 depth 能力记 0 不编造) |
| Expected impact | ✓(S1,条件计算) | `expected_impact_bps`(impact_model + adv 双给才算,UNKNOWN 绝不编造;S3 驱动器本轮不接 adv,恒 None 如实) |
| 真实时间推进 | ✓(S3) | `--follow --interval-seconds` 前台轮询(paper_realtime 同语义,绝不拉起后台进程) |
| 无未来数据 | ✓(S1) | `BOOK_STALE`/`BOOK_FROM_FUTURE` 门(max_book_age_ms;as_of 缺失诚实 None 不可判龄) |
| SHADOW provenance 标签(§18.2) | ✓ | `provenance="SHADOW"` 构造期 fail-closed 校验 + 证据快照同标签 |
| 观测持久化 + 事件链 | ✓(S2) | shadow_store 三链(观测/outbox/dispatch)+ SHADOW_OBSERVED schema(µs,脱敏摘要)+ 第七 dispatch + drain 第 8 源 |
| shadow_ready 全条件门 + 证据 | ✓(S3) | `build_shadow_readiness` 五 blocker + `evidence/shadow_status.json`(首跑 14 观测/3 可成交/439bps,testnet 稀疏诚实呈现) |
