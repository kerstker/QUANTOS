# 现实主义引擎 RE-2:历史情景重演 + 尾部风险 + trend 容量 设计

> L2_DRY_RUN_ONLY,一寸不让。把 RE-1(冲击/容量)之后的现实主义做透:把三条 book 拉过真实历史危机窗口 +
> 量化尾部风险(VaR/CVaR/尾指数/崩盘 beta),并补上 RE-1 欠的 trend 容量。纯离线、读本地库、无网络;诊断非门禁。

## 1. 背景

RE-1 抓到「参与率绑定真容量」的自欺。RE-2 补另一半现实主义:**危机下的行为 + 尾部**。差距报告点名情景重演/
Copula 尾部;RE-1 也欠了 trend 容量。数据实测:carry/crowd(OI/funding)覆盖 **2020-06 → 2026-06(6 年)**,
含真实危机——2021-05 崩盘、2022-05 LUNA、2022-11 FTX、2023-03 银行/USDC 脱锚;trend(daily)仅 1 年,情景重演
样本不足(只算容量 + 标注短样本)。

## 2. 目标 + 预判诚实结论

1. **历史情景重演**:把 carry/crowd 的 book 拉过命名危机窗口 + 各自最差回撤窗口,报窗口内收益/回撤。
2. **尾部风险**:VaR/CVaR(期望损失)+ Hill 尾指数(尾多肥)+ 与 BTC 的下尾依赖(崩盘 beta)。
3. **trend 容量**:补 RE-1 欠项(从 daily 库取 volume 算 ADV)。

**预判(数字为准):** 美元中性 L/S book 对**市场普跌大概率免疫**(风险是截面价差、非 beta),危机窗口可能显示它
扛得住;真正的尾部是**内生价差爆炸**(carry 77% 回撤)。若成立,这是有价值的诚实发现:这些边的尾部风险不是
「市场崩」,是「截面相关性击穿」。数字说什么就是什么。

## 3. 架构

两个纯核(`services/research/realism/`,纯函数、确定性、无 IO/RNG/墙钟)+ 一个 gate 外驱动器。

### 3.1 情景重演核 `services/research/realism/scenario.py`

```python
def window_stats(returns, timestamps, start_ms, end_ms) -> dict:
    """危机窗口 [start,end] 内的 {n_periods, total_return, max_drawdown, worst_period}。窗口无数据 → n_periods=0。"""

def max_drawdown_window(returns, timestamps) -> dict:
    """全程最差回撤的峰→谷 {peak_ts, trough_ts, drawdown, peak_to_trough_periods}(内生最差尾部事件的日期)。"""

def iso_to_ms(iso: str) -> int:
    """'YYYY-MM-DD' → epoch ms(UTC,calendar.timegm,确定性)。"""

# 仅数据覆盖到的真实危机窗口(name, start_iso, end_iso)
CRYPTO_CRISES = [("2021-05 crash", "2021-05-10", "2021-05-31"),
                 ("2022-05 LUNA", "2022-05-05", "2022-05-20"),
                 ("2022-11 FTX", "2022-11-05", "2022-11-21"),
                 ("2023-03 banking/USDC", "2023-03-08", "2023-03-20")]
```

### 3.2 尾部风险核 `services/research/realism/tail_risk.py`

```python
def historical_var_cvar(returns, alpha: float = 0.95) -> dict:
    """历史法:losses=-returns 的 alpha 分位=VaR;超过 VaR 的损失均值=CVaR(期望损失)。{var, cvar, alpha}。"""

def hill_tail_index(returns, tail_q: float = 0.10) -> Optional[float]:
    """Hill 估计损失右尾的尾指数 α̂(越小=尾越肥;正态~∞,幂律 t 分布~自由度)。样本不足 → None。"""

def lower_tail_dependence(x, y, q: float = 0.10) -> Optional[float]:
    """经验下尾依赖 λ_L ≈ P(x<Q_x(q) ∧ y<Q_y(q)) / q ∈ [0,1](x,y 同崩的概率)。长度不齐/太短 → None。"""
```

### 3.3 驱动器 `tools/scenario_tail_research.py`

- 复用 RE-1 的 book 组装(carry: `_carry_panel`;crowd: `build_panel`)得**逐周 gross_returns + timestamps**;
  取 **BTC 逐周收益**作市场代理。
- carry/crowd:每个危机窗口 `window_stats` + `max_drawdown_window` + `historical_var_cvar`(95/99)+
  `hill_tail_index` + `lower_tail_dependence`(vs BTC)。
- trend:从 `crypto_daily.db` 取 volume 算 ADV,复用 `dry_run_trend_multi._legs_and_prices` 逐周权重 → RE-1 `capacity_band`(短样本标注)。
- 落 `data/cache/scenario_tail_snapshot.json`(provenance `REAL_HISTORICAL_REPLAY`)+ 打印每条 book 的危机窗口表现 + 尾部指标 + trend 容量带。

## 4. 数据流

```
book 组装(RE-1 复用)→ gross_returns[周], timestamps[周], btc_returns[周]
  情景:for crisis in CRYPTO_CRISES: window_stats(...) ; max_drawdown_window(...)
  尾部:historical_var_cvar(95/99) ; hill_tail_index ; lower_tail_dependence(book, btc)
  trend:daily volume→ADV + 逐周权重 → capacity_band(短样本)
  -> scenario_tail_snapshot.json + 诚实打印
```

## 5. 安全与约束

- `services/research/realism/{scenario,tail_risk}.py` 纯函数、无 IO/RNG/墙钟、无网络(治理 `test_realism_boundary` 已 glob 整目录,自动覆盖新文件)。
- 驱动器在 `tools/`(读本地库、无网络),不进 `research_runner`。
- 诊断非门禁:不改任何门/CostModel、不驱动晋级/下单、不碰五个 LIVE 标志。
- 诚实:数字为准,不预设结论;窗口无数据/样本不足显式标注。

## 6. 非目标

Copula 全参数拟合(用经验下尾依赖 + Hill 替代,proportionate);2008/2020-03 窗口(crypto 数据 2020-06 起,不覆盖);
console 页(可后续);把尾部/情景接进晋级门。

## 7. 文件清单

- 新建 `services/research/realism/scenario.py`、`tail_risk.py`;`tools/scenario_tail_research.py`。
- 新建 `tests/research/test_scenario.py`、`test_tail_risk.py`。
- 改 `CLAUDE.md`、`docs/QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md`、`docs/PROJECT_STATE.md`、记忆 `realism-engine.md`。

## 8. 自审

- **占位符:** 无 TBD;每核给签名与不变量。
- **一致性:** 情景/尾部纯核 + 驱动器组装分层;危机窗口仅取数据覆盖到的。
- **诚实:** 预判明标「数字为准」;短样本/无数据窗口显式标注;provenance `REAL_HISTORICAL_REPLAY`。
- **范围:** 单一计划可覆盖(2 纯核 + 1 驱动器 + 测试);Copula 全拟合/console/2008 窗口显式划出。
