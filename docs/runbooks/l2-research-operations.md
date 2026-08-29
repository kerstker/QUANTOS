# QUANTOS L2 MVP Operations

The current MVP profile is a public-data and control-plane runtime. It authorizes
only the 22 modules in quantos/contracts/l2_schedule_policy.py. It does not
authorize alpha discovery, selection, lifecycle advancement, forward/paper
evaluation, paper execution, venue access, order submission, emergency trading
action, or a credential-bearing operations API.

All live, broker, order-submission, and direct-AI trading flags remain explicitly
false. Human approval remains required. Retained perpetual-ledger v1 artifacts
are forensic inputs only and carry INVALIDATED_PERP_LEDGER_V1.

## Immutable release and state roots

Registration requires two absolute, different, non-overlapping roots:

- a clean immutable release checkout containing code and canonical release
  evidence for the exact commit;
- a mutable state root owning data/cache, evidence, and .runtime.

On Windows, reconcile the reviewed profile from the release checkout:

    powershell -ExecutionPolicy Bypass -File H:\path\to\validated-release\scripts\register_schedules.ps1 -StateRoot H:\path\to\operational-state

The script location is the code root; it is not caller-selected. Registration
fails when HEAD is not an exact 40-character commit, the release is dirty, the
roots overlap, or canonical validation/release evidence is absent or stale.
Every task enters through tools/l2_schedule_entrypoint.py and binds:

- QUANTOS_RELEASE_COMMIT;
- QUANTOS_RELEASE_ROOT;
- QUANTOS_STATE_ROOT.

The entrypoint verifies the immutable release and canonical evidence before any
safe worker import. It rejects every hard-quarantined or MVP-disabled module
before release verification, parameter processing, registry persistence, or
worker startup. Do not bypass it with direct python -m tools.* invocations.

Schedule reconciliation is not permission to generate new canonical evidence.
Canonical/full validation remains a separately controlled release operation.

## Exact MVP schedule

The policy is a closed set: 22 safe, 13 hard-quarantined, and 15 MVP-disabled
modules. The sets are mutually exclusive and cover the prior 50-module
scheduler vocabulary.

| Task | Cadence | Module |
|---|---|---|
| QUANTOS_artifact_admission_seal | Daily 02:40, only when external seal configuration passes | artifact_admission_seal |
| QUANTOS_binance_book_depth_forward_refresh | Daily 10:00 | binance_book_depth_forward_refresh |
| QUANTOS_binance_realtime_drift_alerts | Every 5 minutes | binance_realtime_drift_alerts |
| QUANTOS_binance_realtime_features | Every 5 minutes | binance_realtime_features |
| QUANTOS_binance_realtime_market | Every 5 minutes | binance_realtime_market |
| QUANTOS_binance_realtime_stream | Startup plus 5-minute restart poll | binance_realtime_stream |
| QUANTOS_binance_rpi_depth | Hourly | binance_rpi_depth |
| QUANTOS_binance_systemic_risk | Every 30 minutes | binance_systemic_risk |
| QUANTOS_bybit_realtime_stream | Startup plus 5-minute restart poll | bybit_realtime_stream |
| QUANTOS_cftc_tff_forward_refresh | Tuesday 10:10 | cftc_tff_forward_refresh |
| QUANTOS_cross_venue_liquidation_health | Every 30 minutes | cross_venue_liquidation_health |
| QUANTOS_defillama_stablecoin_forward_refresh | Daily 09:40 | defillama_stablecoin_forward_refresh |
| QUANTOS_defillama_tvl_forward_refresh | Daily 09:30 | defillama_tvl_forward_refresh |
| QUANTOS_domain_event_ops | Every 15 minutes | domain_event_ops |
| QUANTOS_domain_event_seal | Daily 02:30, only when external seal configuration passes | domain_event_seal |
| QUANTOS_external_heartbeat | Every 5 minutes, only when configured | external_heartbeat |
| QUANTOS_hyperliquid_public_forward_refresh | Daily 09:50 | hyperliquid_public_forward_refresh |
| QUANTOS_backup | Daily 03:00, only when backup configuration passes | l2_backup |
| QUANTOS_okx_borrow_forward_refresh | Daily 09:55 | okx_borrow_forward_refresh |
| QUANTOS_raw_archive_audit | Daily 04:00 | raw_archive_audit |
| QUANTOS_scheduler_watchdog | Every 15 minutes | l2_scheduler_watchdog |
| QUANTOS_wikimedia_attention_forward_refresh | Daily 09:45 | wikimedia_attention_forward_refresh |

The two realtime streams are resident public collectors. The watchdog has a
dedicated unit. Linux rendering generates reviewed concrete units for the
remaining safe workers; it does not install, enable, or start anything.

## Required isolation of old schedules

Windows reconciliation disables the 28 denied tasks and stops their exact
module-owned process trees before it registers safe replacements. Conditional
backup, heartbeat, and seal workers are quiesced by module identity before
configuration readiness is checked, including when they will remain disabled.

Formal Linux cutover requires privilege and is stricter:

- each denied service must be both masked and inactive, or absent;
- each denied timer must be absent, or inactive and disabled/masked;
- an active timer is a breach even when its UnitFileState says disabled;
- a static or disabled service that remains manually startable is a breach;
- generic quantos-l2@ service/timer templates must be absent or permanently
  masked and inactive;
- orphan timers and unknown quantos-* units fail closed.

For every explicit denied unit, stop and disable its timer, then stop and mask
its service. Verify LoadState, UnitFileState, ActiveState, SubState, and Result
after daemon-reload. Never use a broad wildcard removal command. Resolve and
review the exact unit list from the central policy first.

The 13 hard-quarantined modules are:

- alpha_lifecycle_cockpit
- alpha_maintenance_sweep
- alpha_paper_cycle
- alpha_production_workflow
- candidate_portfolio_evidence
- crypto_factory_loop
- dry_run_candidate_multi
- ic_panel_research
- paper_alpha_evaluation
- paper_realtime
- paper_realtime_10k
- paper_research_risk_bundle
- register_alpha_catalog

The 15 MVP-disabled modules are:

- forward_track_advance
- gonogo_report
- hourly_alpha_advance
- mft_l2_runtime
- paper_health
- paper_multivenue_sor
- paper_operational_evidence
- paper_order_preview
- paper_shadow_auto
- paper_shadow_realtime
- realtime_alpha_discovery
- realtime_alpha_execution_paper
- realtime_alpha_formal_shadow
- realtime_alpha_forward_shadow
- rpi_low_frequency_research

The watchdog must report zero denied-task, startable-service, generic-template,
or unknown-unit breaches before a formal MVP schedule is called isolated.
User-local transient console services are a separate diagnostic runtime and do
not prove system-level cutover.

## Binance full-chain proof renewal

binance_realtime_stream is the sole owner of source/cursor genesis audits. It
renews each 24-hour proof in a background subprocess four hours before expiry,
with a three-hour hard ceiling. binance_realtime_features consumes a fresh
resident proof and independently verifies only the suffix; it must never scan
either journal from genesis inside its five-minute schedule.

While a missing or expired proof is rebuilding:

- the resident public WebSocket collector continues ingesting;
- feature publication fails quickly and the timer retries later;
- all research, candidate, and paper consumers remain blocked.

Do not delete the feature-batch authority, extend the proof lifetime, disable
payload verification, or repeatedly start the feature service by hand. Recovery
requires fresh full proofs, a trusted suffix result, and zero watchdog breach.

## Legacy research and paper procedures

The former three-alpha paper cycle, realtime paper marking, paper health,
selection trial-ledger capture, factory loop, IC refresh, lifecycle cockpit,
forward track, shadow, candidate portfolio, Go/No-Go, hourly-alpha, and MFT
procedures are quarantined in this MVP.

Do not run their old operator commands, register their old schedules, or use
their retained JSON/SQLite rows to select, promote, risk-approve, persist paper
authority, or claim runtime readiness. Existing files are preserved for audit;
they are not deleted, migrated, or silently relabelled. Reopening requires an
independent perpetual-ledger v2 with exact data/code/result binding and new
admission evidence.

## Event operations

Manual safe execution:

    python -m tools.domain_event_ops

The cycle drains transactional outboxes and evaluates the shared event
authority. Results are written to:

- data/cache/domain_event_outbox_drain.json
- data/cache/domain_event_health.json
- data/cache/domain_event_ops_status.json
- data/cache/alerts.jsonl for non-pass summaries

Exit 1 or 2 is an operational finding, not a scheduler defect. Inspect the
status snapshot before rerunning an owner producer. Do not delete, rebuild,
auto-migrate, compact, or reset an authority merely to obtain a green result.

domain_event_health.json.worker_identity_policy must remain
PROCESS_DECLARED_UNAUTHENTICATED. This is the honest L2 assurance ceiling, not
an authentication success. External heartbeat data must not include declared
worker names or local paths.

Legacy owner-store migration is outside the automatic MVP schedule. It requires
an explicit maintenance change, a stopped domain-event task, no WAL/SHM
sidecars, recoverable backups, and independent post-migration verification.

## Backup

QUANTOS_backup is registered only when a passphrase of at least 12 characters
and an absolute external destination are available through the approved
operator configuration path. On Windows the destination must be UNC or another
volume. Never place the passphrase in task arguments, source, logs, or this
repository.

The backup captures required L2 authority stores, uses SQLite online backup for
active databases, encrypts every artifact and the manifest, and publishes only
after isolated restore, digest verification, and SQLite integrity_check.
Success evidence is evidence/l2_backup_latest.json.

The append-only restore-history authority must not be deleted or rebuilt to
clear a failure. A pinned task action and exit code zero are insufficient:
missing, corrupt, empty, failed, stale, overdue, or cadence-breached history is
a watchdog breach. Local migration backups do not satisfy off-host retention.

## External heartbeat

Set QUANTOS_HEARTBEAT_URL through the approved operator configuration path to
an operator-owned HTTPS endpoint. Registration rejects HTTP, userinfo,
fragments, redirects, and loopback targets. The sender transmits only bounded
scheduler, event-authority, and backup health fields.

    python -m tools.external_heartbeat

Code and local delivery tests do not prove a monitoring SLA. Retain independent
evidence that a heartbeat arrived and that a deliberately missed heartbeat
generated an operator-visible alert within the agreed SLO.

## Event authority anchor

Set QUANTOS_EVENT_SEAL_DEST to a UNC or different-volume destination, then
reconcile schedules. Manual export and verification are:

    python -m tools.domain_event_seal
    python -m tools.domain_event_seal --verify-anchor <external-anchor-path>

The exporter snapshots the authority read-only, verifies all chains, and writes
a deterministic content-addressed anchor with create-exclusive semantics. The
verifier proves current chains still extend the anchored prefixes. A filesystem
create-once anchor remains PARTIAL_EXTERNAL_WORM_UNVERIFIED until an
operator-owned object-lock/WORM receipt and independent signature exist.

## Boundaries

- Windows scheduled workers use S4U, Limited, and pythonw; they do not require
  an interactive token, stored password, broker credential, or mapped share.
- Local JSONL alert recording is authoritative; optional external delivery has
  no proven SLA without independent receipts.
- The watchdog is independent of monitored worker imports but remains on the
  same host and scheduler.
- No managed task may contain a live module, order/action flag, broker path,
  private venue credential, or mutable configuration that expands the allowlist.
- A local isolated console/runner/frontend smoke stack is diagnostic only. It
  does not replace immutable release evidence or privileged system isolation.
