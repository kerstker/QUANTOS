# QUANTOS 阶段路线图(ROADMAP)

> **用途**:用一页讲清"我们在哪、下一步往哪、通往实盘的门怎么过、什么绝不做"。
> 约定:本文中文叙述,代码标识符/命令/文件路径/徽章用英文。
> 权威规格以 [`QUANTOS_MASTER_BLUEPRINT.md`](../QUANTOS_MASTER_BLUEPRINT.md) 为准,
> 详细计划见 [`docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md`](QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md),
> 当前真实状态见 [`docs/PROJECT_STATE.md`](PROJECT_STATE.md)。
> 本文若与上述活文档冲突,以活文档为准。

---

## 0. 一句话定位

QUANTOS 是一个**安全关键的 AI 量化研究平台**,当前就绪等级 **`L2_DRY_RUN_ONLY`** ——
**只做研究 + 干跑模拟,无实盘、无券商连接、无真凭证**。这是一台"诚实的研究机器":
它的价值不在于"已经会印钱",而在于**严格地把没信号的东西判成没信号**,只在极少数情况下
留下一个**带满折扣、需人工审批的候选**。

---

## 1. Phase 0–14 概览(11 层 / 阶段地图)

阶段编号沿用 blueprint 与 program plan(`docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md`,
里程碑门 G0.5–G14)。下表是**面向路线图的概览**,不是逐字规格;详细状态以 program plan 为准。

| 阶段 | 主题(概览) | 当前定位 |
|---|---|---|
| Phase 0 / 0.5 | 安全地基、契约、治理门、最小可跑骨架 | 后端已建成 |
| Phase 1 | 服务树统一(全部归口 repo 根 `services/`)、数据契约 | 已建成 |
| Phase 2 | 数据护城河(PIT 存储 / survivorship-free 宇宙 / 版本化 / 质量) | 已建成 |
| Phase 3 | 特征 + 因子引擎(IC/ICIR)、泄漏检测 | 已建成 |
| **Phase 3A** | **治理门:通往 L3 / 接触券商的就绪评审(Go/No-Go)** | **NO_GO(见 §4)** |
| Phase 4 | 回测引擎(成本/财务/事件驱动/限价队列) | 已建成 |
| Phase 5 | 验证 / 诚实(walk-forward 含 embargo、Deflated Sharpe、无窥视 OOS) | 已建成 |
| Phase 6 | Alpha Factory(catalog / 生命周期状态机 / 多重检验 BH-FDR / 去冗余) | 已建成 |
| Phase 7 | 组合 + 风险模型(优化器 / 约束 / 因子风险) | 已建成 |
| Phase 8 | 风险门 + kill-switch(`services/risk/pre_trade/` 功能性重建) | 已建成 |
| Phase 9 | 纸面交易 / 对账(均成本制真实对账:realized+unrealized−成本,逐笔残差≈0) | 已建成 |
| Phase 10 | 智能体 / 容器化(`ResearchAgentLoop`、guardrails、导航式搜索) | 已建成 |
| Phase 11 | 监控 / 审计链 / 实验注册表持久化 | 已建成 |
| Phase 12 | 前端只读控制台 + 契约双边守护 | 已建成 |
| Phase 13 | 部署门(读取安全 flags 的 fail-closed gate) | 已建成 |
| Phase 14 | 进程 / 部署隔离(防 build-time 旁路面) | 规划中 |

> **诚实标注**:`CLAUDE.md` 中"~10% built"那段(以及其"paper_trading 仍是 placeholder PnL"的描述)已
> **显著落后于代码现状**。当前 `services/` 树下有 **139 个 `.py` 文件(~15.4k LOC)**;`tests/` 子树下有
> **104 个测试文件 / 741 个 `def test_`**(另有 ~60 个测试散在 `services/**/tests/`,门禁 `validate.py`
> 跑全量约 800+),上表多数"已建成"项有专属测试。但"已建成"=**有真实、有测试的代码**,**不等于"已产出可
> 部署 alpha"**——后者目前只有一个候选(见 §2)。空壳(仅 `__init__`、无实现 py):`services/gateway/`、
> `services/learning/`,以及 `platform/`、`schemas/`、`proto/` 的 py 侧。

---

## 2. 已完成(里程碑实绩)

四块**真实、有测试**的能力已经立起来:

**(1) 研究平台**
- 数据 → 特征 → 因子 → 回测 → 验证 → 组合 → 风险 → 智能体的全栈骨架都有真实代码与测试。
- 截面 alpha 引擎(`services/research/alpha_expression.py`):在 PIT 面板上组合时序算子 +
  截面算子(rank/scale/demean/zscore),解锁 Alpha101 那类真因子;无前视测试钉死。
- 因子发现:GP 表达式树、进化搜索(opt-in 去冗余 / 多样性奖励)、MCTS/UCT 导航、UCB 导引搜索。
- 正式主线编排 `intelligence/research_session.py::run_research_session`:固定受控搜索 runtime，
  调用 `research/session.py` 的确定性核心；模式 `evolutionary/gp/mcts`，搜后产出去冗余
  协同集，`oos_validate=True` 对自己挖的因子跑无窥视 OOS 自验证。

**(2) 诚实机器(防过拟合 / 防自欺)**
- 无窥视滚动选择 OOS、walk-forward(含 embargo 禁运)、Deflated Sharpe、PBO、regime 门——全部 fail-closed。
- 实证战绩:把 4h 短周期、日线趋势 `dist_from_high_14`、Alpha101 级丰富截面因子、
  336 币宽截面统统判成 **NULL / 已证伪**;蛮力综合搜索的 best-of-N 被诚实机器当场拦下(选择 OOS 为负)。
- **战略读数(已实证)**:"挖更多花哨因子"是错的杠杆——瓶颈是**数据**,不是因子生成。

**(3) 数据湖 / 数据护城河(门外只读)**
- PIT 存储、survivorship-free 宇宙、版本化、质量校验、内存读缓存(回测从小时级降到秒级,与 SQLite 逐位一致)。
- 加密多源抓取器(全部在 `tools/`,网络只在门外,只读公开行情,无任何 SDK / 下单):
  CoinGecko OHLC、CoinGecko 真实成交量、CryptoCompare 多年日线(含 2022 熊市)、
  Binance 公开 klines、Binance 公开 `fapi` 资金费率。
- 治理边界 `tests/governance/test_crypto_data_boundary.py` 以 AST 钉死:抓取器只含 stdlib+services/tools、
  无交易所/券商 SDK、无下单/转账调用。

**(4) 第一个验证通过的 carry alpha(lead,非已证实可规模化)**
- **资金费 carry**(流动永续):是迄今唯一**同时过 OOS + 成本 + 跨 regime(含 2022)**的因子。
- 流动永续上、扣**建模**滑点后:**净 Sharpe ~2.1、~47% 年化、7/7 年全正(含 2022)、maxDD ~26%**。
- ⚠️ **这是诚实标注,不是印钞机**:滑点是**建模的、不是实测的**(5bps 乐观,20bps→1.4);
  叠加幸存者偏差、拥挤/可能衰减、未建模尾部风险、容量有限、IC≈0(尾部效应)、OOS 噪声大等**一串硬折扣**。
  定性结论钉死:**carry 是第一个正当的纸面跟踪候选(`paper_track_start` 已记),是 lead,不是已证实可规模化的 alpha。**

> **容器化不变量全程守住**:非人类 actor 全程 `auto_approved=0`,所有因子(含 carry lead)=
> **REQUIRES_HUMAN_REVIEW**;系统**从未自动放行任何因子**。

---

## 3. 下一步(在 L2 内继续,不碰实盘)

下一段全部发生在 **`L2_DRY_RUN_ONLY` 边界内**,目标是把"数据护城河"这条真杠杆做厚,
并把 carry lead 坐实/证伪。不涉及任何券商、下单、实盘 flag。

**(1) Alpha 量产工厂**
- 把截面 alpha 引擎 + 快引擎(已是**永久资产**:本身不产 alpha,有更好数据时即用)接到更系统的批量搜索 +
  生命周期归档,产出**经诚实机器筛过**的候选流;`auto_approved=0` 不变。

**(2) Alt-data 多源扩展**
- 沿"数据是真杠杆"的实证读数扩源:小盘/低流动性长尾(低效定价处)、链上/资金费/情绪/盘口微结构等另类数据、
  其它资产类别、更高频/微结构。这些需要用户拍板成本/资源的"口子"(见 PROJECT_STATE 的待决数据口子)。
- carry lead 的坐实路径:抗幸存者偏差的永续历史、真实 funding+fee 损益、并入其它免费持仓数据(OI/多空比)、
  纸面跟踪看衰减。

**(3) 动态搜索**
- 继续打磨进化 / GP / MCTS / 导引搜索的导航与去冗余、多样性奖励,在更厚的数据上跑——
  搜索本身永远在容器化边界内,产物永远是"需人工审批的候选"。

---

## 4. 通往 L3 的路(治理门,人工多步审批专属,绝不自动)

L3 = 接触真实券商 / 真实凭证 / 实盘可能性的就绪等级。**通往 L3 的唯一通道是 Phase 3A 治理门。**

- **当前状态:`Phase 3A = NO_GO`**(见 `docs/QUANTOS_TOP_TIER_AI_QUANT_DEVELOPMENT_PROCESS.md`
  与 `docs/phase3a/` 下的就绪看板 / Go-No-Go 评审包 / 入场证据追踪 / 阻塞燃尽计划等 9 份文档)。
- **审批是人工、多步、专属的**。安全模型钉死:
  - 五个 flag 默认全 SAFE(`quantos/contracts/config.py` 的 frozen `SafetyConfig`):
    `live_trading_enabled=False / real_broker_enabled=False / allow_order_submission=False /
    allow_ai_direct_trading=False / require_human_approval=True`。
  - `live_trading_armed` 仅当所有 live 前置被显式打开**且上下文非 AI 驱动**才为真——而且
    **"armed 是必要非充分:实盘下单还需一个运行时人工审批 token"**。
  - 非人类 actor **永不可达 APPROVED**、永不能重置 kill-switch、永不能交易实盘、永不能碰凭证;
    自主智能体路径只持有非人类 actor,只能经硬化的 `ResearchAgentLoop` 到达状态机/门。
- **两道机器强制**护住这条路:fail-closed 治理扫描器
  `apps/research-console/scripts/run_paper_to_live_governance_check.py`(扫 `quantos/`+`services/` 整树,
  抓 imports/credentials/apis/flags 四类违规)+ `tests/governance/` 测试(覆盖不能静默退化、正/负控制都钉死)。
- **远程仓库的分支保护需人工启用**(`docs/REMOTE_SETUP.md` 明确:AI 无法创建远程 / 推送 / 开启保护——人工专属步骤)。

> **一句话**:从 L2 到 L3 没有"自动晋级"。必须由**人**走完 Phase 3A 的多步证据 + 评审 + 显式审批,
> 治理门绿、分支保护人工开启,缺一不可。

---

## 5. 明确"不会做"的红线(非可让,机器强制)

下列事项**永不做**——不是"暂时不做",而是被治理扫描器 + `tests/governance/` 钉死的不变量:

- **永不实盘**:`LIVE_TRADING_ENABLED` / `REAL_BROKER_ENABLED` / `ALLOW_ORDER_SUBMISSION` /
  `ALLOW_AI_DIRECT_TRADING` 永不在代码里被置 `True`;`REQUIRE_HUMAN_APPROVAL` 永不置 `False`。
- **永不让 AI 直接交易 / 自动放行**:非人类 actor 永不可达 APPROVED、永不触碰凭证、永不下单;`auto_approved=0`。
- **永不导入券商 / CTP / FIX SDK**:治理扫描器抓 `ctp/thost*/pyctp/vnpy/quickfix/fix*/broker_*` 等;
  抓取器边界另抓 `ccxt/binance(SDK)/coinbase/kraken/ibapi/alpaca/...`。
- **永不提交密钥 / 真凭证**:密钥只读自 env 或 gitignore 的 `key/`,绝不硬编码 / 打印 / 提交。
- **永不用未来数据 / 引入前视(lookahead)**:bars 带 `knowledge_time`;抓取器丢弃进行中的实时 K 线 +
  任何未来时间戳点;ingest 接缝有第二道未来数据 + 跨刻度守护。
- **永不破坏离线 / 确定性**:`services/` 整树**零网络 import**(机器强制);网络只在门外 `tools/`;
  纯 stdlib(禁 numpy/scipy/torch/pandas/ccxt)、无 RNG / wall-clock、fail-closed、可复现。
- **永不藏 / 删失败测试,永不弱化风险门**:研究 / 纸面 / 实盘严格隔离;策略(意图)与风险(许可)分离;
  券商逻辑藏在 adapter 之后。
- **永不把捏造数字写进文档**:没亲眼在工具输出里看到的数字绝不写(PROJECT_STATE §7 记录过一次历史造假已全数撤回——以活文档为准)。

---

## 6. 单一门禁与关键路径

```bash
# 单一 fail-closed 门禁(改完必跑,绿了才提交;CI 同款)
python scripts/validate.py     # AST 治理扫描 + 全 pytest 套件
```

**关键文件(绝对路径)**
- 安全契约:`H:\code\QUANTOS\quantos\contracts\config.py`
- 治理扫描器:`H:\code\QUANTOS\apps\research-console\scripts\run_paper_to_live_governance_check.py`
- 治理测试:`H:\code\QUANTOS\tests\governance\test_governance_baseline.py`、`test_crypto_data_boundary.py`
- 门禁入口:`H:\code\QUANTOS\scripts\validate.py`;CI:`H:\code\QUANTOS\.github\workflows\validate.yml`
- 智能体咽喉点:`H:\code\QUANTOS\services\intelligence\agent_loop.py`
- 活文档:`H:\code\QUANTOS\docs\PROJECT_STATE.md`、`docs\crypto-research-conclusions.md`、`docs\phase3a\`
