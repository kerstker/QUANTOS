# QUANTOS 目标架构(TARGET_ARCHITECTURE)

> 审计日期:2026-07-06 · 目标:L4 执行自动化 + L3 风控边界 + L2 治理审批(**非 L5 全自主**)。
> 原则:在**不破坏现有 L2 安全不变量**的前提下,把已建好的原语逐个接到承重路径 + 补齐事件总线/进程隔离。
> 现状标注:✅=已生产级 · 🟡=已建但未接线/未承重 · 🔴=缺失待建。

---

## 一、设计不变量(非协商 — 目标架构永远保持)

1. `LIVE_TRADING_ENABLED / REAL_BROKER_ENABLED / ALLOW_ORDER_SUBMISSION / ALLOW_AI_DIRECT_TRADING = false`、`REQUIRE_HUMAN_APPROVAL = true` 为默认;实盘须多步真人批准。
2. **策略(意图)与风控(许可)分离**;broker 逻辑在适配器之后;研究/纸面/实盘严格隔离。
3. **AI/非人 actor 永不**:到 APPROVED、reset kill-switch、arm live、触碰凭证、下真实单。
4. **fail-closed 到底**:任何异常/缺失/损坏状态 → 拒绝/停机/升人工,绝不默认放行。
5. **真防御在进程外**(Phase 14):单进程内挡不住受信代码,故 mutating 决策最终须携带出进程权威签名。

---

## 二、目标服务分层(6 层 + 横切 3 面)

```
┌──────────────────────────────────────────────────────────────────────────┐
│  L6 治理层 Governance   多方人工批准 ✅ · 晋级闭环 ✅ · 出进程签名 🔴Phase14 │
│                         真身份认证/RBAC 🔴 · mandate 签名 🔴                │
├──────────────────────────────────────────────────────────────────────────┤
│  L5 运维层 Ops          风险哨兵 ✅ · GoNoGo 报告 ✅ · 实时盘中监控 🔴       │
│                         维护调度器 🟡 · 日报/告警 🟡 · 灾备/回滚 🟡         │
├──────────────────────────────────────────────────────────────────────────┤
│  L4 执行层 Execution    干跑流水线 ✅ · live testnet 栈 🟡 · OMS/EMS 🟡     │
│         ┌─────────────────────────────────────────────────────────────┐   │
│         │  [横切] Pre-Trade Risk Gate ✅ — 每笔订单必经,fail-closed    │   │
│         │  [横切] Kill Switch ✅ — 闩锁跨进程,横切每条下单路径         │   │
│         │  [横切] Audit / Event Sourcing 🟡 — 哈希链✅ 事件总线🔴       │   │
│         └─────────────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────────────────┤
│  L3 组合层 Portfolio    约束优化器 ✅ · 风险平衡 ✅ · firm 硬门 🟡          │
│                         资金分配 ✅ · 降权/退休 🟡 · 实盘 sizing 接线 🔴    │
├──────────────────────────────────────────────────────────────────────────┤
│  L2 研究层 Research     Alpha Factory ✅ · Validation Gate ✅ · 回测 ✅     │
│                         lineage 持久化 🟡 · 前向证据钟 ✅                    │
├──────────────────────────────────────────────────────────────────────────┤
│  L1 数据层 Data         PIT 存储 ✅ · 宇宙/复权/日历 ✅ · 泄漏检测 ✅        │
│                         质量强制门 🟡 · 实时/流式接入 🔴 · Feature Store ✅  │
└──────────────────────────────────────────────────────────────────────────┘
   [横切事件总线 EventBus 🟡]  严格 schema/单机 group/6 条 outbox/health/recovery 已承重;live + HA 待补
   [横切服务注册/配置 🔴]      统一 DI/服务发现 + 分环境配置
   [横切统一契约 quantos/contracts ✅]  frozen pydantic 领域模型,单一事实来源
```

**关键补齐**:
- 🟡 **EventBus**(横切):canonical Event + 严格 schema、三条追加链、单机 consumer group/DLQ、幂等 projection 和六条 transactional outbox 已承重；recovery drain + hourly ops 已闭合，三 owner PASS、三 authority UNAVAILABLE。独立 scheduler watchdog、L2 encrypted backup、三链 portable commitment/extension verifier、create-once external anchor seam 与 bounded HTTPS heartbeat sender 均已承重；当前外部配置缺失，故 6 PASS/1 PARTIAL/3 UNAVAILABLE，且锚点只证明 filesystem create-once、未证明 WORM/外部签名。仍需更广 producer、真实 off-host batch、operator-owned missed-heartbeat receipt、object lock、强 worker identity 与跨主机 broker/复制/HA。
- 🔴 **出进程签名 + 真身份认证/RBAC**(L6,Phase 14):晋级 API 已有 OIDC+mTLS+MFA+RBAC M2 边界；仍需外部部署证据，并让 mandate 变更/kill-switch reset/晋级携带独立进程签发的 `SignedDecisionRef`。
- 🔴 **实时盘中监控 + 容器化/环境隔离**(L5/L15):把周/小时级对账升级为实时;Docker + staging/prod 隔离。

---

## 三、依赖方向(目标:单向无环)

```
  L1 Data  ←  L2 Research  ←  L3 Portfolio  ←  L4 Execution  ←  L5 Ops  ←  L6 Governance
     ↑____________ 所有层依赖 quantos/contracts(叶子,零业务依赖) _____________↑
     ↑____________ 所有层向 EventBus 发事件(不反向依赖发布者) _______________↑
  live/  ──单向──▶ services/（services/ 永不 import live/,已由治理测试机器钉死 ✅）
```

**进度 / 待修**:确定性 service import 图与精确循环/动态加载策略已进入统一门；全部已知反向边已消除。research 的市场数据消费通过 PIT/version port，research→data 为零；experiment/run/paper/forward persistence 同样通过四个 runtime-checkable ports，SQLite 只存在于四个 adapters。验证编排通过必填 backtest port/output contract，walk-forward/CPCV 正本位于中立 validation；mutating runner 与 read-only HTTP routers 分属 `services/research_runner`、`services/console/api`，research 对 FastAPI/uvicorn/tools/data 零 import。console 的 15 个 router/44 paths 与 772 行 root 均有界。事件总线已有严格 schema、单机 consumer group/offset/DLQ、幂等 projection、六条同库 outbox、统一 recovery drain、L2-only hourly ops 和 alert-only lag/retention health；遗留 live-capable tasks 已禁用。当前全图 16 域/36 边，`cyclic_sccs=[]`、`cyclic_edges=[]`。下一步是修复 owner-store BREACH、补安全归档/外部 seal、独立告警 SLA、备份/watchdog 与跨主机 HA；图无环和单机调度仍不等于 Phase 14 进程隔离或跨主机 HA 已完成。

---

## 四、事件流(目标 — 统一事件总线承载全链路)

```
数据事件 ──▶ [DataQualityGate🟡→✅] ──▶ Feature/PIT Store ✅
                                              │
Alpha 假设 ──▶ AlphaFactory ✅ ──▶ Evaluator+ValidationGate ✅ ──(verdict=PASS)──▶ Lifecycle SM ✅
                                              │(fail-closed:证据 None→人工复核)
                                              ▼
                                   [多方三人批准 ✅]──▶ APPROVED(human-only ✅)
                                              │
组合权重 ──▶ Portfolio Optimizer ✅ ──(🔴接线)──▶ 目标仓位
                                              ▼
信号 ──▶ build_intents ✅ ──▶ ┌─ PreTradeRiskGate ✅(每笔,fail-closed)
                              ├─ KillSwitch ✅(横切,闩锁)
                              ├─ Mandate 分级授权 ✅(账户级聚合🟡→✅)
                              └─ 交易所过滤器 ✅
                                              ▼
                              [仅 gate-APPROVED ∧ mandate-AUTO] ──▶ OMS/EMS ▶ venue.place_order
                                              ▼
                              确认轮询(get_order 终态)✅ ──▶ 账本记账 ✅(孤儿窗口🟡→✅)
                                              ▼
                              对账 preflight(drift 硬停)✅ · parity · TCA ✅
                                              ▼
           ┌────────────── 全程发事件到 EventBus 🟡(部分 producer 已承重)─────────────────┐
           │  每个节点:决策原因 + risk_snapshot + market_snapshot,append-only,可回放       │
           └───────────────────────────────────────────────────────────────────────────────┘
                                              ▼
                    异常/越限 ──▶ RiskSentinel ✅ ──▶ KillSwitch.arm ✅ / 紧急减仓 ✅(只减敞口)
```

---

## 五、风控横切路径(强制不可绕过)

每一条会动仓位的路径都必须穿过这三道横切控制,任一 fail-closed:

1. **Pre-Trade Risk Gate**(`services/risk/pre_trade/gate.py` ✅):23 检查 fail-closed;kill-switch 是 CHECK_REGISTRY #1;绑定权威状态调用方无法篡改;异常/审计失败/非终态决策 → 拒绝。
   - **待接线**:live ctx 喂真实 ADV/波动/历史指纹/时间戳,让流动性/波动/重复/频率引擎承重(P1-5)。
2. **Kill Switch**(`kill_switch.py` + `kill_switch_store.py` ✅):闩锁、跨进程持久化、只加不减防竞态、损坏 fail-closed、human-only 三方复位。
3. **Mandate 分级授权**(`live/governance/mandate.py` ✅):gate 之后第二层,限额内 AUTO / 越限升人工;缺失/异常/venue 不匹配 fail-closed。
   - **待改**:gross/net 按账户级 +多策略聚合投影(P1-2);变更须签名(P0-1)。

**唯一刻意的 gate 旁路** = 紧急减仓通道(`emergency.py`):结构性只能减敞口(方向相反 + 量≤持仓 + 强制 reduce_only + 交易所侧 reduceOnly),这是"熔断不能挡去风险"的正确设计,由专属治理测试钉死"不 import 共用 gate/kill-switch"。

---

## 六、交易生命周期(Order State Machine)

```
  NEW ──▶ SUBMITTED ──▶ ACKED ──▶ PARTIALLY_FILLED ──▶ FILLED
                          │            │                   ▲
                          ├──▶ REJECTED│                   │
                          └──▶ CANCELED ◀── (超时撤单+回查权威态 ✅)
```
- 稳定确定性 `client_order_id`(PRIMARY KEY ✅);**待修**同数量碰撞(P1-3)。
- 不信同步 ACK,强制 `get_order` 轮询到终态才记账 ✅。
- 崩溃恢复按增量(非累计)补记 ✅;**待修**place-后-未落库孤儿窗口(P1-4)。

## 七、策略生命周期(资本分档晋级 — 🟡 部分,待建资本档状态机)

```
  RESEARCH ──▶ VALIDATED ──▶ PAPER(前向证据钟≥8周✅) ──▶ [Go/No-Go 人工评审]
                                                              │
                                              🔴 SHADOW(影子实盘,未建)
                                                              ▼
                                     🔴 SMALL_CAPITAL($3-5k)──▶ 🔴 SCALED
                                     (当前靠人改 mandate 数字,无独立资本档状态机)
```
每次升档 = 独立人工门 + 新签 mandate + 前向证据 + 自动提交演练。**待建**:资本分档状态机把"从小资金到加仓"形式化,而非靠人工改 mandate 数字。

## 八、Alpha 生命周期(10 态哈希链审计 SM ✅)

```
  DRAFT ─▶ TESTING ─▶ VALIDATED ─▶ PAPER_TRADING ─▶ SHADOW ─▶ APPROVED(human-only ✅)
             │            │              │              │           │
             └─▶ REJECTED └─(WARN/FAIL)  └────▶ DEGRADED/PAUSED ◀────┘──▶ RETIRED
```
- 邻接表机器强制不能跳级 ✅(`alpha.py:139`);非人到 APPROVED 结构性 raise ✅(`alpha.py:192`)。
- 单一转换 chokepoint:结构锁 → 证据 guard → 哈希链审计 ✅(`guards.py:54`)。
- **待接线**:lineage 育种落 genealogy(P3-4);衰退→退休自动调度(P1-7)。

## 九、Kill Switch 架构(✅ 生产级,少量增强)

```
  触发(arm):任何 actor(SYSTEM/HUMAN/AGENT)可停 ✅ ──▶ 闩锁 tripped
       │ 全局 / 策略 / 账户 / 品种 / 风控 级 scope(scope_value 匹配)✅
       │ 跨进程持久化(kill_switch_store,只加不减防竞态)✅
       │ is_active() 损坏状态 fail-closed 读作 tripped ✅
       ▼
  横切:CHECK_REGISTRY #1,每笔 gate.evaluate 都查 ✅;驱动器 pre-gate 硬停 ✅
       ▼
  复位(reset):HUMAN only + 绑定事件的多方三人 CONSUMED grant ✅;永不自动复位 ✅
```
**增强**:🔴 品种/账户级细分开关的独立运维工具;🔴 reset 须出进程签名(Phase 14)。

## 十、Audit / Replay 架构(🟡 哈希链 + 单机事件回放✅，跨主机 HA/外部 seal 未完成)

```
  当前 ✅:GlobalAuditLog(内存哈希链 seal/verify)+ DurableAuditLog(持久)
           + append-only position_ledger + 不可变 experiment_registry 制品(verify_evaluation 防篡改)
           + 确定性 reproducibility_hash(无墙钟/RNG)

  目标 🟡:统一 EventBus,单机可靠消费已承重;继续让每事件带 {event_id, correlation_id, causation_id,
           strategy_id, alpha_id, signal_id, intent_id, order_id,
           risk_snapshot, market_snapshot, decision_reason},append-only,
           支持:① 每笔交易"为什么发生"完整溯源;② 复现当时系统状态;
                 ③ 回测与实盘事件对齐;④ 事故 replay 复盘。
```

---

## 十一、从当前到目标的最短安全路径(概述,详见 IMPLEMENTATION_ROADMAP)

1. **冻结事实**(本审计)→ 2. **收口 P1 LATENT 工程缺口**(让已有原语承重,全 fail-closed 加法)→ 3. **数据质量强制门 + Feature/Alpha 持久化** → 4. **验证门作 live 前置 + OMS/EMS 订单完整性收口** → 5. **组合优化器接实盘 sizing + firm 硬门 + 退休调度** → 6. **事件总线 + 实时监控 + 容器化** → 7. **Phase 14 进程隔离/出进程签名/真身份认证 + mandate 签名** → 8. **Phase 3A Go/No-Go 人工评审**。

**任何一步都不解锁真钱**:真钱由 Phase 3A 人工门 + 五旗帜 + Phase 14 信任边界共同守护,工程收口只是把"放行当天会转正的 LATENT 缺口"提前补好。
