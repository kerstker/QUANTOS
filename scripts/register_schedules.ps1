# QUANTOS L2_DRY_RUN_ONLY schedule registration.
#
# Running this script authorizes only research, paper evidence, maintenance,
# domain-event operations, and read-only reporting jobs. It never registers a
# venue submission, emergency trading action, credential-bearing API, or live
# monitoring process. Legacy tasks with those capabilities are disabled (not
# deleted) before the L2 jobs are registered.
#
# The script location is the immutable release code root. -StateRoot may point
# at a separate mutable operational checkout containing data/cache; scheduled
# imports still resolve only through tools/l2_schedule_entrypoint.py in this
# release root.
param([string]$StateRoot)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$codeRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($StateRoot)) {
  $stateRoot = $codeRoot
} else {
  $stateRoot = (Resolve-Path -LiteralPath $StateRoot).Path
}
$launcher = Join-Path $codeRoot "tools\l2_schedule_entrypoint.py"
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
  throw "L2 schedule entrypoint is unavailable"
}
$pythonCommand = Get-Command python -ErrorAction Stop
$py = $pythonCommand.Source

function Disable-TaskIfPresent {
  param([Parameter(Mandatory = $true)][string]$Name)

  $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
  if ($null -eq $task) {
    Write-Host "${Name}: not present"
    return
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

  $logRoot = Join-Path $stateRoot ".runtime\logs"
  $action = New-ScheduledTaskAction -Execute $py `
    -Argument "`"$launcher`" $Module" -WorkingDirectory $stateRoot
  $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -StartWhenAvailable -ExecutionTimeLimit $ExecutionTimeLimit
  Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger `
    -Settings $settings -Description "QUANTOS L2_DRY_RUN_ONLY: tools.$Module" `
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
# Poll daily after the expected public-data close so a newly available
# watermark is consumed promptly; the worker's internal seven-day due check
# remains authoritative.
$forwardTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "21:00"
$maintenanceTrigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 `
  -DaysOfWeek Monday -At "09:05"
$eventOpsTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Hours 1)
$schedulerWatchdogTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
  -RepetitionInterval (New-TimeSpan -Minutes 15)
$externalHeartbeatTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(3) `
  -RepetitionInterval (New-TimeSpan -Minutes 5)
$eventSealTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "02:30"
$artifactAdmissionSealTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "02:40"
$backupTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "03:00"
$gonogoTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "23:00"
$paperCycleTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "23:05"
$paperRiskBundleTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "23:08"
$paperPreviewTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "23:10"
# Public-mark paper valuation is a short-lived L2 worker. It refreshes the
# current paper account without creating a resident process or touching a
# private venue/order path.
$paperRealtimeTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(4) `
  -RepetitionInterval (New-TimeSpan -Minutes 5)
# Keep the isolated 10,000 USD paper profile on the same five-minute cadence,
# offset from the default account so the two short-lived workers do not start
# together.  The worker includes its own semantic health check.
$paperRealtime10kTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(7) `
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
Register-L2Task -Name "QUANTOS_forward_track" -Module "forward_track_advance" `
  -Trigger $forwardTrigger
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
Register-L2Task -Name "QUANTOS_paper_realtime" -Module "paper_realtime" `
  -Trigger $paperRealtimeTrigger
Register-L2Task -Name "QUANTOS_paper_realtime_10k" -Module "paper_realtime_10k" `
  -Trigger $paperRealtime10kTrigger
Register-L2Task -Name "QUANTOS_paper_health" -Module "paper_health" `
  -Trigger $paperHealthTrigger

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
Write-Host "L2_DRY_RUN_ONLY schedules registered from $codeRoot using $py"
Write-Host "Mutable operational state root: $stateRoot"
Write-Host "No live submission or broker-capable task was registered."
