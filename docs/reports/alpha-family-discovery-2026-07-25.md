# QUANTOS Alpha 家族自动发现与纸面观察

- 数据版本：`ba6b6c8b021b33ec304f84c93076358c062cd70380614c57796aa53ad4218168`
- PIT 面板：24 个币种 × 2227 个日点
- 全局试验数：2459（持久化，不按会话清零）
- 家族证据链：`PASS`，事件 69 条
- 运行模式：`L2_DRY_RUN_ONLY`，无真实下单能力

## 候选结果

| 家族/候选 | 状态 | 净 Sharpe | 最大回撤 | DSR | PBO | 纸面状态 |
|---|---|---:|---:|---:|---:|---|
| funding_basis/funding_cross_section | REQUIRES_HUMAN_REVIEW | -1.7738960572779745 | 0.9819781058015046 | 0.0 | 0.014285714285714285 | NOT_RUN |
| crowd_reversal/crowd_oi_reversal | REQUIRES_HUMAN_REVIEW | 1.0405790382976152 | 0.20324714224663645 | 0.0 | 0.014285714285714285 | PAPER_ONLY_PENDING_FORWARD |
| trend/trend_7d | REQUIRES_HUMAN_REVIEW | 0.43897036276579793 | 0.5251591270287936 | 0.0 | 0.014285714285714285 | PAPER_ONLY_PENDING_FORWARD |
| mean_reversion/mean_reversion_1d_proxy | REQUIRES_HUMAN_REVIEW | -1.3401546672495894 | 0.9775075301248531 | 0.0 | 0.014285714285714285 | NOT_RUN |
| term_structure/term_structure_spread | DATA_UNAVAILABLE | — | — | — | — | — |
| spot_perp_basis/spot_perp_basis_proxy | REQUIRES_HUMAN_REVIEW | -2.3197619370055818 | 0.924806476834929 | 0.0 | 0.014285714285714285 | NOT_RUN |
| volatility_risk_premium/volatility_risk_premium | DATA_UNAVAILABLE | — | — | — | — | — |
| liquidity_microstructure/liquidity_volume_proxy | REQUIRES_HUMAN_REVIEW | 0.6955101727764784 | 0.3644283923147789 | 0.0 | 0.014285714285714285 | PAPER_ONLY_DAILY_PROXY |
| cross_exchange/cross_exchange_price_discovery | DATA_UNAVAILABLE | — | — | — | — | — |
| event_regime/event_regime_alpha | DATA_UNAVAILABLE | — | — | — | — | — |

## 解释

`PAPER_ONLY_PENDING_FORWARD` 表示已通过数据和执行影子门，但仍需至少 8 个有效前向周期；历史回放不能替代前向证据。
`PAPER_ONLY_DAILY_PROXY` 明确表示目标是分钟/实时，但当前只使用日线代理，不得写成已验证的目标频率 Alpha。
缺少期权、跨交易所、期限结构或事件时点输入时保持 `DATA_UNAVAILABLE`，不使用合成默认值。

## 纸面运行

- `crowd_oi_reversal`：PAPER_ONLY_PENDING_FORWARD，run `a332ebceee61f33a`，周期 365，净收益 1.017191，对账残差 7e-09。
- `trend_7d`：PAPER_ONLY_PENDING_FORWARD，run `ab63be877b75f11c`，周期 365，净收益 1.013721，对账残差 1e-09。
- `liquidity_volume_proxy`：PAPER_ONLY_DAILY_PROXY，run `24c29236d0771a70`，周期 365，净收益 -0.398289，对账残差 2e-09。
