# 停止由 tools.local_console_stack 启动的 QUANTOS 研究控制台进程。
# 只按 manifest 回收本次启动的 PID，不误杀其它程序或 live/调度进程。

$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $root
rtk python -m tools.local_console_stack stop
