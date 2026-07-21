# QUANTOS L2 Research Operations

Current readiness is `L2_DRY_RUN_ONLY`. This is the operational runbook for the
current system. It authorizes research, paper evidence, maintenance, read-only
reporting, and local event operations only. It does not authorize venue access,
order submission, emergency trading action, or a live operations API.

## Scheduled Jobs

Run `powershell -ExecutionPolicy Bypass -File scripts/register_schedules.ps1`
from the repository root to reconcile the L2 schedule set.

| Task | Cadence | Module |
|---|---|---|
| `QUANTOS_factory_loop` | Sunday 20:00 | `tools.crypto_factory_loop` |
| `QUANTOS_ic_panel_refresh` | Sunday 20:30 | `tools.ic_panel_research` |
| `QUANTOS_forward_track` | Daily 21:00 poll (7-day internal due gate) | `tools.forward_track_advance` |
| `QUANTOS_alpha_maintenance` | Monday 09:05 | `tools.alpha_maintenance_sweep` |
| `QUANTOS_domain_event_ops` | Hourly | `tools.domain_event_ops` |
| `QUANTOS_scheduler_watchdog` | Every 15 minutes | `tools.l2_scheduler_watchdog` |
| `QUANTOS_gonogo_report` | Daily 23:00 | `tools.gonogo_report` |
| `QUANTOS_backup` | Daily 03:00, only when configuration passes | `tools.l2_backup` |
| `QUANTOS_external_heartbeat` | Every 5 minutes, only when configured | `tools.external_heartbeat` |
| `QUANTOS_domain_event_seal` | Daily 02:30, only when configured | `tools.domain_event_seal` |

## Three-Alpha Paper Cycle

The operator-level paper flow is a single idempotent, L2-only command. It runs
the authoritative dry-run seam for `carry_oi_majors`, `crowd_contra`, and
`top_contra`, rebuilds the shared paper-cycle/readiness evidence, and never
reads private credentials or submits an order:

```powershell
rtk python -m tools.three_alpha_paper_flow `
  --output data/cache/three_alpha_paper_flow.json `
  --cycle-output evidence/alpha_paper_cycle.json
```

To advance the public forward-paper clock in the same run, add
`--advance-forward`. A failed requested forward refresh makes the whole flow
fail closed; stale evidence is never reported as a successful refresh. The
official evidence gate is eight realized periods per Alpha. Current L2 output
is diagnostic/paper evidence only and cannot authorize Binance private access,
live orders, or a live broker adapter.

## Selection Trial-Ledger History

The M4 trial-ledger history must come from two distinct, completed,
selection-bearing research sessions in the production authority at
`data/cache/experiment_registry.db`. A caller-supplied label, a test registry,
or two snapshots of an unchanged pre-v2 ledger is not a session.

Preflight the production authority without creating or migrating it:

```powershell
rtk python -m tools.search_trial_ledger_history --inspect-ledger
```

The 2026-07-15 read-only preflight found `user_version=0`, 2 retained trials,
8 immutable evaluations, and no search-attempt or exact-link tables. It is
honestly `capture_ready: false`; none of those legacy rows proves a completed
selection session. The first supported real session will invoke the store's
tested atomic v2 migration before it writes search provenance.

Proceed only when the output reports schema v2, valid event/candidate/evaluation
link chains, and `capture_ready: true`. Each capturable attempt must be
`COMPLETED`, have at least one registered candidate, and have at least one
immutable evaluation linked to its exact `search_id` and registered candidate
`config_hash` in the same registry transaction. `search_id`, dataset version,
and timestamps without that candidate binding are not an acceptable substitute.

The smallest supported offline pair currently available is:

```powershell
rtk python -m tools.crypto_alpha_research
rtk python -m tools.search_trial_ledger_history --inspect-ledger
rtk python -m tools.search_trial_ledger_history --session-id <exact-search-id>

rtk python -m tools.fx_research
rtk python -m tools.search_trial_ledger_history --inspect-ledger
rtk python -m tools.search_trial_ledger_history --session-id <exact-search-id>

rtk python -m tools.search_trial_ledger_history --check
```

`crypto_alpha_research` consumes the retained 23-instrument, 2,000-period
crypto history and evaluates 15 curated candidates. `fx_research` consumes the
retained roughly 7,000-period ECB/Frankfurter panel and evaluates 8 candidates.
Budget up to 15 minutes of local CPU time for each on the current workstation;
this is an operator allowance, not a benchmark or SLA. Panel construction and
the validation battery dominate, while the registry writes are small and there
is no external data fee. The tools do not fetch data, promote an alpha, submit
an order, or change L2 readiness. Run them one at a time because the production
registry is a single-node SQLite authority.

Capture the first session before starting the second. The collector requires
the later session to begin after the preceding retained snapshot and requires
its own exact search-evaluation link. A real rerun may reference the same
immutable evaluation artifact, but it must create a new hash-chained link for
the new `search_id`, bound to a candidate actually registered by that session;
relabeling an old attempt or attaching a run to an unrelated candidate cannot
manufacture cross-session history. Do not use `tools.crypto_research_offline`:
it intentionally rebuilds a separate demo
registry. Do not use a test fixture, copy a database into the production path,
enable a network fetch fallback, invent a session ID, or edit
`evidence/alpha_trial_ledger_history.json` by hand. A pre-v2 registry is a migration
precondition, not M4 evidence; operate a real supported v2 research session to
perform the normal atomic schema migration.

Actions use the resolved Python executable, repository working directory,
`IgnoreNew`, and `StartWhenAvailable`. The registration script also disables
the five legacy live-capable or live-dependent tasks without deleting them. It
disables the old backup action before conditionally registering the L2 action.

Inspect the event task with:

```powershell
schtasks /Query /TN QUANTOS_domain_event_ops /FO LIST /V
Get-ScheduledTask -TaskName QUANTOS_domain_event_ops
```

## Event Operations

Manual safe execution:

```powershell
python -m tools.domain_event_ops
```

The cycle first drains the seven transactional outboxes and then evaluates the
shared authority. Results are written to:

- `data/cache/domain_event_outbox_drain.json`;
- `data/cache/domain_event_health.json`;
- `data/cache/domain_event_ops_status.json`; and
- `data/cache/alerts.jsonl` for non-pass summaries.

Exit `1` or `2` is an operational finding, not a scheduler defect. Inspect the
status snapshot before rerunning an owner producer. Do not delete, rebuild, or
auto-migrate an authority merely to obtain a green result.

`domain_event_health.json.worker_identity_policy` must currently read
`PROCESS_DECLARED_UNAUTHENTICATED`. This is the honest L2 assurance ceiling,
not an authentication success. `active_worker_identities` may show declared
local worker names while active; the external heartbeat sends only the policy
enum and never those names. Any stronger value is unsupported and must be
treated as a contract violation until external workload OIDC/mTLS is deployed.

Legacy owner-store migration is a separate maintenance action:

```powershell
python -m tools.domain_event_owner_migration          # read-only plan
python -m tools.domain_event_owner_migration --apply  # explicit staged apply
```

Disable `QUANTOS_domain_event_ops` before apply and require no WAL/SHM sidecars.
The tool backs up, migrates, and verifies each source independently. Never use
`create=True` directly against a production owner database as a migration step.

## Current Findings

The first scheduled event cycle returned `BREACH`. The three legacy stores were
subsequently migrated through the staged protocol. Five refresh-history events
were backfilled, dispatched, and projected with zero pending/DLQ. The current
cycle is `PARTIAL`: dry-run, experiment, and data-refresh are `PASS`;
maintenance, kill-switch, and deployment owner stores are absent. Task Scheduler
records this honestly as exit `2`.

The old `QUANTOS_backup` failed because the user-level passphrase is 11
characters while the fail-closed policy requires at least 12, and no
`QUANTOS_BACKUP_DEST` is configured. It is now disabled. The replacement is
registered only when both settings are available from the Windows user or
machine environment and the destination is UNC or on another volume. Never put
the passphrase in task arguments, source files, logs, or this repository.

After an operator provisions the secret from an approved secret source and sets
an absolute external destination, rerun schedule registration. A manual
preflight uses the same module:

```powershell
python -m tools.l2_backup
python scripts/assess_top_tier.py
```

The backup covers the ten required L2 authority stores plus six optional enabled
authorities, including the restore-history, approval, and identity-authorization authorities. Active SQLite files are captured through the SQLite online backup
API. Every artifact and the manifest are AES-GCM encrypted; the batch is
published atomically only after an isolated restore, digest verification, and
SQLite `integrity_check`. Success evidence is
`evidence/l2_backup_latest.json`. The passphrase environment mechanism protects
backup media but is not a managed KMS/HSM; managed secret delivery and key
rotation remain an infrastructure requirement.

The scheduled command uses the append-only authority at
`data/cache/l2_restore_history.db`. It commits STARTED before preflight and a
structured PASS/FAIL outcome before refreshing the compatibility evidence and
`data/cache/l2_backup_status.json`. Do not delete or rebuild this database to
clear a failure or overdue attempt. The status JSON is a projection and never
authorizes M4.

M4 also requires an external Ed25519 checkpoint for the exact current history
head. The local trust registry at
`config/l2_restore_checkpoint_trust_registry.json` is empty by default. An
operator must provision only the approved public verification key; private
signing material must remain outside the repository and process. Retain the
signed checkpoint in `evidence/l2_restore_history_checkpoint.json` only after
the external object version and retention policy are real. The configured
policy requires a 30-day clean history, at least 16 successes, gaps no larger
than 48 hours, a fresh checkpoint, and at least 90 days of remaining retention.

The scheduler watchdog reads the same history policy and authority with
`create=False`. A pinned task action and exit code `0` are not enough: missing,
corrupt, empty, failed, stale, overdue, or cadence-breached history changes the
backup check to `BREACH`. The first registered trigger may remain PARTIAL, but
an old successful scheduler result cannot hide an unhealthy restore history.

Migration backups under `data/backup/domain_event_owner_migration/` are local
rollback artifacts only. They do not satisfy that off-host requirement.

## External Heartbeat

Set `QUANTOS_HEARTBEAT_URL` through the approved operator configuration path to
an operator-owned HTTPS endpoint. The registration script rejects HTTP,
userinfo credentials, fragments, and loopback targets. The sender refuses
redirects, never records the endpoint, and transmits only bounded scheduler,
event-authority, and backup health fields. It does not send task details, alert
text, file paths, or endpoint credentials.

```powershell
python -m tools.external_heartbeat
```

Code and local delivery tests do not prove a monitoring SLA. Retain evidence
from the external system that a heartbeat arrived and that a deliberately
missed heartbeat generated an operator-visible alert within the agreed SLO.

## Event Authority Anchor

Set `QUANTOS_EVENT_SEAL_DEST` to a UNC or different-volume destination, then
reconcile schedules. Manual export and verification are:

```powershell
python -m tools.domain_event_seal
python -m tools.domain_event_seal --verify-anchor <external-anchor-path>
```

The exporter snapshots the authority read-only, verifies all three chains, and
writes a deterministic content-addressed anchor using create-exclusive mode.
The verifier proves the current event, delivery, and coordination chains still
extend the anchored prefixes. Normal appends pass; truncation or replacement
fails. A filesystem create-once anchor is still labeled
`PARTIAL_EXTERNAL_WORM_UNVERIFIED`; only an operator-owned object-lock/WORM
receipt and independent signature can close that boundary. The tool never
deletes, compacts, or vacuums authority history.

## Boundaries

- Scheduled tasks currently use Windows interactive-logon mode.
- Local JSONL alert recording is authoritative; Telegram is optional and has no
  proven delivery SLA.
- The metadata watchdog is independent of monitored worker imports, but it is
  still on the same host and scheduler. The external heartbeat sender exists,
  but no endpoint or missed-heartbeat receipt is currently configured.
- No task in the L2-managed schedule set may contain a live module,
  order/action flag, or broker path.
