# 小时级 Alpha → Shadow/Paper L2 运维手册

## 1. 安全边界

本流程只用于 `L2_DRY_RUN_ONLY` 研究、Shadow 和 Paper 前向证据积累。它不连接券商，
不读取交易凭证，不签署 mandate，不提交订单，也不允许 AI 直接交易。以下开关必须始终保持：

```text
LIVE_TRADING_ENABLED=false
REAL_BROKER_ENABLED=false
ALLOW_ORDER_SUBMISSION=false
ALLOW_AI_DIRECT_TRADING=false
REQUIRE_HUMAN_APPROVAL=true
```

`hourly_alpha_advance` 没有 `--live`、`--submit` 或 `--arm` 参数。任何“验证通过”只表示
研究证据通过，不表示真钱授权。

## 2. 数据输入与职责分离

网络刷新和工作流推进是两个独立动作：

1. 已批准的数据抓取工具只负责生成本地 raw archive；
2. `hourly_market_materialize` 从 raw archive 生成不可变、带摘要的小时面板；
3. `hourly_alpha_advance` 只读已物化面板，不联网；
4. 前向决策只有在 validation artifact、workflow receipt 和数据准入权威全部匹配时才落盘。

不得把网络请求、凭证读取或 broker adapter 加进 advance 工具。

## 3. 生成已证明的小时面板

在目标 worktree 根目录运行：

```powershell
rtk py -3.14 -m tools.hourly_market_materialize `
  --raw-root data/raw/hourly-market `
  --output-root data/cache `
  --as-of 1785955200
```

输出位于：

```text
data/cache/hourly_market/YYYY-MM-DD/interval-3600/
  manifest.json
  data.jsonl
```

物化器会验证来源文件摘要、PIT 时间、动态宇宙、连续完整桶和输出摘要。gap、未来知识、
不完整桶、摘要冲突或宇宙不足都会直接失败，不发布部分结果。

## 4. 推进 L2 工作流

```powershell
rtk py -3.14 -m tools.hourly_alpha_advance `
  --panel data/cache/hourly_market `
  --as-of 1785955200 `
  --budget 10000
```

预算默认 10,000，硬上限 100,000。超过上限会在生成前拒绝。

进程输出一行 JSON receipt：

- `status=COMPLETED`：本次批次已形成终态 receipt；这包括“0 个候选通过验证”的合法结果；
- `workflow_state=SHADOW`：全部通过候选已有幂等 forward decision；
- `workflow_state=BLOCKED`：没有候选通过，原因保留在 trial/validation receipts；
- `status=BLOCKED` 且 workflow 仍为 `VALIDATED`：验证通过，但数据准入、决策输入或 forward 写入未满足，绝不能视为 Shadow 完成；
- 退出码 `0` 表示批次完整结束，退出码 `2` 表示需运维处理的 fail-closed 状态。

当前 CLI 只消费已物化 `HourlyMarketPanel`。如果生产 validation DAG 所需的已注册 leakage、
OOS 或 realism 证据尚未装入该批次，正确结果是 0 passer 的 `BLOCKED` receipt，而不是伪造 PASS。

## 5. 持久状态与追溯

默认状态目录为 `data/cache`：

| 文件 | 用途 |
|---|---|
| `hourly_alpha_lease.db` | 单节点调度租约与 fencing token |
| `hourly_alpha_workflow.db` | batch、候选 checkpoint、单调状态和审计链 |
| `hourly_alpha_trials.db` | trial 身份、阶段 receipt、验证 artifact |
| `hourly_alpha_forward.db` | 小时 forward decision / realization 追加链 |
| `data_quality_admission.db` | forward 数据准入权威 |

不要手工修改或清空这些 SQLite 文件。候选、trial、artifact、workflow receipt、forward decision
通过 SHA-256 commitment 串联；任一链或投影不一致都应 BLOCK。

## 6. 只读状态

官方控制台提供：

```text
GET /api/v1/console/hourly-alpha
```

响应包含 `schema_version`、`as_of`、`freshness`、`watermark`、batch funnel、candidate states、
store 可用性和 safety。端点只用 SQLite `mode=ro` 打开工作流与 forward 权威，并校验追加链。
任何库缺失或损坏时，整体 `status=BLOCKED`，对应 store 显示 `UNAVAILABLE`；不会生成 mock。

## 7. 告警与值班判断

调度器应把进程 JSON 和退出码写入值班日志，并对以下情况告警：

- 退出码 2；
- 任一 store `UNAVAILABLE`；
- `freshness=STALE`；
- workflow 长时间停在 `RUNNING` 或 `VALIDATED`；
- forward decision / realization 链无效；
- coverage 连续失败、feature/return drift、成本膨胀或 reality gap 超阈值。

0 passer 本身不是进程故障，但必须保留为研究漏斗结果，不能改写成 PASS。

## 8. Checkpoint 与崩溃恢复

生成、OOS、ledger append 或 forward decision 后发生进程中止时：

1. 不删除数据库、receipt 或 lease 文件；
2. 等待 600 秒调度租约到期，或确认原进程已正常释放租约；
3. 用完全相同的 `--panel`、`--as-of`、`--budget`、代码版本和 policy 重跑；
4. 检查 receipt digest 与无故障运行一致；
5. 检查已完成阶段没有重复执行，trial 和 forward decision 没有重复行。

改变 panel、registry、policy 或 budget 会改变 batch 身份；对同一 batch 身份提供不同内容会被
`AlphaWorkflowConflict` 拒绝。不要通过改库“修复”冲突。

## 9. 暂停与退休

`services.alpha.hourly_maintenance` 只允许自动降险：

- 缺证据、链无效、前向样本不足、漂移、成本恶化、reality gap 或连续 coverage 失败 → `PAUSE`；
- 持续失败达到版本化 policy 阈值 → 先 `PAUSE`，之后才允许 `RETIRE`；
- 非人 actor 不得 APPROVE、恢复真钱状态或 reset kill-switch；
- 异常统一 fail closed 为 `PAUSE`；
- 自动恢复没有接线，恢复必须走独立人工评审。

运维人员只读取建议和审计，不得直接编辑 lifecycle/workflow SQLite 投影。

## 10. 7 天 soak

Wave 1 上线到 L2 调度后至少观察 7 天：

1. 每个计划时点只出现一个 batch receipt；
2. 同参数重跑 receipt digest 不变；
3. 无重复 trial、decision 或 realization；
4. freshness/watermark 单调推进；
5. 所有 BLOCK 都有阶段原因和完整证据 commitment；
6. coverage/drift/cost/reality-gap 告警可见；
7. 安全字段始终显示 `L2_DRY_RUN_ONLY`、`allow_order_submission=false`。

任何一天出现链损坏、未来知识、重复副作用、状态倒退或真钱能力暴露，都应停止调度并保留现场。

## 11. 验收命令

```powershell
rtk py -3.14 -m pytest -q -m "not slow" tests/integration/test_hourly_alpha_to_paper.py
rtk py -3.14 -m pytest -q -m slow tests/research/test_alpha_garden.py tests/integration/test_hourly_alpha_to_paper.py
rtk py -3.14 apps/research-console/scripts/run_paper_to_live_governance_check.py
rtk py -3.14 scripts/validate.py
```

只有全部门禁退出 0，且 readiness 仍为 `L2_DRY_RUN_ONLY`，Wave 1 才算完成。
