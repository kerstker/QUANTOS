# Top-Tier Wave 0 Implementation Plan

> **Execution:** Follow the repository's task-by-task TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the machine-verifiable top-tier registry, fail-closed assessor, generated capability matrix, read-only API snapshot, and evidence-driven System Completeness page.

**Architecture:** A pure offline core reads a target-only JSON registry and derives current status through named evidence checkers. A CLI generates canonical JSON/Markdown artifacts; the console API consumes the same result, and the frontend renders it without a second score source.

**Tech Stack:** Python 3.11+ stdlib, FastAPI, pytest, React 18, TypeScript, Vitest.

## Global Constraints

- Preserve `L2_DRY_RUN_ONLY`; never enable live flags, credentials, or AI direct trading.
- Assessment is offline, deterministic, read-only, and fail-closed.
- `MANUAL_EVIDENCE_REQUIRED`, `UNVERIFIED`, and `FAILED` never count as passed.
- `NEEDS_EXTERNAL` and `POST_L2` cannot be satisfied by mock files or scaffolds.
- Backend and frontend consume one truth source; no hard-coded maturity percentages.
- Every stable batch ends with focused verification and a commit.

---

### Task 1: Requirements Registry And Assessment Core

**Files:**
- Create: `config/top_tier_requirements.json`
- Create: `services/governance/top_tier_assessment.py`
- Create: `tests/governance/test_top_tier_assessment.py`

**Interfaces:**
- Consumes: repository root and requirements JSON.
- Produces: `load_requirements(path)` and `assess_repository(repo_root, requirements_path)`.

- [x] Write failing tests for unique IDs, enum validation, red-line verdicts, maturity targets, and manual/external evidence.
- [x] Run `python -m pytest -q tests/governance/test_top_tier_assessment.py`; expect missing module/files.
- [x] Implement target-only registry covering all eleven domains and named checkers returning status, maturity, evidence, detail, and next action.
- [x] Derive `NOT_TOP_TIER` from any failed red line; derive `TOP_TIER_RESEARCH` only when every Tier A target passes; otherwise `IN_PROGRESS`.
- [x] Run focused tests; expect all pass.
- [ ] Commit with `feat(governance): add top-tier assessment core`.

### Task 2: CLI, Generated Matrix, And Anti-Inflation Governance

**Files:**
- Create: `scripts/assess_top_tier.py`
- Create: `docs/QUANTOS_TOP_TIER_STANDARD.md`
- Create: `docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md`
- Create: `evidence/top_tier_assessment.json`
- Create: `tests/governance/test_top_tier_boundary.py`
- Modify: `scripts/validate.py`

**Interfaces:**
- Consumes: Task 1 assessor.
- Produces: `--json-out`, `--matrix-out`, stable JSON, and grouped Markdown.

- [x] Write failing CLI determinism, malformed-registry, red-line, no-network, no-secret, no-broker, and no-order boundary tests.
- [x] Run `python -m pytest -q tests/governance/test_top_tier_boundary.py`; expect missing CLI/artifacts.
- [x] Implement deterministic rendering; honest `NOT_TOP_TIER` is exit 0, while malformed assessment is nonzero.
- [x] Generate artifacts with `python scripts/assess_top_tier.py` and verify byte-stable regeneration.
- [x] Add the new governance tests to the canonical validation suite.
- [ ] Commit with `feat(governance): generate top-tier capability matrix`.

### Task 3: Console API Assessment Snapshot

**Files:**
- Modify: `services/research/console_api/main.py`
- Modify: `tests/research/test_console_api_contract.py`
- Modify: `services/research/console_api/tests/test_console_api.py`

**Interfaces:**
- Consumes: Task 1 assessor and existing `_repo_inventory()`.
- Produces: `/api/v1/console/system-completeness` with assessment, domains, requirements, red lines, summary, safety boundary, and inventory.

- [x] Update contract tests first: require `TOP_TIER_EVIDENCE_ASSESSMENT`, valid system verdict, requirement evidence, and no `completion_score`.
- [x] Run focused backend tests; expect RED against the legacy hand-written score payload.
- [x] Replace the legacy payload with the pure assessor plus supporting repository inventory; preserve read-only markers and async offload.
- [x] Run console API, contract, and async-offload tests; expect all pass.
- [ ] Commit with `feat(console-api): expose top-tier evidence assessment`.

### Task 4: Evidence-Driven System Completeness Page

**Files:**
- Modify: `apps/research-console/src/lib/types/research.ts`
- Modify: `apps/research-console/src/pages/SystemCompleteness.tsx`
- Modify: `apps/research-console/src/pages/__tests__/pages.smoke.test.tsx`
- Modify: `apps/research-console/src/__tests__/app.integration.test.tsx`

**Interfaces:**
- Consumes: Task 3 payload through `fetchSystemCompletenessSnapshot()`.
- Produces: Tier A/Tier B domain views, red-line panel, requirement evidence table, and honest no-score fallback.

- [x] Add failing assertions for Tier A/Tier B, Red Lines, evidence status, and absence of Completion Score UI.
- [x] Run the focused frontend tests; expect RED.
- [x] Define exact payload types and remove `fallbackModuleSeeds`, `completionTone`, score histograms, and all hard-coded completion percentages.
- [x] Render verdicts, maturity, evidence, gap, and next action; fallback reads `UNVERIFIED — assessment artifact unavailable`.
- [x] Run `npm run typecheck`, `npm test`, and `npm run build`; expect all exit zero.
- [ ] Commit with `feat(console): render evidence-driven top-tier assessment`.

### Task 5: Project State, Full Gate, And Publication

**Files:**
- Modify: `docs/PROJECT_STATE.md`
- Modify: `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`
- Regenerate: `docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md`
- Regenerate: `evidence/top_tier_assessment.json`

**Interfaces:**
- Consumes: all Wave 0 artifacts and verification output.
- Produces: one current roadmap pointer and exact Wave 0 evidence.

- [x] Mark the legacy roadmap as historical input and point current prioritization to stable requirement IDs.
- [x] Update project state with assessor verdict, counts, red lines, focused tests, frontend checks, and full gate evidence; do not mark later Waves complete.
- [x] Run `python scripts/assess_top_tier.py`.
- [x] Run `python scripts/validate.py`.
- [x] From `apps/research-console`, run `npm run typecheck`, `npm test`, and `npm run build`.
- [ ] Commit with `docs: establish top-tier Wave 0 baseline`.
- [ ] Push `codex/top-tier-wave0`; pre-push gate must remain enabled and green.
