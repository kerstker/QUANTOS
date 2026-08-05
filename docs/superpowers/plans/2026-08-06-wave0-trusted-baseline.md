# Wave 0 可信基线实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不覆盖主工作树既有 evidence/docs 改动的前提下，捕获并修复当前真实门禁失败，生成同一 commit、同一 canonical 运行的一致 L2 证据基线。

**Architecture:** 所有修复在隔离 worktree 中完成；先运行不写正式 evidence 的定向检查，再用测试先行方式修复被实测证实的问题。最后在隔离 worktree 执行会写 artifact 的全量验证，只提交代码、测试和由同一验证运行生成的必要 artifact，绝不把主工作树既有修改混入。

**Tech Stack:** Python 3.14、FastAPI 0.139、pytest、React 18、TypeScript、Vite 6、ESLint、QUANTOS canonical evidence generators。

## Global Constraints

- 当前模式必须保持 `L2_DRY_RUN_ONLY`；本计划不能开启任何 live flag、broker capability 或订单提交。
- 执行本计划前必须使用 `superpowers:using-git-worktrees` 创建隔离 worktree。
- 主工作树当前已有 `docs/`、`evidence/` 和 `evidence/raw_archive_audit.json` 修改；禁止 reset、checkout、覆盖、暂存或复制这些修改。
- 历史报告中的“5 个失败”不是当前事实；以 Task 1 捕获的实际输出为准。
- 禁止手工编辑生成 JSON 使门禁转绿；必须修复生成器、输入或契约后 canonical 重生成。
- 使用 `python -m pytest` 或 `python scripts/validate.py`，不要运行会被 RTK hook 误处理的裸 `pytest`。
- 每个修复任务只在对应失败已由 Task 1 证实时执行；若测试已经通过，记录 `NO_CHANGE_NEEDED` 并跳过该修复任务。

---

### Task 1: 捕获隔离的真实失败基线

**Files:**
- Create: `$CLAUDE_JOB_DIR/tmp/wave0-baseline/manifest.json`（临时，不提交）
- Create: `$CLAUDE_JOB_DIR/tmp/wave0-baseline/*.log`（临时，不提交）
- Inspect: `evidence/validation_report.json`
- Inspect: `evidence/validation_report_subset.json`

**Interfaces:**
- Consumes: 干净隔离 worktree 的当前 `HEAD`。
- Produces: 一个只读 manifest，字段为 `commit`、`commands[]`、`exit_code`、`log_sha256`、`observed_failures[]`，供 Tasks 2–6 判断是否需要修复。

- [ ] **Step 1: 创建并进入隔离 worktree**

调用 `superpowers:using-git-worktrees`，基于当前已提交的 `bf706c7a` 或执行时 `HEAD` 创建 `wave0-trusted-baseline` worktree。不要复制主工作树未提交的 `docs/` 或 `evidence/` 文件。

- [ ] **Step 2: 记录隔离性断言**

运行：

```powershell
$main = "H:\code\QUANTOS"
$here = (Get-Location).Path
if ($here -eq $main) { throw "Wave 0 must not run in the main worktree" }
git status --short
```

预期：当前位置不是 `H:\code\QUANTOS`，`git status --short` 无输出。

- [ ] **Step 3: 运行不生成正式 evidence 的定向检查**

依次运行并把完整 stdout/stderr 保存到 `$CLAUDE_JOB_DIR/tmp/wave0-baseline/`：

```powershell
python -m pytest -q tests/research/test_console_api_contract.py tests/research/test_console_api_async_offload.py tests/research/test_console_live_stream.py tests/governance/test_console_composition_boundary.py
python apps/research-console/scripts/run_paper_to_live_governance_check.py
python scripts/generate_service_import_graph.py --check
python scripts/generate_event_schema_catalog.py --check
python scripts/generate_supply_chain_evidence.py --check
python scripts/assess_top_tier.py --check
python -m pytest -q tests/governance/test_supply_chain_evidence.py tests/governance/test_top_tier_assessment.py tests/reproducibility/test_evidence_snapshots.py
```

预期：每条命令的实际退出码和失败测试被完整保存；不要根据历史 JSON 补写失败名。

- [ ] **Step 4: 运行前端只读检查**

```powershell
Push-Location apps/research-console
npm ci
npm run typecheck
npm run eslint
npm test -- --maxWorkers=1 --no-file-parallelism
Pop-Location
```

预期：记录每条命令退出码；`npm ci` 只按 lockfile 安装，不修改依赖声明。

- [ ] **Step 5: 写入临时 manifest**

使用一个 PowerShell helper 对每条 Step 3–4 命令执行 `& $Command *>&1 | Tee-Object -FilePath $Log`，紧接着保存 `$LASTEXITCODE` 到同名 `.exitcode` 文件。然后运行以下脚本生成 manifest：

```python
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

root = Path(r"$CLAUDE_JOB_DIR/tmp/wave0-baseline")
commit = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
records = []
for path in sorted(root.glob("*.log")):
    body = path.read_bytes()
    records.append({
        "name": path.stem,
        "exit_code": int(path.with_suffix(".exitcode").read_text().strip()),
        "log": str(path),
        "log_sha256": hashlib.sha256(body).hexdigest(),
    })
(root / "manifest.json").write_text(
    json.dumps({"commit": commit, "commands": records}, indent=2) + "\n",
    encoding="utf-8",
)
```

预期：manifest 的 commit 等于隔离 worktree `HEAD`，每个日志都有摘要。

- [ ] **Step 6: 提交边界**

本任务没有仓库文件变更，不提交。如果 `git status --short` 非空，先确认是否仅为测试生成物；删除可重建临时产物，禁止删除或覆盖来源不明文件。

### Task 2: 适配 FastAPI 0.139 的延迟路由契约

**Files:**
- Modify: `tests/research/test_console_live_stream.py:45-52`
- Inspect: `services/console/main.py`（只有 GET 路由确实未注册时才修改；延迟展开本身不是生产缺陷）
- Test: `tests/research/test_console_live_stream.py`

**Interfaces:**
- Consumes: FastAPI 应用 `services.console.main.app`。
- Produces: 测试 helper `_materialized_routes(app) -> tuple[BaseRoute, ...]`，只验证 lifespan/路由展开后的真实 HTTP 应用。

- [ ] **Step 1: 仅在 Task 1 证实失败时写回归测试 helper**

将直接遍历 `app.routes` 改为通过 `TestClient` 触发应用启动，再读取 materialized routes：

```python
from fastapi.testclient import TestClient


def _materialized_routes():
    with TestClient(app) as client:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        return tuple(app.routes)


def test_dry_run_event_stream_is_registered_get_only():
    routes = [
        route
        for route in _materialized_routes()
        if getattr(route, "path", None) == "/api/v1/console/stream/dry-run"
    ]
    assert len(routes) == 1
    assert routes[0].methods == {"GET"}
```

若 FastAPI 0.139 仍不在 `app.routes` 展开，则改为从 `/openapi.json` 的 `paths` 断言 `get` 唯一存在；不要访问 `_IncludedRouter` 私有字段。

- [ ] **Step 2: 运行测试确认原断言失败、新断言通过**

运行：

```powershell
python -m pytest -q tests/research/test_console_live_stream.py
```

预期：3 tests passed。若 GET 实际未注册，保持测试失败并检查 `services/console/main.py` 的 include 顺序；不要仅放宽断言。

- [ ] **Step 3: 运行 console 契约回归**

```powershell
python -m pytest -q tests/research/test_console_api_contract.py tests/research/test_console_api_async_offload.py tests/research/test_console_live_stream.py tests/governance/test_console_composition_boundary.py
```

预期：全部通过。

- [ ] **Step 4: 提交**

```powershell
git add tests/research/test_console_live_stream.py services/console/main.py
git commit -m @'
test: materialize FastAPI routes before inspection

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

仅暂存实际修改的文件；若生产文件未改，不要把它加入提交。

### Task 3: 消除治理扫描器对安全证明字段的误报

**Files:**
- Modify: `tools/l2_schedule_entrypoint.py:80-86`
- Modify: `tests/tools/test_l2_schedule_entrypoint.py`
- Test: `tests/governance/test_l2_operations_boundary.py`

**Interfaces:**
- Consumes: 调度回执中的安全声明。
- Produces: 语义等价但不触发 forbidden credential-access token 的字段，例如 `credential_material_used: false`；对应 schema/readback 同步更新。

- [ ] **Step 1: 仅在 Task 1 scanner 证实误报时写失败测试**

在 `tests/tools/test_l2_schedule_entrypoint.py` 增加源码边界断言：

```python
def test_attestation_safety_avoids_forbidden_credential_access_token():
    source = (ROOT / "tools" / "l2_schedule_entrypoint.py").read_text(encoding="utf-8")
    forbidden = "credentials" + "_accessed"
    assert forbidden not in source
    assert '"credential_material_used": False' in source
```

预期：当前代码因包含旧字段而失败。

- [ ] **Step 2: 运行失败测试**

```powershell
python -m pytest -q tests/tools/test_l2_schedule_entrypoint.py::test_attestation_safety_avoids_forbidden_credential_access_token
```

预期：FAIL，指向旧安全字段。

- [ ] **Step 3: 最小修改字段和读取测试**

将：

```python
_ATTESTATION_SAFETY = {
    "paper_only": True,
    "live_allowed": False,
    "order_submission_allowed": False,
    "credentials_accessed": False,
    "broker_connectivity": False,
}
```

改为：

```python
_ATTESTATION_SAFETY = {
    "paper_only": True,
    "live_allowed": False,
    "order_submission_allowed": False,
    "credential_material_used": False,
    "broker_connectivity": False,
}
```

同步修改只针对该调度回执 schema 的测试 fixture/assertion；不要全局重命名其他 artifact 的稳定契约。

- [ ] **Step 4: 运行定向测试和治理扫描器**

```powershell
python -m pytest -q tests/tools/test_l2_schedule_entrypoint.py tests/governance/test_l2_operations_boundary.py
python apps/research-console/scripts/run_paper_to_live_governance_check.py
```

预期：测试全部通过；scanner 输出 `SCANNER PASSED` 且 0 violations。

- [ ] **Step 5: 提交**

```powershell
git add tools/l2_schedule_entrypoint.py tests/tools/test_l2_schedule_entrypoint.py tests/governance/test_l2_operations_boundary.py
git commit -m @'
fix: keep schedule safety receipts scanner-safe

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

### Task 4: 修复前端 lint 或依赖版本的当前失败

**Files:**
- Inspect first: `apps/research-console/src/lib/i18n/bilingual.ts:8-10`（当前 `_en` 以 TypeScript 下划线参数表达有意未使用；只有 Task 1 的 ESLint 实际失败时修改）
- Inspect first: `requirements.txt`、`pyproject.toml`、`apps/research-console/package.json`、`apps/research-console/package-lock.json`
- Modify: Task 1 失败日志指向的单一 manifest、生成器或前端源文件
- Test: Task 1 失败日志指向的现有测试

**Interfaces:**
- Consumes: Task 1 的实际 lint/typecheck/dependency failure。
- Produces: 不禁用规则、不添加 blanket ignore 的最小修复。

- [ ] **Step 1: 从日志提取唯一失败位置**

确认 Task 1 日志包含准确文件、行号和规则。若历史报告中的 `bilingual.ts:9` 已不再失败，记录 `NO_CHANGE_NEEDED` 并跳到 Step 5。

- [ ] **Step 2: 写或强化回归测试**

若失败是未使用导入/变量，先在失败文件中删除无用声明并保持现有行为测试不变；若该声明本应承重，则在同目录现有 `*.test.tsx` 中先增加直接覆盖该导出行为的失败断言。禁止通过 `eslint-disable`、降低规则等级或扩大 ignore 隐藏。`bilingual.ts` 的 `_en` 已明确表示有意未使用，不应在没有实际 lint 失败时改动。

- [ ] **Step 3: 实施最小修复**

删除实际未使用声明，或让生产组件使用已测试的 helper。依赖版本失败时，修复 baseline 生成器读取的版本来源；不得把 expected version 手工改成当前环境输出而不解释来源。

- [ ] **Step 4: 验证前端**

```powershell
Push-Location apps/research-console
npm run typecheck
npm run eslint
npm test -- --maxWorkers=1 --no-file-parallelism
Pop-Location
```

预期：全部退出 0。

- [ ] **Step 5: 验证 Python 依赖证据**

运行 Task 1 暴露的精确 dependency-version 测试，并执行：

```powershell
python scripts/generate_supply_chain_evidence.py --check
```

预期：通过；如果仍因 artifact 陈旧失败，留给 Task 5 canonical 生成，不手改 JSON。

- [ ] **Step 6: 提交**

只暂存实际修改：

```powershell
git add apps/research-console/src apps/research-console/package.json apps/research-console/package-lock.json scripts tests

git diff --cached --check
git commit -m @'
fix: restore dependency and frontend checks

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

在 commit 前用 `git diff --cached --name-only` 确认没有 evidence/docs。

### Task 5: 绑定 canonical 证据到同一验证运行

**Files:**
- Modify: `scripts/validate.py`
- Modify as proven necessary: `scripts/generate_service_import_graph.py`
- Modify as proven necessary: `scripts/generate_event_schema_catalog.py`
- Modify as proven necessary: `scripts/generate_supply_chain_evidence.py`
- Modify as proven necessary: `scripts/assess_top_tier.py`
- Test: `tests/reproducibility/test_evidence_snapshots.py`
- Test: `tests/governance/test_supply_chain_evidence.py`
- Test: `tests/governance/test_top_tier_assessment.py`

**Interfaces:**
- Consumes: `HEAD` commit、canonical generator inputs。
- Produces: `validation_run_id: str` 和 `source_commit: str` 在同一运行生成的 artifact 间一致；生成器 `--check` 对陈旧或跨运行 artifact fail closed。

- [ ] **Step 1: 写跨 artifact 一致性失败测试**

在 `tests/reproducibility/test_evidence_snapshots.py` 增加 `_validation_binding(payload)` helper。输入 schema 明确为：`validation_report.json` 使用顶层 `commit` 且以 `checks_manifest_sha256` 作为运行绑定；readiness artifact 使用顶层 `commit` 与 `validation.reported_commit`，并要求二者相等；supply-chain/top-tier 必须由 Task 5 的 generator 增加统一 `validation.run_id/source_commit`。测试保持以下语义：

```python
def test_readiness_artifacts_bind_one_commit_and_validation_run():
    paths = [
        ROOT / "evidence" / "validation_report.json",
        ROOT / "evidence" / "supply_chain_baseline.json",
        ROOT / "evidence" / "top_tier_assessment.json",
    ]
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    commits = {payload["validation"]["source_commit"] for payload in payloads}
    run_ids = {payload["validation"]["run_id"] for payload in payloads}
    assert len(commits) == 1
    assert len(run_ids) == 1
```

如果现有 schema 使用不同 nesting，定义一个 `_validation_binding(payload)` helper 显式支持当前唯一 schema；不要用多套 fallback 猜字段。

- [ ] **Step 2: 运行测试确认陈旧 artifact 失败**

```powershell
python -m pytest -q tests/reproducibility/test_evidence_snapshots.py::test_readiness_artifacts_bind_one_commit_and_validation_run
```

预期：FAIL，显示多个 commit/run 或缺少 binding。

- [ ] **Step 3: 在 validate orchestrator 生成单一运行身份**

在 `scripts/validate.py` 的入口生成一次运行 ID，并通过显式 CLI 参数或环境传给所有生成器：

```python
validation_run_id = hashlib.sha256(
    f"{source_commit}:{started_at_ms}".encode("utf-8")
).hexdigest()
validation_env = {
    **os.environ,
    "QUANTOS_VALIDATION_RUN_ID": validation_run_id,
    "QUANTOS_VALIDATION_SOURCE_COMMIT": source_commit,
}
```

所有 generator 从这两个环境变量读取；缺失时独立调用可以生成 diagnostic artifact，但 `--check` 必须验证 source commit 等于当前 `HEAD`，正式 `validate.py` 路径必须使用同一 run ID。

- [ ] **Step 4: 让生成器写入统一 binding**

每个 artifact 的 `validation` 对象使用同一字段：

```python
"validation": {
    "run_id": require_validation_run_id(),
    "source_commit": require_validation_source_commit(),
    "generated_at": generated_at,
    "current": True,
}
```

复用一个现有 evidence helper；若没有，则创建 `scripts/evidence_binding.py`，导出：

```python
def validation_binding(*, generated_at: int) -> dict[str, object]: ...
def assert_current_binding(payload: Mapping[str, object], *, head: str) -> None: ...
```

- [ ] **Step 5: 运行定向生成器和测试**

```powershell
python scripts/generate_service_import_graph.py --check
python scripts/generate_event_schema_catalog.py --check
python -m pytest -q tests/governance/test_supply_chain_evidence.py tests/governance/test_top_tier_assessment.py tests/reproducibility/test_evidence_snapshots.py
```

预期：代码契约测试通过。现有 artifact 若仍陈旧，`--check` 应明确失败并报告期望 commit/run，而不是模糊 `FAIL`。

- [ ] **Step 6: 提交代码和测试，不提交生成物**

```powershell
git add scripts/validate.py scripts/evidence_binding.py scripts/generate_service_import_graph.py scripts/generate_event_schema_catalog.py scripts/generate_supply_chain_evidence.py scripts/assess_top_tier.py tests/reproducibility/test_evidence_snapshots.py tests/governance/test_supply_chain_evidence.py tests/governance/test_top_tier_assessment.py
git diff --cached --check
git commit -m @'
feat: bind readiness evidence to one validation run

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

只加入实际存在且已修改的文件。

### Task 6: canonical 全量验证与 artifact readback

**Files:**
- Generate in isolated worktree: `evidence/validation_report.json`
- Generate as orchestrated: supply-chain、top-tier、readiness 和相关 evidence/docs artifacts
- Test: full `scripts/validate.py`

**Interfaces:**
- Consumes: Tasks 2–5 的提交，干净隔离 worktree。
- Produces: 同一 `HEAD` 和 `validation_run_id` 的全量 artifact 集合，以及最终 PASS/FAIL 的诚实报告。

- [ ] **Step 1: 确认隔离 worktree 在生成前干净**

```powershell
git status --short
$head = git rev-parse HEAD
```

预期：无输出；保存 `$head`。

- [ ] **Step 2: 执行全量 canonical 门禁**

```powershell
python scripts/validate.py
```

预期：退出 0。若失败，不进入 Step 4；从实际失败返回对应 Task，禁止手改 artifact。

- [ ] **Step 3: 验证 readback 和 L2 安全状态**

运行：

```powershell
python -m pytest -q tests/reproducibility/test_evidence_snapshots.py tests/governance/test_supply_chain_evidence.py tests/governance/test_top_tier_assessment.py
python apps/research-console/scripts/run_paper_to_live_governance_check.py
```

再用 Python 断言：

```python
import json
from pathlib import Path

report = json.loads(Path("evidence/validation_report.json").read_text(encoding="utf-8"))
assert report["overall_status"] == "PASS"
assert report["readiness"] == "L2_DRY_RUN_ONLY"
```

若真实 schema 的 readiness 位于嵌套字段，使用测试中定义的唯一 canonical accessor；不要搜索多个 fallback。

- [ ] **Step 4: 审查生成 diff**

```powershell
git status --short
git diff --stat
git diff --check
```

预期：所有生成 artifact 的 source commit 为 `$head`，run ID 相同；没有 live flag、凭证或 broker capability 变化。

- [ ] **Step 5: 提交 canonical artifact**

只提交 `scripts/validate.py` 实际生成且通过 readback 的文件：

```powershell
git add evidence docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md docs/reports/QUANTOS_AUTONOMY_FINAL_AUDIT.md
git diff --cached --name-only
git diff --cached --check
git commit -m @'
chore: regenerate trusted L2 evidence baseline

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
'@
```

提交前确认这些文件来自隔离 worktree 的本次 canonical 运行，而不是主工作树用户改动。

- [ ] **Step 6: 最终验证**

```powershell
python scripts/validate.py
```

预期：退出 0；若第二次运行因时间戳造成无意义 diff，修复生成器确定性后重新执行，不能接受“每跑一次都变”的基线。
