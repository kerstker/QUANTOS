# Phase 3A - Next Actions

## Current State

QUANTOS remains at L2_DRY_RUN_ONLY. Internal dry-run evidence is complete enough to move the project to external dependency closure, but Phase 3A implementation is still not authorized.

## Immediate Actions

| Priority | Action | Owner | Evidence Required |
|----------|--------|-------|-------------------|
| 1 | Collect governance signatures for E01 | CTO | Completed `GOVERNANCE_SIGNOFF_REGISTER.md` |
| 2 | Send broker test environment request package for E02 | Broker Relationship Owner | Broker response intake record |
| 3 | Prepare legal/compliance review for E03 | Compliance Officer | Completed `LEGAL_COMPLIANCE_EVIDENCE_CHECKLIST.md` after broker response |
| 4 | Prepare Vault credential intake workflow for E04 | Security Officer | Vault path, policy, and access review evidence |
| 5 | Prepare network/IP whitelist review for E06 | Security Officer | Firewall review and broker whitelist evidence |
| 6 | Schedule Go/No-Go pre-review | CTO | Meeting notes assigning E01-E06 closure owners |

## Waiting on Broker

| Needed From Broker | Blocks |
|--------------------|--------|
| Test environment availability and account scope | E02 |
| Broker agreement or testing authorization | E03 |
| Test credential delivery process | E04 |
| IP/port/session details | E06 |
| Protocol documentation and UAT window | Go/No-Go scheduling |

## Do Not Start

Do not implement Phase 3A adapter code, real CTP, real FIX, broker SDK imports, socket connections, credential files, live order APIs, or live trading frontend controls.

## Phase 3A Gate

Phase 3A can be considered only after E01, E02, E03, E04, and E06 are APPROVED and the Go/No-Go review records a GO decision.
