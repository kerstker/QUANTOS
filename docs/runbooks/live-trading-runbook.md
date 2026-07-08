# 实盘路线运维手册(Live Trading Runbook)

适用范围:Stage 0(前向证据钟)+ Stage 1(测试网执行栈)。最后更新 2026-07-05。
设计文档:`docs/superpowers/specs/2026-07-04-live-execution-stack-design.md`、
`docs/superpowers/specs/2026-07-05-institutional-live-stack-design.md`。

**边界(先读这段):** readiness 仍是 L2_DRY_RUN_ONLY。测试网是交易所官方沙盒、零真实资本;
真钱 Go/No-Go 需 ≥8 周前向证据 + Phase 3A 人工评审。mainnet 适配器在 L2 下结构性不可实例化。
本手册所有操作都在这个边界之内。

---

## 1. 周度周期(每周一 09:00,schtasks 调度)

正常顺序(两个驱动器互相独立,先后无硬依赖,但习惯上先纸面后执行):

```bash
# Stage 0:前向纸面决策落库 + 到期兑现(数据刷新包含在内)
python -m tools.forward_track_advance

# Stage 1:测试网执行(默认 preview,绝不真提交)
python -m tools.live_track_advance
# 真提交(需 key/live_mandate.json 已由人签署;没有它照样零提交):
python -m tools.live_track_advance --submit
# 第二交易所:
python -m tools.live_track_advance --venue bybit
```

每轮驱动器内部顺序(`tools/live_track_advance.py::main`):
数据刷新 → 信号(与 Stage 0 同一条 crowd_contra 路径)→ 时钟校正(fail-soft)→
**崩溃恢复**(核对账本非终态订单,回查交易所补记,遗留开单主动撤掉)→ **对账前置**
(账本 vs 交易所仓位,drift 超 1e-6 硬停)→ **风险监控**(回撤/敞口,越限 arm kill-switch +
紧急减仓)→ gate → mandate 分级 → 提交/预览 → 确认(轮询终态,超时撤单回查)→ 记账 + TCA →
**live-paper 对账**快照 → 状态落盘 + 告警。

产物(全部 gitignored,`data/cache/`):
`live_track.db`(账本:订单/成交/TCA)、`live_track_status.json`(本轮状态,console 读它)、
`live_risk_state.json`(峰值权益)、`live_parity.jsonl`(逐周对账,append-only)。
bybit 版本加 `_bybit` 后缀,绝不与 binance 混写。

**验证一轮是否正常:** 看驱动器最后一行输出(`tier=... submitted=... errors=...`),或
console「实盘路线」页,或直接 `data/cache/live_track_status.json`。正常 = `hard_stop` 不存在/
false、`n_submission_errors=0`、`alerts` 无红色项。

## 1.5 LIMIT 限价单(2026-07-06,默认不启用)

设计文档:`docs/superpowers/specs/2026-07-06-limit-order-execution-design.md`。默认路径
(不带 `--order-type`)一字节不变——本节只在显式启用 LIMIT 时适用。

```bash
python -m tools.live_track_advance --venue binance --order-type limit          # preview,默认
python -m tools.live_track_advance --venue binance --order-type limit --submit  # 真实挂单/撤单
```

- **经济定位**:本实现是可成交限价(marketable limit)= 市价单加价格上限,不是做市;
  定价 = 盘口 mid(`venue.book_ticker()`,fail-soft)± 20bps(工程占位,`LimitPricing`
  默认)± band,按方向取整到 tick。盘口不可用/退化(交叉盘/零价/与决策价偏离 >5%)时回退
  决策价锚 + 告警(`LIVE_LIMIT_ANCHOR_FALLBACK`)。
- **挂单不成交是正常路径**:轮询窗口 30 次 × 1s ≈ 30 秒,打满后主动撤单 + 回查权威状态,
  部分成交按交易所报的真实量入账,零成交本轮该标的仓位原样不动(不是异常,不需要人工
  干预)。`data/cache/live_track_status*.json` 里 `limit_fill_stats` 字段(`n_limit`/
  `n_filled`/`n_canceled_zero_fill`/`n_partial`/`fill_rate_qty_weighted`)是判断"band 是否
  设得太紧/太松"的第一手数据——**没有自动改价追单**,连续多周 `fill_rate_qty_weighted` 很低
  说明 book 在悄悄落后 paper book,需要人工重新评估 band/挂单时长。
- **preview 模式完全兼容**:`place_order` 只记录不真提交,`get_order`/`cancel_order` 恒
  `None`(预览从未真提交,查/撤都没有意义)——已确认这个状态在 preview 下被识别为"已经
  问完了",零真实轮询燃烧、零假 `LIVE_ORDER_TIMEOUT_CANCEL` 审计。
- **紧急减仓恒 MARKET**:kill-switch/风险哨兵触发的紧急通道绝不构造 LIMIT(治理测试钉死)
  ——紧急场景下"不成交"才是危险,与 LIMIT 的经济定位刚好相反。
- **真实首次启用前**:先备份 `data/cache/live_track*.db`(账本迁移是加列级安全操作,不
  重写任何存量行,但这是真实交易数据,遵循既有灾备惯例);`band_pct`/挂单时长/锚偏离容忍度
  都是工程占位数字,真实使用前建议按自己对"滑点上限 vs 成交率"的偏好重新评估(见设计文档
  §12 open questions)。

## 2. 故障处置

### 2.1 限流(HTTP 429/418,Bybit retCode 10006)
适配器自带指数退避重试(签名每次重算),耗尽仍限流 → `RateLimitedError` 崩本轮。
**处置:什么都不用做**——下一轮(或手工重跑)会自然恢复;client_order_id 确定性哈希,
重跑绝不会重复下单。频繁出现 → 检查是否有别的进程在打同一个 API key。

### 2.2 时钟漂移(Binance -1021 / Bybit 10002 时间戳错误)
驱动器每轮启动已自动 `sync_clock()`(fail-soft)。仍报错 → Windows 系统时间漂移过大,
`w32tm /resync` 后重跑。

### 2.3 崩溃恢复(上一轮中途被杀)
不需要人工干预:下一轮启动时 `resolve_unconfirmed_orders` 自动回查非终态订单、补记真实成交、
撤掉遗留开单。alerts 里会出现 `crash-recovery: resolved N unconfirmed order(s)`——看到它说明
机制正常工作,不是新故障。

### 2.4 对账 drift 硬停(`RECONCILIATION DRIFT ... HARD STOP`)
账本与交易所仓位对不上,本轮拒绝提交。**这是设计行为,不是 bug。**
处置:人工核对哪边是真相——
- 交易所侧多出仓位(如手工测试单)→ 用一笔对应的 `ledger.record_fill` 补记(参照
  2026-07-04 首次真实 drift 的处理),或在交易所侧平掉;
- 账本多出仓位 → 检查是否有确认失败但实际成交的订单(查 `live_track.db` 的 live_orders
  表非终态行 + 交易所订单历史)。
补记后重跑,`in_sync` 恢复即正常。**绝不放宽 tolerance 来"消除"告警。**

### 2.5 风险越限(kill-switch 触发 + 紧急减仓)
`RISK BREACH` 告警 → 本轮常规批次硬停,kill-switch 已 arm,随后:
- 回撤越限 → 紧急通道全平(reduce-only);
- 敞口越限 → 等比减回限额 90%;
- 纯数据质量问题(缺价/峰值未知)→ **不**自动交易,只停机告警,等人。
处置:确认减仓结果(status json 的 `emergency` 段 + console),分析越限根因。kill-switch 是
闩锁——恢复交易需要人工 reset(`KillSwitch.reset`/`reset_with_grant`,见治理文档);驱动器
每轮新建 KillSwitch 实例,所以下一轮不会被上一轮的闩锁挡住——**越限根因不解决就重跑等于
绕过保护,先解决根因**。

### 2.6 订单超时撤单(`LIVE_ORDER_TIMEOUT_CANCEL` 审计)
轮询打满仍非终态 → 自动撤单 + 回查权威状态记账(部分成交按交易所报的量入账)。偶发正常;
频繁出现 → 检查 `poll_sleep`/`max_confirm_polls` 是否匹配交易所真实撮合延迟。

### 2.7 记账失败(`n_ledger_write_errors > 0`)
订单在交易所侧真实存在但本地没记上(如 sqlite 锁)。**必须人工补记**,否则下一轮对账
必然 drift 硬停(这是兜底,不是灾难)。按审计事件里的 venue_order_id 查交易所侧真实成交量
补 `record_fill`。

## 3. Key 管理与轮换

- 位置:`key/binance_test_key.json`、`key/bybit_test_key.json`(`{"api_key":..., "api_secret":...}`),
  或环境变量 `BINANCE_TESTNET_API_KEY/SECRET`、`BYBIT_TESTNET_API_KEY/SECRET`。`key/` 整目录
  gitignored。
- 轮换:交易所侧生成新 key → 替换文件 → 跑一轮 preview 验证(account/positions 能读)→
  交易所侧删除旧 key。任何工具/日志都只允许出现 key 的名字/长度,绝不允许出现值。
- 泄露怀疑:立刻在交易所侧删 key(测试网 key 无资金权限,但习惯要按真钱标准养)。

## 4. Mandate 签署与更新

1. `python -m tools.create_live_mandate_template` → `key/live_mandate.json.template`;
2. 复制为 `key/live_mandate.json`,**自己**填全部真实限额(模板占位符无法被加载,防止误用);
   `venue`/`exchange` 是结构匹配字段(testnet/binance 或 bybit),其余全部是你的风险决定;
3. 建议设 `expires_at`(epoch 毫秒)——8 周证据窗之后应重新审视限额,而不是无限期有效;
4. 验证:跑一轮 preview,若订单在限额内,status 的 `tier` 会从 `NEEDS_HUMAN_REVIEW` 变 `AUTO`
   (preview 模式依然不真提交——两层独立保护)。
更新限额 = 编辑文件重跑;吊销 = 删文件(fail-closed 回到全量人工审查)。

## 5. preview → --submit 检查清单

全部满足才升级到 `--submit`:
- [ ] 最近 ≥2 轮 preview 全链路干净(无 hard_stop、无 submission/ledger errors);
- [ ] 对账 `in_sync: true`;
- [ ] `key/live_mandate.json` 已签署且限额是你真实想要的数字;
- [ ] console「实盘路线」页与 status json 一致;
- [ ] 明确知道本轮会提交什么(preview 的 `would_submit` 清单);
- [ ] 测试网账户余额/仓位符合预期。
首次 `--submit` 后立刻:查 status json `n_submitted`/`order_stats`、交易所侧订单历史、
下一轮 preview 的对账必须 `in_sync`。

## 6. 监控与告警

- 告警落 `data/cache/alerts.jsonl`(本地台账),可选 Telegram(密钥只走环境变量,见
  `tools/alert_sink.py`);
- console「实盘路线」页 = 只读汇总(证据钟/执行状态/TCA/对账/紧急减仓);
- TCA:名义额加权滑点持续 > 回测假设 10bps → 回测净收益被高估,这个信号必须带进 Go/No-Go;
- parity:`unexpected_live_positions` 非空 = 账外仓位(遗留测试单/对账缺口),按 §2.4 处理;
  `paper_source: recomputed_signal` = 该周期 Stage 0 台账缺失,查 forward_track 是否正常跑。

## 7. 调度

Windows 计划任务(人工注册,错过自动 catch-up):每周一 09:00 依次跑两个驱动器。
查看:`schtasks /query /tn QUANTOS_forward_track`(及 live_track)。驱动器幂等
(决策不可改写 + client_order_id 确定性),手工补跑安全。

## 8. 交易台(LiveDesk,2026-07-05)

前端「交易台」页(`/research/live-desk`)= 类交易所实时界面:双所权益/仓位/uPnL/订单成交
流水/TCA,8s 轮询。后端 `tools/live_ops_api.py`:

```bash
python -m uvicorn tools.live_ops_api:app --host 127.0.0.1 --port 8440
```

操作面治理:页面上的「跑 Preview/真实提交」只会以固定 argv 子进程运行驱动器 CLI——与人工
敲命令行完全同一条 mandate→gate→确认→记账链;「真实提交」需输入 `submit <venue>` 文本
双重确认;单飞锁防并发。**本机 token 认证**(2026-07-05,同机任意进程此前可零门槛触发真实
提交,已修复):首次启动 `live_ops_api` 会在 `key/live_ops_token.txt`(gitignored)生成
一个随机 token;打开交易台页面时,把这个文件的内容粘贴进页面上的输入框——**只存在浏览器
这个标签页的 sessionStorage 里,关闭标签页即清空,绝不会打进前端构建产物**(2026-07-05
安全审查发现:早期版本用 `VITE_OPS_TOKEN` 构建期变量,会被 Vite 编译进 JS bundle 明文,
已改掉)。CORS 仍只放 3100(单机工作站口径,多用户部署是 Phase 14)。Bybit Demo 模拟资金:
页面按钮或 `python -m tools.bybit_demo_funds --amount N`。

## 9. Bybit Demo(2026-07-05)

testnet.bybit.com 水龙头长期失灵;官方主推 Demo Trading(api-demo.bybit.com,V5 协议同
testnet 只换主机)。key 必须在主站切到 Demo Trading 后创建(真实账户 key 在 demo 主机无效,
反之亦然)。驱动器 `--venue bybit_demo`;mandate 文件 `key/live_mandate_bybit_demo.json`
(per-venue,一份 mandate 只授权一个交易所)。

## 10. 持续运营(风险哨兵 + 证据包,2026-07-05)

**风险哨兵**(`python -m tools.live_risk_sentinel [--act]`):两次周度调仓之间的持续风控——
逐 venue 查真实权益/仓位,落权益曲线点(`data/cache/live_equity*.jsonl`,交易台净值曲线的
数据源),用与驱动器同一套 `evaluate_risk_state` 评估(回撤 0.20 + mandate 派生敞口限额)。
默认只告警;`--act` 越限时走独立紧急减仓通道(与驱动器 breach 分支同一策略)。峰值权益与
驱动器共用同一文件(同一回撤口径)。建议每小时调度。

**Go/No-Go 证据包**(`python -m tools.gonogo_report`):聚合前向证据钟/对账历史/TCA/权益
曲线/事故台账 → `data/cache/gonogo_report.json` + `docs/reports/gonogo-<date>.md`。只聚合
不下结论——结论是 Phase 3A 人工评审的事。评审前跑一次即可。

**调度注册**:`scripts/register_schedules.ps1` 一次注册全套(周一双所批 + 每小时哨兵 +
ops API 自启)——**运行它 = 签署常驻自动化授权**,脚本头部列明了每条的含义;单条删除
`schtasks /delete /f /tn QUANTOS_<name>`。
