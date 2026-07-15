# L2 Schedule Boundary and Domain-Event Operations (2026-07-14)

## Trigger

The host still had enabled Windows tasks for two venue submission drivers, an
active emergency-reduction sentinel, and the local live operations API. Their
commands included live-capable flags and the `live/` path does not consume the
canonical `SafetyConfig`. This contradicted the repository's authoritative
`L2_DRY_RUN_ONLY` readiness even though the venues were testnet/demo.

## Safety Response

The following tasks were disabled, not deleted:

- `QUANTOS_live_track_binance`;
- `QUANTOS_live_track_bybit_demo`;
- `QUANTOS_risk_sentinel`;
- `QUANTOS_ops_api`; and
- `QUANTOS_watchdog`, because it expected the disabled live jobs.

The registration script is now L2-only. A governance test rejects any live
module, submission/action flag, hard-coded workspace, or shell-composed task
action. Windows ScheduledTasks registers Python as the executable with separate
arguments and working directory. `IgnoreNew` prevents overlapping invocations.

## Unified Event Operations

`tools/domain_event_ops.py` runs the recovery drain before event-bus health,
continues after either component fails, reduces exceptions to their type, emits
bounded local alerts, writes one status snapshot, and returns:

- `0` for `PASS`;
- `1` for `BREACH`; and
- `2` for `WARN`, `PARTIAL`, or `UNAVAILABLE`.

It has no order submission, broker connection, credential access, domain-state
mutation, or retention deletion capability.

The first scheduled run completed on 2026-07-14 with exit `1`. Scheduling and
the shared projection were healthy; three legacy owner databases failed their
strict outbox/integrity checks, while three owner databases were absent. The
failure remains visible rather than being migrated or reported as green.

## Authority Non-Mutation Correction

The first manual probe exposed that the recovery drain opened the shared event
authority with `create=True`. An existing 4 KiB SQLite file without `event_log`
was initialized as an empty authority. The file is retained because reconstructing
unknown prior bytes would be an unjustified destructive action.

The shared opener now performs a read-only SQLite schema preflight whenever
`create=False`. Existing malformed authorities remain byte-identical and return
`EventBusIntegrityError`; creation is allowed only when the authority path is
absent and at least one source database exists. Drain and health have independent
non-mutation regression tests.

## Honest Boundary

This closes the local L2 scheduling entrypoint and provides a real hourly
single-node operations cycle. It does not provide an independently hosted
watchdog, off-host alert-delivery SLA, external archive/seal, strong workload
identity, cross-host broker, replication, or HA. Windows tasks use interactive
logon mode. The separate legacy backup task is still failing and is not claimed
as a working disaster-recovery control.

## Verification

- Event/monitoring focused regression: `79 passed`.
- Governance/supply-chain focused regression: `76 passed`.
- Unified fail-closed gate: `2327 passed, 1 skipped`; live/trading/tools
  `938 passed`; frontend `68 passed` plus typecheck and production build.
- Governance scanner: `311 files / 0 violations`.
- Event catalog: `11 types / 16 definitions`; service graph:
  `16 domains / 36 edges / 0 cycles`; supply chain: `320 components`.
- Machine assessment remains `NOT_TOP_TIER`: `16/22 PASS`, `4 PARTIAL`,
  `2 MANUAL_EVIDENCE_REQUIRED`, `0 FAILED`.
