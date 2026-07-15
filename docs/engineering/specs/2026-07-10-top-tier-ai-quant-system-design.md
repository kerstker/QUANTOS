# QUANTOS 顶级 AI 量化交易系统标准与补齐体系设计

> **状态：已批准设计基线**  
> **批准日期：2026-07-10**  
> **范围：前端、后端、数据、研究、AI、组合、风险、执行、治理、平台工程与运营**  
> **安全边界：L2_DRY_RUN_ONLY 保持不变；本文不授权真钱交易、真实券商连接或 AI 直接交易。**

## 1. 背景

QUANTOS 已拥有真实的研究统计内核、PIT 数据契约、Alpha 生命周期、组合与风险原语、干跑执行、testnet/live 接缝、治理扫描和研究控制台。但“顶级”口径散落在多份审计与路线图中，评分会随代码漂移，且顶级研究平台与顶级真钱系统经常混淆。

本设计建立唯一上位标准：先定义什么是顶级、什么证据能证明顶级，再根据当前事实逐项补齐。后续路线图、实现计划、控制台完成度和 Go/No-Go 证据必须引用本标准，不能自行采用更宽松口径。

## 2. 双层目标

### Tier A — Institutional Research Grade

在当前 L2 边界内真实建成并机器验证，覆盖数据、研究、AI、验证、组合、风险、干跑、前端、治理、复现与工程交付。

### Tier B — Production Trading Grade

完整定义生产能力，完成安全接缝、testnet、故障演练、进程边界与迁移证据。真钱能力保持禁用，直到未来独立人工 Go/No-Go 决策。

Tier A 达标不自动提升真钱 readiness；Tier B 的接口或模拟完成也不代表真钱路径获批。

## 3. 非目标

- 不启用 `LIVE_TRADING_ENABLED`、`REAL_BROKER_ENABLED` 或 `ALLOW_ORDER_SUBMISSION`。
- 不接入真实券商凭证、提款权限、资金划转或 AI 直接交易。
- 不把回测、历史干跑或 testnet 结果描述为实盘收益证明。
- 不用文档、空目录、接口空壳、mock 或合成页面冒充完成能力。
- 不因目标宏大而一次性重写现有稳定系统。

## 4. 成熟度模型

| 等级 | 名称 | 定义 |
|---|---|---|
| M0 | ABSENT | 没有实现，也没有可执行接缝。 |
| M1 | DEMONSTRATION | 原型、合成、mock、脚手架或仅文档设计。 |
| M2 | REAL_SINGLE_NODE | 真实代码和真实/离线数据在单机运行，有基础测试。 |
| M3 | INSTITUTIONAL_RESEARCH | 完整证据合同、fail-closed 边界、复现、审计和前向/干跑证据。 |
| M4 | PRODUCTION_READY | 强身份、隔离、可观测、恢复、容量、故障演练与部署证明。 |
| M5 | TOP_TIER_CONTINUOUS | SLO、独立验证、红队、故障注入、漂移检测、恢复演练和长期运营证据持续有效。 |

约束：

- M3 是 Tier A 基础目标；承重域需达到 M4 才可称顶级研究平台。
- Tier B 不能只凭代码达到 M5，必须有真实组织与长期运营证据。
- 红线触发时对应域最高 M2，系统 verdict 必须为 `NOT_TOP_TIER`。
- `NEEDS_EXTERNAL` 与 `POST_L2` 必须诚实显示，不能用空实现提高完成率。

## 5. 六项证据合同

每个能力项必须声明并验证所需证据；缺少任一必需证据就不能达到目标等级。

1. **implementation**：真实模块、契约、入口和依赖边界；不接受未调用孤立函数或仅 mock 实现。
2. **verification**：正常、边界、异常、对抗和恢复测试；安全承重路径必须进入 `python scripts/validate.py`。
3. **data**：来源、时间范围、知识时间、版本、哈希、质量与限制；合成数据不能支撑真实成熟度。
4. **operation**：真实端点、页面、指标、日志、状态存储、前向跟踪或演练制品。
5. **governance**：权限、审批、审计、扫描、职责分离和安全边界；自报身份不等于强身份。
6. **reproducibility**：权威命令、环境版本、输入制品和结果摘要；缺失时必须为 `UNVERIFIED`。

## 6. 十一个能力域

### 6.1 数据护城河

双时间/PIT、历史成分、退市与公司行为、数据源语义、单位与时区、交易日历、跨源校验、质量 SLA、血缘、版本、多资产多频率、BYO 授权数据接缝以及可重建制品。供应商数据与 tick/L2 可标记 `NEEDS_EXTERNAL`，但接入契约和质量门必须真实实现。

### 6.2 Alpha 研究

结构化假设、因子 DSL、传统因子、交互、GP/搜索、ML 候选、全局试验总账、等价表达式、搜索预算、因子血缘、多资产多持有期诊断、相关性、边际 IR、容量、拥挤及失败研究留存。

### 6.3 验证与模型风险

Purged/embargoed walk-forward、CPCV、DSR、PBO、FDR/BHY、泄漏检测、无前视选择 OOS、regime、敏感性、成本容量冲击、尾部情景、challenger、模型卡、风险分级及前向证据钟。

### 6.4 组合构建

信号清洗、中性化、风险模型、约束优化、可行性证明、成本容量参与率拥挤约束、多策略风险预算、资本分配、相关性与边际贡献、Brinson/因子/策略/成本归因以及组合压力测试。

### 6.5 风险体系

研究、预交易、盘中、日终和部署风险分层；单笔、单名、行业、因子、策略、账户和全局限额；kill switch、日损、杠杆、流动性、拥挤、陈旧和异常门；状态持久化、重启恢复、审计及 mandate/gate 一致性。

### 6.6 执行与微观结构

干跑、testnet、主网接缝隔离；订单状态机、幂等、重试、撤单、部分成交、孤儿单恢复、交易所过滤、精度、限价、切单、价格保护、预记账、对账、持仓归属、资金费用、TCA、LOB/队列/滑点/冲击仿真。多场所和 SOR 在 L2 内只做仿真与接口验证。

### 6.7 AI 智能体

非人 actor、工具白名单、动作分级、预算与停止条件；模型、提示词、工具和会话血缘；证据引用；自我评估、regret、红队和对抗数据；模型注册、离线评估、漂移和回滚。AI 不能持有密钥、自我审批或直接下单。

### 6.8 治理与安全

强身份、RBAC/ABAC、最小权限、职责分离、多方批准、一次性 subject 绑定授权、外部签名、密钥托管与轮换、禁止提款、不可抵赖审计、事件响应和研究/审批/签名/风险/执行进程隔离。共享 token 与自报标签只能达到单机研究等级。

### 6.9 平台工程

统一后端、前端、治理与制品门禁；类型检查、静态分析、覆盖率、SAST、依赖扫描；锁定依赖、SBOM、制品签名、可重建构建、环境指纹、分支保护、发布审批、回滚和数据库迁移纪律。

### 6.10 可靠性与运营

SLO、健康、就绪、指标、结构化日志、追踪、告警、值班、运行手册、事故复盘、HA、备份、恢复、RPO/RTO、恢复演练、容量规划、故障注入、网络分区、时钟偏移与交易所限流演练。

### 6.11 前端控制台

路由/导航/测试单一注册表；真实数据优先和诚实 provenance/freshness；强身份与权限感知；危险操作确认；完整血缘；统一 loading/error/empty/stale/partial；E2E、视觉回归、无障碍、性能预算和响应式测试。前端不得保存长期密钥或维护手写完成度分数。

## 7. 系统级红线

以下任一成立时，总 verdict 必须为 `NOT_TOP_TIER`：

1. `python scripts/validate.py` 不绿色。
2. 关键页面使用未标记合成数据或把 fallback 描述为真实状态。
3. 承重研究结论没有数据版本、代码版本和证据摘要。
4. Alpha 晋级没有全局试验计数或可以绕过验证链。
5. 风险、mandate、kill switch 或执行 gate 存在旁路。
6. 自报身份被描述为真实多人审批或强身份。
7. 未完成恢复演练却声称具备灾难恢复。
8. 无长期前向证据却把历史结果描述为可部署收益。
9. AI 可以持有密钥、自我审批、修改 readiness 或直接下单。
10. 主网 readiness 可被单一配置、单一 token 或单人操作解锁。
11. 关键依赖、数据或模型无法从声明环境重建。
12. 可通过直接编辑评分结果提升成熟度而无证据校验。

## 8. Wave 0 权威制品

### `docs/QUANTOS_TOP_TIER_STANDARD.md`

面向人的正式标准，按稳定 requirement id、验收条件和红线组织。

### `docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md`

记录 requirement id、Tier、当前/目标成熟度、分类、证据、verdict、gap 和 next action；由评估器生成或校验，不允许人工维护第二套分数。

### `config/top_tier_requirements.json`

机器可读要求注册表，只声明目标，不声明当前完成状态。每项至少包含 `id`、`domain`、`tier`、`target_maturity`、`classification`、`description`、`required_evidence` 和 `red_line`。

### `scripts/assess_top_tier.py`

离线、确定性、只读、fail-closed：读取注册表，以明确 checker 核验文件、测试、命令、端点、页面、摘要和边界；生成 JSON 报告与 Markdown 能力矩阵；人工项为 `MANUAL_EVIDENCE_REQUIRED`，异常为 `UNVERIFIED`/`FAILED`；不读取网络、密钥或运行 live 操作。

## 9. 防虚报设计

治理测试必须钉死：

1. requirement id 唯一，域、Tier、分类和目标等级合法。
2. 通过项必须存在对应 checker 和证据。
3. 手工编辑 matrix 不影响评估结果。
4. `MANUAL_EVIDENCE_REQUIRED` 不计为通过。
5. `NEEDS_EXTERNAL`/`POST_L2` 不得用空文件或 mock 提升成熟度。
6. 红线失败时总 verdict 恒为 `NOT_TOP_TIER`。
7. 评估器无网络、broker SDK、密钥读取和下单调用。
8. 前端只消费评估制品，不包含硬编码顶级分数。

## 10. 控制台集成

升级 `SystemCompleteness` 为标准体系只读视图：分开显示 Tier A/Tier B；展示十一域、红线、证据新鲜度与未验证项；支持 requirement 下钻；区分 `PASS`、`PARTIAL`、`FAILED`、`UNVERIFIED`、`MANUAL_EVIDENCE_REQUIRED`；制品缺失时 fallback 不显示完成分数；页面无修改标准、豁免红线或启用 live 的入口。

## 11. 推进波次

### Wave 0 — 标准与事实基线

正式标准、requirements、评估器、能力矩阵、防虚报测试和控制台集成。完成后以评估器缺口作为唯一路线图。

### Wave 1 — 生产安全地基

修正 live arming 与人工批准语义；建立强身份/授权契约与外部签名迁移；前后端、静态分析和供应链进入统一门禁；补 restore CLI、恢复校验和演练。

### Wave 2 — 数据与研究护城河

强化数据版本、历史成分、跨源验证、质量 SLO、全局试验总账、模型注册、challenger、多资产研究与 ML/AI 证据链。

### Wave 3 — 组合、风险与执行

补齐组合级限额、尾部预算、统一风险状态、LOB 仿真、TCA 反馈、testnet soak、故障注入和 reconciliation 演练。

### Wave 4 — 前端与运营

权限感知控制台、审计时间线、E2E、视觉回归、无障碍、性能预算、SLO、告警、运行手册与事件响应。

### Wave 5 — Production-readiness 证明

进程隔离、外部签名、secrets manager、发布签名以及恢复、轮换、交易所异常和事故演练，生成 Go/No-Go 证据包。Wave 5 完成仍不自动启用真钱。

## 12. Wave 0 验收标准

1. 标准正文覆盖十一域、双 Tier、M0–M5、证据合同和红线。
2. requirements JSON 通过 schema/治理测试。
3. 评估器在仓库根目录离线运行并输出稳定结果。
4. 缺失、异常和人工证据 fail-closed。
5. 能力矩阵由评估结果生成，缺口有唯一 requirement id。
6. console API 提供只读顶级评估快照并通过契约测试。
7. `SystemCompleteness` 显示真实评估结果且无硬编码分数。
8. 前端类型检查、测试和生产构建通过。
9. `python scripts/validate.py` 全绿。
10. `docs/PROJECT_STATE.md` 指向本标准和当前 Wave 状态。

## 13. 架构决策

- 使用 requirements registry + evidence checker，而非手工评分表；目标声明与当前事实分离。
- 评估器离线只读，不能成为新操作面；外部能力使用人工证据状态。
- 不立即删除旧路线图；先映射未完成项到稳定 requirement id，再归档历史文档。
- 不以单一总分掩盖红线；总 verdict 由红线和最低承重域共同决定。

## 14. 风险与缓解

| 风险 | 缓解 |
|---|---|
| requirements 数量过多 | Wave 0 先覆盖承重项，按域增量扩展。 |
| checker 退化成文件存在检查 | 承重项要求测试、运行、治理和复现组合证据。 |
| 前端出现第二份分数 | 治理扫描硬编码 maturity/completion score。 |
| 外部能力被假实现填满 | `NEEDS_EXTERNAL`/`POST_L2` 不可自动通过。 |
| 标准阻碍迭代 | 稳定 id、版本化 checker 和变更迁移。 |
| 大目标导致无止境重构 | 按 Wave 和 requirement 交付可运行增量。 |

## 15. 长期成功定义

只有以下条件全部被当前证据证明时，长期目标才完成：

- Tier A 所有承重 requirement 达到目标成熟度且无红线失败。
- 前后端共享同一能力事实源，操作和状态诚实可追踪。
- 数据、研究、AI、组合、风险和执行形成持续证据闭环。
- 平台具备生产级身份、供应链、可观测、恢复和运营能力。
- Tier B 生产准备要求完成，真钱能力仍受独立安全决策控制。
- 权威门禁、恢复演练、故障演练和复现证据有效且新鲜。

在这些条件未被逐项证明前，目标保持进行中，不得因完成某个 Wave 或测试数量较大而宣称顶级系统已完成。
