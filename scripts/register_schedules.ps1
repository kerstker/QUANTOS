# QUANTOS L2_DRY_RUN_ONLY schedule registration.
#
# Running this script authorizes only research, paper evidence, maintenance,
# domain-event operations, and read-only reporting jobs. It never registers a
# venue submission, emergency trading action, credential-bearing API, or live
# monitoring process. Legacy tasks with those capabilities are disabled (not
# deleted) before the L2 jobs are registered.
#
# The script location is the immutable release code root. -StateRoot must point
# at a separate mutable operational checkout containing data/cache; scheduled
# imports still resolve only through tools/l2_schedule_entrypoint.py in this
# release root. Registration and every later launch bind the exact clean HEAD.
param([Parameter(Mandatory = $true)][string]$StateRoot)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$codeRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($StateRoot)) {
  throw "StateRoot is required for immutable release registration"
}
$stateRoot = (Resolve-Path -LiteralPath $StateRoot).Path
$codePrefix = $codeRoot.TrimEnd("\") + "\"
$statePrefix = $stateRoot.TrimEnd("\") + "\"
if (
  $codeRoot -eq $stateRoot -or
  $stateRoot.StartsWith($codePrefix, [StringComparison]::OrdinalIgnoreCase) -or
  $codeRoot.StartsWith($statePrefix, [StringComparison]::OrdinalIgnoreCase)
) {
  throw "release and state roots must be different and non-overlapping"
}
$gitCommand = Get-Command git -ErrorAction Stop
$releaseCommit = (& $gitCommand.Source -C $codeRoot rev-parse --verify HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $releaseCommit -notmatch "^[0-9a-f]{40}$") {
  throw "release code root has no valid Git HEAD"
}
$releaseStatus = @(
  & $gitCommand.Source -C $codeRoot status --porcelain=v1 --untracked-files=all --ignore-submodules=none --
)
if ($LASTEXITCODE -ne 0) {
  throw "release code root cleanliness could not be verified"
}
if ($releaseStatus.Count -ne 0) {
  throw "release code root must be clean before schedule registration"
}
$launcher = Join-Path $codeRoot "tools\l2_schedule_entrypoint.py"
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
  throw "L2 schedule entrypoint is unavailable"
}
$pythonCommand = Get-Command python -ErrorAction Stop
# ``Get-Command python`` may resolve the WindowsApps execution alias.  Its
# sibling pythonw.exe is another short-lived launcher shim: Task Scheduler then
# sees the shim exit, marks the task Ready, and starts overlapping real workers
# despite MultipleInstances=IgnoreNew.  Ask Python for the actual interpreter
# path and bind the action to its real pythonw sibling instead.
$pythonExecutable = (& $pythonCommand.Source -c "import sys; print(sys.executable)").Trim()
if (
  $LASTEXITCODE -ne 0 -or
  [string]::IsNullOrWhiteSpace($pythonExecutable) -or
  -not [IO.Path]::IsPathRooted($pythonExecutable) -or
  -not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)
) {
  throw "configured Python runtime could not resolve its real executable"
}
$pythonw = Join-Path (Split-Path -Parent $pythonExecutable) "pythonw.exe"
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
  throw "pythonw.exe is unavailable beside the configured Python runtime"
}
# Keep every L2 worker outside the interactive desktop session.  pythonw.exe
# removes the ordinary console host, while S4U also prevents a future child
# process regression from surfacing a window in the signed-in operator session.
# These workers use only local state and unauthenticated public HTTPS data; they
# do not require an interactive token, stored password, broker credential, or
# mapped network share.
$l2Principal = New-ScheduledTaskPrincipal `
  -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
  -LogonType S4U -RunLevel Limited

function Disable-TaskIfPresent {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [string]$Module
  )

  $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
  if ($null -eq $task) {
    Write-Host "${Name}: not present"
    return
  }
  if ($task.State -eq "Running") {
    Stop-ScheduledTask -TaskName $Name -ErrorAction Stop
    $stopDeadline = (Get-Date).AddSeconds(30)
    do {
      Start-Sleep -Milliseconds 200
      $task = Get-ScheduledTask -TaskName $Name -ErrorAction Stop
    } while ($task.State -eq "Running" -and (Get-Date) -lt $stopDeadline)
    if ($task.State -eq "Running") {
      throw "${Name}: running instance did not stop before release registration"
    }
    Write-Host "${Name}: stopped before release registration"
  }
  if (-not [string]::IsNullOrWhiteSpace($Module)) {
    # WindowsApps pythonw is a launcher shim. Task Scheduler can report Ready
    # after stopping the shim while the real interpreter remains orphaned and
    # keeps mutable SQLite/lock files open. Match only the approved L2 launcher
    # plus the exact module, terminate those descendants, and verify quiescence.
    $workerPattern = "*l2_schedule_entrypoint.py* ${Module} --release-commit *"
    $workers = @(
      Get-CimInstance Win32_Process -ErrorAction Stop |
        Where-Object { $_.CommandLine -like $workerPattern }
    )
    foreach ($worker in $workers) {
      # The scheduled shim and its interpreter can finish between CIM
      # enumeration and Stop-Process.  Treat that specific disappearance as
      # quiesced; the mandatory re-scan below still fails closed when any
      # matching process survives (including access-denied failures).
      Stop-Process -Id $worker.ProcessId -Force -ErrorAction SilentlyContinue
    }
    $workerDeadline = (Get-Date).AddSeconds(30)
    do {
      $remainingWorkers = @(
        Get-CimInstance Win32_Process -ErrorAction Stop |
          Where-Object { $_.CommandLine -like $workerPattern }
      )
      if ($remainingWorkers.Count -eq 0) {
        break
      }
      Start-Sleep -Milliseconds 200
    } while ((Get-Date) -lt $workerDeadline)
    if ($remainingWorkers.Count -ne 0) {
      throw "${Name}: orphaned L2 worker did not stop before release registration"
    }
    if ($workers.Count -ne 0) {
      Write-Host "${Name}: stopped $($workers.Count) orphaned L2 worker process(es)"
    }
  }
  Disable-ScheduledTask -TaskName $Name -ErrorAction Stop | Out-Null
  Write-Host "${Name}: disabled before L2 registration"
}

function Get-UserOrMachineEnvironmentValue {
  param([Parameter(Mandatory = $true)][string]$Name)

  $value = [Environment]::GetEnvironmentVariable($Name, "User")
  if ([string]::IsNullOrWhiteSpace($value)) {
    $value = [Environment]::GetEnvironmentVariable($Name, "Machine")
  }
  return $value
}

function Get-L2BackupReadiness {
  $passphrase = Get-UserOrMachineEnvironmentValue -Name "QUANTOS_BACKUP_PASSPHRASE"
  if ([string]::IsNullOrWhiteSpace($passphrase) -or $passphrase.Trim().Length -lt 12) {
    return [pscustomobject]@{ Ready = $false; Reason = "backup passphrase is missing or shorter than 12 characters" }
  }
  $destination = Get-UserOrMachineEnvironmentValue -Name "QUANTOS_BACKUP_DEST"
  if ([string]::IsNullOrWhiteSpace($destination) -or -not [IO.Path]::IsPathRooted($destination)) {
    return [pscustomobject]@{ Ready = $false; Reason = "QUANTOS_BACKUP_DEST is missing or not absolute" }
  }
  $workspaceRoot = [IO.Path]::GetPathRoot($stateRoot)
  $destinationRoot = [IO.Path]::GetPathRoot($destination)
  $isUnc = $destination.StartsWith("\\")
  if (-not $isUnc -and $workspaceRoot -eq $destinationRoot) {
    return [pscustomobject]@{ Ready = $false; Reason = "backup destination is on the workspace volume" }
  }
  return [pscustomobject]@{ Ready = $true; Reason = "strong passphrase and external destination are configured" }
}

function Get-ExternalHeartbeatReadiness {
  $endpoint = Get-UserOrMachineEnvironmentValue -Name "QUANTOS_HEARTBEAT_URL"
  $uri = $null
  if (
    [string]::IsNullOrWhiteSpace($endpoint) -or
    -not [Uri]::TryCreate($endpoint, [UriKind]::Absolute, [ref]$uri) -or
    $uri.Scheme -ne "https" -or
    $uri.IsLoopback -or
    -not [string]::IsNullOrEmpty($uri.UserInfo) -or
    -not [string]::IsNullOrEmpty($uri.Fragment)
  ) {
    return [pscustomobject]@{ Ready = $false; Reason = "operator-owned HTTPS heartbeat endpoint is not configured" }
  }
  return [pscustomobject]@{ Ready = $true; Reason = "HTTPS heartbeat endpoint is configured" }
}

function Get-EventSealReadiness {
  $destination = Get-UserOrMachineEnvironmentValue -Name "QUANTOS_EVENT_SEAL_DEST"
  if ([string]::IsNullOrWhiteSpace($destination) -or -not [IO.Path]::IsPathRooted($destination)) {
    return [pscustomobject]@{ Ready = $false; Reason = "QUANTOS_EVENT_SEAL_DEST is missing or not absolute" }
  }
  $workspaceRoot = [IO.Path]::GetPathRoot($stateRoot)
  $destinationRoot = [IO.Path]::GetPathRoot($destination)
  $isUnc = $destination.StartsWith("\\")
  if (-not $isUnc -and $workspaceRoot -eq $destinationRoot) {
    return [pscustomobject]@{ Ready = $false; Reason = "event seal destination is on the workspace volume" }
  }
  return [pscustomobject]@{ Ready = $true; Reason = "external event seal destination is configured" }
}

function Register-L2Task {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Module,
    [Parameter(Mandatory = $true)]$Trigger,
    [TimeSpan]$ExecutionTimeLimit = (New-TimeSpan -Hours 6)
  )

  # Register-ScheduledTask -Force replaces the definition but does not
  # terminate an already-running instance.  Without an explicit quiesce, an
  # immutable release switch leaves old code holding the mutable state DBs for
  # hours while the new release starts beside it.  Stop and verify the exact
  # same task before replacing its definition; failure is loud and leaves no
  # mixed-release registration claim.
  Disable-TaskIfPresent -Name $Name -Module $Module
  $logRoot = Join-Path $stateRoot ".runtime\logs"
  $action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "`"$launcher`" $Module --release-commit $releaseCommit" `
    -WorkingDirectory $stateRoot
  $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -StartWhenAvailable -ExecutionTimeLimit $ExecutionTimeLimit
  Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger `
    -Settings $settings -Principal $l2Principal `
    -Description "QUANTOS L2_DRY_RUN_ONLY: tools.$Module@$releaseCommit" `
    -Force | Out-Null
  Write-Host "${Name}: registered tools.$Module"
}

$legacyLiveTasks = @(
  "QUANTOS_live_track_binance",
  "QUANTOS_live_track_bybit_demo",
  "QUANTOS_risk_sentinel",
  "QUANTOS_ops_api",
  "QUANTOS_watchdog",
  "QUANTOS_backup",
  "QUANTOS_external_heartbeat",
  "QUANTOS_domain_event_seal",
  "QUANTOS_artifact_admission_seal"
)
foreach ($taskName in $legacyLiveTasks) {
  Disable-TaskIfPresent -Name $taskName
}

$factoryTrigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 `
  -DaysOfWeek Sunday -At "20:00"
$icTrigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 `
  -DaysOfWeek Sunday -At "20:30"
$paperAlphaEvaluationTrigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 `
  -DaysOfWeek Sunday -At "20:45"
# Reconcile candidate identity and every evidence stage before the next
# five-minute paper worker. The job is read-mostly, idempotent, and L2-only.
$alphaProductionWorkflowTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(3) `
  -RepetitionInterval (New-TimeSpan -Minutes 5)
# Candidate backtests are deliberately outside the five-minute mark-to-market
# path. Run them after the daily forward refresh, then build portfolio evidence
# from their persisted authoritative runs. The gap prevents the second task
# from reading a partially refreshed dry-run store.
$candidateDryRunTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "21:10"
$candidatePortfolioEvidenceTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "21:45"
# Poll daily after the expected public-data close so a newly available
# watermark is consumed promptly; the worker's internal seven-day due check
# remains authoritative.
$forwardTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "21:00"
$maintenanceTrigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 `
  -DaysOfWeek Monday -At "09:05"
$eventOpsTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Minutes 15)
$schedulerWatchdogTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
  -RepetitionInterval (New-TimeSpan -Minutes 15)
$externalHeartbeatTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(3) `
  -RepetitionInterval (New-TimeSpan -Minutes 5)
$eventSealTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "02:30"
$artifactAdmissionSealTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "02:40"
$backupTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "03:00"
$rawArchiveAuditTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "04:00"
$gonogoTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "23:00"
$paperCycleTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "23:05"
$paperRiskBundleTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "23:08"
$paperPreviewTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "23:10"
$paperShadowAutoTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "21:20"
# Public-mark paper valuation is a short-lived L2 worker. It refreshes the
# current paper account without creating a resident process or touching a
# private venue/order path.
$paperRealtimeTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(4) `
  -RepetitionInterval (New-TimeSpan -Minutes 5)
# Collect the complete public Binance USD-M surface before paper valuation.
# This task is market-data only and has no credential or broker capability.
$binanceRealtimeMarketTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
  -RepetitionInterval (New-TimeSpan -Minutes 5)
# Binance publishes symbol ADL risk every 30 minutes.  The same low-frequency
# public worker incrementally maintains 30-day basis history, index constituents,
# and insurance-fund snapshots without burdening the five-minute paper loop.
$binanceSystemicRiskTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(8) `
  -RepetitionInterval (New-TimeSpan -Minutes 30)
# The official RPI endpoint is fixed at 1000 levels and IP weight 20 per
# symbol.  Keep the complete response on an independent hourly cadence.
$binanceRpiDepthTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(10) `
  -RepetitionInterval (New-TimeSpan -Hours 1)
$binanceRealtimeFeaturesTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Minutes 5)
$binanceRealtimeDriftAlertsTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
  -RepetitionInterval (New-TimeSpan -Minutes 5)
# Run the immutable 24-trial realtime Alpha budget after the feature worker has
# published a fresh PIT watermark.  Evaluated candidates may enter isolated
# engineering paper immediately; 30d/90d still gate formal graduation only.
$realtimeAlphaDiscoveryTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) `
  -RepetitionInterval (New-TimeSpan -Hours 1)
# Once a candidate clears every immutable discovery gate, freeze forward
# decisions before outcomes on a one-minute cadence.  An empty eligible set is
# the normal SAFE_BLOCKED state and creates no paper position or live authority.
$realtimeAlphaForwardShadowTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(6) `
  -RepetitionInterval (New-TimeSpan -Minutes 1)
# Consume only post-admission Signal-Shadow decisions.  The 30-second offset
# gives the source worker time to commit its hash-chained decision before this
# isolated intent/fill/position/TCA paper worker reads it.
$realtimeAlphaExecutionPaperTrigger = New-ScheduledTaskTrigger -Once `
  -At (Get-Date).AddMinutes(6).AddSeconds(30) `
  -RepetitionInterval (New-TimeSpan -Minutes 1)
$binanceRealtimeStreamTrigger = @(
  (New-ScheduledTaskTrigger -AtStartup),
  # The worker exits cleanly after 23 hours. A daily fixed-time trigger can be
  # swallowed by MultipleInstances=IgnoreNew while the prior instance is
  # still running, leaving a many-hour gap after that instance exits. Polling
  # every five minutes preserves single-instance ownership while bounding
  # both clean-exit and crash recovery to one paper cadence.
  (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5))
)
# Exercise public-book multi-venue routing after the resident Binance stream
# has flushed fresh depth. This remains simulation-only and has no credentials.
$paperMultiVenueSorTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(3) `
  -RepetitionInterval (New-TimeSpan -Minutes 5)
# Keep the isolated 10,000 USD paper profile on the same five-minute cadence,
# offset from the default account so the two short-lived workers do not start
# together.  The worker includes its own semantic health check.
$paperRealtime10kTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(7) `
  -RepetitionInterval (New-TimeSpan -Minutes 5)
$paperShadowRealtimeTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(6) `
  -RepetitionInterval (New-TimeSpan -Minutes 5)
# Retain one semantic observation after the shadow mark cycle.  Maturity is
# reported as the explicit partial exit code; collection continues regardless.
$paperOperationalEvidenceTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(7) `
  -RepetitionInterval (New-TimeSpan -Minutes 5)
# Run the semantic paper health check one minute after the mark worker.  It is
# also short-lived and returns non-zero when the paper artifacts are stale or
# unsafe; it never refreshes data or reaches a broker.
$paperHealthTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) `
  -RepetitionInterval (New-TimeSpan -Minutes 5)

Register-L2Task -Name "QUANTOS_factory_loop" -Module "crypto_factory_loop" `
  -Trigger $factoryTrigger
Register-L2Task -Name "QUANTOS_ic_panel_refresh" -Module "ic_panel_research" `
  -Trigger $icTrigger
Register-L2Task -Name "QUANTOS_paper_alpha_evaluation" -Module "paper_alpha_evaluation" `
  -Trigger $paperAlphaEvaluationTrigger
Register-L2Task -Name "QUANTOS_alpha_production_workflow" -Module "alpha_production_workflow" `
  -Trigger $alphaProductionWorkflowTrigger
Register-L2Task -Name "QUANTOS_candidate_dry_run" -Module "dry_run_candidate_multi" `
  -Trigger $candidateDryRunTrigger -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-L2Task -Name "QUANTOS_candidate_portfolio_evidence" -Module "candidate_portfolio_evidence" `
  -Trigger $candidatePortfolioEvidenceTrigger -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
# The forward worker includes the public-data refresh budget (30 minutes) and
# needs only a small compute margin.  Bound the task so one hung refresh cannot
# monopolize the daily cadence or leave the next run permanently stale.
Register-L2Task -Name "QUANTOS_forward_track" -Module "forward_track_advance" `
  -Trigger $forwardTrigger -ExecutionTimeLimit (New-TimeSpan -Minutes 35)
Register-L2Task -Name "QUANTOS_alpha_maintenance" -Module "alpha_maintenance_sweep" `
  -Trigger $maintenanceTrigger
Register-L2Task -Name "QUANTOS_domain_event_ops" -Module "domain_event_ops" `
  -Trigger $eventOpsTrigger -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-L2Task -Name "QUANTOS_scheduler_watchdog" -Module "l2_scheduler_watchdog" `
  -Trigger $schedulerWatchdogTrigger
Register-L2Task -Name "QUANTOS_gonogo_report" -Module "gonogo_report" `
  -Trigger $gonogoTrigger
Register-L2Task -Name "QUANTOS_alpha_paper_cycle" -Module "alpha_paper_cycle" `
  -Trigger $paperCycleTrigger
Register-L2Task -Name "QUANTOS_paper_research_risk_bundle" -Module "paper_research_risk_bundle" `
  -Trigger $paperRiskBundleTrigger
Register-L2Task -Name "QUANTOS_paper_order_preview" -Module "paper_order_preview" `
  -Trigger $paperPreviewTrigger
Register-L2Task -Name "QUANTOS_binance_realtime_market" -Module "binance_realtime_market" `
  -Trigger $binanceRealtimeMarketTrigger
Register-L2Task -Name "QUANTOS_binance_systemic_risk" -Module "binance_systemic_risk" `
  -Trigger $binanceSystemicRiskTrigger
Register-L2Task -Name "QUANTOS_binance_rpi_depth" -Module "binance_rpi_depth" `
  -Trigger $binanceRpiDepthTrigger
Register-L2Task -Name "QUANTOS_binance_realtime_features" -Module "binance_realtime_features" `
  -Trigger $binanceRealtimeFeaturesTrigger -ExecutionTimeLimit (New-TimeSpan -Minutes 45)
Register-L2Task -Name "QUANTOS_binance_realtime_drift_alerts" -Module "binance_realtime_drift_alerts" `
  -Trigger $binanceRealtimeDriftAlertsTrigger
Register-L2Task -Name "QUANTOS_realtime_alpha_discovery" -Module "realtime_alpha_discovery" `
  -Trigger $realtimeAlphaDiscoveryTrigger -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-L2Task -Name "QUANTOS_realtime_alpha_forward_shadow" -Module "realtime_alpha_forward_shadow" `
  -Trigger $realtimeAlphaForwardShadowTrigger -ExecutionTimeLimit (New-TimeSpan -Seconds 45)
# A full public REST fallback can take longer than one minute; the task
# remains single-instance (IgnoreNew) and fail-closed while it refreshes.
Register-L2Task -Name "QUANTOS_realtime_alpha_execution_paper" -Module "realtime_alpha_execution_paper" `
  -Trigger $realtimeAlphaExecutionPaperTrigger -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-L2Task -Name "QUANTOS_binance_realtime_stream" -Module "binance_realtime_stream" `
  -Trigger $binanceRealtimeStreamTrigger -ExecutionTimeLimit (New-TimeSpan -Hours 23 -Minutes 55)
Register-L2Task -Name "QUANTOS_paper_multivenue_sor" -Module "paper_multivenue_sor" `
  -Trigger $paperMultiVenueSorTrigger
Register-L2Task -Name "QUANTOS_paper_realtime" -Module "paper_realtime" `
  -Trigger $paperRealtimeTrigger
Register-L2Task -Name "QUANTOS_paper_realtime_10k" -Module "paper_realtime_10k" `
  -Trigger $paperRealtime10kTrigger
Register-L2Task -Name "QUANTOS_paper_shadow_auto" -Module "paper_shadow_auto" `
  -Trigger $paperShadowAutoTrigger
Register-L2Task -Name "QUANTOS_paper_shadow_realtime" -Module "paper_shadow_realtime" `
  -Trigger $paperShadowRealtimeTrigger
Register-L2Task -Name "QUANTOS_paper_operational_evidence" -Module "paper_operational_evidence" `
  -Trigger $paperOperationalEvidenceTrigger
Register-L2Task -Name "QUANTOS_paper_health" -Module "paper_health" `
  -Trigger $paperHealthTrigger
# Full raw-object verification is intentionally independent of the paper path
# and may hash the complete immutable archive on a busy host.
Register-L2Task -Name "QUANTOS_raw_archive_audit" -Module "raw_archive_audit" `
  -Trigger $rawArchiveAuditTrigger -ExecutionTimeLimit (New-TimeSpan -Hours 2)

$backupReadiness = Get-L2BackupReadiness
if ($backupReadiness.Ready) {
  Register-L2Task -Name "QUANTOS_backup" -Module "l2_backup" `
    -Trigger $backupTrigger
} else {
  Write-Warning "QUANTOS_backup remains disabled: $($backupReadiness.Reason)"
}

$heartbeatReadiness = Get-ExternalHeartbeatReadiness
if ($heartbeatReadiness.Ready) {
  Register-L2Task -Name "QUANTOS_external_heartbeat" -Module "external_heartbeat" `
    -Trigger $externalHeartbeatTrigger
} else {
  Write-Warning "QUANTOS_external_heartbeat remains disabled: $($heartbeatReadiness.Reason)"
}

$eventSealReadiness = Get-EventSealReadiness
if ($eventSealReadiness.Ready) {
  Register-L2Task -Name "QUANTOS_domain_event_seal" -Module "domain_event_seal" `
    -Trigger $eventSealTrigger
  Register-L2Task -Name "QUANTOS_artifact_admission_seal" `
    -Module "artifact_admission_seal" -Trigger $artifactAdmissionSealTrigger
} else {
  Write-Warning "QUANTOS_domain_event_seal remains disabled: $($eventSealReadiness.Reason)"
  Write-Warning "QUANTOS_artifact_admission_seal remains disabled: $($eventSealReadiness.Reason)"
}

Write-Host ""
Write-Host "L2_DRY_RUN_ONLY schedules registered from $codeRoot using $pythonw"
Write-Host "Mutable operational state root: $stateRoot"
Write-Host "No live submission or broker-capable task was registered."
