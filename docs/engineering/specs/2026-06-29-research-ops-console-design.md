# 研究操作台(Research Operations Console)设计规格

> 日期 2026-06-29 · 状态:设计已批准(方案 A),待用户复核规格后转实施计划。
> 约定:中文叙述;代码标识符英文。安全边界遵循 [`CLAUDE.md`](../../../CLAUDE.md) §1(L2_DRY_RUN_ONLY)。

## 1. 目标与问题

**问题**:当前前端只是静态展示——每跑一次回测就要手写一个写死数字的页面;回测只能在 IDE 里跑。

**目标**:把前端从"看板"升级为"操作台":
1. **动态结果区**:任何回测/研究跑完**自动出现**在一个专门区域,可浏览、可对比,**不再一回测一页**。
2. **前端触发运行**:从前端点按钮触发研究运行(回测/搜索/取数),**不在 IDE 里跑**。
3. **不破坏安全模型**:全程 L2_DRY_RUN_ONLY,`auto_approved` 恒 0,绝不碰下单/券商/凭证/flag/晋级。

**成功标准**:① 现有所有回测结果第一天就在「研究中心」里可见且通用渲染;② 新跑的研究自动登记出现;
③ 能从前端点按钮触发一个白名单内研究运行,看到实时状态,跑完看结果;④ 治理/契约/单测/前端测试全绿,
`console_api` 仍 100% 只读,安全不变量机器强制。

## 2. 架构总览

三个组件 + 一个隔离的触发面(方案 A):

```
  [前端 研究中心区]
     |  GET (只读, 轮询)            |  POST /run (触发, 唯一可变面)
     v                              v
  [console_api  (只读快照)]     [research_runner  (受限研究运行器, 新)]
     |  读                          |  校验白名单 → 异步 job → 跑白名单函数 → 写
     v                              v
  ============== run_registry (stdlib sqlite, 单一事实源) ==============
     ^ 索引器把现有结果 JSON 灌入
```

- **查看链路**(前端 → console_api → registry)**全只读**,与现有 console_api 治理一致。
- **触发链路**(前端 → research_runner → registry)是**唯一可变面**,隔离成独立小服务,被治理钉死为"只研究"。
- 两条链路**共享 run_registry** 作为单一事实源。

## 3. 组件 1:run_registry(研究运行登记表)

**位置**:`services/research/run_registry/`(纯 stdlib sqlite3,镜像现有 `experiment_registry` 的持久化写法)。

**记录契约**(`ResearchRun`):

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | str | 唯一 id = `hash(kind + serialize(params) + created_ts)`(确定性:同 kind+参数+时刻幂等;不同时刻=新运行) |
| `kind` | str | 运行类型(白名单内:`carry_strategy`/`oi_research`/`combo_book`/`survivorship`/`factory_orthogonal`/`factory_loop`/`data_refresh`) |
| `title` | str | 人读标题 |
| `params` | dict | 运行参数(按 kind 的 schema 校验过) |
| `status` | str | `queued`/`running`/`done`/`error` |
| `created_ts` / `finished_ts` | int | 时间戳(由调用方传入,服务内不取墙钟以保持核心确定性;runner 在 tools 边界取墙钟) |
| `metrics` | dict[str,float\|str] | 头条数字(净夏普、maxDD、IC、换手…),驱动 KPI |
| `series` | dict[str,list] | 命名序列(per_year、cap_sweep…),驱动图表 |
| `verdict` | str | 裁决文本(DEPLOYABLE/WEAK_LEAD/REFUTED…) |
| `caveats` | list[str] | 强制折扣 |
| `provenance` | str | 来源(工具名、数据集版本、universe) |
| `error` | str\|None | 出错信息(status=error 时) |

**接口**:
- `class ResearchRunStore`(sqlite):`upsert(run)`、`get(run_id)`、`list(kind=None, limit=...)`(按时间倒序)、`set_status(run_id, status, error=None)`。
- `class RunRegistry`:在 store 上的薄封装 + 校验(record 字段完整、status 合法、kind 在白名单)。**fail-closed**:坏记录 raise,不静默吞。
- **索引器** `index_existing(cache_dir, store)`:把现有 `data/cache/crypto_*.json`(carry_strategy/oi_research/combo_book/oi_survivorship/oi_verify/factory_*)各映射成一条 `ResearchRun`(每种一个 `_adapt_<kind>(json)->ResearchRun` 适配函数,把该 JSON 的关键数字抽进 metrics/series/verdict/caveats)。运行索引器后,**历史回测全部可见**。

**确定性**:服务内不取墙钟/不用 RNG;ts 由调用方注入。纯 stdlib。

## 4. 组件 2:console_api 只读查看端点

`services/research/console_api/main.py` 新增两个 **GET**(保持只读,无 POST):
- `GET /api/v1/console/research-runs` → `{source, read_only:True, runs:[摘要...]}`(摘要 = run_id/kind/title/status/created_ts/headline/verdict;`headline` = 该 kind 的 adapt 指定的一个头条指标 `{label, value}`)。
- `GET /api/v1/console/research-runs/{run_id}` → 完整 `ResearchRun` 记录(顶层键固定)。

**契约**:在 `tests/research/test_console_api_contract.py` 钉住两个端点的顶层键(与前端 `*Payload` 类型一致)。
读 registry;registry 为空时返回空列表(非 fabricated)。`console_api` 仍 100% 只读——治理扫描器 + 契约测试守。

## 5. 组件 3:research_runner(受限研究运行器,新服务)

**位置**:`services/research/research_runner/`(独立 FastAPI app,独立端口,如 8420)。**这是唯一可变面。**

**端点**:
- `POST /run {kind, params}` → 校验 → 入队 → 返回 `{run_id, status:"queued"}`(立即返回,不阻塞)。
- `GET /health` → `{mode:"RESEARCH_RUNNER", live_disallowed:True}`。
- (进度查询走 console_api 的只读 registry 端点,runner 不重复提供。)

**白名单**(`_RUN_KINDS: dict[str, RunSpec]`):每个 kind 映射到:
- 一个具体研究入口函数(在 `services/research` 或 `tools`,如 `tools.crypto_carry_strategy:main`),
- 一个参数 schema(允许的键+类型+范围;拒绝其它),
- 一个 `adapt` 函数(运行产物 → ResearchRun 的 metrics/series/...)。

可触发类型(默认):`carry_strategy` / `oi_research` / `combo_book` / `survivorship` / `factory_orthogonal` / `factory_loop` / `data_refresh`。

**异步执行**:`POST /run` 入内存队列(`concurrent.futures` 线程池,并发上限小,如 2),worker:
`set_status(running)` → 调白名单函数(参数仅白名单键)→ `adapt` → `upsert(done)`;异常 → `set_status(error, msg)`。
**重启不丢**:job 状态在 registry(sqlite),前端轮询 registry 看进度。

**安全(机器强制,治理测试钉死)**:
- runner **只**能跑 `_RUN_KINDS` 内的 kind;未知 kind → 400。**无 `eval`/`exec`/`subprocess`/shell**;参数不拼进任何路径/命令。
- 治理测试 `tests/governance/test_research_runner_boundary.py`:
  - `_RUN_KINDS` 的每个入口模块根 ∈ `{services.research, tools}`,**无一** ∈ `services.trading` 或券商/下单路径;
  - runner 源码不含 `create_order/place_order/submit_order/.withdraw(/fapiPrivate/...`(复用 `_FORBIDDEN_TRADE_CALLS`);
  - runner 不 import 任何 `_FORBIDDEN_SDK`;
  - 每个 RunSpec 的 param schema 非空且类型受限。
- `auto_approved` 恒 0;runner 持非人类 actor,**到不了 lifecycle APPROVED / 任何 live 面 / flag**。
- 网络(data_refresh)只经现有 `tools/` 抓取器。

## 6. 组件 4:前端「研究中心」区 + 通用查看器 + 触发 UI

`apps/research-console/src/`:
- **新导航分组「研究中心 / Research Runs」**,含两页:
  - `ResearchRuns.tsx`(列表):从 `/research-runs` 拉全部运行,可按 kind 筛、按时间/指标排;每行 status chip + 头条指标 + 裁决;点进详情。顶部一排**运行按钮**(每个白名单 kind 一个),点击 → `POST research_runner/run` → 该运行以 `queued` 出现在列表顶部 → 轮询自动变 `running`/`done`。
  - `RunDetail.tsx`(**通用查看器**):从 `/research-runs/{id}` 拉记录,**纯数据驱动渲染**:metrics→KpiStrip、series→BarSeries/AreaChart、verdict→StatusChip、caveats→列表、provenance→脚注。**任何 kind 都用这一个查看器**,不再手写页。
- **触发客户端**:`src/lib/api/research-runner-api.ts`(POST `/run`),与只读 console_api client 分开;runner base url 走 `VITE_RUNNER_URL`(默认禁用 → 按钮显示"需启动 runner")。
- **诚实/安全提示**:页头 ribbon「研究运行 · 不下单/不碰实盘 · L2」。触发按钮旁注「只跑研究计算」。
- 今天的 `BacktestResults` 页保留为精选总览,加一个入口指向「研究中心」看全部。

## 7. 数据流

**触发**:前端点「跑 carry 回测」→ `POST research_runner/run {kind:carry_strategy, params}` → runner 校验 → 入队 → 返回 run_id(queued)→ 前端把它插入列表顶部 → 轮询 console_api `/research-runs` → 看到 running→done → 点开 RunDetail 看结果。

**查看历史**:前端开「研究中心」→ GET `/research-runs` → 列表(含索引器灌入的历史 + 新跑的)→ 点任一 → GET `/research-runs/{id}` → 通用查看器渲染。

## 8. 测试

- **run_registry**:单测——record 往返(upsert/get/list)、status 流转、fail-closed(坏记录 raise);索引器把一个样例 JSON 正确映射成 ResearchRun。
- **console_api**:契约测试(两端点顶层键)+ 单测(空 registry 返回空、详情键齐)。
- **research_runner**:单测——未知 kind 拒绝、参数 schema 拒非法、stub 研究函数 job 跑通写 registry、异常→error 状态;**治理测试**(白名单只研究、无券商 import、无下单调用、param schema 非空)。
- **前端**:smoke 测(ResearchRuns/RunDetail 挂载)+ 端到端浏览器验证(点运行→出现→查看)。
- **门**:`python scripts/validate.py` 全绿(含新治理测试 + 契约);前端 typecheck/test/build 绿。

## 9. 构建顺序(每步测试绿、提交)

1. `run_registry` 存储 + 索引器 + 单测。
2. `console_api` 两个只读端点 + 契约测试。
3. 前端「研究中心」列表 + 通用查看器(只读浏览全部历史)+ smoke + 浏览器验证。
4. `research_runner` 服务:受限触发 + 异步队列 + 单测 + 治理测试。
5. 前端触发 UI(运行按钮 + 实时状态轮询)。
6. 端到端浏览器验证 + 文档。

## 10. 不做(YAGNI)

- 不做用户认证/多用户(单机研究工具)。
- 不做分布式队列(内存线程池足够)。
- 不做参数任意自由表单(每 kind 固定少量受校验参数)。
- 不碰任何 live/broker/order 路径——本设计**只**新增研究计算触发,安全边界与晋级红线完全不动。
