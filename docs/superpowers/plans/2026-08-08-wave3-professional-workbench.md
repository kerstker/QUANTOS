# QUANTOS Wave 3 专业工作台实施计划

> 状态：实施中。Task 39 的 Paper/Testnet soak 是独立运维任务；缺少显式运维启动、
> Testnet 凭证和有效 mandate 时必须保持 `OPERATIONAL_SOAK_PENDING`，不阻止本计划消费
> Task 37 已冻结的只读契约。

**Goal:** 用五个对象工作区和全局治理抽屉替换当前分散、重复的研究控制台；以 Wave 1
Alpha/Paper authority 和 Wave 2 MFT read model 为唯一实时事实源，形成可补洞、可追溯、
无隐式 mock 的专业工作台。

**Architecture:** 前端只通过版本化 HTTP/SSE/WebSocket 契约读取状态，不导入服务代码、
不读取 SQLite、不调用 exchange adapter。初始 snapshot 带 schema/as-of/freshness/watermark/
provenance；增量严格按 cursor 应用，任何 gap、未知 schema、哈希不一致或补洞失败都进入
`RESNAPSHOT_REQUIRED`，绝不把旧状态标为正常。写操作只通过独立 governance control plane，
必须由服务端验证强身份、RBAC、目标版本、幂等键和审计链；外部 IdP/mTLS 不可用时命令面
明确禁用。

**Tech Stack:** React 18、TypeScript 5.6、Vite 6、Vitest、Testing Library、Playwright、
FastAPI 0.139、SQLite read model、SSE/WebSocket。

---

## 不可改变的边界

- 保持 `L2_DRY_RUN_ONLY`、`LIVE_TRADING_ENABLED=false`、`REAL_BROKER_ENABLED=false`、
  `ALLOW_ORDER_SUBMISSION=false`、`ALLOW_AI_DIRECT_TRADING=false`、
  `REQUIRE_HUMAN_APPROVAL=true`。
- Wave 3 不创建 mainnet 能力，不读取凭证，不签 mandate，不提交订单，不复位 kill-switch。
- 前端不得直接调用 `live/` adapter、scheduler、OMS、ledger 或数据库；执行状态只消费
  Task 37 的 `/api/v1/console/mft-execution` snapshot/delta v1 契约。
- 真实模式中禁止隐式 mock、缓存旧快照冒充当前状态、把 HTTP 200 解释为可用，或把
  timeout、429、空结果、未知 schema、缺字段和断流解释为成功。
- 演示模式和真实模式永久视觉区分；关键状态同时使用文字、图标和颜色。
- 所有命令都必须由独立 control plane 服务端鉴权；浏览器字段、`prompt`、本地存储和
  自报 reviewer 均不构成身份或授权。
- 每个任务独立提交，不把后端 read contract、治理写路径和前端页面混在同一提交。

## 冻结数据源

| 对象工作区 | 首选事实源 | 允许的辅助源 | 禁止 |
|---|---|---|---|
| Market Monitor | MFT v1 `market_gap`、`scheduler`、`risk` | 现有只读 overview/market snapshot | 直连 venue、伪造行情 |
| Alpha Control Plane | `/api/v1/console/hourly-alpha` | Alpha library/family/lifecycle 只读端点 | 前端拼接候选状态为批准 |
| Validation Evidence | Trial Ledger/validation/forward evidence 只读端点 | 版本化 artifact snapshot | 旧 evidence 回退、合成 PASS |
| Portfolio & Risk | portfolio/risk 只读端点 + MFT `positions/risk/reconciliation` | attribution/drift snapshot | 从界面改风险数字 |
| Execution & Orders | Task 37 MFT v1 的 `intent_funnel/oms/fills/tca/mandate_expiry` | 无 | adapter、私有执行存储 |

全局治理、审计、健康和任务抽屉只聚合相应只读端点，不复制五个工作区的完整内容。

---

## Task 40：冻结 Wave 3 页面与数据源 manifest、严格客户端契约

**Files**

- Create: `apps/research-console/src/workbench/data-source-manifest.ts`
- Create: `apps/research-console/src/workbench/contracts.ts`
- Create: `apps/research-console/src/workbench/mft-reducer.ts`
- Create: `apps/research-console/src/workbench/__tests__/contracts.test.ts`
- Create: `apps/research-console/src/workbench/__tests__/mft-reducer.test.ts`
- Modify: `apps/research-console/src/lib/api/research-console-api.ts`
- Modify: `quantos/contracts/hashing.py`
- Modify: `quantos/contracts/__init__.py`
- Modify: `services/monitoring/mft_read_model.py`
- Test: `apps/research-console/src/lib/api/research-console-api.test.ts`
- Test: `tests/research/test_mft_execution_console_contract.py`
- Test: `tests/contracts/test_contracts.py`

**Acceptance**

- manifest 对五个顶级工作区给出唯一 purpose、route、权威 endpoint 和 provenance policy；
- MFT v1 snapshot/delta 运行时校验 fail-closed，不只依赖 TypeScript 静态类型；
- Task 37 v1 以兼容加法暴露精确 `payload_hash_input`（解决 JSON 解析丢失 `1.0` 浮点
  类型标签的问题）；reducer 验证 schema、from_cursor、连续 sequence、prev_hash、
  payload_hash、event_hash、terminal cursor 和 safety；不信任服务端未校验字段；
- gap、时间倒退、未知 section、未知 schema、缺 freshness/watermark/provenance 均返回
  `RESNAPSHOT_REQUIRED`，不部分应用；
- REAL_API 失败不返回 mock，MOCK 模式显式带 `SYNTHETIC` 标签。

## Task 41：实现 snapshot→delta 实时同步与断线补洞

**Files**

- Create: `apps/research-console/src/workbench/mft-sync.ts`
- Create: `apps/research-console/src/workbench/useMftReadModel.ts`
- Create: `apps/research-console/src/workbench/__tests__/mft-sync.test.ts`
- Create: `apps/research-console/src/workbench/__tests__/useMftReadModel.test.tsx`
- Modify: `services/console/api/live_stream_views.py`
- Test: `tests/research/test_mft_execution_console_contract.py`

**Acceptance**

- 首次只接受完整 snapshot；之后从该 cursor 分页拉 delta，直到 `has_more=false`；
- SSE 只通知只读 MFT watermark 变化，REST delta 仍是补洞权威；重连携带 cursor；
- 若加入 WebSocket，只能投递 Task 37 已签入 read model 的 market/OMS/fill 事件，不能连
  adapter，也不能承载命令；
- 断流、乱序、重复、429、timeout、abort、cursor gap、SSE event-id 不匹配均有确定状态；
- 补洞失败后停止展示“实时”，重新获取完整 snapshot；连续失败保留显式 unavailable。

## Task 42：替换应用外壳、全局状态条和五对象导航

**Files**

- Modify: `apps/research-console/src/app/routes.tsx`
- Modify: `apps/research-console/src/ui/AppShell.tsx`
- Create: `apps/research-console/src/workbench/GlobalStatusBar.tsx`
- Create: `apps/research-console/src/workbench/GlobalDrawerHost.tsx`
- Test: `apps/research-console/src/__tests__/app.integration.test.tsx`
- Test: `apps/research-console/src/ui/workbench-shell.test.tsx`

**Acceptance**

- 顶级导航仅保留五个对象工作区；详情页仍可路由但不重复成为顶级目的地；
- 全局状态条常驻显示 L2/Paper/Testnet 环境、venue/account、freshness、watermark、
  kill-switch、mandate、告警和强身份状态；未知值显式 `UNKNOWN`；
- 治理、审计、健康和任务改为全局抽屉；键盘和命令面板可达；
- 深色/浅色、窄屏和宽屏都不遮挡关键安全状态。

## Task 43：首页 / Market Monitor

**Files**

- Create: `apps/research-console/src/pages/workbench/MarketMonitor.tsx`
- Test: `apps/research-console/src/pages/workbench/__tests__/market-monitor.test.tsx`

**Acceptance**

- 展示市场连续性、scheduler、全局收益风险、数据 freshness、告警、事件带和下一次调仓；
- 每个关键字段可查看 source、snapshot id、as-of 和 freshness；
- MFT 不可用、陈旧或缺 section 时只显示诊断，不生成交易建议或伪造数字；
- 相同内容不在抽屉或其他顶级工作区完整复制。

## Task 44：Alpha Control Plane

**Files**

- Create: `apps/research-console/src/pages/workbench/AlphaControlPlane.tsx`
- Test: `apps/research-console/src/pages/workbench/__tests__/alpha-control-plane.test.tsx`

**Acceptance**

- 聚合候选 blotter、batch funnel、血缘、家族、IC/OOS、生命周期、租约/自动任务和 evidence
  drill-down；
- 候选状态来自 Wave 1 authority，缺 Trial Ledger/forward store 时 fail-closed；
- “validated”只表示研究验证，绝不显示为真钱批准；
- 大列表支持筛选、键盘定位和虚拟化或有界分页。

## Task 45：Validation Evidence

**Files**

- Create: `apps/research-console/src/pages/workbench/ValidationEvidence.tsx`
- Test: `apps/research-console/src/pages/workbench/__tests__/validation-evidence.test.tsx`

**Acceptance**

- 统一呈现 Trial Ledger、分层验证 DAG、回测、成本/容量、反证矩阵和 Shadow/Paper 现实差距；
- PASS/BLOCK/UNKNOWN 不以颜色单独表达；证据摘要与输入/commit/schema 绑定可见；
- 陈旧、缺失、n mismatch、自报 positive 或 malformed evidence 显式 BLOCK/UNAVAILABLE；
- 聚合页只给摘要和 drill-down，不复制完整旧页面。

## Task 46：Portfolio & Risk

**Files**

- Create: `apps/research-console/src/pages/workbench/PortfolioRisk.tsx`
- Test: `apps/research-console/src/pages/workbench/__tests__/portfolio-risk.test.tsx`

**Acceptance**

- 展示 sleeves、目标/实际仓位、风险预算、敞口、归因、漂移、衰退、对账和应急状态；
- research target 与 MFT actual 明确分栏，不在缺 actual 时复用 target；
- kill-switch、mandate expiry、reconciliation divergence 和 market gap 永久可见；
- 所有风险值只读，不提供浏览器内直接编辑或 reset。

## Task 47：Execution & Orders

**Files**

- Create: `apps/research-console/src/pages/workbench/ExecutionOrders.tsx`
- Test: `apps/research-console/src/pages/workbench/__tests__/execution-orders.test.tsx`

**Acceptance**

- 仅消费 MFT v1，展示 environment、intent funnel、OMS、fills、positions、TCA、reconciliation、
  mandate expiry 和待审队列；
- 订单/成交事件按 authority sequence 显示，重复 event id 不造成重复经济效果；
- ACK UNKNOWN、部分成交/撤单竞态、账本失败、陈旧数据和 gap 都有显式恢复提示；
- L2 下无 submit/cancel/reset/approve 直连按钮。

## Task 48：收敛全局抽屉、路由和页面清单

**Files**

- Create: `apps/research-console/src/workbench/drawers/GovernanceDrawer.tsx`
- Create: `apps/research-console/src/workbench/drawers/AuditDrawer.tsx`
- Create: `apps/research-console/src/workbench/drawers/HealthDrawer.tsx`
- Create: `apps/research-console/src/workbench/drawers/TasksDrawer.tsx`
- Modify: `apps/research-console/src/app/routes.tsx`
- Modify/Delete: 已被五对象工作区完整吸收的旧页面与对应测试
- Test: `apps/research-console/src/pages/__tests__/pages.smoke.test.tsx`
- Test: `apps/research-console/src/pages/__tests__/workflow-boundaries.test.tsx`

**Acceptance**

- manifest 中 purpose 和权威数据源唯一；聚合页不复制完整子页面；
- Getting Started、重复研究结论、陈旧 redirect、合成蓝图和不可达页面删除或归档为文档；
- 旧书签只保留语义明确、无安全歧义的 redirect；
- 真实模式构建产物不导入 `mock-data`，页面没有静默 fallback。

## Task 49：强身份命令预览、批准和一次性回执

**Files**

- Modify: `services/promotion_api/main.py`
- Modify: `apps/research-console/src/lib/api/promotion-api.ts`
- Create: `apps/research-console/src/workbench/commands.ts`
- Create: `apps/research-console/src/workbench/CommandReviewPanel.tsx`
- Test: `tests/governance/test_promotion_api_endpoints.py`
- Test: `tests/governance/test_promotion_api_boundary.py`
- Test: `apps/research-console/src/workbench/__tests__/commands.test.ts`

**Acceptance**

- 每个命令包含服务端验证的 OIDC subject/RBAC、随机且单次的 idempotency key、目标对象版本、
  非空业务理由和预期影响预览；
- 高影响操作遵循“预览→风险差异→多方批准→一次性执行→权威回查→审计回执”；
- 重放相同 key 返回同一 receipt，不重复副作用；key 内容冲突 fail-closed；目标版本变化拒绝；
- 外部 IdP/mTLS 未配置、身份弱、token 到期、角色不足或任一审计写入失败时命令不可执行；
- 本任务只覆盖研究晋级/治理命令；订单提交、资本扩大、风险限额修改和 kill-switch reset 不进入前端。

## Task 50：浏览器端到端、可访问性与 Wave 3 全量验收

**Files**

- Create: `apps/research-console/e2e/professional-workbench.spec.ts`
- Generate: `evidence/workbench_wave3_acceptance.json`
- Modify: `docs/PROJECT_STATE.md`

**Acceptance**

- 实际启动前后端，以真实只读 fixtures 验证五工作区、全局抽屉、snapshot→delta、断线补洞、
  resnapshot、命令预览、多人批准和一次性回执；
- 使用键盘和可访问性扫描验证关键路径；深色/浅色、窄屏/宽屏有视觉证据；
- 注入 unknown schema、missing field、stale、gap、429、timeout、SSE/WS 断流和空结果，全部
  显式失败且无 mock；
- 前端 typecheck、eslint、Vitest、build、Playwright、治理扫描和
  `py -3.14 scripts/validate.py` 全绿；
- evidence 绑定同一 canonical run、输入摘要和 commit；readiness 仍为 `L2_DRY_RUN_ONLY`；
- 若外部强身份基础设施不可用，命令实际执行验收必须标 `EXTERNAL_IDENTITY_PENDING`，不得伪造
  APPROVED；只读工作台验收可独立通过。

---

## 每个任务的固定验收顺序

1. 单个规格审查：用途、数据源、时序、失败语义和安全边界；
2. 先写失败测试；
3. 最小实现；
4. 聚焦回归；
5. 单个质量审查：运行时校验、断线恢复、键盘/可访问性、敏感信息和权限边界；
6. 治理扫描；
7. 实际浏览器运行验收（有可视变化的任务）；
8. 独立提交。

## Wave 3 退出条件

- Tasks 40–50 独立提交并通过 canonical gate；
- 五对象工作区成为唯一顶级信息架构，治理/审计/健康/任务只作为全局抽屉；
- snapshot + delta 从零重建与权威状态一致，断线补洞失败显式 resnapshot；
- 真实模式无隐式 mock，每个关键字段可见来源、时点和 freshness；
- 所有实际可执行命令满足强身份、RBAC、幂等、目标版本、预览和审计回执；不满足时零命令可执行；
- Task 39 若未完成继续诚实保持 `OPERATIONAL_SOAK_PENDING`，Wave 3 不改变该状态；
- 五个真钱安全旗帜不变，L2 不获得任何订单提交、资本扩大或风险门绕过能力。
