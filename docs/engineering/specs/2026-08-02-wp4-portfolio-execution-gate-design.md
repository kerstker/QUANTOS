# WP4 组合与执行承重接入晋级 Gate 设计

Date: 2026-08-02
Readiness: `L2_DRY_RUN_ONLY`
Roadmap slice: WP4（组合与执行承重）

## 路线图原文

> 把边际组合贡献、共同暴露、容量、拥挤、真实盘口成本和多场所 Paper TCA 接入晋级 Gate。

## 现状盘点（探索结论 2026-08-02）

已在门内（承重）：
- 研究 evaluator 对 catalog approved_set 的 `marginal_ir`/`max_corr`（REQUIRES_HUMAN_REVIEW）——
  但 incumbent 是研究 catalog，**不是生产 dry-run book**。
- 九引擎研究风险包（per-decision 执行输入，EXECUTION_PAPER preview 的硬门）。
- 拥挤门（z>3.0 跳周）承重于周度调仓准入，不是晋级证据。

纯诊断、无门消费：
- `capacity_curve` 容量带（go_live guidance 只告警不 clamp）。
- `factor_decomp` 公共风险分解（只接 W6 部署缩放，不接晋级）。
- `population_optimizer.marginal_contributions`/`family_testing.marginal_ir`（研究驱动器专用）。
- `paper_multivenue_sor` 多场所 Paper TCA（console 只读 + watchdog 健康，无门）。

## 设计决定（探索结论支撑）

1. **落点 = 工作流调和器的 RESEARCH_RISK gate 扩展，不是第六个 gate**。
   `services/research/alpha_production_workflow.py` 冻结五门 canonical 顺序，加第六门会
   使 append-only 的既有 bundle 全部失效（无迁移路径）；portfolio-relative 证据也不属于
   per-decision 的九引擎包（粒度不同）。做法：新证据 artifact + `_risk_gate` 归约条件 +
   新 blocker，`eligible_for_execution_paper` 谓词本身不动。
2. **incumbent book = dry_run.db 正典 run 集合**（legacy 三条 + challenger 权威回测），
   不是研究 catalog。候选的边际贡献必须对「当前生产候选组合」计算。
3. **全部阈值 UNCALIBRATED 工程默认并随证据落盘**；首跑只产数字与分布，
   门槛生效与否由独立的预注册评审切片决定——本切片先把证据做成可复算、
   哈希绑定、fail-closed 的承重输入（缺失=NOT_READY，绝不放行）。

## 切片

### W4-1 `services/research/portfolio_evidence.py`（纯核）+
`tools/candidate_portfolio_evidence.py`（驱动器）→
`data/cache/candidate_portfolio_evidence.json`

per 候选五行证据：
- **边际组合贡献**：候选权威干跑曲线与 incumbent book 等权/逆波动合成曲线在
  公共周期对齐（`book_combine.align_on_common_weeks`），报
  `family_testing.marginal_ir`（OLS appraisal ratio，短样本/奇异 → None fail-closed）
  与增量 Sharpe（含候选 vs 不含）。
- **共同暴露**：`factor_decomp` 10 因子库 OLS 的 R² 与最大 |β|；
  与 incumbent 各书的残差相关最大值。
- **容量**：`capacity_curve` 参与率绑定容量带（UNCALIBRATED_PRINCIPLED 标注）。
- **拥挤**：候选干跑拥挤门决策统计（z 分布、BLOCK 周占比）。
- **真实盘口成本**：Almgren-Chriss 冲击带 + 多场所 SOR Paper TCA 引用
  （simulated、UNCALIBRATED；缺口盘深度历史 → 显式 MISSING 不编造）。

### W4-2 调和器接线

`tools/alpha_production_workflow.py::_risk_gate`：RESEARCH_RISK READY 需要
九引擎包 READY ∧ portfolio evidence 行存在且五子项非 MISSING（阈值判定留
W4-3 预注册评审）；缺 artifact/缺行/缺子项 → 新 blocker
`PORTFOLIO_EVIDENCE_INCOMPLETE`，状态 PENDING。证据 content hash 进门哈希。

### W4-3（后续独立评审）：阈值预注册与生效

首跑分布出来后，按 727 §10.3「不得只比总收益」的十键面板纪律预注册阈值
（marginal IR 下界、共同暴露上界、容量带下界、拥挤 BLOCK 占比上界、
TCA 带上界），版本化 `portfolio_gate_policy.v1` 内容寻址后生效。
本切片不预断任何候选。

### W4-3 预注册（2026-08-02 生效）：`portfolio_gate_policy.v1`

首跑分布（192 共同周，legacy 行）：marginal_ir 0.063/0.130/0.206，
incremental_sharpe −0.053/+0.344/+0.701，R² 0.045–0.090，
残差相关 ≤0.072，容量带低 $1.95M/$7.16M，拥挤 BLOCK 占比 5.7–8.9%。

| 阈值 | 值 | 锚定 |
|---|---|---|
| `min_marginal_ir` | 0.0 | 必须贡献正交 IR，不允许为零/负 |
| `min_incremental_sharpe` | 0.0 | 不得稀释 incumbent book（carry −0.053 即被此条诚实拦下） |
| `max_common_exposure_r2` | 0.5 | UNCALIBRATED；W3 观测 ≤0.198 |
| `max_residual_corr_vs_incumbent` | 0.5 | W3 聚类 rho_cut=0.5 先例 |
| `min_capacity_to_aum_ratio` | 3.0 | W7 §11.3「部署 ≤ 安全容量/3」先例（UNCALIBRATED 带） |
| `max_crowding_blocked_share` | 0.25 | UNCALIBRATED；观测 ≤0.089 |
| TCA | 本切片不设阈值 | canary 引用，费率 UNCALIBRATED_RESEARCH_ASSUMPTION |

语义：缺子证据 = INCOMPLETE（维持 PENDING，不算 merit 失败）；证据齐全而
未过阈 = FAIL（证据说不）。政策内容寻址进门证据哈希；改阈值 = 新政策版本。
全部 UNCALIBRATED 工程默认，校准评审是独立后续切片，不得因候选未过而下调。

## 边界

- 只加证据与 fail-closed 完备性检查；不自动晋级、不碰 live/mandate/凭证。
- 数据缺口（盘口深度史、跨所执行）显式 MISSING，绝不合成。
- 单节点、确定性纯核 + tools IO 边界；无网络。
