# 统一因子搜索:基本面价值因子接入可复现会话

**日期:** 2026-06-25
**状态:** 已通过头脑风暴确认 → 实现
**所在层:** `services/intelligence/` + `services/research/`
**就绪等级:** L2_DRY_RUN_ONLY —— 仅研究;离线、确定性、失败关闭。

## 目标

让 `run_research_session` 的引导搜索在**一个 UCB1 bandit** 上跨族统一探索:价格因子
(factor×operator×window)与基本面价值因子(metric×mode,E/P、B/M)同台竞争。把上一子项
建好的基本面价值因子真正闭环进可复现会话(评估记入 registry、bandit 跨会话学习)。

## 关键约束

- 纯 Python stdlib;确定性;失败关闭;agent 围栏不变(仍只经 PROPOSE,永不可达 APPROVED)。
- 纯加法、向后兼容:不给 `fund_store` 时 `run_research_session` 与现状逐字节相同。
- 复用 `GuidedSearchLoop`/`UCBSearch`/`ResearchAgentLoop`/registry 持久化,均不改语义。

## 非目标(YAGNI)

- 不做基本面 operator/window 组合(基本面假设 = metric×mode,够用)。
- 不改前端/后端合约/`*Payload`。

## 架构

### 1. `CombinedHypothesisSpace`(新增 `services/intelligence/search_space.py`)

复合假设元组带族标签,全字符串、JSON 安全(bandit snapshot 需要):
- 价格:`("P", factor, operator, str(window))`;基本面:`("F", metric, mode)`。
- `__init__(price_space: HypothesisSpace, metrics: tuple, modes: tuple)`;校验非空。
- `hypotheses()` → 价格族 `[("P",*h) for h in price_space.hypotheses()]` ∪ 基本面族
  `[("F",m,mode) for m in metrics for mode in modes]`,排序。
- `arms_of(h)`:`h[0]=="P"` → `(("factor",h[1]),("operator",h[2]),("window",h[3]))`;
  `"F"` → `(("metric",h[1]),("mode",h[2]))`。臂在独立命名空间(price/fundamental 不混)。
- `to_definition(*h)`:
  - `P`:`AlphaDefinition(alpha_id=f"h_{op}_{factor}_{w}", name=..., factor_ids=(factor,), params={"operator":op,"window":w})`(同现有价格 defn)。
  - `F`:`AlphaDefinition(alpha_id=f"hf_{metric}_{mode}", name=..., factor_ids=(metric,), params={"metric":metric,"mode":mode,"family":"fundamental"})`。
  - distinct alpha_id/definition_hash → 诚实多重检验计数。

### 2. 评分公平性(改 `UCBSearch._score`)

价格假设 3 臂、基本面 2 臂;现 `_score` 求和 → 偏向多臂族。改为**臂 UCB 均值** `sum(...)/len(arms)`:
- 同构 3 臂价格空间:均值 = 求和/3,**排序与求和完全一致 → 零回归**(`arm_means`/`update`/`snapshot` 不变)。
- 任一未拉臂 → UCB=inf → 均值仍 inf → 优先探索(两族都先被探一遍)。
- 混合族:每臂平均后公平比较,不因臂数偏置。

### 3. 统一 provider(新增 `services/research/fundamental_factors.py`)

`make_combined_periods_provider(price_store, fund_store, instruments, *, event_times, fwd_horizon, universe=None)`
→ `provider(defn)`:
- `"metric" in defn.params` → `build_fundamental_periods(fund_store, price_store, instruments, defn.params["metric"], …, mode=defn.params.get("mode","ratio_to_price"), universe=universe)`。
- 否则 → 价格路径:复用 `make_data_periods_provider` 的逻辑(operator/window → `build_panel_periods`)。
  实现:内部直接调 `make_data_periods_provider(...)(defn)` 或复用其 body。

### 4. session 接线(扩展 `run_research_session`)

新增可选 `fund_store=None, metrics: tuple = (), modes: tuple = ("ratio_to_price",)`:
- `fund_store and metrics` 给定 → `effective_space = CombinedHypothesisSpace(space, metrics, modes)`;
  provider = `make_combined_periods_provider(store, fund_store, instruments, event_times=…, fwd_horizon=…, universe=universe)`。
- 否则 → 现有 `space` + `make_data_periods_provider`(逐字节不变)。
- `GuidedSearchLoop(effective_space, inner_loop=…)`;bandit 持久化(`load_bandit_into`/`save_bandit`)照常
  (复合臂 JSON 安全;跨会话旧价格臂载入、新基本面臂 inf 起步)。
- `report` 同含 `h_*`(价格)与 `hf_*`(基本面);评估记入 registry;`data_gaps`/contained/确定性不变。

## 错误处理 / 失败关闭

- 一个假设的 provider 异常被 `GuidedSearchLoop` 隔离(已有:记 reward 0,搜索继续)——基本面假设同享此隔离。
- `CombinedHypothesisSpace` 维度空 → `ValueError`。

## 确定性

无 RNG;`hypotheses()` 排序;均值评分确定;同输入同输出。

## 测试矩阵

`tests/intelligence/test_combined_space.py`:
1. `hypotheses()` 含两族且数量 = 价格数 + metrics×modes;`arms_of` 价格 3 臂 / 基本面 2 臂派发正确。
2. `to_definition`:P→价格 defn(params operator/window、factor_ids、alpha_id `h_*`);F→基本面 defn
   (params metric/mode/family、factor_ids=(metric,)、alpha_id `hf_*`)。
3. `UCBSearch(combined)`:snapshot→restore 往返(复合臂/假设元组);未拉臂 inf;propose 确定性。
4. `_score` 均值零回归:纯价格 `HypothesisSpace` 上 propose 顺序与改动前一致的代理断言
   (两个等价假设按臂均值排序;现有 intelligence 套件全绿)。

扩展 `tests/research/test_session.py`:
5. session 带 `fund_store+metrics`:跑通,`report` 同含 `h_*` 与 `hf_*` alpha_id,contained
   (无 APPROVED),确定性(两次 `arm_means` 相同),`data_gaps` 存在。
6. 向后兼容:不给 `fund_store` → 现有 session 测试全绿(纯价格路径不变)。

## 涉及文件

- 修改 `services/intelligence/search_space.py` —— `CombinedHypothesisSpace`。
- 修改 `services/intelligence/search_strategy.py` —— `_score` 改臂 UCB 均值。
- 修改 `services/research/fundamental_factors.py` —— `make_combined_periods_provider`。
- 修改 `services/research/session.py` —— `run_research_session(fund_store, metrics, modes)`。
- 新建 `tests/intelligence/test_combined_space.py`;扩展 `tests/research/test_session.py`。
- 复用不动:`GuidedSearchLoop`、`UCBSearch`(除 `_score`)、`build_fundamental_periods`、`make_data_periods_provider`、registry 持久化。

## 安全审查

- agent 围栏与权限不变(仍只 PROPOSE);无新权限。
- 纯加法、向后兼容;评分改动对现有同构空间零回归;无前瞻(两族 provider 均 PIT/as_of)。
- 不改契约/前端/后端合约门。
