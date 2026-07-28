# QUANTOS 自治能力基线与差距分析

> 本报告由 `scripts/generate_autonomy_baseline.py` 生成。它是当前快照，
> 不覆盖历史证据，也不授予任何真实资金或订单权限。

- 生成时间（Unix ms）：`1785213308476`
- commit：`b2e2b1a5d2f59f75bf8c859a928d0696bbd1bc4f`
- 当前等级：`L2_DRY_RUN_ONLY`
- 输入哈希：`a6a417746e7e30f1edc289333ee6408c7b4ade1035e6930a5d3b7f57a4bc4d1c`
- 配置哈希：`b59e44d7cc6f2afe03ea4e4a37e775cd9759d0ee2744d71279b3387b59b516a0`

## Phase 0/1 当前状态

| 阶段 | 状态 | 依据/阻断 |
|---|---|---|
| Phase 0 基线与安全冻结 | `PASS` | 安全旗标与输入清单满足；完整门禁报告已纳入判定 |
| Phase 1 数据血缘与统一门禁 | `PASS` | admission=PASS, quality=PASS, lineage=PASS, backfill=PASS |

## 当前机器证据

- Data Admission：`PASS`（quality=`PASS`, lineage=`PASS`）。
- Raw lineage/backfill：`PASS`。
- Paper 10k：`PAPER_MARKS_ONLY_REPLAY`；paper_ready=`False`，paper_mark_ready=`True`，paper_preview_ready=`False`。
- Top-tier：`NOT_TOP_TIER`；摘要=`{"failed": 1, "manual_evidence_required": 0, "partial": 6, "passed": 15, "red_lines_failed": 3, "total": 22, "unverified": 0}`。
- Full validation report：scope=`ALL`，status=`PASS`。

## 安全边界

| 旗标/能力 | 当前值 |
|---|---|
| `LIVE_TRADING_ENABLED` | `false` |
| `REAL_BROKER_ENABLED` | `false` |
| `ALLOW_ORDER_SUBMISSION` | `false` |
| `ALLOW_AI_DIRECT_TRADING` | `false` |
| `REQUIRE_HUMAN_APPROVAL` | `true` |
| live_allowed | `false` |
| order_submission_allowed | `false` |
| real broker | `STRUCTURAL_REJECTING_SEAM_ONLY` |

## 证据冲突

发现并记录以下冲突；按优先级使用当前可执行证据，未把冲突伪装成全绿：

- `EVIDENCE_CONFLICT.PROJECT_STATE_VS_CURRENT_ADMISSION`：当前 admission 为 `PASS`，`docs/PROJECT_STATE.md` 仍含历史 `quality=BLOCK or lineage=BLOCK`；该冲突已 fail-closed 记录，活文档已明确标注其为历史状态。

## 尚未完成的真实缺口

- 完整九引擎 Paper 风险输入包与至少 8 个有效前向周期仍未满足；不能以默认值、旧快照或合成数据替代。
- 新研究入口已接入 TrialRecord 与 OOS access 哈希链；2533 条历史 bare trial 的缺失字段不可事后伪造。独立模型风险审查身份、外部 OIDC/mTLS、授权数据、跨场所执行和外部封存仍需真实证据。
- 主网 broker、真实凭证和真实下单路径不属于当前 L2 开发范围，且必须继续保持结构性拒绝。

## 下一项唯一最高优先级

在当前 admission 已 PASS 的前提下，生成并绑定三条 Alpha 的完整九引擎 Paper 风险输入包；缺失项必须继续输出 `PAPER_BLOCKED`，并为每条 Alpha 保留拒单、对账和审计证据。
