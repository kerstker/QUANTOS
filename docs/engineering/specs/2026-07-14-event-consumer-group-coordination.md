# Event Consumer Group Coordination (2026-07-14)

## Scope

This increment adds load-bearing **single-node** consumer-group coordination to
the existing SQLite event authority. It does not claim cross-host consensus,
broker replication, or Kafka-equivalent availability.

## Invariants

- Group `partition_count` and `session_timeout_ms` are immutable after creation.
- Membership and rebalance records form an append-only SHA-256 chain in the same
  SQLite authority as the event and delivery journals.
- A heartbeat uses caller-supplied epoch milliseconds. Group time cannot move
  backwards on mutation or partition ownership checks.
- Active worker IDs are sorted and partitions are assigned round-robin. A changed
  assignment increments one eager-rebalance generation.
- Claim, ACK, and failure paths verify current generation, membership liveness,
  partition ownership, delivery lease, and claim time. Stale work is fenced.
- Partition keys use `correlation_id` when present, otherwise `event_id`, hashed
  with SHA-256. Ordering is guaranteed only within that stable key/partition.
- Internal partition offsets use a reserved prefix plus a hash of the group ID;
  user group names cannot collide with internal consumer IDs.
- Retry, DLQ, error redaction, and offsets reuse the existing consumer journal.

## Production Path

`project_pending_domain_events` now heartbeats before claiming and leaves on a
clean exit. Its topology remains exactly one partition so historical global order
and the existing `domain-event-projection-v1` offset are preserved. A process
crash skips the leave; another worker can take ownership only after session expiry
and rebalance. Projection-before-ACK remains idempotent and at-least-once.

## Failure Model

The coordinator fails closed on journal tampering, configuration drift, clock
rollback, expired membership, stale generation, non-owner claims, ID collisions,
and ACK before claim. Concurrent SQLite writers serialize through
`BEGIN IMMEDIATE`; legacy databases add the coordination journal on open.

## Honest Boundary

Worker IDs are trusted in-process identities and time is injected by the caller.
The append-only log has no external seal and no retention/compaction policy yet.
Cross-host membership, consensus, broker replication, HA, strong workload
identity, lag monitoring, and retention alerts remain separate work.

## Verification

- `tests/monitoring/test_event_consumer_group.py`
- `tests/monitoring/test_event_consumer.py`
- `tests/tools/test_domain_event_bus.py`
- `tests/governance/test_event_bus_wiring.py`
- `tests/governance/test_supply_chain_evidence.py`

The focused P2-5 regression set passes 234 tests; service graph, schema Catalog,
supply-chain drift checks, and Ruff are green. The machine verdict remains
`NOT_TOP_TIER` at 16/22 PASS. The final unified gate passes 2287 main tests with
1 skip, 916 live/trading/tools tests, 68 frontend tests plus typecheck/build, and
306 governance-scanned files with zero violations.
