# QUANTOS 鈥?Context Handoff for Next Claude Code Session

## Project
**QUANTOS** 鈥?Institutional-grade autonomous AI quant research and trading simulation system.

## Readiness Level
**L2_DRY_RUN_ONLY** 鈥?All trading is simulated or synthetic. Zero real connectivity.

## Status

| Phase | Status |
|-------|--------|
| Phase 1 Research OS | FROZEN v1.0 鉁?|
| Phase 2 Trading Simulation + Governance | FROZEN L2 v1.0 鉁?|
| Phase 3A Broker Test Environment Adapter | **BLOCKED** 猬?|
| Phase 3A implementation | **NOT AUTHORIZED** |

---

## Completed Phases

| Phase | Name |
|-------|------|
| Phase 1 | AI Quant Research OS 鈥?17 services, 350+ tests |
| Phase 2A | Trading Boundary Architecture 鈥?14 docs |
| Phase 2B | Pre-Trade Risk Gate 鈥?27 rejection reasons |
| Phase 2B.1 | Pre-Trade Risk Integration Gate 鈥?37 tests |
| Phase 2C | Simulated OMS 鈥?21 rejection reasons |
| Phase 2C.1 | Simulated OMS Gate 鈥?38 tests |
| Phase 2D | Simulated EMS 鈥?23 rejection reasons |
| Phase 2D.1 | Simulated EMS Gate 鈥?43 tests |
| Phase 2E | Exchange Simulator 鈥?29 rejection reasons |
| Phase 2E.1 | Exchange Simulator Gate 鈥?50 tests |
| Phase 2F | CTP/FIX Adapter Architecture 鈥?16 docs |
| Phase 2G | Dry-Run CTP/FIX Adapters 鈥?22 rejection reasons |
| Phase 2G.1 | Dry-Run Adapter Gate 鈥?65 tests |
| Phase 2H | Paper-to-Live Governance 鈥?15 docs + scanner |
| Phase 3A | Entry Evidence Tracker + Pre-Entry Pack + Internal Evidence Sprint |

---

## Phase 3A Entry Criteria: 10/15 APPROVED

**Approved (10):**
- E05: No credentials in code 鉁?- E07: Broker readiness checklist 鉁?- E08: Kill switch tested (5/5 levels) 鉁?- E09: Rollback tested (L3鈫扡2) 鉁?- E10: DR runbooks approved 鉁?- E11: UAT plan approved 鉁?- E12: Incident response approved 鉁?- E13: No production credentials 鉁?- E14: No production trading 鉁?- E15: Phase 2G.1 tests pass (65/65) 鉁?
**Pending (5 鈥?external/governance):**
- E01: Governance approval (8 role signatures)
- E02: Broker test environment account 鈽?CRITICAL PATH
- E03: Legal/compliance approval
- E04: Test credentials in Vault
- E06: Network access reviewed

**Dependency chain:** E02 (Broker) 鈫?E03, E04, E06.  E01 (Governance) is independent.

---

## Internal Evidence (10 artifacts in evidence/phase3a/)

1. governance_scanner_report.md 鈥?0 violations
2. dry_run_adapter_gate_evidence.md 鈥?65/65 passed
3. dry_run_ctp_mapping_evidence.md 鈥?placeholder fields only
4. dry_run_fix_mapping_evidence.md 鈥?conceptual tags only
5. kill_switch_drill_report.md 鈥?5/5 passed
6. rollback_drill_report.md 鈥?L3鈫扡2 passed
7. dr_dry_run_report.md 鈥?9/9 verified
8. uat_dry_run_report.md 鈥?16/16 verified
9. audit_evidence_package.md
10. event_evidence_package.md

---

## Key File Locations

| What | Where |
|------|-------|
| Trading services | `services/trading/` (pre_trade_risk, simulated_oms, simulated_ems, exchange_simulator, dry_run_adapters) |
| Research services | `services/research/`, `services/alpha/`, `services/data/`, `services/portfolio/`, `services/risk/` |
| Architecture docs | `docs/phase2a/` (10), `docs/phase2f/` (16) |
| Governance docs | `docs/phase2h/` (15) |
| Phase 3A docs | `docs/phase3a/` (17) |
| Evidence | `evidence/phase3a/` (10) |
| Integration tests | `tests/integration/` |
| Research Console | `apps/research-console/` (Vite + React + Tailwind, 9 workspaces) |
| Platform manifests | `platform/` (K8s, Kafka, Vault, monitoring) |
| Governance scanner | `scripts/run_paper_to_live_governance_check.py` |

---

## Strict Forbidden Actions

| 鉂?Forbidden | Reason |
|-------------|--------|
| Implement real CTP/FIX | L2_DRY_RUN_ONLY only |
| Import CTP SDK, QuickFIX, broker SDK | No SDK until Phase 3A approved |
| Create socket connections to broker/exchange | No real connectivity |
| Create credential files (.env with real values) | Credentials only in Vault |
| Create live order APIs | Only dry-run/synthetic endpoints |
| Create live trading frontend buttons | Research console only |
| Set live_trading_allowed=true | Must remain false |
| Change readiness level from L2 | Phase 3A not yet approved |
| Approve Phase 3A implementation | Only when all 15 criteria APPROVED |

---

## How to Start the Research Console

```bash
cd apps/research-console
npm install && npm run dev
# 鈫?http://localhost:3100/research
```

## How to Run the Governance Scanner

```bash
python scripts/run_paper_to_live_governance_check.py
```

---

## Expected Next State

**PHASE_3A_EXTERNAL_DEPENDENCY_PACKAGE_READY** (all docs produced, 10/15 criteria approved, 5 remaining external)

**PHASE_3A_STILL_BLOCKED_EXTERNAL_DEPENDENCIES** (if broker/governance remains pending)

Phase 3A implementation MUST NOT be authorized unless all 15 entry criteria show APPROVED.

---

*Handoff prepared 2026-06-11. QUANTOS is at L2_DRY_RUN_ONLY. Phase 3A is BLOCKED.*
