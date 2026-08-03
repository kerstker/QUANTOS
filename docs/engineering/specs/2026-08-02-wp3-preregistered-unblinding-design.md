# WP3 预注册揭盲与自动交接设计

Date: 2026-08-02
Readiness: `L2_DRY_RUN_ONLY`
Roadmap slice: WP3（并行前向与自动交接）

## 路线图原文

> 候选冻结即盲化 shadow；历史 Gate 通过后自动进入 Execution Paper；
> 按候选预注册的有效独立观察数、持有期和 regime 覆盖揭盲，不以频繁查看结果替代证据。

## 现状盘点（已存在，不重建）

- **冻结即盲化 shadow**：WP1 的 attested forward ledger 决策先于结果落账
  （`forward_decision_bindings` 绑定数据准入权威），历史 OOS 与 shadow 并行积累。
- **自动交接**：`eligible_for_execution_paper` 由调和器自动推导；调度中的
  `paper_order_preview` 在证据满足时自动过门、模拟成交、落账——无需人工搬运。
- **原始期数门**：`_shadow_gate` 目前以 `n_forward_periods >= 8`
  （`REQUIRED_FORWARD_PERIODS`）判 MATURE。

## 缺口

1. **有效独立观察数 ≠ 原始期数**。决策在上一决策兑现前开出（3d 变体曾稳态积压
   2 条 pending）时，两条兑现窗口重叠，包含共同价格路径，不是两次独立观察。
   原始计数会把重叠窗口重复计为独立证据。
2. **regime 覆盖未判定**。8 个前向期可能全部落在同一市场 regime（例如单一低
   波动资金费环境），影子证据无法说明跨 regime 稳定性。
3. **揭盲政策未预注册、未版本化**。`8` 是写死的工程常量，没有内容寻址的政策
   身份；未来改门槛无法审计。

## 设计

### 纯核 `services/research/unblinding.py`（无 IO/RNG/墙钟）

- `UnblindingPolicy`（frozen dataclass）：
  - `min_periods: int = 8`（原始兑现期下界，与现行门同值）
  - `min_independent_periods: int = 8`（**有效独立观察数**下界）
  - `min_regime_classes: int = 2`
  - `max_single_regime_share: float = 0.75`
  - `policy_version: str = "unblinding_policy.v1"`
  - 全部 UNCALIBRATED 工程默认，随证据落盘；`content_hash()` 内容寻址。
- `RealizationWindow(decided_ts, as_of_ts)`：从 attested ledger 的兑现记录构造。
- `effective_independent_periods(windows)`：最早结束优先的贪心区间调度，返回
  最大互不重叠兑现窗口子集基数（确定性、键序无关；端点相接 `[t0,t1] [t1,t2]`
  算不重叠）。重叠只少计、绝不多计。
- `regime_coverage(labels)`：逐窗口 regime 标签分布；`n_classes` 与
  `max_share` 双条件。
- `evaluate_unblinding(windows, labels, policy)` →
  `{status: SEALED | READY_TO_UNBLIND, independent_periods, regime_distribution, blockers[]}`。
  fail-closed：任一窗口缺 regime 标签 → `SEALED` + `REGIME_EVIDENCE_MISSING`，
  绝不猜；窗口为空 → `SEALED`。**揭盲判定只消费计数与覆盖，不读收益/IC**
  （不以频繁查看结果替代证据——绩效证据是另一个独立 gate 的事）。

### regime 标签（PIT 安全）

调和器对每个兑现窗口以**截至窗口末端**的扩展窗口计算：
- `funding_z`：BTC 资金费日和的 trailing z（expanding 均值/方差，只用 ≤ t 数据）；
- `rv_z`：BTC 日收益 trailing 30d 实现波动的 expanding z。

两维各取 expanding 三分位 → 最多 9 个 regime 类。数据源 = 本地
`crypto_perp_mark_prices.json` / funding 日数据（与 forward 驱动器同一批文件，
PIT 语义 kt=et）。扩展窗口无前视；**不**用全样本分位（那是回顾性描述）。

### 调和器接线（`tools/alpha_production_workflow.py`）

`_shadow_gate` 升级为三重条件全部满足才 `MATURE`：
`n_forward_periods >= min_periods` ∧
`independent_periods >= min_independent_periods` ∧
regime 覆盖双条件。未满足保持 `RUNNING` 并在 gate evidence 落
`unblinding` 子块（政策 content hash、各项实测值、未满足项清单）。
政策以 workflow 级版本注册（`unblinding_policy.v1`）并进入 gate evidence
content hash；**不**改动已冻结候选身份（identity hash 不变、证据钟不重置）。
未来候选可在 FROZEN 时携带自己的政策覆盖——新身份、新证据钟。

### 边界（诚实声明）

- 本切片只实现**揭盲纪律**：它可以让 MATURE 比原始计数更晚到来，绝不更早。
- 不改动 preview/fill/九引擎/晋级门槛；绩效 FAIL 的候选依然 FAIL。
-  regime 阈值全部 UNCALIBRATED；首跑数字落盘供后续校准评审。
- 单一节点、确定性核心；无网络；不碰 live/凭证/下单路径。

## 切片与验收

W3-1（本切片）：纯核 + 调和器接线 + 钉死测试
（重叠折扣、端点相接、缺标签 fail-closed、政策哈希漂移检测、现行 10 候选
影子门回归）。验收对照：
- [ ] 重叠窗口的有效独立数严格小于原始期数（构造案例）
- [ ] 现行 7d 候选（无重叠）有效数 == 原始期数（零行为漂移）
- [ ] regime 缺证据 → SEALED，绝不 READY
- [ ] workflow 事件链含政策 content hash，篡改政策文件 → 链校验失败
