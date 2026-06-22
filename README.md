# QUANTOS 鈥?Autonomous AI Quantitative Research & Trading System

**Readiness Level**: L2_DRY_RUN_ONLY
**Repository**: Standalone 鈥?separated from EXPY monorepo

## Quick Start

```bash
# Research Console (frontend 鈥?9 workspaces)
cd apps/research-console
npm install && npm run dev
# 鈫?http://localhost:3100/research

# Governance Scanner
python scripts/run_paper_to_live_governance_check.py
```

## Project Structure

```
QUANTOS/
鈹溾攢鈹€ services/          # Backend services (24 total)
鈹?  鈹溾攢鈹€ data/          # Market data, ticks, quality, features
鈹?  鈹溾攢鈹€ research/      # Datasets, factors, backtests, experiments, agents
鈹?  鈹溾攢鈹€ alpha/         # Alpha discovery engine, validation gate
鈹?  鈹溾攢鈹€ portfolio/     # Portfolio construction, optimization
鈹?  鈹溾攢鈹€ risk/          # Research risk engine
鈹?  鈹斺攢鈹€ trading/       # Pre-trade risk, Sim OMS, Sim EMS, Exchange Sim, Dry-run adapters
鈹?鈹溾攢鈹€ apps/research-console/  # Vite + React + TypeScript + Tailwind
鈹溾攢鈹€ platform/          # K8s, Kafka (70 topics), Vault, Istio, monitoring
鈹溾攢鈹€ docs/              # Architecture & Governance (phase2a, phase2f, phase2h, phase3a)
鈹溾攢鈹€ evidence/phase3a/  # Phase 3A evidence artifacts (10 items)
鈹溾攢鈹€ scripts/           # Gate runners + governance scanner
鈹溾攢鈹€ proto/             # gRPC service definitions
鈹溾攢鈹€ schemas/           # Avro event schemas
鈹斺攢鈹€ tests/integration/ # Integration tests (200+)
```

## Architecture

DDD | CQRS | Event-Driven | Hexagonal | Fail-Closed | 150+ rejection reasons

## Phase Status

| Phase | Status |
|-------|--------|
| Phase 1 鈥?Research OS | FROZEN v1.0 鉁?|
| Phase 2 鈥?Trading Simulation | FROZEN L2 v1.0 鉁?|
| Phase 3A 鈥?Broker Test | BLOCKED 猬?(10/15 criteria) |

## Safety Boundary

- 鉂?Zero real CTP/FIX connectivity 鈥?AST verified
- 鉂?Zero broker SDK imports (CTP SDK, QuickFIX) 鈥?AST verified
- 鉂?Zero real credentials 鈥?all placeholder fields
- 鉂?Zero live trading code 鈥?live_trading_allowed=false everywhere
- 鉁?All trading is simulated or synthetic
- 鉁?8 safety scanners, 0 violations

## Handoff

See `CONTEXT_HANDOFF_PHASE3A.md` for complete project context.
