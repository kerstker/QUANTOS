# 按端口强制停止 scripts\start_console.ps1 启动的三个研究控制台进程
# (console_api :8400 / research_runner :8420 / 前端 :3100)。不碰 live/ 排程进程。
#
# 用法:powershell -File scripts\stop_console.ps1

foreach ($port in 8400, 8420, 3100) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) {
        Write-Host "端口 $port 上没有监听进程,跳过。" -ForegroundColor DarkGray
        continue
    }
    foreach ($conn in $conns) {
        $procId = $conn.OwningProcess
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "停止端口 $port 上的进程 $($proc.ProcessName) (PID $procId)..." -ForegroundColor Yellow
            Stop-Process -Id $procId -Force -Confirm:$false
        }
    }
}
Write-Host "完成。" -ForegroundColor Green
