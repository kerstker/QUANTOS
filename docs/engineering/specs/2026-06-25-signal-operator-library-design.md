# 时序信号算子库扩充(A 类 #1)

**日期:** 2026-06-25
**状态:** 已通过头脑风暴确认 → 实现
**所在层:** `services/research/signals.py` + `services/intelligence/evolutionary_search.py`
**就绪等级:** L2_DRY_RUN_ONLY —— 仅研究;确定性、PIT 安全、失败关闭。

## 目标

拓宽机器可发现的 alpha 空间:给 `SIGNAL_OPS` 增加一批稳健的时序算子,并纳入进化/引导搜索的
默认词汇,使引导/统一/进化/交互搜索立刻能搜到更丰富的因子族。

## 关键约束

- 纯 Python stdlib(`math`/`statistics`);确定性;PIT 安全(由调用方传 knowable-by-t closes);
  退化(过短/零分母)返回 `None`(panel builder 据此丢弃该标的)。
- **纯加法/零回归**:`SIGNAL_OPS` 新增键不动既有键;`momentum/ts_mean/zscore` 与所有现有测试不变。
- 扩 `DEFAULT_VOCAB["operators"]` 仅增加可搜候选(fail-safe:更多试验,既有成员断言仍成立)。

## 非目标(YAGNI)

- 不做横截面变换(rank/winsorize)与行业中性化(需 sector 元数据契约;留后续)。

## 新算子(均 `(closes: Sequence[float], window: int) -> Optional[float]`)

- **`volatility`** —— 窗口内简单收益 `pstdev`。`window<2` 或 `len<window+1` 或任一零分母 → None。
- **`ts_rank`** —— `closes[-1]` 在末 `window` 值中 `<=` 的占比 ∈ (0,1]。`window<2` 或 `len<window` → None。
- **`ema_ratio`** —— `closes[-1]/EMA − 1`,`EMA` 用 `alpha=2/(window+1)` 从 `closes[0]` 迭代(确定性)。
  `window<1` 或空 或 `EMA==0` → None。
- **`dist_from_high`** —— `closes[-1]/max(末 window) − 1`(≤0)。`window<1` 或 `len<window` 或 `max==0` → None。
- **`accel`** —— `_momentum(closes, window) − _momentum(closes[:-window], window)`(动量加速度);
  任一段 None(含 `len<2*window`)→ None。复用现有 `_momentum`。

`DEFAULT_VOCAB["operators"]` 扩为 `("momentum","ts_mean","zscore","volatility","ts_rank","ema_ratio","dist_from_high","accel")`。

## 确定性 / 失败关闭

无 RNG;纯函数;退化输入 → None(不抛、不产生 NaN/inf —— 调用方/横截面层已有非有限守卫)。

## 测试矩阵(扩展 `tests/research/test_signals.py` 或新建)

1. 每个新算子:已知输入的数值正确(手算对拍)。
2. None 守卫:过短窗口、零分母、退化(常量序列对 volatility=0 仍是有效 0;ema_ratio 常量→0)。
3. 确定性:同输入两次同结果。
4. `SIGNAL_OPS` 含全部 8 个键;`DEFAULT_VOCAB["operators"]` 含新算子。
5. 集成:一个用新算子的价格 genome(如 `("P","volatility","5")`)经 `make_evolution_provider` 能构建 `PanelPeriod`。

## 涉及文件

- 修改 `services/research/signals.py` —— 5 个新算子 + 注册进 `SIGNAL_OPS`。
- 修改 `services/intelligence/evolutionary_search.py` —— `DEFAULT_VOCAB["operators"]` 扩充。
- 扩展/新建 `tests/research/test_signals.py`(+ 一条集成断言)。

## 安全审查

- 纯计算、无外部依赖、无网络;PIT 安全由调用方保证;不改契约/前端/后端合约门。
- 纯加法;扩搜索空间只增加多重检验试验数(被 DSR/var_sr 正确惩罚)。
