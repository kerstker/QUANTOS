# Stage 0 设计:真前向 paper-track + 生产化数据刷新(实盘路线第一步)

> **背景:** 用户已拍板走 Phase 3A 人工门通往小资金实盘(决策记录:
> `docs/2026-07-04-live-readiness-review-and-plan.md` §5)。真钱开闸门 = **≥8 周前向证据**,
> 日历时间是最长瓶颈,所以 Stage 0 的唯一目标是**让证据钟今天开始走**。
> **审查发现:** `paper_track.db` 现有 12 条 track 全是老合成时代小时因子(合成纪元时间戳),
> 真可部署边 `crowd_contra` 从未入前向跟踪;三个数据源水位 2026-06-26/-06-26/-07-02(已滞后)。

## 1. 与现有"断点2" paper-track 的关系(诚实区分)

现有 `advance_paper_track`(`paper_trading/advance.py`)是**历史切分**变体:对一段已知历史收益序列
按 boundary 取尾巴当 paper 区。它诚实,但**不是前向**——整段序列在写入时已经存在。

真前向的定义性约束:**决策必须在结果存在之前被持久化**。
- T 时刻:用 ≤T 的数据算出权重,**先落库**(append-only decision ledger,带墙钟 recorded_at);
- T+1 周:新数据到达后,按**已落库的权重**计算实现收益,追加 paper period(真实时间戳)。
- 权重落库在前、收益兑现在后,顺序由墙钟戳证明——这才是交易所之外能拿到的最强前向证据。

## 2. 组件

### 2.1 前向核心 `services/research/paper_trading/forward.py`(纯逻辑,门内)
- `ForwardLedger`:同 `paper_track.db` 文件里新增 append-only 表
  `forward_decisions(alpha_id, decided_ts, recorded_at, weights_json, source, realized)`,
  PK `(alpha_id, decided_ts)`,INSERT OR IGNORE 幂等;不改动 `PaperTrackStore` 现有两表。
- `record_decision(ledger, alpha_id, decided_ts, recorded_at, weights)`:decided_ts = 权重所依据的
  最后一根数据 bar 的 event_time(数据时间);recorded_at = 驱动器传入的墙钟(核心自己不碰墙钟)。
- `realize_due(ledger, store, alpha_id, price_at, funding_between, *, min_gap_ms, min_coverage)`:
  对每条未兑现决策,若存在 ≥ decided_ts+min_gap_ms 的下一个价格截面则兑现:
  `ret = Σ_i w_i·(p_i(T')/p_i(T)−1) + Σ_i (−w_i)·funding_sum_i(T,T']`(美元中性 L/S 的周度 gross
  + 资金费现金流,与干跑口径一致;**不含交易成本**——净成本执行现实是干跑流水线的职责,来源字符串
  `FORWARD_LIVE_GROSS_PLUS_FUNDING` 诚实标注)。同时算截面 Spearman `realized_ic`(信号 vs 实现收益)。
- fail-closed:价格覆盖率 < `min_coverage`(默认 0.8)→ **不兑现、留 pending、报告 alert**,绝不静默
  用残缺截面凑数;无 RNG、无墙钟、无网络(治理不变量)。

### 2.2 增量数据刷新 `tools/live_data_refresh.py`(网络在门外)
三个源全部复用现有幂等原语,只抓水位之后的缺口:
1. **价格** `crypto_funding.db`:逐 ticker `fetch_daily_rows(days=缺口+3)` →
   `ingest_provider_rows(store, now=墙钟, time_unit='ms')`(PIT 主键去重 + 未来数据守卫);
2. **资金费** `crypto_funding_rates.json`:逐 ticker `fetch_daily_funding(days=缺口+3)` 按日桶合并;
3. **OI 指标** `crypto_oi_metrics.json`:调 `binance_oi_metrics_fetch.main(bounded=True)`
   (Data Vision 归档,本身断点续传,滞后 ~1-2 天是源头属性)。
产出 `data/cache/live_refresh_status.json`:各源刷新前/后水位、新增行数、失败列表、staleness 天数。
限速沿用各 fetcher 内建 sleep;单 ticker 失败 fail-soft 记录、不中断整轮。

### 2.3 前向驱动器 `tools/forward_track_advance.py`(墙钟只在此边界)
每周(或任意时刻,幂等)运行:
1. 调 2.2 刷新数据;
2. `build_panel()`(OI 大币宇宙)→ `ax.evaluate` 算 `CROWD_CONTRA`(主边)与 `CARRY`(对照)信号
   → `_legs(sig, QUANTILE)` 权重——**与干跑驱动器同一条信号路径**,零新信号逻辑;
3. 若最新数据行距上次决策 ≥7 天 → `record_decision`(两条边各一);
4. `realize_due` 兑现所有到期决策 → `PaperTrackStore.append_periods` + `recompute_summary`
   (backtest_sharpe 参照:crowd_contra 用干跑 VT 1.38 那条的周度 gross 口径对应值,首次运行时从
   历史面板同口径重算,存进 status 以固定参照);
5. 写 `data/cache/forward_track_status.json`(每边:n_periods/paper_sharpe/paper_ic/最后决策与兑现
   时间戳/pending 数;数据 staleness;告警列表)——console 后续可加只读端点。

### 2.4 告警接缝 `tools/alert_sink.py`
- `emit(alerts)`:默认 append `data/cache/alerts.jsonl`(本地台账,永远生效);
  若 env 有 `QUANTOS_TG_BOT_TOKEN`+`QUANTOS_TG_CHAT_ID` 则再 POST Telegram sendMessage
  (stdlib urllib;密钥只读 env,永不落 repo/日志)。
- 触发条件:任一源刷新失败;价格 staleness >3 天 / OI >5 天;兑现覆盖率不足;|周收益| >20%(异常值);
  驱动器异常退出(top-level except → emit 后 re-raise)。

### 2.5 调度(Windows 本机)
`schtasks /Create /SC DAILY /TN QUANTOS_ForwardTrack /TR "<python> tools/forward_track_advance.py" /ST 09:00`

The task polls daily so a delayed public-data watermark is consumed promptly;
the worker still records a decision only after its internal seven-day due gate.
+ 文档写明:错过也无妨,驱动器幂等、下次运行自动补(catch-up 由"距上次决策 ≥7 天"判定驱动)。

## 3. 治理与边界(一寸不让)
- 网络/墙钟只在 `tools/`;`services/` 新增 `forward.py` 纯 stdlib、无网络无墙钟无 RNG——现有治理
  扫描器与 `tests/governance/` 全部照常覆盖,预期零改动、门保持全绿。
- 不触碰:evaluator/晋级门/干跑流水线/五个安全 flag。前向跟踪是**诊断与证据积累**,非晋级门禁;
  是否据此开真钱 = Stage 2 的 Go/No-Go 人工评审。
- `paper_track.db` 老合成 track 保留不删(历史证据),新 track 用真实纪元时间戳 + `FORWARD_LIVE_*`
  source 前缀,天然可区分。

## 4. 测试(先行)
`tests/research/test_forward_track.py`:
1. decision 幂等(同 decided_ts 重录不重复);2. 兑现数学(手算 L/S+资金费对拍);3. IC 对拍;
4. 无前视(min_gap 之内绝不兑现);5. 覆盖率 fail-closed(<0.8 留 pending + alert);
6. 兑现后 summary 与 store 一致;7. ledger 与老表共存(现有 12 条 track 不受扰)。
刷新/驱动器逻辑中可离线测的部分(水位计算、合并、status 生成)注入 fake http_get 测试。

## 5. 非目标(本 Stage)
实盘执行(Stage 1)、venue 适配器、mandate 分级授权、console 页面(端点后补)、carry 之外更多对照边。
