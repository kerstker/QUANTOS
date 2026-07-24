# QUANTOS 加密量化稳健优化

`tools.crypto_robust_optimization` 是三条冻结 Alpha 的稳健参数筛选入口。它只运行
研究和纸面路径，默认保持 `L2_DRY_RUN_ONLY`，不读取私钥、不导入券商 SDK，也不提交订单。

## 运行

```powershell
python -m tools.crypto_robust_optimization
```

CUDA 不可用时命令返回非零状态并在报告中写入 `GPU_UNAVAILABLE`；不会静默改用 CPU
生成“GPU”结果。完整矩阵写入
`data/cache/crypto_robust_optimization_cells.jsonl`，权威汇总写入
`evidence/crypto_robust_optimization.json`。

## 固定口径

- Alpha：`carry_oi_majors`、`crowd_contra`、`top_contra`；公式不在本研究中重写。
- 调仓：周度，风险年化 `ppy=52`。
- 候选：三 Alpha simplex 每 10%，杠杆上限 0.25x–2.0x，回撤阈值 8%–20%。
- 压力：手续费 0–10 bps、Almgren–Chriss 影响 5/10/20 bps、参与率 5/10/20%、
  执行铺开 1/3/5 天；Binance taker 4 bps 是主基准。
- 回撤阈值只是准入门，不会覆盖交易逻辑做动态减仓。

候选配置 hash 不包含压力单元，因此同一个模型候选在不同压力下不会被重复计入
全局试验次数。所有候选 hash 通过 `data/cache/experiment_registry.db` 的 append-only
搜索记录登记；旧的 GPU 试验预算保留。

## 硬门

历史筛选必须同时具备 PIT/质量证据、DSR ≥ 0.95、PBO ≤ 0.25、OOS VT Sharpe ≥ 0.8、
Walk-forward 和市场状态盈利折比例 ≥ 75%、完整九引擎研究风险输入，以及所有压力单元
最差 VT Sharpe ≥ 0.8、最大回撤不超过候选阈值、正收益压力单元比例 ≥ 75%。任何缺失
输入均为 `BLOCK`，四引擎旧干跑诊断不会冒充九引擎门。

通过历史硬门且位于稳定连通区域的候选才进入 CPU 精确纸面：整手数量、最小数量/步长、
费用、资金费、预交易门、crowding gate、九引擎研究风险、纸面成交、PnL 对账和审计链
必须全部完成；对账残差超过 `1e-6` 或有未解释拒单即淘汰。CPU 结果优先于 GPU 近似。

前向账本还必须有至少 8 个有效周期、数据新鲜、累计净收益为正、回撤在阈值内且账本
一致，才会分类为 `PAPER_READY_FOR_REVIEW`。少于 8 周只能是
`PAPER_ONLY_PENDING_FORWARD`；任何阶段都不会解除 `LIVE_BLOCKED_L2`。

## 当前本地运行

最近一次报告记录 RTX 5090 D / CuPy 14.1.1，完整 374,220 个压力单元已执行。当前没有
可部署候选：冻结 Alpha 的 evaluator-level DSR/PBO/WF 绑定缺失、carry 的完整 PIT
价格/资金费区间存在缺口，且若干候选的最差压力 Sharpe 低于 0.8。该结论是硬拒绝，
不是降低门槛后的次优选择。

