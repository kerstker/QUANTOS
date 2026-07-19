# 会话进化模式:进化/交互/价值因子闭环进可复现会话

**日期:** 2026-06-25
**状态:** 已通过头脑风暴确认 → 实现
**所在层:** `services/research/session.py`
**就绪等级:** L2_DRY_RUN_ONLY —— 仅研究;离线、确定性、失败关闭、容器化不变。

## 目标

把进化搜索(子项2,交叉天然涵盖特征交互;原子腿涵盖价格 + 基本面价值因子)接入
`run_research_session`,让它在**可复现会话**里被编排:经会话 catalog(registry 跨会话试验
计数)受控运行,评估记入 registry,数据缺口汇总。一次调用即可机器进化式发现 alpha。

## 关键约束

- 纯加法、**向后兼容**:`evolutionary=False`(默认)时 `run_research_session` 逐字节不变。
- 容器化:进化经会话 `ResearchAgentLoop(catalog)`(PROPOSE),agent 永不可达 APPROVED。
- 复用 `EvolutionarySearchLoop`/`make_evolution_provider`(子项2)、registry 记录/`load_into`、
  `universe_data_gaps`。确定性。

## 非目标(YAGNI)

- 不持久化进化态(无 UCBSearch bandit;`save_bandit` 仅 guided 路径)。
- 不做交互/价值因子的独立"固定网格"会话模式(进化模式已涵盖)。

## 设计

`run_research_session(..., evolutionary: bool = False, evo_generations: int = 2,
                      evo_population: int = 8, evo_elite: int = 3)`:

`evolutionary=True` 分支(在 `catalog` 由 registry `load_into` 之后):
1. `provider = make_evolution_provider(store, fund_store, instruments, event_times=…, fwd_horizon=…, universe=universe)`。
2. **种子**(从已传 `space` + `metrics` 导出):
   价格原子 `[("P", op, str(w)) for op in space.operators for w in space.windows]`
   + 基本面原子 `[("F", m, mode) for m in metrics for mode in modes]`(有 `fund_store` 时)。
3. **vocab** 同源:`{operators: space.operators, windows: tuple(str(w)…), metrics, modes, combines:("sum","product")}`。
4. `loop = EvolutionarySearchLoop(provider, inner_loop=ResearchAgentLoop(catalog=catalog), vocab=vocab)`;
   `out = loop.run(seed, generations=evo_generations, population_size=evo_population, elite_k=evo_elite, timestamp=timestamp)`。
5. `human_review_queue = [aid for aid, e in out["report"].items() if e.get("state") == "PAPER_TRADING"]`。
6. 共享尾段:`data_gaps`(有 universe 时)、对 `out["report"]` 每个有 evaluation 的 alpha
   `record_evaluation`、`registry.save(catalog)`(**不** `save_bandit`)。
7. 返回 `{dataset_version, mode:"evolutionary", best: out["best"], n_evaluated: len(out["report"]),
   recorded_run_ids, report, human_review_queue, data_gaps}`(无 `arm_means`)。

`evolutionary=False`:现有 guided/unified 路径与返回**逐字节不变**(含 `arm_means`、`save_bandit`)。

## 错误处理 / 失败关闭

- 进化内每基因型 provider/评估错误隔离(子项2)。
- registry 为 None 时不记录(同现有)。

## 确定性

种子/vocab 由排序输入导出;`EvolutionarySearchLoop` 确定性;同输入同输出。

## 测试矩阵(扩展 `tests/research/test_session.py`)

1. `evolutionary=True`:跑通;`out.mode=="evolutionary"`;`best` 非空且降序;`report` 含进化产物
   (`h_*` 价格 / 若有 fund 则 `hf_*` / 交叉产 `hx_*`);`recorded_run_ids` 非空(评估记入 registry);
   `catalog.global_trial_count` 增长(多重检验);contained(无 APPROVED);确定性(两次 best 相同)。
2. 向后兼容:`evolutionary=False`(默认)下现有 session 测试全绿;返回含 `arm_means`。

## 涉及文件

- 修改 `services/research/session.py` —— `run_research_session(evolutionary, evo_*)` 分支。
- 扩展 `tests/research/test_session.py`。
- 复用不动:`evolutionary_search`、`fundamental_factors`、`panel_builder`、registry、`ResearchAgentLoop`。

## 安全审查

- 容器化:进化经同一受控 PROPOSE 路径;无新权限、无 APPROVED、无凭证。
- 多重检验诚实:进化产物各计一次试验,DSR/var_sr 随之加重。
- 纯加法、向后兼容;不改契约/前端/后端合约门。
