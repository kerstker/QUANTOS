# QUANTOS 系统排查审计 + 完善路线图

> 2026-06-29。9 智能体全量取证审计(每条结论带 `file:line`)。用户指令顺序:**① 先排查整个系统 → ② 再完善功能 → ③ 最后做前端指挥中心**。本文是 ① 的结论与 ②③ 的路线。

## 1. 一句话结论

**后端比文档说的真实得多——真正的缺口是"接线"(把已存在且已测的真引擎接进 `console_api`),不是"造引擎"。** 全门禁 ~1014 测试通过;`l2_ok = true`。

## 2. 真实度分布(36 个前端界面)

| 等级 | 数量 | 代表页 |
|---|---|---|
| ✅ REAL 真数据 | 7 | ResearchRuns / RunDetail / CompareRuns / CryptoLive / BacktestResults(誊抄真数,静态) |
| 🟡 PARTIAL 真引擎判假数据 | 6 | Risk / Validation / ComputationLab / Portfolio / PaperTrading / MonitoringDrift |
| 🟠 MOCK 合成常量 | 17 | Overview / Experiments / Events / EvidenceLedger / 多数治理页 |
| ⚪ NONE 纯前端硬编码 | 6 | AlphaZoo / DslExplorer / AlphaGraph / LifecycleMonitor / PopulationOptimizer / SignalToPortfolio |
| 🔴 BROKEN | 1 | services/trading/pre_trade_risk(已隔离,Phase 8 重建) |

## 3. 关键洞察

1. **假数据集中在一个根。** 6 个 PARTIAL 页的门/数学早是真的(`RiskApprovalGate` 9 引擎、`compute_ic/sharpe`、`PnLAttributor` 对账),唯一假的是输入:`services/research/console_api/main.py:85-100` 的 `_factor_values`/`_forward_returns` 是 `math.sin/cos`。**换这一处为 CryptoLive 真实快照 → 一次升真 6 页。** 全局最高杠杆。
2. **根 CLAUDE.md 过时。** "~10% built / paper PnL 占位 / research_risk 无测试 / data,alpha,portfolio 空脚手架"——全部过时。实测:`services/alpha`(26 模块/185 测试)、`data`(89)、`portfolio`(57)、`intelligence`(59)、`validation`/`backtest_engine`/`experiment_registry`/`monitoring audit_log` 等都真、都有测试。
3. **安全全绿。** 5 个 live 标志默认 SAFE 且 frozen;非人类 actor 到 APPROVED 三重锁;治理扫描器 182 文件 0 违规;`auto_approved=0`;唯一可变面 research_runner 机器钉死。唯一 **P2**:`tests/governance/test_research_runner_boundary.py` import 依赖 PYTHONPATH,错目录跑会假性跳过(非安全漂移)。

## 4. 真实引擎清单(可复用,完善功能时优先接它们)

| 引擎 | 路径 | 状态 | 能支撑的页 |
|---|---|---|---|
| factor_engine(IC/AST编译器) | services/research/factor_engine | REAL_TESTED | Validation/AlphaFactory/ComputationLab |
| backtest_engine(单/事件驱动) | services/research/backtest_engine | REAL_TESTED | BacktestResults/Explainability/Portfolio |
| validation(DSR/CPCV/walk-forward) | services/research/validation | REAL_TESTED | Validation/PromotionControl |
| alpha_factory + cs_genome/gp_tree | services/research | REAL_TESTED | AlphaFactory/PopulationOptimizer |
| factor_set(协同选集/去冗余) | services/research/factor_set.py | REAL_TESTED | AlphaFactory/PopulationOptimizer |
| paper_trading(真实对账 PnL) | services/research/paper_trading | REAL_TESTED | PaperTrading |
| run_registry / research_runner | services/research | REAL_TESTED | ResearchRuns/RunDetail/CompareRuns(已接) |
| experiment_registry(持久) | services/research/experiment_registry | REAL_TESTED | Experiments/EvidenceLedger |
| risk/research_risk(9 引擎门) | services/risk/research_risk | REAL_TESTED | Risk(已接)/ScenarioStress/PromotionControl |
| risk/pre_trade + KillSwitch | services/risk/pre_trade | REAL_TESTED | (未接)Risk 安全原语 |
| alpha(catalog/lifecycle/zoo/graph/optimizer) | services/alpha(26 模块) | REAL_TESTED | AlphaZoo/AlphaGraph/Lifecycle/PopulationOptimizer |
| data(PIT/universe/quality) | services/data | REAL_TESTED | DataCatalog/MonitoringDrift |
| portfolio(优化器/风险模型) | services/portfolio | REAL_TESTED | Portfolio/SignalToPortfolio |
| monitoring(audit_log hash链) | services/monitoring | REAL_TESTED | Events/EvidenceLedger/SystemHealth |

**真缺(非接线,需新建):** authn/z 网关(`services/gateway` 空)、股票/A股数据接入、model_registry、RBAC/审批/incident 工作流引擎、kill-switch/pre-trade 的 console 端点。

## 5. 路线图(已批准:先 Phase 0 + Phase 1;数据域=仅加密;真缺子系统页=诚实标注)

- **Phase 0 排查收口**(S,零风险):删 3 死文件 + 空目录;删 ~10 空 DDD 脚手架(误导架构地图);LifecycleMonitor/SystemHealth 补诚实徽标;修 P2;纠正 CLAUDE.md;App.tsx React.lazy 拆 533KB 首屏。
- **Phase 1 一处换源升真 6 页**(L,最高杠杆):`_factor_values`/`_forward_returns` 的 sin/cos → 真实快照收益 → Validation/Risk/ComputationLab/Portfolio/PaperTrading(+PromotionControl)升真;给真门加 REAL 徽标。
- **Phase 2 接已有真引擎**(L):PopulationOptimizer/AlphaGraph/DslExplorer/AlphaFactory/AlphaZoo 建端点(`services/alpha` 现成);BacktestResults 接 run_registry。
- **Phase 3 持久化与审计链**(L):Experiments/EvidenceLedger/Events/LifecycleMonitor/Agents/Overview。
- **Phase 4 安全原语 + 治理工作流**(XL):kill-switch/pre-trade 上台;PromotionControl 升真;真缺子系统页诚实标注 NO_GO。
- **Phase 5 前端指挥中心**(L,最后):react-grid-layout 自由网格,默认磁贴全部指向已升真的页;导航重组。

## 6. 已决策

1. 起步:**Phase 0 + Phase 1**,做完回看。
2. 数据域:**暂只用加密真实数据**(CoinGecko/Binance 离线快照),股票/A股延后。
3. 真缺子系统的治理页:**诚实标注"未实现/需 Phase 14 身份层"**,不伪造,集中精力升真有真引擎的页。

## 7. 安全护栏(本轮全程不破)

`LIVE_TRADING_ENABLED=false` / `REAL_BROKER_ENABLED=false` / `ALLOW_ORDER_SUBMISSION=false` / `ALLOW_AI_DIRECT_TRADING=false` / `REQUIRE_HUMAN_APPROVAL=true`;不引券商 SDK;不下单/不碰凭证;`auto_approved=0`;services/ 纯 stdlib 确定性;升真只换"读真实快照/registry",绝不引入实盘/网络到 services 核心。
