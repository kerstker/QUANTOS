# QUANTOS — Agent Operating Guide

QUANTOS is a **safety-critical AI quantitative trading research platform**.
Authoritative spec: [`QUANTOS_MASTER_BLUEPRINT.md`](QUANTOS_MASTER_BLUEPRINT.md)
(11 layers, phases 0–14). Program plan:
[`docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md`](docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md).

Readiness: **L2_DRY_RUN_ONLY** — research + dry-run simulation only. No live
trading, no broker connectivity, no real credentials.

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

## What actually exists (verified, ~10% built)

Real code:
- `services/research/console_api/main.py` — FastAPI **read-only mock snapshot**
  generator for the frontend (deterministic synthetic data; not real research).
- `services/risk/research_risk/domain/` — 9-engine research-risk gate
  (deterministic, fail-closed). No tests yet; no kill-switch (Phase 8).
- `services/research/factor_engine/domain/model.py` — real IC/ICIR math
  (on synthetic data).
- `services/research/paper_trading/domain/` — models + **placeholder** PnL
  (`notional*0.001`; real reconciliation is Phase 9).
- `services/research/experiment_registry/` — durable persistence (stdlib sqlite3):
  `ExperimentRegistry.load_into(catalog)` at session start + `.save(catalog)` at
  end make the catalog-wide Deflated-Sharpe trial count + var_sr survive across
  processes (no longer reset on exit); `.record_evaluation(...)` writes an
  immutable, reproducible artifact and optionally emits an `EXPERIMENT_RECORDED`
  event into the global audit chain. **Opt-in**: `AlphaCatalog`/`AlphaFactory`
  behave exactly as before when the registry is unused. **Fail-closed**: loading
  an uninitialized/corrupt store raises (never silently resets multiple testing).
- `services/trading/dry_run_adapters/` — real, tested, fail-closed dry-run
  adapters (no broker SDK).
- `apps/research-console/src/` — React 18 + Vite 6 + TS (strict) + Tailwind;
  25 pages, mock-first (not wired to a real backend).

Quarantined / broken:
- `services/trading/pre_trade_risk/main.py` — **NON-FUNCTIONAL** (missing domain
  modules + undefined-name bug). Do not wire; rebuild in Phase 8.

Shared contracts:
- `quantos/contracts/` — canonical domain contracts (SafetyConfig §1.2,
  RejectionReason §8, OrderState §7, RiskDecision). Single source of truth.

Empty scaffolding (PLANNED): `services/{data,alpha,portfolio,execution,gateway,
intelligence,learning}`, `platform/`, `schemas/`, `proto/`, `config/`.

## Commands

```bash
# Full fail-closed gate (governance scanner + all runnable test suites)
python scripts/validate.py

# Governance scanner only
python apps/research-console/scripts/run_paper_to_live_governance_check.py

# Frontend (mock-first; opens http://localhost:3100/research)
cd apps/research-console && npm ci && npm run typecheck && npm run build && npm test

# Frontend against the REAL read-only backend (Phase 12 wiring)
#   1) start the console API (read-only computed snapshots, no broker/OMS):
python -m uvicorn services.research.console_api.main:app --host 127.0.0.1 --port 8400
#   2) point the console at it and disable mock mode (CORS allows :3100 only):
cd apps/research-console && VITE_API_URL=http://127.0.0.1:8400 VITE_MOCK_MODE=false npm run dev
```

Note: all backend services now live under the repo-root `services/` tree
(unified in Phase 1; `apps/research-console/` is frontend-only). `scripts/validate.py`
runs the full suite from the repo root — prefer it over a bare `pytest`.

Phase 12 wiring is contract-guarded on BOTH sides: `tests/research/test_console_api_contract.py`
(in the gate) asserts every endpoint returns the exact top-level keys the
frontend `*Payload` types read, and the console API client validates the
read-only snapshot markers at runtime (`assertConsoleSnapshot`) so a drifted /
non-snapshot 200 surfaces as a visible error + honest mock fallback rather than
silently rendering `undefined`. Change a payload shape → change both the TS type
and the contract test in the same commit.
