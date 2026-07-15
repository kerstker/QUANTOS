# 一键启动 QUANTOS 研究控制台:只读后端 API(console_api :8400)+ 研究运行器
# (research_runner :8420,唯一可变面)+ 前端(:3100)。三个服务各开一个独立
# PowerShell 窗口,方便分别看日志、分别用 Ctrl+C 停。
#
# 不包含的东西(有意,不是漏掉):
#   - live/ 实盘自动化(风控哨兵/死人开关/双所周度驱动器/灾备等 11 个任务)——由
#     schtasks 排程独立管理,见 scripts/register_schedules.ps1;手动重复启动会和
#     排程任务抢占同一份状态文件/账本,不要在这里重复拉起。
#   - promotion_api(:8460)——CLAUDE.md/模块 docstring 明确"默认不启动、不进任何
#     自启/调度",手动需要时自己单独跑(见脚本末尾打印的命令),不放进一键启动。
#
# 用法:powershell -File scripts\start_console.ps1
# 停止:关掉对应窗口,或在窗口内 Ctrl+C;也可以用 scripts\stop_console.ps1 按端口强杀。

$ErrorActionPreference = "Stop"
$root = git rev-parse --show-toplevel
if (-not $root) { $root = (Resolve-Path "$PSScriptRoot\..").Path }
Set-Location $root

function Start-Labeled {
    param([string]$Title, [string]$WorkDir, [string]$Command)
    $inner = "`$host.ui.RawUI.WindowTitle = '$Title'; Set-Location '$WorkDir'; $Command"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $inner | Out-Null
}

Write-Host "启动 console_api(只读研究 API,:8400)..." -ForegroundColor Cyan
Start-Labeled -Title "QUANTOS console_api :8400" -WorkDir $root `
    -Command "python -m uvicorn services.console.main:app --host 127.0.0.1 --port 8400"

Write-Host "启动 research_runner(研究运行器/唯一可变面,:8420)..." -ForegroundColor Cyan
Start-Labeled -Title "QUANTOS research_runner :8420" -WorkDir $root `
    -Command "python -m uvicorn services.research_runner.main:app --host 127.0.0.1 --port 8420"

Start-Sleep -Seconds 2

Write-Host "启动前端研究控制台(:3100,首次编译需要几秒)..." -ForegroundColor Cyan
$frontendDir = Join-Path $root "apps\research-console"
Start-Labeled -Title "QUANTOS frontend :3100" -WorkDir $frontendDir `
    -Command "`$env:VITE_API_URL='http://127.0.0.1:8400'; `$env:VITE_RUNNER_URL='http://127.0.0.1:8420'; `$env:VITE_MOCK_MODE='false'; npm run dev"

Write-Host ""
Write-Host "三个服务已在独立窗口启动:" -ForegroundColor Green
Write-Host "  console_api      http://127.0.0.1:8400/docs"
Write-Host "  research_runner  http://127.0.0.1:8420/docs"
Write-Host "  前端             http://127.0.0.1:3100  (稍等 Vite 编译完再打开)"
Write-Host ""
Write-Host "停止:关闭对应窗口 / 窗口内 Ctrl+C,或运行 scripts\stop_console.ps1" -ForegroundColor Yellow
Write-Host ""
Write-Host "未包含(有意):" -ForegroundColor DarkGray
Write-Host "  live/ 实盘自动化 —— 由 schtasks 排程管理,不要在此重复手动拉起" -ForegroundColor DarkGray
Write-Host "  promotion_api(:8460)—— 默认不自启,需要时手动:" -ForegroundColor DarkGray
Write-Host "    python -m uvicorn services.promotion_api.main:app --host 127.0.0.1 --port 8460" -ForegroundColor DarkGray
