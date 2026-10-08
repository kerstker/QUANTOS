# 2026-07-06 — IC 面板 per-factor 持有期口径 + ALFRED vintage 宏观数据(设计 + 实施结果)

> 战线④(研究数据面增量)。设计经对抗式评审(NEEDS_CHANGES → 7 条 required_changes 全部落实,
> 逐条对照见 §5)。诊断非门禁:本战线所有产出不驱动任何自动晋级/下单,不碰 evaluator/AlphaCatalog/
> 风控门/五个真钱旗帜;`live/` 树零接触。readiness 仍是 L2_DRY_RUN_ONLY。

## 0. 动机

- **IC 面板持有期口径**:crowd_contra 日频面板 t=0.66 vs 周度重算 t=2.99 的"持有期口径效应"
  (机构级补全 ⑨ 的诚实性核查发现)一直是面板的已知盲区——慢速持仓类因子在 h=1 日频口径下被
  系统性低估,横向比较失真。此前只能靠单独脚本重算,现在把多持有期口径做进面板本身。
- **ALFRED vintage**:宏观 regime 方向(FRED 六条市场型日频序列)当时刻意不抓经济发布类
  (CPI/GDP)——免费 fredgraph 只给**最新修正值**,拿它当"当时可知"是前视。ALFRED
  (ArchivaL FRED)给每个观测的全部历史 vintage(`realtime_start` = 真实首次可知日),
  才能诚实地做通胀/就业 regime 维。

## 1. IC 面板 per-factor 持有期(已落地)

### 1.1 纯核(`services/research/ic_panel.py`,只加不改)

`compute_period_ic_series` **一字不动**(既有快照数字/交叉核对可复现性是资产,evidence/ic_panel.json
数字引用它)。新增:

```python
def compute_horizon_ic_series(signal_matrix, price_matrix, horizon) -> (ics, start_indices)
```

- 起点 t 的 IC = corr_rank(signal[t], price[t+h]/price[t]−1),内层配对/过滤/秩相关与 h=1 路径
  同一套浮点运算顺序 → horizon=1 时与 `compute_period_ic_series` 逐元素**逐位**相等(测试钉死)。
- 抽样 = **非重叠**(t=0,h,2h,…,t+h<T)。这是唯一诚实口径:重叠收益让 IC 序列自相关、naive
  t-stat 虚高。**评审拍板:不留 stride 参数**(重叠口径是负资产;Newey-West 因复杂度和可审计性
  被否;`inspect.signature` 测试钉死参数集)。
- 抽样起点固定 0 → 相位依赖(确定性、已知局限,docstring 标注)。
- "持有期"单位 = 面板自身周期(当前四宇宙全日频,h=7≈周度),不是绝对日历天。
- 每期有效对 <4 仍记 None;tie 处理沿用 `_rank` 的既有简化。

### 1.2 注册表(`tools/ic_panel_registry.py`)

可选键 `"holding_periods"`,缺省 `[1]`(完全向后兼容)。取值拍板:慢速持仓类
(carry_composite / crowd_contra / funding_contra_level / funding_contra_delta / oi_ls_delta_contra)
与趋势类(trend_composite / dist_from_high_14)= `[1,7,14]`;336 币宽宇宙三条
(momentum_7_wide / low_vol_wide / reversal_7_wide)= `[1,7]` 省算力。

### 1.3 驱动器 + 快照 schema v2(`tools/ic_panel_research.py`)

主循环抽成可单测的纯组装函数 `factor_row(entry, panel)`:

- h=1 走 `compute_period_ic_series` **旧函数、旧调用形态**(字节级复现);h>1 走
  `compute_horizon_ic_series`,per_year 用 `start_indices` 对齐 event_times。
- row 顶层六统计 + per_year **恒 == by_period["1"]**(向后兼容锚,测试钉死);注册表条目漏写 1
  也强制补上(顶层锚必须存在)。
- 快照顶层加 `"schema_version": 2`;row 加 `holding_periods` + `by_period`(键=str(h))。
  **只加字段,不删不改既有字段**;console_api `_ic_panel_payload` rows 原样透传 → 后端零改动。

### 1.4 h=1 逐位一致的可执行检测点(评审 required_change #3)

`tools/ic_panel_h1_parity_check.py`:对齐 (name, universe) 后,逐行比较顶层六统计
(mean_ic/ic_std/icir/hit_rate/t_stat/n_periods)+ per_year,**逐位相等**(JSON 解码后 `==`,
零容差),漂移即非零退出逐项列出。

**正确用法(本次实际执行的流程)**:先备份任务前快照 → 用**旧代码对当前数据**跑一份基线
(`ic_panel_snapshot.h1_baseline.json`;实测与任务前快照 0 漂移,数据未动)→ 改代码 → 新驱动器
重跑 → `python -m tools.ic_panel_h1_parity_check <基线> <新快照>`。两跑之间不刷新任何数据,
把"数据漂移"从"代码漂移"里隔离出去。**本次结果:H1 PARITY OK,10 行逐位一致。**

### 1.5 契约与前端(同 commit,评审 required_change #6)

- `tests/research/test_console_api_contract.py` 新增 `test_ic_panel_by_period_schema_v2_consistency`:
  **绑定 schema_version**(快照存在且 `schema_version>=2` 时必须有 holding_periods/by_period 且
  by_period["1"] == 顶层;CI 无快照诚实 skip;本地 v2 快照已生成 → 断言实际生效),消除"本地旧
  快照静默跳过"窗口。ic-panel 顶层契约 keys 不变。
- `research.ts`:抽 `IcPanelAgg` 接口,`IcPanelRow extends IcPanelAgg` 加**可选**
  `holding_periods?/by_period?`(旧快照/mock 不炸,TS strict 过)。
- `ICPanel.tsx`:主表保持 h=1 六列(跨因子可比性是主表职责)+ 紧凑"多期 t"列
  (`h1:0.66 / h7:2.91 / h14:3.35`,缺失显示 —);"逐年"展开升级为持有期详情面板
  (per-period 表,行=h,可点选;下半 = 选中 h 的逐年 chips,默认 h=1,旧快照兜底顶层 per_year)。

### 1.6 首跑真实发现(2026-07-06,与既有结论互证)

| 因子 | h1 t | h7 t | h14 t | 解读 |
|---|---|---|---|---|
| crowd_contra | 0.66 | **2.91** | **3.35** | 持有期口径效应在面板内直接复现(此前周度重算 t=2.99 的系统化版) |
| carry_composite | 0.19 | **2.75** | **2.74** | 新信息:日频价格 IC≈0,但周度口径出现显著价格 IC(慢速因子同款口径效应) |
| funding_contra_level | −0.00 | 2.19 | 2.29 | 同上,资金费水平反向在周度口径有信号迹象 |
| momentum_7_wide | −16.36 | −5.13 | — | 日频巨 t 到 h7 缩到 ~1/3(与"微观结构反弹、持有不过夜就没了"的 gauntlet 结论一致) |
| reversal_7_wide | 16.36 | 5.13 | — | 同上(镜像) |
| low_vol_wide | 15.72 | 8.17 | — | 周度仍大,但 gauntlet 已证无净-成本可交易边——IC 大 ≠ 可交易 |
| dist_from_high_14 | 2.36 | 1.93 | 3.27 | 趋势类横向参考 |
| trend_composite | 1.23 | 1.30 | 1.48 | 无显著变化 |
| funding_contra_delta | 1.11 | 0.55 | −0.27 | 快变量在长持有期衰减(方向符合直觉) |
| oi_ls_delta_contra | −2.02 | −0.78 | 0.20 | 同上 |

诚实边界:h>1 非重叠 → n 缩 ≈ n/h(h14 的逐年桶部分落到 n<10 全 None,诚实稀疏);IC 显著 ≠
可交易(low_vol/reversal 已被 gauntlet 打掉);**可部署 book 不变(crowd_contra 单条)**——
carry h7 价格 IC 是否值得送 wide_factor_gauntlet 复检,是另一个独立决策。

## 2. ALFRED vintage 数据(纯核+工具+测试已落地;真实抓取待 key)

### 2.1 可达性与 key(2026-07-06 本环境实测)

- `api.stlouisfed.org/fred/series/observations` **可达、秒回**;无 key → HTTP 400
  "Variable api_key is not set"。**必须免费 API key**(https://fredaccount.stlouisfed.org/apikeys)。
- 本环境 `FRED_API_KEY` 未设、`key/fred_api_key.txt` 不存在 → **真实 vintage 抓取本次未执行**
  (诚实降级,不造假数据)。key 就位后一条命令即可:`python -m tools.alfred_fetch`。
- **不设 fredgraph 兜底**(评审+设计一致拍板):免费 CSV 只给最新修正值,冒充 vintage = 前视,
  宁缺毋滥。

### 2.2 `tools/alfred_fetch.py`(网络只在 tools/,治理边界已钉)

- 单请求全 vintage 形态:`?series_id=…&realtime_start=1776-07-04&realtime_end=9999-12-31
  &limit=100000&offset=N`;`count` 分页(CPIAUCSL 季调重估每年改写全历史,必超一页),装配行数
  与 count 不齐 → raise(宁失败勿半库)。
- `parse_observations` 纯函数:`value=="."` = 缺失跳过;值不可解析 / `realtime_start<date`
  (理论不可能,防御)→ rejected 计数不入库。
- 入库:`FeatureValue(feature_id=series, instrument_id="MACRO", event_time=date,
  knowledge_time=realtime_start)` → 独立 `data/cache/macro_alfred.db`(不混 macro_fred.db——
  那个库 drop 重建且 knowledge=event 语义不同);复用 SqliteFundamentalStore 原 schema,
  restatement 天然保留,零迁移。FeatureValue 契约拒 lookahead → contract-rejected 计数上报,
  **绝不悄悄改时间**。
- **key 泄漏面(评审 required_change #2)**:key 只从 `FRED_API_KEY` env 或 gitignored
  `key/fred_api_key.txt` 读;缺失/空白 → 诚实说明 + `sys.exit(1)`、**零网络请求**(fake transport
  断言零调用)。key 以 query 参数进 URL → 所有异常路径 `raise RuntimeError(...) from None`,
  消息**只带 series_id+offset+状态码,绝不带完整 URL**;测试钉死 fake transport 抛错(消息内嵌
  含 key 的 URL)时,异常消息与 stdout/stderr 均不含 key、原异常链被切断。
- 时间戳约定:yyyymmdd int(macro 库既有约定,沿用 fred_fetch;非 temporal.py docstring 的
  epoch-µs 原义——BitemporalRecord 只强制 knowledge>=event 的序关系,与单位无关,库内自洽)。
- ALFRED 档案起点前的老观测 realtime_start=档案首 vintage 日(晚于真实发布日 = 保守方向,
  PIT 安全;对 crypto 样本 2020+ 无实际影响)。
- **不进 research_runner 白名单**:带网络+key 的抓取器不给前端触发面(runner 保持最小研究面)。

### 2.3 PIT 求值纯核(`services/research/macro_regime.py`,纯函数无 IO)

- `pit_latest_asof(rows, as_of)` / `pit_yoy_asof(rows, as_of, months_back=12)`:逐点语义规范。
  只用 knowledge<=as_of 的行;v0 = 最新 event 的最新可知值;YoY 基期按 yyyymm 月减(跨年安全;
  GDPC1 季频传 12 落在去年同季,**有季频 fixture 测试**,不再是口头断言)。跨 vintage 混用是
  特性——每个值都是 as_of 时点研究员真实拥有的最佳已知值。
- `pit_series(rows, as_ofs, months_back=None)`:**调用侧确定性能策略(评审 required_change #5,
  选"预索引"而非"文档化接受分钟级成本")**——as_ofs 必须升序(违反 → ValueError fail-closed),
  rows 只做一次 (knowledge, event) 排序 + 单调游标增量索引,O(R log R + R + W);对照:逐周调
  逐点函数是 O(W×R),CPIAUCSL 全 vintage >10 万行 × ~230-300 周 = 数千万次元组过滤 = 分钟级。
  与逐点函数的语义等价性有测试钉死。确定性:排序键完全有序、无 RNG/墙钟。

### 2.4 宏观 regime 增量(`tools/macro_regime_research.py`,不另起快照)

- 新维度 `inflation_CPI_yoy`(pit_series YoY → tercile_labels)+ `labor_UNRATE`(pit_series
  水平 → tercile_labels),与既有 vol_VIX/rates_2s10s/credit_HYOAS 并列进 `dims`(console
  research-findings 契约只锁顶层 keys,后端零改动)。GDPC1 第一批**只入库不分桶**(季频 × 6 年
  crypto 样本 ≈ 24 期,分桶无统计意义)。
- **fail-soft(已真实验证)**:macro_alfred.db 不存在/坏/序列空 → 两维整体缺席 + snapshot
  `alfred_present:false` + note 诚实标注"未抓取、诚实跳过",绝不 raise、**绝不用 macro_fred.db
  最新修正值冒充 vintage**。
- PIT 边界(note+本文档双标注):①输入值真 PIT(发布前不可见、修正只在 realtime_start 后可见);
  ②分位标签仍全样本回顾性(与 VIX 维口径一致,非 PIT 择时;要择时得换 expanding_tercile_labels,
  非本战线目标);③月频标签黏滞 ~4 周 → 桶内周收益不独立,只报描述统计;④crypto 样本只覆盖一轮
  通胀周期(2021-22 冲高 + 2023-25 回落),n=1,不得当择时依据。

## 3. 治理与边界

- 网络 import 只出现在 `tools/alfred_fetch.py`;`services/` 只加纯函数
  (`test_services_tree_has_no_network_imports` 自动守住)。
- `tests/governance/test_crypto_data_boundary.py`:`_FETCHERS` 加 `tools/alfred_fetch.py`,
  并**补钉既有缺口** `tools/fred_fetch.py`(带网络却一直不在名单;评审 required_change #1+#7:
  `_ALLOWED_FETCHER` 同时加 `csv`(fred_fetch 的纯 stdlib CSV 解析)与 `quantos`(内部契约包
  FeatureValue,两个 fetcher 都用)——注释写明"不增加网络/交易/凭证能力"。**fred_fetch 补钉
  建议拆独立 commit**(见 §6)。
- 新字段名(holding_periods/by_period/schema_version/realtime_start/pit_series/alfred_present…)
  已对照治理扫描器 FORBIDDEN_APIS/FORBIDDEN_FLAGS/_LIVE_FLAGS/FORBIDDEN_CREDENTIALS 字面量清单
  核查,无碰撞(扫描器本身零改动;alfred_fetch 在 tools/,本就不在 SCAN_SUBDIRS)。
- key/ 目录已有 gitignore 约定(`key/` 整目录忽略),fred_api_key.txt 落位即受保护。

## 4. 测试地图(本战线新增 33 个)

- `tests/research/test_ic_panel.py` +11:horizon 语义/h=1 逐位锚/非重叠抽样/无 stride 参数
  (inspect.signature)/阈值语义/坏价格/per_year 对齐/factor_row 缺省/顶层==by_period["1"]/
  强制 h=1/注册表 holding_periods 合法性。
- `tests/data/test_alfred_fetch.py` +13(全 fake transport 不出网):parse 语义(knowledge=
  realtime_start/"."/lookahead/不可解析)/restatement PIT 可见性/契约拒收/分页装配+mismatch
  raise/缺 key fail-closed 零请求(SystemExit code=1)/空白 key/三条 key 不泄漏(transport 异常/
  HTTP 状态/非 JSON)/main 端到端落库。
- `tests/research/test_macro_regime.py` +8:pit_yoy 只用可知行(晚知修正前后)/缺基期 None/
  全不可知 None/季频 GDPC1/pit_latest restatement/pit_series 与逐点等价/非升序 ValueError/
  驱动器无 ALFRED 库 fail-soft。
- `tests/research/test_console_api_contract.py` +1:schema v2 一致性(绑定 schema_version)。
- 治理:既有 3 条边界测试自动覆盖两个新钉住的 fetcher(不计新增)。

## 5. 评审 required_changes 落实对照

| # | 要求 | 落实 |
|---|---|---|
| 1 | _ALLOWED_FETCHER 加 'quantos'(+'csv') | 已加,注释写明"内部契约包/纯 stdlib 解析,不增加网络/交易/凭证能力" |
| 2 | alfred 异常路径 URL 脱敏 + key 不泄漏测试 | `_err()` 模板只带 series+offset+状态码;`from None` 切断异常链;3 条泄漏测试 |
| 3 | h=1 逐位一致的可执行检测点 | `tools/ic_panel_h1_parity_check.py` + 基线快照流程;实测 10 行 PARITY OK |
| 4 | stride 参数二选一 | **删**(非重叠是唯一口径);`inspect.signature` 测试钉死 |
| 5 | pit_yoy 调用侧性能策略写进设计 | `pit_series` 单调游标预索引(§2.3),等价性测试钉死 |
| 6 | 契约断言与快照重跑同 commit / 绑 schema_version | 两者都做:断言绑定 schema_version>=2,且 v2 快照本机已生成使断言实际生效 |
| 7 | fred_fetch 补 _FETCHERS 拆独立说明 | §6;实现在同一工作树,commit 时单独拆 |

## 6. 建议 commit 拆分(实施者不 commit,留给人工核验)

1. **governance(补钉既有缺口)**:`tests/governance/test_crypto_data_boundary.py` 中
   `fred_fetch.py` 进 `_FETCHERS` + `_ALLOWED_FETCHER` 加 `csv`/`quantos` —— 独立 commit,
   消息写明是补钉存量缺口(fred_fetch 自诞生起带网络却不受只读数据边界测试约束)。
2. **IC 面板持有期**:ic_panel.py + 注册表 + 驱动器 + parity 脚本 + 契约测试 + 前端 + 快照重跑。
3. **ALFRED vintage**:alfred_fetch.py(+ `_FETCHERS` 加行)+ macro_regime 纯函数 + 驱动器增量
   + 测试。

## 7. 显式待办 / 非目标

- **待 key**:用户注册免费 FRED API key → `key/fred_api_key.txt` → `python -m tools.alfred_fetch`
  (预计 CPIAUCSL 全 vintage >10 万行,库体积数十 MB,首跑分钟级)→ 重跑
  `python -m tools.macro_regime_research` 看通胀/就业分桶首跑数字。
- carry h7 价格 IC(t≈2.75)是否送 wide_factor_gauntlet 净-成本复检 = 独立决策,本战线不擅动。
- GDPC1 分桶(等更长样本/股票宇宙)、经济发布惊奇度(actual vs 共识,需另一数据源)、
  expanding PIT 分位择时 = 非目标。
- 面板 per-factor 持有期不改变任何晋级/部署决策;可部署 book 仍是 crowd_contra 单条。
