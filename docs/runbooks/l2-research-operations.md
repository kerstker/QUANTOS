# QUANTOS L2 Research Operations

Current readiness is `L2_DRY_RUN_ONLY`. This is the operational runbook for the
current system. It authorizes research, paper evidence, maintenance, read-only
reporting, and local event operations only. It does not authorize venue access,
order submission, emergency trading action, or a live operations API.

## Scheduled Jobs

Registration requires two absolute, different, non-overlapping roots. The code
root is a clean, validated release checkout; the state root is the mutable
operational tree that owns `data/cache`, `evidence`, and `.runtime`. A
single-root installation is rejected rather than treated as a production-paper
release:

```powershell
powershell -ExecutionPolicy Bypass `
  -File H:\path\to\validated-release\scripts\register_schedules.ps1 `
  -StateRoot H:\path\to\operational-state
```

The script location is the code root; it is never caller-selected. Registration
fails unless Git `HEAD` is a 40-character commit and the release has no tracked
or untracked changes. Each task action carries that exact commit through
`--release-commit`. Before every import, `tools/l2_schedule_entrypoint.py`
rechecks the release `HEAD`, verifies full cleanliness, rejects overlapping
release/state roots, and binds `QUANTOS_RELEASE_COMMIT`,
`QUANTOS_RELEASE_ROOT`, and `QUANTOS_STATE_ROOT` into the worker. The imported
module must also resolve under the release `tools` directory.

The Task Scheduler working directory remains the state root. Mutable default
paths resolve there, including the autonomous realtime discovery and forward
shadow stores; the console uses the same `QUANTOS_STATE_ROOT`. Every invocation
writes a hash-bearing receipt under `.runtime/l2-release-runs/`. The receipt
separates release verification from worker outcome, so a verified release plus
a non-zero worker exit is recorded as `release_verified=true`, `status=FAILED`.
Receipt schema v3 also carries `release_evidence_required`,
`release_evidence_verified`, the exact release-evidence manifest and its SHA-256,
plus `worker_attestation_required`, `worker_attested`, the validated worker
attestation document, and its canonical SHA-256. Before importing any worker,
the launcher verifies that the full canonical generated-artifact set is hash-bound
to the same current full validation run. That required set includes the validation
report, top-tier assessment, SBOM, container vulnerability evidence, and the two-cold-
build reproducibility proof. An evidence-only descendant commit is accepted only
when its stable canonical source anchor matches the report; any source, artifact,
manifest, or cross-link drift fails closed. The unique
Text evidence is hashed in the repository's declared LF form so Windows checkout
line-ending conversion cannot invalidate an otherwise identical immutable release;
all non-line-ending bytes and all binary artifacts remain exact. The unique
`alpha_production_workflow`, the public-book `paper_multivenue_sor`, formal
`paper_order_preview`, its `paper_research_risk_bundle` producer, and
the `paper_operational_evidence` window collector, and
`realtime_alpha_execution_paper` are required: a successful or safe-blocked run
without its exact release commit and complete logical input/output hashes fails
closed. Alpha binds its evidence, policies, authority chain heads, and projection;
SOR binds its complete public cross-venue book bundle, routing contract, status
artifact, and dry-run reproducibility hash. Realtime execution paper binds one
transactionally frozen Signal Shadow view, parsed feature/contract status, pre/post
execution-ledger authorities, any consumed routing snapshot, and its report.
The research-risk producer binds the candidate universe and fixed risk contract,
requires all source authorities (including SQLite WAL bytes) to remain identical
across the computation, and binds the exact per-candidate input view plus output.
It must publish through a unique same-directory staging file. The producer computes
the source-after view and worker attestation against that staging file and may call
atomic `os.replace` only when `attestation.complete` is exactly `true`. An incomplete,
unstable, interrupted, or exception path must remove the staging file and leave the
prior canonical bytes unchanged. A failed release-run receipt therefore never
authorizes the canonical file, even when that file exists.

If a failed run may have been produced by a release older than this atomic-publish
contract, compare the canonical SHA-256 with the output hash of the last successful,
worker-attested receipt. On mismatch, move the canonical file to a recoverable
`evidence/quarantine/` path that includes its hash; do not delete or overwrite it.
Run the producer at most once from a validated immutable release in a window with no
other template worker active. Publishability is proven only when the new receipt has
`release_verified=true`, `worker_attested=true`, matching source-before/source-after
hashes, and an output hash equal to the canonical SHA-256. Atomic publication closes
the contamination failure mode; it does not make the current 23:08 schedule free of
writer contention, so a later `RESULT_1` remains a real availability breach and must
not be reset or thresholded away.
The operational collector binds the exact upstream release receipt and Shadow
artifact, candidate scope, preregistered window policy, isolated drill results,
and the append-only evidence authority before and after collection. Re-observing
one upstream receipt after a collector release change is cycle-idempotent: the
cycle is not counted twice or rewritten, while the new release records its own
isolated drill events.
Workers not yet instrumented remain explicit with
`worker_attestation_required=false`; that is a migration boundary, not a claim
that their mutable inputs are already release-bound.
The independent watchdog pins the Python executable class, launcher path,
module, state root, and release commit; legacy direct `-m tools.*`, unpinned, or
same-root actions fail closed as `ACTION_DRIFT`. Existing tasks must therefore
be re-registered from a clean release after adopting this contract.

| Task | Cadence | Module |
|---|---|---|
| `QUANTOS_factory_loop` | Sunday 20:00 | `tools.crypto_factory_loop` |
| `QUANTOS_ic_panel_refresh` | Sunday 20:30 | `tools.ic_panel_research` |
| `QUANTOS_paper_alpha_evaluation` | Sunday 20:45 | `tools.paper_alpha_evaluation` |
| `QUANTOS_forward_track` | Daily 21:00 poll (7-day internal due gate) | `tools.forward_track_advance` |
| `QUANTOS_alpha_maintenance` | Monday 09:05 | `tools.alpha_maintenance_sweep` |
| `QUANTOS_domain_event_ops` | Hourly | `tools.domain_event_ops` |
| `QUANTOS_scheduler_watchdog` | Every 15 minutes | `tools.l2_scheduler_watchdog` |
| `QUANTOS_gonogo_report` | Daily 23:00 | `tools.gonogo_report` |
| `QUANTOS_alpha_paper_cycle` | Daily 23:05 (after Go/No-Go) | `tools.alpha_paper_cycle` |
| `QUANTOS_paper_research_risk_bundle` | Daily 23:08 | `tools.paper_research_risk_bundle` |
| `QUANTOS_paper_order_preview` | Daily 23:10 (after paper cycle) | `tools.paper_order_preview` |
| `QUANTOS_binance_realtime_market` | Every 5 minutes | `tools.binance_realtime_market` |
| `QUANTOS_binance_systemic_risk` | Every 30 minutes | `tools.binance_systemic_risk` |
| `QUANTOS_binance_rpi_depth` | Hourly | `tools.binance_rpi_depth` |
| `QUANTOS_binance_realtime_features` | Every 5 minutes | `tools.binance_realtime_features` |
| `QUANTOS_binance_realtime_drift_alerts` | Every 5 minutes | `tools.binance_realtime_drift_alerts` |
| `QUANTOS_realtime_alpha_discovery` | Hourly | `tools.realtime_alpha_discovery` |
| `QUANTOS_realtime_alpha_forward_shadow` | Every minute | `tools.realtime_alpha_forward_shadow` |
| `QUANTOS_binance_realtime_stream` | Startup + 5-minute restart check | `tools.binance_realtime_stream` |
| `QUANTOS_paper_multivenue_sor` | Every 5 minutes | `tools.paper_multivenue_sor` |
| `QUANTOS_paper_realtime` | Every 5 minutes (short-lived worker) | `tools.paper_realtime` |
| `QUANTOS_paper_realtime_10k` | Every 5 minutes (short-lived worker, offset) | `tools.paper_realtime_10k` |
| `QUANTOS_paper_shadow_auto` | Daily 21:20 | `tools.paper_shadow_auto` |
| `QUANTOS_paper_shadow_realtime` | Every 5 minutes | `tools.paper_shadow_realtime` |
| `QUANTOS_paper_health` | Every 5 minutes (one minute after marks) | `tools.paper_health` |
| `QUANTOS_backup` | Daily 03:00, only when configuration passes | `tools.l2_backup` |
| `QUANTOS_external_heartbeat` | Every 5 minutes, only when configured | `tools.external_heartbeat` |
| `QUANTOS_domain_event_seal` | Daily 02:30, only when configured | `tools.domain_event_seal` |

## Three-Alpha Paper Cycle

The operator-level paper flow is a single idempotent, L2-only command. It runs
the authoritative dry-run seam for `carry_oi_majors`, `crowd_contra`, and
`top_contra`, rebuilds the shared paper-cycle/readiness evidence, then runs the
same pre-trade gate and dry-run adapter for the paper order preview. These three
pre-unified-workflow books are explicitly classified as
`LEGACY_DIAGNOSTIC_PAPER`: they may continue receiving public realtime marks and
producing PnL diagnostics, but they cannot count as a new-pipeline candidate or
obtain deployment eligibility. It never reads private credentials or submits
an order:

```powershell
rtk python -m tools.l2_paper_operator
```

The operator advances the public forward-paper clock by default. Use
`--no-forward` only when the current refresh evidence is already known to be
current. A failed requested forward refresh makes the whole operator fail
closed; stale evidence is never reported as a successful refresh. The official
evidence gate is eight realized periods per Alpha. New decisions are bound to
the append-only data-admission authority and new realizations are committed
atomically with their paper period. Rows written before that binding existed
are reported as `legacy_unattested_decisions` and never count toward the gate.
Current L2 output is diagnostic/paper evidence only and cannot authorize
Binance private access, live orders, or a live broker adapter. The combined
operator receipt is `data/cache/l2_paper_operator.json`.

The scheduled `paper_order_preview` task is the operator's paper-order view. It
reads the latest attested decision and local public mark cache, runs the same
pre-trade gate and dry-run adapter as the manual command, and atomically writes
`data/cache/paper_order_preview.json`. It is intentionally read-only and does
not advance forward realization or persist any live position; a non-empty
attested backlog or stale evidence blocks the preview.

The optional `paper_realtime` task is a bounded public-mark refresh. Each
invocation runs one cycle and exits, so it does not leave a resident polling
process. It only refreshes the L2 paper mark receipt; it never advances a
live decision, reads credentials, or submits an order. Register it with the
L2 schedule reconciler when continuous paper observation is desired, and
disable that task to stop the cadence.

`QUANTOS_paper_realtime_10k` uses the same bounded worker for the isolated
10,000 USD profile. It runs its own `--health-check` and writes the suffixed
receipts, so it does not share or mutate the default 1,000,000 USD paper
ledger. Both scheduled workers are short-lived and use `MultipleInstances
IgnoreNew`.

## Real-Time Paper Marking + Backtest Reference

For between-rebalance paper P&L, run the foreground public-mark cycle:

```powershell
rtk python -m tools.paper_realtime --cycles 1
```

要在同一条前台命令中完成纸面再平衡、公开标记和语义健康判定，可加
`--health-check`。命令仍是有界进程，结束后不会留下轮询后台进程：

```powershell
rtk python -m tools.paper_realtime --cycles 1 --health-check
```

只有实时周期和 `paper_health` 都通过时该命令才返回成功；健康报告仍写入
`data/cache/paper_health.json`（可用 `--health-output` 覆盖）。

The first cycle bootstraps the L2 paper operator, polls the unauthenticated
public mark-price endpoint, reconstructs the three Alpha paper fills from the
append-only order ledger, reconciles current equity/P&L, and stores a
hash-chained observation in `data/cache/paper_realtime.db`. It also carries the
latest authoritative dry-run/backtest summary into
`data/cache/paper_realtime_status.json`. Each cycle also refreshes public
funding-rate events and point-in-time funding marks; a cache older than 12 hours
is reported as incomplete rather than treated as zero funding. No private Binance endpoint, key,
account, broker SDK, or order submission is reachable from this driver.

To keep polling in the foreground (Ctrl-C stops it cleanly):

```powershell
rtk python -m tools.paper_realtime --follow --interval-seconds 30
```

Use `--refresh-paper` when a new paper rebalance decision is expected; without
it, follow mode only re-marks the existing paper positions. The default one
cycle is intentionally bounded so an operator never leaves an unmanaged
background process behind. The console exposes the latest receipt under the
Dry-Run Pipeline page and `/api/v1/console/dry-run.paper_realtime`.
The receipt keeps `paper_mark_ready` separate from
`backtest_reference_present_for_all_three`; `cycle_completed` is true only when
both the three-Alpha paper marks and all three authoritative backtest
references are present.

The paper-order ledger is one append-only paper-account profile.  Its decision
identity is `(alpha_id, decision_ts)`, so rerunning an already recorded
decision with a different `--aum` or `--cost-bps` is blocked explicitly rather
than silently changing positions or replaying the wrong size.  For a separate
small-capital sizing trial, use a separate store and output artifact:

```powershell
rtk python -m tools.paper_order_preview --aum 10000 `
  --paper-store data/cache/paper_order_ledger_10k.db `
  --output data/cache/paper_order_preview_10k.json
```

This remains paper-only; the separate store is an isolated paper account, not
an authorization for live trading.

要把完整的实时纸面链路固定在一个 10,000 USD 隔离账户，可直接运行：

```powershell
rtk python -m tools.paper_realtime --cycles 1 --health-check --aum 10000 `
  --paper-store data/cache/paper_order_ledger_10k.db `
  --store data/cache/paper_realtime_10k.db `
  --output data/cache/paper_realtime_10k_status.json `
  --operator-receipt data/cache/l2_paper_operator_10k.json `
  --health-output data/cache/paper_health_10k.json
```

该账户与默认 1,000,000 USD 纸面账本完全隔离；整手舍入可能使不同 Alpha
产生不同的成交腿数，但每个 Alpha 必须仍满足成交、拒单为零和对账残差为零。

## Paper Health Check

Task Scheduler success only proves that a worker exited successfully.  Before
using the paper view, run the semantic, fail-closed health check as well:

```powershell
rtk python -m tools.paper_health
```

It verifies that the public mark is fresh (default maximum age: five minutes),
all three Alpha accounts have paper fills and zero reconciliation residual,
the three authoritative dry-run references are present, the paper preview is
complete, and live/order/credential flags are explicitly disabled.  It writes
`data/cache/paper_health.json` and returns exit code `0` only when the paper
workflow is ready.  A stale or incomplete artifact returns non-zero; this
check never refreshes data or submits an order.

## Selection Trial-Ledger History

The M4 trial-ledger history must come from two distinct, completed,
selection-bearing research sessions in the production authority at
`data/cache/experiment_registry.db`. A caller-supplied label, a test registry,
or two snapshots of an unchanged pre-v2 ledger is not a session.

Preflight the production authority without creating or migrating it:

```powershell
rtk python -m tools.search_trial_ledger_history --inspect-ledger
```

### Portable history capture and repository seal

The live SQLite registry and the tracked portable evidence have separate
responsibilities. An operator inspects and captures the live registry; capture
changes that history by recording a selected completed session. The seal then
publishes the resulting minimal repository evidence and binds the exact history
and snapshot bytes. CI verifies the committed bundle, so a fresh checkout does not need
`data/cache`. Operators must still independently check the live registry; a
repository seal is not an assertion that the live database remains current.

Use this exact capture, live-check, seal, and portable-check sequence:

```powershell
rtk python -m tools.search_trial_ledger_history --inspect-ledger
$sessionId = Read-Host 'Paste one CAPTURABLE search_id from the inspection output'
if ([string]::IsNullOrWhiteSpace($sessionId)) { throw 'search_id is required' }
rtk python -m tools.search_trial_ledger_history --session-id $sessionId
rtk python -m tools.search_trial_ledger_history --check
rtk python -m tools.search_trial_ledger_history --seal-snapshot
rtk python -m tools.search_trial_ledger_history --check-snapshot
```

Do not substitute `--check-snapshot` for the live `--check`: the first validates
the published, data-minimized snapshot and the strict seal fields that hash the
exact history/snapshot bytes, while the second validates the current
operational authority. Never hand-edit any bundle artifact. A changed history
or snapshot byte must fail. A malformed, inconsistent, or hash-mismatched seal
must fail, but the seal JSON is not signed or self-digested: whitespace-only
changes or a still-valid `sealed_at_ms` can pass semantic verification. Git
review/provenance is the seal's current trust anchor.

The 2026-07-15 read-only preflight found `user_version=0`, 2 retained trials,
8 immutable evaluations, and no search-attempt or exact-link tables. It is
honestly `capture_ready: false`; none of those legacy rows proves a completed
selection session. The first supported real session will invoke the store's
tested atomic v2 migration before it writes search provenance.

Proceed only when the live-authority output reports its actual schema as v2 or
later, separately reports v2 as the minimum required contract, verifies the v2
event/candidate/evaluation-link subset, and reports `capture_ready: true`. A
portable repository snapshot must remain exactly schema v2. A live v3 result
does not prove that v3-only family-authority tables are healthy; those tables
require their own exact verifier when the separately reviewed White/SPA Phase A
is implemented. The current live-v3 check authorizes only the v2 subset. Each capturable attempt must be
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

The source-contract v5 backup covers twelve required L2 authority stores plus
nine optional authority roles when present, including restore history, the MFT
Testnet soak evidence authority, approvals, and identity authorization. Active
SQLite files are captured through the SQLite online backup API. The copy is
bounded to 256 pages per step and deliberately yields between successful partial
steps; this preserves a consistent online snapshot without starving writers on
large DELETE-journal authorities. Every artifact and the manifest are AES-GCM encrypted; the batch is
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
