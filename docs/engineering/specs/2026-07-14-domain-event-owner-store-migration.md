# Domain-Event Owner-Store Migration (2026-07-14)

## Problem

The first scheduled event-operations cycle found three physically valid SQLite
databases whose domain tables predated transactional outboxes:

- `dry_run.db`: 7 dry-run records;
- `experiment_registry.db`: 2 trials, 2 alpha states, and 8 evaluations; and
- `live_refresh_history.db`: 5 refresh records and 20 normalized source rows.

Opening them with `create=True` in place would have mixed schema mutation with
verification and offered no rollback boundary.

## Migration Protocol

`tools/domain_event_owner_migration.py` implements an explicit plan/apply flow:

1. refuse any WAL/SHM sidecar;
2. read the source with SQLite `immutable=1` and require `integrity_check=ok`;
3. hash the schema and every ordered row of protected domain tables;
4. create a local logical backup with the SQLite backup API;
5. create a same-directory staging database with the backup API;
6. run the owner store's existing migration only against staging;
7. verify target outbox chains and exact protected-table digest equality;
8. recheck source SHA-256 and sidecars; and
9. atomically replace the source, then verify again or restore the backup.

Sources migrate independently. A bad source is `BREACH` but does not block a
separate valid source. Exceptions expose only their type.

## Applied Result

All three real stores migrated successfully. Their protected digests before and
after were identical. Logical backups are retained under
`data/backup/domain_event_owner_migration/` and are local, not off-host/WORM.

Dry-run and experiment migrations created empty outboxes and did not fabricate
historical events. Refresh history used its existing deterministic legacy
backfill contract, producing five `data.market.refresh.recorded` events. The
recovery cycle published and acknowledged all five, projected all five, and
ended with zero pending events and zero DLQ records.

The scheduled task now returns `2/PARTIAL`: the three migrated owner stores are
`PASS`; alpha-maintenance, kill-switch, and deployment authorities are absent.
Those absent stores were not synthesized merely to obtain a green status.

## Additional Correction

`RefreshHistoryStore(create=False)` previously switched an incomplete legacy
database into WAL mode before checking for outbox tables, creating stale WAL/SHM
sidecars on every failed recovery attempt. It now performs an immutable schema
preflight before opening a writable connection and explicitly closes failed
constructors. A regression proves legacy bytes remain unchanged and no sidecar
is created.

## Honest Boundary

This is a single-node, maintenance-window migration with local rollback copies.
It is not online schema evolution, cross-host replication, external archival,
or disaster recovery. Missing authorities remain visible as `UNAVAILABLE`.

## Final Verification

The first full gate after migration found one stale console assertion: the
events endpoint now correctly preferred `DOMAIN_EVENT_PROJECTION`, while the
legacy test allowed only run-registry or synthetic provenance. The assertion
was updated to the existing backend contract and its event type check was made
source-neutral. The final fail-closed gate passed all 18 stages: 2334 backend
tests passed with one skipped, 954 live/trading/tools tests passed, 68 frontend
tests plus typecheck and production build passed, and the governance scanner
reported 311 files with zero violations.
