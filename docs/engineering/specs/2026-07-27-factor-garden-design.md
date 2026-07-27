# Factor Garden 设计与 F 系列切片计划(727 §6 / Phase 4)

- 日期:2026-07-27
- 依据:[`docs/QUANTOS_727.md`](../../QUANTOS_727.md) §6(Factor Garden:
  §6.1 FactorDefinition / §6.2 FactorObservation / §6.3 offline-online 一致性 /
  §6.4 因子家族)、§8.1(无经济解释的表达式禁令)
- 状态:**F1 已落地(本提交)**——契约加宽 + `services/factor_garden/`
  (definitions + registry 边界,22 测试);F2–F5 计划见 §3。
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
| F2 | canonical 因子收敛:tools/ 双份定义(CARRY 双份、crowd_contra 决策行三套)移入 services 侧注册;QUANTILE 0.2 vs 0.3 分叉**诚实记录为两个版本**,不擅自统一 | 计划 |
| F3 | materializer + feature_values schema v2:§6.2 FactorObservation 落盘面(+computed_at/dataset_version/materializer_version/quality_verdict) | 计划 |
| F4 | offline_online_parity + dependency_graph + health:同一核心计算函数不同窗口适配器的 parity test;dataset BLOCK → factor STALE 传播(泛化 forward_track_advance 的语义) | 计划 |
| F5 | family_cluster:§6.4 家族维度聚类,接入 W3 簇图;显式建立 FactorFamily ↔ 组合风险维映射 | 计划 |

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

## 5. 明确非目标(F1 不做,单独评审)

1. **不统一 QUANTILE 0.2 vs 0.3 分叉**——那是行为变更(两个数字背后是不同的
   前向证据积累),需人工决策;F2 只把两份定义注册为两个版本并诚实记录。
2. **不接 console**——Phase 11 的事;F1 无 HTTP 面。
3. **不迁移 factor_engine**——字符串 AST 编译器维持隔离/死码现状,删除或重建
   是独立决策(与 quarantined `trading/pre_trade_risk` 同款处置)。
4. **不驱动物化**——F3 才落 §6.2;F1 的注册表只登记定义,不产生因子值。
5. **不接任何晋级/下单路径**——诊断非门禁立场不变。

## 6. L2 边界声明

- 五个安全旗标、live-order 路径、凭证零触碰;`services/factor_garden` 零网络
  import(治理扫描机器钉死)。
- 确定性核心:无墙钟(created_at_ms/recorded_at_ms 全为调用方 int ms)、
  无 uuid(factor_id = name + version,哈希全部内容派生)。
- 单节点 SQLite 权威,与所包装的 feature registry 同边界;跨主机共识 = post-L2。
