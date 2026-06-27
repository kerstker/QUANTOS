# QUANTOS 系统架构

> **就绪等级:`L2_DRY_RUN_ONLY`** —— 仅做研究 + 干跑(dry-run)模拟。无实盘交易、无券商连接、无真实凭据。
> 权威规格:[`QUANTOS_MASTER_BLUEPRINT.md`](../QUANTOS_MASTER_BLUEPRINT.md)(11 层蓝图,phases 0–14)。
> 程序计划:[`QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md`](QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md)。
> 活文档(当前状态以此为准):[`PROJECT_STATE.md`](PROJECT_STATE.md)。

本文描述 QUANTOS 的系统架构:11 层蓝图逐层标注实现状态、容器化与安全模型、数据到生命周期的端到端数据流、诚实评估机器,以及作为永久资产的截面 alpha 引擎与数据湖适配层。

**诚实声明:** 本平台尚处早期。这是一个**安全攸关的研究平台**,不是、且永远不会在此等级成为实盘交易系统。截至目前的全部实证研究里,只找到 **1 个**经得起检验的可部署候选(资金费 carry,**回测**净 Sharpe @5bps ~2.0、且诚实折扣后**可部署夏普远低于 2.0**,带一长串硬折扣),其余是一批严格记录的 **NULL**。这是研究诚信的体现,不是印钞机。

---

## 1. 11 层蓝图(逐层状态)

状态标注约定:**【已建】** = 有实质代码且有专门测试;**【部分】** = 有实质代码但功能不全或为占位实现;**【计划】** = 仅脚手架(`__init__.py` 或空目录);**【隔离】** = 已验证非功能、被排除在门禁之外。

> ⚠️ 仓库根的 `CLAUDE.md` 中 \"what actually exists (~10% built)\" 一节**已显著过时**:它把 `services/{data,alpha,portfolio,execution,intelligence}` 都列为空脚手架,但这些目录现都为**已建 + 有测试**。当前实测:`services/` 下 139 个 `.py` 文件,`tests/` 下 104 个测试文件 / 741 个 `test_` 函数。下表以文件树实测为准。

| # | 层 | 路径 | 状态 | 说明 |
|---|---|---|---|---|
| 0 | 共享契约 (Contracts) | `quantos/contracts/`(17 文件) | **【已建】** | `SafetyConfig`(§1.2)、alpha / research / order / risk / killswitch / governance / events / factor / portfolio / temporal 契约;单一事实来源。测试:`tests/contracts/*` |
| 1 | 数据护城河 (Data Moat) | `services/data/`(18 文件,~1232 LOC) | **【已建】** | PIT 存储、无幸存者偏差宇宙、版本化、质量、归一化、公司行为、摄入、持久/日历/基本面/行情存储。测试:`tests/data/*` |
| 2 | 特征 (Features) | `services/features/`(6 文件,~249 LOC) | **【已建】** | 计算、注册表、feature_set、校验、泄漏检测器。测试:`tests/features/test_features.py` |
| 3 | 因子 / 截面 alpha 引擎 | `services/research/factor_engine/`、`services/research/alpha_expression.py` 等 | **【已建】** | IC/ICIR 真实数学(合成数据)、截面 alpha 表达式引擎、GP 树、交互因子、标注、regime、frac_diff、样本权重。详见 §6 |
| 4 | Alpha 生命周期 | `services/alpha/`(20 文件,~1806 LOC) | **【已建】** | catalog、factory、生命周期状态机、evaluator、ranker、correlation、cluster、orthogonalize、crowding、capacity、decay、retirement、multiple_testing(BH-FDR)、guards、panel_backtest。测试:`tests/alpha/*`(17 文件) |
| 5 | 回测 / 验证(诚实评估机器) | `services/research/backtest_engine/`(11 文件)、`services/research/validation/`(5 文件) | **【已建】** | 事件驱动引擎、成本、撮合、融资、账本、公司行为;walk_forward(purged/embargo)、deflated_sharpe、pipeline、gate。详见 §5 |
| 6 | 组合 (Portfolio) | `services/portfolio/`(8 文件,~582 LOC) | **【已建】** | optimizer、constrained_optimizer、constraints、factor_risk、config。测试:`tests/portfolio/*`(6 文件) |
| 7 | 研究风险门 (Research-Risk Gate) | `services/risk/research_risk/domain/`(4 文件) | **【已建】** | 9 引擎失败关闭门(`gate.py`、`risk_modules.py` 294 LOC、`models.py`)。**现已有测试** `tests/risk/test_research_risk_gate.py`(纠正 CLAUDE.md \"No tests yet\") |
| 8 | 盘前风险 / 终止开关 | `services/risk/pre_trade/`(8 文件) | **【已建】** | gate、checks(230 LOC)、kill_switch、config、context、audit、order_bridge。这是**功能重建**(区别于隔离的旧模块)。测试:`tests/risk/test_pre_trade_gate`、`test_kill_switch`、`test_pre_trade_reasons` |
| 8 | 盘前风险(旧) | `services/trading/pre_trade_risk/main.py`(148 LOC) | **【隔离】** | 非功能,带 `QUANTOS_QUARANTINED_NON_FUNCTIONAL` 标记;两处缺陷(缺 `.domain` 模块 + 未定义名)。被 pytest `--ignore` 排除。**勿接线** |
| 9 | 纸面交易 (Paper Trading) | `services/research/paper_trading/domain/`(4 文件) | **【部分】** | models、reconciliation、consistency 已建;PnL 仍为**占位** `notional*0.001`(真实对账是 Phase 9 后续) |
| 9 | 干跑适配器 | `services/trading/dry_run_adapters/`(5 文件) | **【已建】** | 真实、有测试、失败关闭、**无券商 SDK**。测试:`dry_run_adapters/tests/`、`tests/integration/test_dry_run_all.py` |
| 10 | 执行边界 (Execution) | `services/execution/`(3 文件,~90 LOC) | **【已建】** | intent_builder、boundary(只构建意图,不下单)。测试:`tests/execution/*` |
| 11 | 智能体 / 自治研究 | `services/intelligence/`(9 文件,~828 LOC) | **【已建】** | `ResearchAgentLoop`(非人类 actor,无 APPROVE 路径)、guardrails、reward、guided_search、evolutionary_search、MCTS。详见 §2。测试:`tests/intelligence/*`(11 文件) |
| — | 监控 (Monitoring) | `services/monitoring/`(3 文件) | **【已建】** | audit_log、health。测试:`tests/monitoring/*` |
| — | 部署门 (Deployment) | `services/deployment/`(2 文件) | **【已建】** | gate(读取安全标志)。测试:`tests/deployment/test_deployment_gate.py` |
| — | 实验注册表 | `services/research/experiment_registry/`(3 文件) | **【已建】** | stdlib `sqlite3` 持久化(store、registry),跨进程保留试验计数以做可复现 DSR 计账。测试:`tests/research/test_experiment_*` |
| — | 控制台 API | `services/research/console_api/main.py`(~3796 LOC) | **【部分】** | FastAPI **只读** 计算/合成快照生成器(供前端),非真实研究后端。测试:`console_api/tests/`、`tests/research/test_console_api_contract.py` |
| — | 网关 (Gateway) | `services/gateway/` | **【计划】** | 仅 `__init__.py` |
| — | 学习 (Learning) | `services/learning/` | **【计划】** | 0 个 py 文件 |
| — | 基础设施占位 | `platform/`、`schemas/`、`proto/` | **【计划】** | `platform/` 有 kubernetes/terraform/observability 等配置树但无 Python;`schemas/` 3 个 JSON-Schema(events/risk/trading)+ avro/protobuf 子目录;`proto/` 仅 README |

前端 `apps/research-console/`(React 18 + Vite 6 + TS strict + Tailwind,26 页,mock-first)不属于 `services/` 层,Phase 12 双向契约守护(见 §4)。

---

## 2. 容器化与安全模型(非人类 actor 永不可达 APPROVED)

### 2.1 五条不可谈判的安全标志

编码于 `quantos/contracts/config.py`,为 frozen 的 Pydantic v2 `SafetyConfig`,每个标志默认即为 SAFE 值:

```python
live_trading_enabled   = False
real_broker_enabled    = False
allow_order_submission = False
allow_ai_direct_trading= False
require_human_approval  = True
```

- `live_trading_armed`(property)仅当**每一项**实盘前置条件都被显式打开**且**上下文非 AI 驱动时才为 True ——「arming is necessary but not sufficient: a live order also needs a runtime human-approval token」。
- `assert_research_safe()` **失败关闭**:任一实盘标志为真、或允许 AI 直接交易、或人工审批被关闭,即抛 `SafetyViolation`。
- `RESEARCH_SAFE_DEFAULTS = SafetyConfig()` 是研究/纸面代码处处使用的规范安全实例。

标志被接线进:`services/risk/pre_trade/checks.py`、`services/deployment/gate.py`、治理扫描器 `apps/research-console/scripts/run_paper_to_live_governance_check.py`。

### 2.2 容器化(Containment):非人类 actor 永不可达 APPROVED

自治智能体路径只持有**非人类 actor**,且**只能**经由加固过的 `ResearchAgentLoop` 触达状态机与风险门。该 loop 在物理上**没有通往 APPROVE 的路径**:

`services/intelligence/agent_loop.py` 的硬约束(逐字):

```python
self.actor = actor or Actor(actor_id=\"research-agent\", kind=ActorKind.AGENT)
if self.actor.is_human:
    raise ValueError(\"the autonomous research loop must run as a non-human actor\")
```

loop 只能把候选推进到 `PAPER_TRADING`,然后把它们交给**人工审核队列**(`human_review_queue`)等待人类审批 token。每一步都同时受 guardrail 策略与生命周期状态机检查,所以智能体**不能自我放行、不能重置终止开关**(这些动作会抛异常)。每个候选相互隔离:某个候选评估失败/触发 guardrail 时记录错误、**不推进**、继续其余(失败关闭)。

> 经验证的不变量(贯穿全部加密研究):非人类 actor 全程 `auto_approved=0`,所有因子(含 carry lead)= `REQUIRES_HUMAN_REVIEW`;系统**从未自动放行任何因子**。

### 2.3 两层威胁模型(CLAUDE.md §safety boundary)

- **运行时控制层** —— 智能体 guardrails(`services/intelligence`)、alpha 生命周期状态机、盘前风险门,治理传入的 *actor* 与 *evidence*:非人类 actor 不能到达 APPROVED、不能重置终止开关、不能实盘交易、不能触碰凭据。
- **构建期面(单进程内不防御)** —— 受信任代码若直接构造一个 `HUMAN` 的 `Actor`、或直接调 `AlphaLifecycleManager`/`KillSwitch`/`catalog._set_state`,即绕过咽喉点。此面由**代码评审、治理扫描器、(Phase 14)部署/进程隔离**治理。直接管理器/目录访问是人类受控工作流,**不是**智能体 API。

`platform/` 下的 kubernetes / terraform / observability / security / storage 配置树即为 Phase 14 部署期进程隔离的占位(尚无 Python)。

### 2.4 治理门(两道,均失败关闭)

- **(a) Paper-to-Live 治理扫描器** `apps/research-console/scripts/run_paper_to_live_governance_check.py` —— AST 走查 `quantos/` + `services/` 整树(锚定仓库根,跳过 `tests/`/`__pycache__`),四类违规:**imports**(禁券商/CTP/FIX SDK)、**credentials**(仅当真实非占位字面量赋值才报)、**apis**(`place_order` 等词边界正则)、**flags**(真实 `SafetyConfig` arming 标志被置 True / `require_human_approval=False`)。任一违规非零退出。
- **(b) 治理测试** `tests/governance/` —— `test_governance_baseline.py` 元测试保证 `services/` 下没有非测试 `.py` 逃出扫描范围、植入正/负控样本断言每类违规被抓且无误报;`test_crypto_data_boundary.py` 钉死数据边界(见 §7)。

单一门禁入口:`scripts/validate.py`(治理扫描器 → 全部 pytest 套件;任一失败即非零退出)。CI:`.github/workflows/validate.yml`(`permissions: contents: read`,backend Python 3.12 跑 `validate.py`,frontend Node 20 跑 typecheck/build/test)。

---

## 3. 数据流:数据 → 研究 → 评估 → 生命周期

```
┌─────────────┐   只读公共行情(网络只在门外 tools/)
│   tools/    │   coingecko / cryptocompare / binance / funding fetchers
│  fetchers   │   注入式 http_get → 离线可测;丢弃在途/未来 K 线
└──────┬──────┘
       │ 已抓取的原始记录(无 SDK、无下单、无转账)
       ▼
┌─────────────────────────────────────────────────────┐
│ [1] 数据护城河  services/data/                          │
│  SourceSpec 适配器(§8) → normalize_records(失败关闭)   │
│  → market_provider.ingest_provider_rows(now=…)        │
│    · reject_if_future 未来数据守卫                       │
│    · time_unit 跨尺度守卫(ms vs YYYYMMDD)              │
│  → SqlitePITStore(knowledge_time / store.as_of)        │
└──────┬──────────────────────────────────────────────┘
       │ PIT 安全的 (T x N) 面板(knowledge ≤ t)
       ▼
┌─────────────────────────────────────────────────────┐
│ [2][3] 特征 + 截面 alpha 引擎                            │
│  泄漏检测器 / FieldPanel / alpha_expression(§6)        │
│  ts 轴只回看、cs 轴只看同期 → 无窥视;forward label 仅评估用 │
└──────┬──────────────────────────────────────────────┘
       │ alpha 信号
       ▼
┌─────────────────────────────────────────────────────┐
│ [5] 诚实评估机器  validation/ + backtest_engine/        │
│  事件驱动回测(成本/撮合/融资) → IC/ICIR                  │
│  → Purged-CV / embargo walk-forward → 无窥视 OOS        │
│  → Deflated Sharpe(跨进程试验计数)→ PBO / BH-FDR        │
└──────┬──────────────────────────────────────────────┘
       │ ValidationVerdict(PASS / FAIL)+ evidence
       ▼
┌─────────────────────────────────────────────────────┐
│ [4][7][11] Alpha 生命周期状态机                          │
│  PROPOSED→TESTING→VALIDATED→PAPER_TRADING              │
│  9 引擎研究风险门(失败关闭)+ guardrails                  │
│  非人类 actor 止步于此 → human_review_queue            │
│         ─── APPROVED 需人类 token(智能体不可达)───        │
└─────────────────────────────────────────────────────┘
```

关键无窥视保证:bars 带 `knowledge_time`(「knowable at CLOSE」);fetcher 丢弃在途/未来 K 线;摄入缝 `ingest_provider_rows(now=…)` 武装第二道未来数据守卫与 time_unit 尺度检查;评估用的 forward return 是**已实现 LABEL**(按设计向前看 —— 那是结果不是信号)。

---

## 4. 前端契约守护(Phase 12)

控制台 API 是**只读**计算/合成快照生成器,无券商/OMS。Phase 12 接线**双向**契约守护:`tests/research/test_console_api_contract.py`(在门内)断言每个端点返回前端 `*Payload` 类型读取的精确顶层键;客户端在运行时校验只读快照标记(`assertConsoleSnapshot`),让漂移/非快照的 200 响应显式报错并诚实回退到 mock,而非静默渲染 `undefined`。

**契约双改规则:** 改一个 payload 形状 → 必须在**同一提交**里同时改 TS `*Payload` 类型与契约测试。

---

## 5. 诚实评估机器(DSR / PBO / BH-FDR / Purged-CV / 无窥视 OOS)

`services/research/validation/` + `backtest_engine/` 构成评估机器,其全部存在意义是**抵抗自我欺骗(选择性过拟合)**:

- **Purged-CV / embargo walk-forward**(`walk_forward.py`)—— 训练/测试折之间做 purge + embargo,切断标签重叠造成的信息泄漏。
- **无窥视 OOS** —— 因子选择只在样本内做,样本外**不回看**;一个样本内诱人的因子若在 OOS 死亡即被证伪、归档。
- **Deflated Sharpe(DSR)**(`deflated_sharpe.py`)—— 按试验次数与方差对 Sharpe 做去膨胀;实验注册表跨进程持久化试验计数,使 DSR 计账可复现、不在退出时被重置。
- **PBO(过拟合概率)/ BH-FDR**(`services/alpha/multiple_testing.py`)—— Benjamini-Hochberg 多重检验校正,控制在大量候选里的假发现率。
- **门 (gate)** `validation/gate.py` + 9 引擎研究风险门 —— 失败关闭裁决。

### 已证实它能挡住过拟合(实证)

评估机器的价值不是嘴上的,是被反复证明的:

- 短周期 4h 截面:加宽截面把**样本内** DSR 抬到 0.586,但无窥视 OOS 证伪(样本外 **−2.01**)—— 选择性过拟合被挡。
- 日线 `dist_from_high_14`:2024–25 牛市样本内扣成本净 Sharpe ≈2.4、DSR 0.95,但出 regime(5.5 年含 2022 熊市)一测即死(全样本净 Sharpe **−0.55**、IC≈0)—— 被识别为运气窗口、归档。
- 蛮力综合搜索 261 个截面 alpha:raw 前 15 名全是 carry 变体(DSR ~0.98–1.0),但对全 261 的**选择 OOS 为负(−0.45)**—— 盲目 best-of-N 过拟合被诚实机器拦下。

**唯一过关的 lead** —— 资金费 carry(流动永续,价格 + 实收资金费 − 手续费):**回测**净 Sharpe @5bps ~2.0(@20bps 1.36)、7/7 年全正含 2022,但带一长串硬折扣(幸存者偏差、滑点 5bps 偏乐观、拥挤近年衰减、尾部风险未建模、容量有限),故**可部署夏普远低于 2.0**——是拥挤的 yield 不是魔法 alpha。它是 **lead 而非已证实可规模化的 alpha**,且仍 = `REQUIRES_HUMAN_REVIEW`。详见 [`PROJECT_STATE.md`](PROJECT_STATE.md) §7。(注:`crypto-research-conclusions.md` 是更早一轮收尾,只覆盖价/量 NULL,**不含**这个更新的 carry lead。)

> 战略读数:「挖更多花哨因子」是错的杠杆,已实证。真正的杠杆是**数据护城河**(小盘长尾、另类数据、其它资产类别、更高频微结构)。截面 alpha 引擎 + 快引擎是**永久资产**(本身不产 alpha,有更好数据时即用)。

---

## 6. 截面 alpha 引擎(`alpha_expression`)

`services/research/alpha_expression.py` 给因子语法加上**截面这第二个轴**。逐个体的 `SIGNAL_OPS` 只能表达「从某个体自身历史得到一个标量」,所以多数公式化 alpha(WorldQuant Alpha101 类)**无法表达** —— 它们要把**时间序列算子**(delay/delta/rank/decay/corr,只读个体自身过去)与**截面算子**(rank/scale/demean,在某一瞬间横跨整个宇宙)组合起来。

- 表示:一个 alpha 是嵌套元组表达式,在 `FieldPanel` 上求值 —— PIT 安全的 (T × N) 字段矩阵(行=周期,列=个体)。
- **时间序列算子读 DOWN 一列**(只看该个体自身过去)→ 无 lookahead;
- **截面算子读 ACROSS 一行**(同一周期所有个体)→ 在 t 时刻全部可知;
- 组合后仍 PIT 安全:基矩阵由 `store.as_of`(knowledge ≤ t)构建,ts 轴只回看,cs 轴只看同 t。
- 纯 Python stdlib、确定性、失败关闭(缺失/过短 → `None`,向上传播)。无窥视由 `tests/research/test_alpha_expression.py` 钉死。

配套永久资产:`gp_tree`(遗传规划)、`interaction_factors`、`labeling`(triple-barrier / meta-labeling)、`regime`、`frac_diff`、`sample_weights`、`factor_timing`、`session`(研究会话编排)。

---

## 7. 数据湖与 SourceSpec 适配器

**网络物理上只在门外 `tools/`** —— `services/` 整树**零网络 import**(由 `test_services_tree_has_no_network_imports` 硬性钉死:禁 `urllib/socket/requests/httpx/aiohttp/websocket(s)/ftplib/telnetlib/http`)。研究核心是「构造即离线」,不是靠约定。

五个 fetcher(全部只读公共行情、无 SDK、无下单/转账):

| Tool | 源 | 鉴权 | 数据 |
|---|---|---|---|
| `coingecko_fetch.py` | CoinGecko `/coins/{id}/ohlc` | 只读数据 key | 日内 OHLC(无量) |
| `coingecko_volume_fetch.py` | CoinGecko `/market_chart` | 同上 | 价 + 真实 24h 滚动量(流动性) |
| `cryptocompare_fetch.py` | CryptoCompare `histoday` | 可选只读 key | 日线 OHLCV ~2000 天(达 2022 熊市) |
| `binance_klines_fetch.py` | Binance 公共 klines | **无 key/无账户** | 多年日线 OHLCV |
| `binance_funding_fetch.py` | Binance `fapi` 公共 `fundingRate` | **无 key/无账户** | 日聚永续资金费(另类数据) |

### SourceSpec 适配器(`services/data/adapters.py` —— 离线声明式缝)

加一个新数据源应是一次**声明,而非新解析器**。`SourceSpec`(frozen dataclass)声明某源的原始记录如何映射到规范 bar 字段(instrument_id / event_time / OHLC / volume)、在哪个时间尺度、close 在 bar 开盘后多久可知(`knowledge_lag`):

- `__post_init__` 失败关闭:`field_map` 缺必填键、既无 `instrument_id` 映射又无固定 id、或 `knowledge_lag < 0`,即抛。
- `normalize_records` 失败关闭:某记录缺映射字段或值非数值即抛清晰 `ValueError`(spec/data 不匹配是真错误,**绝不静默丢行**)。
- `register_source` 拒绝重名。
- 该层纯且离线:「never touches the network or credentials (that stays out-of-gate in tools/)」—— 只归一化已抓取的记录。

如此一来,更丰富的 feed(真实量、另一交易所、股票 OHLCV、更高级别加密套餐)插入只需一个 spec、**核心零改动**。本地 PIT 存储 `services/data/persistent_store.SqlitePITStore`(`data/cache/*.db`)被 gitignore,抓取的数据**不进版本控制**。

### 数据边界机器钉死(`tests/governance/test_crypto_data_boundary.py`,AST)

- 每个 fetcher 的 import 根 ⊆ `{json, os, time, sys, urllib, services, tools, __future__}`,且与禁用 SDK `{ccxt, binance, coinbase, krakenex, ib_insync, ibapi, alpaca, oandapyV20, fix, quickfix, ctp, vnpy, …}` 不相交。
- 源码不含任何 `create_order/place_order/submit_order/cancel_order/fapiPrivate/sapiPost/.withdraw(/transfer(` 等下单或转账调用。
- 新 fetcher 必须加入 `_FETCHERS` 列表,边界测试才覆盖到它。

---

## 8. 横切不变量:纯 stdlib / 确定性 / 失败关闭 / 可复现

这些是机器强制的横切约束(CT 不变量),贯穿全部 `services/`:

- **纯 stdlib / 确定性** —— 研究核心仅用标准库(`sqlite3`,核心不引 numpy/pandas);无 RNG / 无 wall-clock 依赖;前端无 `Date.now`/`Math.random`。唯一引 `aiokafka`/`prometheus_client` 的地方是**隔离的非功能模块**,它不运行。
- **失败关闭 (fail-closed)** —— 安全裁决、风险门、归一化、SourceSpec、生命周期推进,遇歧义/缺失一律**拒绝/抛出**,绝不静默放行或丢数据。
- **离线 (offline)** —— `services/` 整树零网络 import(机器钉死);网络只在门外 `tools/`,fetcher 注入式 `http_get` 使测试全离线。
- **可复现 (reproducible)** —— PIT `knowledge_time` + `store.as_of` 保证无窥视;实验注册表跨进程持久化试验计数以做可复现 DSR 计账;依赖 pin 在 `requirements.txt` / `requirements-dev.txt`。
- **无未来数据 / 无 lookahead** —— 多层防御:bars 带 `knowledge_time`;fetcher 丢在途/未来 K 线;摄入缝 `reject_if_future` + `time_unit` 跨尺度守卫;alpha_expression 的 ts 轴只回看、cs 轴只看同期。

---

## 关键文件路径

- 蓝图 / 计划:`H:\\code\\QUANTOS\\QUANTOS_MASTER_BLUEPRINT.md` · `H:\\code\\QUANTOS\\docs\\QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md` · `H:\\code\\QUANTOS\\docs\\PROJECT_STATE.md`
- 安全契约:`H:\\code\\QUANTOS\\quantos\\contracts\\config.py`
- 智能体咽喉点:`H:\\code\\QUANTOS\\services\\intelligence\\agent_loop.py`
- 截面 alpha 引擎:`H:\\code\\QUANTOS\\services\\research\\alpha_expression.py`
- 离线数据缝:`H:\\code\\QUANTOS\\services\\data\\adapters.py` · `H:\\code\\QUANTOS\\services\\data\\market_provider.py`
- 治理扫描器:`H:\\code\\QUANTOS\\apps\\research-console\\scripts\\run_paper_to_live_governance_check.py`
- 治理 / 边界测试:`H:\\code\\QUANTOS\\tests\\governance\\test_governance_baseline.py` · `H:\\code\\QUANTOS\\tests\\governance\\test_crypto_data_boundary.py`
- 单一门禁入口:`H:\\code\\QUANTOS\\scripts\\validate.py` · CI:`H:\\code\\QUANTOS\\.github\\workflows\\validate.yml`
- 加密研究结论:`H:\\code\\QUANTOS\\docs\\crypto-research-conclusions.md`
