# Phase 5 —— Portfolio 生命周期 + Post-Trade 对账 + 退休调度(诚实裁定 + 落地)

> 2026-07-07 · ultracode 4-agent 并行调查(均亲自 Read/亲跑)+ 主审 TDD 落地 · IMPLEMENTATION_ROADMAP.md Phase 5
> 核心:Phase 5 三项的机制半区大多**已建但缺调度/接线**;能本轮干净做完的是把死代码接成承重驱动器 +
> 加 fail-closed 监控机制 + 把已有报告补测试/补 PnL 归因/上架 console。所有自主动作严格限定"只减不加 /
> 只加告警 / 只读报告",涉及减仓目标数字 / 异常阈值的激活留用户签。

## 三项裁定总览

| 子项 | 裁定 | 本轮做了 | 留用户签/后续 |
|---|---|---|---|
| 1 维护/退休自动调度(P1-7) | MIXED | 死代码 `run_maintenance` 接成周度驱动器 + live"停止加仓"联动 | 降级→**减仓目标数字**、RETIRED→全平人工确认 |
| 2 实时盘中监控(dim10) | MIXED | 权益异常检测器(默认惰性)+ margin 加法门 + 哨兵 alert-only 接线 | 跳变%/margin 地板**阈值**;近实时(<小时)= cadence |
| 3 自动日终报告 + PnL/成本归因(dim10) | MIXED | 纯 assemble 重构 + 补测试 + PnL/成本归因块 + console 端点/页 + 日调度 | —(全只读,无风险数字) |

## 子项1 · 维护/退休自动调度(P1-7)—— 机制承重接线 ✅,减仓留签

**问题**:`services/alpha/maintenance.run_maintenance`(经审计 manager 把衰退 alpha 降级/退役,非人 actor、
只到 DEGRADED/PAUSED/RETIRED、绝不 APPROVED)**已建已测但零调用方**(自己的 docstring 承认是"等 Phase-14
调度器周期调的 HOOK")。`services/research/paper_trading/health.paper_health_by_alpha` 已把 paper_track.db
翻成它要的 `{backtest, live}` 形状。缺的三块:调度驱动器、live-vs-backtest 指标算入、退休联动 live 仓位。

**已落地(纯加法,fail-closed,门禁全绿)**:
- `services/alpha/live_linkage.py`(纯函数):`strategy_status_from_report` 把 report 翻成每策略
  `{state, reasons}`;`maintenance_skip_reason` **镜像 `firm_gate_skip_reason`**——本策略 RETIRED/PAUSED
  → 返回"停止新增"skip 串,DEGRADED/OK/缺策略/坏输入 → None(保守加法,绝不伪造 block)。**DEGRADED 刻意
  只观察不停**:自动停掉一个仍合规的策略是过度反应,留给人读告警。
- `tools/alpha_maintenance_sweep.py`(驱动器):从真实 paper_track.db 经 `paper_health_by_alpha` 算证据,
  以**非人 actor** 跑审计过的 sweep,落只读 `data/cache/alpha_maintenance_status.json`。**fail-closed**:
  无 backtest 参照(`data/cache/alpha_backtest_refs.json` 缺,agent 不代填研究参照数字)/ 无 paper track
  → 零动作、快照与"没跑"字节等价。播一次性 catalog 到 PAPER_TRADING(非特权态)纯为跑降级逻辑,持久
  产物是快照非 catalog 态,绝不播 APPROVED(containment)。
- `tools/live_track_advance.py`:加 `maintenance_skip()` 读快照,接在 firm_skip 旁——降级即**停止新增敞口**
  (现有仓位原样不动);缺文件(未调度 sweep)→ None → 旧行为字节等价。preview 只告警,--submit 才跳批。
- `scripts/register_schedules.ps1`:一条周一 09:05 schtasks(证据推进之后、真实调仓之前)。用户 re-run 脚本 = 签署。

**诚实边界 / 留用户签(spec §9)**:自动**减仓**已有 live 仓位(降级时把敞口缩回去)需 de-risk **目标数字**;
RETIRED→全平是 full-flatten 必须人工确认。本轮只做 fail-safe-方向的"停止加仓"(结构上只可能减少能力、绝不
自动下减仓单)。`DegradationThresholds` 用既有保守默认(mechanism 自带);若要它 gate live de-risk,激活
数字需用户签。**35 tests**(8 linkage + 7 sweep + 3 driver wiring + 治理钉死无 order 路径)。

## 子项2 · 实时盘中监控(dim10)—— 异常检测机制 ✅,阈值/近实时留

**已有**:`tools/live_risk_sentinel.py` 是**小时级**心跳(schtasks HOURLY),已读真实权益/仓位、**持久化权益
曲线** `live_equity{suffix}.jsonl`、用同一套 `evaluate_risk_state`(回撤/gross/net)评估、越限 arm kill-switch
+ `--act` 走独立 reduce-only 紧急通道。**真缺**:① `evaluate_risk_state` 无 margin 比率检查;② **零异常检测**
(只有静态阈值越限,抓不到仍在绝对限内的权益尖峰/崩塌)——而验收正是"抓到注入的异常"。

**已落地(纯加法,默认惰性 → 字节等价)**:
- `live/execution/anomaly.py`(纯函数):`detect_equity_anomaly(prior, current, max_jump_pct=None)`。
  **默认 None = 惰性**(生产字节等价,直到用户签一个跳变阈值);开启后两轮 |权益跳变| 超阈值 → 告警;
  fail-closed:缺/坏基线 → `insufficient_history`,current 非有限 → `invalid_current_equity`。
- `live/execution/risk_monitor.evaluate_risk_state`:加可选 `min_margin_ratio/maint_margin_balance/
  margin_maint_amount`(全默认 None → 旧行为逐位相同);开启后比率 < 地板 → 加 reason,数据不全 → fail-closed。
- `tools/live_risk_sentinel.py`:`_last_equity_point` 读上一轮基线,`detect_equity_anomaly` 接成
  **alert-only 分支**(独立于 breach 路径)——**绝不** arm kill-switch / 减仓 / 碰 order 路径(异常常良性
  但值得人看;自动 halt 一个统计毛刺是新的不受控动作)。`_EQUITY_JUMP_ALERT_PCT` 默认 None(关闭)。

**诚实边界 / 留**:跳变%、margin 地板都是**用户签的风险数字**——建机制 + fail-closed 默认,激活留签。
"近实时/盘中(<小时)"= 无状态周度 REST 模型上的 cadence 变更,推迟(小时是安全上限)。**16 tests**
(7 anomaly + 5 margin + 4 sentinel 基线)。

## 子项3 · 自动日终报告 + PnL/成本归因(dim10)—— 全只读,最干净

**已有**:`tools/gonogo_report.py`(200 行)已聚合前向证据 + 逐 venue parity/TCA/权益回撤 + 事故台账,产
JSON + 人读 md。**真缺**:① 零测试;② 未调度(只手动跑);③ 未上 console;④ PnL 归因薄(只有权益首末+回撤,
无 PnL/成本分解)。全属只读报告,无自动动作、无风险数字。

**已落地(全只读加法)**:
- `assemble_report`(纯核,IO 抽到 build_report 边界)+ `assemble_pnl_attribution(equity_points, tca)`
  (纯):从**真实持久化**权益曲线(每点带哨兵 spread 进的 `upnl_total/gross_notional/net_notional`)+ 完整
  TCA 算 —— 权益变动 / 最新未实现(venue 报的 uPnL)/ gross-net / 执行成本估计(TCA 加权滑点×名义额)。
  **诚实边界写进 payload note**:total_equity_change 是权益总账(含已实现+未实现+资金费);**不造** realized/
  unrealized 的假 fill-match waterfall(需成本基,刻意不引入)。既有顶层键字节等价,PnL 块附各 venue(加法)。
- **7 tests**(此前零测试的装配器现被覆盖)。
- `services/research/console_api` GET `/api/v1/console/gonogo`:读快照 fail-closed 到 SYNTHETIC,**单向边界**
  (绝不 import live/tools,只读 JSON),契约测试钉死键 + provenance。
- `apps/research-console` GoNoGo 证据页(类型 + client + 页 + 路由,mock-first,注册表自动进导航/冒烟)。
- `scripts/register_schedules.ps1`:一条 DAILY 23:00 盘后报告。

## 诚实边界总结

Phase 5 的诚实答案:三项的机制半区大多**已建**,本轮把 ① 死掉的维护 sweep 接成**承重驱动器 + fail-safe 停止
加仓联动**、② 补上真缺的**异常检测机制 + margin 门**(默认惰性)、③ 把已有报告**补测试 + PnL/成本归因 +
上架 console + 日调度**。所有自主动作严格 fail-safe 方向(只减不加 / 只加告警 / 只读报告);涉及**减仓目标
数字 / 异常阈值 / margin 地板**的激活一律留用户签(建机制 + fail-closed 默认,不代签风险数字);近实时盘中
(<小时)cadence 变更推迟。**全量门禁绿(validate.py 三检全 PASS,live 栈 674);前端 typecheck/build/61 tests 绿。**

**留后续(需用户拍板)**:降级→自动减仓的 de-risk 目标数字 + 独立安全路径(与紧急通道复用还是新开)、
跳变%/margin 地板/近实时 cadence 的启用、维护 sweep 的 backtest 参照数据(研究/人填 alpha_backtest_refs.json)。
