# 设计:research-console 信息架构重构(按顶级 AI 量化系统工作流重组)

> 2026-07-03。动因(用户反馈):「根本没法使用、太多无用界面、逻辑不清楚」。
> 证据基础:同日 7 路复核 + 三轮审计的页面真伪分类(REAL / SYNTHETIC 藍圖)。

## 1. 问题(核实过的)

- **42 页塞在 5 个导航组**,「研究」组一项就 17 个链接;仪表盘、操作台、数据源、因子工具、元信息混排——没有工作流逻辑。
- **12 页是纯合成壳或重复**:6 个治理蓝图页(incident/reviewer/access/committee/promotion/model-risk,端点 `SYNTHETIC_DETERMINISTIC`,真身份层是 POST_L2)+ `Agents`(端点合成,真 loop 无持久层)+ `Phase3AReadiness`(一次性阶段检查单,历史残留)+ `ScenarioStress`/`BacktestExplainability`(合成演示;真压力重演已随 Rank #7 进 Risk 页 stress 块)+ `SignalToPortfolio`(无端点的前端演示)+ `Overview`(与 CommandCenter 重复的第二仪表盘)。
- 真实承重页(ResearchRuns 操作台、DryRunPipeline、ICPanel、Validation、EvidenceLedger…)被淹没在噪声里。
- 路由/导航是两份手工清单(App.tsx 里 nav[] 与 <Routes> 各写一遍),已经出现漂移空间;冒烟测试第三份手工清单只盖 14/42 页。

## 2. 目标

1. 导航 = 研究生命周期(顶级量化系统的心智模型):总览 → 数据 → 因子研究 → 验证 → 组合风险 → 执行跟踪 → 治理系统。
2. 砍掉全部纯合成/重复页;诚实姿态由 `SystemCompleteness`(REAL_REPO_INVENTORY,真实列出缺失子系统)统一承担——删页不删诚实。
3. 路由、导航、冒烟测试收敛到**单一页面注册表**(single source of truth),新页自动进导航与测试。
4. 后端零改动(console_api 端点与契约测试不动;删的是前端页,合成端点保留)。

## 3. 新信息架构(7 组 · 28 个导航项 · 30 个页面文件)

| 组 | 页 |
|---|---|
| 总览 OVERVIEW | 指挥中心 CommandCenter(落地页,吸收 Overview 漏斗) |
| 数据 DATA | 数据目录 DataCatalog · 加密市场 CryptoLive · 外汇市场 FxResearch |
| 因子研究 ALPHA RESEARCH | 研究运行(操作台)ResearchRuns · IC 面板 ICPanel · 因子工厂 AlphaFactory · Alpha 资产库 AlphaZoo · 相关图谱 AlphaGraph · DSL 检查器 DslExplorer · 研究结论 ResearchFindings |
| 验证 VALIDATION | 验证门 Validation · 实验登记 Experiments · 回测战果 BacktestResults · 计算实验室 ComputationLab |
| 组合与风险 PORTFOLIO & RISK | 组合构建 Portfolio · 种群优化器 PopulationOptimizer · 风险仪表盘 Risk · 衰退监控 MonitoringDrift |
| 执行与跟踪 EXECUTION | 干跑流水线 DryRunPipeline · Carry 就绪 CarryReadiness · 纸面跟踪 PaperTrading · 生命周期 LifecycleMonitor |
| 治理与系统 GOVERNANCE & SYSTEM | 证据链 EvidenceLedger · 事件流 Events · 系统健康 SystemHealth · 系统完整度 SystemCompleteness · 架构 Architecture |

非导航路由:`/research/runs/:id`(RunDetail)、`/research/compare`(CompareRuns)。

**删除(12 个页面文件)**:Overview、Agents、ModelRiskReview、ReviewerWorkflow、AccessControl、IncidentResponse、PromotionControl、ReleaseCommittee、BacktestExplainability、ScenarioStress、Phase3AReadiness、SignalToPortfolio。

**旧路由重定向(书签不断链,语义就近)**:`/research`→指挥中心;6 治理蓝图页 + agents + phase-3a → `system-completeness`(那里诚实说明「未建成」);scenario-stress → risk(真压力块在此);backtest-explainability → backtest-results;signal-to-portfolio → portfolio。

## 4. 技术方案

- 新 `src/app/routes.tsx`:`PAGES: PageDef[]`(path/zh/en/icon/group/load 动态 import/end)+ `GROUPS` + `REDIRECTS`。App.tsx 从注册表派生 lazy 组件、`<Routes>` 与 `NavGroup[]`(AppShell 不动)。
- CommandCenter:漏斗磁贴链接 `/research` → `/research/alpha-zoo`(原目标已成重定向)。
- 冒烟测试改注册表驱动:遍历 `PAGES` 异步 import + render,断言非空 DOM(30/30,永久自动覆盖新页);新增导航一致性测试(nav ⊆ routes、重定向目标存在)。
- 保留:api 守卫测试、format 测试、mock-first 模式、全部真实页内部实现(本次不动页内视觉)。
- 删页遗留的 mock/类型导出暂留(无害、typecheck 不受影响),后续清理批处理。

## 5. 验收

`npm run typecheck && npm run build && npm test` 全绿(冒烟 30/30);后端 `scripts/validate.py` 全绿(零后端改动);导航 7 组 28 项;删除页路由 301 语义重定向;CLAUDE.md/PROJECT_STATE 页数与结构描述刷新。

## 6. 诚实边界

- 删页不改后端合成端点(契约测试锁定端点集合;端点清理属 Rank #24 后端拆分时再议)。
- `SystemHealth` 保留但其延迟数仍是硬编码合成(Rank #26 升真;页内已有诚实徽标)。
- 本次是**信息架构与测试地板**重构,不重做各页视觉(已是 premium fintech 风格);「逻辑清楚」靠分组 + 砍噪声 + 单一注册表达成。
