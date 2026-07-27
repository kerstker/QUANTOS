# 三个正交状态机设计与迁移方案（727 §3 / §11）

- 日期：2026-07-27
- 依据：[`docs/QUANTOS_727.md`](../../QUANTOS_727.md) §3（三个正交状态机）、§11（生命周期控制器）
- 状态：**C1（核心机器 + 测试）、C2（双写持久化）与 C3（health 机器接入
  维护链路）已落地**,C4（console 三维展示）待做。落地分 4 个独立提交,
  见 §6;实施记录见 §7.1/§7.2/§7.3（C3 含行为变化评审清单）。
- readiness：L2_DRY_RUN_ONLY 不变。本方案不改五个安全旗标，不新增任何真实下单路径。
- 前置：[`2026-07-27-trial-provenance-and-family-fdr-design.md`](2026-07-27-trial-provenance-and-family-fdr-design.md) §9 把本项列为下一最高优先级。

## 1. 问题：一个字段编码了三台机器

`quantos/contracts/research.py::AlphaState` 是**单一混合字段**，10 个值：

```text
DRAFT  TESTING  VALIDATED  PAPER_TRADING  SHADOW
APPROVED  DEGRADED  PAUSED  RETIRED  REJECTED
```

§3 开篇即明令「不得再用一个状态字段混合研究、部署和健康」。逐值归属后，
**三个值各自跨了两台机器**：

| 当前值 | ResearchState | DeploymentState | HealthState |
|---|---|---|---|
| `DRAFT` | DRAFT | OFFLINE | — |
| `TESTING` | TESTING | OFFLINE | — |
| `VALIDATED` | VALIDATED | OFFLINE | — |
| `REJECTED` | INVALIDATED | CLOSED | — |
| `RETIRED` | RESEARCH_RETIRED | CLOSED | — |
| `PAPER_TRADING` | （隐含 VALIDATED） | PAPER | — |
| `SHADOW` | （隐含 VALIDATED） | SHADOW | — |
| **`APPROVED`** | （隐含 VALIDATED） | **LIVE** | — |
| **`DEGRADED`** | — | **上一部署位（信息已丢）** | **DEGRADED** |
| **`PAUSED`** | — | **PAUSED** | **原因未记录** |

### 1.1 具体损失，不是洁癖

§3.4 举的五个组合状态里有三个**在单字段模型下无法表达**：

```text
研究有效但交易所断线   VALIDATED + PAUSED + FAILED_EXECUTION
研究有效但 Regime 不合  VALIDATED + PAUSED + DEGRADED_REGIME
数据血缘失败           VALIDATED + PAUSED + FAILED_DATA
```

这三个都要落到同一个 `PAUSED`。后果是**运维层面的**：值班的人看到 `PAUSED`
无法回答「为什么停的、根因消除了吗、能恢复吗」——而 §11.3 的恢复流程
（根因消除 → 自动重放验证 → Shadow 冷静期 → 恢复申请 → 人工批准）
第一步就需要知道根因。今天这个信息只能从审计链里反推。

同理 `DEGRADED → SHADOW`（`ALPHA_ALLOWED_TRANSITIONS`）把「健康恢复」和
「退回影子部署」压成一条边：健康好转必然伴随一次部署位移动，即使部署位本来就在 SHADOW。

### 1.2 反过来说：拆分是**安全性**收益，不只是整洁

`quantos/contracts/alpha.py::AGENT_REACHABLE` 现在是一张**黑名单**：

```python
AGENT_REACHABLE = {TESTING, VALIDATED, PAPER_TRADING, SHADOW,
                   DEGRADED, PAUSED, REJECTED, RETIRED}   # 独缺 APPROVED
```

它靠「把 APPROVED 排除在外」来保证非人类 actor 到不了实盘。这是对的，但它是
**枚举式**的：将来加任何一个新状态，作者必须记得判断要不要加进这张表；忘了加
= 默认禁止（这个方向安全），但如果误加一个授予实盘权限的新状态 = 静默放开。

三机器模型下，这条不变量变成**结构性**的：

```text
ResearchState   —— 全部边非人类可达（研究本来就是 agent 的领地）
HealthState     —— 全部边非人类可达（健康是观测；自动降险是 §11.2 明确许可的）
DeploymentState —— 唯一带人类专属边的机器，且专属边恰好是「提升风险」那几条
```

即 §1.1「运营自治，资本受控」的分界线**与机器边界重合**，不再依赖一张要人工维护
的白/黑名单。这是拆分最强的论据。

## 2. 两条硬约束：历史值不可重写

调研 `services/alpha/lifecycle.py` 与 `services/alpha/guards.py` 后发现两处
承重耦合，它们决定了迁移方式**必须是加法投影，不能是替换**。

### 2.1 状态值在哈希链里

`AlphaLifecycleManager.advance()` 把状态值字符串直接喂进证据哈希：

```python
evidence_hash = stable_hash({
    "alpha_id": alpha_id, "src": src.value, "dst": dst.value, ...
})
```

`AlphaTransition` 自带 `prev_hash` 链。改任何一个枚举值（例如 `APPROVED` → `LIVE`）
会让**所有历史转换的哈希重算失败** —— 等于宣布既有审计链无效。这不可接受：
篡改证据链正是 §0.4.3 禁止的。

### 2.2 状态值被绑进已签署的多方批准

`services/alpha/guards.py`：

```python
PROMOTION_SUBJECT_PREFIX = "alpha-promotion"
def promotion_subject(alpha_id, dst): return f"{PREFIX}:{alpha_id}:{dst.value}"
def parse_promotion_subject(subject): ...; return alpha_id, AlphaState(parts[-1])
```

grant 的 subject 是字面串 `alpha-promotion:{alpha_id}:APPROVED`，反解时执行
`AlphaState(parts[-1])`。§1.2 规定「任意一项变化，授权立即失效」—— 这个行为是**对的**，
但意味着改枚举值会让 `approvals.db` 里所有已由三个真人签署的 grant 一次性**解析不了**。
这也不可接受：那些是真人的签名，不能被一次重构注销。

### 2.3 由此得出的裁定

> `AlphaState` **保留为历史身份与审计身份**；三台新机器作为**今后的权威**并存。
> 不重写任何历史值，不重签任何 grant。

这与 §7 里 `trial_provenance` 用**伴生表**而非给 `trials` 加列是同一个判断：
承重的旧身份不动，新语义旁挂。

## 3. 目标模型

新增 `services/lifecycle/`（§11 指定路径），三个枚举 + 三张转换表：

### 3.1 ResearchState

```text
DRAFT → TESTING → VALIDATED → CHALLENGED → VALIDATED   (可循环)
                            ↘ INVALIDATED → RESEARCH_RETIRED
```

`CHALLENGED` 是当前模型**完全没有**的状态（§3.1 要求，§10.2 Challenger 流程依赖它）。
今天 Champion 被检测到衰退后没有「正在被挑战」这一态可记。

### 3.2 DeploymentState

```text
OFFLINE → PAPER → SHADOW → MICRO_LIVE → LIVE
任意部署态 → PAUSED        (自动允许)
PAUSED → PAPER / SHADOW / MICRO_LIVE   (回真钱须重新人工批准)
任意态 → CLOSED            (不可原地恢复)
```

与当前模型的差异：
- 新增 `MICRO_LIVE`（§12.3）—— 当前 `AlphaState` 刻意没有实盘态，注释写明
  「promoting capital into live 是 L3 边界」。**本方案保持这一点**：`MICRO_LIVE`/`LIVE`
  在 L2 下是**结构性不可达**的枚举成员，进入它们需要人工签名的 Deployment Grant
  **且** readiness 高于 L2。定义枚举值 ≠ 开放路径，与 `LiveBrokerAdapter`
  的拒绝式接缝同构。
- `CLOSED` 明确「不得原地恢复，只能新建 deployment instance」（§3.2），
  当前 `RETIRED: frozenset()` 只做到「无出边」，没有「必须新建实例」的表达。

### 3.3 HealthState

```text
HEALTHY → WATCH → DEGRADED → FAILED   (可回退，回退不改部署位)
```

每个非 HEALTHY 态**必须**带 §3.3 的 11 个原因码之一：

```text
DATA_STALE  DATA_LINEAGE_BROKEN  SIGNAL_DECAY  REGIME_MISMATCH
COST_INFLATION  SLIPPAGE_BREACH  POSITION_MISMATCH  ORDER_UNKNOWN
BROKER_DISCONNECTED  RISK_BREACH  MODEL_INVALIDATED
```

fail-closed：无原因码不得进入非 HEALTHY 态（与 §7 里
「终态失败必须带 failure_reasons」同一条原则 —— 没人能复核的降级不是审计轨迹）。

### 3.4 组合不变量（机器可测）

```text
DeploymentState ∈ {PAPER, SHADOW, MICRO_LIVE, LIVE}  ⟹  ResearchState ∈ {VALIDATED, CHALLENGED}
ResearchState = INVALIDATED                          ⟹  DeploymentState ∈ {PAUSED, CLOSED}
HealthState = FAILED                                 ⟹  DeploymentState ∈ {PAUSED, CLOSED}
DeploymentState 提升 (PAPER→SHADOW→MICRO_LIVE→LIVE)  ⟹  actor 为 HUMAN 且有有效 Grant
DeploymentState 下降 / → PAUSED / → CLOSED           ⟹  任何 actor 皆可（§11.2 自动降险）
HealthState ≠ HEALTHY                                ⟹  reason_code 非空
```

第 4、5 条是承重的：**自动降险、人工增险**。这也是 §1.2 那 9 条
「系统不得自动完成」在三机器模型下的完整表达。

## 4. 投影：新旧如何共存

`services/lifecycle/projection.py`（纯函数，无 IO）：

```text
project(AlphaState) -> (ResearchState, DeploymentState, HealthState | None)
```

投影表即 §1 的表。三处有信息损失，**必须显式返回「未知」而不是猜**：

| 旧值 | 损失 | 处理 |
|---|---|---|
| `DEGRADED` | 上一部署位 | `DeploymentState = UNKNOWN_LEGACY`，不猜 SHADOW |
| `PAUSED` | 暂停原因 | `HealthState.reason = UNKNOWN_LEGACY` |
| `PAPER_TRADING`/`SHADOW`/`APPROVED` | 研究态是否 CHALLENGED | 记 `VALIDATED`（保守：不假设被挑战过） |

`UNKNOWN_LEGACY` 是**诚实的哨兵值**，不是 bug。它标记「这条历史记录写入时该维度
没被记录」，与 §7 里拒绝回填 2533 条历史 provenance 是同一立场：当时没记的，
事后编造即伪造。新写入的记录一律三维齐全，`UNKNOWN_LEGACY` 只可能出现在
迁移前的历史行上，并有测试钉死这一点。

## 5. 每处调用点的影响

调研得到的全部承重调用点（`grep AlphaState\.` 去掉测试）：

| 文件 | 用法 | 迁移动作 |
|---|---|---|
| `quantos/contracts/alpha.py` | `ALPHA_ALLOWED_TRANSITIONS` / `AGENT_REACHABLE` / `ALPHA_TERMINAL_STATES` | **保留不动**（历史身份）；三张新表并存 |
| `services/alpha/lifecycle.py` | `advance()` 按 `dst` 单枚举分派 guard | 加 `advance_research/deployment/health` 三入口；旧 `advance` 一字不动 |
| `services/alpha/guards.py` | `promotion_subject(..., APPROVED)` | **不动**（见 §2.2）；新增 grant 绑 `DeploymentState` |
| `services/alpha/catalog.py` | `_state` 单字典、`by_state`、`approved_set` | 加三个并行字典 + `by_deployment/by_health`；旧方法保留 |
| `services/alpha/degradation_monitor.py` | `_STATE` 把 OK/DEGRADED/PAUSED/RETIRED 映到 `AlphaState` | 改映到 `HealthState` + 原因码（**这是本项最大真实收益**：监控器本来就在算健康，却被迫写成混合态） |
| `services/alpha/maintenance.py` | `_LIVE_STATES` 含 APPROVED/PAPER/SHADOW/DEGRADED | 改成 `DeploymentState ∈ {PAPER,SHADOW,MICRO_LIVE,LIVE}`（`DEGRADED` 本不该在这张表里 —— 它是健康态混进了部署判断） |
| `services/alpha/promotion.py` | `PROMOTABLE_DSTS = {APPROVED}` | 加 `DeploymentState` 版；旧集合保留 |
| `services/alpha/retirement.py` | `advance(..., RETIRED)` | 改走 `DeploymentState.CLOSED` + `ResearchState.RESEARCH_RETIRED` 两步 |
| `services/alpha/factory.py`、`services/intelligence/agent_loop.py` | 驱动 TESTING/VALIDATED/PAPER/REJECTED | 改走 research/deployment 双入口 |
| `services/promotion_api/main.py` | `_CANDIDATE_STATES` | 改按 `DeploymentState` 过滤 |
| `services/alpha/registry.py`、`quantos/contracts/alpha_asset.py` | `AlphaAsset.status: AlphaState` | 加三个可选字段；`status` 保留 |
| `services/console/api/*` | 契约里暴露 `state` | 加三个字段；`state` 保留（契约测试锁两侧同 commit 改） |
| `data/cache/experiment_registry.db::alpha_state` | 单 `state` 列 | 惰性迁移加 3 列 + 3 原因码列，nullable 无 DEFAULT（历史行诚实读回 NULL） |

`services/alpha/degradation_monitor.py` 和 `maintenance.py` 那两行是**本项价值的证明**：
衰退监控器算出来的本来就是健康信号，却因为只有一个状态字段而不得不写成
部署态；而 `maintenance` 的「实盘态清单」里混进了 `DEGRADED` 这个健康值。
拆分后这两处各自变短、变对。

## 6. 分 4 个提交落地

每个提交自身门禁全绿、可独立回滚。

| # | 内容 | 行为变化 | 规模 |
|---|---|---|---|
| **C1** | `services/lifecycle/{research_state,deployment_state,health_state,projection}.py` + 三张转换表 + 投影 + §3.4 六条不变量的测试 | **零**（无人调用） | M |
| **C2** | `alpha_state` 表惰性迁移加 6 列；`catalog` 三个并行字典；双写（新旧同事务）；读时新列缺失即走投影 | **零**（旧读路径仍权威） | M |
| **C3** | `advance_research/deployment/health` 三入口 + per-machine guard 分派；`degradation_monitor`/`maintenance` 切到新机器 | **有**：自动降险改走 health 机器 | L |
| **C4** | console 契约 + 前端三维展示（§18.3 Decision/Reason/Evidence/Blocking/Required Human Action） | 只读展示 | M |

**C3 是唯一改变运行行为的提交**，需单独评审。C1/C2 是纯加法。

## 7. 必须证明的不变量（测试清单）

除 §3.4 六条外：

1. `project()` 对全部 10 个旧值有定义，且三处损失返回 `UNKNOWN_LEGACY` 而非猜测值。
2. 非人类 actor 无法把 `DeploymentState` 往上提升（穷举全部边 × 全部 actor 类型的网格测试）。
3. `HealthState` 任何非 HEALTHY 态缺原因码即拒绝。
4. `CLOSED` 无出边；恢复必须产生**新** deployment instance id。
5. 迁移后历史 `AlphaTransition` 链**仍可重算通过**（直接回答 §2.1）。
6. 迁移后 `approvals.db` 里已签署 grant **仍验证通过**（直接回答 §2.2）。
7. 双写期间新旧两套状态**不会互相矛盾**（投影(旧) == 新，逐行）。
8. 727 §20.2 第 14 条：`RESEARCH_RETIRED` / `CLOSED` 不能原地复活。
9. 727 §20.2 第 16 条：strategy_hash / config_hash 变化后 Grant 失效。

第 5、6 条是本方案的**验收核心** —— 它们证明这次重构没有注销任何既有证据或签名。

## 7.1 C1 实施中发现：旧机器从未建模 §12 的分级上线（真实发现）

写 §7 第 7 条那个「每条历史边都要能投影成合法新边」的测试时，它**抓到三条投影不合法的旧边**：

```text
PAPER_TRADING → APPROVED     投影成 PAPER      → LIVE
SHADOW        → APPROVED     投影成 SHADOW     → LIVE
PAUSED        → APPROVED     投影成 PAUSED     → LIVE
```

三条是同一个缺陷：**旧机器允许人类从纸面/影子/暂停态直接授权实盘，中间没有
`MICRO_LIVE` 这一级。** 而 §12.4 明确要求「只有 Micro-Live 经过足够真实成交、对账、
故障恢复和 Reality Gap 验证后」才能生成 Live 晋级建议；§11.2 更是直接把
`PAUSED → LIVE` 列为系统与人都不得走的捷径。

L2 下这三条是无害的（五个旗帜结构性挡死执行），但它意味着**状态机本身从来没有
表达过 §12 的分级上线**。这不是投影的 bug，是旧模型的缺陷被投影照出来了。

裁定：**不把这三条加进新机器**（那等于把旧的宽松度进口过来，直接废掉 §12.4）。
它们作为**已声明的收紧**记录在 `tests/lifecycle/test_legacy_identity_preserved.py::
_INTENTIONAL_DIVERGENCES`，并用**双向相等**断言钉死 —— 新出现未声明的分歧会失败，
已声明的分歧悄悄消失（= 新机器被放松回旧宽松度）也会失败。

**对 C3 的影响（必须在 C3 评审时单独确认）**：采用新机器后，一条今天能走
`PAPER_TRADING → APPROVED` 的 alpha 将必须依次经过 `SHADOW → MICRO_LIVE → LIVE`。
这是 §12 要求的正确行为，但它是**真实的行为收紧**，不是纯重构。C3 不能当作
「只是换个类型」提交。

## 7.2 C2 落地记录（2026-07-27，提交见 git log `feat(lifecycle): ... C2`）

- `alpha_state` 表惰性迁移：`_ensure_triple_columns()` 在**首次 catalog 写**
  （`save_catalog_snapshot` / `save_evaluation_catalog_snapshot` / `upsert_alpha`）
  时 `ALTER TABLE` 加 6 列（`research_state`/`deployment_state`/`health_state`
  + 三个 reason 列），**全部 nullable 无 DEFAULT**。纯读路径不触发迁移；
  `all_alphas()` 按 `PRAGMA table_info` 动态选列，旧库读回 NULL。
- 双写：`registry.save()` / `save_evaluation_context()` 把 `project(legacy)`
  的三元组与旧 `state` 写进**同一行、同一事务**（崩溃窗口不存在）；
  `upsert_alpha` 的旧签名调用者可以只写旧列 → 新列 NULL。
- 读路径：`registry._triple_from_row` —— 新列非 NULL 以新列为准，
  NULL 走 `project(legacy state)` 投影回退；`lossy` 元数据在 C2 恒等于投影的
  lossy（C2 写的就是投影），**C3 写出发散三元组时必须重估这一来源**。
- catalog：`_research`/`_deployment`/`_health`/`_lossy` 四个并行字典，
  `register`/`_set_state` 双写，新增 `triple()/research_state()/deployment_state()/
  health()/by_deployment()/by_health()` 只读 API；`state()/by_state()/approved_set()`
  一字未动，旧读路径仍权威。
- 诚实注记：旧签名 `upsert_alpha` 覆盖行时新列写 NULL —— 这不是丢数据，
  读路径投影回退与 C2 双写结果逐位等价（因为 C2 三元组 ≡ 投影）。
- 真实库验证：`data/cache/experiment_registry.db`（66 行，未迁移）读路径
  正常，迁移将在该库首次 catalog 写时惰性发生。
- 测试 `tests/lifecycle/test_dual_write_persistence.py`（6）：双写同 row 同事务、
  投影回退（含 DEGRADED 的 UNKNOWN_LEGACY + lossy）、迁移惰性/幂等/历史行保留、
  坏枚举 fail-closed、HEALTHY/NONE 往返。

## 7.3 C3 落地记录（2026-07-27）—— 唯一改变运行行为的提交

**改了什么**：`degradation_monitor` / `maintenance` / `retirement` 切到三台正交
机器;自动降险改走 health 机器（`AlphaLifecycleManager.advance_health`）。

**行为变化逐条（评审清单）**：

1. **降级不再覆盖部署位**。旧：`PAPER_TRADING → DEGRADED` 把部署位信息写没。
   新：legacy 字段写法和以前逐位相同（所有未切换的读者看到的东西不变）,
   但 catalog 三元组里 `deployment=PAPER` 被保留,`health=(DEGRADED, SIGNAL_DECAY)`
   带机器可读原因码 —— §3.4 的组合状态首次可表达,§11.3「根因消除」有了入口。
2. **monitor 的 PAUSED 建议映射为 health FAILED + 自动降险**。FAILED 在部署机
   器上强制 de-risk 到 PAUSED（§3.4 不变量 3 / §11.2,任何 actor 可达）。
   legacy 仍写 PAUSED,报告 action 串仍是 `"PAUSED"` —— live_linkage /
   maintenance events / console 消费格式零变化（有测试钉死）。
3. **legacy 表达不了的恢复边,health 机器能记**。`FAILED→DEGRADED`（健康恢复）
   合法,但 legacy `PAUSED→DEGRADED` 不存在 —— 此时 legacy 保持更严重的值
   （fail-safe 方向）,health 机器如实记录,audit metadata 标
   `legacy_write=skipped_illegal_legacy_edge`。旧行为是整条记录 error。
4. **sweep 集合按部署态筛选**。旧 `_LIVE_STATES` 里混着 DEGRADED（健康值）;
   新集合 = `DeploymentState ∈ {PAPER, SHADOW, MICRO_LIVE, LIVE}` + 保守保留
   `UNKNOWN_LEGACY`（旧 DEGRADED 行,丢了部署位但本来在跑,掉出 sweep =
   静默停止监控一个衰退 alpha,危险方向）。传 legacy `AlphaState` 值进
   `live_states` 直接 `TypeError`（静默匹配空 = 全 alpha 失监,必须响）。
5. **退役链路不变**。RETIRED 仍走 legacy audited advance;投影
   (RESEARCH_RETIRED, CLOSED, HEALTHY) 本来就对。
6. **幂等收敛**。同一 verdict 重复观测 = no-op 不进审计;同态不同原因 =
    audited 修正（`skipped_same_state`）,不再每个 sweep 重复转换。
7. **HEALTHY 恢复刻意未接线**:§11.3 恢复（根因消除→重放→Shadow 冷静期→
   人工批准）是人门,C3 不写;`advance_health(..., HEALTHY)` 直接 raise。

**持久化**:三元组首次可能**偏离投影**（如 VALIDATED+PAPER+DEGRADED）。
新增第 7 列 `lossy_json`（同一惰性迁移机制,逐列 ADD COLUMN）:C2 行
lossy_json=NULL → lossy 从投影派生;C3 行显式存（含空列表）。
`research_challenged` 标记在 C3 行上保留 —— C3 没记录挑战史,不假装知道。

**未做的（刻意）**:`advance_research` / `advance_deployment` 公共入口 —
C3 唯一真实调用者是 health 机器的 de-risk 耦合（内联实现）,为无调用者的
入口写代码违反本仓库「不写投机性死代码」纪律;有真实调用者时再加。
`promotion.py` / `agent_loop.py` / `factory.py` / console 的切换留 C4/后续。

测试 `tests/lifecycle/test_c3_health_machine.py`（13）。旧套件
（tests/alpha 185 + tests/tools + console 契约）**零修改通过** ——
证明 legacy 可见行为逐位不变。

## 8. 明确非目标

- **不实现 `MICRO_LIVE`/`LIVE` 的任何执行路径。** 它们是枚举成员 + 拒绝式守卫，
  与 `LiveBrokerAdapter` / `NullSigningAuthority` 同构。真钱仍被 readiness pin
  + 五旗帜结构性挡死。
- **不删 `AlphaState`。** 见 §2。删它要等到所有历史链与 grant 都被外部签名重锚，
  那是 post-L2 的事。
- **不改任何风险阈值。** 本方案只重排状态语义，不动一个门槛数字。
- **不动 `live/` 树。** 那里的仓位/风控是账户级的，不感知 alpha 生命周期。

## 9. 已知代价，写在前面

1. **双写期有两套状态并存**（C2→C3 之间）。缓解：§7 第 7 条不变量逐行钉死
   「投影(旧) == 新」，任何漂移即测试失败。
2. **`UNKNOWN_LEGACY` 会长期存在**于历史行。这是诚实的代价，不是债务 ——
   替代方案（猜一个部署位回填）是伪造。
3. **`services/lifecycle/` 与既有 `services/alpha/lifecycle.py` 同名易混。**
   前者是三台机器的定义（纯数据 + 纯函数），后者是编排器（带审计链、guard、catalog）。
   落地时在两个文件头互相注明。
