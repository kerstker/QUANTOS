# QUANTOS L2_DRY_RUN_ONLY schedule registration.
#
# Running this script authorizes only the static MVP closed set: public raw/PIT
# collection, resident public streams, observation health, and control-plane
# workers. It never registers research/candidate/paper authority, venue
# submission, emergency trading action, or a credential-bearing API. Tasks
# outside the MVP are disabled (not deleted) before safe jobs are registered.
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

function Get-L2OwnedProcesses {
  param([Parameter(Mandatory = $true)][string]$Module)

  if ([string]::IsNullOrWhiteSpace($Module)) {
    return @()
  }
  $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
  $workerPattern = "*l2_schedule_entrypoint.py* ${Module} --release-commit *"
  $owned = @{}
  foreach ($process in $processes) {
    if (
      $process.Name -in @("python.exe", "pythonw.exe") -and
      -not [string]::IsNullOrWhiteSpace($process.CommandLine) -and
      $process.CommandLine -like $workerPattern
    ) {
      $owned[[int]$process.ProcessId] = $process
    }
  }

  # Capture the complete process tree while the approved release launcher is
  # still present.  Task Scheduler can stop its WindowsApps shim before the
  # real interpreter and its subprocesses have quiesced.
  do {
    $added = $false
    $parentIds = @($owned.Keys)
    foreach ($process in $processes) {
      $processId = [int]$process.ProcessId
      if (
        -not $owned.ContainsKey($processId) -and
        $parentIds -contains [int]$process.ParentProcessId
      ) {
        $owned[$processId] = $process
        $added = $true
      }
    }
  } while ($added)

  if ($Module -eq "binance_realtime_stream") {
    # The initial cursor authority audit deliberately runs in an isolated
    # subprocess.  Once its launcher has exited it is no longer discoverable
    # through ParentProcessId, so bind the only allowed orphan signature to the
    # exact public cursor store under the operator-selected state root.
    $cursorStore = Join-Path $stateRoot "data\cache\binance_realtime_stream.db"
    $cursorVerifierPattern = "*-m tools.binance_realtime_stream --verify-cursor-chain --store $cursorStore --cursor-checkpoint-seq * --cursor-checkpoint-row-hash *"
    $quotedCursorVerifierPattern = "*-m tools.binance_realtime_stream --verify-cursor-chain --store `"$cursorStore`" --cursor-checkpoint-seq * --cursor-checkpoint-row-hash *"
    foreach ($process in $processes) {
      if (
        $process.Name -in @("python.exe", "pythonw.exe") -and
        -not [string]::IsNullOrWhiteSpace($process.CommandLine) -and
        (
          $process.CommandLine -like $cursorVerifierPattern -or
          $process.CommandLine -like $quotedCursorVerifierPattern
        )
      ) {
        $owned[[int]$process.ProcessId] = $process
      }
    }
  }
  return @($owned.Values)
}

function Disable-TaskIfPresent {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [string]$Module
  )

  $ownedBeforeStop = @()
  if (-not [string]::IsNullOrWhiteSpace($Module)) {
    $ownedBeforeStop = @(Get-L2OwnedProcesses -Module $Module)
  }
  $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
  $taskWasRunning = $false
  if ($null -eq $task) {
    Write-Host "${Name}: not present"
  } else {
    $taskWasRunning = $task.State -eq "Running"
    # Disable first so a trigger cannot create a new process between the
    # quiescence scan and force replacement.
    Disable-ScheduledTask -TaskName $Name -ErrorAction Stop | Out-Null
    Write-Host "${Name}: disabled before L2 registration"
  }
  if ($taskWasRunning) {
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
    # Merge descendants captured before Stop-ScheduledTask with a fresh scan.
    # The identity includes CreationDate so a rapidly reused PID is never
    # mistaken for the worker that registration observed.
    $workersByIdentity = @{}
    $ownedAfterStop = @()
    if ($taskWasRunning -or $ownedBeforeStop.Count -ne 0) {
      $ownedAfterStop = @(Get-L2OwnedProcesses -Module $Module)
    }
    foreach ($worker in @($ownedBeforeStop) + @($ownedAfterStop)) {
      $identity = "$($worker.ProcessId)|$($worker.CreationDate.ToUniversalTime().Ticks)"
      $workersByIdentity[$identity] = $worker
    }
    $workers = @($workersByIdentity.Values)
    $workerDeadline = (Get-Date).AddSeconds(30)
    do {
      $remainingByIdentity = @{}
      foreach ($worker in $workers) {
        $current = @(
          Get-CimInstance Win32_Process `
            -Filter "ProcessId = $($worker.ProcessId)" `
            -ErrorAction SilentlyContinue
        )
        if (
          $current.Count -ne 0 -and
          $current[0].CreationDate -eq $worker.CreationDate
        ) {
          $identity = "$($worker.ProcessId)|$($worker.CreationDate.ToUniversalTime().Ticks)"
          $remainingByIdentity[$identity] = $current[0]
        }
      }
      foreach ($worker in @(Get-L2OwnedProcesses -Module $Module)) {
        $identity = "$($worker.ProcessId)|$($worker.CreationDate.ToUniversalTime().Ticks)"
        $remainingByIdentity[$identity] = $worker
        $workersByIdentity[$identity] = $worker
      }
      $workers = @($workersByIdentity.Values)
      $remainingWorkers = @($remainingByIdentity.Values)
      if ($remainingWorkers.Count -eq 0) {
        break
      }
      foreach ($worker in $remainingWorkers) {
        Stop-Process -Id $worker.ProcessId -Force -ErrorAction SilentlyContinue
      }
      Start-Sleep -Milliseconds 200
    } while ((Get-Date) -lt $workerDeadline)
    if ($remainingWorkers.Count -ne 0) {
      throw (
        "${Name}: owned L2 subprocess did not stop before release registration; " +
        "orphaned L2 worker did not stop before release registration"
      )
    }
    if ($workers.Count -ne 0) {
      Write-Host "${Name}: stopped $($workers.Count) owned L2 worker process(es)"
    }
  }
  if ($null -eq $task) {
    return
  }
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
  "QUANTOS_watchdog"
)
foreach ($taskName in $legacyLiveTasks) {
  Disable-TaskIfPresent -Name $taskName
}

# Conditional control-plane workers are always quiesced with their exact module
# identity before readiness is evaluated.  This also terminates detached owned
# workers when missing backup/heartbeat/seal configuration prevents a new
# Register-L2Task call from performing its normal module-specific replacement.
$conditionalMvpTasks = @(
  [pscustomobject]@{ Name = "QUANTOS_backup"; Module = "l2_backup" },
  [pscustomobject]@{ Name = "QUANTOS_external_heartbeat"; Module = "external_heartbeat" },
  [pscustomobject]@{ Name = "QUANTOS_domain_event_seal"; Module = "domain_event_seal" },
  [pscustomobject]@{ Name = "QUANTOS_artifact_admission_seal"; Module = "artifact_admission_seal" }
)
foreach ($task in $conditionalMvpTasks) {
  Disable-TaskIfPresent -Name $task.Name -Module $task.Module
}

# These historical workers can launder invalid V1 returns/evaluations into
# durable registry, risk-READY, or workflow authority.  Quiesce existing task
# definitions (including owned subprocesses) and deliberately do not register
# replacements until a signed V2 backtest authority exists.
$invalidatedV1AuthorityTasks = @(
  [pscustomobject]@{ Name = "QUANTOS_candidate_dry_run"; Module = "dry_run_candidate_multi" },
  [pscustomobject]@{ Name = "QUANTOS_candidate_portfolio_evidence"; Module = "candidate_portfolio_evidence" },
  [pscustomobject]@{ Name = "QUANTOS_factory_loop"; Module = "crypto_factory_loop" },
  [pscustomobject]@{ Name = "QUANTOS_ic_panel_refresh"; Module = "ic_panel_research" },
  [pscustomobject]@{ Name = "QUANTOS_alpha_lifecycle_cockpit"; Module = "alpha_lifecycle_cockpit" },
  [pscustomobject]@{ Name = "QUANTOS_alpha_maintenance"; Module = "alpha_maintenance_sweep" },
  [pscustomobject]@{ Name = "QUANTOS_alpha_paper_cycle"; Module = "alpha_paper_cycle" },
  [pscustomobject]@{ Name = "QUANTOS_paper_alpha_evaluation"; Module = "paper_alpha_evaluation" },
  [pscustomobject]@{ Name = "QUANTOS_alpha_production_workflow"; Module = "alpha_production_workflow" },
  [pscustomobject]@{ Name = "QUANTOS_paper_realtime"; Module = "paper_realtime" },
  [pscustomobject]@{ Name = "QUANTOS_paper_realtime_10k"; Module = "paper_realtime_10k" },
  [pscustomobject]@{ Name = "QUANTOS_paper_research_risk_bundle"; Module = "paper_research_risk_bundle" },
  [pscustomobject]@{ Name = "QUANTOS_register_alpha_catalog"; Module = "register_alpha_catalog" }
)
foreach ($task in $invalidatedV1AuthorityTasks) {
  Disable-TaskIfPresent -Name $task.Name -Module $task.Module
}

# These workers are not legacy-invalidated, but they carry research, candidate,
# forward, or paper authority that is intentionally outside the minimal safe
# runtime. Explicitly quiesce them so an earlier profile or a manual schedule
# cannot remain active across a release switch.
$mvpDisabledTasks = @(
  [pscustomobject]@{ Name = "QUANTOS_forward_track"; Module = "forward_track_advance" },
  [pscustomobject]@{ Name = "QUANTOS_gonogo_report"; Module = "gonogo_report" },
  [pscustomobject]@{ Name = "QUANTOS_hourly_alpha_advance"; Module = "hourly_alpha_advance" },
  [pscustomobject]@{ Name = "QUANTOS_mft_l2_runtime"; Module = "mft_l2_runtime" },
  [pscustomobject]@{ Name = "QUANTOS_paper_health"; Module = "paper_health" },
  [pscustomobject]@{ Name = "QUANTOS_paper_multivenue_sor"; Module = "paper_multivenue_sor" },
  [pscustomobject]@{ Name = "QUANTOS_paper_operational_evidence"; Module = "paper_operational_evidence" },
  [pscustomobject]@{ Name = "QUANTOS_paper_order_preview"; Module = "paper_order_preview" },
  [pscustomobject]@{ Name = "QUANTOS_paper_shadow_auto"; Module = "paper_shadow_auto" },
  [pscustomobject]@{ Name = "QUANTOS_paper_shadow_realtime"; Module = "paper_shadow_realtime" },
  [pscustomobject]@{ Name = "QUANTOS_realtime_alpha_discovery"; Module = "realtime_alpha_discovery" },
  [pscustomobject]@{ Name = "QUANTOS_realtime_alpha_execution_paper"; Module = "realtime_alpha_execution_paper" },
  [pscustomobject]@{ Name = "QUANTOS_realtime_alpha_formal_shadow"; Module = "realtime_alpha_formal_shadow" },
  [pscustomobject]@{ Name = "QUANTOS_realtime_alpha_forward_shadow"; Module = "realtime_alpha_forward_shadow" },
  [pscustomobject]@{ Name = "QUANTOS_rpi_low_frequency_research"; Module = "rpi_low_frequency_research" }
)
foreach ($task in $mvpDisabledTasks) {
  Disable-TaskIfPresent -Name $task.Name -Module $task.Module
}

# DefiLlama exposes no historical vintages.  Capture one new public, keyless
# completed-day TVL cross-section every morning so a real PIT history begins
# accumulating without credentials or any venue/order capability.
$defillamaTvlForwardRefreshTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "09:30"
# Stablecoin history is likewise revisable.  Persist one new global supply
# vintage each morning after the chain-TVL snapshot; public/keyless and L2-only.
$defillamaStablecoinForwardRefreshTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "09:40"
# Wikimedia history has no QUANTOS-captured vintages. Persist one canonical
# user-pageview cross-section daily; retrospective history stays diagnostic.
$wikimediaAttentionForwardRefreshTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "09:45"
# Third-venue public funding/premium history is immutable and can be merged
# incrementally.  Refresh daily without wallet/account/order authority.
$hyperliquidPublicForwardRefreshTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "09:50"
$okxBorrowForwardRefreshTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "09:55"
# Binance Data Vision publishes checksum sidecars for completed UTC-day
# bookDepth archives.  Capture one immutable, public, keyless day each morning;
# retrospective rows keep fetch-time knowledge and never receive backdated
# forward credit.
$binanceBookDepthForwardRefreshTrigger = New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At "10:00"
# CFTC TFF positions are weekly.  Tuesday morning is deliberately one full
# week after the report date used by the research knowledge-time policy.
$cftcTffForwardRefreshTrigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 `
  -DaysOfWeek Tuesday -At "10:10"
$crossVenueLiquidationHealthTrigger = New-ScheduledTaskTrigger -Once `
  -At (Get-Date).AddMinutes(17) `
  -RepetitionInterval (New-TimeSpan -Minutes 30)
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
Register-L2Task -Name "QUANTOS_defillama_tvl_forward_refresh" -Module "defillama_tvl_forward_refresh" `
  -Trigger $defillamaTvlForwardRefreshTrigger -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-L2Task -Name "QUANTOS_defillama_stablecoin_forward_refresh" -Module "defillama_stablecoin_forward_refresh" `
  -Trigger $defillamaStablecoinForwardRefreshTrigger -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-L2Task -Name "QUANTOS_wikimedia_attention_forward_refresh" -Module "wikimedia_attention_forward_refresh" `
  -Trigger $wikimediaAttentionForwardRefreshTrigger -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-L2Task -Name "QUANTOS_hyperliquid_public_forward_refresh" -Module "hyperliquid_public_forward_refresh" `
  -Trigger $hyperliquidPublicForwardRefreshTrigger -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

Register-L2Task -Name "QUANTOS_okx_borrow_forward_refresh" -Module "okx_borrow_forward_refresh" `
  -Trigger $okxBorrowForwardRefreshTrigger -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-L2Task -Name "QUANTOS_binance_book_depth_forward_refresh" -Module "binance_book_depth_forward_refresh" `
  -Trigger $binanceBookDepthForwardRefreshTrigger -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-L2Task -Name "QUANTOS_cftc_tff_forward_refresh" -Module "cftc_tff_forward_refresh" `
  -Trigger $cftcTffForwardRefreshTrigger -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-L2Task -Name "QUANTOS_cross_venue_liquidation_health" `
  -Module "cross_venue_liquidation_health" `
  -Trigger $crossVenueLiquidationHealthTrigger -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-L2Task -Name "QUANTOS_domain_event_ops" -Module "domain_event_ops" `
  -Trigger $eventOpsTrigger -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-L2Task -Name "QUANTOS_scheduler_watchdog" -Module "l2_scheduler_watchdog" `
  -Trigger $schedulerWatchdogTrigger
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
Register-L2Task -Name "QUANTOS_binance_realtime_stream" -Module "binance_realtime_stream" `
  -Trigger $binanceRealtimeStreamTrigger -ExecutionTimeLimit (New-TimeSpan -Hours 23 -Minutes 55)
Register-L2Task -Name "QUANTOS_bybit_realtime_stream" -Module "bybit_realtime_stream" `
  -Trigger $binanceRealtimeStreamTrigger -ExecutionTimeLimit (New-TimeSpan -Hours 23 -Minutes 55)
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
