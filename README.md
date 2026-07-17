# QUANTOS — AI Quantitative Trading Research Platform

> **TL;DR (English):** A safety-critical, **research-only** AI quant platform (readiness `L2_DRY_RUN_ONLY`) — built as an honest research machine: one validated deployable alpha (funding carry, *modeled* net Sharpe ~2.0–2.1 with heavy caveats) amid a pile of rigorous nulls. **No live trading, ever.**

<!-- CI 徽章占位(开启远程仓库 + 分支保护后替换为真实 URL) -->
[![validate](https://img.shields.io/badge/CI-validate-lightgrey)](.github/workflows/validate.yml)
[![readiness](https://img.shields.io/badge/readiness-L2__DRY__RUN__ONLY-orange)](#当前状态)
[![live trading](https://img.shields.io/badge/live%20trading-disabled-critical)](#安全横幅)

## Devpost / AI-assisted development

**Repository:** [github.com/kerstker/QUANTOS](https://github.com/kerstker/QUANTOS)

QUANTOS was developed with OpenAI Codex and GPT-5.6 as an AI-assisted engineering workflow, with human review and final control. The goal was not to ask an AI to invent a trading strategy and deploy it, but to use AI to build a reproducible research machine that can reject unsafe or unsupported conclusions.

- **Codex as the engineering environment:** Codex was used to navigate the repository, follow `AGENTS.md` safety rules, turn the blueprint into small testable changes, edit backend and console code, inspect diffs, and run the repository's validation gates.
- **GPT-5.6 for implementation and reasoning:** GPT-5.6 was used to design and review deterministic factor research, point-in-time data handling, leakage checks, purged/embargoed validation, fail-closed risk gates, dry-run execution, audit/event contracts, and the read-only research console. It also helped compare research findings against the safety boundary instead of treating backtest performance as permission to trade.
- **Human-in-the-loop accountability:** The model never receives broker credentials, never imports a broker/CTP/FIX SDK, and cannot submit live orders or reach the `APPROVED` state. Human review remains required for governance decisions. Every change is checked with tests, governance scanning, and the single fail-closed entry point, `python scripts/validate.py`.
- **Concrete AI-assisted outputs:** The repository contains the guarded `ResearchAgentLoop`, point-in-time research and validation engines, the dry-run pipeline, immutable evidence/audit artifacts, and a React console that exposes provenance and safety state rather than fabricating live-trading capability.



---

## 安全横幅

**就绪等级:`L2_DRY_RUN_ONLY` —— 只做研究与 dry-run 模拟。无实盘交易、无券商连接、无真实凭据。**

五个不可协商的安全开关全部默认在「安全」一侧(定义在 `quantos/contracts/config.py` 的 frozen Pydantic v2 `SafetyConfig`):

```
live_trading_enabled   = False
real_broker_enabled    = False
allow_order_submission = False
allow_ai_direct_trading= False
require_human_approval = True
```

- `assert_research_safe()` 失败即关闭(fail-closed):任何实盘开关被打开、AI 直连交易被允许、或人工审批被关掉,都会抛 `SafetyViolation`。
- 即便「armed」也不够:真正下一单还需要运行时的人工审批 token,且必须是非 AI 上下文。
- 这些不变量由治理扫描器和 `tests/governance/` 在每次门禁运行时机器强制执行。**没有显式、多步的人工审批,实盘永远不可能发生。**

---

## 这是什么

QUANTOS 是一个**安全关键的 AI 量化【研究】平台**。当前仍是早期阶段 —— 权威蓝图(`QUANTOS_MASTER_BLUEPRINT.md`)规划了 11 层、phases 0–14;本仓库的早期状态文档自记为 **~10% 建成**。

它**不是**交易系统,也不打算变成交易系统。它是一个用来做严肃量化研究、并在每一步都把「不能碰实盘」钉死在代码与 CI 里的实验台:

- 纯 stdlib、确定性、失败关闭;`services/` 整棵树**零网络 import**(离线由构造保证,不是靠约定)。
- 不导入任何券商 / CTP / FIX SDK;不硬编码凭据。
- 网络访问物理上只存在于门外的 `tools/`(只读公开市场数据)。
- 非人类 actor 全程无法到达 `APPROVED`,系统从未自动放行任何因子。

> 注:仓库内一处旧的代理操作指南把 data/alpha/portfolio/execution/intelligence 等目录标为「空脚手架」,这与当前树**不符**(它们已是真实且带测试的代码)。以本 README 的「架构一览 / 当前状态」为准。

---

## 为什么值得看 —— 一个诚实的研究机器

这个项目的价值不在「会不会印钞」,而在**它不自欺**。在大量加密因子研究里,系统挖出的几乎全是**严谨的 NULL**,并被如实归档:

- **短周期 4h / 小时线 = NULL**:加宽截面把样本内 Deflated Sharpe 抬到 0.586,但无窥视 OOS 证伪为 −2.01(选择性过拟合)。
- **日线趋势 `dist_from_high_14`**:在 2024–25 牛市样本内诱人(net Sharpe≈2.4),一出 regime(含 2022 熊市,5.5 年面板)就死(全样本 net Sharpe −0.55、IC≈0)。**已证伪、已归档**。
- **截面 Alpha101 级丰富因子 / 336 币宽截面 = 同样无信号**:把可表达因子空间放大几个数量级,幸存者仍是 0。这把「瓶颈是数据,不是因子生成」从论断变成了实证。

**唯一一个验证过的可部署 lead = 资金费 carry(流动永续上)**:

- 跨 regime 真赚钱:de-redundant carry 组合在 115 永续 × 多年面板上**7/7 年全正(含 2022)**;在流动子集 + 建模滑点下,**建模净 Sharpe ~2.1、~47% 年化、maxDD ~26%**。
- **但这是 lead,不是已证实可规模化的 alpha**,带一串硬折扣:幸存者偏差、**滑点是建模而非实测**(5bps 乐观,20bps→1.4)、拥挤可能衰减、尾部风险未建模、容量有限、IC≈0(收益来自尾部)。
- 它是**第一个正当的纸面跟踪候选**(区别于已证伪的 `dist_from_high_14`),而非魔法 alpha。

一个真 alpha + 一堆 null,正是这台机器**不骗自己**的证据。

---

## 架构一览

蓝图规划 11 层、phases 0–14。下面区分**真实(有实质代码 + 专门测试)**与**计划(空脚手架)**:

| 层 / 区域 | 路径 | 状态 |
|---|---|---|
| 共享契约 | `quantos/contracts/` | 真实 —— SafetyConfig、alpha/research/order/risk/killswitch/event/factor/portfolio 契约 |
| 数据护城河 | `services/data/` | 真实 —— PIT 存储、无幸存者偏差宇宙、版本化、质量、归一化、公司行为、摄入 |
| 特征 | `services/features/` | 真实 —— 计算、注册表、特征集、校验、泄漏检测 |
| 回测引擎 | `services/research/backtest_engine/` | 真实 —— 引擎、成本、指标、撮合、融资、事件引擎、报告 |
| 验证 | `services/research/validation/` | 真实 —— purged/embargo walk-forward、Deflated Sharpe、pipeline、gate |
| 因子引擎 | `services/research/factor_engine/domain/` | 真实 —— IC/ICIR 数学(合成数据上) |
| Alpha 生命周期 | `services/alpha/` | 真实 —— catalog、factory、状态机、ranker、相关/聚类/正交、拥挤、容量、BH-FDR 多重检验 |
| 组合 | `services/portfolio/` | 真实 —— 优化器、约束优化、因子风险 |
| 研究风险闸 | `services/risk/research_risk/domain/` | 真实 —— 9 引擎、确定性、失败关闭(**已有测试**) |
| 预交易风险(重建) | `services/risk/pre_trade/` | 真实 —— gate、checks、kill_switch、context、audit(功能性重建) |
| 执行 | `services/execution/` | 真实 —— intent_builder、boundary |
| 智能 / 代理 | `services/intelligence/` | 真实 —— `ResearchAgentLoop`(非人类 actor、无 APPROVE 路径)、guardrails、guided/evolutionary search、MCTS |
| 监控 / 部署 | `services/monitoring/`、`services/deployment/` | 真实 —— 审计日志、健康检查、读安全开关的部署闸 |
| 实验注册表 | `services/research/experiment_registry/` | 真实 —— stdlib sqlite3 持久化(跨进程的 Deflated-Sharpe 试验计数,失败关闭) |
| 纸面交易 | `services/research/paper_trading/domain/` | 模型真实;PnL 为**占位**(`notional*0.001`,真实对账是 Phase 9) |
| Dry-run 适配器 | `services/trading/dry_run_adapters/` | 真实、有测试、失败关闭(无券商 SDK) |
| 控制台 API | `services/research/console_api/main.py` | 真实 FastAPI,但提供**只读计算 / 合成快照**(非真实研究) |
| 前端 | `apps/research-console/src/` | React 18 + Vite 6 + TS(strict)+ Tailwind;26 页,mock-first |
| **隔离 / 损坏** | `services/trading/pre_trade_risk/main.py` | **非功能**(`QUANTOS_QUARANTINED_NON_FUNCTIONAL`),为 Phase 8 重建保留,门禁排除。**勿接线** |
| **计划(空)** | `services/gateway/`、`services/learning/`、`platform/`、`schemas/`(仅 JSON)、`proto/` | 脚手架占位,尚无 Python |

---

## 数据湖

所有抓取器都住在门外的 `tools/`(网络只在这里、只读公开市场数据、不在门禁内)。**没有一个导入交易所 / 券商 SDK,没有一个发出任何下单 / 账户 / 转账调用。**

| 工具 | 来源 | 鉴权 | 返回数据 |
|---|---|---|---|
| `coingecko_fetch.py` | CoinGecko `/coins/{id}/ohlc` | 只读数据 key | 日内 OHLC(无量) |
| `coingecko_volume_fetch.py` | CoinGecko `/market_chart` | 只读数据 key | 价格 + 真实 24h 滚动成交量 |
| `cryptocompare_fetch.py` | CryptoCompare `histoday` / `mktcapfull` | 可选只读 key(无 key 亦可) | 日线 OHLCV ~2000 天(达 2022 熊市)+ 市值榜 |
| `binance_klines_fetch.py` | Binance 公开 klines / exchangeInfo | **无 key、无账户** | 多年日线 OHLCV + USDT 现货标的 |
| `binance_funding_fetch.py` | Binance `fapi` 公开 `fundingRate` | **无 key、无账户** | 按日聚合的永续资金费(另类数据) |

**治理边界(由 `tests/governance/test_crypto_data_boundary.py` 用 AST 机器钉死):**

- 每个抓取器的 import 根 ⊆ `{json, os, time, sys, urllib, services, tools, __future__}`,且与交易所/券商 SDK(ccxt、binance、ib_insync、alpaca、ctp、vnpy、quickfix…)**互斥**。
- 源码不含任何 `create_order / place_order / cancel_order / withdraw / transfer` 等下单/转账调用。
- **`services/` 整棵树无任何网络 import** —— 离线核心由测试保证。
- **凭据**只是只读数据 key:运行时从环境变量或 gitignored 的 `key/` 目录读取,绝不硬编码 / 打印 / 提交。本地 PIT 缓存(`data/cache/*.db`)也被 gitignore,抓来的数据不进版本库。

---

## 快速开始

```bash
# 1) 单一失败关闭门禁(治理扫描器 + 全部可运行测试套件)
#    人和 CI 都跑这一个;任一检查失败即非零退出。离线、确定性、无需密钥/网络。
python scripts/validate.py

# 仅治理扫描器
python apps/research-console/scripts/run_paper_to_live_governance_check.py
```

```bash
# 2) 前端(mock-first)→ http://localhost:3100/research
cd apps/research-console && npm ci && npm run typecheck && npm run build && npm test
cd apps/research-console && npm run dev

# 前端对接真实只读后端(Phase 12 wiring;CORS 仅放行 :3100)
python -m uvicorn services.research.console_api.main:app --host 127.0.0.1 --port 8400
cd apps/research-console && VITE_API_URL=http://127.0.0.1:8400 VITE_MOCK_MODE=false npm run dev
```

```bash
# 3) 研究驱动(门外只读 CLI 研究脚本,需要网络;不在门禁内)
#    例如资金费 carry 这条 lead 的研究 / 验证 / 压测驱动:
python tools/crypto_funding_research.py
python tools/crypto_carry_strategy.py
python tools/crypto_carry_stress.py
```

---

## 仓库结构

```
quantos/contracts/         # 共享领域契约(SafetyConfig 等单一真理源)
services/                  # 后端服务树(统一于 Phase 1;纯 Python、离线、确定性)
  data/ features/          # 数据护城河 + 特征
  alpha/ portfolio/        # alpha 生命周期 + 组合
  research/                # 回测、验证、因子引擎、实验注册表、纸面交易、console_api
  risk/                    # research_risk(9 引擎闸)+ pre_trade(重建)
  execution/ intelligence/ # 执行边界 + 研究代理(ResearchAgentLoop)
  monitoring/ deployment/  # 审计/健康 + 读安全开关的部署闸
  trading/                 # dry_run_adapters(真实)+ pre_trade_risk(隔离/非功能)
  gateway/ learning/       # 计划(空)
tools/                     # 门外只读市场数据抓取器 + 加密研究驱动(唯一带网络处)
apps/research-console/     # 前端(React 18 + Vite 6 + TS strict);frontend-only
tests/                     # contracts/data/features/research/alpha/portfolio/risk/
                           # execution/intelligence/monitoring/deployment/governance/integration
scripts/validate.py        # 单一失败关闭门禁入口
docs/                      # 蓝图/计划/状态/研究结论/phase3a/superpowers
schemas/ proto/ platform/  # 计划占位(JSON schema / infra 配置)
```

---

## 当前状态

- **就绪等级:`L2_DRY_RUN_ONLY`** —— 研究 + dry-run 模拟,无实盘。
- **代码体量(从文件系统直接清点):`services/` 下 139 个 `.py`;`tests/` 下 104 个测试文件、741 个 `test_` 函数。**
- **门禁:** `python scripts/validate.py` 是单一失败关闭入口(治理扫描器 → 全部 pytest 套件);CI(`.github/workflows/validate.yml`)在 push 到 `main`/`master` 及所有 PR 上跑它,`permissions: contents: read` 最小权限。
  - `backend` job:Python 3.12,`pip install -r requirements-dev.txt`,跑 `python scripts/validate.py`(离线、无密钥)。
  - `frontend` job:Node 20,`npm ci → typecheck → build → test`。
- **研究结论(诚实):** 大盘加密「价+量+日线」段无跨 regime 真信号;唯一可部署 lead 是资金费 carry(策略 net@5bps≈2.03、流动子集压测 2.09,即建模净 Sharpe ~2.0–2.1,带一串硬折扣),已进纸面跟踪、未规模化。瓶颈是**数据**,不是因子生成。
- 注:不同状态文档对测试计数互有出入(程序计划记 433 py + 5 fe;活文档 `PROJECT_STATE.md` 记 `validate.py` 813 绿;文件系统直接清点为 741 个 `test_` 函数)。**以文件系统清点 + `validate.py` 实跑为准。**

---

## 深入文档

- 权威蓝图(11 层 / phases 0–14):[`QUANTOS_MASTER_BLUEPRINT.md`](QUANTOS_MASTER_BLUEPRINT.md)
- 开发程序计划(14 阶段 / 里程碑闸):[`docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md`](docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md)
- 系统架构(11 层逐层实况):[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- 路线图(阶段 / 现状 / 通往 L3):[`docs/ROADMAP.md`](docs/ROADMAP.md)
- 诚实研究纪实(null + 唯一验证的 carry):[`docs/RESEARCH_FINDINGS.md`](docs/RESEARCH_FINDINGS.md)
- 数据湖与数据源(只读公开行情 + 治理边界):[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md)
- GitHub 设置清单(description/topics/分支保护):[`docs/GITHUB_SETUP.md`](docs/GITHUB_SETUP.md)
- 项目活状态 / 交接:[`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md)
- 加密研究结论备忘录:[`docs/crypto-research-conclusions.md`](docs/crypto-research-conclusions.md)
- 代理操作指南:[`CLAUDE.md`](CLAUDE.md)
- 贡献规范:[`CONTRIBUTING.md`](CONTRIBUTING.md) · 安全策略:[`SECURITY.md`](SECURITY.md)
- 远程仓库 / 分支保护 runbook:[`docs/REMOTE_SETUP.md`](docs/REMOTE_SETUP.md)
- 安全契约源码:[`quantos/contracts/config.py`](quantos/contracts/config.py)
- 治理扫描器:[`apps/research-console/scripts/run_paper_to_live_governance_check.py`](apps/research-console/scripts/run_paper_to_live_governance_check.py)

---

## 复现性与数据许可

- **门禁 `scripts/validate.py` 是确定性的**(无 RNG / 无墙钟):同一提交逐位可复现。研究驱动(`tools/`)依赖本地缓存的数据快照(`data/cache/*.db`、`*.json`,均 gitignored),给定同一快照其结论可复现;但它们读墙钟时间并按需重抓,故跨日重跑不保证逐位一致。
- **数据许可**:抓取的公开行情仅作研究**缓存于本地、不随仓库分发**(`data/cache/`、`key/` 已 gitignore),使用时请遵守各数据源(CoinGecko / CryptoCompare / Binance)的服务条款。本仓库当前为 Private 且**尚无 `LICENSE` 文件**;开源前需先选定许可证(见 [`docs/GITHUB_SETUP.md`](docs/GITHUB_SETUP.md))。
- **已知缺口(诚实标注)**:carry lead 的**容量(可承载资金量)尚未估计**、滑点为**建模而非实测**、幸存者偏差未校正——这些都是评估其现实价值前的待补项,详见 [`docs/RESEARCH_FINDINGS.md`](docs/RESEARCH_FINDINGS.md)。

---

## 免责声明

- 本仓库**仅供研究与教育用途**,**不构成任何投资建议、要约或推荐**。
- 就绪等级为 `L2_DRY_RUN_ONLY`:**不进行实盘交易,不连接券商,不使用真实凭据**;实盘在没有显式、多步人工审批前永远不可能发生。
- 文中出现的任何回测 / 夏普 / 收益数字均基于历史与/或合成数据,且(如资金费 carry)滑点为**建模而非实测**,带有幸存者偏差、拥挤衰减、尾部风险未建模等已知折扣;**过往表现不预示未来结果**。
- 加密资产风险极高,可能损失全部本金。是否使用本仓库任何想法或代码,风险与责任完全自负。
