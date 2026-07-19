# Process-declared event worker assurance

Date: 2026-07-15

Status: implemented and exercised under `L2_DRY_RUN_ONLY`.

## Problem

The single-node event consumer group already committed `worker_id` into its
append-only coordination and delivery journals, but the value was an unqualified
caller-provided string. A console or audit reader could mistake that identifier
for an authenticated workload identity. External OIDC/mTLS workload identity is
not available in the current environment and must not be simulated locally.

## Implemented contract

Every new heartbeat now converts the declared worker name to a reserved internal
identity with one fixed assurance:

```text
PROCESS_DECLARED_UNAUTHENTICATED
```

The internal representation is included unchanged in both existing SHA-256
chains:

- `event_consumer_group_journal.worker_id` for membership and rebalance; and
- `event_consumer_journal.worker_id` for claim/fail/ack/dead-letter delivery.

No SQLite columns or historical hashes are rewritten. The public assignment and
health views decode the readable declared name and expose the assurance as a
separate field. Callers cannot supply the reserved namespace themselves, so
they cannot self-label a stronger assurance. The code defines no externally
authenticated assurance value.

Existing raw worker IDs remain verifiable and are labeled
`LEGACY_UNLABELED`. If a still-active legacy member with the same declared name
is encountered, the coordinator first appends a valid legacy `LEFT`, then the
new assured heartbeat and deterministic rebalance. This avoids transient double
ownership during migration without editing old rows.

## Operations visibility

The verified event-health document now includes:

```json
{
  "worker_identity_policy": "PROCESS_DECLARED_UNAUTHENTICATED",
  "active_worker_identities": []
}
```

The external heartbeat sends only the bounded policy enum, never worker names.
Unknown policy text is dropped. This makes the missing strong workload identity
visible to an operator-owned monitor without expanding its data surface.

The real local authority accepted a recovery cycle with the new encoding. The
coordination chain verified, the worker left cleanly, event health remained
`PASS`, and the last records carried the reserved process-declared identity.

## Honest limits

This is an assurance-labeling and tamper-binding improvement, not authentication.
Trusted local code can still choose a declared name, caller time still drives
leases, and SQLite remains a single-host coordinator. Closing strong worker
identity requires externally issued short-lived workload credentials, trusted
mTLS, managed key rotation/revocation, process isolation, and independent
deployment evidence. `GOV.IDENTITY.STRONG_AUTH` therefore remains open.

## Validation

The final unified gate passed all 18 stages: 2353 main tests passed with one
expected skip, 971 independent live/trading/tools tests passed, frontend
typecheck/68 tests/production build passed, and the governance scanner reported
312 files with zero violations. The machine assessment remained
`NOT_TOP_TIER` at 15/22 requirements passed.
