# QUANTOS 调度注册(2026-07-05,2026-07-05 补充运维地基两条)。运行一次即注册全套常驻任务;
# 每条都可单独删除:schtasks /delete /f /tn "QUANTOS_<name>"
#
# 注册的授权含义(运行本脚本 = 你签署以下常驻自动化):
#   1. 每周一 09:00 前向证据钟推进(纸面,零订单);
#   2. 每周一 09:10/09:20 双所真实提交调仓(--submit;mandate fail-closed 限额内自动,
#      超限自动升级人工,mandate 删除即全停);
#   3. 每小时风险哨兵 --act:越限时走独立 reduce-only 紧急通道自动减仓
#      (结构上只能减仓;纯数据质量问题绝不自动交易);
#   4. 登录时自启交易台 ops API(:8440,仅本机);
#   5. 每小时死人开关:检查以上任务是否按预期节奏运行,静默失败会大声告警;
#   6. 每日灾备:业务数据+mandate 配置备份到 data/backup/(不含 API 密钥,见
#      tools/live_backup.py 模块说明——密钥备份是你自己的安全实践)。
param()

$py = "H:\miniconda\python.exe"
$wd = "H:\code\QUANTOS"

schtasks /create /f /tn "QUANTOS_forward_track" `
  /tr "cmd /c cd /d $wd && $py -m tools.forward_track_advance" /sc WEEKLY /d MON /st 09:00
schtasks /create /f /tn "QUANTOS_live_track_binance" `
  /tr "cmd /c cd /d $wd && $py -m tools.live_track_advance --submit" /sc WEEKLY /d MON /st 09:10
schtasks /create /f /tn "QUANTOS_live_track_bybit_demo" `
  /tr "cmd /c cd /d $wd && $py -m tools.live_track_advance --venue bybit_demo --submit" /sc WEEKLY /d MON /st 09:20
schtasks /create /f /tn "QUANTOS_risk_sentinel" `
  /tr "cmd /c cd /d $wd && $py -m tools.live_risk_sentinel --act" /sc HOURLY
schtasks /create /f /tn "QUANTOS_ops_api" `
  /tr "cmd /c cd /d $wd && $py -m uvicorn tools.live_ops_api:app --host 127.0.0.1 --port 8440" /sc ONLOGON
schtasks /create /f /tn "QUANTOS_watchdog" `
  /tr "cmd /c cd /d $wd && $py -m tools.watchdog" /sc HOURLY
schtasks /create /f /tn "QUANTOS_backup" `
  /tr "cmd /c cd /d $wd && $py -m tools.live_backup" /sc DAILY /st 03:00

schtasks /query /fo CSV | Select-String "QUANTOS"
Write-Host "`n全部注册完成。删除单条: schtasks /delete /f /tn QUANTOS_<name>"
