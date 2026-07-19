# 现实主义引擎 RE-1:市场冲击 + 容量衰减曲线 设计

> L2_DRY_RUN_ONLY,一寸不让。把回测/干跑的**成本与容量**从「未校准、默认关闭的 sqrt stub」升级到**学术级
> 冲击模型 + 容量衰减曲线**,并应用到已有的 carry/crowd_contra/trend 三条干跑 book——回答「这些 edge 能扛
> 多大规模」。纯离线、读本地库、无网络、无真单;冲击/容量只是研究诊断,不驱动任何晋级/下单。

## 1. 背景与缺口

差距报告([`QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md`](../../QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md))把「现实主义」列为**特别重要**:
现实约束太松 × 关卡会抓海市蜃楼 = 无 edge 的信号在组合层看起来可部署——这是最阴险的自欺,而本项目存在的意义
就是防它。当前状态:

- `services/research/backtest_engine/costs.py::CostModel.impact_bps` —— sqrt 冲击,**`impact_coef=0` 默认关闭**、
  **从未用真实成交校准**。
- `services/alpha/capacity.py` —— 有参与率上限 + 冲击预算上限 + 容量折扣夏普,但只是**单点** helper,
  冲击系数为 0 时退化成只有参与率上限,且**从未应用到真实的干跑 book**。
- carry 驱动器自己注释:「$1M keeps slippage realistic; **at $100M real market impact rises — that's the separate
  capacity analysis, not modelled here**」——缺口是驱动器自己标注的。

## 2. 目标

1. 建一个**学术级冲击模型**(Almgren-Chriss 式:临时 + 永久冲击、参与率驱动),取代未校准 stub。
2. 建**容量衰减曲线**:给定一条 book 的逐期换手 + 每名 ADV,扫 AUM 网格 → 每档净冲击后的收益/夏普/回撤 →
   找 edge 归零的临界 AUM。
3. **应用到真实的 carry/crowd_contra/trend 干跑 book**,落 `capacity_snapshot.json`,如实记录每条边的容量。

## 3. 诚实核心决定(非目标里的关键)

**L2 无 tick/真成交,冲击系数无法校准。** 所以**绝不报一个假装精确的单点容量数**。做法:

- 冲击模型参数取**文献标准**(临时冲击指数 α≈0.5、永久冲击线性),但把**冲击系数 c 当作扫描维**,
  c ∈ [保守, 激进] 一个合理区间;
- 容量以**区间带**呈现:「在 c 的合理区间内,edge 在 $X–$Y AUM 之间归零」;
- 所有输出显式标注 `UNCALIBRATED_PRINCIPLED`(有原理、无真实成交校准),与项目一贯的诚实 provenance 一致。

## 4. 架构

新建 `services/research/realism/`(纯函数、确定性、无 IO/RNG/墙钟)。

### 4.1 冲击模型(第 1 块)`services/research/realism/impact.py`

```python
@dataclass(frozen=True)
class AlmgrenChrissImpact:
    temp_coef: float = 10.0       # 临时冲击系数 η(bps);作用于本次参与率
    perm_coef: float = 5.0        # 永久冲击系数 γ(bps);作用于本次持仓占 ADV
    temp_exponent: float = 0.5    # 临时冲击指数 α(平方根律,文献标准 0.5–0.6)

    def temporary_bps(self, trade_notional: float, adv_notional: float) -> float:
        """临时冲击(bps):η·(|trade|/ADV)^α。撮合当时的滑点,只作用于这一笔。"""
    def permanent_bps(self, trade_notional: float, adv_notional: float) -> float:
        """永久冲击(bps):γ·(|trade|/ADV)。把价格推走、留在市场里。"""
    def total_cost_notional(self, trade_notional: float, adv_notional: float) -> float:
        """一笔交易的总冲击成本($)= (temp+perm) bps × |trade|。ADV<=0 或 trade=0 → 0(fail-safe)。"""
```

### 4.2 容量衰减曲线(第 2 块)`services/research/realism/capacity_curve.py`

```python
def net_return_series_at_aum(
    gross_returns: Sequence[float],                       # 逐期毛收益(book 层,未扣冲击)
    turnover_by_period: Sequence[list[tuple[float, float]]],  # 每期 [(|Δnotional_frac|, adv_notional_i), ...] per name
    aum: float, impact: AlmgrenChrissImpact) -> list[float]:
    """逐期:冲击成本 = Σ_i impact.total_cost_notional(frac_i*aum, adv_i);净收益 = 毛 - 冲击/aum。"""

def capacity_curve(gross_returns, turnover_by_period, adv_meta, aum_grid, impact,
                   ppy: int = 52) -> list[dict]:
    """扫 AUM 网格 → 每档 {aum, net_total_return, net_sharpe, net_max_drawdown}。纯确定性。"""

def critical_aum(curve: list[dict], sharpe_floor: float = 0.3) -> Optional[float]:
    """净夏普首次跌破 floor 的 AUM(线性插值);全程在 floor 以上 → None(容量 > 网格上限)。"""

def capacity_band(gross_returns, turnover_by_period, adv_meta, aum_grid,
                  impact_coef_lo_hi: tuple, ppy=52, sharpe_floor=0.3) -> dict:
    """冲击系数扫 [lo, hi] → critical_aum 的区间带 {aum_low, aum_high, curves}。诚实区间,非单点。"""
```

### 4.3 应用驱动器(第 3 块)`tools/capacity_research.py`

- 对 carry / crowd_contra / trend 各自:复用其宇宙/信号(carry: `crypto_carry_strategy`;crowd: `crypto_oi_verify.CROWD_CONTRA` on `crypto_oi_research.build_panel`;trend: `crypto_daily_composite`)重建**逐周权重**,算每名 **ADV**(median close·volume from panel)与**逐期毛收益**(book 价格 MTM,与干跑同法),组装成 `capacity_band` 的输入。
- 落 `data/cache/capacity_snapshot.json`:每条边的容量带 `{aum_low, aum_high}` + 曲线点 + `provenance: UNCALIBRATED_PRINCIPLED`。
- 打印诚实结论:「carry 容量带 ~$X–$Y;crowd_contra ~$X–$Y;trend ~$X–$Y(净夏普 floor=0.3)」。

### 4.4 测试 + 治理(第 4 块)

- `tests/research/test_impact.py`:临时/永久单调、平方根律、ADV<=0/trade=0 → 0、参与率越大冲击越大。
- `tests/research/test_capacity_curve.py`:AUM 越大净夏普单调不增(冲击单调);critical_aum 插值正确;
  band lo<=hi;零换手 → 净=毛(冲击 0)。
- `tests/governance/test_realism_boundary.py`:`realism/` 无网络/券商 SDK import;纯函数无 IO。

## 5. 数据流

```
每条 book:重建逐周权重 + 每名 ADV + 逐期毛收益
  capacity_band(gross, turnover, adv, aum_grid, [c_lo,c_hi]):
    for c in [c_lo, c_hi]:
      for aum in aum_grid:
        逐期冲击成本 = Σ_i AlmgrenChrissImpact(c).total_cost_notional(frac_i*aum, adv_i)
        净收益 = 毛 - 冲击/aum  → net_sharpe(aum)
      critical_aum(c) = 净夏普跌破 0.3 的 AUM
    band = [critical_aum(c_lo), critical_aum(c_hi)]   # 诚实区间
  -> capacity_snapshot.json (三条边的带 + 曲线 + UNCALIBRATED_PRINCIPLED)
```

## 6. 安全与约束

- `services/research/realism/` 纯函数、确定性、无 IO/RNG/墙钟、无网络/券商 SDK import;进 gate。
- 驱动器 `tools/capacity_research.py` 在 gate 外(读本地库、组装 IO),不进 `research_runner`。
- 冲击/容量只是**研究诊断**——不改 CostModel 默认、不驱动晋级/下单、不碰五个 LIVE 标志。
- 诚实:输出标 `UNCALIBRATED_PRINCIPLED`,容量给区间带不给假单点。

## 7. 非目标(RE-2 及以后)

历史情景重演(2008/2020/2022 危机窗口)、copula/EVT 尾部风险、console 容量页(RE-1 先落 json + 打印;
console 可作 RE-1b 小跟进)、把冲击接进 CostModel 默认(会改已有结果,显式不做)。

## 8. 自审(spec self-review)

- **占位符:** 无 TBD;每块给签名与不变量。
- **一致性:** 冲击系数「无法校准 → 给区间带」在 §3/§4.2/§5 一致;容量核纯函数、驱动器组装 IO 分层清楚。
- **诚实:** 显式 `UNCALIBRATED_PRINCIPLED`、区间带非单点、不冒充校准;不改 CostModel 默认避免污染已有结果。
- **范围:** 单一实现计划可覆盖(2 纯模块 + 1 驱动器 + 测试);情景重演/尾部/console 显式划出。
