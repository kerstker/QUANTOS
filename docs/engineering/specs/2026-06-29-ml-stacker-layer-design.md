# 学习型信号组合层(ML stacker)—— 设计 spec

> 状态:设计已与用户确认(2026-06-29)。下一步:writing-plans 出实现计划。
> 约定:本文中文叙述,代码标识符英文。安全/确定性/诚实边界见 §6,不可让。
> 由来:[`docs/QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md`](../../QUANTOS_GAP_ANALYSIS_vs_TOP_QUANT.md) 头号杠杆「补脑」(P0,CRITICAL:零 ML/DL 层)。

## 1. 目标(一句话)

给 QUANTOS 加一个**确定性的学习型信号组合(stacking)层**:用真 ML(GBM + Ridge 基线)把现有因子池的多个
(各自无 OOS edge 的)信号,学成一个截面 meta-信号预测前向收益,**照走现有无窥视 OOS 关卡 + 成本扫描**,
并把「组合 stacker vs Ridge 基线 vs 单因子最优」三方 OOS 并排报出来——第一次回答「学习型组合到底帮不帮得上」。

这是差距报告头号杠杆「补脑」的第一刀,在**不破 L2 安全边界、不弱化诚实关卡**前提下扩大「可发现 edge 的类型」。

## 2. 已确认的关键决策

- **依赖政策**:放宽——`services/` 引入真 ML 库(`numpy` + `scikit-learn`),但**确定性使用**(固定 `random_state`、
  `n_jobs=1`、确定性 solver、关 BLAS 多线程)。`services/` 的**无网络 import** 不变量继续机器强制(ML 库非网络)。
- **做什么**:学习型信号组合 / stacking(不是新 alpha 生成器,不是成本建模)。
- **模型**:GBM 回归器(`HistGradientBoostingRegressor`)+ Ridge 基线;截面排序→多空。
- **范围(首刀)**:核心 ML 模块 + 研究驱动器走关卡。**后端为主,不接 console、不接 ResearchAgentLoop**。

## 3. 诚实关键点(不可妥协)

学习型组合**最容易在薄数据上过拟合**,所以诚实门是本刀的灵魂:

1. **训练无泄漏**:stacker 的拟合只能用 purged + embargo 的滚动训练块;测试块严格未来、不参与拟合/选型/超参。
2. **最终裁决用现有 no-peek 滚动 OOS**(`services/research/oos_validation.py`),不是训练 CV 的内部分数。
3. **三方并排**:组合 stacker、Ridge 基线、单因子最优,**全部 OOS** 报出——这样能直接看到「非线性脑」相对
   线性基线/单因子有没有加价值。若 GBM 打不过 Ridge,就如实说非线性没帮上。
4. **成本扫描**(沿用 FX gauntlet 的 1/3/5/10/20bps 存活曲线)。
5. **多重检验诚实**:尝试 GBM/Ridge/多超参 = 多 trial,在快照里如实记 trial 数(供 DSR 折扣口径参考)。

> 反面教材(禁止):用全样本拟合后报样本内分数当 edge;用测试段调超参再报该段 OOS;只报组合不报基线对比。

## 4. 组件与接口(每个单元一个清晰职责)

### 4.1 `services/research/ml/feature_panel.py`
- `build_feature_panel(factor_periods: Mapping[str, Sequence[PanelPeriod]]) -> FeaturePanel`:把因子池的多份
  `PanelPeriod`(每因子一份,含 instruments/signals/forward_returns)按 **(period_index, instrument)** 对齐成:
  `X`(行=(期,标的),列=因子信号)、`y`(该行的前向收益)、行的 `(period_index, instrument)` 索引。
  仅保留所有因子都有值的 (期,标的)(inner-join,fail-soft 丢缺行)。PIT 安全:特征是决策时的因子值,y 是其前向收益。
- 纯数据整形(numpy);无 RNG / 无墙钟。

### 4.2 `services/research/ml/stacker.py`
- `fit_stacker(X, y, *, model="gbm"|"ridge", seed=0) -> Stacker`:确定性拟合。`gbm` =
  `HistGradientBoostingRegressor(random_state=seed, ...)`;`ridge` = `Ridge(...)`(无 RNG)。
- `Stacker.predict(X) -> np.ndarray`(每行一个分数)。
- 模块导入即设确定性环境(`OMP_NUM_THREADS=1` 等线程钉死 + 固定 seed);**同输入→同输出**(确定性测试钉死)。

### 4.3 `services/research/ml/walk_forward_stack.py`
- `walk_forward_stack(panel: FeaturePanel, period_index: Sequence[int], *, train_size, test_size, embargo,
  model, seed=0) -> dict`:purged+embargo 滚动。每折:用训练期的行拟合 stacker → 预测测试期的行 → 按
  **截面**(同 period 内)排序成多空权重 → 该期组合收益。拼出 OOS 组合收益序列 + 元信息(折数/训练测试规模)。
- 返回 `{"oos_returns": [...], "n_folds": int, ...}`。这是**防泄漏的训练+OOS预测**核心;纯编排,确定性。

## 5. 数据流(驱动器 `tools/ml_stack_research.py`,墙钟只在此边界)

1. 载入因子池面板:复用 `make_data_periods_provider`(crypto `crypto_history.db` 或 FX `fx.db`),为 POOL 里每个
   (op,window) 建 `PanelPeriod` 序列。
2. `build_feature_panel` → 特征矩阵 X/y + 索引。
3. `walk_forward_stack`(GBM)+`walk_forward_stack`(Ridge)→ 两条 OOS 组合收益;另取单因子最优的 OOS(复用
   `walk_forward_selection_oos`)。
4. 三方 + 成本扫描:对每条 OOS 收益算关卡指标(sharpe、no-peek OOS、成本敏感);并排报。
5. 诚实裁决(`STACK_BEATS_BASELINE` / `NO_LIFT_OVER_LINEAR` / `REFUTED_NO_EDGE`)+ 写资产隔离的 `data/cache/ml_stack_snapshot_<asset>.json`。
6. **不**注册进 run_registry(与既有研究驱动器一致,结果落快照)。确定性、可复现、可重跑。

## 6. 容器化 / 安全 / 确定性边界(机器强制,不可让)

- `LIVE_TRADING_ENABLED=false` 等五标志不动;ML 层**零下单/券商/凭证路径**,只产候选信号、只读关卡。
- **确定性保住**:固定 `random_state` + `n_jobs=1` + 线程环境钉死 + 确定性 solver;确定性测试钉死同输入同输出。
  墙钟只在驱动器边界。
- **无网络 import 不变量继续机器强制**:`tests/governance/test_crypto_data_boundary.py::test_services_tree_has_no_network_imports`
  仍须绿(numpy/sklearn 非网络)。治理扫描器仍绿(非交易 SDK)。
- **诚实门不弱化**:学习信号照走泄漏检测 + CPCV/DSR/no-peek OOS;过拟合照样被证伪。
- 新依赖钉版本进 `requirements.txt` + `pyproject.toml`(新段,注明 "research ML layer";non-network、确定性)。

## 7. 测试(TDD,门绿)

- `tests/research/test_ml_feature_panel.py`:多份合成 `PanelPeriod` → X/y 对齐正确(行=期×标的、列=因子、inner-join 丢缺行)。
- `tests/research/test_ml_stacker.py`:确定性(同输入两次拟合预测**逐位相同**);GBM/Ridge 都能拟合+预测;退化输入 fail-soft。
- `tests/research/test_ml_walk_forward_stack.py`:**无泄漏**——造一个只在样本内可分离的合成信号 → OOS 组合收益≈0/负;
  造一个真前向可预测的合成信号 → OOS 组合收益>0;purged/embargo 边界正确(测试块不进训练)。
- `scripts/validate.py` 全绿(治理扫描 + 全套);确定性测试纳入。

## 8. 非目标(YAGNI)

- 不接 console、不接 ResearchAgentLoop/晋级链(首刀后端研究驱动器为主;接线是后续增量)。
- 不做超出现有因子池的特征工程(原始价/量特征、另类数据留后续)。
- 不上 NN/xgboost/RL(GBM+Ridge 够答「学习型组合帮不帮得上」)。
- 不期待找到 edge——薄数据 + 已知无趋势 edge,预期大概率被关卡证伪;**能力 + 三方诚实对比**是交付物。
