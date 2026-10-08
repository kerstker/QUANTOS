# Domain Event Anchor and External Heartbeat

Date: 2026-07-14

## Problem

The event authority already had three locally verified append-only chains:
canonical events, consumer delivery attempts, and consumer-group coordination.
No portable commitment covered all three, so a local attacker could truncate or
replace the entire database and recompute a new internally valid history unless
an old head was retained elsewhere.

The local scheduler watchdog could detect a failed worker, but it stopped with
the same host and Task Scheduler it monitored. No external party could detect
that correlated failure.

## Three-Chain Commitment

`EventAuthorityCommitment` records count, maximum sequence, and head hash for
each verified chain, plus a canonical overall SHA-256. Construction occurs only
after all three complete chains pass verification. `from_dict` requires an
exact schema and recomputes the overall hash.

`DurableEventBus.extends_authority_commitment` first revalidates current
history, then checks each anchored prefix at its exact sequence and row count.
Appending new records is valid. Truncation, row replacement, sequence drift,
or wholesale database replacement returns false.

## Create-Once External Anchor

`tools/domain_event_seal.py` opens the source through a read-only SQLite
connection, creates an isolated online snapshot, verifies the snapshot, and
writes a canonical content-addressed commitment with exclusive-create mode.
An existing identical object is idempotent. An existing different object at the
same content address is treated as collision or tampering and is never
overwritten. No DELETE, UPDATE, VACUUM, or compaction touches the authority.

The filesystem sink is explicitly
`PARTIAL_EXTERNAL_WORM_UNVERIFIED`. A different drive is not proof of a separate
failure domain, object lock, external signature, or retention policy.

## External Heartbeat

`tools/external_heartbeat.py` sends a canonical JSON summary to a configured
operator-owned HTTPS endpoint. It rejects URL userinfo, fragments, loopback,
non-HTTPS transport, and redirects. The local result contains only an endpoint
fingerprint. Payload fields are allowlisted verdicts, counters, timestamps,
chain-integrity booleans, and backup destination class; free-form task details,
alerts, paths, and errors never leave the host.

The task is registered every five minutes only when
`QUANTOS_HEARTBEAT_URL` passes configuration validation. A remote monitoring
service must independently alert on absence; local successful POST tests do not
prove that SLO.

## Scheduling and Runtime Result

`QUANTOS_domain_event_seal` is conditional on an external UNC/different-volume
destination. `QUANTOS_external_heartbeat` is conditional on the HTTPS endpoint.
The scheduler watchdog treats an enabled task without valid configuration as a
breach and an absent/disabled unconfigured task as unavailable.

The first reconciliation exposed a real registration bug: `schtasks /query`
for a missing conditional task emitted a native error that PowerShell promoted
under `ErrorActionPreference=Stop`. Registration now uses
`Get-ScheduledTask`/`Disable-ScheduledTask`; a full real rerun succeeded.

Current status is 6 PASS, 1 PARTIAL, 3 UNAVAILABLE, and 0 BREACH. Backup,
heartbeat, and external seal are unconfigured. A real read-only diagnostic over
the current authority committed 5 event rows, 10 delivery rows, and 33
coordination rows; the extension verifier passed and the temporary diagnostic
anchor was removed.

## Open Evidence

- operator-owned HTTPS endpoint and received-heartbeat evidence;
- proof that a missed heartbeat pages an operator within an agreed SLO;
- off-host encrypted backup and recurring restore evidence;
- WORM/object-lock retention receipt and independent external signature;
- key rotation and ownership procedures; and
- safe archive lifecycle before any compaction is considered.

## Verification

The event, consumer, projection, recovery, scheduler, and external-boundary
focused set passed 128 tests. A real authority diagnostic produced and verified
a commitment over 5 event rows, 10 delivery rows, and 33 coordination rows; the
temporary anchor was then removed. With no heartbeat endpoint configured, the
manual sender wrote only an `UNAVAILABLE` status and made no network request.

The first full gate exposed a pre-existing Windows spawn test sensitivity: a
healthy no-op child occasionally consumed the test's two-second non-target
budget during full-suite process startup. The production hard deadline remains
unchanged. The test gives the healthy/global path five seconds while preserving
the hanging source's 200ms deadline; four concurrent stress repetitions passed.
The final fail-closed gate passed all 18 stages: 2338 backend tests with one
skip, 969 live/trading/tools tests, 68 frontend tests plus typecheck/build, and
311 governance-scanned files with zero violations.
