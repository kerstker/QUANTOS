# QUANTOS 差距矩阵(GAP_MATRIX)

> 审计日期:2026-07-06 · 方法:15 维度多智能体审计(18 agent 深读源码 + 8 组 P0 红队 + 主审第一手核实)
> 事实基线:1329 个 `.py`、241 个测试文件、`python scripts/validate.py` = **1564 passed / exit 0**、
> 治理扫描器 223 文件 **0 违规**、`services/`+`quantos/` 内 **0 个券商/网络 SDK import**。
> 就绪度:**L2_DRY_RUN_ONLY**(仅研究 + 干跑 + 交易所 testnet/demo 沙盒,零真实资本)。

## 评分标准

| 分 | 含义 |
|---|---|
| 0 | 不存在 |
| 1 | 只有文档/概念 |
| 2 | 有 mock/demo,不可生产 |
| 3 | 有基础实现,不完整或缺关键接线/测试 |
| 4 | 工程化较完整,可受控进入模拟盘/小资金受控实盘 |
| 5 | 生产级:强风控 + 审计 + 回放 + 测试 + 降级 + 运维 |

**风险等级**:P0=可致真实资金损失/订单失控/风控绕过/不可恢复;P1=阻塞进入模拟盘/实盘;P2=质量/可维护/扩展;P3=体验/展示/文档。
**ACTIVE**=当前即可被触发/当前即错;**LATENT**=现被 L2/沙盒/人工门挡住,Phase 3A 放行当天转 ACTIVE。

## 15 维度评分总表

| # | 维度 | 当前状态 | 分 | 主要证据(file:line) | 核心缺口 | 最高风险 | 优先级 |
|---|---|---|:-:|---|---|:-:|:-:|
| 1 | 总体架构 | 真契约领域模型 + fail-closed 环境隔离 + live/ 单向边界;但无事件总线、无服务注册表、包级循环依赖、god-module | **3** | `quantos/contracts/config.py:17`、`deployment/gate.py:43`、`console_api/main.py:5288`(5288 行) | 无运行时事件总线/服务发现;research↔alpha 循环依赖;99 空 DDD 目录 | P2/LATENT | P2 |
| 2 | 数据层 | PIT/宇宙/复权/日历/泄漏检测生产级;但质量门 computed-not-enforced、乱序死检查、无实时 | **4** | `data/pit_store.py:32`、`persistent_store.py:41`、`file_ingest.py:66`(乱序死检查) | 质量不阻断接入;`check_volume_units` 未接线;无流式接入 | P1/LATENT | P1 |
| 3 | Alpha Factory | 全库最成熟;真 DSL + 确定性生成器 + fail-closed 证据门 + 10 态审计生命周期 | **4** | `alpha_expression.py:329`、`evaluator.py:140`、`alpha.py:186` | lineage 未接线;富 AlphaAsset 无持久层;衰退→退休无调度器 | P1/LATENT | P2 |
| 4 | 回测引擎 | 事件驱动 + 向量化 + 成本模型 + PnL 对账恒等式;确定性可复现(无 RNG/墙钟只在边界) | **4** | `execution/dry_run_pipeline.py`、`experiment_registry/store.py:73`、`evaluator.py:166` | 无撮合/限价簿模拟;容量系数未校准;冲击 stub | P2/LATENT | P2 |
| 5 | Validation Gate | 真 DSR/PBO/CPCV/walk-forward/泄漏检测,fail-closed,自主路径接成 chokepoint | **4** | `validation/gate.py:51`、`deflated_sharpe.py:41`、`cpcv.py:86` | **验证门不是 live 部署的结构性前置**;是 metrics arbiter | P1/LATENT | **P1** |
| 6 | 组合层 | 真约束优化器 + 风险平衡 + 多因子风险模型(真 PSD 校验)+ 三套归因 | **4** | `constrained_optimizer.py:136`、`factor_risk.py:35`、`firm_aggregation.py:8` | 优化器不在实盘 sizing 路径;firm 聚合只诊断不阻断;多 alpha 失效无自动 de-risk | P1/LATENT | P1 |
| 7 | 交易边界 | 全库最成熟安全维度;双状态机 + 非人不可达 APPROVED + 多方三人批准 + 分级 mandate | **4** | `alpha.py:186`、`guardrails.py:36`、`approval.py:137`、`mandate.py:45` | Phase 14 进程隔离未实现;promotion_api 自报标签;mandate 只算当批 | P0/LATENT | **P1** |
| 8 | Pre-Trade Risk Gate | 真 fail-closed 门(23 检查,异常必拒、审计失败必拒、永不 error-approve)+ 闩锁 kill-switch | **4** | `gate.py:56-92`、`kill_switch.py:41`、`checks.py:207` | 流动性/波动/重复/频率引擎在 live ctx 被占位符架空;无撤单率检查 | P1/LATENT | **P1** |
| 9 | OMS/EMS/Gateway | 真 testnet 执行栈(确认轮询/撤单/超时/崩溃恢复/双所);工程良好的原型 | **3** | `live/pipeline.py:349`、`pipeline.py:104`(不信 ACK)、`resolve_unconfirmed` | client_order_id 同数量碰撞;place-后-未落库孤儿窗口;无主备/FIX | P1/LATENT | **P1** |
| 10 | 交易中/后风控 | risk_monitor + reconciliation(drift 硬停)+ parity + TCA + sentinel + GoNoGo | **3** | `reconciliation.py:41`、`risk_monitor.py:84`、`live/execution/parity.py` | 无实时盘中监控(周/小时级);无保证金细粒度监控;周度对账 | P1/LATENT | P1 |
| 11 | Kill Switch/降级/恢复 | 闩锁 + 跨进程持久化 + 只加不减防竞态 + human-only 三方复位 + fail-closed | **4** | `kill_switch.py:41-49`、`kill_switch_store.py:39`、`emergency.py:47` | 紧急全平依赖峰值权益持久化正确;无品种/账户级细分开关 | P2/LATENT | P2 |
| 12 | 审计/回放/事件溯源 | 哈希链 GlobalAuditLog + DurableAuditLog + append-only 账本 + 不可变实验制品 | **3** | `monitoring/audit_log.py:26`、`experiment_registry/registry.py:112`、`position_ledger.py:30` | 无统一事件总线;无跨服务 correlation/causation ID;无事件级回放 | P2/LATENT | P2 |
| 13 | AI Agent/治理 | guardrail 非人研究 loop;结构性到不了 APPROVED;能力白名单;逐候选错误隔离 | **4** | `agent_loop.py:34`、`guardrails.py:36`、`alpha.py:192` | promotion_api 把绕过面变 runtime;mandate 可被 agent 改(无签名) | P0/LATENT | **P1** |
| 14 | 前端/控制台 | 真只读 console(42 GET 端点)+ MOCK_MODE 标识 + 危险按钮经治理 CLI 子进程 | **4** | `console_api/main.py`(0 变更端点)、`routes.tsx`、`live_ops_api.py:73` | 真/合成同端点混杂;无统一 auth/RBAC(per-service token) | P3/ACTIVE | P3 |
| 15 | DevOps/MLOps/安全/测试 | CI(validate.yml on push/PR)+ 治理扫描器 + git hooks + 密钥 gitignore | **3** | `.github/workflows/validate.yml`、`scripts/validate.py`、`.gitignore:51` | **无 Docker/容器化**;无 staging/prod 隔离基建;无监控告警/灾备基建;~~门禁遗漏 tests/live~~(本轮已修) | P2/LATENT | P2 |

> **维度 15 修正(本轮审计发现并修复)**:`scripts/validate.py`(CI 门禁)此前硬编码测试目录、**从不运行
> `tests/live`(36 文件,live 执行栈)**——最安全关键的下单代码不在 CI 里。已作为独立子进程纳入(+527 测试,
> 门禁总覆盖 2094)。另发现 `tests/reproducibility` 有预存在证据摘要漂移,交还用户(见 RISK_REGISTER P1-10)。

**均分 ≈ 3.67**。清晰的两极分布:**研究 + 安全治理半区(维度 2/3/5/6/7/8/11/13/14)= 近机构级 4 分;执行 + 运维基建半区(维度 1/9/10/12/15)= 工程化原型 3 分。**

## 已实现 / 部分实现 / 未实现

### 生产级、真实、有测试(REAL — 直接可信)
- 统一契约领域模型 `quantos/contracts/`(17 个 frozen pydantic,单一事实来源)
- 环境/就绪度隔离(SafetyConfig 五旗帜 + deployment gate 不可覆盖 L2 pin,LIVE 结构性挡死)
- PIT 双时态存储 + 无幸存者偏差宇宙 + PIT 复权 + 交易日历对账 + 泄漏检测器(148 测试)
- 截面因子 DSL 引擎 + 确定性生成器(cs_genome/gp_tree)+ 多重检验(DSR/PBO/CPCV/BH-BHY-FDR)+ 10 态审计生命周期(334 测试)
- 约束组合优化器(真 PSD 校验)+ 风险平衡 + 三套归因(160 测试)
- PreTradeRiskGate(23 检查 fail-closed)+ 闩锁 KillSwitch(跨进程持久化)
- 多方三人批准状态机(哈希链)+ 晋级执行核(先烧 grant 后推进)
- 干跑流水线(gate→纸面成交→对账恒等式→审计,残差≈0)
- live/ testnet 执行栈(确认轮询/撤单/崩溃恢复/双所,真实联调过)
- 治理扫描器 + 边界测试(0 券商 SDK、单向依赖、mainnet 不可实例化)

### 部分实现 / 已建已测但未接线 / 诊断非门禁(PARTIAL)
- 验证门禁真实但**未盖在 live 部署路径上**(dim5)
- 组合优化器真实但**不在实盘 sizing 关键路径**(dim6)
- firm 跨 venue 敞口聚合真实但**只诊断不阻断**(dim6)
- 衰退/退休/维护 sweep 已建已测但**无调度器、无自动指标喂入**(dim3/dim6)
- alpha lineage(`make_child_asset`)已测但**零非测试调用方**(dim3)
- 现实主义冲击/容量引擎真实但**系数 UNCALIBRATED**(dim4/dim5)
- 实盘流动性/波动/重复/频率引擎存在但**被占位符数据 + 无限阈值架空**(dim8)
- `check_volume_units` 单位守卫已测但**只接在一个研究诊断工具**(dim2)

### Mock / 合成(诚实标注,非伪造)
- console_api 缺失子系统回退 `math.sin/cos` 合成因子,一律打 `SYNTHETIC_DETERMINISTIC` 标(dim1/dim14)
- data-catalog 在 L2 用合成 bar 算目录哈希(诚实标注)

### 空壳(PLANNED,无码)
- `services/{gateway,learning}`(0 py 文件)
- 99 个空的 DDD 六边形脚手架叶目录(真实代码全在 flat 模块)
- `platform/`、`schemas/`、`proto/`、`config/`(声明,未落地)

## Mock 与 Production 的关键区别(本仓库的诚实性)

本仓库的可贵之处:**它没有用 mock 冒充生产能力**。缺失的子系统被**诚实标注**(`SYNTHETIC_DETERMINISTIC`、`UNCALIBRATED_PRINCIPLED`、`REQUIRES_HUMAN_REVIEW`、docstring 自认边界),而非伪造。真正的差距不是"假的当真的",而是三类:
1. **已建已测但未接线**(engine 存在但没盖在承重路径上)——这是最主要的差距形态。
2. **诊断非门禁**(IC 面板/现实主义/firm 聚合明确"不驱动晋级/下单")——设计上的克制,需在放行前决定哪些升级为门禁。
3. **Phase 14 结构性推迟**(出进程签名/进程隔离/真身份认证)——单进程内挡不住受信代码,全库反复诚实注脚,是明确推迟不是隐瞒。

## 优先级排序(放行小资金实盘前必须收口)

1. **P1 — 验证门禁作为 live 部署结构性前置**(dim5):live 驱动器 preflight 强制要求 PASS ValidationResult/APPROVED 或可审计人工 override。
2. **P1 — mandate 账户级敞口聚合 + 多策略硬停**(dim7/dim8):evaluate_mandate 按账户级 gross/net 投影;多策略共享未分块 mandate → 硬降 NEEDS_HUMAN_REVIEW。
3. **P1 — client_order_id 同数量碰撞修复**(dim9):加 per-batch 序号/nonce,消除静默记账吞没。
4. **P1 — place-后-未落库孤儿窗口**(dim9):place 前先写 pending 账本行,让崩溃恢复看得见。
5. **P1 — 实盘 ctx 回填历史指纹/时间戳 + 撤单率引擎**(dim8):让重复/频率引擎在 live 承重。
6. **P1 — firm 级敞口硬门**(dim6):跨 venue 净/毛/单名集中上限,越限阻断/减仓。
7. **P2 — 数据质量接入强制 + 乱序死检查修复 + volume 单位守卫接线**(dim2)。
8. **P0(放行日转正)— mandate 完整性 + Phase 14 出进程签名/真身份认证**(dim7/dim13):这是真钱与自签限额之间的唯一门,必须在接真实 venue 前升级为密码学保护。
