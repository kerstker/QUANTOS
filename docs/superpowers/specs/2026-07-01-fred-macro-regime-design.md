# FRED 宏观数据 + Regime 条件化 设计(数据方向第一增量)

> L2_DRY_RUN_ONLY,一寸不让。抓真实 FRED **市场型日频宏观序列**(无修正、EOD 可知 → 真 PIT),填进**已建但从未
> 填的** `fundamental_store`,再把新宏观数据接到**已验证的 carry/crowd_contra 边**上——按宏观 regime 分桶看表现
> 差异。fetcher 网络在 `tools/`(出 gate);`services/` 保持无网络;只读研究、无下单。

## 1. 背景与选择

股票免费源在本环境被墙(Stooq 机器人验证 / Yahoo 429,均不绕过);**FRED `fredgraph.csv` 可达**(无 key、无 bot-wall、
深史)。数据基建**已存在**:`fundamental_store.py`(PIT bitemporal 特征库 + BYO-CSV 摄入)、`fundamental_factors.py`。
所以本增量 = **抓 FRED 宏观填 store + 用它条件化现有边**,不重建机器。

## 2. 诚实边界(范围)

- **只抓市场型日频序列**(VIX/利率/斜率/信用利差/美元/实利率)——这些**无修正、EOD 即可知**,`knowledge_time =
  观测日`,真 PIT 无前视。
- **经济发布类(CPI/GDP/就业)显式不碰**:免费 fredgraph 只给**最新修正值**(= 前视)且有发布滞后,PIT-correct 需
  ALFRED vintage 数据——列为诚实待办,不用假装。
- **Regime 标签用全样本分位**=**回顾性描述**(诊断用),不是 PIT 可交易的择时信号——显式标注。若某 regime 里边死了,
  那是真实风险旗标,与 PIT 无关。
- 诊断非门禁:不驱动晋级/下单、不碰五个 LIVE 标志。

## 3. 架构(四子项目)

### FM-1 FRED fetcher + 填 store

`tools/fred_fetch.py`(网络,出 gate):
- 抓 6 条序列(`&cosd=1990-01-01` 强制全史):`VIXCLS`(股票波动)、`DGS10`(10Y)、`T10Y2Y`(2s10s 斜率)、
  `BAMLH0A0HYM2`(高收益 OAS 信用利差)、`DTWEXBGS`(广义美元)、`DFII10`(10Y 实利率)。
- 解析 CSV(跳过 `.` 缺失),逐行写 `SqliteFundamentalStore.add(FeatureValue(feature_id=series_id,
  instrument_id="MACRO", event_time=YYYYMMDD, knowledge_time=YYYYMMDD, value))`(市场序列 EOD 可知 → kt=et,
  满足 kt>=et 无前视)。落 `data/cache/macro_fred.db`。
- 打印每序列 rows/added/span;fail-closed(网络失败/空 → 非零退出、不写半截)。

### FM-2 宏观 regime 分类器 `services/research/macro_regime.py`(纯函数)

```python
def tercile_labels(values: Sequence[float]) -> list[str]:
    """全样本三分位 → ['low'|'mid'|'high'](回顾性描述,非 PIT 择时)。"""

def sign_regime(value: float, *, invert_label=("inverted","normal")) -> str:
    """符号 regime(如 2s10s<0=inverted 倒挂 / >=0=normal)。PIT(单点符号)。"""

def bucket_returns(returns, labels) -> dict:
    """按 label 分桶 → {label: {n, ann_sharpe, mean_return, total_return}}。纯确定性。"""
```

### FM-3 regime 条件化边分析 `tools/macro_regime_research.py`

- 复用 RE 的 carry/crowd_contra 逐周 `(gross_returns, timestamps)`(从 `scenario_tail_research._book_series` /
  `capacity_research` 复用组装)。
- 逐周把 ts(epoch ms)→ YYYYMMDD,`store.latest_as_of(series,"MACRO",yyyymmdd)` 取**该周可知**的宏观值(PIT)。
- 三个 regime 维:`vol`(VIX 三分位)、`rates`(T10Y2Y 符号:倒挂/正常)、`credit`(HY OAS 三分位)。
- 每条边 × 每维:`bucket_returns` → 每桶 {n, 年化夏普, 均收益}。落 `data/cache/macro_regime_snapshot.json` +
  打印诚实结论(边在哪些 regime 强/弱/死)。

### FM-4 治理 + 全门绿 + 文档

- 治理:`tools/fred_fetch.py` 允许网络(gate 外);`services/research/macro_regime.py` 纯函数无网络
  (治理已有 services 全树无网络不变量覆盖)。加 `tests/research/test_macro_regime.py`。
- 记结论:CLAUDE.md、gap 文档(数据缺口部分闭合:宏观已接)、PROJECT_STATE、记忆。

## 4. 数据流

```
FRED fredgraph.csv ──(tools/fred_fetch,网络)──> SqliteFundamentalStore(macro_fred.db, kt=et 无前视)
carry/crowd 逐周(gross,ts)──ts→YYYYMMDD──> store.latest_as_of(series, "MACRO", 该周) (PIT)
  → regime 标签(VIX 三分位 / 2s10s 符号 / HY OAS 三分位)
  → bucket_returns:每桶年化夏普/均收益 → macro_regime_snapshot.json + 诚实结论
```

## 5. 安全与约束

- `tools/fred_fetch.py` 网络在 gate 外(同 crypto fetcher);`services/research/macro_regime.py` 纯函数、无网络/IO/RNG/墙钟。
- fetcher fail-closed:HTTP 非 200 / 空 body / 全 `.` → raise、非零退出,不写半截库。
- 只读研究诊断;不驱动晋级/下单;不碰 LIVE 标志。
- Regime 标签回顾性(全样本分位),显式标注非 PIT 择时。

## 6. 非目标

经济发布类序列(需 ALFRED vintage);宏观**时序信号**当可交易策略(无 Treasury/ETF 可交易标的);expanding-window
PIT regime(用全样本分位诊断,标注);console 页(可后续)。

## 7. 文件清单

- 新建 `tools/fred_fetch.py`、`services/research/macro_regime.py`、`tools/macro_regime_research.py`、
  `tests/research/test_macro_regime.py`。
- 改 CLAUDE.md、`docs/QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md`、PROJECT_STATE、记忆(新 `macro-regime.md`)。

## 8. 自审

- **占位符:** 无 TBD;FM-2 给签名与不变量;FM-1/3 给明确抓取/组装指令。
- **一致性:** 「市场序列 kt=et 无前视」在 §2/§3.1/§4 一致;regime 回顾性标注在 §2/§3.2/§5 一致。
- **诚实:** 经济发布类 + PIT regime 显式划出;fetcher fail-closed;数字为准。
- **范围:** 单一计划可覆盖(1 fetcher + 1 纯核 + 1 驱动器 + 测试);ALFRED/时序信号/console 显式非目标。
