# Data/Risk Domain Events and Event-Bus Health (2026-07-14)

## Scope

This increment extends the existing single-node canonical event authority in
three load-bearing places:

1. every durable market-data refresh record creates a canonical event in the
   same SQLite transaction;
2. an honestly evaluated dry-run research-risk decision is retained beside the
   exact immutable dry-run snapshot in the existing transactional outbox; and
3. consumer lag, poison events, and append-only storage pressure become a
   deterministic health snapshot with explicit alert thresholds.

It does not add a network broker, live order path, credentials, deployment
execution, automatic log deletion, or cross-host high availability.

## Data Refresh Event

`data.market.refresh.recorded` contains only bounded operational facts: run ID,
overall outcome, source/failure counts, duration, and the SHA-256 hashes of the
canonical refresh status and history entry. Free-form source errors are not
copied to the shared event log.

`RefreshHistoryStore.append` commits the history row, normalized source rows,
and event outbox row under one `BEGIN IMMEDIATE`. The outbox and dispatch
acknowledgements are append-only SHA-256 chains. Existing refresh databases are
migrated only when the outbox table is absent; their already-verified history is
backfilled deterministically. A partial or tampered outbox is never repaired.

## Research-Risk Event

`research.risk.dry_run.evaluated` is emitted only when a `DryRunRun` already
contains the real `REAL_RESEARCH_RISK_SUBSET` result. The event records the
decision, run/skipped engine names, gate counts, bounded reason strings, series,
configuration provenance, and the exact dry-run reproducibility hash.

The event remains diagnostic. `RISK_APPROVED` means only that the honestly fed
subset passed; it does not promote an alpha, grant deployment permission, or
authorize an order. The event and dry-run snapshot share the existing
`DryRunStore.save_run_with_outbox` transaction.

## Lag and Retention Health

The event authority exposes a verified operational snapshot for a registered
consumer group. Lag is the count of pending events assigned to each partition,
not the misleading difference between global sequence numbers. Event age uses
the versioned schema registry so mixed historical millisecond/microsecond events
normalize without magnitude guessing.

The health evaluator reports:

- event, delivery, and coordination-chain integrity;
- active workers, per-partition offsets/high-watermarks, pending count and age;
- dead letters across all group partitions;
- event/delivery/coordination row counts and allocated SQLite bytes; and
- deterministic warning/breach codes against an explicit policy.

Retention mode is `APPEND_ONLY_ALERT_ONLY`. Thresholds create alerts before or
at capacity limits, but no code compacts, truncates, rewrites, or deletes event
history. A future archival design must first preserve a verifiable external
seal and replay anchor.

## Honest Boundary

This remains one SQLite authority on one host. Worker identity and clock values
are caller supplied. There is no consensus, broker replication, external chain
seal, automatic archival, or independent alert delivery SLA. These limitations
remain open in the top-tier assessment.

