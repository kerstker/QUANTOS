# Phase 3A - Legal / Compliance Evidence Checklist

This checklist supports E03. It records whether the broker test environment scope is legally and operationally approved. It does not authorize production trading.

## Required Inputs

| ID | Evidence | Owner | Status | Evidence Location |
|----|----------|-------|--------|-------------------|
| L01 | Broker test environment confirmation | Broker Relationship Owner | PENDING | _______________ |
| L02 | Broker agreement or testing authorization | Compliance Officer | PENDING | _______________ |
| L03 | Permitted test instruments and sessions | Trading System Owner | PENDING | _______________ |
| L04 | Data usage terms for test market data | Compliance Officer | PENDING | _______________ |
| L05 | Order testing permission for synthetic/test orders | Compliance Officer | PENDING | _______________ |
| L06 | Record retention requirement | Compliance Officer | PENDING | _______________ |
| L07 | Internal governance signoff reference | CTO | PENDING | `GOVERNANCE_SIGNOFF_REGISTER.md` |
| L08 | No production credential assertion | Security Officer | APPROVED | `governance_scanner_report.md` |
| L09 | No production trading assertion | Risk Officer | APPROVED | `governance_scanner_report.md` |

## Review Questions

| Question | Answer | Reviewer |
|----------|--------|----------|
| Is the request limited to a broker test environment? | _______________ | Compliance Officer |
| Are production credentials excluded? | Yes | Compliance Officer |
| Are production instruments excluded unless explicitly approved as test symbols? | _______________ | Compliance Officer |
| Are audit and retention requirements documented? | _______________ | Compliance Officer |
| Is UAT limited to synthetic/test orders? | _______________ | Compliance Officer |
| Is any regulatory notification required before testing? | _______________ | Compliance Officer |

## E03 Decision

| Decision Field | Value |
|----------------|-------|
| E03 status | PENDING / APPROVED / REJECTED |
| Approved by | _______________ |
| Approval date | _______________ |
| Expiry date | _______________ |
| Conditions | _______________ |

## Rejection Handling

If any critical legal/compliance item is rejected, Phase 3A remains NO_GO and no adapter implementation may begin.
