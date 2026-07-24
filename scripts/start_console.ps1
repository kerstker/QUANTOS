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
rtk python -m tools.local_console_stack start
