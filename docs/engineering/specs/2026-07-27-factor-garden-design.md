# Factor Garden 设计与 F 系列切片计划(727 §6 / Phase 4)

- 日期:2026-07-27
- 依据:[`docs/QUANTOS_727.md`](../../QUANTOS_727.md) §6(Factor Garden:
  §6.1 FactorDefinition / §6.2 FactorObservation / §6.3 offline-online 一致性 /
  §6.4 因子家族)、§8.1(无经济解释的表达式禁令)
- 状态:**F1–F5 全部落地,Phase 4 完成**——F1:契约加宽 +
  `services/factor_garden/`(definitions + registry 边界,22 测试);F2:canonical
  因子收敛(`canonical.py`,11 测试),tools/ 双份定义全部消灭;F3:物化器 +
  feature_values schema v2(`materializer.py` + value_store 四列,16 测试);
  F4:offline_online_parity + dependency_graph + health(24 测试,含真实双路径
  parity 案例);F5(本提交):family_cluster(11 测试)。验收对照见 §9。
- readiness:L2_DRY_RUN_ONLY 不变。本方案不改五个安全旗标,不新增任何真实下单
  路径,不触碰凭证,`services/` 内零网络 import;注册一个因子不改变任何评估
  门禁、mandate 或 live 路径(诊断非门禁)。

## 1. 问题:因子定义三套竞争,物化几乎没有

727 §6 要求一个统一的 Factor Garden(定义 + 物化 + offline/online 一致性 +
家族 + 健康传播)。今天的现实是三套互相不知情的「因子定义」:

| 现状 | 位置 | 缺什么 |
|---|---|---|
| alpha_expression 嵌套 tuple DSL | `services/research/alpha_expression.py` | **承重且正确**(canonicalize/alpha_id 身份、PIT 求值),但定义只是裸表达式——无经济假说、无家族、无数据依赖声明、无版本 |
| factor_engine 字符串 AST 编译器 | `services/research/factor_engine/` | 死路:字符串 DSL 与 tuple DSL 双轨,表达能力窄,无任何生产调用方 |
| ic_panel_registry 因子名单 | `tools/ic_panel_registry.py` | 无版本、无身份哈希、无假说;定义散落在 `tools/`(gate 外) |

具体损失(不是洁癖):

1. **offline/online 有真分叉**:CARRY 在 tools/ 有双份定义
   (`crypto_carry_strategy.py::COMPOSITE` 与 `ic_panel_registry.py` 照抄配方),
   crowd_contra 的 QUANTILE 在 forward_track(0.30)与 variants 族(0.20)间分叉,
   决策行选择在 dry-run / forward-track / IC 面板三套驱动器里各写一份。
   727 §6.3 的「同一 FactorDefinition、同一核心计算函数」今天不成立。
2. **物化几乎没有**:§6.2 FactorObservation(computed_at/dataset_version/
   materializer_version/quality_verdict)完全没有落盘面;因子值只有即时计算,
   没有可追溯的「哪个版本的数据算出了哪个值」。
3. **无身份与变更检测**:一个因子的表达式改了,今天没有任何哈希能发现——
   trial ledger 记的是 alpha 表达式,因子作为一等对象不存在。

## 2. 设计裁定

1. **唯一 canonical 形式 = alpha_expression 嵌套 tuple DSL**。
   它已经是生产路径(search/evaluator/dry-run/forward-track)实际消费的形式,
   自带 `serialize`(单射 S 表达式)/ `canonicalize`(交换律子项排序)/
   `alpha_id`(等价表达式共享身份)。F1 的 `formula=serialize(expr)`、
   `expression_hash=alpha_id(expr)`,字符串 AST 不进入契约。
2. **契约加宽,不重建**。`FeatureDefinition` 已是最接近 §6.1 的承重契约
   (注册表/历史链/物化全挂在它上面);F1 以**全部可选带默认值**的方式加上
   §6.1 缺失字段(hypothesis/family/datasets/lag/frequency/universe/预处理/
   provenance/created_at_ms),旧构造形状逐位不变
   (tests/contracts/test_data_feature.py 零修改通过)。
   `SqliteFeatureRegistryStore` 序列化是全量 `model_dump_json`,加宽字段天然
   round-trip(测试钉死);store 只加了一个 `all_items()` 只读方法支撑
   by_family 扫描。
3. **FactorCategory 不迁入**。`services/portfolio` 的 FactorCategory(风险模型
   维度)是组合风险概念,§6.4 的 FactorFamily 是研究谱系概念——两者命名相似
   但语义不同,迁入会制造「一个枚举两种含义」的静默错配。F1 在
   `quantos/contracts/feature.py` 新增独立的 `FactorFamily` 13 值枚举;
   映射关系留给 F5(family_cluster)显式建立。
4. **factor_version 由注册表层承载**。契约不重复 version 字段——
   `FeatureRegistryItem.version` 已经是版本权威(哈希链按 (id, version) 记账);
   `build_factor_definition` 只校验 factor_version 非空,调用方把同一字符串
   交给 `FactorGardenRegistry.register`。
5. **garden 不变量双层 fail-closed**:builder 层(§6.1/§8.1:假说非空、
   数据依赖非空、真 FactorFamily、表达式过 DSL 自身的 serialize 往返 +
   合成面板求值校验)+ registry 边界层(手工拼的缺字段 FeatureDefinition
   同样被拒)——与 broker_adapter / research_runner 的 chokepoint 同款纵深。
6. **`inputs = required_datasets`**。契约 `inputs` 的语义是「上游 feature/data
   ids」,F1 填数据依赖,保持老消费者(按 inputs 追 lineage)不断。

## 3. F 系列切片计划

| 切片 | 内容 | 状态 |
|---|---|---|
| **F1** | 契约加宽(FeatureDefinition §6.1 字段 + FactorFamily)+ `services/factor_garden/`(definitions + registry 边界,store `all_items()` 只读加法) | **本提交** |
| F2 | canonical 因子收敛:tools/ 双份定义(CARRY 双份、crowd_contra 决策行三套)移入 services 侧注册;QUANTILE 0.2 vs 0.3 分叉**诚实记录为两个版本**,不擅自统一 | **本提交** |
| F3 | materializer + feature_values schema v2:§6.2 FactorObservation 落盘面(+computed_at/dataset_version/materializer_version/quality_verdict) | **本提交** |
| F4 | offline_online_parity + dependency_graph + health:同一核心计算函数不同窗口适配器的 parity test;dataset BLOCK → factor STALE 传播(泛化 forward_track_advance 的语义) | **本提交** |
| F5 | family_cluster:§6.4 家族维度聚类,接入 W3 簇图;显式建立 FactorFamily ↔ 组合风险维映射 | **本提交**(聚类 ✓;FactorFamily ↔ 组合风险维显式映射留评审,见 §10.7) |

切分原则:每片独立可测、独立入库、不依赖后片;F1 不预留「将来一定这样」的
抽象(F3 的物化 schema 可能反过来修正 F1 的字段选择,届时显式评审)。

## 4. F1 实施记录(2026-07-27)

### 4.1 契约加宽(`quantos/contracts/feature.py`)

- 新增 `FactorFamily` str 枚举(§6.4 全部 13 值,测试钉死集合相等)。
- `FeatureDefinition` 新增 16 个字段,**全部可选带默认值**:
  `factor_family_id`(存在时经 field_validator 校验枚举成员)、
  `expression_hash`、`economic_hypothesis`、`required_datasets`、
  `minimum_lag`、`frequency`、`universe`、`normalization`、`neutralization`、
  `winsorization`、`missing_policy`、`expected_turnover`、
  `known_failure_regimes`、`creator_actor`、`creator_model`、`prompt_hash`、
  `created_at_ms`(调用方供给,**绝不默认墙钟**)。
- 旧构造形状不变:`tests/contracts/test_data_feature.py`(13)+ `tests/features`
  零修改通过。

### 4.2 `services/factor_garden/definitions.py`(纯核,无 IO/RNG/墙钟)

- `FactorDefinitionError(ValueError)`。
- `build_factor_definition`:fail-closed 校验(§6.1/§8.1 假说非空、
  required_datasets 非空、family 必须真枚举、表达式必须嵌套 tuple 且过
  DSL 自身的 serialize→deserialize 往返 + 3×2 合成面板 `evaluate` 校验——
  钉死算子名/元数/窗口参数类型;created_at_ms/minimum_lag 非负 int、bool 拒);
  factor_id = name(稳定人类身份,演进由 version 承载)。
- `factor_content_hash`:`stable_hash` 覆盖身份字段(name/family/
  expression_hash/factor_version/required_datasets)——变更检测/审计;
  provenance 字段(created_at_ms/prompt_hash/status)**不进**哈希(测试钉死:
  改表达式变哈希,只改 created_at_ms 不变)。

### 4.3 `services/factor_garden/registry.py`(薄边界)

- `FactorGardenRegistry` 包装 `SqliteFeatureRegistryStore`:register 在边界
  复检 garden 不变量(纵深防御——手工拼的缺假说/家族/数据/哈希的
  FeatureDefinition 即便过了契约也被拒),owner=creator_actor、初始 DRAFT,
  `factor_content_hash` 记入 lineage metadata(审计可查);set_status/get/
  versions/audit 直通;`by_family` 经 store 新加的 `all_items()` 只读扫描。
- 持久化/版本/哈希链历史全部由既有 store 承载——不重造第二份。

### 4.4 测试(`tests/factor_garden/`,22)

- test_definitions.py(14):13 家族钉死、哈希稳定性、交换律等价 dedup、
  §6.1 全字段落契约、五类 fail-closed(空假说/空数据/假家族/非 tuple 表达式/
  未知算子)、created_at_ms 负数拒、向后兼容裸构造、契约层非法家族值拒、
  content hash 两条性质。
- test_registry.py(8):注册→get 全加宽字段 round-trip(**这条抓 store
  序列化漂移**)、versions 累积、by_family 过滤 + 非法家族拒、重复
  (id, version) 拒、set_status 进哈希链历史、边界拒手工缺字段定义、
  跨重开持久化(tmp_path, create=False 重开)、content hash 进审计 lineage。

## 5. F2 实施记录(2026-07-27,canonical 因子收敛)

### 5.1 移动了什么(全部逐位不变,移动非调参)

- 新模块 `services/factor_garden/canonical.py`(纯常量 + 纯函数,import 时零 IO):
  `CARRY_COMPOSITE_EXPRESSION` / `CROWD_CONTRA_EXPRESSION` / `TOP_CONTRA_EXPRESSION` /
  `TREND_COMPOSITE_EXPRESSION` / `FUNDING_FIELD` / `legs(sig_row, q)` /
  `CANONICAL_FACTORS` / `register_canonical_factors`。
- **消灭的双份/多份定义**(移动前已逐一验证逐位相同):
  - `crypto_oi_research.CARRY` ≡ `crypto_carry_strategy.COMPOSITE`(serialize/alpha_id
    均相同,机器验证)→ 两文件改为从 canonical 导入并以历史名 re-export;
    `binance_usdc_alpha_retest.CARRY`(第三份 frozen 副本)同样收敛。
  - `_legs` 两份副本(`crypto_oi_research` / `crypto_carry_strategy`)经
    `inspect.getsource` 逐字节验证相同 → 唯一 `canonical.legs`,两文件别名 re-export。
  - `CROWD_CONTRA` 10 份逐字副本(`crypto_oi_verify` + binance_usdc_alpha_retest/
    crypto_combo_book/crypto_factory_{intraday,loop,orthogonal,overfit_ab}/
    crypto_funding_interactions/crypto_oi_survivorship/crypto_positioning_divergence)
    与 `TOP_CONTRA` 4 份(crypto_oi_verify/binance_usdc_alpha_retest/crypto_combo_book/
    forward_track_advance)→ 全部改为 canonical 导入。
  - 趋势 composite 双份(`crypto_daily_composite.COMPOSITE` ≡
    `dry_run_trend_multi._COMPOSITE`)→ `TREND_COMPOSITE_EXPRESSION`。
  - 对象同一性(非仅相等)由测试钉死:15 个 tools 模块的历史名全部 `is` canonical
    常量,既有 importer/测试零改动。
- `CANONICAL_FACTORS`(3 个 F1 garden 定义:carry_composite=FUNDING_CARRY /
  crowd_contra、top_contra=POSITIONING_CROWDING):真经济假说、
  required_datasets 指向工具实际消费的数据集 id(crypto_funding /
  crypto_funding_rates / crypto_oi_metrics)、frequency=1d、universe 与
  ic_panel_registry 口径一致、creator_actor="quantos-research"、created_at_ms=0。
- `register_canonical_factors(registry, *, recorded_at_ms)`:幂等(同定义已注册 →
  skip;同 (id, version) 不同定义 → raise);由驱动器调用,import 时不执行。

### 5.2 QUANTILE 分叉记录(不统一,人工决策)

- `CARRY_QUANTILE_OFFLINE = 0.2`(`crypto_carry_strategy.QUANTILE`,115 币资金费宇宙
  离线 book)与 `CARRY_QUANTILE_LIVE = 0.3`(`crypto_oi_research.QUANTILE`,~24 币 OI
  宇宙,经 live_track_advance 进 live 轨道)——两个数字背后是不同的前向证据积累,
  F2 仅以两个显式命名常量记录于 canonical.py 模块 docstring,**不统一**;统一是行为
  变更,留人工决策。

### 5.3 诚实边界:trend 不在 CANONICAL_FACTORS

趋势 composite 的生产形式是 `SIGNAL_OPS` (op, window) pair-tuple,经
`ic_panel.signal_composite` 消费——其语义(缺口跳过式历史累积、all-or-nothing
子信号门槛、≥3 有效才 z-score)与 tuple DSL 的 `ts_*` 矩阵行窗口**不逐位等价**;
`build_factor_definition` 强制 DSL,把 trend 硬塞进 CANONICAL_FACTORS 会是再推导而非
移动,违背本切片「逐位不变」纪律。故 F2 只把 pair-tuple 形式收敛为单一真源
(`TREND_COMPOSITE_EXPRESSION`),DSL 原生 trend 因子的注册是另一个显式评审的切片。

### 5.4 测试(`tests/factor_garden/test_canonical.py`,11)

CANONICAL_FACTORS 过 F1 全不变量;expression_hash = 同 tuple 的 alpha_id(身份跨移动
保持);**dedup 证明**(两 carry 名 resolve 到同一对象);trend/crowd/top 同名对象同一性;
QUANTILE 分叉两值钉死;`legs` 语义合成数据钉死 + tools 别名同一性;
register_canonical_factors 全新注册/幂等重开/冲突 raise/输入校验。
回填验证:`tests/tools -k "carry or crowd or oi or trend or dry_run"`(95)、
`tests/factor_garden + tests/tools + tests/research`、
`tests/governance` 全绿。

## 6. F3 实施记录(2026-07-27,物化器 + feature_values schema v2)

### 6.1 `services/features/value_store.py` schema v2(惰性迁移)

- 新列(727 §6.2 FactorObservation):`computed_at_ms INTEGER` /
  `dataset_version TEXT` / `materializer_version TEXT` / `quality_verdict TEXT`,
  全 nullable 无 DEFAULT。`_SCHEMA` 刻意保持 v1 形状,`_ensure_v2_columns`
  (与 `experiment_registry._ensure_triple_columns` 同先例:PRAGMA table_info +
  BEGIN IMMEDIATE 下逐列 ALTER TABLE)是**唯一**加列路径,首次 v2 写触发、幂等;
  旧行读回 NULL = 「物化于 v2 之前」诚实标记,绝不回填。`_SCHEMA_VERSION = 2`。
- 写路径:`add`/`add_many` 加四个可选 kwargs(默认 None → 旧 6 列 INSERT 逐位
  不变、不触发迁移;既有 tests/features 35 测试零改动通过);带任一 v2 元数据 →
  校验(computed_at_ms 非负 int 非 bool;三个 TEXT 非空)→ 迁移 → 10 列 INSERT。
- 读路径(最小侵入裁定):`as_of`/`latest_as_of` 保持返回 FeatureValue 契约逐位
  不变;新增 `as_of_observations` 返回全列 dict(未迁移 v1 库以 NULL AS 回退),
  同一 PIT 过滤与排序。

### 6.2 `services/factor_garden/materializer.py`(纯核 + 注入 store)

- `MATERIALIZER_VERSION = "1.0.0"`;`materialize_factor`:
  `evaluate(deserialize(defn.formula), panel)` → 逐格落
  `FeatureValue(feature_id=defn.name, et, kt=et, value)`;None 格(缺失/warmup)
  不落盘(无观测,沿用 materialize_rolling_feature 先例);NaN/Inf 不落盘、计入
  `skipped_non_finite`;幂等靠 store 主键 INSERT OR IGNORE(重跑 0 新增,测试钉死)。
  返回 {factor_id, factor_version, written, skipped_existing, skipped_non_finite,
  instruments, periods, dataset_version, materializer_version}。
- `materialize_many`:逐因子隔离失败(记 `{"factor_id", "error"}`,与
  services/alpha/maintenance 同先例)。

### 6.3 knowledge_time 语义裁定(kt := event_time)

`FieldPanel` **不携带逐格 knowledge_time**;其构建契约(`build_field_panel`:
`latest_as_of(inst, t, t)` 且仅当最新可知 bar 的 event_time == t 才取值,否则
None)是「格 (t,i) 在 t 可知」。`evaluate` 只组合 ts 算子(回看 ≤t 行)与 cs 算子
(同行 t),故 (t,i) 的因子值完全由 t 可知的数据算出 → **kt := event_time**,与
仓库既有 EOD 数据存储(FRED/Deribit)的 kt=et 约定一致。该 kt 是从 panel 的 PIT
契约**推导**而非虚构;物化器与所有 `evaluate` 消费方同级信任 panel 契约——
调用方自建受污染 panel 时 kt=et 同样被污染(docstring 明文)。

### 6.4 quality_verdict 来源边界

quality_verdict 是**调用方/全局标记**,整批逐行照写;本切片**不做**逐格
leakage/质量检测(那是 F4 的事),绝不编造逐格判定。

### 6.5 测试(16)

- `tests/features/test_value_store_v2.py`(7):手工 v1 库读回 NULL 新列;首次 v2 写
  触发迁移;迁移幂等(二次写 + 重开);v1 写路径不迁移且逐位不变;v2 元数据校验;
  INSERT OR IGNORE 幂等;as_of_observations PIT 过滤/排序/校验。
- `tests/factor_garden/test_materializer.py`(9):合成 panel(2×3)物化行数/主键/
  值 = 直接 evaluate 逐格一致;kt=et + PIT 读(未来 kt 不可见);v2 四列落盘;
  重跑 0 新增;warmup None 不落盘;NaN/Inf 跳过计数(monkeypatch evaluate);
  输入校验;materialize_many 隔离失败;registry→materializer 端到端
  (register_canonical_factors → get → materialize → PIT 读回 + v1 契约读一致)。

## 7. F4 实施记录(2026-07-27,parity + dependency graph + health)

### 7.1 `offline_online_parity.py`(纯函数)

- `ParityReport` 冻结 dataclass(factor_id/compared_cells/mismatches/max_abs_diff/
  tolerance/verdict/detail);`compare_series`(key 交集排序 by repr 逐格比对,
  |diff|>tolerance 计 mismatch,NaN≠NaN/inf≠finite 恒 mismatch、None==None 不计,
  detail 确定性序前 10 条)+ `compare_panels`(形状不符 → MISMATCH 带形状 detail
  不抛;空面板 → INSUFFICIENT_OVERLAP)。
- **fail-closed 裁定:交集为空 → PARITY_INSUFFICIENT_OVERLAP,绝不谎报 OK**。
- **真实 parity 案例**(test_parity.py,本切片最有价值的测试):6 币 × 20 期全填充
  合成 panel,online = **真 live 路径** `live_track_advance._latest_weights_and_prices_for`
  (未改动原函数,直接喂 panel_bundle),offline = **真干跑组合**
  `ax.evaluate(expr, panel)` + `canonical.legs(sig[r], CARRY_QUANTILE_LIVE)`
  (dry_run_crowd_contra_multi.py:139-157 的同一组合)——取 live 选中的同一决策行 r,
  `compare_series(tolerance=0.0)` 对 crowd_contra 与 carry 各跑一遍,PARITY_OK、
  max_abs_diff=0.0。**局限(诚实记录):三套行选择政策(live 回看 ≤7 行 + 覆盖率 /
  前向 paper cadence / 干跑全历史周网格)未统一——parity 只在同一行选择政策下判定,
  统一政策是行为变更,留评审。**

### 7.2 `dependency_graph.py`(纯函数,不可变)

- `DependencyGraph(factor_datasets, alpha_factors, known_datasets=None)` +
  `from_definitions(defns, alpha_links, known_datasets)`;MappingProxyType 不可变,
  全部读返回排序 tuple。`factors_for_dataset` / `datasets_for_factor` /
  `dependents_of_dataset`(dataset 失败 → 直接 factors + 沿 factor→alpha 边传递的
  alphas)/ `topo_factors`(诚实注:§6 图无 factor→factor 边,topo 即排序,
  存在是为给调用方一个稳定评估序)/ `validate()`(悬空引用清单 fail-closed
  供调用方拒;dataset 维悬空只在声明 known_datasets 时才判定——外部数据集宇宙
  不是图能判的,诚实跳过)。

### 7.3 `health.py`(纯核,事实注入)

- `DatasetHealth`(PASS/BLOCK/STALE/UNKNOWN,**UNKNOWN 是诚实缺省:无事实 ≠ 健康**)
  + `FactorHealthVerdict`(HEALTHY/STALE/PAUSED + blocking_datasets + reasons)。
- `evaluate_factor_health`:任一依赖 BLOCK **或 UNKNOWN** → PAUSED(缺证据不放行,
  fail-closed 钉死);仅 STALE → STALE;全 PASS → HEALTHY;无声明依赖/未知因子 →
  PAUSED。`propagate`:全图评估,任一依赖 factor 非 HEALTHY → alpha PAUSED 带
  blocking_factors 明细——forward_track_advance 的 raw_lineage_block_reason /
  live_track_advance 的 batch_skip_reason 语义的图驱动泛化。
- **事实注入裁定**:核心绝不自己读 db/json——三个权威源(admission_store verdict /
  refresh_history 哈希链 / live_refresh_status.json 水印)由调用方/tools 读盘后注入。
- **不写回 registry 裁定**:本切片纯评估;`set_status` 写回会留哈希链痕迹但也是
  自动状态变更,留给驱动器显式调用(模块 docstring 明文)。

### 7.4 测试(24)

- test_parity.py(9):全等/容差内外/交集空 fail-closed/NaN-Inf-None 语义/形状不符
  不抛/输入校验 + **2 个真实双路径 parity 案例**(crowd_contra、carry)。
- test_dependency_graph.py(6):构建/读/传播到 alpha/两类悬空引用/构造校验 +
  不可变/CANONICAL_FACTORS 实图。
- test_health.py(9):全 PASS→HEALTHY;BLOCK→PAUSED 带明细;**无事实 → UNKNOWN →
  PAUSED(fail-closed 钉死)**;STALE→STALE;BLOCK 优先于 STALE;无依赖/未知因子 →
  PAUSED;DatasetHealth 校验;propagate 到 alpha(含 alphas 参数合流与图外因子
  fail-closed)。

## 8. F5 实施记录(2026-07-27,family_cluster)

### 8.1 `family_cluster.py`(纯核,无 IO)

- `FactorClusterAssignment` 冻结 dataclass(factor_id/family(FactorFamily)/
  cluster_id/method∈{DECLARED,CORRELATION,SINGLETON});`FactorClusterError`。
- **声明家族优先**:factor_family_id 无效/缺失 → FactorClusterError(fail-closed,
  绝不猜家族;未知字符串家族在 F1 契约层已被拒,模块层可达形态只有 None)。
- **家族内相关聚类 = 并查集(传递闭包)**:correlation 注入
  ({(a,b):ρ},键序无关、负相关高幅值也并——拥挤反向变体同族,§7.3),|ρ|≥rho_cut
  合并。**刻意不复用 `services/alpha/cluster.py` 的 complete-linkage**:那个实现
  (a) 签名吃 return_series 自算相关,与本模块「相关注入」边界不合;(b) complete-
  linkage 明确要求**禁止链式**(P2-3),与本切片规定的传递闭包语义直接冲突。
  诚实记录链式风险:并查集是单链语义,a-b/b-c 会把 a-c 不相关的两端拉进同簇——
  对 §7.3 的用途这是**保守方向**(更宽的簇 = 声称的独立赌注更少,绝不更多),
  也是 rho_cut 保持显式 UNCALIBRATED 工程默认(0.5,W3 同口径)的原因。
- correlation=None → 全 SINGLETON(诚实:无相关证据 = 不声称冗余也不声称独立);
  有相关图但本因子无任何边 → DECLARED(仅声明家族可归)。
- cluster_id 确定性:`<FAMILY>#<簇内最小 factor_id>`(插入序无关,测试钉死);
  簇内并查集 root 取字典序最小者。
- `family_of` + `cluster_summary`(per family/cluster 计数,确定性序)。
- **§7.3 接缝(只接口不整合)**:cluster_id 就是 family-level DSR/FDR 的分组键
  (trial 多重性应按 cluster_id 计,不按单个 factor_id)——docstring 明文,
  但**不改** family_testing/ranker/multiple_testing 任何现有代码(整合是调用方
  的事,单独评审)。

### 8.2 测试(`tests/factor_garden/test_family_cluster.py`,11)

声明家族分组 + family_of;缺失家族 fail-closed(None 形态);传递闭包 a-b/b-c →
a,b,c 同簇;低于阈值各 SINGLETON;跨家族 ρ=0.99 也不并簇;correlation=None 全
SINGLETON;无边因子 DECLARED;cluster_id 确定性(同输入同 id、插入序无关、键序
无关);负相关高幅值并簇;相关图/阈值/重复 id 校验;summary 计数。

## 9. 727 Phase 4 验收对照表(F1–F5 全部落地,2026-07-27)

| 验收项 | 状态 | 落点 |
|---|---|---|
| 稳定 factor_id/version(身份 + 变更检测) | ✓ F1 | `build_factor_definition`(factor_id=name 稳定人类身份,version 注册表承载)+ `factor_content_hash` 身份字段哈希;`expression_hash=alpha_id` |
| 历史与实时同逻辑(offline/online 同一计算) | ✓ F2+F4 | F2 消灭全部双份定义(单一 canonical 真源,对象同一性钉死);F4 parity 真实双路径案例(真 live 函数 vs 真干跑组合,同一决策行 tolerance=0 PARITY_OK;三套行选择政策不统一属显式非目标) |
| 依赖失败传播暂停 | ✓ F4 | `health.evaluate_factor_health/propagate`:BLOCK/UNKNOWN → factor PAUSED → alpha PAUSED 带明细(fail-closed;泛化 forward_track/live_track 语义);verdict 写回 registry 留驱动器显式决策 |
| 物化(§6.2 FactorObservation 落盘面) | ✓ F3 | `materializer.py` + feature_values schema v2(computed_at_ms/dataset_version/materializer_version/quality_verdict;kt:=et 裁定;幂等;旧行 NULL 诚实标记) |
| 家族聚类(§6.4/§7.3 先聚类再 family-level FDR) | ✓ F5 | `family_cluster.py`(声明家族 + 家族内并查集,cluster_id = FDR 分组键接缝) |

遗留诚实边界(全部显式非目标,见 §10):逐格 leakage 检测未做;三套行选择政策
未统一;FactorFamily ↔ 组合风险维(FactorCategory)显式映射留评审;cluster_id
未接入任何 ranker/FDR 代码;QUANTILE 0.2/0.3 分叉未统一(人工决策)。

## 10. 明确非目标(F1–F5 不做,单独评审)

1. **不统一 QUANTILE 0.2 vs 0.3 分叉**——那是行为变更(两个数字背后是不同的
   前向证据积累),需人工决策;F2 已把两值以显式命名常量
   (`CARRY_QUANTILE_OFFLINE`/`CARRY_QUANTILE_LIVE`)诚实记录于 canonical.py,
   统一留人工。
2. **不接 console**——Phase 11 的事;F1–F3 无 HTTP 面。
3. **不迁移 factor_engine**——字符串 AST 编译器维持隔离/死码现状,删除或重建
   是独立决策(与 quarantined `trading/pre_trade_risk` 同款处置)。
4. **不做逐格质量/leakage 检测与 verdict 自动写回**——F3 的 quality_verdict 是
   调用方/全局标记;F4 的 health 是纯评估(UNKNOWN→PAUSED fail-closed),
   set_status 写回留给驱动器显式调用;逐格 leakage 检测仍需独立评审。
5. **不统一三套行选择政策**——live 回看/前向 cadence/干跑周网格各有语义,
   parity 只在同一政策下判定;统一是行为变更,需评审。
6. **不接任何晋级/下单路径**——诊断非门禁立场不变;物化只写 feature_values
   观测,health 只产出 verdict,不改变任何评估门、mandate 或 live 路径。
7. **不建 FactorFamily ↔ 组合风险维(FactorCategory)映射、不把 cluster_id 接进
   ranker/FDR**——F5 的 table 行所述「接入 W3 簇图/显式映射」以接缝形式交付
   (cluster_id 即分组键,§8.1);真正的整合改的是 W3/多重检验承重代码,单独评审。

## 11. L2 边界声明

- 五个安全旗标、live-order 路径、凭证零触碰;`services/factor_garden` 零网络
  import(治理扫描机器钉死)。
- 确定性核心:无墙钟(created_at_ms/recorded_at_ms 全为调用方 int ms)、
  无 uuid(factor_id = name + version,哈希全部内容派生)。
- 单节点 SQLite 权威,与所包装的 feature registry 同边界;跨主机共识 = post-L2。
