# QUANTOS — AI Quantitative Trading Research Platform

**Readiness:** `L2_DRY_RUN_ONLY` — research and dry-run simulation only.
No live trading, no broker connectivity, no real credentials.

> **Status honesty:** this repository is an **early-stage scaffold (~10% built)**.
> The sections below describe what *actually exists today*; everything else in
> the [master blueprint](QUANTOS_MASTER_BLUEPRINT.md) is **PLANNED**. See the
> [development program plan](docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md) for the
> phased roadmap and [`CLAUDE.md`](CLAUDE.md) for the agent operating guide.

## What exists today

| Area | Path | Status |
|---|---|---|
| Research console API | `services/research/console_api/` | Real FastAPI service, but serves **deterministic mock snapshots** (synthetic data), not real research |
| Research-risk gate | `services/risk/research_risk/domain/` | Real, deterministic, fail-closed 9-engine gate (no tests yet; no kill-switch — Phase 8) |
| Factor engine | `services/research/factor_engine/domain/` | Real IC/ICIR math (on synthetic data) |
| Paper trading | `services/research/paper_trading/domain/` | Models real; PnL is a **placeholder** (`notional*0.001`) — real reconciliation is Phase 9 |
| Dry-run adapters | `apps/research-console/services/trading/dry_run_adapters/` | Real, tested, fail-closed (no broker SDK) |
| Research console (frontend) | `apps/research-console/src/` | React 18 + Vite 6 + TS (strict) + Tailwind; 25 pages, **mock-first** (not wired to a real backend) |

**Quarantined:** `apps/research-console/services/trading/pre_trade_risk/main.py`
is **non-functional** (missing modules + an undefined-name bug); preserved for
the Phase 8 rebuild.

**Empty scaffolding (PLANNED):** `services/{data,alpha,portfolio,execution,
gateway,intelligence,learning,trading}`, `platform/`, `schemas/`, `proto/`,
`config/`.

## Quick start

```bash
# Full fail-closed validation gate (governance scanner + all runnable tests)
python scripts/validate.py

# Frontend (mock-first) → http://localhost:3100/research
cd apps/research-console && npm ci && npm run dev
```

## Safety boundary (enforced in code + CI)

- `live_trading_allowed=False` on all trading dataclasses; the dry-run validator
  rejects any attempt to enable it.
- No broker / CTP / FIX SDK imports; no hardcoded credentials.
- The governance scanner (`apps/research-console/scripts/run_paper_to_live_governance_check.py`)
  and `tests/governance/` enforce these invariants on every run; CI runs
  `scripts/validate.py` and is fail-closed.

Live trading must remain impossible without explicit, multi-step human approval
(see blueprint §1, §5.2).
