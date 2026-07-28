# QUANTOS 顶级 AI 量化系统审查（2026-07-27）

## 结论

QUANTOS 当前是 **L2 研究 + 干跑/纸面交易平台**，不是可自动放行实盘的生产交易系统。研究、组合、风险和治理原语已经形成较完整的单机闭环；主要缺口不是再增加一个 Alpha，而是证据连续性、外部运营能力、模型风险治理、数据深度和多场所执行证据。

本轮不降低任何门槛，也不打开真实下单。当前纸面订单预览的阻断是有效的：三条 Alpha 有已认证的前向决策；截至 2026-07-29，九引擎风险输入的容量、成交量、AUM、收益和 regime 已由真实 PIT 数据自动生成，但当前决策早于新评估证据，因此 `alpha_metadata` 不能事后回填，仍只能 `PAPER_BLOCKED`。系统不会用默认值、未来证据或旧的四引擎子集伪造成交。

## 已具备的能力

| 维度 | 当前能力 | 判断 |
|---|---|---|
| Alpha 研究 | `carry_oi_majors`、`crowd_contra`、`top_contra`，截面排序、IC/ICIR、GP/DSL、组合诊断 | 研究原语较强，仍需持续 OOS 证据 |
| 回测/验证 | PIT、泄漏检查、CPCV、PBO、DSR、purged/embargoed walk-forward | 是当前最成熟的部分 |
| 组合/成本 | 约束优化、风险平衡、冲击/容量、尾部/情景和归因诊断 | 已能识别 gross 与净执行差异 |
| 风险 | 九引擎 research-risk gate、pre-trade gate、kill-switch、审计链 | L2 下 fail-closed，安全边界正确 |
| Paper | mark/funding/账本/整数手数/对账/幂等预览和实时健康检查 | 能运行，但完整证据不足时必须阻断 |
| AI | 非人类 ResearchAgentLoop、工具边界、模型风险制品和历史 | 有受控研究循环，尚非可独立交易 AI |
| 前端 | 研究、风险、Paper、系统健康等页面和 API | 已可用，但统一控制面、后台任务和跨页上下文仍不足 |
| 实盘 | testnet/适配器接缝和人工审批设计 | `L2_DRY_RUN_ONLY`；真实 broker/mainnet 结构性关闭 |

## 本轮修复

### 1. 完整研究风险输入包的严格边界

新增 `services/risk/research_risk/bundle.py`，只读加载并校验同一 PIT 快照的风险输入。每条 Alpha 必须同时提供：

- `capacity_cny`、`daily_volume_cny`、`total_aum`；
- 至少两个对齐的 Alpha 收益序列；
- 与收益序列对齐的 regime 标签；
- `age_days`、验证置信度、paper 置信度以及 decay/overfit/leakage 标志；
- Alpha、决策时间、决策 payload SHA-256、数据版本和风险配置哈希绑定。

输入缺失或不一致返回 `PENDING`/`BLOCKED`，绝不填充默认值。还增加了当前决策的容量键覆盖、当前 Alpha 收益存在性、配置哈希格式检查。

### 2. 纸面订单预览接入绑定包

`tools/paper_order_preview.py` 现在从 `data/cache/paper_research_risk_inputs.json` 读取绑定包。没有真实制品时，输出会明确显示：

- `PAPER_BLOCKED`；
- `REAL_RESEARCH_RISK_FULL_PENDING`；
- `research_risk_bundle.status=PENDING`；
- `missing=bundle_file`。

旧的四引擎 `REAL_RESEARCH_RISK_SUBSET` 永远不能成为成交凭据。

### 3. 健康检查可视化阻断原因

`tools/paper_health.py` 增加 `research_risk_bundles`、`research_risk_bundle_complete` 及逐 Alpha 状态，前端/API 可以直接显示“缺少哪份证据”，而不是只显示笼统的“前向门未通过”。

### 4. 同 PIT 评估与风险包自动生产

`tools/paper_alpha_evaluation.py` 现在按内容寻址的研究 Alpha ID，把评估契约、完整全局 trial 分母、OOS 收益、DSR/PBO 和重放摘要写入生产 experiment registry，并生成 `evidence/paper_alpha_evaluations.json`。三份当前评估均通过制品完整性复核，但在 2539 次全局试验分母下统计 verdict 均为 `FAIL`；“可验证”不等于“通过门禁”。

`tools/paper_research_risk_bundle.py` 从真实 PIT ADV、Frankfurter/ECB CNY 汇率、历史 Alpha 收益、VIX regime、维护状态、前向账本和已验证评估构造绑定包。容量规则固定为逐名 PIT 日均 quote ADV 的 10%，没有合成默认值。评估 `as_of` 必须不晚于前向决策 `recorded_at`，否则只把 `alpha_metadata` 标为缺失。

Windows L2 调度增加每周日 20:45 的 `paper_alpha_evaluation`，在 21:00 `forward_track_advance` 前运行；风险包、订单预览、实时 10k、健康检查和 watchdog 保持 fail-closed。Task Scheduler 当前仍是交互登录身份，不代表跨主机或服务身份的生产调度。

## 当前顶级门状态

权威状态仍以 `evidence/top_tier_assessment.json`、`config/top_tier_requirements.json` 和 `python scripts/assess_top_tier.py --check` 为准。本次审查发现的非通过项分为三类：

1. **可在本地继续修复的证据一致性**：模型风险基线中的实现摘要与当前代码漂移；全局试验账本仍需要跨会话、不可丢弃的完整历史制品。
2. **需要真实外部输入/运营的项目**：授权深度/逐笔数据、跨场所 SOR、外部 OIDC/mTLS/密钥生命周期、30 天恢复演练和外部封存/供应链证明。
3. **纸面部署前置条件**：完整九引擎风险包、逐 Alpha 复核、至少 8 个有效前向周期、正净收益、回撤和账本一致性。

这些项目不能通过修改评分器、降低阈值或重写历史证据来“修复”。

## 顶级目标的剩余路线

### L2 安全范围内

1. 在下一次评估先于决策的周期生成完整同快照风险输入包；当前五类真实输入已具备，禁止把 2026-07-29 的评估回填到更早决策。
2. 对三条 Alpha 分别跑完整九引擎 gate 和 CPU 精确纸面复核，保留拒单、资金费、整数手数、对账和审计记录；当前低 DSR 必须继续阻断。
3. 继续累计三条 Alpha 各至少 8 个独立前向周期，再评估 `PAPER_ONLY_PENDING_FORWARD` → `PAPER_READY_FOR_REVIEW`。
4. 继续维护模型风险制品和跨会话 trial ledger 历史，不修改既有历史、不复用旧 artifact ID。
5. 前端补齐 BFF/统一会话、Job Registry、后台活动时间线、阻塞/重试入口和版本握手。

### 只能在 L2 之后

授权数据、tick/LOB 执行校准、外部强身份和密钥托管、跨主机恢复/封存、真实 broker SOR 及 mainnet 交易。这些是外部证据与组织运行能力，不应在本地用 mock 代替。

## 验收

新增回归覆盖严格风险包缺失、决策摘要不匹配、容量键缺失、当前 Alpha 收益缺失、配置哈希校验和健康检查可见性。执行统一验收：

```text
python scripts/validate.py
```

在统一验收通过前，不应把系统描述为“顶级”或“可实盘”；正确分类仍是 `L2_DRY_RUN_ONLY`。

## 最终复核结果（2026-07-27）

本轮修复后，权威能力快照为 `NOT_TOP_TIER`：22 项中 15 项通过、4 项部分通过、2 项需要人工/外部证据、1 项失败；3 个红线项仍未关闭。模型风险制品已刷新为 25 个制品、4 条运行事件、3 个不可变历史修订；Alpha 全局 trial ledger 已保留 2 个跨会话搜索记录并通过链校验；供应链基线已按当前代码重新生成（534 个组件）。

统一验收 `python scripts/validate.py` 已通过：主测试 3112 passed/1 skipped，live/tools 测试通过，治理扫描 356 个文件、0 违规，前端 typecheck、ESLint、测试和生产构建全部通过。验收通过只证明本地工程一致性，不会替代外部证据或前向交易证据。

截至 2026-07-29，当前三条 Alpha 的纸面状态仍是 `PAPER_BLOCKED`。纸面健康检查已从“缺六类输入”收敛为只缺 `alpha_metadata`：`capacity_cny`、`daily_volume_cny`、`total_aum`、`alpha_returns` 和 `regime_labels` 均由真实 PIT 来源生成。缺失不是生产器空壳，而是当前决策早于评估证据，因果门拒绝事后回填；每条 Alpha 的前向有效周期仍为 `0/8`。三份新评估虽然制品校验通过，但 DSR 在 2539 次全局试验校正后均低于门槛，verdict 为 `FAIL`。因此现有记录仍只能作为诊断快照，不能作为完整纸面成交或实盘授权。

剩余顶级门的首要阻塞是：外部模型提供方与独立审查身份、授权数据深度、跨场所 SOR、外部 OIDC/mTLS/密钥生命周期、真实 30 天恢复历史与外部签名封存/供应链收据。它们必须通过真实部署和时间累积获得，不能通过修改阈值或生成合成历史关闭。
