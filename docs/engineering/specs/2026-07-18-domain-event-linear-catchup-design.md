# Domain-Event Linear Catch-Up Design

Date: 2026-07-18

Status: independently reviewed; user approved for implementation on 2026-07-18

Readiness boundary: `L2_DRY_RUN_ONLY`

## 1. Problem

The scheduled `tools.domain_event_ops` path is functionally correct but does
not scale with the retained authority history.  The current production-sized
authority contains roughly 61,000 events, 65,000 consumer-journal records, and
32,000 projection records.  A single scheduled projection run can consume one
CPU core for a long period while advancing only a small part of the backlog.

The cause is structural rather than a slow hash primitive:

- `DurableEventBus.claim_partition()` and `ack_delivery()` force a complete
  event-log, consumer-journal, and consumer-group verification for every event;
- `ConsumerGroupCoordinator.require_ownership_locked()` verifies the complete
  group journal again inside those already-verified operations;
- `DomainEventProjection.apply()` reloads and verifies the complete projection
  history for every event; and
- `_partition_event_row_locked()` materializes every event after the current
  offset with `fetchall()`, even though the official one-partition projection
  needs only the first row.

With `E`, `C`, `G`, and `Q` denoting the four retained history sizes and `R_i`
the event suffix materialized by claim `i`, one run costs approximately
`Theta(K(E+C+G+Q) + K^2 + sum(R_i))`.  When batch and backlog scale together,
this is quadratic.  Increasing the scheduler frequency or batch size cannot
repair that complexity.

## 2. Goals

1. Make one warm single-node catch-up run `O(N + K)`: one complete verification
   when each authority is opened, followed by constant-work trusted appends and
   acknowledgements.
2. Preserve append-only hashes, at-least-once delivery, projection idempotency,
   single-partition total order, generation fencing, and fail-closed behavior.
3. Detect every external commit, unexpected same-connection change, schema
   change, tail mismatch, rollback, or uncertain commit state and fall back to
   complete verification before another mutation.
4. Keep all trusted frontiers process-local and ephemeral.  A restart must
   discard them and verify every retained row again.
5. Bound partition scanning memory and make the official one-partition lookup
   a single indexed `LIMIT 1` query.
6. Add structural complexity tests so a future regression cannot hide behind a
   small fixture or a generous wall-clock threshold.

## 3. Non-goals

- No deletion, compaction, checkpoint truncation, retention mutation, or hash
  history rewrite.
- No persistent cache, sidecar head file, or database row that is treated as a
  stronger trust root than the retained chains.
- No change to event schemas, partition identity, consumer offsets, delivery
  ordering, DLQ behavior, or the publish-before-ack contract.
- No cross-host broker, consensus, replicated log, HA, or external sealing.
- No claim to resist a privileged database administrator who can rewrite every
  row, recompute every chain, and forge the deployment's external evidence.
  The current L2 SQLite authority does not provide that boundary either.
- No live trading, broker, order, credential, or network capability.

## 4. Chosen architecture

### 4.1 Ephemeral verified frontiers

Each open authority keeps an in-memory `VerifiedFrontier` for every chain it
owns.  A frontier contains only facts obtained by a successful full verifier or
by a committed append through the owning object's controlled helper:

- chain name;
- last sequence number and row count;
- last record hash;
- an `ACTIVE` or `INVALID` state.

Connection-global freshness is held separately in one `ConnectionEpoch`, not
copied into each chain frontier.  The epoch contains the exact connection
identity, a monotonic owner generation, `total_changes`, `PRAGMA data_version`,
`PRAGMA schema_version`, and an `ACTIVE` or `INVALID` state.  A consistent
runtime snapshot is one epoch plus all frontiers owned by that connection.

`DurableEventBus` owns separate event, consumer, and consumer-group frontiers.
`DomainEventProjection` owns the projection frontier.  The values are never
serialized and never accepted from a caller.

Object construction continues to run the existing complete row and schema
verification.  Only that successful result creates the initial frontier.

### 4.2 Freshness sentinel before every operation

Under the existing Python lock, and inside `BEGIN IMMEDIATE` for a mutating
operation, the owner reads a constant-size sentinel:

- current `data_version`, `schema_version`, and `total_changes`;
- current `(last_sequence, record_hash)` for each owned chain through an
  indexed descending `LIMIT 1` lookup; and
- the connection/frontier generation.

The warm sentinel never executes `COUNT(*)` or scans `sqlite_schema`.  Verified
row count is retained in memory and advances only after a controlled committed
append.  Exact schema and trigger identities are checked at construction and
whenever `schema_version` or another freshness value changes and the complete
verifier runs.

The warm fast path is allowed only when all values equal the last atomically
promoted connection epoch and chain frontiers.  Any of the following
invalidates the epoch and every affected frontier and runs
the existing complete verifier before the operation continues:

- `data_version` changed, which means another SQLite connection committed;
- `schema_version` changed;
- `total_changes` differs from the owner's expected committed value;
- a sequence or tail hash differs;
- a previous commit or rollback left an uncertain state; or
- the required schema/trigger sentinel differs.

An external commit is never accepted through suffix-only verification.  It
always causes a complete verification of the event, consumer, and group
authorities before the next event-bus mutation.  This retains detection of an
older-row rewrite whose final tail was left unchanged.

Invalidation does not erase the last trusted frontier.  After the complete
verification, every newly verified chain must prove monotonic extension of its
old anchor: its row count and maximum sequence cannot decrease, and the row at
the old `(last_sequence, row_count)` position must still carry the exact old
record hash.  Equality is allowed; a longer chain is allowed only when it
contains that exact prefix.  A missing/changed anchor, shorter chain, or
different self-consistent replacement chain permanently fails the open object
closed instead of rebasing trust to the new history.

After a process restart there is no trusted in-memory anchor.  Full replay can
detect malformed hashes, gaps, schema drift, and referential corruption, but it
cannot distinguish a valid older prefix from the original complete history.
Detecting a self-consistent rollback across restarts requires an externally
sealed head/checkpoint and remains an explicit post-L2 gap.  This design must
not claim otherwise.

### 4.3 Controlled append and commit promotion

An append helper first checks the active epoch/frontier, derives the next record
from its verified head, executes one insert, and builds a transaction-local
tentative next frontier.  A transaction may stage several rows and several
affected chains while retaining the pre-transaction epoch and all unaffected
frontiers.  Nothing staged is trusted until commit succeeds and a post-commit
constant-size sentinel exactly matches the expected sequence, tail hash,
schema version, connection change count, and staged logical row-count
increment.  That row-count increment is proven by the controlled inserted row,
not by a warm-path `COUNT(*)`.

Promotion is atomic in memory: one successful commit replaces the connection
epoch and every affected frontier together.  Before promotion, the owner also
proves that every unaffected chain still has its pre-transaction tail.  It then
associates those unchanged frontiers with the new epoch.  This prevents a
consumer append from either making event/group frontiers spuriously stale or
silently blessing an unverified change in an unaffected chain.

On rollback, commit error, post-commit mismatch, or escaping `BaseException`,
the tentative frontier is discarded.  If the connection remains usable, the
next operation performs a complete verification; if commit outcome is
uncertain, the owner closes/invalidates the connection and fails closed.

Only changes made through these controlled helpers may advance a frontier
without a complete scan.  Direct SQL through the same connection changes
`total_changes` or `schema_version` and therefore invalidates the fast path.

### 4.4 Consumer-group state remains generation-fenced

Full group verification continues to rebuild `_states` from the complete
coordination journal.  The row-state transition logic is extracted into one
pure validator used by both full replay and controlled append.

For a same-connection `GROUP_CREATED`, `HEARTBEAT`, `LEFT`, or `REBALANCED`
append, that validator checks the row against the current verified immutable
state.  One group transaction starts with a transaction-local staged copy of
the committed state/head; each row is reduced against the previous staged
result, so a later HEARTBEAT/REBALANCED row sees an earlier GROUP_CREATED or
LEFT row from the same transaction.  The complete staged state/head is promoted
once after commit.  Rollback discards all staged rows and never mutates the
committed fencing snapshot.  Internal calls stop recursively replaying the
complete journal.

`require_ownership_locked()` still runs inside the event-bus write transaction
after the freshness sentinel.  It still checks current generation,
`partition_count`, assignment ownership, active membership, lease expiry,
monotonic time, and the deterministic desired rebalance.  A heartbeat from
another connection changes `data_version`, forces full replay, and fences the
stale assignment before an event can be claimed or acknowledged.

### 4.5 Partition selection

For `partition_count == 1`, `_partition_event_row_locked()` uses the equivalent
of:

```sql
SELECT ... FROM event_log
WHERE seq > ?
ORDER BY seq
LIMIT 1
```

For multiple partitions, the hash-based partition function remains unchanged.
Rows are consumed from a cursor with bounded `fetchmany()` chunks until the
next matching event is found.  The method never calls `fetchall()` for the
unbounded suffix.  Because a partition's durable offset advances to every
matched event, skipped gaps are scanned once per partition rather than retained
in memory.

No SQL hash function, changed partition key, or repartitioning migration is
introduced.

### 4.6 Projection fast path

`DomainEventProjection` performs a complete verification when opened.  Each
`apply()` then:

1. acquires `BEGIN IMMEDIATE`;
2. checks the projection freshness sentinel;
3. falls back to complete verification on any change;
4. checks the unique event ID/sequence replay condition;
5. inserts against the verified head or returns the exact idempotent no-op;
6. commits; and
7. promotes the tentative frontier only after the post-commit sentinel matches.

The explicit `chain_valid()`/operator verification path always performs a full
verification.  The warm runtime frontier is an optimization, not a substitute
for an explicit audit.

## 5. Crash and concurrency semantics

- Crash before projection commit: no projection row exists and the delivery is
  retried after lease expiry.
- Crash after projection commit but before ACK: reopening fully verifies the
  projection; exact replay is a no-op; the consumer ACK then advances once.
- Crash after CLAIM: the retained lease/retry rules are unchanged.
- Crash after ACK commit: the consumer offset is authoritative even if the
  caller did not receive the response.
- Restart: all frontiers are absent, so the complete event, consumer, group,
  and projection chains are verified before processing.
- Concurrent legitimate writer: `BEGIN IMMEDIATE` serializes mutations; the
  next owner observes `data_version` drift and fully verifies before continuing.
- External broken append or older-row mutation: the next operation observes a
  freshness change and fails during complete verification before mutation.
- Stale consumer-group generation: the refreshed group state fences it before
  claim/ACK.

The projection-before-ACK ordering and at-least-once behavior remain exactly as
they are today.

## 6. Security boundary

The optimization trusts only three things already inside the current L2 threat
model: a complete verification performed by this process, SQLite transaction
serialization, and changes committed by the same owner through its private
append helpers.  It does not trust a saved cache or a caller-provided head.

`data_version` is not treated as proof of integrity.  It is only a freshness
alarm: a changed value triggers the real complete verifier.  Likewise,
sequence/count/hash tail queries cannot prove an unchanged prefix by
themselves; they are used only when no external commit or unexpected local
change has occurred since the prefix was completely verified.

The full verifier and tamper-evident chains remain the authority.  External
sealing, strong worker identity, replicated consensus, and defense against a
privileged full-chain rewrite remain explicit post-L2 gaps.

## 7. Test strategy

### 7.1 Complexity RED tests

Tests instrument verifier entry points and SQLite trace callbacks instead of
depending only on elapsed time.

1. After object construction, project and ACK 256 events with no external
   writer.  The warm path must execute zero complete event, consumer, group, or
   projection history scans per event.
2. The number of full-history verifications must be bounded by
   `opens + detected_external_commits + explicit_audits`, never by the batch
   size.
3. The official one-partition path must issue a `LIMIT 1` lookup and materialize
   exactly one candidate row per claimed event.
4. A multi-partition fixture must use bounded chunks and prove that no call
   materializes the complete remaining suffix.
5. Repeated `require_ownership_locked()` calls against an unchanged group must
   not replay the group journal.

These tests are red against the current implementation even on tiny fixtures.

### 7.2 Integrity and fencing tests

- A valid append through a second event-bus connection causes exactly one full
  refresh before the first connection proceeds.
- An external truncation or a rewritten but internally self-consistent chain
  is rejected because it does not monotonically extend the retained in-memory
  frontier anchor.
- A second connection drops an append-only trigger and tampers with an early
  event, consumer, group, or projection row; the next operation fails before
  any new row is written.
- Unexpected SQL through the owner's connection invalidates the frontier.
- Schema/trigger drift invalidates the frontier even when the tail hash is
  unchanged.
- A foreign heartbeat/rebalance fences an old assignment before claim and ACK.
- A failed insert, rollback, failed commit, or uncertain commit cannot leave an
  active tentative frontier.
- Close and reopen forces a complete verification and detects retained
  corruption.
- Projection-commit-before-ACK crash replay remains idempotent and produces one
  projection row and one terminal consumer offset.
- Explicit `chain_valid()` still scans and detects corruption even when a warm
  frontier exists.

### 7.3 Operational benchmark

The structural tests are the CI gate.  A deterministic work-counter fixture
with `E=50,000`, `C=50,000`, `G=20,000`, `Q=25,000`, and `K=1,000`
additionally requires:

- complete retained-row hashes bounded by `E`, `C`, `G`, and `Q` respectively;
- exactly `2K` new consumer-tail validations for CLAIM and ACK;
- at most `K` new projection-tail/idempotency validations;
- group transition work bounded by the rows actually appended in the current
  heartbeat/leave transaction, with zero retained-group replay on the warm
  claim/ACK loop;
- at most `K+1` candidate rows materialized for the official single partition;
- a multi-partition fetch buffer no larger than 256 rows; and
- doubling `K` to increase warm-path work by no more than two times plus a
  fixed constant, while independently doubling retained `E`, `C`, `G`, or `Q`
  does not increase warm work.

A separate diagnostic benchmark uses a
temporary authority with at least 50,000 events, 50,000 consumer records, and
20,000 group records and 25,000 projection records, catches up 1,000 events,
and reports:

- events per second;
- number of complete verifier invocations;
- rows materialized by partition lookup;
- peak resident memory; and
- final event/consumer/group/projection chain validity.

The first accepted implementation must record the pre/post result on the
current Windows host and demonstrate at least a 20x throughput improvement,
while the structural verifier-count requirements remain the non-flaky pass/fail
contract.  The real operator database is read-only input to diagnosis and is
never used as a mutable benchmark fixture.

## 8. Files and ownership

- `services/monitoring/event_bus.py`: event/consumer freshness sentinels,
  controlled frontier promotion, and bounded partition lookup.
- `services/monitoring/consumer_group.py`: shared row-state validator,
  incremental same-owner state/head update, unchanged fencing rules.
- `services/monitoring/event_projection.py`: projection frontier and warm
  append/idempotent replay path.
- `services/monitoring/domain_event_runtime.py`: unchanged public projection
  semantics; optional diagnostic counters only.
- `tests/monitoring/test_event_consumer.py`: event/consumer freshness and
  tamper matrix.
- `tests/monitoring/test_event_consumer_group.py`: incremental replay and stale
  generation fencing.
- `tests/monitoring/test_event_projection.py`: projection frontier, crash
  replay, and tamper detection.
- `tests/tools/test_domain_event_ops.py`: bounded catch-up integration and
  process cleanup.
- a dedicated performance test/benchmark module may contain large deterministic
  fixtures, but no production verifier may import it.

## 9. Acceptance criteria

The work is accepted only when:

1. warm single-process catch-up is structurally `O(N + K)`, not `O(N * K)`;
2. any external commit, unexpected same-connection change, schema drift, tail
   mismatch, rollback, or uncertain commit causes complete re-verification or
   fail-closed connection invalidation;
3. an external refresh must monotonically extend the last in-memory verified
   anchor and may never rebase to a shorter or different self-consistent chain;
4. restart always verifies the complete retained histories, while the absence
   of a cross-restart external anti-rollback seal remains explicitly reported;
5. no trusted frontier is persisted or supplied by a caller;
6. consumer-group generation fencing and assignment semantics are unchanged;
7. projection-before-ACK and exact replay idempotency are unchanged;
8. one-partition lookup is `LIMIT 1` and multi-partition memory is bounded;
9. explicit audit APIs still perform complete verification;
10. focused monitoring/tool tests, governance scanner, and the canonical
   `rtk python scripts/validate.py` gate are rerun; and
11. every Python/test/helper process started for the work is confirmed absent
    at the task boundary.
