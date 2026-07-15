# L2 Restore History Authority And External Checkpoint

## Decision

One successful `l2_backup_latest.json` file is M3 evidence only. M4 recovery
readiness requires recurring history, dynamic freshness, retained failures,
and an independently signed external checkpoint. Latest-success state never
grants M4.

## Local Authority

`services/governance/l2_restore_history.py` owns
`data/cache/l2_restore_history.db`.

- Every attempt appends `STARTED` before backup work.
- One terminal `OUTCOME` records PASS, FAIL, or timeout without updating the
  start event.
- WAL, `synchronous=FULL`, foreign keys, strict schema and trigger sets,
  UPDATE/DELETE rejection, a contiguous SHA-256 event chain, global head CAS,
  and exact replay idempotency are mandatory.
- A second active attempt is rejected. A crashed attempt remains open and only
  an explicit timeout transition may append its failure.
- Retained payloads contain stable codes and digests only. They never contain
  a passphrase, destination path, repository path, source filename, endpoint,
  host/user identity, or arbitrary exception text.

The history database is itself a required L2 backup source. Its snapshot
contains the current attempt's committed STARTED event and all earlier terminal
history. Restore performs both SQLite integrity and semantic history-chain
verification.

## Recorded Backup Flow

`tools/l2_backup.py::run_recorded_l2_backup` is the scheduled entry point.

1. Create or verify the history authority without modifying an existing
   invalid database.
2. Append STARTED.
3. Validate configuration, source contract, external destination, snapshots,
   encryption, manifest, isolated restore, SQLite integrity, RPO, and RTO.
4. Append PASS or a structured FAIL.
5. Publish compatibility evidence and status projections.

If the process dies after step 2, the open attempt is visible. If a projection
write fails after PASS, the authority remains the source of truth and the
projection can be rebuilt. A projection cannot authorize M4.

## Dynamic Health Policy

The top-tier checker receives one explicit `as_of_ms` context and checks:

- exact source-contract digest and L2 mode;
- latest terminal is PASS;
- no failure in the clean lookback;
- no overdue open attempt;
- latest attempt and success freshness;
- all attempt and success gaps;
- minimum history span and success count; and
- complete external encrypted restore proof for every success.

The repository policy currently requires 30 days of history, at least 16
successes, no gap over 48 hours, no latest attempt/success older than 48 hours,
a 30-day clean failure window, and a six-hour attempt timeout.

## External Checkpoint

`services/governance/l2_restore_checkpoint.py` is verify-only. It contains no
private-key or signing capability. An Ed25519 envelope binds the exact local
store kind/schema/event count/head sequence/head hash plus:

- external issuer and key ID;
- seal timestamp;
- immutable object version ID; and
- retention end timestamp.

Verification requires a caller-governed active public key, exact current head,
bounded future skew, a seal no older than 48 hours, and at least 90 days of
remaining retention. Duplicate JSON keys, algorithm confusion, revoked or
wrong keys, stale/future seals, head mismatch, and insufficient retention fail
closed.

`config/l2_restore_checkpoint_trust_registry.json` is intentionally empty.
Provisioning a real public key and receiving signed WORM/object-lock receipts
is an operator-owned external action. No signing key belongs in this repository.

## Honest Boundary

The local SQLite history is replaceable and is not WORM. The verifier and
empty trust registry prove that the boundary exists and fails closed; they do
not prove that an external authority, immutable object store, scheduler SLA,
or missed-heartbeat alert is operational. Until those facts exist, the restore
requirement remains M3/PARTIAL and `L2_DRY_RUN_ONLY` remains unchanged.
