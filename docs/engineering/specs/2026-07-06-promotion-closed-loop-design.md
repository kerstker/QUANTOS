# 多方批准持久化 + 研究晋级前端闭环(战线①)—— 设计与落地记录

> **身份边界已于 2026-07-12 被取代:** 本文的批准持久化和一次性晋级流程仍有效，但下文
> 共享 token + 自报操作者设计仅作历史记录。当前承重 API 使用
> `2026-07-12-strong-identity-baseline-design.md` 定义的 OIDC + mTLS + RBAC 边界。

日期:2026-07-06 · 分支:`codex/firm-aggregation-and-followups` · readiness:**L2_DRY_RUN_ONLY 全程不变**
设计来源:设计 agent 产出 + 对抗式评审(8 条 required_changes 全部落地,逐条对照见 §11)。

## 0. 一句话

给多方批准状态机(`services/governance/approval.py`,零改动)加 **append-only sqlite 持久化**
(`approval_store.py`),新建独立 mutating 服务 **promotion_api(端口 8460,默认不启动)**,
三个不同真人经前端 **PromotionQueue** 页走完 REQUESTED→REVIEWED→APPROVED→CONSUMED,
由 `services/alpha/promotion.py::execute_promotion` 兑现成 `AlphaLifecycleManager.advance(grant=)`
新增加法入口驱动 alpha →APPROVED。console_api 保持 GET-only、research_runner 保持
research-only、live/ 与五个真钱旗帜零接触——**APPROVED 只是研究生命周期状态(paper/干跑部署
资格),不解锁 LIVE**。

## 1. 数据流

```
前端 PromotionQueue.tsx(X-Gov-Token sessionStorage + X-Operator 自报标签)
   │  POST /api/v1/governance/promotion/requests/{id}/{review|approve|reject|execute}
   ▼
services/promotion_api/main.py(8460,独立 mutating 组合服务,镜像 research_runner 认证)
   │  纯转换 open_request/review/approve/reject/consume(approval.py 状态机,零改动)
   ▼
services/governance/approval_store.py(append-only sqlite:data/cache/approvals.db)
   │  execute:预检(不烧)→ consume 落库(烧 grant)→ advance
   ▼
services/alpha/promotion.py::execute_promotion
   │  registry.load_into(catalog) → manager.advance(alpha_id, APPROVED, HUMAN(executor),
   │  grant=consumed, paper_evidence=真实 artifact, eval_trial_count=artifact) → registry.save
   ▼
DurableAuditLog(data/cache/governance_audit.db)—— 每个 mutating 步骤 + 执行三态全落链
```

## 2. ApprovalStore(`services/governance/approval_store.py`)

两表:`approval_requests`(request_id PK / subject_id / kind / created_at / **n_steps** /
**head_hash** —— 后两列是**同事务链头承诺**)+ `approval_steps`(PK `(request_id, step_index)`,
每步存写入时算好的 `entry_hash`)。

- **write-only-adds**:save() 的传入聚合必须以库内已存步骤为严格前缀(逐条 entry_hash 相等),
  只 INSERT 新增尾部;分叉/缩短/换 subject → `ApprovalStoreConflict`,绝不 clobber
  (memory「Concurrent state write races」教训)。
- **fail-closed 重载**:load() 逐条重建 pydantic Step 重算 entry_hash 比对(字段篡改)、
  step_index 连续无洞、prev_hash 链自洽(重排/跨请求拼接)、条数==n_steps ∧ 链头==head_hash
  (截断——特别是"删掉尾部 REJECTED 步复活已否决请求"攻击)、最后再过一遍 approval.py 的
  `_hash_chain_ok`。任一不满足 → `ApprovalStoreError`。
- **并发**:进程内 `threading.Lock` + `BEGIN IMMEDIATE`;跨进程双写者都过前缀检查时,后到者
  INSERT 撞主键,`sqlite3.IntegrityError` **显式映射**为 `ApprovalStoreConflict`(评审 #5),
  事务回滚不半写。
- 纯 stdlib、店内无墙钟(`now` 调用方注入)。
- 诚实边界(与 `DurableAuditLog.seal()` 注脚一字对齐):能直接写 sqlite 文件的攻击者可同时改
  承诺列——单机文件写权限 = 单进程受信代码,真防御是 Phase 14 出进程签名;纵深 =
  promotion_api 把每步同时落 governance_audit.db,单库篡改与审计链对不上。

## 3. lifecycle / guards 加法(零回归)

- `AlphaLifecycleManager.advance(..., grant=None, eval_trial_count=None)`:纯加法,镜像
  `KillSwitch.reset_with_grant` / `evaluate_deployment(grant=)` 先例。`grant is None` → 旧
  ApprovalRecord 路径一字不动(`tests/alpha/test_lifecycle_manager.py` 原样全绿)。
- dst==APPROVED ∧ grant 非 None → `guards.guard_to_approved_with_grant`:
  - `verify_multi_party_grant(grant, promotion_subject(alpha_id, APPROVED))` 必须 True
    (CONSUMED ∧ subject 绑定 ∧ ≥3 两两不同真人 ∧ 链自洽,fail-closed);
  - 执行人 `actor.actor_id` 必须 == grant 中 APPROVED 步的 actor(第三人执行自己的批准,
    别人不能代花——对齐旧 guard 的 approver-identity 语义);
  - `actor.is_human`、paper 证据完整性/一致性检查全部保留——多方批准替代的只是
    ApprovalRecord 那一项,证据门一寸不松;
  - **比旧路径更严的一处(评审 #1)**:`eval_trial_count is None` → 直接拒
    (`NO_EVALUATION_TRIAL_COUNT`),不像旧路径在 None 时静默跳过 stale-trial-count 检查——
    因为持久化世界里每次执行都是新进程,`_eval_trial_count` dict 恒空,靠它整条防线结构性失效。
- **subject 绑定**:`promotion_subject(alpha_id, dst) = "alpha-promotion:{alpha_id}:{dst}"` +
  `parse_promotion_subject`(单一真源在 guards.py;alpha_id 可含冒号,从右侧解析)。
  第一期 dst 白名单 = `{APPROVED}`(唯一 human-only 的晋级)。
- **grant 可追溯(评审 #2)**:grant 路径下 `AlphaTransition.approval_id` 与 evidence_hash 记
  `grant.request_id`——APPROVED 转换在转换链上与其三方批准连着;旧路径字段值不变。
- 结构锁不动:非人 actor 即便持合法 grant,`assert_alpha_transition` 先抛
  `UnauthorizedPromotion`(治理测试钉死)。

## 4. 证据来源钉死(评审 #1 / #8 —— 绝不从请求体代填)

`gather_promotion_evidence(registry, paper_store, alpha_id)`:

| 字段 | 唯一来源 | 缺失时 |
|---|---|---|
| `backtest_sharpe` | 该 alpha **最新 evaluation artifact** 的 `net_sharpe`(experiment_registry;先过 `verify_evaluation` 内容哈希,被篡改的 artifact 不作数) | None + note → guard 拒 |
| `paper_sharpe` | `paper_track.db` 的 `track(alpha_id).paper_sharpe` | None + note → guard 拒 |
| `eval_trial_count` | 同一 evaluation artifact 的 `catalog_trial_count`,与 `catalog.global_trial_count()` 一起喂 guard | None → `NO_EVALUATION_TRIAL_COUNT` 拒 |

catalog 长大后(新 trial 落库)旧 artifact 的 trial count 过期 →
`TRIAL_COUNT_UNDERSTATED_AT_APPROVAL` 拒(端到端测试
`test_stale_eval_trial_count_rejected_end_to_end` 钉死)。

已知映射前提(设计 risks 原样保留):paper_track.db 以 alpha_id 键控;若某 alpha 从未进过
paper-track(命名不对齐),gather 诚实返回不完整证据 → 永远 guard 拒——诚实但流程走不通,
命名映射约定是使用前提,不由代码猜。

## 5. execute_promotion(`services/alpha/promotion.py`)

顺序刻意(每步都是 fail-closed 决定):

1. `store.load` fail-closed 重载;
2. **consume 前预检(评审 #4,全部不烧 grant → `PromotionPrecheckError`,API 409)**:
   subject 可解析 ∧ dst∈白名单;请求状态==APPROVED;**executor == APPROVED 步 actor**;
   experiments db 文件级单飞锁可得(评审 #7,`<exp_db>.promotion.lock`,O_CREAT|O_EXCL);
   `registry.load_into` 可读(损坏的 registry 在烧之前炸);alpha 在 catalog ∧
   `can_alpha_transition(src, APPROVED)`;证据读取(纯读)不炸;
3. **先烧 grant**:`consume` 落库在 advance 之前——advance 失败 grant 即作废、必须重走三方;
   绝不出现「已推进但 grant 还能再花」或「崩溃留下半花 grant」(用户已在设计阶段确认此
   fail-closed 成本;candidates 端点带预检提示减少白烧);
4. **烧毁后全路径审计(评审 #3)**:成功 `PROMOTION_EXECUTED` / guard 拒绝与任意异常
   `PROMOTION_EXECUTION_FAILED`(带 reasons),`registry.save` 在同一包络内;audit event_id
   含时间戳+nonce,撞重自动加序号(评审 #5——DurableAuditLog 对重放 raise),审计库本身坏
   → outcome.audit_ok=False 诚实带回,绝不静默。

open 时也预检 alpha 存在 ∧ can transition(评审 #4②)——结构性不可能的请求不进队列。

## 6. promotion_api(`services/promotion_api/main.py`,8460)

- **选址更新(2026-07-13)**:HTTP 组合根迁到独立 `services/promotion_api/`，避免治理域反向依赖 alpha/research；治理扫描显式覆盖该目录。
  「无网络 import / 不得 import live/」既有机器边界;mutating FastAPI 服务在 services/ 有
  research_runner 先例。console_api(GET-only)/ research_runner(research-only)/
  live_ops_api(交易台权限)都不该长这个权能——权限分离。
- **认证双头**:`X-Gov-Token` 本机共享密钥(`key/governance_token.txt`,首次启动生成,
  gitignored,`secrets.compare_digest`,镜像 research_runner 2026-07-06 刚落地的
  `_load_or_create_token`/`require_runner_token`;独立 token 不复用交易台/runner token)+
  `X-Operator` 自报问责标签(mutating 必填,400)。**GET 数据端点也要 token**;`/health`
  静态元数据除外(镜像 runner 先例)。CORS 仅 localhost:3100;响应永不含 token 值。
- 端点:`GET candidates`(PAPER_TRADING/SHADOW/PAUSED 的 alpha + 证据摘要 + 预检提示——
  只提示不放行)/ `GET requests` + `GET requests/{id}`(步骤时间线)/
  `POST requests`(open,预检)/ `POST {id}/review|approve|reject`(ApprovalViolation → 409
  原样透出)/ `POST {id}/execute`(进程内 threading.Lock 单飞防双击 + 文件级跨进程单飞)。
- 墙钟与 uuid 只在此服务边界产生;核心(approval/approval_store/promotion)纯。

## 7. 威胁模型扩张(评审 #6 —— 本设计最重要的一段,启动前必读)

**本 API 把「受信代码在进程内凑三个 HUMAN Actor」这个 build-time 绕过面,变成了 runtime
HTTP 能力:持 gov token 的任何本机进程——包括 AI agent——可以用三个自报的 X-Operator
标签走完三方批准并执行晋级,零代码变更、零 code-review 痕迹。**

此前这条绕过路径必须写代码、会被 code review 和治理扫描器看见;现在它是一个 HTTP 调用序列。
「三个不同真人」的机器不变量在单机工作站信任模型下 = 三个两两不同的**自报标签**。它挡住的
是:agent 无 token 时的自批、流程遗漏、无问责的匿名操作;它挡不住:一个蓄意的人(或持
token 的 agent)打三个名字。这与「agent 自批被挡住」的旧表述**并不等价**——挡住 agent 的
是「agent 环境不持有 token」这条**运维纪律**,不是代码。

缓解(全部到位才可启动服务):

1. **服务默认不启动**——不进任何自启脚本 / schtasks / launch 配置;由用户读过本节后自己拉起;
2. **AI agent 运行环境不应持有 `key/governance_token.txt`**(与 live_ops token 同口径);
3. token 文件权限 = key/ 目录既有口径(gitignored,本机文件系统权限);
4. 全程双库留痕:approvals.db 哈希链+承诺 × governance_audit.db 哈希链,交叉可对账;
5. APPROVED ≠ 真钱:LIVE 仍被 readiness pin + 五旗帜结构性挡死,grant 合法也不解锁 LIVE
   (与 `evaluate_deployment(grant=)` 既有语义一致)。

这是单机工作站信任模型的延续(与 live_ops_api 同口径);多用户真身份认证/出进程签名 =
Phase 14(`services/governance/signing.py` + `docs/PHASE_14_PROCESS_ISOLATION.md`)。
同样的说明写死在:promotion_api 模块 docstring、promotion.py docstring、前端 PromotionQueue
页常驻横幅、本文档。**CLAUDE.md 威胁模型段的同步更新留给用户核验时一并拍板**(本次改动
刻意不碰 CLAUDE.md)。

## 8. 前端

- `src/lib/api/promotion-api.ts`:`VITE_GOVERNANCE_URL`(默认无 → 页面诚实降级为"未启动"
  说明);token 走 sessionStorage(key `quantos-gov-token`,逐字镜像 runner-token 模式,
  刻意不用 VITE_ 构建期变量);X-Operator 复用 research-runner-api 的 getOperator。
- `src/pages/PromotionQueue.tsx`(注册表 `routes.tsx` 治理与系统组加一行 → 自动进导航 +
  自动被冒烟覆盖):三区——候选卡(证据摘要+发起)/ 进行中(步骤时间线,谁做的+何时+备注,
  按状态渲染下一步按钮;当前 operator 与已有步骤同名时按钮置灰并注明——**前端只是提示,
  真拒绝在状态机**;execute 只对批准人亮)/ 历史(CONSUMED/REJECTED,执行失败如实显示
  「grant 已一次性烧毁,需重新走三方」)。威胁模型横幅常驻页顶。
- 刻意**不改** `src/lib/types/research.ts` 与 `tests/research/test_console_api_contract.py`
  (另一 agent 在改;promotion-api 是独立服务、独立 client 文件,不经 console_api 契约)。
- 旧重定向 `/research/promotion-control` → system-completeness **维持不动**(设计 open
  question 建议改指新页,但未获用户确认,留给用户拍板)。

## 9. 运维(启动即须遵守)

```
python -m uvicorn services.promotion_api.main:app --host 127.0.0.1 --port 8460
```

- **必须单进程单 worker**(评审 #5):ApprovalStore 的跨进程主键兜底只保证不写坏;
  execute 的文件级单飞锁保证 experiments db 不并发交错(与 research_runner 触发的研究进程
  并发写同一 registry 的场景,execute 期间会 fail-closed 拒绝而不是交错)。
- 前端:`VITE_GOVERNANCE_URL=http://127.0.0.1:8460`(与 VITE_API_URL/VITE_RUNNER_URL 并列)。
- token 生命周期:首启生成 `key/governance_token.txt`;换钥 = 删文件重启;绝不进构建产物/
  localStorage(sessionStorage-only)。
- 崩溃遗留的 `<exp_db>.promotion.lock`(锁文件内写有 pid/时间)需人工确认后删除——
  fail-closed:宁可拒绝执行也不并发写坏累积语义的 registry。
- 审计对账:`DurableAuditLog.chain_valid()` × approvals.db `load()` 全绿才是干净状态。

## 10. 测试(本战线新增 62 个 = 后端 56 + 前端 6,全绿;全量 tests/governance + tests/alpha
341 通过、tests/research 508 通过、治理扫描器 0 violations、前端 60 通过 + tsc 干净)

- `tests/governance/test_approval_store.py`(8):round-trip 保链、append-only 超集/分叉/
  缩短/subject 冲突、IntegrityError→Conflict 竞态映射、列表/摘要。
- `tests/governance/test_approval_store_tamper.py`(8):字段/note 篡改、重排、跨请求拼接、
  尾截断复活 REJECTED、承诺列不一致、同 index 重放、index 空洞。
- `tests/alpha/test_lifecycle_grant_path.py`(10):subject 往返、非 CONSUMED/subject 不匹配/
  executor≠approver/非人+合法 grant/证据不松/trial count 缺失与过期、放行+approval_id 可追溯、
  旧路径零回归。
- `tests/alpha/test_promotion_execute.py`(12):证据只来自真实 artifact(含篡改 artifact 不
  作数)、四类预检不烧、烧毁先于 advance、落盘生效、成功/失败双审计、event_id 撞重重试、
  端到端 stale trial count。
- `tests/governance/test_promotion_api_boundary.py`(8):无 SDK/网络/live/tools import、
  治理扫描器对 services/governance+alpha 显式全绿、非人到不了 APPROVED、单人/两人凑不齐、
  持久化篡改被拒、全端点 401、LIVE 五旗帜零接触。
- `tests/governance/test_promotion_api_endpoints.py`(10):三个不同 operator 全流程+第三人
  execute 成功、同人两角 409、非批准人 execute 409 且不烧、reject 关闭、404/400、候选证据形状。
- 前端 `promotion-api.test.ts`(6):双头 header、GET 带 token、409 detail 透出、未配置即禁用;
  PromotionQueue 冒烟由注册表自动覆盖。

## 11. 评审 required_changes 逐条对照

| # | 要求 | 落点 |
|---|---|---|
| 1 | trial count 从 evaluation artifact 取,取不到 fail-closed | `gather_promotion_evidence` + `guard_to_approved_with_grant`(NO_EVALUATION_TRIAL_COUNT)+ 两个专测 |
| 2 | grant 可追溯 | lifecycle commit 记 `grant.request_id` 进 approval_id/evidence_hash + 专测 |
| 3 | 烧毁后全路径审计 | load_into 挪到烧前;advance/registry.save 全包络;三态必落审计 |
| 4 | 减少白烧:executor 预检 + alpha/can_transition 预检 | execute 预检(guard 内检查原样保留双保险)+ open 时预检 + 四个专测 |
| 5 | IntegrityError→Conflict + 竞态测试 + 单 worker 写死 | approval_store 显式映射 + 陈旧读竞态测试 + §9/模块 docstring |
| 6 | 威胁模型文案升级(runtime HTTP 能力、agent 也能打三个名字) | §7 + promotion_api/promotion.py docstring + 前端常驻横幅(CLAUDE.md 留给用户) |
| 7 | experiments db 单飞锁为必选项 + 测试 | `_SingleFlight`(execute 必经)+ API 进程内 Lock + 专测 |
| 8 | backtest_sharpe 证据来源钉死进测试断言 | `test_gather_evidence_from_real_artifacts` / `test_grant_path_does_not_relax_paper_evidence` |

非目标(维持设计原样):TESTING/VALIDATED/PAPER_TRADING 不进此队列;`_MIN_DISTINCT_HUMANS`
维持 3(open question,用户拍板);promotion-control 旧重定向不动(同上);真出进程签名 =
Phase 14。
