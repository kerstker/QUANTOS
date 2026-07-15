# QUANTOS 差距矩阵(GAP_MATRIX)

> 审计日期:2026-07-06 · 方法:15 维度多智能体审计(18 agent 深读源码 + 8 组 P0 红队 + 主审第一手核实)
> 事实基线:1329 个 `.py`、241 个测试文件、`python scripts/validate.py` = **1564 passed / exit 0**、
> 治理扫描器 223 文件 **0 违规**、`services/`+`quantos/` 内 **0 个券商/网络 SDK import**。
> 就绪度:**L2_DRY_RUN_ONLY**(仅研究 + 干跑 + 交易所 testnet/demo 沙盒,零真实资本)。
>
> **2026-07-14 增量说明**:本表仍是 2026-07-06 的 15 维审计快照；当前顶级能力机器真源是
> `evidence/top_tier_assessment.json`(`NOT_TOP_TIER`,15/22 PASS)。备份要求已改用运营证据 checker，
> 不据此重打其余历史分数。

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
| 2 | 数据层 | PIT/宇宙/复权/日历/泄漏检测 + strict/atomic/cadence + content-addressed raw archive;perp mark 定价;前向 quality/lineage 双门 fail-closed;完整准入 evidence 与 redacted event 同事务;显式 volume unit/semantics | **4** | `services/data/{adapters,quality,file_ingest,persistent_store,panel_quality,raw_archive,refresh_history,admission_store}.py`、`tools/{live_data_refresh,data_quality_admission,raw_data_lineage_backfill,data_refresh_slo_evidence}.py` | mark/OI 最近窗口 lineage PASS；funding quality/lineage/SLO BLOCK；仅 2/30 轮历史；历史 backfill、外部 WORM/异地副本、流式接入及 FX/宏观/股票等价准入契约仍缺 | P1/LATENT | P1 |
| 3 | Alpha Factory | 真 DSL + 确定性生成器 + fail-closed 证据门 + 10 态审计生命周期；AlphaAsset sqlite/育种 lineage；周度维护 sweep | **4** | `alpha_expression.py`、`evaluator.py`、`asset_store.py`、`maintenance.py`、`tools/alpha_maintenance_sweep.py` | 自动衰退已联动停止新增；实际减仓比例/退休全平待人工授权；GP/evolutionary 仍缺统一 asset 挂载点 | P1/PARTIAL | P2 |
| 4 | 回测引擎 | 事件驱动 + 向量化 + 成本模型 + PnL 对账恒等式;确定性可复现(无 RNG/墙钟只在边界) | **4** | `execution/dry_run_pipeline.py`、`experiment_registry/store.py:73`、`evaluator.py:166` | 无撮合/限价簿模拟;容量系数未校准;冲击 stub | P2/LATENT | P2 |
| 5 | Validation Gate | 真 DSR/PBO/CPCV/walk-forward/泄漏检测,fail-closed;driver + `submit_live_batch` 双层必填 attestation | **4** | `validation/gate.py:51`、`live/governance/validation_attestation.py`、`live/pipeline.py` | 结构门已闭合；PASS hash 尚未解析到不可变 ValidationResult，override 身份/签名仍依赖外部权威 | P0/P1 EXTERNAL | P1 |
| 6 | 组合层 | 真约束优化器 + 风险平衡 + 多因子风险模型 + firm 硬门 + crowd_contra 签署 z-score 拥挤门 | **4** | `constrained_optimizer.py:136`、`factor_risk.py:35`、`firm_gate.py`、`crowding_calibration.py` | 单策略下 sleeve 优化恒等；多策略子限额/自动 de-risk 待授权；通用拥挤/退出天数默认值缺生产微观结构校准 | P1/PARTIAL | P1 |
| 7 | 交易边界 | 全库最成熟安全维度;双状态机 + 非人不可达 APPROVED + 多方三人批准 + 分级 mandate | **4** | `alpha.py:186`、`guardrails.py:36`、`approval.py:137`、`mandate.py:45` | Phase 14 进程隔离未实现;promotion_api 自报标签;mandate 只算当批 | P0/LATENT | **P1** |
| 8 | Pre-Trade Risk Gate | 真 fail-closed 门(24 检查,异常必拒、审计失败必拒、永不 error-approve)+ 闩锁 kill-switch；live ADV/波动/去重/限频/撤单率承重 | **4** | `gate.py:56-92`、`kill_switch.py:41`、`checks.py`、`gate_context.py` | P1-5 代码闭合；参与率/波动/撤单率仍是 L2 工程基线，缺授权生产微观结构样本校准 | P2/EVIDENCE | **P2** |
| 9 | OMS/EMS/Gateway | 真 testnet 执行栈(确认轮询/撤单/超时/崩溃恢复/双所);订单身份含前仓，账本冲突显式拒绝；pre-journal fail-closed | **3** | `live/pipeline.py`、`live/execution/{filters,position_ledger}.py`、`resolve_unconfirmed` | P1-3/P1-4 已闭合；仍为 SQLite 单写者，无主备/FIX/drop-copy | P1/LATENT | **P1** |
| 10 | 交易中/后风控 | risk_monitor + reconciliation(drift 硬停)+ parity + TCA + sentinel + GoNoGo | **3** | `reconciliation.py:41`、`risk_monitor.py:84`、`live/execution/parity.py` | 无实时盘中监控(周/小时级);无保证金细粒度监控;周度对账 | P1/LATENT | P1 |
| 11 | Kill Switch/降级/恢复 | 闩锁 + 跨进程持久化 + 峰值权益历史上界验证 + human-only 三方复位 + fail-closed | **4** | `kill_switch.py:41-49`、`kill_switch_store.py:39`、`equity_state.py`、`emergency.py:47` | P2-3 孤立坏峰值误全平已闭合；上游权益异常、现金流/account identity 调整 high-water mark 与品种/账户级细分开关仍缺 | P2/PARTIAL | P2 |
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
- firm 跨 venue 敞口已有独立硬门；残留是越限自动按比例减仓(dim6)
- 衰退/退休维护已有周度调度、paper 指标和停止新增联动；残留是减仓比例/全平授权(dim3/dim6)
- AlphaAsset sqlite + cs_genome 育种 lineage 已接；GP/evolutionary 统一挂载点仍缺(dim3)
- 现实主义冲击/容量引擎真实但**系数 UNCALIBRATED**(dim4/dim5)
- 实盘 ADV/波动/重复/频率/撤单率已承重；阈值仍是 L2 工程基线、待授权生产样本校准(dim8)
- P2-2 已更正并闭合:数据库 quote USD 单位一致，错误来自容量消费端二次乘价；极差现只作 dispersion guard(dim2)

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

1. **已完成本地结构闭合 — 验证 attestation 是 driver 与 `submit_live_batch` 双层必填前置**(dim5)；剩余是外部身份/签名与不可变 ValidationResult 解析，不以本地自签关闭。
2. **已完成 — mandate 账户级敞口聚合 + 多策略硬停**(dim7/dim8)。
3. **已完成 — client_order_id 前仓身份 + 账本 compare-on-conflict**(dim9)：同 delta 不同状态不碰撞，未知碰撞整批 venue 前拒绝。
4. **已完成 — place-后孤儿窗口**(dim9):pre-journal fail-closed，place 后写失败进入恢复对账。
5. **已完成 — live 历史指纹/时间戳 + 撤单率**(dim8):24 个检查承重，阈值校准另列外部证据。
6. **已完成机制 — firm 级敞口硬门**(dim6):跨 venue 越限阻断新增；自动减仓比例待人工授权。
7. **P2 — 数据质量 + raw archive 双门与刷新 SLO 机制已承重;P2-2 volume 语义/双乘已闭合；funding lineage/SLO、30 轮运营历史和 off-host archive 仍 open**(dim2)。
8. **已完成当前价路径 — P2-6 同点跨源核价**(dim8/dim9):venue 盘口与 CoinGecko 均携带源时间戳，缺失/陈旧/错时/偏离逐标的冻结；7 天信号新鲜度与阈值校准仍 open。
9. **已完成单机可靠半区 — P2-5 持久事件链**(dim10/dim15):六条同库 outbox、统一 recovery drain、authority immutable preflight 与 L2-only hourly ops 已承重；三份 legacy owner DB 已 staged migration，5 条 refresh events 已 publish/ack/project，pending/DLQ=0。当前三个启用 owner PASS、三个未启用 authority UNAVAILABLE。独立 scheduler watchdog 已承重；L2 backup 有 online snapshot/加密 manifest/隔离 restore。三链 commitment + extension verifier、create-once external anchor seam 和 HTTPS heartbeat sender 已实现并有真实 authority 诊断，但当前 backup/heartbeat/seal 配置均缺，现场 6 PASS/1 PARTIAL/3 UNAVAILABLE/0 BREACH。Catalog 仍为 11 types/16 definitions/18 bindings。更广 producer、真实 external backup、missed-heartbeat receipt、WORM/object lock、外部签名、跨主机 HA 与强 worker identity 仍 open。
10. **P0(放行日转正)— mandate 完整性 + Phase 14 出进程签名/真身份认证**(dim7/dim13):这是真钱与自签限额之间的唯一门,必须在接真实 venue 前升级为密码学保护。
