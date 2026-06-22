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
```

Note: all backend services now live under the repo-root `services/` tree
(unified in Phase 1; `apps/research-console/` is frontend-only). `scripts/validate.py`
runs the full suite from the repo root — prefer it over a bare `pytest`.
