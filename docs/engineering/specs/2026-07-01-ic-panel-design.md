# 截面 IC 面板 —— 设计 spec

> 状态:设计已与用户确认(2026-07-01,方案 A)。下一步:writing-plans。
> 约定:中文叙述,代码标识符英文。安全边界见 §6,不可让。
> 由来:轨道2(数据基建)brainstorm 中途发现前提过时——数据广度已经不小(Binance 336 币 6 年、
> FX 46 种 27 年),且已有一个被搁置的候选信号 `crowd_contra`(持仓反向,对 carry 增量 IR 1.13,
> 过 4/5 项证伪检验)。用户选定轨道2 的真正产出:**通用截面 IC 诊断面板**(任意因子集 × 任意宇宙,
> 方便以后新候选都过一遍),而非盲目再抓更多加密数据。

## 1. 目标(一句话)

把散落在 `tools/crypto_oi_research.py`(`_rank_ic`)等驱动器里的"逐期截面 rank IC"临时代码,提炼成
**一个可复用模块 + 一份可扩展的命名因子注册表 + 一个离线快照驱动器 + 一个 console 页**,让"新候选因子
先过一遍截面 IC 诊断"成为系统里的一个标准能力,而不是每次现写一段脚本。

## 2. 已确认的关键决策

- **用途**:通用研究诊断工具(任意已注册因子 × 任意已注册宇宙的对比表),不是单为 carry/crowd_contra
  做的一次性视图,虽然这两个已验证信号会作为面板的首批条目。
- **不是晋级关卡**:面板只做"初筛/横向对比",不取代 `services/alpha/evaluator`(DSR/PBO/walk-forward)
  那套真正的晋级门禁。面板结果不会、也不能让任何东西自动晋级。
- **展现形态**:离线快照 + console 页(跟现有 31+ 端点/页同一模式——驱动器算好写 JSON,端点读快照
  fail-closed,契约测试,前端表格/热图)。**不做**在线现算/自由文本表达式输入(方案 B,范围/风险都更大,
  且是系统里第一个"实时算"页,打破既有的"读快照"模式)。
- **IC 口径**:统一用**逐期截面 rank IC**(信号在 t 期的截面排名 vs 价格从 t 到 t+1 的收益的排名相关),
  跟 `crypto_oi_research.py::_rank_ic` 现有口径一致;不混入 funding/OI 现金流的"经济收益"(那是 carry
  验证驱动器自己的关切,不在这个面板里重复)。

## 3. 现有可复用的真零件

- `services/research/alpha_expression.py::build_field_panel(store, instruments, event_times, fields) ->
  FieldPanel`、`evaluate(expr, panel) -> Matrix`——因子表达式 DSL 求值引擎,carry/crowd_contra/trend 三个
  已验证信号全部是这套 DSL 的元组(如 `("cs_rank", ("neg", ("field", "global_ls")))`)。
- `tools/crypto_oi_research.py::_rank_ic(sig, px)`——**待泛化对象**:已经是正确的"逐期截面 Spearman rank
  IC 取均值"逻辑,只是(a)私有、(b)只回均值不回序列、(c)散落单个驱动器里。
- `tools/crypto_carry_strategy.py::COMPOSITE`、`tools/crypto_oi_verify.py::CROWD_CONTRA`、
  `tools/crypto_daily_composite.py::COMPOSITE`——三个已验证因子的**现成 DSL 表达式**,直接复用,不重写。
- 各驱动器已有的"建面板"配方(`crypto_carry_multi`/`dry_run_carry_multi` 的 funding 拼接、
  `crypto_oi_research.py::build_panel` 的 OI 字段拼接)——面板驱动器的每个宇宙加载函数照抄这些配方。

## 4. 组件与接口

### 4.1 `services/research/ic_panel.py`(新模块,纯函数,无 IO)
```
compute_period_ic_series(signal_matrix: list[list[float|None]],
                         price_matrix: list[list[float|None]]) -> list[float|None]
```
逐期 t(到 len-1 为止):取该期非 None 的 (signal[t][i], price[t+1][i]/price[t][i]-1) 对,少于 4 对该期记
`None`;否则算 Spearman rank correlation(内联一个小型 `_rank` 竞赛排名,复用 `_rank_ic` 的实现思路,不跨
模块 import `factor_engine._rank` 私有符号)。返回**逐期序列**(不是均值)——这是对 `_rank_ic` 的泛化:
均值只是调用方对这个序列做一次聚合。

```
aggregate_ic_series(ics: list[float|None], event_times: list[int] | None = None) -> dict
```
过滤 None 后:`{"mean_ic": ..., "ic_std": ..., "icir": mean/std, "hit_rate": 同号占比,
"t_stat": mean/(std/sqrt(n)), "n_periods": n}`;若给 `event_times`,加 `"per_year": {年份: 该年 mean_ic}`。
样本不足(n<10)时全部字段为 `None`(诚实,不硬凑)。

### 4.2 `FACTOR_REGISTRY` + `UNIVERSE_REGISTRY`(同一模块或 `ic_panel_registry.py`,声明式)
`UNIVERSE_REGISTRY`:name → 一个"如何建面板"的加载器函数(db 路径 + 实例枚举 + 需要拼接的额外字段,照抄
对应驱动器现有配方)。首批 4 个:
- `crypto_funding`(115 币,`crypto_funding.db` + funding 字段拼接,carry 用)
- `crypto_oi_majors`(14 大币,`crypto_funding.db` 子集 + `crypto_oi_metrics.json` OI 字段拼接——照抄
  `tools/crypto_oi_research.py::build_panel` 的加载配方,crowd_contra 用)
- `crypto_daily`(18 币,`crypto_daily.db`,trend 用)
- `crypto_binance_wide`(336 币,`crypto_binance.db`,宽宇宙基线对照用)

`FACTOR_REGISTRY`:`(name, expr, universe, thesis)` 列表,首批:
- `carry_composite`(`tools.crypto_carry_strategy.COMPOSITE`, `crypto_funding`)
- `crowd_contra`(`tools.crypto_oi_verify.CROWD_CONTRA`, `crypto_oi_majors`)
- `trend_composite`(`tools.crypto_daily_composite.COMPOSITE`, `crypto_daily`)
- `dist_from_high_14`(单因子基线, `crypto_daily`)
- `momentum_7_wide`(单因子基线, `crypto_binance_wide`,对照"宽宇宙价格类因子仍无 edge"的既有结论)

新候选出现时:加一行 `FACTOR_REGISTRY` 条目(如需新宇宙,先加一行 `UNIVERSE_REGISTRY`),重跑驱动器
即可——这是"以后新候选都过一遍"的具体落地方式。

### 4.3 驱动器 `tools/ic_panel_research.py`
逐 `(name, expr, universe, thesis)`:按 `universe` 加载器建面板 → `ax.evaluate(expr, panel)` 得信号矩阵 →
`compute_period_ic_series` + `aggregate_ic_series`(含 per_year)→ 收集一行结果。写
`data/cache/ic_panel_snapshot.json`:`{"generated_at", "rows": [{"name", "universe", "thesis", "n_instruments",
"n_periods", ...aggregate_ic_series 的字段}], "universes": {name: {"n_instruments", "date_range"}}}`。

### 4.4 console:端点 + 契约 + 页
- `services/research/console_api/main.py` 加 `_ic_panel_payload()`:读快照,fail-closed 到
  `SYNTHETIC_DETERMINISTIC`(无快照/CI),真时 `data_provenance="REAL_IC_PANEL_OFFLINE_SNAPSHOT"`。
  端点 `/api/v1/console/ic-panel`。
- `tests/research/test_console_api_contract.py` 注册 keys(`snapshot_present, verdict, rows, universes`)+
  provenance 集合。
- 前端 `apps/research-console/src/pages/ICPanel.tsx`:表格(因子 × 行:name/universe/mean_ic/icir/hit_rate/
  t_stat/n_periods)+ 一个纯 CSS/SVG 的 IC 强度小色块(红绿映射,不用新依赖)+ 点开某行看 per_year 小趋势;
  诚实标注"初筛诊断,非晋级门禁——真正的门禁是 evaluator 的 DSR/PBO/walk-forward"。type +
  api client + 导航(「研究」分组下,靠近 Alpha Zoo/DSL Explorer)。

## 5. 数据流
命名因子 + 命名宇宙(注册表)→ 建面板(复用 alpha_expression)→ 逐期截面 rank IC 序列 → 聚合统计(mean/
ICIR/hit-rate/t-stat/per-year)→ 快照落盘 → console 端点读快照 → 页面表格/热图。

## 6. 容器化 / 安全边界(不可让)

- 面板驱动器 `tools/ic_panel_research.py` **只读本地库**(`crypto_funding.db`/`crypto_binance.db`/
  `crypto_daily.db` + 已缓存的 JSON),**无网络**;`services/research/ic_panel.py` 是纯函数、无 IO、
  无 RNG、无墙钟。
- console 端点只读、fail-closed;**面板结果不驱动任何自动晋级**——不写 `experiment_registry`、不碰
  `AlphaCatalog`/`AlphaLifecycleManager`,纯诊断展示。
- 不改变现有 evaluator/晋级链路;`services/` 无网络 import 不变;不进 research_runner。

## 7. 测试(TDD,门绿)

- `services/research/ic_panel.py`:`compute_period_ic_series`(已知合成序列验证方向和数值、样本不足期记
  None)、`aggregate_ic_series`(mean/std/icir/hit_rate/t_stat 已知输入已知输出、n<10 全 None、per_year 分组
  正确)。
- 驱动器:import 检查 + 真跑一次(生成快照,carry_composite/crowd_contra 的 mean_ic 应为正、与已有
  `crypto_oi_verify.json` 里的 ic≈0.03 量级一致——交叉验证泛化后的实现没有引入偏差)。
- console:契约测试注册;typecheck/build;浏览器验证表格渲染真数据、零控制台错误。
- `scripts/validate.py` 全绿。

## 8. 非目标(YAGNI)

- 不做在线/参数化现算(方案 B);不做自由文本表达式输入。
- 不重新验证 carry/crowd_contra 的完整证伪链条(那些已经做过,面板只是把 IC 这一项通用地摆出来对比)。
- 不新增数据源/新抓取器(那是"轨道2 原始设想"里已经不成立的部分)。
- 不接入晋级/paper-track 自动化;`crowd_contra` 是否真的送入纸面追踪+干跑,是另一个独立决策(面板只是
  让"要不要"这件事更容易判断,不代替判断)。
- FX(46 种)暂不入首批宇宙注册表(当前因子注册表都是加密专属信号;FX 加入只需未来加一行宇宙+因子)。
