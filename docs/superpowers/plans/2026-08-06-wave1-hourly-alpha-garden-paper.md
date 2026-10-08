# Wave 1 小时级 Alpha Garden → Paper 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保持 L2、零真钱能力的前提下，构建小时级 PIT 数据、每天 1 万–10 万候选的 Alpha Garden、不可变 Trial Ledger、分层验证 DAG，以及自动 Shadow/Paper/衰退闭环。

**Architecture:** 复用现有 `services/data`、`services/research`、`services/alpha` 和 `services/intelligence` 的确定性核心，在它们上方增加候选身份、家族、小时级数据合同和持久工作流编排。网络和墙钟只存在于 `tools/` 边界；`services/` 保持离线、确定、无 broker/network import。新链路只写研究、Shadow 和 Paper 状态，机器到 `PAPER` 为止。

**Tech Stack:** Python 3.14、SQLite、NumPy、scikit-learn、pytest、现有 PIT/validation/alpha lifecycle/research session 组件。

## Global Constraints

- 本计划必须在 Wave 0 全绿后、独立 worktree 中执行。
- 保持 `L2_DRY_RUN_ONLY` 和五个安全旗帜；禁止 import `live/`、exchange SDK、网络库或凭证路径进入 `services/`。
- 首期数据频率为 1h 和 4h，基础输入为 1m/5m bar、funding、mark price、OI；网络抓取仅在 `tools/`。
- 动态宇宙每个时点 30–80 个 Binance USDT 永续，必须按当时可知的流动性、上市历史、价差和数据完整性筛选。
- Alpha Garden 每天生成 1 万–10 万候选，但验证门允许 0 个通过。
- 机器最多推进至 `PAPER`；不得创建或消费真钱 grant，不得调用订单接口。
- 所有候选、trial、证据和状态转移可复现、可追溯、append-only。
- 使用测试先行；每项任务先见失败，再做最小实现，再定向验证，再提交。

---

### Task 1: 定义小时级市场数据合同

**Files:**
- Create: `services/data/hourly_market.py`
- Test: `tests/data/test_hourly_market.py`

**Interfaces:**
- Produces: `HourlyBar`、`HourlyFunding`、`HourlyOpenInterest`、`HourlyMarketPanel`；`aggregate_bars(rows, *, interval_seconds) -> tuple[HourlyBar, ...]`；`validate_panel(panel) -> None`。
- Consumes: 仅不可变、已排序的原始观测；无 IO、无墙钟。

- [ ] **Step 1: 写数据合同失败测试**

```python
from services.data.hourly_market import HourlyBar, aggregate_bars


def test_aggregate_bars_uses_event_time_and_latest_knowledge_time():
    rows = (
        HourlyBar("BTCUSDT", 0, 10, 10, 12, 9, 11, 100),
        HourlyBar("BTCUSDT", 300, 310, 11, 13, 10, 12, 120),
    )
    result = aggregate_bars(rows, interval_seconds=3600)
    assert result == (HourlyBar("BTCUSDT", 0, 310, 10, 13, 9, 12, 220),)


def test_aggregate_bars_rejects_future_knowledge():
    row = HourlyBar("BTCUSDT", 3600, 3599, 10, 11, 9, 10, 1)
    with pytest.raises(ValueError, match="knowledge_time"):
        aggregate_bars((row,), interval_seconds=3600)
```

`HourlyBar` 参数顺序固定为 `instrument, event_time, knowledge_time, open, high, low, close, quote_volume`。

- [ ] **Step 2: 运行测试确认缺少模块**

```powershell
python -m pytest -q tests/data/test_hourly_market.py
```

预期：FAIL，`ModuleNotFoundError: services.data.hourly_market`。

- [ ] **Step 3: 实现不可变合同和确定性聚合**

```python
@dataclass(frozen=True, slots=True)
class HourlyBar:
    instrument: str
    event_time: int
    knowledge_time: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float
```

验证 `knowledge_time >= event_time`、价格有限且正、volume 非负、每个 instrument 的 event_time 严格递增；聚合桶使用 `event_time // interval_seconds`，knowledge time 取输入最大值。

- [ ] **Step 4: 补齐 funding/OI/panel 测试与实现**

`HourlyFunding(instrument, event_time, knowledge_time, rate)`、`HourlyOpenInterest(..., value)` 必须有限；`HourlyMarketPanel` 绑定 `schema_version="hourly-market-v1"`、`as_of`、bars/funding/OI tuple 和 `source_digest`。

- [ ] **Step 5: 验证并提交**

```powershell
python -m pytest -q tests/data/test_hourly_market.py
python apps/research-console/scripts/run_paper_to_live_governance_check.py
git add services/data/hourly_market.py tests/data/test_hourly_market.py
git commit -m @'
feat(data): add causal hourly market contract

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

### Task 2: 实现逐时点动态永续宇宙

**Files:**
- Create: `services/data/hourly_universe.py`
- Test: `tests/data/test_hourly_universe.py`

**Interfaces:**
- Consumes: `UniverseObservation(instrument, event_time, knowledge_time, quote_volume, spread_bps, listed_at, data_coverage)`。
- Produces: `select_hourly_universe(observations, *, as_of, policy) -> HourlyUniverse`；`UniversePolicy(min_size=30, max_size=80, min_listing_age_seconds, max_spread_bps, min_data_coverage)`。

- [ ] **Step 1: 写无存活偏差测试**

```python
def test_universe_never_uses_observation_known_after_as_of():
    rows = (
        obs("BTCUSDT", event_time=100, knowledge_time=100, volume=1000),
        obs("NEWUSDT", event_time=100, knowledge_time=101, volume=9999),
    )
    result = select_hourly_universe(rows, as_of=100, policy=policy(min_size=1))
    assert result.instruments == ("BTCUSDT",)
```

再测试成交额降序、instrument 稳定 tie-break、上市年龄、价差、覆盖率和最大 80。

- [ ] **Step 2: 确认失败**

```powershell
python -m pytest -q tests/data/test_hourly_universe.py
```

预期：模块不存在。

- [ ] **Step 3: 最小实现**

筛选 `event_time <= as_of` 且 `knowledge_time <= as_of` 的每标的最新观测；应用政策；按 `(-quote_volume, instrument)` 排序；不足 `min_size` 抛 `UniverseAdmissionError`，不得缩小后继续。

- [ ] **Step 4: 验证并提交**

```powershell
python -m pytest -q tests/data/test_hourly_universe.py tests/data/test_hourly_market.py
git add services/data/hourly_universe.py tests/data/test_hourly_universe.py
git commit -m @'
feat(data): add point-in-time hourly universe

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

### Task 3: 建立小时级离线 materializer

**Files:**
- Create: `tools/hourly_market_materialize.py`
- Test: `tests/tools/test_hourly_market_materialize.py`
- Modify: `tests/governance/test_crypto_data_boundary.py`

**Interfaces:**
- Consumes: repo 现有 raw archive 的 Binance bar/funding/OI 记录。
- Produces: append-only partition `data/cache/hourly_market/YYYY-MM-DD/*.jsonl` 和 manifest；调用 Task 1/2 纯函数。

- [ ] **Step 1: 写 fake raw archive 测试**

测试两次 materialize 相同输入产生相同 manifest digest；半写分区不能被 reader 看见；未知 schema、缺 gap 或未来 knowledge_time fail closed。

- [ ] **Step 2: 确认失败**

```powershell
python -m pytest -q tests/tools/test_hourly_market_materialize.py
```

- [ ] **Step 3: 实现 CLI 和原子提交**

入口：

```python
def materialize_hourly_market(
    *, raw_root: Path, output_root: Path, as_of: int, policy: UniversePolicy
) -> MaterializationReceipt: ...
```

先写同目录 `.tmp`，flush+fsync 后 `os.replace`；manifest 绑定输入文件摘要、policy、schema、as_of、输出摘要。CLI 只接受显式路径和 as-of，不从墙钟猜测。

- [ ] **Step 4: 治理测试**

把工具加入允许网络/IO 的 `tools/` 边界清单；断言 `services/data/hourly_*.py` 无 network import。

- [ ] **Step 5: 验证并提交**

```powershell
python -m pytest -q tests/tools/test_hourly_market_materialize.py tests/governance/test_crypto_data_boundary.py
python apps/research-console/scripts/run_paper_to_live_governance_check.py
git add tools/hourly_market_materialize.py tests/tools/test_hourly_market_materialize.py tests/governance/test_crypto_data_boundary.py
git commit -m @'
feat(data): materialize attested hourly panels

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

### Task 4: 定义 canonical Alpha 身份

**Files:**
- Create: `services/research/alpha_identity.py`
- Test: `tests/research/test_alpha_identity.py`

**Interfaces:**
- Produces: `AlphaDefinition`、`canonical_alpha_json(definition) -> str`、`alpha_id(definition) -> str`。
- Identity 输入：definition、feature contract、data schema、universe policy、horizon、cost model、validation policy。

- [ ] **Step 1: 写稳定身份测试**

```python
def test_alpha_id_is_order_independent_but_policy_sensitive():
    left = definition(params={"window": 24, "lag": 1})
    right = definition(params={"lag": 1, "window": 24})
    assert alpha_id(left) == alpha_id(right)
    assert alpha_id(replace(left, horizon="4h")) != alpha_id(left)
```

测试 NaN、Infinity、任意 callable/bytes 和未登记 model/operator 被拒绝。

- [ ] **Step 2: 确认失败并实现**

使用 canonical JSON：排序键、UTF-8、紧凑 separators；float 必须有限；ID 为 `sha256` 64 hex。定义 frozen dataclass，不接受自由代码字段。

- [ ] **Step 3: 验证并提交**

```powershell
python -m pytest -q tests/research/test_alpha_identity.py
git add services/research/alpha_identity.py tests/research/test_alpha_identity.py
git commit -m @'
feat(research): add canonical alpha identity

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

### Task 5: 建立候选家族和去重索引

**Files:**
- Create: `services/research/alpha_family.py`
- Test: `tests/research/test_alpha_family.py`

**Interfaces:**
- Consumes: `AlphaDefinition`、收益/信号 fingerprint。
- Produces: `family_id(definition) -> str`、`deduplicate_candidates(candidates, *, correlation_threshold) -> DedupResult`。

- [ ] **Step 1: 写参数变体归族测试**

同一 operator tree 不同窗口属于同一家族；不同经济输入或方向不自动合并；exact `alpha_id` 重复必须只保留一次；收益相关高于阈值的非同族候选标记 behavioral duplicate，但不篡改 genealogy。

- [ ] **Step 2: 实现结构 fingerprint**

family identity 忽略登记为 tunable 的数值参数，保留 operator、field、direction、horizon class 和 model template。使用稳定 tie-break 选代表候选。

- [ ] **Step 3: 验证并提交**

```powershell
python -m pytest -q tests/research/test_alpha_family.py tests/research/test_alpha_identity.py
git add services/research/alpha_family.py tests/research/test_alpha_family.py
git commit -m @'
feat(research): group and deduplicate alpha families

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

### Task 6: 实现受约束候选生成注册表

**Files:**
- Create: `services/research/alpha_garden.py`
- Test: `tests/research/test_alpha_garden.py`

**Interfaces:**
- Consumes: 现有 DSL/GP/MCTS/evolutionary generators，经 adapter 返回 `AlphaDefinition`。
- Produces: `GeneratorRegistry`、`GenerationBudget(max_candidates, max_family_candidates, seed)`、`generate_batch(registry, budget, context) -> CandidateBatch`。

- [ ] **Step 1: 写预算和确定性测试**

相同 registry/context/seed 输出顺序和 ID 完全相同；总数不超过 max；每家族不超过 max_family；未登记 generator/model/operator 拒绝；0 candidate 合法。

- [ ] **Step 2: 实现 registry**

注册项字段：`name`、`version`、`family`、`generate(context, rng)`、`max_share`。不允许动态 import 路径或源码字符串。

- [ ] **Step 3: 接入现有生成器 adapter**

为现有 alpha_factory、GP、MCTS 和固定 ML 模板添加薄 adapter；adapter 只转换为 `AlphaDefinition`，不复制算法。

- [ ] **Step 4: 吞吐 smoke**

增加标记为 slow 的 10,000 候选确定性 smoke；断言完成、唯一 ID 比例、家族 cap 和峰值输出数量，不用脆弱墙钟作为 correctness 门。

- [ ] **Step 5: 验证并提交**

```powershell
python -m pytest -q tests/research/test_alpha_garden.py
python -m pytest -q -m slow tests/research/test_alpha_garden.py

git add services/research/alpha_garden.py tests/research/test_alpha_garden.py
git commit -m @'
feat(research): add bounded institutional alpha garden

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

### Task 7: 建立 append-only Trial Ledger

**Files:**
- Create: `services/research/trial_ledger.py`
- Test: `tests/research/test_trial_ledger.py`

**Interfaces:**
- Produces: `TrialRecord`、`TrialLedger.append(record)`、`TrialLedger.load(trial_id)`、`TrialLedger.verify()`、`effective_trial_count(family_id)`。
- SQLite 表：`trial_events`、`trial_heads`；每条事件含 prev_hash/event_hash，更新删除触发器拒绝。

- [ ] **Step 1: 写 append-only 和并发测试**

测试重复同内容幂等；同 trial_id 不同内容冲突；UPDATE/DELETE 失败；截断/篡改 verify 失败；两个 writer 只有一个写入；FAILED/CANCELLED 也计入有效试验政策。

- [ ] **Step 2: 实现 schema 和事务**

使用 `BEGIN IMMEDIATE`；append 同事务验证 head、插入 event、更新 head commitment。事件状态：`PLANNED/RUNNING/PASSED/FAILED/CANCELLED`，状态只能追加转换事件。

- [ ] **Step 3: 实现 effective count**

按 validation policy 规定对 exact duplicate 去重，但失败、中断和同族参数尝试计数；返回 count 和组成 trial IDs 以便审计。

- [ ] **Step 4: 验证并提交**

```powershell
python -m pytest -q tests/research/test_trial_ledger.py
git add services/research/trial_ledger.py tests/research/test_trial_ledger.py
git commit -m @'
feat(research): add append-only global trial ledger

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

### Task 8: 实现分层验证 DAG

**Files:**
- Create: `services/research/validation_dag.py`
- Test: `tests/research/test_validation_dag.py`

**Interfaces:**
- Produces: `ValidationStage` protocol、`ValidationPolicy`、`run_validation_dag(candidate, panel, policy, ledger) -> ValidationArtifact`。
- 固定阶段：`cheap`、`oos`、`realism`；每段只有 PASS/BLOCK，缺 evidence 为 BLOCK。

- [ ] **Step 1: 写 fail-closed DAG 测试**

cheap BLOCK 时 oos/realism 不运行；任何 provider 抛异常形成 BLOCK artifact 并写 ledger；policy/hash 不匹配拒绝；0 通过者合法；同 trial 重试不重复副作用。

- [ ] **Step 2: 实现 stage protocol 和 artifact**

```python
class ValidationStage(Protocol):
    name: str
    version: str
    def evaluate(self, context: ValidationContext) -> StageEvidence: ...
```

Artifact 绑定 candidate alpha_id、panel digest、policy digest、各 stage evidence hash、effective trial count 和最终 verdict。

- [ ] **Step 3: 复用现有验证器**

adapter 连接 leakage、purged walk-forward、DSR、PBO、BH-FDR、成本、容量、regime 和 tail-risk；缺输入时 BLOCK，不返回 None 后继续。

- [ ] **Step 4: 验证并提交**

```powershell
python -m pytest -q tests/research/test_validation_dag.py tests/research/test_trial_ledger.py

git add services/research/validation_dag.py tests/research/test_validation_dag.py
git commit -m @'
feat(research): orchestrate fail-closed alpha validation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

### Task 9: 建立持久候选工作流与租约

**Files:**
- Create: `services/research/alpha_workflow_store.py`
- Create: `services/research/alpha_workflow.py`
- Test: `tests/research/test_alpha_workflow.py`

**Interfaces:**
- Produces: `WorkflowStore.claim(batch_id, worker_id, lease_until)`、`checkpoint(...)`、`complete(...)`；`run_candidate_batch(...) -> BatchReceipt`。
- 状态：`QUEUED/RUNNING/VALIDATED/BLOCKED/SHADOW/PAPER/PAUSED/RETIRED`。

- [ ] **Step 1: 写租约和恢复测试**

两个 worker 不能同时 claim；过期租约可被接管；checkpoint 后崩溃只重跑未完成 candidate；同 batch 重试 receipt digest 相同；状态倒退拒绝。

- [ ] **Step 2: 实现 SQLite durable store**

使用原子 claim 和 optimistic version；保存 candidate IDs、checkpoint watermark、receipt digest，不保存任意 Python object。

- [ ] **Step 3: 编排 Garden→DAG**

`run_candidate_batch` 调 Task 6 生成、Task 5 去重、Task 7 记 trial、Task 8 验证；所有输出绑定同 panel/registry/policy digest。

- [ ] **Step 4: 验证并提交**

```powershell
python -m pytest -q tests/research/test_alpha_workflow.py tests/research/test_alpha_garden.py tests/research/test_validation_dag.py

git add services/research/alpha_workflow_store.py services/research/alpha_workflow.py tests/research/test_alpha_workflow.py
git commit -m @'
feat(research): persist resumable alpha workflows

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

### Task 10: 接入 Shadow/Paper 前向闭环

**Files:**
- Create: `services/research/hourly_forward.py`
- Modify: `services/research/paper_trading/forward.py`
- Modify: `services/research/paper_trading/forward_store.py`
- Test: `tests/research/test_hourly_forward.py`

**Interfaces:**
- Consumes: PASS `ValidationArtifact`、小时级 panel、alpha workflow receipt。
- Produces: append-only `HourlyDecision`、`HourlyRealization`；`record_hourly_decision`、`realize_due_hourly`。
- 决策必须先落库，兑现必须在 horizon 到期后且 coverage 达标。

- [ ] **Step 1: 写先决策后结果测试**

测试 1h/4h horizon；结果已存在但决定未先记录时拒绝；knowledge_time 超过 decision time 的输入不能参与信号；coverage 不足保持 pending；同 decision 不可改写。

- [ ] **Step 2: 扩展 store schema**

新增 schema_version/horizon/alpha_id/workflow_receipt_hash/panel_digest；旧周频记录兼容只读，不伪造回填小时字段。触发器禁止 update/delete。

- [ ] **Step 3: 实现小时兑现**

复用现有 `realize_due` 的先后、coverage 和 admission 语义，把固定周 gap 参数化为明确 horizon；保持现有周频行为不变。

- [ ] **Step 4: 验证并提交**

```powershell
python -m pytest -q tests/research/test_hourly_forward.py tests/research/test_forward_track.py tests/research/test_forward_track_gate.py tests/research/test_forward_evidence_admission.py

git add services/research/hourly_forward.py services/research/paper_trading/forward.py services/research/paper_trading/forward_store.py tests/research/test_hourly_forward.py
git commit -m @'
feat(research): add hourly forward paper evidence

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

### Task 11: 自动漂移、暂停和退休

**Files:**
- Create: `services/alpha/hourly_maintenance.py`
- Modify: `services/alpha/maintenance.py`
- Test: `tests/alpha/test_hourly_maintenance.py`

**Interfaces:**
- Consumes: lifecycle state、forward evidence、feature/return drift、cost/reality gap。
- Produces: `MaintenanceRecommendation`；自动操作只允许 `PAPER→PAUSED`、`PAUSED→RETIRED` 或风险收缩，不允许自动 APPROVED/live 恢复。

- [ ] **Step 1: 写权限和阈值测试**

缺数据、漂移 BLOCK、成本恶化、连续 coverage 失败分别建议 PAUSE；长期失效建议 RETIRE；非人 actor 无法 APPROVE、恢复真钱或 reset kill-switch；异常 fail closed 为 PAUSE。

- [ ] **Step 2: 实现纯评估和受限应用**

`evaluate_hourly_maintenance(...)` 纯函数；`apply_hourly_maintenance(...)` 只调用现有生命周期允许的降级路径并记录审计。阈值来自版本化 policy，不硬编码在 UI。

- [ ] **Step 3: 验证并提交**

```powershell
python -m pytest -q tests/alpha/test_hourly_maintenance.py tests/alpha/test_maintenance.py tests/alpha/test_maintenance_store.py tests/governance/test_multi_party_approval_boundary.py

git add services/alpha/hourly_maintenance.py services/alpha/maintenance.py tests/alpha/test_hourly_maintenance.py
git commit -m @'
feat(alpha): pause and retire degraded hourly alphas

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

### Task 12: 添加 L2 调度入口和只读状态

**Files:**
- Create: `tools/hourly_alpha_advance.py`
- Create: `services/console/api/hourly_alpha_views.py`
- Modify: `services/console/main.py`
- Modify: `tools/l2_schedule_entrypoint.py`
- Test: `tests/tools/test_hourly_alpha_advance.py`
- Test: `tests/research/test_hourly_alpha_console_contract.py`
- Modify: relevant governance allowlists

**Interfaces:**
- CLI: `python -m tools.hourly_alpha_advance --panel data/cache/hourly_market --as-of 1785955200 --budget 10000`。
- GET snapshot: `/api/v1/console/hourly-alpha`，read-only，包含 schema_version/as_of/freshness/watermark、batch funnel、candidate states 和 safety。

- [ ] **Step 1: 写安全边界测试**

源码不得 import `live`、broker/network SDK 或 credential helpers；CLI 无 `--submit/--live/--arm`；console 只有 GET；snapshot 明确 `read_only=true`、`readiness=L2_DRY_RUN_ONLY`、`allow_order_submission=false`。

- [ ] **Step 2: 写端到端 fake panel 测试**

小型固定 panel + registry 运行生成→去重→验证→Shadow/Paper decision；运行两次不重复；崩溃 checkpoint 后恢复；0 通过者返回成功 receipt。

- [ ] **Step 3: 实现工具入口**

网络刷新是另一个显式工具；advance 只读已 attested panel。使用 PID/lease store，不用进程内全局锁。预算默认 10,000，最大 100,000，越界拒绝。

- [ ] **Step 4: 实现只读 projection**

从 workflow/trial/forward stores 生成 snapshot；任何 store 缺失显示 `UNAVAILABLE/BLOCKED`，不生成 mock。

- [ ] **Step 5: 验证并提交**

```powershell
python -m pytest -q tests/tools/test_hourly_alpha_advance.py tests/research/test_hourly_alpha_console_contract.py
python apps/research-console/scripts/run_paper_to_live_governance_check.py

git add tools/hourly_alpha_advance.py services/console/api/hourly_alpha_views.py services/console/main.py tools/l2_schedule_entrypoint.py tests/tools/test_hourly_alpha_advance.py tests/research/test_hourly_alpha_console_contract.py tests/governance
git commit -m @'
feat: operate hourly alpha-to-paper workflow

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

只暂存与本任务实际相关的治理测试文件，不要整目录盲加。

### Task 13: 完成确定性、故障恢复和全量门禁验收

**Files:**
- Create: `tests/integration/test_hourly_alpha_to_paper.py`
- Create: `docs/runbooks/hourly-alpha-operations.md`
- Generate: Wave 1 L2 diagnostic evidence（路径由现有 evidence convention 确定）

**Interfaces:**
- Consumes: Tasks 1–12 全部接口。
- Produces: 可重放 E2E receipt 和中文运维手册；不产生真钱 grant/order。

- [ ] **Step 1: 写端到端验收测试**

固定 raw fixture → materialize → dynamic universe → 10k candidates → family dedup → staged validation → 0或少量 Shadow/Paper；断言所有 candidate/trial/evidence/decision 可追溯。为测试速度使用 tiny registry 做主 correctness 测试，另用 slow smoke 验证 10k 吞吐。

- [ ] **Step 2: 写故障注入测试**

在 generation、OOS、ledger append 和 forward decision 后分别模拟 kill；恢复后 receipt 与无故障运行相同，无重复 trial/decision。注入 stale/gap/hash mismatch 后结果必须 BLOCK。

- [ ] **Step 3: 编写中文 runbook**

明确：输入生成、advance、状态读取、告警、checkpoint 恢复、artifact 校验、暂停/退休、7 天 soak；醒目标注 L2/Paper only，无真钱能力。

- [ ] **Step 4: 定向验证**

```powershell
python -m pytest -q tests/data/test_hourly_market.py tests/data/test_hourly_universe.py tests/research/test_alpha_identity.py tests/research/test_alpha_family.py tests/research/test_alpha_garden.py tests/research/test_trial_ledger.py tests/research/test_validation_dag.py tests/research/test_alpha_workflow.py tests/research/test_hourly_forward.py tests/alpha/test_hourly_maintenance.py tests/tools/test_hourly_alpha_advance.py tests/integration/test_hourly_alpha_to_paper.py
python -m pytest -q -m slow tests/research/test_alpha_garden.py tests/integration/test_hourly_alpha_to_paper.py
```

预期：全部通过。

- [ ] **Step 5: 全量门禁**

```powershell
python scripts/validate.py
```

预期：退出 0，readiness 仍为 `L2_DRY_RUN_ONLY`。

- [ ] **Step 6: 端到端实际运行验证**

用 `tests/data/test_raw_archive.py` 已有的临时 raw archive 构造方式生成确定性 fixture，并执行：

```powershell
$fixtureRoot = "$env:CLAUDE_JOB_DIR\tmp\hourly-alpha-raw-fixture"
$outputRoot = "$env:CLAUDE_JOB_DIR\tmp\hourly-market"
python -m tools.hourly_market_materialize --raw-root $fixtureRoot --output-root $outputRoot --as-of 1785955200
python -m tools.hourly_alpha_advance --panel $outputRoot --as-of 1785955200 --budget 10000
```

预期：生成可验证 receipt；无 network、无 broker、无 order；第二次运行幂等。

- [ ] **Step 7: 提交**

```powershell
git add tests/integration/test_hourly_alpha_to_paper.py docs/runbooks/hourly-alpha-operations.md
git diff --cached --check
git commit -m @'
test: verify hourly alpha-to-paper recovery

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

7 天 soak 是实现后的独立运维验收，不能在单次开发会话中伪造。启动 soak 前需注册 L2 任务和外部 heartbeat，但仍不涉及真钱或订单提交。
