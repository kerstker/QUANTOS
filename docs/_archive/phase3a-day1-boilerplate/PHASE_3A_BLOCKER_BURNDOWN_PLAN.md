# Phase 3A - Blocker Burn-Down Plan

## Executive Summary

**Current**: L2_DRY_RUN_ONLY
**Target**: L3_BROKER_TEST_ENV_READY
**Entry Criteria**: 10/15 APPROVED
**Remaining Blockers**: 5

The remaining blockers are governance or external dependency items. Phase 3A implementation remains blocked.

## Remaining Blockers

| ID | Blocker | Category | Status | Owner | Depends On | Unblock Condition |
|----|---------|----------|--------|-------|------------|-------------------|
| E01 | Governance approval | Governance | PENDING | CTO | None | Required roles sign the governance register |
| E02 | Broker test environment account | Broker | PENDING | Broker Relationship Owner | None | Broker confirms test environment access and account scope |
| E03 | Legal/compliance approval | Compliance | PENDING | Compliance Officer | E02 | Broker agreement and test scope are legally approved |
| E04 | Test credentials in Vault | Credentials | PENDING | Security Officer | E02 | Test credentials are stored only in Vault with access policy evidence |
| E06 | Network access reviewed | Network | PENDING | Security Officer | E02 | Broker IP/port details, firewall review, and whitelist evidence are archived |

## Dependency Chain

```
E01 can move independently.
E02 is the external critical path.
E02 -> E03, E04, E06.
All five blockers must close before formal Phase 3A approval.
```

## Burn-Down Sequence

| Sequence | Action | Owner | Evidence Produced |
|----------|--------|-------|-------------------|
| 1 | Send internal approval memo and collect signatures | CTO | Completed governance signoff register |
| 1 | Send broker test environment request package | Broker Relationship Owner | Broker response intake record |
| 2 | Review broker agreement and test scope | Compliance Officer | Legal/compliance evidence checklist |
| 2 | Prepare Vault path and credential handling workflow | Security Officer | Vault path and access control evidence |
| 2 | Prepare network review against broker IP/port details | Security Officer | Network/IP whitelist evidence |
| 3 | Hold Go/No-Go review after E01-E06 are closed | CTO | Go/No-Go decision record |

## Current No-Go Reasons

1. Governance signatures are incomplete.
2. Broker test environment access is not confirmed.
3. Legal/compliance approval depends on broker materials.
4. Test credentials have not been received or placed in Vault.
5. Network/IP whitelist review depends on broker endpoint details.

## Safety Boundary

No real CTP/FIX implementation, SDK import, socket connection, credential file, live order API, or frontend trading control is authorized by this plan.
