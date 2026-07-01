# 多方人工批准状态机 设计(轨道3 子项目2)

> L2_DRY_RUN_ONLY,一寸不让。本子项目只造「批准的构造路径」——把它从**单人一张条子**抬到
> **多个不同真人 + 全程哈希链审计**,并接进现有的两个安全门。**绝不**动五个 LIVE 标志、
> **绝不** import 券商/CTP/FIX/网络 SDK、**绝不**写能真发单/真凭证/真连接的代码、**绝不**弱化任何现有门。

## 1. 背景与要解决的缺口

今天 QUANTOS 的人工批准是**单步**的:

- `quantos/contracts/governance.py::ApprovalRecord` 是一张扁平记录——一个 `approver`、一个
  `approved: bool`、一个 `subject_id`。
- 两个 chokepoint 各自只要**一个**真人 + **一张**已批准的记录:
  - `services/risk/pre_trade/kill_switch.py::KillSwitch.reset` —— 要 HUMAN actor +
    绑定到事件的 `ApprovalRecord`(`subject_id == incident_id`、`approver == actor.actor_id`)。
  - `services/deployment/gate.py::evaluate_deployment` —— PAPER 及以上要一张 VERIFIED HUMAN
    `ApprovalRecord`(`approved`、`approver == actor.actor_id`、`actor.is_human`、subject 匹配)。

**缺口:** 没有多方 / 四眼(four-eyes)/ 职责分离(segregation of duties)。同一个人既能提出又能批准;
一个人一张条子就够。对「通往实盘」这种最敏感的动作,这道门太薄。

**本子项目做什么:** 造一个**可复用的多步批准状态机**——一份批准要走
`REQUESTED → REVIEWED → APPROVED → CONSUMED`,**提出 / 复核 / 批准必须是三个两两不同的真人**,每一步都追加
到一条自带哈希链的账本(跟 `AuditEvent` 同法防篡改),批准**一次性**、**绑定到具体 subject**。然后给两个
现有门各加一条**更严的新入口**吃这个多方 grant——旧的单记录路径保留、不弱化,已有测试不回归。

## 2. 诚实边界(非目标)

- 本子项目让**批准的构造路径**变成多方且可审计。它在**单进程内挡不住**受信代码直接凑三个 `HUMAN`
  `Actor`、直接调转换函数绕过——那个 build-time 面(受信代码可绕过)是**子项目3(部署/进程隔离 + 签名引用)**
  的范畴,和 CLAUDE.md 威胁模型一字不差。设计里把这条明说,不假装它是信任边界。
- 不真上实盘、不引入真券商连接、不动五个 LIVE 标志、不弱化任何门。
- 不做前端页面、不做 console 端点、不做跨进程/跨时间的持久层(核心是**内存里的不可变聚合**;durable 存储
  留给后续,非本子项目范围)。
- 不重命名或废弃现有 `ApprovalRecord`(它仍是两个门今天接受的记录类型)。

## 3. 架构(方案 1:独立状态机 + 接进现有门)

新建**扁平模块** `services/governance/approval.py`(跟仓库「真代码在扁平 `services/<域>/*.py`、
不在嵌套 DDD 空壳」的约定一致)。核心是**函数式 / 不可变**的:每个转换函数吃当前聚合 + actor,返回**新**聚合,
**无墙钟、无随机**(`at: int`、id 由驱动层传,跟干跑流水线的确定性核同法)。

### 3.1 状态与聚合(第 1 块)

```python
class ApprovalState(str, Enum):
    REQUESTED = "REQUESTED"
    REVIEWED  = "REVIEWED"
    APPROVED  = "APPROVED"
    CONSUMED  = "CONSUMED"
    REJECTED  = "REJECTED"   # 终态:任一复核/批准人可否决

class ApprovalStep(BaseModel):        # frozen
    state: ApprovalState              # 这一步把请求推进到的状态
    actor_id: str                     # 执行这一步的真人
    timestamp: int                    # ge=0,由驱动层传
    note: str = ""
    prev_hash: str = ""
    @property
    def entry_hash(self) -> str: ...  # stable_hash({state,actor_id,timestamp,note,prev_hash})

class ApprovalRequest(BaseModel):     # frozen
    request_id: str                   # min_length=1
    subject_id: str                   # min_length=1,批准绑定的对象(如 kill-switch incident_id / 部署 subject)
    steps: tuple[ApprovalStep, ...] = ()
    @property
    def state(self) -> ApprovalState              # 最后一步的 state;空→REQUESTED 之前视作"未创建"
    @property
    def distinct_human_actor_ids(self) -> frozenset[str]  # 走过 request/review/approve 的不同 actor 集
    @property
    def head_hash(self) -> str                    # 末步 entry_hash(空则 "")
```

哈希链:第一步 `prev_hash=""`,之后每步 `prev_hash = 上一步.entry_hash`。任何中间篡改都会让链断。

### 3.2 纯转换(第 2 块)

签名统一:`fn(req, actor: Actor, *, at: int, note: str = "") -> ApprovalRequest`。全部 **fail-closed**:

- `open_request(request_id, subject_id, actor, *, at, note="")` —— 建新请求,首步 `REQUESTED`。
  `actor` 非真人 → raise `ApprovalViolation`。
- `review(req, actor, *, at, note="")` —— 要求当前 `REQUESTED`;`actor` 非真人 → raise;
  `actor.actor_id` 已是提出人 → raise(不同人)。追加 `REVIEWED` 步。
- `approve(req, actor, *, at, note="")` —— 要求当前 `REVIEWED`;`actor` 非真人 → raise;
  `actor.actor_id` ∈ {提出人, 复核人} → raise(第三个不同人)。追加 `APPROVED` 步。
- `reject(req, actor, *, at, note="")` —— 当前 ∈ {REQUESTED, REVIEWED};`actor` 非真人 → raise。
  追加 `REJECTED` 终态步。
- `consume(req, subject_id, actor, *, at, note="")` —— 要求当前 `APPROVED` ∧ `req.subject_id == subject_id`
  ∧ `actor` 真人;否则 raise。追加 `CONSUMED` 终态步。**再次 consume 因当前非 APPROVED 而 raise(一次性)。**

状态不对(如对 REQUESTED 直接 approve)→ raise。新异常类型 `ApprovalViolation(PermissionError)`。

### 3.3 不变量校验器(第 3 块)

```python
def verify_multi_party_grant(req: ApprovalRequest, subject_id: str) -> bool:
    """fail-closed:唯一放行条件——已 CONSUMED ∧ subject 绑定 ∧ ≥3 个不同真人 ∧ 哈希链自洽。否则 False。"""
```

- `req.state == CONSUMED`;
- `req.subject_id == subject_id`(空 subject 一律 False,不许空绕过);
- `len(req.distinct_human_actor_ids) >= 3`;
- 哈希链自洽:逐步重算 `entry_hash`、`prev_hash` 链对得上;
- 任一异常 → False(fail-closed)。

### 3.4 接进现有门(第 4 块,向后兼容、只做加法)

- `KillSwitch.reset_with_grant(actor, req, *, at) -> KillSwitchState` —— 新方法。内部:
  `verify_multi_party_grant(req, self._state.incident_id)` 为真 ∧ `actor.is_human` 才复位,否则 raise
  `UnauthorizedReset`。**旧 `reset(actor, approval, *, at)` 一字不动、不弱化。**
- `evaluate_deployment(..., grant: Optional[ApprovalRequest] = None)` —— 新增关键字参数。当传了 `grant`,
  PAPER 及以上的人工批准检查改由 `verify_multi_party_grant(grant, subject)` 满足(多方路径);不传 `grant` 时
  **完全保持**现有单 `ApprovalRecord` 行为。LIVE 仍被 readiness pin + LIVE flags 结构性挡死(不动)。

> 为什么"加法"而非"替换":`reset`/`evaluate_deployment` 是已测的安全控制,替换其签名 blast radius 大、有
> 削弱风险。加法让**合法操作流程**抬到多方,同时零回归——符合"强化现有的门、不弱化任何门"。

### 3.5 治理测试(第 5 块,机器钉死)

`tests/governance/test_multi_party_approval_boundary.py` + `tests/governance/`(或 `tests/risk`/`tests/deployment`)
单元:

- 非人 actor 在任一步 → raise;非人永远到不了 APPROVED;
- 单人自批(提出人=批准人)被拒;两人(缺第三个不同人)被拒;
- `consume` 两次被拒(一次性);subject 不匹配 → `verify_multi_party_grant` False;
- 篡改任一步 note/actor 后哈希链校验 False;
- `reset_with_grant` 用合法三方 grant 放行、用两方/非 CONSUMED grant 被拒;`reset` 旧路径不回归;
- `evaluate_deployment(grant=合法三方)` PAPER 放行、`grant=两方` 被拒;不传 grant 的旧行为不回归;
- `approval.py` import 根 ∩ 券商 SDK / 网络库 = ∅(复用 broker 接缝那套 `_import_roots` AST 检查)。

## 4. 数据流

```
驱动/操作层(传 id + at)
  open_request(req_id, subject_id, alice, at=t0)      -> REQUESTED   (step0, prev="")
  review(req, bob, at=t1)                             -> REVIEWED    (step1, prev=step0.hash)
  approve(req, carol, at=t2)                          -> APPROVED    (step2, prev=step1.hash)
  consume(req, subject_id, operator, at=t3)           -> CONSUMED    (step3, prev=step2.hash)
        │
        ▼  verify_multi_party_grant(req, subject_id) == True  (CONSUMED ∧ 3 不同真人 ∧ 链自洽)
  KillSwitch.reset_with_grant(operator, req, at=t3)   -> 复位
  evaluate_deployment(env=PAPER, grant=req, subject=…) -> allowed
```

alice/bob/carol 两两不同的 `HUMAN` `Actor`;任一为 `AGENT`/`SYSTEM` 或有重复 → 到不了 CONSUMED / 校验 False。

## 5. 错误处理(全 fail-closed)

- 所有转换:前置不满足(状态错 / 非人 / actor 重复 / subject 不符)→ raise `ApprovalViolation`。
- `verify_multi_party_grant`:任何不满足或任何异常 → 返回 False(绝不"出错=放行")。
- `reset_with_grant`:校验不过 → raise `UnauthorizedReset`(沿用现有类型)。
- `evaluate_deployment`:已有 `try/except → blocked` 外壳保持不动(异常仍 fail-closed 拒绝)。

## 6. 确定性与安全约束

- 核心**无墙钟、无随机**:`at: int`、`request_id` 由驱动层边界传入(同 dry-run 核)。
- `approval.py` **不进** `research_runner` 白名单(它不是 research kind);**不 import** 任何券商/CTP/FIX/网络 SDK。
- 只依赖 `quantos.contracts`(Actor/ApprovalRecord/hashing)与 stdlib;pydantic v2 frozen 模型。
- 五个 LIVE 标志不动;不新增任何能真发单/真凭证/真连接的代码路径。

## 7. 文件清单

- 新建 `services/governance/__init__.py`、`services/governance/approval.py`(状态机 + 校验器)。
- 改 `services/risk/pre_trade/kill_switch.py`(加 `reset_with_grant`,旧 `reset` 不动)。
- 改 `services/deployment/gate.py`(加 `grant=` 关键字路径,旧路径不动)。
- 新建 `tests/governance/test_multi_party_approval.py`(状态机/校验器单元)+
  `tests/governance/test_multi_party_approval_boundary.py`(import 边界 + 机器不变量)。
- 改 `tests/risk/`、`tests/deployment/` 各加接入点测试(不回归旧路径)。
- 文档:CLAUDE.md、docs/PROJECT_STATE.md、记忆 [[track3-live-scaffold]]。

## 8. 自审(spec self-review)

- **占位符扫描:** 无 TBD/TODO;每块给了签名与不变量。
- **内部一致:** 三处「三个不同真人」一致;哈希链法与 `AuditEvent` 一致;"只做加法、不弱化旧路径"贯穿第 3.4/5/7 节。
- **范围:** 单一实现计划可覆盖(一个模块 + 两处加法 + 测试);durable 持久层、前端、真适配器都显式划出范围外。
- **歧义:** 「批准一次性」= consume 后状态为 CONSUMED,再 consume 因当前非 APPROVED 而 raise——已在 3.2/3.5 写死。
