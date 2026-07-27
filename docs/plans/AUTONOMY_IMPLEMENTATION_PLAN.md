# QUANTOS 自治能力实施计划

本计划承接 `docs/QUANTOS_727.md`，当前范围限定在 L2 研究、干跑和纸面运行；任何真实资金、真实凭证、主网 broker 或真实订单能力仍然是结构性禁止项。

## 当前执行切片：Phase 0/1

### Phase 0：基线与安全冻结

目标是建立可重建的事实基线，而不是把历史快照重新包装成当前 PASS。

- 使用 `scripts/generate_autonomy_baseline.py` 生成 `evidence/autonomy_baseline.json` 和本计划配套的差距报告。
- 基线记录当前 commit、输入制品 SHA-256、SafetyConfig 配置哈希、机器状态和文档/机器证据冲突。
- 安全验收必须保持：`LIVE_TRADING_ENABLED=false`、`REAL_BROKER_ENABLED=false`、`ALLOW_ORDER_SUBMISSION=false`、`ALLOW_AI_DIRECT_TRADING=false`、`REQUIRE_HUMAN_APPROVAL=true`。
- `scripts/validate.py` 现在必须逐阶段输出开始/结束/耗时，支持 `--stage`、`--timeout-seconds`、`--json-report`，并把超时进程标记为失败。

### Phase 1：数据血缘与统一门禁

- 以当前 `evidence/data_quality_admission.json` 和 `evidence/raw_data_lineage_backfill.json` 为机器权威；不修改期望日期、不覆盖历史证据。
- 对 raw archive、canonical cache、REST gap-fill 和 8 日 forward 窗口继续做严格逐 ticker/day 对账。
- 数据准入或 lineage 任一失败时，依赖 Alpha 必须保持 `BLOCKED/PAUSED`，禁止用最后一次好数据生成新的风险或纸面成交。
- `evidence/validation_report.json` 是统一门禁的机器结果；只要完整 gate 未完成或任一阶段超时，Phase 0 不能记为全绿。

## 后续顺序

1. 生成并绑定三条 Alpha 的完整九引擎 Paper risk bundle；缺输入继续 `PAPER_BLOCKED`。
2. 在同一 PIT 快照上累计至少 8 个有效前向周期，并保留决策、成交模拟、费用、资金费、对账和审计链。
3. 完善跨会话、不可丢弃的 Trial Ledger 历史和模型风险审查身份；禁止改写既有历史制品。
4. 继续补齐前端阻断原因、后台 Job Registry 和运行时间线，但保持前端只读、高风险操作走独立审批 API。
5. 将外部身份、授权数据、tick/LOB 执行校准、跨主机恢复/封存和真实 broker 作为独立的 post-L2 外部项目，不用 mock 关闭这些门。

## 验收与证据

```text
rtk python scripts/generate_autonomy_baseline.py
rtk python scripts/validate.py --stage governance --timeout-seconds 1800
rtk python scripts/validate.py --json-report evidence/validation_report.json
```

必须同时检查：

- `evidence/autonomy_baseline.json`
- `docs/reports/autonomy_gap_analysis.md`
- `evidence/validation_report.json`
- `evidence/data_quality_admission.json`
- `evidence/raw_data_lineage_backfill.json`

测试绿只证明本地工程一致性，不会把 Paper、Shadow 或 L2 研究证据升级成实盘授权。

## 明确阻断

当前仍不能宣称 `paper_ready` 或真实订单可用：完整九引擎风险包、前向周期、外部模型/身份、授权数据、跨场所执行、外部签名封存等证据仍不完整。所有这些缺口必须在制品中显示为 `PENDING`、`BLOCKED` 或 `PARTIAL`，不可填默认值。
