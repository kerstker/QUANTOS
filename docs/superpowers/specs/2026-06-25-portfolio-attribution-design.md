# 组合归因深化:收益归因 + 边际风险贡献(A 类 #2)

**日期:** 2026-06-25
**状态:** 已通过头脑风暴确认 → 实现
**所在层:** `services/portfolio/`
**就绪等级:** L2_DRY_RUN_ONLY —— 仅研究;纯 Python、确定性、失败关闭。

## 目标

补齐组合**收益侧归因**(现仅有风险贡献):每个 alpha 对实现收益的贡献,与边际风险贡献一起
组成完整的"风险 + 收益"归因报表(直接支撑 console Portfolio 的 `return_attribution`/
`risk_contributions`,提升组合可解释性)。

## 关键约束

- 纯 Python stdlib;确定性;失败关闭(协方差走现有 `covariance_matrix` 的 min_overlap 守卫)。
- **纯加法/零回归**:全是新函数,不动 `build_portfolio`/优化器/现有风险函数。

## 非目标(YAGNI)

- 不做广义风险预算优化器(命中任意目标风险贡献的求解器;risk-parity 等贡献已有)。
- 不做多期 / Brinson 配置-选择归因。

## 设计

### `marginal_risk_contributions(weights, cov)`(放 `risk_model.py`)
返回边际风险贡献向量 `MRC_i = (cov·w)_i`(方差空间)。性质:`sum_i w_i·MRC_i == portfolio_variance`
(与现有 component `risk_contributions = w_i·MRC_i` 一致)。

### `return_attribution(returns_by_alpha, weights, *, horizon=None)`(放 `construction.py`)
每 alpha 实现收益贡献 = `w_i × R_i`,其中 `R_i = sum(returns_by_alpha.get(id, [])[-horizon:])`
(**简单累计=区间收益和**,选简单而非复利以保证**精确可加性**:`sum_i w_i·R_i` 恰等于组合区间
简单收益)。返回 `{"contribution": {id: w_i·R_i}, "total": Σ, "pct": {id: contribution_i/total}}`。
`total==0` 时 `pct` 全 0(失败关闭,不除零)。在 `weights` 上迭代;缺 returns 序列的 alpha 贡献 0。

### `attribution_report(returns_by_alpha, weights, *, config=None)`(放 `construction.py`)
一次返回完整归因:
```
{"return_contribution": {id: w·R}, "pct_return": {id: ..},
 "risk_contribution_pct": {id: ..}, "marginal_risk": {id: (cov·w)_i}}
```
风险侧用 `covariance_matrix`+`shrink_covariance`(现有);id 取协方差的对齐集;收益侧用
`return_attribution`。确定性。

## 错误处理 / 失败关闭

- `return_attribution`:`total==0` → pct 全 0;缺序列 → 该 alpha 贡献 0(不抛)。
- `attribution_report` 的风险侧:overlap < min_overlap → `covariance_matrix` 抛(fail-closed,与现有一致)。

## 确定性

无 RNG;`sorted` id;纯算术。同输入同输出。

## 测试矩阵(扩展 `tests/portfolio/`)

1. `return_attribution`:贡献数值对拍;`sum(contribution) == total`;`total == 组合区间简单收益`
   (两 alpha 手算);缺序列 alpha 贡献 0;`total==0` → pct 全 0。
2. `marginal_risk_contributions`:`MRC_i==(cov·w)_i`;`sum(w_i·MRC_i)==portfolio_variance`(一致性)。
3. `attribution_report`:键/形状完整;risk_contribution_pct 求和≈1(非零方差);确定性(两次相同)。
4. fail-closed:overlap 不足 → 风险侧抛(用现有短序列)。

## 涉及文件

- 修改 `services/portfolio/risk_model.py` —— `marginal_risk_contributions`。
- 修改 `services/portfolio/construction.py` —— `return_attribution`、`attribution_report`。
- 扩展 `tests/portfolio/`(新建 `test_attribution.py`)。
- 复用不动:`covariance_matrix`/`shrink_covariance`/`risk_contributions`/`percent_risk_contributions`。

## 安全审查

- 纯计算、无外部依赖/网络;不改契约/前端/后端合约门。
- 纯加法;不影响既有组合构建与风险路径。
