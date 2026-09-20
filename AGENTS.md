# QUANTOS — Agent Operating Guide

QUANTOS is a **safety-critical AI quantitative trading research platform**.
Authoritative spec: [`QUANTOS_MASTER_BLUEPRINT.md`](QUANTOS_MASTER_BLUEPRINT.md)
(11 layers, phases 0–14). Program plan:
[`docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md`](docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md).

Readiness: **L2_DRY_RUN_ONLY (default)** — 分层表述,三层实况:

> **2026-08-29 RED/P0 authority override:** The legacy multi-period dry-run,
> candidate-selection, score reuse, and V1 paper-fill paths have invalid daily
> available-time, same-period execution, perpetual cash-flow, contract-quantity,
> margin/liquidation, capacity/risk, and run-identity semantics.  Every historical
> Sharpe/P&L or statement in project records that calls those paths "authoritative",
> "deployable", or paper/live evidence is superseded and must be treated as
> `INVALIDATED_PERP_LEDGER_V1` diagnostic history only.  Those entrypoints are
> quarantined.  The Decimal linear-perpetual V2 runner produces
> `TECHNICAL_VALIDATION_ONLY`, never authority.  Until an independently controlled
> signed source/result authority is integrated, `is_authoritative_backtest_record`
> deliberately admits no record; candidate promotion and new paper-fill
> persistence remain blocked.  Raw public-data collection and read-only forward
> observation may continue.  Do not regenerate or reuse historical profitability
> claims through the legacy paths.

- **L2 研究 + 干跑(默认态)**:研究与全量测试默认运行在 L2,mainnet
  实盘被多重结构性人工门封锁(见下)。
- **Testnet 已联通**:`live/` 执行栈已存在并联调 —— Binance/Bybit
  期货 testnet 适配器(`live/exchange/`)、`live/pipeline.py::submit_live_batch`
  (attestation→plan→gate→mandate→ledger→place→confirm→audit 全链)、
  `tools/live_ops_api.py`(port 8440 只读运维 API)。
- **Mainnet 被多重人工门结构封锁**:mainnet 适配器构造即
  `raise SafetyViolation`(`SafetyConfig.live_trading_armed` 默认 False);
  下单必须经过 validation attestation + 人工 mandate(AUTO 拒绝)+
  pre-trade gate + kill-switch + 账本预记账;MEXC 现货 mainnet 适配器
  (建设中)同样遵循。MEXC 无现货 testnet → 毕业路径 = 干跑 → 小额真实 +
  人工 mandate → 规模化。无真凭证落盘承诺不变(`key/` gitignored,
  只写格式文档,绝不写真实密钥)。

## Safety boundary (non-negotiable — blueprint §1)

- `LIVE_TRADING_ENABLED=false`, `REAL_BROKER_ENABLED=false`,
  `ALLOW_ORDER_SUBMISSION=false`, `ALLOW_AI_DIRECT_TRADING=false`,
  `REQUIRE_HUMAN_APPROVAL=true`. Live trading must remain impossible without
  explicit multi-step human approval.
- Never commit secrets / broker credentials; never import a broker/CTP/FIX SDK;
  never bypass or weaken risk gates; never use future data / introduce
  lookahead; AI agents never place live orders or touch credentials.
- Keep research / paper / live strictly isolated; strategy (intent) separate
  from risk (permission); broker logic behind adapters.
- Tests-first; smallest safe change; never hide or delete failing tests.

These invariants are machine-enforced by the governance scanner (below) and the
tests in `tests/governance/`.

**Threat model (two layers).** The agent guardrails (`services/intelligence`),
the alpha lifecycle state machine, and the pre-trade risk gate are RUNTIME
controls over the *actor* and *evidence* passed in: a non-human actor cannot reach
APPROVED, reset the kill-switch, trade live, or touch credentials. They do NOT —
and in a single process cannot — defend against TRUSTED CODE that constructs a
`HUMAN` `Actor`, calls `AlphaLifecycleManager`/`KillSwitch`/`catalog._set_state`
directly, or otherwise bypasses the chokepoint. That build-time surface is
governed by code review, the governance scanner, and (Phase 14) deployment/process
isolation. The autonomous agent path holds only a non-human actor and reaches the
state machine/gate only through the hardened `ResearchAgentLoop`; direct manager/
catalog access is for human-controlled workflows and is not an agent API.

## Current work and context

- **2026-09-20 research-first authorization:** Current delivery priority is the
  [DGX research-first decision](docs/engineering/specs/2026-09-20-dgx-research-first.md).
  Defer institutional off-host recovery, external backup and identity infrastructure
  as prerequisites for local research. Retain PIT, realistic costs/risk, locked OOS,
  trial history and existing trading authorization boundaries. Codex owns architecture,
  research design and review; Claude Code may implement bounded, file-disjoint tasks.
  The new research assessment view is pending implementation; never relabel deferred
  TOP22 requirements or unprofitable strategies as PASS.
- The user authorized implementation of the September 5 DGX roadmap. Read the
  [current roadmap](docs/engineering/plans/2026-09-05-dgx-engine-foundation-roadmap-review.md),
  its opening execution note, the
  [increment record](docs/engineering/specs/2026-09-05-dgx-engine-foundation-increment1.md)
  and the [DGX runbook](docs/runbooks/dgx-engine-development.md) for engine work.
  The original draft's review-only wording is historical; authorization covers
  development, not release, economic authority or live operations.
- At this checkpoint B0 and the first E1/E2 accounting changes exist. E2.1,
  D1/I1/V1 and operations/release work remain incomplete. Verify code, HEAD and
  tests before extending this snapshot. Do not substitute older roadmap milestones.
- Read the current header and task-relevant sections of
  [PROJECT_STATE](docs/PROJECT_STATE.md), and relevant blueprint/spec sections.
  Use `rg -n` / `rg --files` and bounded reads; do not load the entire state diary,
  historical guide, generated evidence or full test logs by default.
- The [historical capability guide](docs/reports/2026-09-05-agent-guide-history.md)
  preserves previous detail for targeted lookup. Add new progress to the relevant
  implementation record or PROJECT_STATE, not this always-loaded instruction file.

## Development workflow

- Carry authorized work through implementation, relevant validation and a reviewable
  result. Routine reads, reversible edits, debugging and local checks do not need
  another confirmation. Preserve unrelated user edits and use `codex/` for new branches.
- Keep permission boundaries concrete: do not infer authorization to deploy,
  switch resident services, write production state, change economic authority,
  send external messages or perform destructive actions from a request to develop.
  Prepare the reviewable result before seeking any required final authorization.
- Use subagents only when the user or applicable instructions explicitly request
  delegation. When authorized, assign independent bounded work with clear file
  ownership; integrate and verify the results in the primary task.
- Respect existing human gates. Do not fabricate a HUMAN actor, reset a kill-switch,
  alter locked OOS, promote candidates, delete experiments/audit history, implement
  withdrawals/transfers or auto-tune live parameters to make a check pass.
- Report the outcome, relevant evidence and remaining limitations concisely in the
  user's language. During substantial work, provide short progress updates.

## DGX environment and isolation

- Use the isolated Python 3.12 environment and ARM64 hash-pinned lockfile specified
  in the DGX runbook. The shell's default `python` may resolve to Python 3.13;
  invoke the verified interpreter explicitly. Do not upgrade dependencies or change
  model/reasoning settings solely to chase nominal speed.
- Development venv at the September 5 checkpoint:
  `/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905/venv`.
  Node: `/home/kerstker/.nvm/versions/node/v24.16.0/bin/node`.
  Check paths before use; these are machine-local snapshots, not portable defaults.
- Keep `/opt/quantos/releases/<commit>` immutable and `/srv/quantos/state` out of
  writable test fixtures. Preserve `/home/kerstker/src/QUANTOS` user changes.
  Use task-specific output directories and frozen read-only research inputs.
- `scripts/dgx_develop.py` verifies the expected full HEAD, architecture, Python,
  dependencies and explicit Node path. See the runbook for its command; supply a
  reviewed full commit hash. Its `--test` runs the offline execution/risk/entry
  subset with isolated outputs and a restricted child environment. It does not
  establish release readiness or economic authority.

## Validation and commands

- For behavioral changes, first reproduce the failure with a meaningful regression,
  then make the smallest safe change. Keep existing failing tests visible.
- Run tests relevant to the affected behavior and boundaries. For a documentation
  change, run link/hygiene and applicable instruction/launcher contract checks;
  do not add tests that merely repeat prose. Once relevant checks pass, repeat or
  broaden them only for new changes, failures, required gates or unresolved risks.
- `scripts/validate.py` remains the canonical full fail-closed gate at milestone /
  release acceptance. A targeted green suite is incremental evidence, never a claim
  that the full gate passed. Follow any more specific mandatory gate in the plan.
- Git hooks must not be silently skipped. If this checkout's hooks are not executable,
  run the actual hook explicitly with the verified development interpreter on PATH
  and record the result before committing. Do not use `--no-verify` to bypass failures.

Commands below use `python` as shorthand for that verified interpreter:

```bash
# Canonical full gate
python scripts/validate.py
# Fast repository and governance checks
python scripts/check_repository_hygiene.py
python apps/research-console/scripts/run_paper_to_live_governance_check.py
# Frontend checks, from apps/research-console (install only when needed)
npm run typecheck
npm run build
npm test
```

## Architecture and service boundaries

- Backend code is under root `services/`; `apps/research-console/` is frontend.
  Inspect real modules before implementing an apparent empty scaffold.
- Keep pure research math offline; network adapters belong in approved boundary
  modules. Preserve the service import graph and governance scanner constraints.
- PIT reads must respect decision-time availability, not merely event-time order.
  `fetch_bars_pit` filters each bar's knowledge time against its own event time;
  this alone does not establish arbitrary decision-time `as_of(t)` safety.
- Keep one canonical perpetual cash-flow/fee/position ledger. Explicitly version
  rounding, cost basis, venue rules and run identity. Missing evidence is UNKNOWN
  or a fail-closed rejection, never fabricated profitability or authority.
- `services/risk/pre_trade/` is the supported risk gate; quarantined legacy
  `trading/pre_trade_risk` and V1 research entrypoints must not be reconnected.
- Local development service entrypoints (starting services is a separate action):
  read-only console `services.console.main:app` on 8400; guarded research runner
  `services.research_runner.main:app` on 8420. The runner only accepts whitelisted
  research mutations and cannot access order/broker/credential paths. Live-ops
  API `tools/live_ops_api.py` on 8440 is read-only.
- A console payload shape change must update the corresponding frontend TypeScript
  contract and `tests/research/test_console_api_contract.py` in the same change.
  Preserve snapshot markers and honest missing-data behavior.
