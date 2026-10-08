# L2 Backup and Scheduler Watchdog

Date: 2026-07-14

## Finding

The legacy `QUANTOS_backup` task was enabled but repeatedly returned exit 1.
Its user-level passphrase had 11 characters, below the 12-character fail-closed
minimum, and it had no external destination. The old tool also centered on the
legacy live/testnet artifact set. The existing maturity checker nevertheless
reported PASS merely because `tools/live_backup.py` and `tools/watchdog.py`
existed.

The old watchdog imported live worker configuration and was disabled with the
other live-dependent tasks. It could not independently detect action drift,
disabled jobs, wrong working directories, or the failure of the backup task.

## Backup Contract

`tools/l2_backup.py` backs up ten required L2 authority stores and six
optional authorities. Sources must be regular, non-symlink files under the
explicit `data/cache` or `evidence` allowlist; the `key/` tree and broker
credentials are outside the boundary.

The watchdog now combines Task Scheduler metadata with the append-only restore
history authority. A successful Windows task result is downgraded to BREACH
when the configured history is missing, corrupt, empty, stale, failed,
overdue, or outside cadence. It reads the shared policy from the top-tier
registry with duplicate-key rejection and opens the authority with
`create=False`; inspection cannot repair or create history.

For non-empty SQLite sources the tool uses the SQLite online backup API and
runs `PRAGMA integrity_check` on the snapshot. Other files use a stable
before/read/after stat check. Each plaintext digest and logical source path is
recorded in an encrypted manifest. Artifacts use AES-GCM through the existing
backup crypto primitive. A temporary batch is restored into an isolated tree,
checked against the manifest, and SQLite-verified before an atomic directory
rename publishes the batch.

Scheduled operation requires:

- a passphrase of at least 12 non-whitespace characters;
- an absolute UNC or different-volume `QUANTOS_BACKUP_DEST`;
- no plaintext or same-volume opt-out; and
- no secret embedded in the scheduled action.

The current host does not meet the first two configuration requirements, so
the old task is disabled and no replacement task is registered. This is
`UNAVAILABLE`, not PASS. Local-only output exists solely as an explicit test or
development option and cannot satisfy operational maturity.

## Scheduler Watchdog Contract

`tools/l2_scheduler_watchdog.py` uses only Task Scheduler metadata. It does not
import any monitored worker. Every 15 minutes it checks the exact Python module
action, workspace, enabled/running state, latest result, and cadence for the L2
allowlist. Unexpected enabled `QUANTOS_*` tasks are a breach. Result codes are:

- `0`: all checks PASS;
- `1`: at least one BREACH;
- `2`: PARTIAL, PENDING, or UNAVAILABLE with no breach.

The first real run returned PARTIAL: six PASS, domain-event ops PARTIAL because
three authorities are intentionally absent, backup UNAVAILABLE, and zero
breaches. Alerts are emitted only when the degraded signature changes.

## Honest Boundary

This increment does not create an off-host backup, invent a passphrase, or
claim WORM retention. A same-host scheduled watchdog cannot detect simultaneous
loss of the host, Task Scheduler, and itself. Operator-provisioned secret and
destination configuration, an external heartbeat, retention/object lock,
external manifest sealing, and periodic recovery ownership remain open.

## Verification

Focused backup, watchdog, schedule-governance, maturity-checker, and console
contract coverage passed 58 tests. The final repository gate passed all 18
stages: 2334 backend tests passed with one skipped; 954 live/trading/tools tests
passed; 68 frontend tests, typecheck, and production build passed; and the
governance scanner reported 311 files with zero violations. The machine result
is intentionally `NOT_TOP_TIER`: 15/22 PASS, 5 PARTIAL, 2 manual/external, and
0 failed.
