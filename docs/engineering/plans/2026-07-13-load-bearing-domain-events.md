# 2026-07-13 承重关键领域事件链

状态：P2-5 的关键 producer 与可验单节点事件链已落地；全面解耦和生产事件平台仍开放。

## 原问题

仓库已有 canonical `Event` 和 `DurableEventBus`，但只有总线单测，没有任何 service producer。
业务仍全部依赖直接 import 和共享 SQLite，因此“事件总线”不能作为真实能力证据。

## 已实现

- `DurableEventBus` 每行覆盖完整 Event、correlation/causation 和前序记录摘要，形成 SHA-256 链。
- SQLite 使用 WAL + FULL synchronous + `BEGIN IMMEDIATE`；多个连接追加时先刷新并验证当前链头。
- UPDATE/DELETE 触发器保持 append-only；绕过触发器篡改后，全链回放和未来发布失败关闭。
- v1 无链表可原地补链迁移；部分迁移或混合空链拒绝打开。
- `publish` 严格拒绝重复；`publish_once` 只允许 event + 两个因果 ID 完全相同的确定性重跑。
- `services/execution/events.py` 构造 dry-run started、逐意图 pretrade decision、completed 事件。
- 单期和多期 pipeline 在 fill 前写入 gate decision；事件失败时 run 不返回、不落 DryRunStore。
- 七个正式 dry-run driver 通过 `tools/domain_event_bus.py` 共用 `data/cache/domain_events.db`。
- `services/alpha/maintenance_events.py` 将非人维护的 DEGRADED/PAUSED/RETIRED 结构化写入同一总线。
- 单元测试默认不传 bus，保持纯测试无工作区 IO；正式 driver 才打开持久库。

## 验收

- 16 项总线测试覆盖顺序、因果过滤、订阅 persistence-first、重复、幂等、跨重启、双连接、
  旧库迁移、触发器和绕过篡改。
- 干跑测试覆盖批准/拒绝/完成因果链、零 fill 拒绝、确定性重跑和持久库重开。
- alpha 测试验证结构化衰退指标进入 transition event。
- 治理测试钉死两个 service producer 实际 import canonical Event，七个 driver 与维护 sweep 均接线，
  event-bus tool 无网络/交易能力。
- 聚焦扩展套件：162 passed；Ruff 与 `git diff --check` 通过。
- 最终统一门禁：主套件 2129 passed / 1 skipped；live/trading/tools 902 passed；
  前端 68 tests + typecheck/build；治理扫描 249 文件零违规。

## 仍开放

1. 同步函数调用仍是权威路径；事件目前不驱动业务状态，只提供持久证据与回放。
2. kill-switch、testnet/live pre-trade、promotion/deployment 尚未成为 producer。
3. 没有跨进程 consumer、offset、outbox、schema registry、死信、背压或重放隔离。
4. SQLite 单节点不等于 HA broker；跨主机复制与长期 SLO 需要真实部署环境。

本项保持 `L2_DRY_RUN_ONLY`，不增加网络、券商、凭证、批准或订单提交权限。
