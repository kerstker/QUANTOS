# QUANTOS 全面审查报告 — 是否够得上「顶级 AI 量化系统」

日期: 2026-06-23 · 方法: 88-agent 多维对抗式审查(ultracode)
范围: 后端 phases 0.5–14 + 前端接线 · 就绪级别: L2_DRY_RUN_ONLY

**审查方法**: 4 个清点员建立独立事实基线 → 8 维专家深审(读真实代码、跑只读检查、给 0–100 成熟度评分)→ 每条发现由 2 个独立怀疑者(存在性/复现 + 正确性/严重度两种视角)对抗验证 → 首席审计综合 + 完整性批判。全程只读,不改代码。结果: **31 条发现被双视角一致确认,6 条有分歧,0 条纯反驳**。

---

## 1. 总体结论

**综合评分: 76 / 100 — B 级。一个真正可靠、失败关闭的 L2 研究脚手架;运行时对 AI Agent 的遏制经实测密不透风。但它还不是顶级 AI 量化系统。**

核心安全不变量**成立且经实测验证**(不是只读测试):非人类 actor 无法到达 APPROVED、无法 APPROVE 或 RESET_KILL_SWITCH(均 HUMAN_ONLY)、永远无法 LIVE_TRADE 或 ACCESS_CREDENTIALS(FORBIDDEN_ALWAYS);Agent 允许动作集里根本没有 APPROVE 路径。pre-trade 网关是真正的 run-all-collect-all,外层 try/except 出错绝不返回 APPROVED。434 测试通过。

距顶级有三类、互相独立的差距(均已确认,均非实盘越界,L2 边界圈住了爆炸半径):
1. **构建期治理扫描器有真实盲区** —— 而运行时威胁模型恰恰把构建期表面托付给它。
2. **Alpha 工厂自动晋升路径比文档宣称的弱** —— Agent 实际走的那条路绕过了已存在的严格闸门。
3. **若干顶级能力缺失或仅占位**(真实数据湖、持久化/实验登记、事件驱动回测、因子风险模型) —— 对 L2 研究脚手架属预期,但要冲顶级必须补。

---

## 2. 分维度评分

| 维度 | 评分 | 等级 | 一句话 |
|---|---|---|---|
| 架构与工程质量 | 82 | A | 契约即单一真源、确定性、分层隔离扎实 |
| 安全与治理不变量 | 82 | B | 运行时 Agent 遏制实测密不透风;弱点在构建期扫描器,不在运行时控制 |
| pre-trade 风控层 | 82 | B | 全系统工程最佳的子系统;9 引擎研究风控网关明显更弱但只喂只读快照 |
| Alpha 工厂/量化正确性 | 68 | B | 统计内核真实可靠(IC 序列算 ICIR、美元中性净成本回测、DSR 失败关闭);但实际晋升路径骑着 PBO=0.0 默认值 |
| 回测/验证门/纸面对账 | 62 | C | 验证门数学严谨,但 Agent 走的工厂路径绕过了它 |
| 前端/API 诚实性 | 61 | C | 合成数字以 BACKEND_COMPUTED 呈现,缺 real-vs-synthetic 标注 |
| 数据护城河/防泄漏 | 58 | C | PIT 抽象正确,但泄漏检测器未接入晋升路径 |
| 对标顶级差距 | 18 | D | 无真实数据、无持久化、无事件驱动回测、无因子风险模型 |

---

## 3. 真实优势(均有证据/实测支撑)

- **运行时 Agent 路径遏制密不透风且有测试背书**: 直接执行 guardrails 验证 —— APPROVE/RESET_KILL_SWITCH 为 HUMAN_ONLY,LIVE_TRADE/ACCESS_CREDENTIALS 为 FORBIDDEN_ALWAYS,Agent 允许集无 APPROVE 路径(`services/intelligence/guardrails.py`);非人类 actor 无法到达 APPROVED(`quantos/contracts/alpha.py` 抛 UnauthorizedPromotion)。
- **全栈失败关闭**: pre-trade 网关外层 try/except 出错绝不 APPROVED、审计写失败即拒(`services/risk/pre_trade/gate.py:84-92`);kill-switch 状态损坏读作 tripped、重置绑定 HUMAN actor + 事件绑定批准(`kill_switch.py:29-65`);SafetyConfig 默认安全。
- **最难的量化坑处理正确**: Deflated Sharpe 对**全目录**试验数与**全目录** var_sr 去膨胀,无法诚实估计时**失败关闭**到人工复核(INSUFFICIENT_CATALOG_FOR_DSR),而非把它兜底成噪声 alpha(`services/alpha/evaluator.py:96-104`)。
- **ICIR/t/p 值来自真正的逐期截面 IC 序列**(`evaluator.py:63-67`),避开单截面 bug;覆盖过薄走人工复核(THIN_COVERAGE)。
- **全局防篡改审计链**用带外 seal 承诺检出截断/整体替换,不止裸哈希链(`services/monitoring/audit_log.py`);两个独立层拒绝未批准 LIVE 部署(gate + DeploymentRecord validator)。
- **威胁模型文档诚实精确**(CLAUDE.md、lifecycle.py),正确区分「对 actor 的运行时控制」与「构建期可信代码表面」—— 罕见且诚实。

---

## 4. 已确认的关键问题

> 全部为**已确认**(双怀疑者一致)。无一是实盘越界 —— L2 边界圈住了爆炸半径。按主题归类。

### 主题 A — Alpha 工厂晋升路径绕过了已存在的严格闸门(利润核心完整性,最重要)

文档宣称「过拟合/泄漏/单一 regime 的候选无法通过」。但在 `ResearchAgentLoop`/工厂**实际走的那条自动路径**上,这些防线被硬编码成「通过」:

- **[P0] 泄漏检测器从未接入晋升路径,`leakage_ok` 默认 True** — `services/alpha/evaluator.py:57,109`。未来偷窥的 alpha 仅凭未验证的默认值就能进 PAPER_TRADING。
- **[P1] PBO/CSCV 过拟合闸门被绕过(默认 0.0)** — `services/alpha/evaluator.py:60` + `services/alpha/factory.py:38`。工厂调 `evaluate()` 时不传 PBO,过拟合概率恒为 0,PBO 守卫(`guards.py:33` 仅 `pbo>0.5` 才拒)永不触发。**而一条已正确接好 PBO 的路径其实存在于 `services/research/validation/pipeline.py:92-94` —— 平台有两条晋升路径,Agent 用的是弱的那条。**
- **[P1] 泄漏 + 单一 regime 检查在工厂路径上「默认通过」** — `services/alpha/evaluator.py:57-58`。
- **[P1] IC p 值用正态近似而非 Student-t,在系统允许的小样本下高估显著性** — `services/alpha/metrics.py:48-50`。整个 BH-FDR 边缘检验建立在系统性偏小的 p 值上,FDR 控制在数据最薄、过拟合风险最高处反保守。
- **[P2] BH-FDR「第二独立透镜」与 DSR 不独立**(同信号/收益),对孤立 alpha 无保护;**[P2] walk_forward_passed 被伪造成「净 Sharpe>0」**,非真实样本外步进。

> 影响: 一个 DSR 过关的过拟合/泄漏 alpha 能在 Agent 自动路径上推进到 PAPER_TRADING,**零过拟合惩罚、零泄漏校验**。这不是实盘越界(Agent 仍无法 APPROVE/实盘),但「利润中心」的晋升门比宣称的弱。修法: 把真实 PBO/CSCV 与泄漏检测算进 evaluator/工厂,移除安全默认值,无法计算时失败关闭到人工复核。

### 主题 B — 构建期治理扫描器盲区(P1,运行时模型所依赖的控制)

- **[P1] 扫描器不覆盖唯一在跑的服务(console_api)与若干已填充包** — `apps/research-console/scripts/run_paper_to_live_governance_check.py:51-66`。SCAN_SUBDIRS 列了 services/research 的细分子目录却漏了父目录,于是 3716 行的 console_api 与真实 IC/ICIR 的 factor_engine 落在所有扫描根之外。正是 Phase 0.5 声称修过的「扫描局部树、空过」缺陷类。
- **[P1] FORBIDDEN_FLAGS 监视的字符串在代码库里不存在** — 同文件 `:40-43`。watches `live_trading_allowed/LIVE_MODE/...`(代码里没有),却不监视真实的 `live_trading_enabled/real_broker_enabled/allow_order_submission`。实测: 植入 `SafetyConfig(live_trading_enabled=True, real_broker_enabled=True, allow_order_submission=True)` 扫描**零违规通过**,而该构造恰使 `live_trading_armed==True`。
- **[P2] FORBIDDEN_APIS 是固定子串表**: 改名的下单函数可规避(欠包含),且良性字段 `contains_live_orders` 会误命中 `live_order`(过包含)—— 证明扫描器范围从未对唯一在跑的服务校验过。

> 修法: 扫描根改为包根(services/ + quantos/);改名为 AST/词边界匹配;检测真实 SafetyConfig 起爆标志;加元测试断言 services/ 下每个非测试 .py 都在扫描根内 + 真违规植入正控测试。

### 主题 C — 研究风控网关失败开放 + 零测试(已圈住)

- **[P1] 9 引擎研究风控网关在空/退化输入上失败开放** — `services/risk/research_risk/domain/risk_modules.py`(stress/capacity/correlation 在缺数据时 `passed=True`),且 `gate.py:28-128` 无异常聚合、零测试。唯一的护身符: 目前只被只读 console 快照消费(`console_api/main.py:128`),无下单路径。但「确定性、失败关闭」的标称未达。修法: 比照 pre-trade 网关的 run-all-collect-all + 缺输入即拒 + 异常映射为 REJECTED,补全测试。

### 主题 D — 前端诚实性

- **[P1] 合成的 alpha/PnL/数据质量数字以 BACKEND_COMPUTED 呈现,无 synthetic/illustrative 标注** — `services/research/console_api/main.py`(多处)。real-API 模式下,用户看到「已连接/后端计算」的 UI 展示虚构的 Sharpe、CNY 净值曲线、「1842 万行/99.2% 覆盖」,而唯一的标签只区分 mock-vs-backend,不区分 real-vs-synthetic。修法: 每个 payload 加 `data_provenance` 标记,合成数据时在 DataSourceHeader 常驻「合成演示数据 — 非真实研究/PnL」徽章。

### 有分歧的发现(6 条,谨慎对待)

部署 LIVE 阻断是参数默认而非硬平台钉死(可信代码传 L4+flags 可翻转 —— 属已记录的构建期表面)、crowding 检测实现但未在晋升路径调用、研究风控非二元结果被 `.approved` 当通过、stable_hash 依赖 `default=str` 可能跨类型碰撞、bare `pytest` 无项目配置等。均为低危或设计权衡。

---

## 5. 审查自身的盲区(完整性批判 —— 诚实记录)

完整性批判指出本次审查并非完整的全平台评估:

- **3 个已建子系统未单独打分**: 数据 PIT 存储(`services/data/`,9 文件 —— 抗前视的**地基**)、特征泄漏检测器(`services/features/leakage.py` —— 第二道抗前视控制)、组合优化器(`services/portfolio/` —— 数值正确性如 PSD 协方差/约束)。
- **一个被表扬的「优势」实为过誉**: 执行边界的「独立 re-verification」(`gate.py:110-116` 的 `verify_ref`)其实是对公开输入做**无密钥 SHA**(`short_hash`/`stable_hash`),任何能调用或复算哈希的进程内代码都能为任意 intent 伪造合法 ref,且 verify_ref 从不检查 23 项检查是否真的产出 APPROVED(它现造一个 `RiskDecision(APPROVED)`)。代码自己的 docstring 也承认「content binding, NOT a cryptographic proof」。应从「优势」降级为「局限」,或加 HMAC。
- **泄漏检测器结构性规避**: `detect_leakage` 只扰动未来 bar 的 price/volume/knowledge_time;经数组位置、或未扰动的 event_time/instrument_id 偷窥未来的特征会假阴性通过。
- **两条并行验证/晋升路径逻辑分叉**(`validation/pipeline.py` vs `alpha/evaluator.py`): 修一条不保护另一条,是正确性漂移与复核混淆风险。

---

## 6. 对标顶级的差距与路线图

顶级机构级 AI 量化平台具备而 QUANTOS 缺失或仅占位的能力(对 L2 研究脚手架属预期,冲顶级须补):

| 领域 | 现状 | 世界级 | 优先级 |
|---|---|---|---|
| 真实数据湖/接入 | 仅 PIT 抽象,全程合成内存 bar(`services/data/ingestion.py`) | 干净、深、PIT 正确、无幸存者偏差的历史数据湖 + 厂商对账 + PIT 基本面 | P0 |
| 持久化/实验登记/复现 | 进程内、退出即失(`services/data/versioning.py`) | 每次回测/训练为一等不可变可查工件(参数+代码哈希+数据版本+指标) | P0 |
| 事件驱动回测 | 无组合级微结构(`backtest_engine/engine.py`) | 全域事件驱动 + 真实成交/滑点/冲击/借券/融资 | P1 |
| 因子风险模型/约束优化 | 仅对角收缩协方差 + 风险平价(`portfolio/optimizer.py`) | 多因子风险模型 + 约束优化(暴露中性/换手/杠杆约束) | P1 |
| 自主 Agent 研究环 | 固定脚本流水线,无假设生成/搜索/学习(`agent_loop.py`) | 在护栏下生成与排序假设、搜索特征/模型空间、从结果分布学习 | P1 |
| 加密溯源/批准绑定 | 内容绑定,verify_ref 可伪造 | 签名批准令牌 + 进程外验证 | P3(L2 适当推迟,L3+ 前必备) |

**路线图**(从「可靠 L2 脚手架」到「顶级」):
- **M1 — 堵构建期治理盲区(P1)**: 扫描根改包根/全 services+quantos;AST/词边界匹配;检测真实起爆标志;元测试 + 正控测试。
- **M2 — 让晋升路径与文档一样严(P1)**: 把真实 PBO/CSCV + 泄漏检测接进 evaluator/工厂,移除安全默认;IC p 值改 Student-t/bootstrap;统一两条晋升路径。
- **M3 — 加固已圈住但潜伏的表面(P2)**: 研究风控网关改失败关闭 + 补测试;部署就绪级别由单一不可覆写源钉死。
- **M4 — L2 之外的溯源(P3,L3+ 前置)**: 签名批准/风控决策引用 + 进程外验证;进程/部署隔离。
- **战略(冲顶级)**: 真实数据湖 + 实验登记/持久化;事件驱动回测;因子风险模型 + 约束优化器;Agent 研究环的真实智能。

---

## 6.5 整改记录(2026-06-23,本次已修)

审查后立即按 tests-first 修复了三个在范围内的确认差距,全程闸门绿:

- **M2 利润核心完整性**(commit f46a014): evaluator 的 leakage/pbo/regime 证据参数改为
  `Optional=None`,缺失即失败关闭到 REQUIRES_HUMAN_REVIEW(不再静默放行);工厂用 CSCV 算
  **真实批次级 PBO** 传入并接受逐候选 leakage/regime 证据,无证据则停在 TESTING 待人工;
  IC p 值改 **Student-t**(df=T-1,正则化不完全 beta)取代正态近似。
- **M1 构建期治理盲区**(commit 811fc48): 扫描根改为包根(quantos/ + services/,107 文件,
  覆盖 console_api + factor_engine);FORBIDDEN_APIS 改**词边界**(`contains_live_orders`
  不再误报、真实 `place_order` 仍捕获);新增**真实起爆标志**检测(live_trading_enabled 等
  =True、require_human_approval=False);治理测试加**覆盖元测试**(services/ 下每个非测试
  .py 必在扫描根内)+ 真实标志正控。
- **M3 加固**(commits df42ed4, 65fcf98): 9 引擎研究风控网关空输入失败关闭 + 异常聚合 + **首套测试**;
  部署门加**不可覆写的 PLATFORM_READINESS 钉死**(传 L4 也无法解锁 LIVE);console_api 全 payload
  加 `data_provenance="SYNTHETIC_DETERMINISTIC"` + 前端常驻"合成演示数据"徽章;执行边界
  `verify_ref` 的"独立校验"表述**诚实降级**为无密钥内容一致性检查(非进程内信任边界)。

闸门: **451 Python 测试 + 5 前端测试全绿,治理扫描 107 文件 0 违规**。

**仍未处理(更大工程,留作路线图)**: walk_forward_passed 仍为 `net_sharpe>0` 的占位(P2);
以及第 6 节的战略差距(真实数据湖、持久化/实验登记、事件驱动回测、因子风险模型 + 约束优化器、
Agent 研究环的真实智能)—— 这些是冲"顶级"的下一阶段建设项,非本次修复范围。

## 7. 一句话定论

**就 L2 研究平台而言,QUANTOS 是个可靠、失败关闭的脚手架,核心安全不变量经实测确实成立,pre-trade 网关是我审过工程最佳的子系统,量化统计内核真实,威胁模型文档异常诚实 —— 但它还不是顶级。三个已确认、在范围内的差距挡在中间: 构建期治理扫描器(运行时模型所依赖的控制)有真实盲区;Alpha 工厂实际走的晋升路径骑着硬编码的 PBO=0.0 与默认通过的泄漏/regime 检查,使过拟合/泄漏守卫形同虚设。没有一个是实盘越界,L2 边界圈住了爆炸半径,但三者都必须先关闭,才配得上顶级标准。总评: B(76)。**
