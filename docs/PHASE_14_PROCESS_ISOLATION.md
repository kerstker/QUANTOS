# Phase 14 — 进程隔离 / 出进程签名权威(威胁面与架构)

> **L2 免责:** 本文描述的是 **post-L2 的目标架构**。当前 QUANTOS 处于 `L2_DRY_RUN_ONLY`,只落一个
> **结构性拒绝的签名接缝**(`services/governance/signing.py`):进程内无密钥、`sign()` 即拒、
> `verify_signed_ref` 恒 False。**不建真跨进程签名器、不持任何密钥材料、不接进任何执行路径、不动五个
> LIVE 标志。** 真出进程签名权威 + 真进程隔离部署 = post-L2、人工门控 = 显式非目标。

## 1. 核心事实:单进程内挡不住受信代码

QUANTOS 的运行时安全控制(agent guardrails、alpha lifecycle 状态机、pre-trade 门、干跑守卫、多方批准)
都是对**传入的 actor 与 evidence** 的运行时控制。它们挡得住一个**非人 actor** 走正常路径去晋级/复位/发单。

但它们——**在单进程内、根本上**——挡不住 **受信代码(TRUSTED CODE)**:

- 受信代码能**重算无密钥内容哈希**(`ExecutionBoundary.verify_ref` / `PreTradeRiskGate.issue_ref` 都是
  unkeyed 内容绑定,不是密码学证明);
- 能**直接构造一个 `HUMAN` `Actor`**,凑齐多方批准要求的三个不同真人;
- 能**直接调** `AlphaLifecycleManager` / `KillSwitch` / `catalog._set_state`,绕过 chokepoint;
- 能**读到同进程里的任何密钥**,于是任何"进程内签名"都是自欺。

这是硬事实,代码库里到处诚实地写着这句注脚。结论只有一个:**真防御只能在进程外。**

## 2. 真防御:出进程签名权威

把"授权"从研究进程里搬出去,交给一个跑在**独立信任域**的**签名权威**:它持有私钥,研究进程够不到;
任何"允许越过 L2 的动作"都需要一个由该权威**签发**的 `SignedDecisionRef`,执行进程用**公钥独立验签**。
研究进程即便被受信代码完全支配,也**产不出**一个有效签名(它没有私钥),于是绕不过这道门。

`services/governance/signing.py` 把这个边界形式化(L2 下是拒绝式空壳):

- `SignedDecisionRef`(纯值:`payload_hash` / `signature` / `key_id` / `issued_by`)—— 一个出进程签发的
  授权引用长什么样;
- `SigningAuthority`(Protocol:`is_out_of_process` / `key_id` / `sign`)—— 权威接缝;
- `NullSigningAuthority`(L2 默认)—— 无密钥、`sign()` 即 `raise SigningUnavailable`;
- `verify_signed_ref(ref, payload_hash, authority)` —— fail-closed;L2 下无出进程权威,恒 False。

## 3. 受信代码可绕过面 × 出进程签名权威怎么堵

| # | 单进程内可绕过的面 | 为什么单进程挡不住 | 出进程签名权威怎么堵(post-L2) |
|---|---|---|---|
| 1 | `ExecutionBoundary.verify_ref`(`services/execution/boundary.py`) | ref 是**无密钥**内容哈希,受信代码可重算 | `admit` 在 readiness>L2 时额外要求一个经出进程权威验证的 `SignedDecisionRef`;私钥不在研究进程,重算不出签名 |
| 2 | `PreTradeRiskGate.issue_ref`(`services/risk/pre_trade/gate.py`) | 内容绑定、非密码学证明 | 决策内容哈希由出进程权威签,执行进程用公钥独立验签 |
| 3 | `approval.verify_multi_party_grant`(`services/governance/approval.py`) | 受信代码可凑三个 `HUMAN` `Actor` | grant 的内容哈希须由出进程权威签发,单靠进程内凑 Actor 产不出签名 |
| 4 | 直接调 `AlphaLifecycleManager`/`KillSwitch`/`catalog._set_state` | 受信代码可绕 chokepoint 直接改状态 | 越过 L2 的状态迁移须携带出进程签发的授权 ref;无签名的迁移被执行进程拒绝 |
| 5 | `assert_dry_run_adapter`(`services/trading/broker_adapter.py`) | 运行时防误用,非信任边界 | 「换真适配器」的授权须出进程签发 + 人工门控,受信代码换不了 |

## 4. 架构(三进程 / 三信任域)

```
┌────────────────────────┐        payload_hash         ┌──────────────────────────────┐
│  研究进程 (RESEARCH)     │ ─────────────────────────▶ │  签名权威进程 (AUTHORITY)      │
│  · 跑搜索/回测/干跑       │                             │  · 持私钥(可能 HSM)           │
│  · 顶多拿公钥,**无私钥** │ ◀───────────────────────── │  · 人工门控:多步人工批准后才签  │
│  · 受信代码即便被支配,   │       SignedDecisionRef     │  · 与研究进程强隔离(独立信任域)│
│    也产不出有效签名       │                             └──────────────────────────────┘
└───────────┬────────────┘                                              │
            │ SignedDecisionRef                                          │ 公钥(仅验证用)
            ▼                                                            ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  执行进程 (EXECUTION):admit 前 verify_signed_ref(ref, payload_hash, pubkey_authority)  │
│  · 只放行携带有效出进程签名的动作;私钥永不进本进程                                       │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

关键性质:**私钥永不进入研究进程**。研究进程被完全支配的最坏情况下,攻击面止于"能请求签名",而签名需经
**签名权威进程的人工门控**——那是轨道3 子项目2(多步人工批准状态机)在出进程语境下的落点。

## 5. post-L2 迁移路径

1. 部署签名权威为**独立进程/服务**(独立信任域,私钥在 HSM/KMS,研究进程无凭证)。
2. `ExecutionBoundary.admit(intent, ctx, decision, ref, signed_ref)` 在 `readiness > L2` 时额外要求
   `verify_signed_ref(signed_ref, payload_hash, pubkey_authority)` 为真;`payload_hash` = 该动作对应的
   approval grant / risk 决策的内容哈希(把子项目2 的 grant、pre-trade 决策接上)。
3. 签名权威只在**多步人工批准**(子项目2 状态机)达成 `CONSUMED` 后才 `sign`;签名把"人已批准"这件事
   变成研究进程伪造不了的密码学事实。
4. 治理扫描器 + code review 继续守 build-time 面;进程隔离守 runtime 面。两者叠加 = 完整防御。

## 6. 为何 L2 下不接线(诚实决定)

L2 下**没有真出进程权威**。若此刻把"必须验签"强行接进 `ExecutionBoundary.admit` 或干跑流水线:

- 要么它是**死代码**(永远拿不到有效 `signed_ref`,分支从不触发);
- 要么它把**能跑的干跑流水线全堵死**(每笔都因无签名被拒)。

两者都不诚实——一个假装有防护,一个破坏已验证能跑的系统。所以 L2 下签名接缝是个**独立的、可测的、
会拒绝的**制品 + 本文档,与 `LiveBrokerAdapter`(同样不接任何 live 路径、只结构性拒绝)完全对称。真接线
是 post-L2 的事,由本文 §5 描述。

## 7. 与轨道3 前两子项目的关系

轨道3 = 通往实盘的安全脚手架,三块合起来才是完整骨架:

- **子项目1(券商接缝,`broker_adapter.py`)** —— 把"换适配器"形式化 + 运行时防误用守卫;
- **子项目2(多方批准,`approval.py`)** —— 把"人工批准"抬到三个不同真人 + 哈希链审计,接进现有两门;
- **子项目3(本篇,`signing.py` + 本文档)** —— 画出**唯一能挡受信代码**的那层(出进程签名权威),
  并诚实标注它是 post-L2。

子项目1、2 在单进程内把**运行时误用**挡住;子项目3 指明:要挡**受信代码**,只能出进程——这正是
CLAUDE.md 威胁模型里"(Phase 14)deployment/process isolation"那句话的兑现(以设计 + 拒绝式接缝的形式,
而非真上实盘)。
