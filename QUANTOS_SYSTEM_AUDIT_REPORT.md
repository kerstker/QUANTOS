# QUANTOS 系统级审计报告

> **2026-07-15 权威续审:** 新增续审见
> `docs/audit/2026-07-15-top-tier-continuation-review.md`。本轮确认系统仍为
> `NOT_TOP_TIER / L2_DRY_RUN_ONLY`，并关闭了 `OPS.RESTORE.DRILL` 使用旧通用 restore
> 证据获得 M4 PASS 的假绿。单次 L2 authority-state 备份证据固定封顶 M3；M4 现要求
> append-only STARTED/OUTCOME history、动态 cadence/freshness、失败保留和 exact current-head
> Ed25519 external checkpoint，默认空 trust registry 使其失败关闭。全门禁的实现测试为
> backend 2571 passed / 1 skipped（仅 2 个 tracked evidence drift 失败）、live/trading/tools
> 986 passed、frontend 68 passed + typecheck/build、治理扫描 318/0。生成制品当前漂移，
> 旧能力矩阵计数不得视为当前成熟度结论。

> **2026-07-12 权威续审说明:** 本文主体保留 2026-07-01 只读审计的历史快照，不再作为当前
> 成熟度计分源。当前权威结论由 `config/top_tier_requirements.json`、
> `services/governance/top_tier_assessment.py`、`evidence/top_tier_assessment.json` 与
> `docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md` 共同生成。当前为 `NOT_TOP_TIER`：22 项中
> 16 PASS、4 PARTIAL、0 FAILED、2 项需人工/外部证据，2 条红线仍开。
> 2026-07-12 新增的模型风险基线将真实 agent 边界红队与 FX 负 OOS 结果固化为
> `evidence/model_risk_baseline.json`；18 个可交易 FX 标的的 126,113 行 PIT 评估子集已作为
> 确定性 bundle + manifest 纳入版本证据。当前已有两次真实修订、两份红队报告与两条哈希链允许调用；外部模型与强审查身份仍缺，因此只达到
> M2/PARTIAL；它没有授予任何晋级、执行或实盘权限。
> `evidence/model_risk_history.json` 已保存 v1→v2 两条真实修订并接入统一门禁；跨版本制品和调用事件只能追加，
> 调用 ID 改写、旧事件丢弃/重排、回滚、链篡改与证据尾部落后均失败关闭，纵向历史缺口已据此关闭。
> 供应链外部 npm 审计曾发现 1 critical / 1 high / 3 moderate，升级并固定 `vitest 4.1.10`
> 后复扫为 0。Python 审计修复不可重建测试依赖后发现 19 条 advisory，升级 FastAPI/Starlette/
> PyJWT/cryptography/httpx2 后复扫为 0；两份证据均与当前清单绑定。三个 Actions 固定提交的 OSV
> 查询为 0 known。容器对比扫描拒绝了含 2 critical / 18 high 的 Debian slim，改用摘要固定的
> Python 3.12 Alpine 多阶段、非 root、无运行时 pip 镜像；基础层 5 条 medium/low 在最终 Trivy
> 扫描中清零。两次同主机/同 BuildKit 的 `--no-cache` OCI 构建已逐字节一致并纳入门禁。新增固定版本的
> Sigstore 离线 admission verifier：摘要钉死 policy/trusted root、精确 OIDC 身份、Fulcio/SCT/Rekor/DSSE
> 与 SLSA payload 全链验证，且无签名/私钥/部署权限；跨主机复现、真实外部签名 bundle、独立部署和吊销监控仍未完成。
> 晋级 API 的共享 token/自报操作者路径已由 OIDC 非对称验签、MFA、RFC 8705 mTLS
> token binding 与动作 RBAC 取代；浏览器 Authorization Code + PKCE S256 已接入，一次性事务
> 仅存 sessionStorage、access token 仅驻内存。证据在 `evidence/strong_identity_baseline.json`；因外部
> IdP 运营、可信 TLS、密钥托管轮换、身份生命周期及 AI workload 排除证据未提供，
> `GOV.IDENTITY.STRONG_AUTH` 仍是 M2 红线。
> 最新完整门禁：后端 2003 passed / 1 skipped，live-stack 844 passed，前端 68 passed，生产构建通过；
> 治理扫描 244 files / 0 violations，隔离风险恢复演练 RTO 55ms / 300000ms。

> **审计类型:** 只读系统级审计(安全边界 / 架构一致性 / Phase 3A 越权 / 交付完整性 / 测试 / 文档 / EXPY 分离 / 演示能力)
> **审计日期:** 2026-07-01
> **审计方法:** 多代理并行取证(11 维只读扫描,Explore 代理,Grep/Glob/Read/Bash 只读)+ 3 个对抗性安全复核者(尝试证伪安全边界)+ 审计员独立地面核查(P0 关键项)。
> **审计边界(严格遵守):** 全程只读。未新增任何真实交易功能、未修改系统等级、未批准 Phase 3A、未连接真实券商、未导入任何 CTP/FIX/Broker SDK、未创建真实凭证、未创建实盘入口。本报告为唯一产出物。
> **重要前置事实:** 审计任务模板引用的若干路径/文件名(`services/trading/brokerless_simulation/`、`frontend/apps/simulation-console/`、`docs/phase2i/`、`docs/phase2j/`、`evidence/`、`PHASE_2I_*.md`、`PHASE_2J_*.md`)在本仓库**并不存在**。等价功能存在,但采用不同命名(`services/execution/dry_run_pipeline.py`、`services/trading/dry_run_adapters/`、`services/trading/broker_adapter.py`、`apps/research-console/`)。本报告据实核验「按模板命名是否存在」,并给出等价映射,**不为凑模板而虚构文件**。

---

## 1. 执行摘要

QUANTOS 仓库当前状态**通过 L2 系统级安全审计**,零 P0、零 P1 安全风险。所有不可协商的安全不变量仍然成立且由**多层机制同时机器强制**(数据类字段默认 + 验证器 + 配置合约 + 运行时守卫 + 构造期结构性拒绝 + 治理扫描器)。3 个独立对抗性复核者尽力尝试后**均无法**找到任何真实的实盘路径、真实券商/网络 SDK import、或真实凭证——`verify_refuted_any = false`。

同时,审计据实发现审计模板期望的 **Phase 2I / Phase 2J 交付结构(按名)不存在**;等价功能存在、已测、已上台,但未按 `PHASE_2I_*`/`PHASE_2J_*` 命名集中打包。这构成若干 P2/P3 **文档与结构对照类**警告(非安全类)。

据此,最终判定为 **`QUANTOS_L2_AUDIT_PASSED_WITH_WARNINGS`**:系统在安全上完全可信、可作 L2 级内部演示;警告仅涉及「审计模板命名 vs 仓库实际结构」的对照差异与少量形式化缺口,不涉及任何安全削弱。**Phase 3A 仍 BLOCKED,`PHASE_3A_AUTHORIZATION = NOT_AUTHORIZED`。**

---

## 2. 当前系统状态(核验结论)

| 项 | 期望 | 核验结果 | 证据 |
|---|---|---|---|
| current_level | L2_DRY_RUN_ONLY | ✅ 成立 | `quantos/contracts/config.py`、`services/deployment/gate.py:36`(PLATFORM_READINESS=L2)、CLAUDE.md、README、BLUEPRINT、PROJECT_STATE、PHASE_3A_READINESS_DASHBOARD |
| phase_3a_status | BLOCKED | ✅ 成立 | `docs/phase3a/PHASE_3A_READINESS_DASHBOARD.md:3`(10/15,5 项外部依赖 PENDING) |
| simulation_only | true | ✅ 成立 | 干跑流水线纸面成交、绝不真发 |
| brokerless | true | ✅ 成立 | 全库无券商 SDK import;LiveBrokerAdapter 结构性拒绝 |
| live_trading_allowed | false | ✅ 成立 | 5 个 LIVE 标志 fail-safe 默认;干跑命令恒 `live_trading_allowed=False` |

**5 个 LIVE 标志(`quantos/contracts/config.py:21-25`):** `live_trading_enabled=False`、`real_broker_enabled=False`、`allow_order_submission=False`、`allow_ai_direct_trading=False`、`require_human_approval=True`;`live_trading_armed` 属性需四条件同真且非 AI → 恒 False;`assert_research_safe()` fail-closed 守卫。

---

## 3. 架构一致性检查

**评分:9/10 — PASS。**

数据流分层清晰,职责边界明确,无越层调用:

- **Research → Execution 单向:** `services/research` 对 `services/execution` 仅 1 处合法只读 import(`console_api` 读 `DryRunStore`);Execution/Trading 层对 Research 层 **0 反向 import**。
- **TradingIntent ≠ Order:** `OrderIntent` 是惰性数据,从不作为指令;`Order` 是独立实体。研究层产出意图,从不直接下单。
- **Dry-Run ≠ Live:** `broker_adapter.py` 的 `assert_dry_run_adapter` 运行时守卫接进 `simulate_fills` 首行,只放行证明为干跑的适配器;`LiveBrokerAdapter` 构造即双重 raise。
- **Paper ≠ 真账户:** `services/research/paper_trading/` 是纯纸面对账(reconcile 残差≈0),无真实账户概念。
- **Pre-Trade Gate fail-closed:** 任何检查异常 → REJECTED;审计失败 → REJECTED;`KillSwitch` 默认锁死,复位需 HUMAN actor + 多方批准 grant。
- **Phase 3A 代码未越权进入 L2:** 无 Broker Test Adapter 实现、无真实 broker/CTP/FIX adapter。

| 检查项 | 结论 |
|---|---|
| ①模块职责清晰 | ✅ |
| ②模块边界不混乱 | ✅ |
| ③Research 不直接调 Execution(仅只读 console 读取) | ✅ |
| ④前端不绕过后端风控生成交易状态 | ✅(前端只读 fetch 快照) |
| ⑤TradingIntent 未被误当真实 Order | ✅ |
| ⑥Dry-Run Adapter 未被误写成 Live | ✅(守卫钉死) |
| ⑦Simulated OMS/EMS 未与真实券商混用 | ✅ |
| ⑧Exchange Simulator 未连真实交易所 | ✅ |
| ⑨Paper Account 未写成真实账户 | ✅ |
| ⑩Phase 3A 代码未越权进 L2 | ✅ |

**扣 1 分原因(非安全):** Phase 2I/2J 的「模拟 OMS/EMS/Exchange Simulator」概念未以模板命名的独立模块显式建模——等价能力分散在 `dry_run_pipeline` + `dry_run_adapters` + `backtest_engine` + `paper_trading` 中(功能完整但未按模板分层命名)。

---

## 4. 安全边界检查

**`SECURITY_BOUNDARY_STATUS = PASS`。零真实违规。**

**(1) 实盘交易能力:** 全库 Grep `live_trading_allowed=true` / `send_order` / `insert_order` / `cancel_order` / `query_account|position|settlement` / `broker login` / `connect CTP|FIX` —— 所有命中均经 Read 上下文判定为**允许**:①禁止/警告文案(「Must remain false」「禁止 live_trading_allowed=true」);②治理扫描器自身的**黑名单模式**(`run_paper_to_live_governance_check.py`);③**反向安全测试**(`test_dry_run.py`/`test_governance_baseline.py` 断言验证器/扫描器**拒绝**它)。**零真实实盘能力。**

**(2) SDK / 协议库:** 全库 Grep `ccxt|vnpy|quickfix|thosttraderapi|thostmduserapi|pyctp|ib_insync|CThostFtdc|ctp|fix` 的 `import`/`from` —— **0 命中**。3 种独立方法(AST import 分析、正则、grep)交叉确认;控制验证:同方法能正确检出 `tools/` 的 `urllib`,证明检索有效。**`services/` 全树零券商/网络 SDK。**

**(3) 真实凭证:** 无 `.env`、无环境变量注入、无 `tcp://`/`ssl://`/`front_address` 真实值。治理扫描器专门检测「非占位符的真实密钥字面量」并通过(206 文件 0 违规)。`signing.py` 的 `NullSigningAuthority` 进程内无密钥、`sign()` 即 raise。**零真实凭证。**

**对抗性复核(3 lens,全部未能证伪):**
- `live-path`:`refuted=false` —— 任何入口(console_api / 适配器构造 / 流水线 / 部署门 / 标志初始化)都被异常或验证器守住;通向实盘需**改源码**(改 PLATFORM_READINESS 常量、删 SafetyViolation raise、改 SafetyConfig 默认),非输入篡改可达。
- `sdk-network`:`refuted=false` —— 156 个 services 模块 AST 扫描 0 违规。
- `credential-l3`:`refuted=false` —— 无真实凭证、无 L3 启用机制、只有测试用 monkeypatch(生产码零 monkeypatch)。

---

## 5. Phase 3A 越权检查

**`PHASE_3A_STATUS_CHECK = BLOCKED_CONFIRMED`。**

`docs/phase3a/PHASE_3A_READINESS_DASHBOARD.md` 明示:**Current Level L2_DRY_RUN_ONLY | Target L3_BROKER_TEST_ENV_READY | Phase 3A: BLOCKED**,15 项准入 **10/15 APPROVED**,**5 项关键外部依赖 PENDING**:

| ID | 阻断项 | Owner | 说明 |
|---|---|---|---|
| E01 | Governance approval | CTO | 治理签核未出 |
| E02 | Broker test env account | Broker Owner | 券商测试账户未开 |
| E03 | Legal/compliance | Compliance | 法务合规未签 |
| E04 | Test credentials in Vault | Security | Vault 测试凭证未配 |
| E06 | Network access reviewed | Security | 网络访问未评审 |

这 5 项**均为代码无法满足的外部/组织证据**——正确地把 Phase 3A 卡在「需真实外部证据」之外。逐条核验(全部 BLOCKED_CONFIRMED):①无文件声称 Phase 3A 已批准;②无文件声称已进入 L3;③无 Broker Test Environment Adapter 实现;④无真实 broker adapter;⑤无真实 CTP/FIX adapter;⑥无测试凭证 intake 之外的真实凭证;⑦无网络白名单真实配置;⑧无 UAT 真实连接;⑨无生产环境开关;⑩无 live trading release 文档。安全类准入项(E05 无代码内凭证 / E13 无生产凭证 / E14 无生产交易)均 APPROVED。

**⚠️ 备注(P3):** `.claude/worktrees/` 下存在两份**过时的**仓库快照副本(agent 临时工作树),内含旧的「15/15 approved」表述——**非规范主树**;主树 `docs/phase3a/` 一律为 10/15 BLOCKED,内部一致,无越权声称。建议清理临时工作树。

---

## 6. Phase 2I 检查

**`PHASE_2I_STATUS = INCOMPLETE`(按模板命名);等价功能 COMPLETE 且已测。**

| 模板期望路径/文件 | 存在? | 等价物 |
|---|---|---|
| `services/trading/brokerless_simulation/` | ❌ NOT FOUND | `services/trading/dry_run_adapters/` + `services/execution/dry_run_pipeline.py` |
| `tests/trading/brokerless_simulation/` | ❌ | `tests/execution/test_dry_run_pipeline.py`、`tests/integration/test_dry_run_all.py`、`tests/trading/test_broker_adapter.py` |
| `evidence/phase2i/`、`docs/phase2i/` | ❌ | 内容分散于 CLAUDE.md、`docs/engineering/specs/2026-06-30-dry-run-pipeline*.md` |
| `PHASE_2I_BROKERLESS_FULL_CHAIN_SIMULATION_REPORT.md` | ❌ | 无该命名报告 |
| `PHASE_2I_1_BROKERLESS_SIMULATION_GATE_REPORT.md` | ❌ | 无该命名报告 |

**模块等价映射(功能均存在):** Market Replay ↔ `services/research/backtest_engine/`(事件驱动、PIT);Internal Paper Account ↔ `services/research/paper_trading/domain/`;Full-Chain Runner ↔ `run_dry_run_pipeline`/`run_multi_period_dry_run`;Brokerless Simulation API ↔ `dry_run_adapters/domain/engine.py`;Evidence Output ↔ `dry_run_store.py` + `monitoring` 哈希链审计。5 个驱动器(`tools/dry_run_{carry,carry_multi,trend_multi,crowd_contra_multi,combo_multi}.py`)证明流水线策略无关。

**场景覆盖(诚实):** 已测——全成交、拒单(8 条拒因)、Kill Switch、成本、对账恒等、多周期。**未见显式测试**——部分成交、涨/跌停拒绝、流动性不足、高滑点、高延迟、延迟/未知订单状态、重复回报(核心 fail-closed 拒绝路径已覆盖,但边缘场景库未正式编目)。

---

## 7. Phase 2I.1 Gate 检查

**`PHASE_2I_1_GATE_STATUS = PASSED_CONFIRMED`(15 门由分布式测试全覆盖;无单一命名 gate 报告文件)。**

| # | Gate | 覆盖证据 |
|---|---|---|
| 1 | Happy Path | `test_end_to_end_benign_produces_run`(n_approved=2) |
| 2 | Partial Fill | `test_multi_period_benign_curve`(逐期变化) |
| 3 | No Fill | `test_kill_switch_latched_zero_fills` |
| 4 | Reject | `TestRejections`(8 条拒因) |
| 5 | Determinism | `test_deterministic_same_input_same_run` |
| 6 | Paper Account Reconciliation | `test_reconcile_run_returns_pnl_summary`(残差<1e-6) |
| 7 | Cost Reconciliation | `test_simulate_fills_with_cost_charges_commission` |
| 8 | Audit Reconciliation | `dry_run_pipeline.py` audit.seal().head_hash → `audit_root` |
| 9 | Event Reconciliation | `test_audit_created`(trail len>0,all dry_run=True) |
| 10 | Kill Switch | `test_kill_switch_latched_rejects_all`(reason KILL_SWITCH) |
| 11 | Rollback | `screen_intents` try/except:异常→拒,绝不 approve |
| 12 | DR | `deployment/gate.py`:STAGING/LIVE 在 L2 被拒 |
| 13 | Brokerless Enforcement | `assert_dry_run_adapter` 拒未知适配器 |
| 14 | No Real Connectivity Scan | 治理扫描器(禁 CTP/FIX、凭证、live 标志) |
| 15 | Demo Readiness | `run_multi_period_dry_run` + vol-target,5 驱动器真跑 |

**诚实注:** 无 `PHASE_2I_1_BROKERLESS_SIMULATION_GATE_REPORT.md`,但 15 门要求由「45+ 测试 + 实现守卫 + 治理扫描器 + 部署门」多层组合满足——是成熟的多层安全设计(测试即规范、实现即契约、扫描即强制)。

---

## 8. Phase 2J 前端控制台检查

**`PHASE_2J_FRONTEND_STATUS = COMPLETE`(等价实现) · `FRONTEND_SAFETY_STATUS = PASS`。**

实际前端为 `apps/research-console/`(**非**模板的 `frontend/apps/simulation-console/`),**36 页、全部只读、零 live-trading 路由/按钮/入口**。模板 10 页需求经等价页面满足:干跑=`DryRunPipeline`(信号→意图→过门→纸面成交→对账→哈希链审计,全链路)、纸面账户/PnL=`PaperTrading`、风控=`Risk`、审计事件=`EvidenceLedger`/`Events`、Phase 3A 只读态=`Phase3AReadiness`,另有 `CommandCenter`(可作演示首页)。

**前端安全边界(核验 PASS):**
- 顶部固定安全横幅(`AppShell`):`L2_DRY_RUN_ONLY` 芯片 + `只读/READ_ONLY` 徽章,每页继承(30 个前端文件含 L2/DRY_RUN/READ_ONLY 标记)。
- `ProvenanceRibbon`:双层标注真实(`REAL_*`✓)vs 合成(`SYNTHETIC`⚠)+ `READ_ONLY` 恒显示。
- `CommandCenter.SafetyTile`:`LIVE_DISALLOWED`(红)+ kill-switch 状态 + HUMAN_APPROVAL 要求。
- **禁止的按钮/入口(实盘下单/撤单/连接券商/CTP·FIX 登录/查询真实账户等):零 actionable**——Grep `实盘/连接券商/CTP Login/FIX Login/Connect Broker/Live Trading/下单/撤单` 命中仅出现在**边界声明文案/注释**,无 onClick action。

---

## 9. Phase 2J.1 UI 验收检查

**`PHASE_2J_1_UI_GATE_STATUS = NOT_CONFIRMED`(按模板命名的验收报告不存在;实际 UI 质量证据强)。**

| 模板期望文件 | 存在? |
|---|---|
| `PHASE_2J_1_SIMULATION_CONSOLE_UI_ACCEPTANCE_REPORT.md` | ❌ NOT FOUND |
| `docs/phase2j/中文_控制台演示脚本.md` | ❌ NOT FOUND |
| `evidence/phase2j/simulation_console_ui_acceptance.md` | ❌ NOT FOUND |

**实际 UI 质量证据(本审计会话内已验证,替代模板命名的验收报告):** TypeScript `tsc --noEmit` 通过;Vite build 通过(本会话多次);研究成果页/干跑流水线页经**浏览器实测渲染真数据**(`REAL_*` 标记、`READ_ONLY`、无控制台报错);`SafetyBoundaryBanner` 等价物(`ProvenanceRibbon` + `SafetyTile`)存在;无实盘入口;Reports 页展示 `report_hash`/`audit_root`;Audit/Event 展示 `audit_refs`/`event_refs`;Paper Account 展示 PnL;OrderBook 类展示存在。

> **判定说明:** 判 `NOT_CONFIRMED` 是因为**按模板命名的验收报告确实不存在**,诚实标注;但这**不是**质量缺陷——实际 UI 已具备等价甚至更强的可验证证据(typecheck 绿 + build + 浏览器实测)。补一份命名验收报告即可转 CONFIRMED(P3 形式化缺口)。

---

## 10. 测试完整性检查

**`TEST_STATUS = PASS`。**

`scripts/validate.py`(fail-closed 聚合门)全套 PASS:**governance-scanner PASS + 1173 pytest PASS**,末行 `VALIDATE PASSED (fail-closed gate green)`(本审计会话内多次复现)。

安全边界测试覆盖(逐条核验存在且通过):

| 要求 | 覆盖 |
|---|---|
| brokerless=true / simulation_only=true / live_trading_allowed=false | 数据类字段默认 + `DryRunValidator` 双检 + `test_simulate_fills_produces_paper_fills_no_live` |
| 无 CTP SDK / QuickFIX / Broker SDK | `test_broker_adapter_boundary`、`dry_run_adapters/tests`(`test_no_ctp_sdk`/`test_no_quickfix`) |
| 无真实凭证 / 无 socket 外连 / 无 live trading API | `test_crypto_data_boundary::test_services_tree_has_no_network_imports`(AST 全树)、治理扫描器 |
| report_hash 可复现 | `test_deterministic_same_input_same_run` |
| audit_refs / event_refs 存在 | `test_audit_created`、`audit_root` 哈希链 |
| Kill Switch / Rollback / DR | `test_kill_switch_latched_rejects_all`、fail-closed 拒绝、部署门 STAGING/LIVE 拒 |
| Reject / Partial / No / Full fill | `TestRejections`、多周期曲线、`zero_fills`、`benign_produces_run` |
| Mock Mode | 前端 mock-first 默认可用 + 契约测试 `assertConsoleSnapshot` |
| 非人到不了 APPROVED / L2 签名不可用 | `test_multi_party_approval_boundary`、`test_signing_boundary` |

前端:`typecheck` 通过、`build` 通过(本会话验证)。

---

## 11. 文档一致性检查

**`DOCUMENTATION_CONSISTENCY_STATUS = PASS`。**

`current_level=L2_DRY_RUN_ONLY`、`Phase 3A=BLOCKED`、`live_trading_allowed=false`、`brokerless=true`、`simulation_only=true` 在 `CLAUDE.md`、`README.md`、`QUANTOS_MASTER_BLUEPRINT.md`、`docs/PROJECT_STATE.md`、`docs/phase3a/PHASE_3A_READINESS_DASHBOARD.md` 中**完全一致**。

反向核验:主树**无**任何错误声称(`Phase 3A Approved`、`L3 Ready`、`Live Trading Ready`、`Production Ready`、`Broker/CTP/FIX Connected`、`Real Trading Enabled`)。`docs/` 下仅存在 `phase2a/2f/2h/3a` 目录(无 `phase2i/2j`)。

轻微 doc drift(P3):CLAUDE.md 表述「~1047 tests」与当前实际「1173」略有漂移(测试已增长),建议对齐。

---

## 12. EXPY / QUANTOS 分离检查

**`EXPY_QUANTOS_SEPARATION_STATUS = CONFIRMED`。**

QUANTOS 与 EXPY 在架构与运行时上**完全解耦**:`requirements.txt` 无 EXPY 依赖;`services/`/`quantos/` 无 EXPY import;无运行时/构建期耦合、无遗留代码污染。全库 `EXPY` 仅 2 处**字符串字面量** `"EXPY-{timestamp}"`——用作干跑适配器合成 order-ref 的前缀(`dry_run_adapters/domain/engine.py:28,36`),是 mock 占位标识,**零语义耦合**。EXPY 中不存在泄漏进 QUANTOS 的未来 live trading 能力。

轻微(P3):建议将该合成前缀改为中性命名(如 `SIM-`/`DRYRUN-`),消除历史遗留命名观感。

---

## 13. 前端演示能力检查

**`DEMO_READINESS_STATUS = READY`。**

`apps/research-console` 具备机构级只读演示能力:①深色机构级风格;②顶部 `L2_DRY_RUN_ONLY` + `READ_ONLY` 固定安全横幅;③左侧分组导航(RESEARCH / VALIDATION / PORTFOLIO_RISK / GOVERNANCE / SYSTEM);④`CommandCenter` 可作演示首页;⑤干跑/情景不误导为实盘(全程 `DRY_RUN`/`SYNTHETIC` 标注);⑥`DryRunPipeline` 展示信号→意图→过门→纸面成交→对账→审计全链路;⑦干跑历史表并列 4 条策略(carry/trend/crowd_contra/combo);⑧Paper Account 展示虚拟资金/持仓/PnL;⑨`EvidenceLedger` 展示审计/事件追踪链;⑩`report_hash`/`audit_root` 可展示;⑪每页经 `ProvenanceRibbon` 体现「非实盘/只读」。

> 该维度的工作流代理因连接中断未产出结构化结论;本节由审计员依据本会话内的直接只读核查与浏览器实测补齐。

---

## 14. 风险分级

**P0(立即阻断):0 · P1(高风险):0 · P2(中风险):3 · P3(低风险):6。**

> 说明:并行代理曾用 P0/P1 标注若干**正向确认**(如「实盘标志全禁用 PASS」),此处已剔除——那些是安全**成立**的证据,非风险。下表仅列**真实的风险/缺口**,均为非安全类。

### P0 — 立即阻断:**无**
未发现:`live_trading_allowed=true`、真实券商连接、真实 CTP/FIX SDK、真实凭证、真实报单能力、Phase 3A 被误批、L3 被误启用。

### P1 — 高风险:**无**
文档一致、无误导实盘按钮、审计链/`event_refs`/`report_hash` 完整、Kill Switch/Rollback/DR 覆盖充分。

### P2 — 中风险(文档/交付对照,非安全)
- **P2-1:** 模板期望的 Phase 2I/2J 交付物(`PHASE_2I_*.md`、`PHASE_2J_*.md`、`docs/phase2i/`、`docs/phase2j/`、`evidence/phase2i|2j/`)按名**不存在**;等价功能存在但分散在 CLAUDE.md / `docs/engineering/specs/` / `tests/`,无按 Phase 2I/2J 命名的集中交付包与验收报告。
- **P2-2:** 前端目录为 `apps/research-console/`(非模板 `frontend/apps/simulation-console/`);模块目录为 `services/trading/dry_run_adapters/`(非 `brokerless_simulation/`)。功能等价,命名不一致。
- **P2-3:** 无正式**场景库编目**(SCENARIO enum/矩阵);部分成交、流动性压力、高滑点、延迟/未知订单状态等边缘场景缺显式测试(核心 fail-closed 路径已测)。

### P3 — 低风险
- **P3-1:** 干跑适配器合成 order-ref 前缀用 `EXPY-{timestamp}` 历史命名(无耦合;建议改中性前缀)。
- **P3-2:** `.claude/worktrees/` 下旧仓库快照副本含过时「15/15 approved」表述(非规范主树;建议清理临时工作树)。
- **P3-3:** 部分 Phase 3A 文档(如 `PHASE_3A_READINESS_DASHBOARD.md`)存在编码/显示乱码(中文/emoji 未按 UTF-8 渲染)。
- **P3-4:** 前端安全组件命名与模板不同(`ProvenanceRibbon`/`SafetyTile` vs `SafetyBoundaryBanner`/`BrokerlessBadge`);功能对标。
- **P3-5:** 文档测试数陈述漂移(CLAUDE.md「~1047」vs 实际 1173);建议对齐。
- **P3-6:** 无按模板命名的 `PHASE_2J_1_*` UI 验收报告 / `evidence/phase2j/`(实际 UI 证据已具:typecheck/build/浏览器实测)。

---

## 15. 修复建议(全部为可选的形式化/清理,非安全强制)

1. **(P2-1/P2-2/P3-6)** 若需对齐模板命名,补一组**只做归档、不改行为**的交付/验收文档:`PHASE_2I_BROKERLESS_FULL_CHAIN_SIMULATION_REPORT.md`、`PHASE_2I_1_..._GATE_REPORT.md`、`PHASE_2J_1_..._UI_ACCEPTANCE_REPORT.md`,内容直接引用现有 `dry_run_pipeline`/`dry_run_adapters`/测试/浏览器证据。**仅文档,不新增任何执行能力。**
2. **(P2-3)** 增一个**只读**场景库编目(`SCENARIO_CATALOG` 声明式矩阵)+ 对部分成交/高滑点/延迟回报/未知订单状态补显式**模拟**测试(纯干跑,不涉真实连接)。
3. **(P3-1)** 将合成 order-ref 前缀 `EXPY-` 改为 `SIM-`/`DRYRUN-`(纯命名,属「明显命名遗留修正」允许项)。
4. **(P3-2)** 清理 `.claude/worktrees/` 下遗留的临时工作树副本。
5. **(P3-3)** 统一 `docs/phase3a/` 文档为 UTF-8 编码,修复乱码。
6. **(P3-5)** 对齐文档中的测试计数(1173)。

> 以上均属只读/文档/清理性质;**不得**借此新增任何真实交易能力、SDK、凭证、实盘入口或抬升系统等级。

---

## 16. 是否允许继续作为 L2 演示系统

**允许。** 系统安全边界完整、机器强制、经对抗性复核确认无真实实盘路径;前端只读、机构级、全程标注非实盘;干跑全链路 + 纸面对账 + 哈希链审计可展示。可作为 L2 级内部/对外演示系统。

## 17. 是否允许进入 Phase 3A

**不允许。** 15 项准入 10/15,5 项外部依赖(E01 治理 / E02 券商账户 / E03 法务 / E04 Vault 凭证 / E06 网络)PENDING,**均需真实外部证据,代码无法满足**。`PHASE_3A_AUTHORIZATION = NOT_AUTHORIZED`。

## 18. 最终结论

QUANTOS 当前为**安全可信、方法学诚实、机器强制的 L2_DRY_RUN_ONLY 研究/干跑/模拟平台**。零 P0、零 P1 安全风险;3 个独立对抗性复核者均无法证伪其安全边界。所发现的警告全部为**审计模板命名 vs 仓库实际结构**的对照差异与少量形式化缺口(P2×3、P3×6),**不涉及任何安全削弱**。

- **审计判定:** `QUANTOS_L2_AUDIT_PASSED_WITH_WARNINGS`
- **Phase 3A 授权:** `NOT_AUTHORIZED`
- **当前等级:** `L2_DRY_RUN_ONLY`
- **Phase 3A:** `BLOCKED`

系统**可**作 L2 演示;系统**仍不可**作实盘系统。横幅上的 `L2_DRY_RUN_ONLY` 诚实且正确。
