# carry+crowd 组合 book 分散验证 设计

> L2_DRY_RUN_ONLY。FRED regime 分析显示 carry(爱 risk-on)与 crowd_contra(爱 risk-off)宏观 regime 画像相反、
> 互补。本增量测:合起来跑是否真更平滑/更高风险调整收益(兑现分散故事)。纯离线、诊断非门禁、无网络无下单。

## 1. 背景

- carry:低VIX SR+2.36/高VIX+0.65、正常曲线+2.08/倒挂+0.67(爱平静)。
- crowd_contra:高VIX SR+1.96/低+1.25、倒挂+1.76/正常+0.88(爱压力)。
- 二者相反 → 组合应抹平 regime 依赖、抬高风险调整收益。**这是可解释的分散假设,现在用数据检验它。**

## 2. 诚实边界

- **收益流层面的分散测试**,非同一执行 book(carry 58 流动币 / crowd 14 大币,不同宇宙、不同资金规模、不同网格)。
- gross 无成本 → **RELATIVE 比较**(组合 vs 单条)才是结论,不是绝对夏普。
- 逐周对齐按 **ISO 年-周**(两条网格接近但不必逐日相同)。
- 分散可能兑现、也可能不(若两条边在同一周同向,组合不改善)——数字为准。
- 诊断非门禁:不驱动晋级/下单、不碰 LIVE 标志。

## 3. 架构

### 3.1 纯核 `services/research/book_combine.py`

```python
def _iso_week(ms: int) -> str:
    """epoch ms → 'YYYY-Www'(ISO 年-周),对齐键。"""

def align_on_common_weeks(a_ret, a_ts, b_ret, b_ts) -> dict:
    """两条 (returns, timestamps_ms) 按 ISO 年-周对齐:同周多值取首个。
    返回 {weeks:[...], a:[...], b:[...]}(仅两边都有的周,按周排序)。"""

def inverse_vol_weights(series_list) -> list[float]:
    """逆波动权重 ∝ 1/pstdev(归一化到和=1);某条零波动/太短 → 等权回退。"""

def combine_returns(series_list, weights) -> list[float]:
    """逐期加权和 Σ w_i·r_i(等长)。"""
```

### 3.2 驱动器 `tools/combo_book_research.py`

- 复用 `scenario_tail_research._book_series` 取 carry/crowd 的 `(gross, ts)`。
- `align_on_common_weeks` → 共同周的 carry/crowd 收益。
- 组合两方案:**50/50 等权** 与 **逆波动(风险平价)**。
- 指标(共同周上):carry-alone / crowd-alone / combo-eq / combo-invvol 各自年化夏普、最大回撤、总收益;
  相关系数 corr(carry, crowd)。
- combo 的宏观 regime 分桶(复用 `macro_regime`,读 `macro_fred.db`):看组合是否抹平 vol/rates regime 依赖。
- 落 `data/cache/combo_book_snapshot.json`(provenance `REAL_COMBINED_BOOK`)+ 打印诚实结论。

## 4. 数据流

```
carry(gross,ts) + crowd(gross,ts) ──align ISO 年-周──> 共同周 [carry_w, crowd_w]
  corr(carry,crowd)
  combo_eq = 0.5·carry + 0.5·crowd ; combo_invvol = 逆波动加权
  指标:各自 年化夏普/最大回撤/总收益(共同周)
  combo × macro regime 分桶(是否抹平 regime 依赖)
  -> combo_book_snapshot.json + 诚实结论(分散是否兑现)
```

## 5. 安全与约束

- `services/research/book_combine.py` 纯函数、无 IO/RNG/墙钟/网络;驱动器 `tools/`(读本地库)。
- 只读诊断;不驱动晋级/下单;不碰 LIVE 标志。
- 诚实:RELATIVE 比较、不同宇宙/规模、gross 无成本——显式标注。

## 6. 非目标

同一执行 book 的真组合(需统一宇宙 + 干跑流水线,那是后续);成本后组合;把组合送晋级/干跑(独立决策)。

## 7. 文件清单

- 新建 `services/research/book_combine.py`、`tools/combo_book_research.py`、`tests/research/test_book_combine.py`。
- 改 CLAUDE.md、PROJECT_STATE、记忆(`macro-regime.md` 补组合结论)。

## 8. 自审

- **占位符:** 无 TBD;核给签名与不变量。
- **一致性:** 「RELATIVE 比较、收益流层面、ISO 年-周对齐」在 §2/§3/§4 一致。
- **诚实:** 不同宇宙/规模/gross 显式标注;分散可能不兑现;数字为准。
- **范围:** 单一计划可覆盖(1 纯核 + 1 驱动器 + 测试);同一执行 book/成本后/晋级显式非目标。
