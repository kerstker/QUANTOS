# QUANTOS 调度注册(2026-07-05 首版,2026-07-06 补充自动回测/研究挖掘两条)。运行一次即
# 注册全套常驻任务;每条都可单独删除:schtasks /delete /f /tn "QUANTOS_<name>"
#
# 注册的授权含义(运行本脚本 = 你签署以下常驻自动化):
#   1. 每周日 20:00 自动因子挖掘(loop-until-dry 正交边挖掘,MAX_ROUNDS=8 自终止,纯研究——
#      不写 AlphaCatalog/不驱动晋级,auto_approved 恒 0,只落 data/cache/ 快照供人工查看);
#   2. 每周日 20:30 IC 面板诊断刷新(逐候选因子截面 IC + 多持有期,同样纯诊断非门禁);
#   3. 每周一 09:00 前向证据钟推进(纸面,零订单);
#   3b. 每周一 09:05 alpha 维护 sweep(Phase 5 item 1):经审计 manager(非人)把衰退 alpha 降级/
#      退役,落只读快照 data/cache/alpha_maintenance_status.json;live 驱动器读到本策略 RETIRED/PAUSED
#      即**停止新增敞口**(现有仓位原样不动,绝不自动减仓——减仓目标数字 + full-flatten 需人签)。
#      纯研究状态 sweep + 停止加仓联动,不驱动任何自动晋级、不碰 order 路径。排在 09:00 证据推进之后、
#      09:10 真实调仓之前,故当轮降级能挡下同一早上的加仓。无 backtest 参照/无证据时 fail-closed 空跑。
#   4. 每周一 09:10/09:20 双所真实提交调仓(--submit;mandate fail-closed 限额内自动,
#      超限自动升级人工,mandate 删除即全停);
#   5. 每小时风险哨兵 --act:越限时走独立 reduce-only 紧急通道自动减仓
#      (结构上只能减仓;纯数据质量问题绝不自动交易);
#   6. 登录时自启交易台 ops API(:8440,仅本机);
#   7. 每小时死人开关:检查以上任务是否按预期节奏运行,静默失败会大声告警;
#   8. 每日灾备:业务数据+mandate 配置备份到 data/backup/(不含 API 密钥,见
#      tools/live_backup.py 模块说明——密钥备份是你自己的安全实践)。tools/live_backup.py
#      2026-07-08 起 fail-closed:没配 QUANTOS_BACKUP_PASSPHRASE 就拒绝执行(不再静默明文)。
#      本脚本注册这条任务前,若当前 shell 已设置该变量,会用 setx 持久化进当前用户的环境块,
#      让不继承交互式 shell 的计划任务子进程也能看到它;若未设置,本脚本会打印醒目警告并
#      跳过注册这一条任务,不会留一条此后每天静默失败的计划任务。另见 tools/watchdog.py
#      新增的 backup 新鲜度检查(死人开关兜底,防止这条任务日后又静默失效而无人发现)。
#   9. 每日 23:00 盘后 GoNoGo 证据报告(Phase 5 item 3):聚合前向证据 + 逐 venue PnL/成本归因 +
#      事故台账,落 data/cache/gonogo_report.json + docs/reports/gonogo-<date>.md。**纯只读报告**——
#      不下 Go/No-Go 结论(结论是 Phase 3A 人工评审)、不驱动任何自动化、不碰 order 路径。console
#      /api/v1/console/gonogo 只读该快照展示。
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
schtasks /create /f /tn "QUANTOS_alpha_maintenance" `
  /tr "cmd /c cd /d $wd && $py -m tools.alpha_maintenance_sweep" /sc WEEKLY /d MON /st 09:05
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
# 2026-07-08 code review 修复:tools/live_backup.py 的 fail-closed 密码闸门("没配
# QUANTOS_BACKUP_PASSPHRASE 就拒绝执行,不静默退化成明文")上线后,若这里继续像其余任务一样
# 直接注册、不传密码,QUANTOS_backup 会从此每天在触碰文件系统前以 SystemExit 拒绝执行——静默、
# 无监控、无日志。这里改为:有密码就 setx 持久化 + 正常注册(并把 stdout/stderr 落日志,此前
# 完全没有);没配置就大声警告 + 不注册,不留一条注定天天失败的计划任务给未来的自己发现。
$backupPassphrase = $env:QUANTOS_BACKUP_PASSPHRASE
if ($backupPassphrase) {
  # setx 把当前用户的环境变量持久化进注册表——之后任何以同一用户身份启动的新进程(含 Task
  # Scheduler 为计划任务重新构建的进程环境)都能看到它,不依赖这个交互式 shell 存活。
  setx QUANTOS_BACKUP_PASSPHRASE $backupPassphrase | Out-Null
  schtasks /create /f /tn "QUANTOS_backup" `
    /tr "cmd /c cd /d $wd && $py -m tools.live_backup >> data\cache\quantos_backup_task.log 2>&1" `
    /sc DAILY /st 03:00
  Write-Host "QUANTOS_backup: passphrase persisted (setx) + task registered (encrypted daily backup)."
} else {
  Write-Warning "QUANTOS_BACKUP_PASSPHRASE not set in this shell -- QUANTOS_backup task was NOT registered."
  Write-Warning "  tools/live_backup.py now refuses to run without it (fail-closed, no plaintext fallback)."
  Write-Warning '  Fix: $env:QUANTOS_BACKUP_PASSPHRASE = "<passphrase>"; then re-run this script.'
  Write-Warning "  (or knowingly accept unencrypted backups by adding --allow-plaintext to this block)."
}
schtasks /create /f /tn "QUANTOS_gonogo_report" `
  /tr "cmd /c cd /d $wd && $py -m tools.gonogo_report" /sc DAILY /st 23:00

schtasks /query /fo CSV | Select-String "QUANTOS"
Write-Host "`n全部注册完成。删除单条: schtasks /delete /f /tn QUANTOS_<name>"
