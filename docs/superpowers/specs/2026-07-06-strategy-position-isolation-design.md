# strategy_id 仓位隔离(战线③)——设计与落地记录

日期:2026-07-06 · readiness 不变:**L2_DRY_RUN_ONLY**(测试网/Demo 零真实资本)
状态:**commit 1(账本)+ commit 3(驱动器)已落地;commit 2(mandate per-strategy 子限额)显式推迟(业务决定,见 §5)**
经对抗式评审(NEEDS_CHANGES → required_changes #1–#10 全部处理,见 §8 对照表)。

## 0. 核心裁定(一句话)

**strategy_id 只是本地账本内部的分账维度。** venue/保证金/风控/对账/紧急减仓全部维持账户级
(现状不动);唯一诚实的多策略语义 = 「账户级总账本 vs venue 对账 + 本地按 fill.strategy_id
分区分账」,分区求和恒等于账户级(结构性不变量,测试钉死,不需要运行时校验)。

为什么这是唯一诚实的切法:交易所根本不知道策略——Binance /fapi positionRisk、Bybit V5
position list 都是账户级的(Hedge Mode 的 positionIdx 区分多空方向,不是策略)。任何
"per-strategy vs venue" 对账都是拿本地分解自己对自己,伪造独立性。

## 1. 账本(live/execution/position_ledger.py)

- **新库 schema**:live_orders / live_fills / live_tca 各加 `strategy_id TEXT NOT NULL`,
  **刻意无默认值**——绕过 Python API 的裸 INSERT 在新库上直接报错,而不是静默错标
  (评审"小"项采纳)。
- **惰性迁移**(每次打开都跑,create 与非 create 路径都跑——ops API/gonogo 用
  `LiveLedger(path)` 无 create 打开):老库缺列 → `ALTER TABLE ADD COLUMN strategy_id TEXT
  NOT NULL DEFAULT 'crowd_contra'`。SQLite 的 ADD COLUMN **不重写任何存量行、不碰任何已有值**
  (老行读取时返回列默认值)——存量真实测试网数据零丢失零改动,这正是回填语义。默认值会
  永久留在被迁移库的 schema 里(SQLite 无法无损删默认值)——无害:写入 API 全部显式必填
  strategy_id,SQL 默认值只对迁移前的存量行生效。
- **迁移并发安全(评审 #3)**:账本的打开方是多个独立进程(周度驱动器、小时级哨兵 --act、
  常驻 ops API 每请求打开、gonogo_report、备份)。PRAGMA→ALTER 两步非原子,两进程同时打开
  老库都看到列缺失、都 ALTER——后到者的 `duplicate column` OperationalError 按"已被并发迁移
  完成"吞掉(只吞 duplicate column,其它 OperationalError 照常暴露)。busy_timeout 评估:
  Python `sqlite3.connect` 默认 `timeout=5.0` 秒本来就注册了 busy handler,驱动器写库期间
  ops API 的打开会等待而非立刻 `database is locked`,不需要额外配置;若未来出现长事务再议。
  **实施当天的真实印证**:本机常驻 ops API(uvicorn reload)在代码落盘后的下一次前端轮询里
  就对 binance/bybit_demo 两个真实账本触发了迁移(bybit testnet 未配置 key 故未打开)——
  逐行核对与迁移前备份完全一致、回填值全部 'crowd_contra',并发/热迁移路径在真实环境先于
  测试网联调被走通。
- **写入 API**:`record_submission` / `record_fill` / `record_tca` 的 `strategy_id` 为
  **必填关键字**(不给默认值)——强制显式从驱动器一路传到底。in-repo 调用方封闭可枚举
  (pipeline.py / emergency.py / 各测试),一个 commit 内同改。
- **读取 API**:不带参 = 账户级聚合(现有语义一字节不变);带参 = 策略过滤。
  `all_positions(strategy_id=None)` / `orders(strategy_id=None)` /
  `fills(instrument_id=None, *, strategy_id=None)` / `position(inst, strategy_id=None)` /
  `strategy_ids()`(**orders ∪ fills** 的 DISTINCT——评审 #5:只提交未成交的策略也必须可见)。
- **结构性不变量**(测试钉死,非运行时检查):`Σ_s all_positions(strategy_id=s)` 逐标的恒等于
  `all_positions()`——账户级就是全部 fills 的和,策略级是按 fill.strategy_id 的分区,分区求和
  恒等于全集,数学上不可能漂移。
- **紧急减仓保留账本**:`live/execution/emergency.py::EMERGENCY_STRATEGY_ID =
  "emergency_flatten"`(与既有 audit actor "live:emergency_flatten" 同词,不造新词)。紧急减仓
  作用于账户级 venue 仓位,成交无法按策略归属(也不该在紧急时刻做归属决策);记入保留账本后
  账户级立即正确(含减仓),各策略分账仍显示"该策略以为自己持有的仓位",差额全部落在
  emergency_flatten 账本里——恒等式不破、对账不破。事后重归属 = 事故复盘的人工决定(§10)。

## 2. 对账语义(live/execution/reconciliation.py——零代码改动,docstring 钉死)

- `preflight_check(ledger.all_positions(), venue_positions)` 保持现状不带 strategy_id:
  `all_positions()` 无参语义就是账户级,多策略后这行一个字都不用改。
- **明令禁止** per-strategy vs venue 对账(docstring + 说明性测试
  `test_preflight_stays_account_level_with_multi_strategy_ledger`:单策略分账对 venue 必然
  报假 drift——其它策略的持仓全成了"漂移")。
- 策略级一致性的真实保证 = §1 分区恒等式(本地结构性成立)+ 账户级对账(外部权威核对)。
  账户级 in_sync ∧ 分区恒等式成立 ⇒ 每个策略分账的可信度等于账户账本的可信度。
- **事实更正(评审 #4)**:`tools/live_risk_sentinel.py` 的风控仓位来自 **venue 真实持仓行**
  (`binance_position_rows` / `bybit_position_rows`),不是 `ledger.all_positions()`(初版设计
  声称核实过、实际没核实);LiveLedger 只在 `--act` 分支作为紧急减仓的记账目标。本战线对
  哨兵**零改动**,回归钉子 = `test_sentinel_risk_positions_come_from_venue_rows_not_ledger`
  (无 all_positions、无 strategy_id、LiveLedger 构造仅存在于 --act 分支)。

## 3. 流水线 / 驱动器接线

### 3.1 live/pipeline.py

- `submit_live_batch(..., account_positions=None)`(评审 required #1):
  - `current_positions` = **本策略**分账仓位 → `build_intents` 目标增量(否则第二条策略的
    delta 会去 net 第一条策略的持仓,把别人的仓当自己的平掉);
  - `account_positions` = **账户级**全量仓位 → `evaluate_mandate` 的 per-instrument cap 投影
    (共享 venue mandate 的单标的上限约束的是账户对该标的的总敞口——只看本策略仓位会让两条
    策略各自贴着 cap、账户实际 2×cap 双双判 AUTO);
  - `account_positions=None` 回退 `current_positions`(单策略调用方向后兼容:单策略下
    账户=策略是事实,不是近似);
  - 冻结语义共存:frozen 只作用于 targets/current 两侧的增量计算;mandate 的账户上下文
    **不按 frozen 过滤**(账户事实不因冻结而改变,与 63982c8 的既有注释语义一致)。
  - 注入桩测试:monkeypatch `live.pipeline.evaluate_mandate` 捕获实参,断言 mandate 收到
    账户级、venue 收到按策略级仓位算出的增量;另有端到端反例测试
    `test_per_instrument_cap_enforced_on_account_level_exposure`(错用策略级口径就会漏放)。
- 账本打标:`record_submission` / `record_fill` / `record_tca` 全部带 `strategy_id=strategy_id`。
- 崩溃恢复 `resolve_unconfirmed_orders`:补记 fill 的 strategy_id **从订单行继承**
  (`o["strategy_id"]`,迁移后行必有此键)——账目跟着订单走,恢复路径不需要知道当前驱动器
  跑的是哪条策略;驱动器在每轮读仓位之前对**全账户**跑一遍(覆盖所有策略的遗留订单)。

### 3.2 tools/live_track_advance.py(驱动器)

- **策略注册表**(镜像 ic_panel_registry「加一行」范式):
  `_STRATEGIES = {"crowd_contra": {"signal": _latest_weights_and_prices}}`;
  CLI `--strategy`(默认 `_STRATEGY_ID="crowd_contra"`,未注册 → SystemExit,在任何网络调用
  之前)。第二条策略上线 = 注册表加一行 + 用户重签 mandate(§5)。
- **口径分离**(本战线最大实施风险,评审 risks #1;helper `position_scopes(ledger, sid)` 行为
  可测 + 源码结构钉子锁 main() 接线):
  - `account_positions = all_positions()` → `preflight_check` / `make_ctx_factory`(gate 的
    敞口/集中度/杠杆看全账户)/ `submit_live_batch(account_positions=)`;
  - `strategy_positions = all_positions(strategy_id=sid)` → `submit_live_batch(
    current_positions=)` / parity 的 live 侧(否则第二条策略上线当天,第一条的全部持仓都是
    本策略 parity 里的 `unexpected_live_positions` 假阳性)。
  - 风控 `evaluate_risk_state` 维持现状(venue_positions,账户级,不变)。
- **make_ctx_factory 参数化 strategy_id**(评审 #8):StrategyApproval 挂真实策略名
  (check_strategy_approved 不比对 id、不会误拒,但审计痕迹必须真实)。
- **per-strategy status 路径规则**(评审 #9):默认策略沿历史路径
  `live_track_status{suffix}.json`(连续性:watchdog/ops API/gonogo/备份 glob 不破),非默认
  策略 `live_track_status{suffix}_{strategy_id}.json`。规则只在 `_venue_paths` 一处生效
  (单一真源,"live_track_status" 字面量全文件仅此一处,测试钉死),驱动器全部 **4 个** status
  写点(对账漂移 / kill-switch / 风险越限 3 个 hard-stop + 正常收尾)共用 `paths["status"]`,
  且每个载荷都带 `"strategy": strategy_id`。其余路径全部账户级 per-venue 共享(账本/风控峰值/
  day_state/审计/kill_switch——它们是账户概念,拆开反而错);parity jsonl 记录自带 strategy_id
  字段,自描述,共享一个文件。
- **多策略未签子限额告警**(评审 #5,本轮范围裁定的简化落地):
  `multi_strategy_mandate_alert(ledger.strategy_ids(), 本次 strategy_id, mandate)`——基于
  **本次调用的 strategy_id ∪ 账本历史(orders ∪ fills)** 判断,第二条策略首次运行、任何订单
  落库之前就响(不等事后 fills,preview 也响);emergency_flatten 不算策略;mandate 一旦带
  非空 `strategies` 属性(commit 2 落地后)告警自动闭嘴。提醒而非硬停(维持"mandate 数字由
  用户签、代码不代填"哲学)。告警文案已核对通用治理扫描器 FORBIDDEN_APIS 字面量(词边界+
  可选复数),测试钉死。
- **kill-switch 语义不变**:跨进程闩锁 per-venue 共享,天然覆盖多策略——一条策略触发熔断,
  另一条也被挡(共享同一个保证金池,这是特性)。

### 3.3 tools/watchdog.py(评审 #10)

`live_track_status_targets(venue, suffix)`:默认策略恒被期待(venue 已配置即应每周运行);
非默认策略**只有在其状态文件已存在(= 真的被调度运行过)时**才进入期待集——注册表回答
"谁能跑",不回答"谁被调度了",按注册表全集期待会对"注册了但只在别的 venue 调度"的策略永久
误报。代价诚实记录:非默认策略下线后遗留状态文件会持续告警,直到人工删除该文件(这是特性——
静默消失的调度正是死人开关要抓的)。测试:注册未调度不告警 + 已调度文件进期待集。

### 3.4 其它消费方核查(评审 #6)

- `tools/live_ops_api.py` / `tools/gonogo_report.py`:调用 `_venue_paths(cfg["suffix"])`
  位置参数,新签名 keyword-only 默认值向后兼容,读的都是默认策略的历史 status 路径 →
  **零改动**(非默认策略的 status/分账视图 = 后续增强,见 §10)。
- `tools/live_backup.py`:glob `live_track_status*.json` 覆盖新路径——**用测试钉死**
  (`test_manifest_covers_per_strategy_status_files`),不再是"碰巧匹配"。
- **console(services/research/console_api/main.py)核实结论**:`_live_trading_payload` 是
  **逐字段提取**(execution_stack 只挑白名单字段),不透传未知字段——status 快照新增的
  `"strategy"` 字段对 console 契约**零影响**(契约测试断言顶层键,未动)。把 strategy 字段
  surface 到 console/LiveTrading.tsx 需要改 services/ 树 + TS 类型 + 契约测试同 commit——
  本轮按协调指令不碰 services/,列为后续(§10)。

## 4. 风控 / 紧急减仓:维持账户级(现状不动)

- 保证金、强平、回撤都是账户级物理事实,交易所不按策略清算;kill-switch 保护的是资本不是
  归因;账户级是两个口径里**更保守**的那个(任何一条策略把账户拖越限,全账户停)——安全关键
  系统里保守方向就是正确方向。
- 紧急减仓输入 venue_positions(账户级),**不按策略拆**(紧急时刻按策略拆单引入"先平谁"的
  决策延迟,而独立紧急通道的设计初衷就是最短路径减敞口);成交记入 EMERGENCY_STRATEGY_ID
  保留账本(§1),归因与减仓速度解耦。

## 5. mandate per-strategy 子限额 = **显式留白(业务决定,待用户拍板)**

设计的 commit 2(`mandate.strategies` 可选子限额块 + `load_mandate` 静态校验 + evaluate_mandate
fail-closed `strategy_not_in_mandate`)**本轮不实现**:子限额的具体数字必须由用户签署,方案
选择(A 各自子限额 / B 共享池运行时聚合 / C 每策略独立文件)也是业务权衡。`mandate.strategies`
缺失走旧行为(单策略共享全额)本来就是设计的兼容路径,现有已签署 mandate 文件继续有效。

**诚实论证重写(评审 #2,给未来实现 commit 2 的人)**:mandate 的 max_gross/max_net 是
**逐批次订单名义额之和**(live/governance/mandate.py L89–105),不是累计持仓敞口——
"Σ子限额 ≤ venue 总限额签署时静态校验 ⇒ 账户总敞口被结构性界住"这个论证**不成立**:每周批次
各自限内,持仓可以周复一周累积,mandate 完全界不住账户敞口。真正界住账户敞口的是 gate 的
`max_gross_exposure`/`max_net_exposure` + **账户级** per-instrument cap 的持仓投影(本轮
required #1 修的正是后者的口径)。选方案 A 依然合理(与现状语义对齐、无跨进程竞态),但
"免运行时聚合"的代价要如实写为**接受 mandate 只管单批授权**,不是结构性敞口上界。

过渡窗口风险(设计 risks 原文,依然成立):第二条策略在用户重签 mandate 之前就 --submit,
两条策略共享全额度、总敞口可能达 2×预期——缓解 = §3.2 的告警 + runbook 上线检查清单,
不是机器强制。

## 6. 治理边界影响 = 零结构性变化

- `services/` 与 `quantos/` 一行未改 → 通用治理扫描器输入零变化;live/ 继续刻意不进
  `SCAN_SUBDIRS`(勿"修复")。
- `tests/governance/test_live_execution_boundary.py` 全部既有断言原样成立(新增代码零网络/
  SDK/密钥面);本战线新增的治理性测试全部放 `tests/live/`(与 live/ 专属治理测试同列)。
- 新字段名 `strategy_id`/`strategy` 均非 FORBIDDEN_APIS 字面量(词边界正则核对过);注意
  **`live_orders` 会命中 `live_order` + 可选复数**——该表名只存在于 live/ 树(不被扫描),
  绝不要把它写进 services/ 或 quantos/ 树的任何字符串。

## 7. 存量账本一次性核查(评审 #7,只读,2026-07-06)

方法:sqlite `mode=ro` 打开三个真实账本逐行枚举 + 前缀反算验证。**核查先于迁移完成;迁移前
全量备份在 `data/backup/pre-strategy-isolation-20260706/`(gitignored),事后逐行比对确认
迁移零改动。**

| 账本 | live_orders | live_fills | 形态 |
|---|---|---|---|
| live_track.db(Binance)| 15 | 25 | 三种形态,见下 |
| live_track_bybit.db | 0 | 0 | 空(testnet 水龙头失灵,从未跑过)|
| live_track_bybit_demo.db | 20 | 20 | 全部 32-hex 决策订单,两个调仓批次 |

live_track.db 的三种行形态与归属判定:

1. **32-hex 决策订单/成交**(12 orders + 14 fills,fill_id 16-hex):前 16 位
   前缀经 `decision_prefix("crowd_contra", decided_ts, inst)` **逐一反算命中**——
   forward 台账唯一 decided_ts=1782950400000(2026-07-02),TRX/XLM/ADA/ATOM/NEAR 等前缀
   全部匹配 ⇒ 确属 crowd_contra 决策产物(计算验证,非记忆断言)。bybit_demo 的 20 单同法。
2. **`quantos-smoke-1783147029` + `recon-smoke-fill-1`**(0.002 BTC):2026-07-04 真实联调
   冒烟单 + runbook §"交易所侧多出仓位"的人工补记 fill——不是 crowd_contra 的决策产物,
   是它的运维残留。
3. **`resize-comp-*` 10 笔 fills(无对应 order 行,filled_at=1783300000000)**:2026-07-05
   client_order_id 撞 ID CRITICAL bug 之后的人工补偿记账(调仓增量真实执行但被
   INSERT OR IGNORE 吞掉的那批)——经济上就是 crowd_contra book 的调仓。
4. **审计库核对:全库无 `EMERGENCY_ORDER_SUBMITTED` 事件**——真实 breach 减仓从未发生,
   不存在需要归到 emergency_flatten 的存量账目。

**裁定(用户拍板,任务书 #7)**:三种形态一刀切保持 ALTER 默认值回填 `'crowd_contra'`。
形态 2/3 是手工补记但都属 crowd_contra book 的运维产物;0.002 BTC 冒烟仓在 parity 里本来就
如实报 unexpected(既有行为),分账后归属 crowd_contra 不改变任何对账结论。若未来事故复盘
需要更细归属(如 `manual_backfill` 保留账本),那是新的人工补记流程决定,不回改存量。

**迁移后验证(真实数据)**:迁移前后逐行逐值一致(除新增列)、账户级 positions 逐标的相等、
`strategy_ids()==["crowd_contra"]`、分区恒等式成立。

## 8. 评审 required_changes 对照

| # | 处置 |
|---|---|
| 1 | `submit_live_batch(account_positions=None)`,mandate 收账户级、build_intents 用策略级;注入桩 + 端到端反例测试 ✅(§3.1)|
| 2 | 方案 A 论证诚实重写 ✅(§5)|
| 3 | duplicate column 幂等 catch + 双开测试;busy_timeout 评估(Python 默认 5s 已覆盖)✅(§1)|
| 4 | §2 哨兵事实更正 + 回归钉子测试 ✅ |
| 5 | 告警改为按本次 strategy_id 判断(首次运行即响);strategy_ids() 并上 live_orders ✅(§3.2)|
| 6 | 消费方全核查:ops_api/gonogo 零改动(核实后声明)、backup glob 加测试、console 核实结论=契约不受影响、console surfacing 留后续 ✅(§3.4)|
| 7 | 存量账本逐行枚举 + 前缀反算 + 手工行识别 + 归属裁定入文档 ✅(§7)|
| 8 | make_ctx_factory 的 StrategyApproval.strategy_id 参数化 ✅ |
| 9 | 路径规则单一真源 + 全部 4 个写点共用 + 源码钉子测试 ✅(§3.2)|
| 10 | watchdog 按"状态文件存在"期待 + 注册未调度不告警测试 ✅(§3.3)|

## 9. 测试(全部离线,零网络)

新增/扩充(commit 1+3 共 ~30 个新测试):
- `tests/live/test_position_ledger_strategy.py`(新,11):新库 schema 三表带列、写 API 必填
  关键字、真实形态老库迁移逐行保真、连开三次幂等、并发双开不炸、duplicate-column 只吞重复列、
  无 tca 表最老库、双策略对冲净额、**分区恒等式(含 emergency)**、strategy_ids 并 orders、
  无参读取语义不变。
- `tests/live/test_pipeline.py`(+5):行打标、恢复继承订单 strategy_id、mandate 账户级 vs
  build_intents 策略级(注入桩)、account_positions=None 回退、账户级 per-instrument cap
  端到端反例。
- `tests/live/test_emergency.py`(+1):紧急成交入保留账本,账户级含减仓、原策略分账不变。
- `tests/live/test_reconciliation_preflight.py`(+1):对账账户级 + 禁止口径的说明性反例。
- `tests/live/test_parity.py`(+1):策略过滤消除跨策略 unexpected 假阳性 + 错误口径反例。
- `tests/live/test_strategy_wiring.py`(新,17):注册表、--strategy SystemExit、status 路径
  规则(默认沿历史/非默认加后缀/其余路径账户级共享)、4 写点源码钉子、ctx_factory 参数化、
  position_scopes、多策略告警网格(含"首次运行即响"/emergency 除外/FORBIDDEN_APIS 文案核对)、
  watchdog 目标(默认恒期待/注册未调度不告警/已调度进期待)、哨兵回归钉子。
- `tests/live/test_live_backup.py`(+1):备份 glob 覆盖 per-strategy status。
- 既有测试的 record_* 调用点全量同 commit 更新(必填关键字破坏性签名变更,调用方封闭)。

## 10. 显式非目标 / 留白(需要人拍板,不由代码悄悄决定)

1. **mandate per-strategy 子限额(commit 2)**:业务决定,数字用户签(§5)。
2. **emergency_flatten 账目的事后重归属**(按比例?按人工判断?)——append-only 账本下的
   重归属机制是事故复盘流程的一部分,本战线只保证账目不丢不混、恒等式不破。
3. **第二条策略是谁、何时上**:当前可部署 book 仍是 crowd_contra 单条(carry 净-成本不成立、
   wide 因子已被证伪),本战线只做前置。
4. **同 venue 多策略调度方式**:推荐 schtasks 串行(同一任务内顺序跑两次驱动器),规避 sqlite
   写锁与 mandate 评估并发窗口;两策略同 venue 同标的反向下单造成的账户级来回换手费用问题
   一并待议。
5. **ops API / console / gonogo 的 per-strategy 视图**:ops API `last_run` 只读默认策略
  status;console `_live_trading_payload` 逐字段提取,`strategy` 字段待接(改 services/ 需与
  TS 类型/契约测试同 commit);ReviewQueue.record_batch 尚无 strategy 归属列。
6. **CLAUDE.md 摘要段**:按协调指令本轮不改 CLAUDE.md,落地摘要由本文档承担,CLAUDE.md
   补记留给统一提交时人工核验后追加。
