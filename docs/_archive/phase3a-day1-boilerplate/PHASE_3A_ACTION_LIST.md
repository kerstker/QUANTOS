# Phase 3A - Action List

## Current Gate State

| Metric | Value |
|--------|-------|
| Current readiness level | L2_DRY_RUN_ONLY |
| Target readiness level | L3_BROKER_TEST_ENV_READY |
| Entry criteria approved | 10/15 |
| Remaining blockers | 5 |
| Phase 3A implementation | NOT AUTHORIZED |

## Actions That Can Start Now

| ID | Action | Owner | Evidence Required |
|----|--------|-------|-------------------|
| A1 | Collect governance signatures for E01 | CTO | Signed governance register |
| A2 | Send broker test environment request package for E02 | Broker Relationship Owner | Broker response intake record |
| A3 | Prepare legal/compliance review package for E03 | Compliance Officer | Legal/compliance checklist ready for broker materials |
| A4 | Prepare Vault credential intake path for E04 | Security Officer | Vault path and access policy draft |
| A5 | Prepare network review workflow for E06 | Security Officer | Firewall and whitelist review template |
| A6 | Schedule Go/No-Go pre-review | CTO | Owner/date assignments for E01-E06 |

## Actions Waiting on Broker

| ID | Action | Owner | Dependency |
|----|--------|-------|------------|
| B1 | Confirm broker test environment account | Broker Relationship Owner | Broker response |
| B2 | Archive broker agreement or testing authorization | Compliance Officer | Broker response |
| B3 | Intake test credentials into Vault | Security Officer | Broker test credentials received |
| B4 | Complete IP whitelist and firewall review | Security Officer | Broker IP/port details received |
| B5 | Archive protocol documentation and UAT window | Trading System Owner | Broker technical documents |

## Blocks Phase 3A

| Criterion | Blocker | Status |
|-----------|---------|--------|
| E01 | Governance approval incomplete | PENDING |
| E02 | Broker test environment account not confirmed | PENDING |
| E03 | Legal/compliance approval not complete | PENDING |
| E04 | Test credentials not provisioned in Vault | PENDING |
| E06 | Network access not reviewed | PENDING |

## Immediate Priorities

1. CTO starts E01 signoff collection.
2. Broker Relationship Owner sends `BROKER_TEST_ENV_REQUEST_PACKAGE.md`.
3. Security Officer prepares Vault and network evidence templates without collecting any credentials in repo.

Do not start adapter implementation until all 15 entry criteria are APPROVED.
