# QUANTOS L2 Backup and Restore Runbook

Date: 2026-07-16  
Status: Historical operating procedure; see current DGX acceptance below

> **2026-09-13 DGX update:** The user relaxed the full-scale restore target to **7,200 seconds (2 hours)**. Use the explicit `--rto-ms 7200000` override for this scope. Full verification and the 24-hour RPO remain required; prior 300-second failures remain unchanged. See [scope and current attempts](../engineering/specs/2026-09-13-full-restore-target.md). The older measurements below are historical and do not establish current full-scale readiness.

## Overview

This runbook documents the operational procedures for QUANTOS L2 authority state backup and restore operations. The backup system protects 10 required authority stores (domain events, dry-run ledger, experiment registry, paper track, etc.) with AES-GCM encryption and restore verification.

## Prerequisites

### Required Environment Variables

1. **QUANTOS_BACKUP_PASSPHRASE**
   - Minimum 12 non-whitespace characters
   - Set at User or Machine level
   - Never embedded in scheduled task command
   - Example: `[Environment]::SetEnvironmentVariable("QUANTOS_BACKUP_PASSPHRASE", "strong-pass-phrase-12+", "User")`

2. **QUANTOS_BACKUP_DEST**
   - Absolute path (UNC or different volume from workspace)
   - Must NOT be on same volume as workspace (H:\)
   - Example: `I:\QUANTOS_L2_BACKUPS` or `\\backup-server\quantos`
   - Set at User or Machine level
   - Example: `[Environment]::SetEnvironmentVariable("QUANTOS_BACKUP_DEST", "I:\QUANTOS_L2_BACKUPS", "User")`

### Verification

```powershell
# Check configuration
$dest = [Environment]::GetEnvironmentVariable("QUANTOS_BACKUP_DEST", "User")
$pass = [Environment]::GetEnvironmentVariable("QUANTOS_BACKUP_PASSPHRASE", "User")
Write-Host "QUANTOS_BACKUP_DEST: $dest"
Write-Host "Passphrase length: $($pass.Length) chars (need 12+)"
```

## Scheduled Backup Operations

### Registering the Backup Task

The backup task is registered via `scripts/register_schedules.ps1`:

```powershell
cd H:\code\QUANTOS
.\scripts\register_schedules.ps1
```

The script will:
- Validate passphrase strength (≥12 chars)
- Verify destination is external (different volume or UNC)
- Register task `QUANTOS_backup` if configuration is valid
- Skip registration with warning if configuration is unavailable

**Schedule**: Daily at 03:47 (non-peak hour)

### Task Configuration

- **Task Name**: `QUANTOS_backup`
- **Action**: `python -m tools.l2_backup`
- **Working Directory**: `H:\code\QUANTOS`
- **Trigger**: Daily at 03:47
- **User**: Current user context
- **Privileges**: No elevation required (User-level environment variables)

### Monitoring Scheduled Backups

Check task status:

```powershell
Get-ScheduledTask -TaskName "QUANTOS_backup" | Select-Object TaskName, State, LastRunTime, LastTaskResult
```

Check backup history:

```powershell
Get-ChildItem $env:QUANTOS_BACKUP_DEST | Select-Object Name, LastWriteTime
```

Expected: One backup directory per run with timestamp format `YYYYMMDDTHHMMSSdZ`

## Manual Backup Operations

### Ad-Hoc Backup

Run a manual backup (e.g., before system maintenance):

```powershell
cd H:\code\QUANTOS
python -m tools.l2_backup
```

Expected output: JSON with `"verdict": "PASS"`

### Backup Verification

Each backup automatically performs:
1. SQLite online backup API for database sources
2. Stable file read with before/after stat check
3. AES-GCM encryption of all artifacts
4. Manifest generation with SHA256 digests
5. Restore verification into temporary isolated tree
6. SQLite integrity check on restored databases

A backup only succeeds if ALL verification steps pass.

## Restore Operations

### Restore Drill (Test Recovery)

Run a restore drill without affecting production state:

```powershell
cd H:\code\QUANTOS
python -m tools.restore_drill
```

This:
- Identifies the most recent backup batch
- Restores to isolated temporary directory
- Verifies manifest and SQLite integrity
- Records attempt in `l2_restore_history.db`
- Cleans up temporary files
- Does NOT touch production databases

Expected output: JSON with `"verdict": "PASS"`

### Production Restore (Disaster Recovery)

**WARNING**: Production restore overwrites current authority state. Only perform this after confirming data loss or corruption.

#### Pre-Restore Checklist

1. [ ] Confirm current state is corrupted or lost
2. [ ] Stop all QUANTOS scheduled tasks
3. [ ] Stop any running QUANTOS services (console API, research runner, ops API)
4. [ ] Backup current state (even if corrupted) to separate location
5. [ ] Identify target backup timestamp to restore

#### Restore Steps

1. **Stop all QUANTOS operations**:

```powershell
# Disable all QUANTOS tasks
Get-ScheduledTask -TaskName "QUANTOS_*" | Disable-ScheduledTask

# Verify no Python processes running QUANTOS modules
Get-Process python -ErrorAction SilentlyContinue
```

2. **Identify target backup**:

```powershell
Get-ChildItem $env:QUANTOS_BACKUP_DEST | Sort-Object Name | Format-Table Name, LastWriteTime
```

3. **Restore from backup**:

```powershell
cd H:\code\QUANTOS
# The tool automatically selects the most recent backup
# For specific timestamp, use: --batch-id 20260715T193659Z
python -m tools.l2_backup --restore
```

4. **Verify restoration**:

```powershell
# Check that required databases exist and are not zero-size
Get-ChildItem data/cache/*.db | Select-Object Name, Length, LastWriteTime
```

5. **Re-enable scheduled tasks**:

```powershell
Get-ScheduledTask -TaskName "QUANTOS_*" | Enable-ScheduledTask
```

6. **Resume normal operations**

#### Post-Restore Verification

- Run a manual backup to verify system health: `python -m tools.l2_backup`
- Check console health endpoints
- Verify most recent runs in research registry
- Check paper track latest decision

### Recovery Time Objective (RTO)

- **Current DGX full-scale target**: 7,200 seconds (2 hours), set explicitly with `--rto-ms 7200000`.
- **Historical target**: 300 seconds; unchanged in old receipts and generic library defaults.
- **Measured evidence**: the earlier full-scale artifact restore exceeded its 300-second target at 932.389 seconds; full SQLite verification remained incomplete. A fresh complete result under the new target is pending. The old ~319ms small-fixture measurement is not full-scale recovery evidence.

RTO includes:
- Identifying backup batch
- Decrypting manifest
- Restoring all authority databases
- Verifying SQLite integrity

### Recovery Point Objective (RPO)

- **Target RPO**: 86,400,000 ms (24 hours)
- **Scheduled cadence**: Daily at 03:47

With daily backups, maximum data loss is 24 hours (time since last successful backup).

## Backup Source Contract

### Required Sources (10)

These authority stores are required for backup to succeed:

1. `data/cache/domain_events.db` - Domain event authority
2. `data/cache/domain_event_projection.db` - Event projections
3. `data/cache/dry_run.db` - Dry-run execution ledger
4. `data/cache/experiment_registry.db` - Research experiment registry
5. `data/cache/live_refresh_history.db` - Data refresh history
6. `data/cache/data_quality_admission.db` - Data quality gates
7. `data/cache/paper_track.db` - Forward paper track decisions
8. `data/cache/research_runs.db` - Research run registry
9. `data/cache/governance_audit.db` - Governance audit log
10. `data/cache/l2_restore_history.db` - Restore history authority

### Optional Sources (6)

These are backed up when present but backup succeeds even if absent:

- `data/cache/alpha_maintenance.db` - Alpha lifecycle state
- `data/cache/live_kill_switch.db` - Kill switch latch state
- `data/cache/deployment_state.db` - Deployment authority
- `data/cache/artifact_admission.db` - Artifact admission gate
- `data/cache/approvals.db` - Multi-party approval state
- `data/cache/identity_authorization.db` - Identity authorization

### Exclusions

The following are intentionally excluded from backup:

- `key/` directory (broker credentials, signing keys)
- Testnet/demo credentials
- Large derived datasets in `data/`
- Temporary files and caches
- Build artifacts and node_modules

## Security Boundaries

### Encryption

- All backup artifacts encrypted with AES-GCM
- Passphrase must be strong (≥12 chars, non-whitespace)
- Manifest includes plaintext digests but no secrets
- Encrypted files have `.enc` suffix

### Access Control

- Backup destination requires write access
- Restore requires read access to backup destination
- No elevation/admin privileges required
- Operates under user context with environment variables

### Audit Trail

Every backup and restore operation is recorded in:
- `l2_restore_history.db` (append-only authority)
- Task Scheduler history
- Evidence snapshots in `evidence/l2_backup_latest.json`

## Troubleshooting

### Backup Task Shows "Disabled"

**Cause**: Configuration does not meet fail-closed requirements

**Solution**:
1. Check environment variables are set correctly
2. Verify passphrase is ≥12 characters
3. Verify destination is absolute and external volume
4. Re-run `scripts/register_schedules.ps1`

### "BACKUP_CONFIGURATION_UNAVAILABLE" Error

**Cause**: Environment variables not set in current session

**Solution**:
```powershell
# Set for current session
$env:QUANTOS_BACKUP_DEST = [Environment]::GetEnvironmentVariable("QUANTOS_BACKUP_DEST", "User")
$env:QUANTOS_BACKUP_PASSPHRASE = [Environment]::GetEnvironmentVariable("QUANTOS_BACKUP_PASSPHRASE", "User")
```

### Backup Fails with "restore_verified: False"

**Cause**: Encrypted backup could not be decrypted or restored

**Solution**:
1. Verify passphrase is correct
2. Check destination disk space
3. Check for file system errors
4. Review manifest.json.enc for corruption

### SQLite Integrity Check Failed

**Cause**: Source database is corrupted

**Solution**:
1. Identify corrupted database from backup output
2. Consider restoring from previous known-good backup
3. If corruption is in optional source, investigate but backup can proceed

## Evidence and Compliance

### Operational Evidence Location

- `evidence/l2_backup_latest.json` - Latest backup result snapshot
- `I:\QUANTOS_L2_BACKUPS\` - Encrypted backup archives
- `data/cache/l2_restore_history.db` - Restore drill and production restore audit

### Evidence Requirements for Maturity

**OPS.BACKUP.ENCRYPTED** (M2→M3):
- ✅ Encrypted backups present in external destination
- ✅ 5+ backup snapshots generated
- ✅ Restore verification included in each backup
- ✅ Scheduled task registered and enabled

**OPS.RESTORE.DRILL** (M2→M3):
- ✅ Restore drill procedure documented
- ⏳ Periodic restore drill schedule (recommend: monthly)
- RTO documented: current DGX full-scale target 7,200s; fresh full-scale acceptance pending. Historical small-fixture timing is not a production readiness measurement.
- ⏳ RPO documented (24h target, verified in practice)

### Monthly Restore Drill Checklist

Schedule a monthly restore drill to verify recovery capability:

1. [ ] Run `python -m tools.restore_drill`
2. [ ] Verify output shows `"verdict": "PASS"`
3. [ ] Check `l2_restore_history.db` for recorded attempt
4. [ ] Confirm the complete restore meets the target recorded for that attempt (current DGX full-scale target: 7,200 seconds), including full SQLite and history checks
5. [ ] Document result in operations log

## Change History

- 2026-07-16: Initial runbook created with operational configuration
  - 5 backup snapshots generated
  - Configuration documented
  - RTO/RPO targets established
  - Source contract version 2 documented
