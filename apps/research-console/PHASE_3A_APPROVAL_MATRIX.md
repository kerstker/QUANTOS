# Phase 3A - Approval Matrix

## 8 Roles x 15 Criteria

| Role | Approval Scope | Relevant Criteria | Current Status | Veto | Expiry | Comments |
|------|----------------|-------------------|----------------|------|--------|----------|
| CTO | Technical readiness and final governance | E01, E07, E10, E11, E12, E15 | PENDING SIGNOFF | Yes | 90 days | E01 still requires signatures |
| Quant Research Lead | Strategy quality and research evidence | Phase 1 evidence | PENDING SIGNOFF | No | 90 days | Advisory for broker test readiness |
| Risk Officer | Risk controls and kill switch evidence | E08, E09, E14 | PENDING SIGNOFF | Yes | 90 days | E08/E09/E14 evidence is approved |
| Compliance Officer | Legal, compliance, audit scope | E03, E13 | PENDING E03 | Yes | 90 days | Depends on broker agreement/test scope |
| Security Officer | Credential isolation and network | E04, E05, E06, E13 | PENDING E04/E06 | Yes | 90 days | E05/E13 approved; E04/E06 pending |
| Operations Lead | Runbooks, UAT, incident response | E07, E10, E11, E12 | PENDING SIGNOFF | No | 90 days | Evidence approved; signature still needed |
| Trading System Owner | System readiness and dry-run adapter evidence | E02, E08, E09, E15 | PENDING E02 | No | 90 days | E08/E09/E15 approved; E02 pending |
| Broker Relationship Owner | Broker account and test environment | E02 | PENDING E02 | No | 90 days | External critical path |

## Approval Order

1. CTO starts E01 governance signature workflow.
2. Broker Relationship Owner obtains E02 broker test environment confirmation.
3. Compliance Officer completes E03 after broker materials arrive.
4. Security Officer completes E04 and E06 after broker credentials and endpoint details arrive.
5. CTO convenes Go/No-Go review only after E01-E06 are approved.

## Veto Rules

Risk Officer, Compliance Officer, and Security Officer have instant veto authority. Any veto keeps Phase 3A at NO_GO until resolved and re-reviewed.

## Approval Expiry

Approvals expire after 90 days. Any system change, personnel change, broker change, or incident triggers re-approval.
