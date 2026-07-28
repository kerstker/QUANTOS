# TrialRecord 溯源 + 家族级多重检验设计（727 §7）

- 日期：2026-07-27
- 依据：[`docs/QUANTOS_727.md`](../../QUANTOS_727.md) §7（文档自称「扩大 AI 挖掘前的最高优先级」）
- readiness：**L2_DRY_RUN_ONLY 不变**。本次改动不碰订单、凭证、券商、五个安全旗标。
- 证据：`evidence/trial_ledger_integrity.json`（本次新增，只读生成器 `scripts/generate_trial_ledger_integrity.py`）

## 0. 先纠正主提示词的一个过时假设

727 文档 §0.1 把「全局 Trial Ledger 仍是 PARTIAL」列为关键缺口，并在 §7 要求**新建**
`services/alpha_factory/{trial_ledger,trial_store,trial_hash_chain}.py`。

按 §0.2 证据优先级（当前可执行源码 > 文档）核查后，**这个假设是过时的**：

账本早已存在且相当强 —— `services/research/experiment_registry/store.py` 拥有
**13 张表、全部带 append-only UPDATE/DELETE 触发器、三条独立哈希链**
（`search_attempt_events.event_hash` / `search_candidates.candidate_hash` /
`search_evaluations.link_hash`），外加 `tools/search_trial_ledger_history.py`
把校验过的快照留存进 `evidence/alpha_trial_ledger_history.json`。生产库现况
（本次实测）：**2533 个 trial、17644 个搜索候选、212 个 evaluation、71 个搜索事件**。

所以**没有**新建第二套账本 —— 那会造出两个互相矛盾的权威来源，正是 §22.2/§22.12 禁止的。
真实缺口只有两个，本次针对性补齐：

| 真实缺口 | 证据 |
|---|---|
| `trials` 表只有 **2 列**（`config_hash`, `first_seen_ts`），§7.1 要求 ~28 字段的 TrialRecord | 实测 schema |
| 只有 flat BH/BHY/Bonferroni/Holm，**无家族级 FDR** | `services/alpha/multiple_testing.py` 通读 |

## 1. 架构决定：provenance 是**伴生表**，不是给 `trials` 加列

`trials` 表带 `trials_no_update` 触发器。给它 `ALTER TABLE ADD COLUMN` 在 sqlite 里
可行，但会让「一个 config 一行」的语义与 §7.2 打架：

> §7.2：「重复候选也必须记账」「参数扫描的每个配置都计数」

这两句拉向不同方向。`trials` 按 config **去重**（这对 trial *计数* 是正确的 —— 同一 config
重跑不该让 DSR 分母虚增）；但「每次**尝试**都要记账」要求同一 config 的两次尝试留两条记录。

裁定：**尝试多、去重 config 一**。
- `trial_provenance` 新表，主键 `trial_id`（一次尝试一行），`config_hash` 是**外连接键**；
- `trials` 一字不改（零回归风险，触发器/既有测试全不动）；
- 两次尝试同一 config → 2 条 provenance 行 + 1 条 trial 行，两条规则同时成立。

## 2. 事务不可分割：provenance 与 trials 同事务写

`record_trial_provenance()` 在**同一个 `BEGIN IMMEDIATE`** 里同时写
`trial_provenance` 和 `INSERT OR IGNORE INTO trials`。

理由是承重的：若两次写可分离，一次真实尝试就可能有 provenance 却**逃出 DSR/PBO/FDR 分母**
（§7.2 明令 trial count 必须来自权威账本）。`trial_provenance_chain_valid()` 也会把
「provenance 行的 config 不在 trials 里」判为链无效 —— 即便有人强行拆开这两个写。
测试 `test_provenance_for_a_config_absent_from_trials_is_rejected_by_verify` 钉死此点。

## 3. 迁移：复用既有已测机制，版本 2→3

不写新迁移代码。既有 `_ensure_search_schema()` 已具备：`user_version` 门控 +
`BEGIN IMMEDIATE` 加锁 + 逐语句 `_migration_checkpoint` 测试缝 + 任何异常整体回滚 +
提交前 `_require_search_schema()` 验证。

它**重放整个 `_SEARCH_SCHEMA` 脚本**、每条都是 `CREATE ... IF NOT EXISTS`，所以只需：
1. 把 `trial_provenance` 表 + 2 索引 + 2 触发器**追加**到 `_SEARCH_SCHEMA` 末尾；
2. `_SEARCH_SCHEMA_VERSION` 2 → 3。

v2 老库首次 provenance 写入时惰性迁移：只新增表，**既有行一条不动**
（`test_existing_v2_store_migrates_and_keeps_its_rows` 验证 legacy trial 与
`search_history_valid()` 都存活）。

前向兼容已核对：`tools/search_trial_ledger_history.py` 的 `LEDGER_CONTRACT_VERSION = 2`
用 `schema_version < 2` 判定，`3 < 2` 为假 → 放行；它的触发器检查是固定 5 表清单，
加第 6 张表不影响。**留存的哈希链快照史保持有效，无需重签。**

## 4. §7.1 的诚实边界

`TrialProvenance`（`services/research/trial_provenance.py`）是纯值对象：无 IO、无墙钟、
无 RNG，时间戳与标识符全部调用方提供 → 重放逐位一致。

关键字段与设计取舍：

- **`viewed_oos_boundaries`** —— §7.2 要求「OOS 查看次数必须记录」，§0.4.7 禁止
  「把历史回测 OOS 反复查看后仍称为未见数据」。存**去重后的边界集合**而非计数器：
  计数器可被重置，集合让「同一边界看了 3 次」与「看了 3 个不同边界」不再混同。
  `oos_peek_count` 是派生属性。
- **`actor_type` 是独立枚举**，不与 `actor_id` 合并 —— 防止非人类 actor 被字符串格式化
  「洗白」成人类。
- **`status` 承载 §8.3 漏斗的淘汰层**（15 个 `ELIMINATED_*` 值）。fail-closed 硬规则：
  终态失败**必须**带 `failure_reasons`，否则构造即 `ValueError`。理由见 §22.11 ——
  没人能复核的淘汰不是审计轨迹。
- **`failure_reasons` 保持有序**（漏斗产出顺序是信息），而 `factor_ids` /
  `dataset_versions` / `viewed_oos_boundaries` 规范化为**排序去重** ——
  调用方的迭代顺序不该改变同一次尝试的身份哈希。
- **NaN/Inf 指标直接拒绝**：会破坏 canonical JSON 往返，导致留存的 `entry_hash`
  重载后无法验证。

## 5. §7.3 家族级 FDR

文档给的例子本身就是问题陈述：

```
rank(global_ls)  zscore(global_ls)  winsorized_rank(global_ls)
mean_7d(global_ls)  mean_14d(global_ls)
```

一个经济想法穿了五件变换的外衣。flat BH 按「五个独立赌注」花 FDR 预算，家族里最好的
成员因此跨过了一道**从未真有五倍宽**的门槛。

`services/alpha/family_testing.py` 只补层级，不重造 flat 透镜（BH/BHY/DSR/effective_trials
继续由 `multiple_testing.py` 拥有），也不重造聚类（复用 `cluster.py` 的**完全连锁**
—— 它刻意避免单连锁链化）：

| 函数 | 作用 | 关键取舍 |
|---|---|---|
| `assign_families` | 家族归属 | **声明的经济家族优先于样本相关性**。反过来会让「本样本里相关性恰好弱」的同机制家族被拆开，悄悄恢复我们正要消除的膨胀 |
| `simes_pvalue` | 家族级单一 p 值 | 家族内是强正相关，Simes 在正依赖下有效；取 min 反保守不足，Bonferroni 丢弃联合证据 |
| `hierarchical_fdr` | 两阶段（Benjamini-Bogomolov） | 阶段二的水平是 `q * R/n_families`。**没有这个缩放**，多个家族存活后整体 FDR 就会漂过 q |
| `family_adjusted_trial_count` | 家族感知 trial 计数 | 每家族按 `effective_trials(n, 家族内平均 abs corr)` 计费后求和。缺相关性输入时**按全额成员数计费**（保守方向，只可能让 deflation 更严，绝不派发折扣） |
| `marginal_ir` | 边际 IR | 见下 |

无 p 值的家族记 `NO_PVALUE` 且**不入选** —— §9.2 禁止证据缺失默认 PASS。

## 6. 测试抓到的两个真实实现缺陷（记录在案）

诚实记录：本次测试抓到我自己实现里的两个真 bug，不是测试问题。

**① `marginal_ir` 曾用 `mean(residual)`，恒返回 ≈0。**
回归带截距时残差均值**恒为 0**，该函数因此是个废函数。正确定义是
**截距 / 残差标准差**（appraisal ratio）—— 截距才是边际 alpha。已修。

**② 近乎完全共线时返回浮点垃圾。**
候选 = 1.5 × 现有仓位（精确重标）时，残差 sd 与截距都塌到 1e-18 量级，
**两者相除得到 -0.249** —— 一个看起来像真 appraisal ratio 的纯浮点噪音。
绝对的 `sd > 0` 守卫抓不住。已加**相对量级守卫**：未解释方差比
`(sd_resid / sd_candidate)² < 1e-12` 即返回 `None`。
理由正是 §0.4.4/§22.13 —— 完全冗余时该比值数学上**未定义**，报 0.0 是
「伪造一个没人做过的测量」。

## 7. P5-2：官方 research session 接线

`services/intelligence/research_session.run_research_session` 现在在官方持久化路径中
把候选定义传给 `trial_provenance_for_definition()`，并在 provider 读取市场数据前写入
`TrialProvenance`。因此 guided、evolutionary/GP、MCTS 三条 session 路径共享同一接缝：

- `search_candidates` 先登记 config hash；
- provenance 写入失败会抛出 `TrialPersistenceError`，搜索不继续；
- 能知道的 factor、universe、参数、dataset、actor 和时间戳写入；
- 无法从通用 `AlphaDefinition` 可靠推导的表达式、hypothesis、code hash 保持为空，绝不猜测；
- `trial_id = search_id:config_hash` 保持单 session/config 幂等，跨 session 仍分别计数。

这使五类已知 selection-bearing entry（factory、registered batch、official research
session、alpha-family discovery、robust grid）具备 provenance 写入能力，但不回填历史
bare trials，当前制品仍可诚实保持 PARTIAL。

## 8. 制品诚实定位：PARTIAL 不是 PASS

`evidence/trial_ledger_integrity.json` 当前结果：

```text
verdict           = PARTIAL
attempts_recorded = 0
bare_trials       = 2533
coverage_ratio    = 0.0
trial_provenance  = TABLE_PRESENT
trial_oos_access  = TABLE_PRESENT (生产库已由 user_version=3 增量迁移到 v4)
其余 append-only 表 = 全部 GUARDED
```

**机制已落地并可验证，但生产账本 2533 个历史 trial 的 provenance 覆盖率是 0。**
制品据此判 `PARTIAL` 并仅保留一条 `open_gaps`：

1. `TRIAL_PROVENANCE_NOT_YET_WRITTEN_BY_EVERY_ENTRY_POINT` —— 五个已知的选择性研究入口
   已接线，但历史入口和未来绕过 canonical registration 的 driver 仍可能只登记裸
   config hash；`bare_trials` 保持非零、制品保持 PARTIAL。
我**没有**为了让制品好看而回填历史 —— 那是 §0.4.3/§22.14 禁止的伪造证据。
本次仅在精确的既有 production DB 上执行 v3→v4 schema migration；它只创建缺失的
OOS access table/index/append-only triggers，未写入任何历史 provenance 或 OOS 行。

家族级 FDR 已作为 **BHY 之外的附加门**接入 `AlphaLifecycleManager`：候选必须同时通过
既有全局 BHY 与 family-level/within-family FDR，绝不以家族门替换或弱化全局门。
官方 research session 也已把 walk-forward 与 CPCV 实际返回的测试区间追加到独立
`trial_oos_access` 哈希链；TrialRecord 仍在数据访问前写入且不可变。OOS 账本写失败时
搜索按 `FAILED` 终止，不能带着未记账的 OOS 结果继续。

## 9. 非目标（本次明确不做）

- 不新建 `services/alpha_factory/` 包（账本已存在，见 §0）。
- 不回填 2533 条历史 trial 的 provenance —— 那些字段当时没被记录，事后编造即伪造。
- 不回滚或重做 `AlphaState` 的三态拆分（§3）—— C2/C3 已在
  `services/lifecycle/`、`services/alpha/catalog.py`、experiment-registry 持久化与
  console snapshot 中落地；本设计只记录 Trial Ledger 的真实缺口。

## 10. 当前后续优先级（非本设计的已完成项）

**补齐可审计的 provenance 覆盖与外部前向证据。** §3 的三个正交状态机已经存在，
但历史 `AlphaState` 仍作为兼容投影保留，不能据此声称所有旧记录都有完整三态字段。
当前可继续推进的顺序是：

1. 让所有新增 selection-bearing driver 复用五个已接线入口与官方 OOS 访问记录器；
   不回填历史裸 trial。
2. 获得真实 Paper 前向周期、MEXC 外部只读/`order/test` 以及 Phase 12 外部故障矩阵
   证据；缺少外部条件时继续保持 `BLOCKED`。
