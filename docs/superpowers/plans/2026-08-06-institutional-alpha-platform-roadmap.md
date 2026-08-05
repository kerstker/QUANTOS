# QUANTOS 机构式 Alpha 平台实施总路线图

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按可信基线、小时级 Alpha→Paper、受控中频执行、专业工作台四个独立里程碑，把 QUANTOS 演进为“研究自治、资本受控”的机构式量化平台。

**Architecture:** 保留现有 `services/` 研究与治理核心，以及隔离的 `live/` 执行边界；先建立一致、可复现的 L2 证据基线，再构建小时级数据、Alpha Garden、验证 DAG 和 Shadow/Paper 闭环。执行控制面与前端分别使用后续独立计划，任何阶段都不能把 Alpha 的机器批准解释为真钱授权。

**Tech Stack:** Python 3.14、FastAPI 0.139、SQLite、NumPy、scikit-learn、React 18、TypeScript、Vite 6、pytest、Vitest、Binance WebSocket/REST（Wave 2）。

## Global Constraints

- 当前模式必须保持 `L2_DRY_RUN_ONLY`，直到独立 Phase 3A Go/No-Go 和外部可信基础设施完成。
- 五个安全旗帜保持：`LIVE_TRADING_ENABLED=false`、`REAL_BROKER_ENABLED=false`、`ALLOW_ORDER_SUBMISSION=false`、`ALLOW_AI_DIRECT_TRADING=false`、`REQUIRE_HUMAN_APPROVAL=true`。
- 新 Alpha 可由机器自动推进至 Paper；首次真钱额度必须由多个强身份真人批准。
- AI 不得接触凭证、扩大资本、修改风险限额、复位 kill-switch 或绕过 mandate。
- 首期市场是 Binance USDT 永续，频率为 1–4 小时，动态逐时点宇宙为 30–80 个合约。
- Alpha Garden 首期目标吞吐为每天 1 万–10 万候选；允许 0 个候选通过。
- 候选只能使用类型安全 DSL 和登记模型模板，不允许任意 Python 进入研究或执行进程。
- 所有 prose、计划、规格和用户界面默认中文；代码标识、协议字段和标准缩写保留英文。
- 任何 evidence、readiness 或 validation artifact 必须绑定当前 commit、输入摘要和同一 canonical 验证运行。
- 执行前必须保护当前工作树既有修改；不得 `git reset --hard`、`git checkout -- evidence docs` 或覆盖未提交 evidence/docs。
- 测试必须通过 `python scripts/validate.py` 或计划指定的定向命令；不要使用会被 RTK hook 误处理的裸 `pytest`。

---

## 子计划边界

### Plan A：Wave 0 可信基线

文件：`docs/superpowers/plans/2026-08-06-wave0-trusted-baseline.md`

交付：

- 在隔离 worktree/临时 evidence root 中捕获当前真实失败，不覆盖主工作树 artifact；
- 修复 FastAPI 0.139 路由契约、治理扫描器字段字面量、依赖/前端 lint 及当前真实失败；
- canonical 重生成并一致性校验 readiness、供应链和 top-tier 证据；
- 全量门禁回绿且状态仍为 L2。

进入条件：已批准设计文档 `docs/superpowers/specs/2026-08-06-institutional-alpha-mft-workbench-design.md`。

退出条件：`python scripts/validate.py` 在干净执行工作树中返回 0，最终证据属于同一 commit/运行且无互相矛盾状态。

### Plan B：Wave 1 小时级 Alpha Garden → Paper

文件：`docs/superpowers/plans/2026-08-06-wave1-hourly-alpha-garden-paper.md`

交付：

- 小时级 PIT 数据合同和动态 30–80 币宇宙；
- canonical `alpha_id`、家族去重和每天 1 万–10 万候选生成；
- append-only Trial Ledger 和分层验证 DAG；
- Shadow/Paper 前向证据、现实差距、漂移、暂停和退休；
- 只读的 Alpha→Paper 运维状态，绝不新增真钱能力。

进入条件：Plan A 全绿并提交。

退出条件：至少 7 天 soak 由单独运维任务完成；功能实现阶段先通过确定性回放、崩溃恢复、零通过者和端到端 Alpha→Paper 验收。

### Plan C：Wave 2 受控中频执行

后续另写，不能和 Plan B 混做。范围包括 Binance 行情流、常驻 scheduler、OMS 订单事件状态机、实时风险、账户级对账、独立执行权限域和 Paper/Testnet soak。Micro-Live 仍需要外部 IdP/mTLS、独立签名权威、签署 mandate、前向证据和人工 Go/No-Go。

### Plan D：Wave 3 专业工作台

后续另写，不能在统一实时契约稳定前先造界面。范围包括五对象工作区、snapshot+delta、SSE/WebSocket、强身份命令、全局治理抽屉，以及删除重复、陈旧和合成页面。

## 依赖顺序

```text
Plan A 可信基线
  └─ Plan B 小时级 Alpha → Paper
       ├─ Plan C 受控中频执行
       └─ Plan D 专业工作台（可在 Plan B read-model 稳定后启动）
```

Plan C 与 Plan D 可以在 Plan B 的稳定接口完成后并行，但真钱操作面必须等待 Plan C 的权限和命令契约，不得由前端直接调用 exchange adapter。

## 总体验收

- 每个 Plan 独立提交、独立测试、独立评审；禁止一个提交横跨研究、执行和前端三个信任域。
- 每个阶段的失败都诚实阻止下一阶段，不能用 mock、旧 artifact 或手工改 JSON 转绿。
- 最终系统能自动生成、验证、Shadow/Paper 和淘汰 Alpha；真钱只在真人签署的有限授权内自治。
