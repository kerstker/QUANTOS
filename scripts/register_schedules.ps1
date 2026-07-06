# QUANTOS 调度注册(2026-07-05 首版,2026-07-06 补充自动回测/研究挖掘两条)。运行一次即
# 注册全套常驻任务;每条都可单独删除:schtasks /delete /f /tn "QUANTOS_<name>"
#
# 注册的授权含义(运行本脚本 = 你签署以下常驻自动化):
#   1. 每周日 20:00 自动因子挖掘(loop-until-dry 正交边挖掘,MAX_ROUNDS=8 自终止,纯研究——
#      不写 AlphaCatalog/不驱动晋级,auto_approved 恒 0,只落 data/cache/ 快照供人工查看);
#   2. 每周日 20:30 IC 面板诊断刷新(逐候选因子截面 IC + 多持有期,同样纯诊断非门禁);
#   3. 每周一 09:00 前向证据钟推进(纸面,零订单);
#   4. 每周一 09:10/09:20 双所真实提交调仓(--submit;mandate fail-closed 限额内自动,
#      超限自动升级人工,mandate 删除即全停);
#   5. 每小时风险哨兵 --act:越限时走独立 reduce-only 紧急通道自动减仓
#      (结构上只能减仓;纯数据质量问题绝不自动交易);
#   6. 登录时自启交易台 ops API(:8440,仅本机);
#   7. 每小时死人开关:检查以上任务是否按预期节奏运行,静默失败会大声告警;
#   8. 每日灾备:业务数据+mandate 配置备份到 data/backup/(不含 API 密钥,见
#      tools/live_backup.py 模块说明——密钥备份是你自己的安全实践)。
#
# 诚实边界:①②是"自动跑研究/回测",产出落数据快照供人工在前端/console 查看——不等于
# "自动晋级/自动上线新策略"。任何新因子从这里跑出来之后,能不能进 paper-track/干跑/真实
# 执行,仍然要走战线①的晋级闭环(多方批准)或你亲自拍板,代码不会替你自动把新东西接进
# ③④的真实调仓循环。
param()

$py = "H:\miniconda\python.exe"
$wd = "H:\code\QUANTOS"

schtasks /create /f /tn "QUANTOS_factory_loop" `
  /tr "cmd /c cd /d $wd && $py -m tools.crypto_factory_loop" /sc WEEKLY /d SUN /st 20:00
schtasks /create /f /tn "QUANTOS_ic_panel_refresh" `
  /tr "cmd /c cd /d $wd && $py -m tools.ic_panel_research" /sc WEEKLY /d SUN /st 20:30
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
