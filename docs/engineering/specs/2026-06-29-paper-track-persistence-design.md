# 断点2:前向 paper 追踪持久层 —— 设计 spec

> 状态:设计已与用户确认(2026-06-29)。下一步:writing-plans 出实现计划。
> 约定:本文中文叙述,代码标识符英文。安全边界见 §6,不可让。

## 1. 目标(一句话)

为 alpha 建一个**逐日累积、可增长、可查询、不可变**的前向 paper P&L/IC 持久层,把唯一真边(资金费
carry)和其它 alpha 的**真样本外纸面表现**逐期落库,从而一次性解锁四件被"无前向指标"卡住的事:

1. LifecycleMonitor 的 `live_ic` 从"诚实 null"变成**真累积纸面 IC**;
2. 衰退维护 pass(断点4)从"无 live 输入"变成由**真 backtest-vs-paper 差距**驱动;
3. Alpha Zoo 多维驾驶舱新增**时间/活体征维度**(paper IC、paper Sharpe、perf_gap、纸面期数);
4. carry 这条边的**前向样本外验证**有了逐日累积的曲线(体检的头号缺口)。

就绪等级不变:**L2_DRY_RUN_ONLY**。纯 Python 标准库、确定性(墙钟只在驱动器 HTTP/CLI 边界)、
append-only、研究只读、`auto_approved=0` 与"非人永不 APPROVED"不变;`live_ic` 变真**不会**让任何东西
自动晋级——晋级到 APPROVED 仍是人工专属。

## 2. 已确认的两个关键决策

- **前向语义 = 两者都要**:① 留出尾部回放(OOS tail)打底,今天就能在现有数据上看到曲线增长;
  ② 新数据追加(new-data append),真日历前向。**同一个 `advance` 接口、同一张 store,两种来源**
  (`source ∈ {OOS_TAIL, NEW_DATA}`)。
- **首增量范围 = 全竖切**:store + 推进 + 驱动填充 + **全部三个消费方**(live_ic / 衰退 / Zoo 时间维)
  一次接齐。

## 3. 诚实关键点 —— train/paper 真切分(不可妥协)

"留出尾部"要是**真样本外**才诚实。现有 60 个 alpha 是在**整段**序列上评估的,所以把尾巴**事后**贴成
"OOS paper"是不诚实的(那段被评估见过)。

因此驱动器 `tools/crypto_paper_track.py` 必须:用既有确定性 `run_research_session` 在 **`returns[:boundary]`
(train)** 上评估出 alpha + 其 backtest 指标,**再把同一 alpha 前向应用到 `returns[boundary:]`(paper)**
算 OOS 收益/IC 入库。这样纸面 P&L 是评估**没见过**的那段。`boundary` 默认取 ~70%(留 ~30% 当 paper),
可配。新数据到了走同一个 `advance`(`source=NEW_DATA`)在 `last_ts` 之后追加。

> 反面教材(禁止):把已评估的整段尾巴直接当 paper 回贴、或把 in-sample 指标标成 live —— 那是用假设
> 代替真样本外,违反平台诚实第一。

## 4. 数据模型 —— `data/cache/paper_track.db`(gitignore;CI 无库→消费方 fail-closed 到 null)

照搬 `services/research/experiment_registry/store.py` 的成熟模式(schema 校验、`create=` 守卫、追加式)。

```sql
CREATE TABLE IF NOT EXISTS paper_periods (
  alpha_id        TEXT    NOT NULL,
  as_of_ts        INTEGER NOT NULL,   -- UTC ms,逐期
  realized_return REAL    NOT NULL,   -- 该 alpha 在该期的 L/S 实现收益
  realized_ic     REAL,               -- 该期截面 IC(可空)
  source          TEXT    NOT NULL,   -- OOS_TAIL | NEW_DATA
  PRIMARY KEY (alpha_id, as_of_ts)    -- INSERT OR IGNORE 幂等
);
CREATE TABLE IF NOT EXISTS paper_track (
  alpha_id        TEXT PRIMARY KEY,
  start_ts        INTEGER,
  last_ts         INTEGER,
  n_periods       INTEGER,
  cum_return      REAL,               -- 累积纸面收益
  paper_sharpe    REAL,               -- 纸面 Sharpe(annualised 252,与全仓口径一致)
  paper_ic        REAL,               -- 纸面期均 IC —— LifecycleMonitor live_ic 的真源
  backtest_sharpe REAL,               -- train 段评估 Sharpe(对照)
  perf_gap        REAL                -- backtest_sharpe − paper_sharpe(= consistency.performance_gap)
);
```

## 5. 组件与接口(每个单元一个清晰职责)

### 5.1 `services/research/paper_trading/store.py::PaperTrackStore`
- `__init__(path, *, create=False)` —— sqlite,`create=False` 且无库 → 调用方 fail-closed;schema 缺表即抛
  (never silent partial)。
- `append_periods(alpha_id, rows)` —— `rows=[(as_of_ts, realized_return, realized_ic, source)]`,`INSERT OR
  IGNORE`(幂等:重跑安全、新期追加)。
- `recompute_summary(alpha_id, *, backtest_sharpe=None)` —— 从 `paper_periods` 重算 `paper_track` 一行
  (n_periods/cum_return/paper_sharpe/paper_ic/start_ts/last_ts/perf_gap)。
- `track(alpha_id) -> dict | None`、`periods(alpha_id) -> list`、`all_tracks() -> list[dict]`。
- 纯 stdlib(sqlite3 + statistics + `services/research/backtest_engine/metrics.sharpe`);无 RNG/无墙钟。

### 5.2 `services/research/paper_trading/advance.py::advance_paper_track`
- `advance_paper_track(store, alpha_id, returns, ic_series, *, boundary, source, ts0, dt, backtest_sharpe)`:
  取 `returns[boundary:]`/`ic_series[boundary:]`,逐期 `as_of_ts = ts0 + i*dt` 组 rows,`append_periods` +
  `recompute_summary(backtest_sharpe=backtest_sharpe)`。幂等;返回新增期数。
- 纯函数式编排,无 IO 以外副作用;不触 lifecycle/catalog。

### 5.3 `tools/crypto_paper_track.py`(离线驱动器,墙钟只在此边界)
- 读真收益/IC 源(`session._factor_returns` 或快照 `factor_series`),对每个目标 alpha:在 `[:boundary]`
  评估(backtest_sharpe),把 alpha 前向应用到 `[boundary:]` 得 OOS 收益/IC,`advance(... source=OOS_TAIL)`。
- 目标集:carry 打头 + registry 里的 alpha(通用);`boundary` 可配。确定性、可复现、可重跑(幂等)。
- **不**注册进 run_registry(白名单不含此 kind;与既有研究驱动器一致,结果落 `paper_track.db`)。

### 5.4 三个消费方(console_api 读 store,fail-closed 到 null/SYNTHETIC)
- **LifecycleMonitor**(`_lifecycle_monitor_payload`):每行 `live_ic = PaperTrackStore.track(aid).paper_ic`
  (有 paper→真值;无→仍 null);`recommendation` 在有 paper 时由真 paper 喂 `monitor_alpha_health`
  (有真 live 输入则不再恒 `NO_LIVE_DATA`)。新增 provenance 子标记 `PAPER_TRACK`。
- **Maintenance pass**(`services/alpha/maintenance.run_maintenance`):`health_by_alpha` 的 `live` 侧从
  `paper_track`(paper return/sharpe)真填——衰退维护从"按需空跑"变"有真输入可判 DEGRADED/RETIRE"。
- **Alpha Zoo**(`_alpha_zoo_payload` + `_ZOO_DIMENSIONS`):新增 `paper_ic / paper_sharpe / perf_gap /
  n_paper_periods` 四维(分组"时间/活体征");有 paper 数据才有值,否则 null(诚实)。前端 `ZooDimension`
  自动渲染(已是元数据驱动)。

## 6. 容器化 / 安全边界(机器强制,不可让)

- `LIVE_TRADING_ENABLED=false` 等五标志不动;**`live_ic` 变真不解锁任何自动晋级**——晋级到 APPROVED 仍
  人工专属、`auto_approved=0`。paper-track 是**研究只读**记录,无券商/无下单/无凭证。
- 纯 stdlib、确定性(墙钟只在 §5.3 驱动器边界取);append-only、不可变明细。
- store 缺失/损坏 → 消费方 fail-closed 到 null/SYNTHETIC(CI 无库照样绿)。
- 治理:不引入网络 import(driver 读本地真数据);若日后驱动器需联网抓"新数据"(`NEW_DATA`),走
  `tools/` 已有抓取器边界 + 治理白名单(本 spec 不含联网抓取,留作 B 延伸的后续)。

## 7. 测试(TDD,门绿)

- `tests/research/test_paper_track_store.py`:append 幂等(重复 `as_of_ts` 不重复计)、`recompute_summary`
  数值正确(paper_sharpe/paper_ic/cum_return/perf_gap)、`create=False` 无库 fail-closed、schema 缺表抛。
- `tests/research/test_paper_track_advance.py`:`advance` 的 `[boundary:]` 切分、幂等、`perf_gap` 等于
  `consistency.performance_gap`、新期追加。
- 消费方:`test_console_api`/契约各加"有 paper→真值 / 无 paper→null"——LifecycleMonitor `live_ic`、Zoo
  新四维;`maintenance` 用真 paper 输入驱动 DEGRADED 的一条。
- `scripts/validate.py` 全绿(治理扫描 + 全套)。

## 8. 实现拆分(writing-plans 细化)

1. `PaperTrackStore`(store.py)+ TDD。
2. `advance_paper_track`(advance.py)+ TDD。
3. `tools/crypto_paper_track.py` 驱动器:train/paper 切分 + 填充真 paper 数据(carry + registry)。
4. 消费方①:LifecycleMonitor `live_ic` + recommendation 真接 + 契约/测试。
5. 消费方②:maintenance pass 真 paper 输入 + 测试。
6. 消费方③:Alpha Zoo 时间维四维 + 前端(元数据驱动,改动小)+ 契约/测试。
7. 门绿 + 提交;重启 console_api;浏览器实测 live_ic/Zoo 时间维变真。

## 9. 非目标(YAGNI)

- 不做实盘/券商/真凭证(L2 边界)。
- 不做联网抓"新数据"的 `NEW_DATA` 自动增长(本 spec 只做 store 支持 `NEW_DATA` 来源 + OOS_TAIL 填充;
  真联网增长留后续)。
- 不动晋级审批逻辑、不让 agent 因 paper 数据自动 APPROVED。
