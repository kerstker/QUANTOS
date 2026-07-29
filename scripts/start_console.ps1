# QUANTOS 本地研究控制台启动入口。
#
# 只启动 L2 研究面：console API、research runner、React 控制台；
# 不启动 live/、promotion API、调度任务或任何券商路径。
# tools.local_console_stack 会记录 PID、日志并做本机健康检查，
# 同时自动避开 Windows 预留的 3100 端口（本机通常回退到 3110）。

# The managed research runner resolves to services.research_runner.main:app;
# its whitelist contains research-only jobs and never broker/order/credential paths.
$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $root
$pythonCommand = Get-Command python -ErrorAction Stop
$pythonw = Join-Path (Split-Path -Parent $pythonCommand.Source) "pythonw.exe"
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
  throw "pythonw.exe is unavailable beside the configured Python runtime"
}

# The manager and all resident children stay outside a visible console. Runtime
# output is written under .runtime/console-stack/logs for IDE inspection.
#
# Avoid the PowerShell process-spawning cmdlet here. Apart from this machine's
# Path/PATH collision, that path can briefly allocate an interactive console
# host before applying its requested window style. ProcessStartInfo carries the
# no-window contract into process creation itself.
$launch = [System.Diagnostics.ProcessStartInfo]::new()
$launch.FileName = $pythonw
$launch.Arguments = "-m tools.local_console_stack start"
$launch.WorkingDirectory = $root
$launch.UseShellExecute = $false
$launch.CreateNoWindow = $true
$launch.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
$manager = [System.Diagnostics.Process]::Start($launch)
if ($null -eq $manager) {
  throw "failed to start the headless QUANTOS console manager"
}
$manager.Dispose()
