# 因子方法论实现地图(QUANTOS)

> 本文把"因子发现方法论全谱"逐项对照 QUANTOS 的**真实实现状态**,避免过度宣称。
> 约束前提:**纯 Python 标准库**(禁 numpy/scipy/torch)、确定性、可复现、离线门禁、
> 容器化(非人类 agent 永不可达 APPROVED)。
> 状态图例:✅ 已实现 · 🟡 部分/差一点 · ❌ 未实现。
> 最近更新:GP 表达式树 + 去冗余 + Purged/Embargoed CV + 三重门/元标签(2026-06)。

---

## 一、四类总览(先看结论)

| 类别 | 状态 | 一句话 |
|---|---|---|
| **严格性 / 不自欺层**(多重检验、Purged CV、容量成本) | ✅ 基本齐全 | 我们的护城河,很多机构这块都不扎实 |
| **机器发现**(GP 表达式树、进化、去冗余协同集) | ✅ 已建 | "动态自然发现因子"的正解已落地 |
| **标签工程**(三重门、元标签) | ✅ 已建 | 研究层原语,尚未接自动晋级 |
| **经济先验因子**(价值/动量/质量/低波/carry) | 🟡 部分 | 价格类全,价值部分,质量/carry 缺数据 |
| **纯 Python 能做但没做**(Regime、择时、分数阶差分、信息论) | ❌ 待补 | 不卡技术,只是还没排期 |
| **重型 ML / 深度学习 / RL / GNN / LLM / 另类数据 / 因果** | ⛔ 卡口子 | 需引入依赖或新数据,**需人决策** |

---

## 二、逐项对照

### A. 假设驱动(经济先验)因子 —— 最 durable(衰减慢)
- 动量 / 反转 / 低波 / 趋势 —— ✅ `services/research/signals.py` 8 个算子
  (momentum、ts_mean、zscore、volatility、ts_rank、ema_ratio、dist_from_high、accel)
- 价值(E/P、B/M)—— 🟡 `services/research/fundamental_factors.py` + 基本面库(股票可用)
- 质量(ROE、利润率)—— ❌ 缺数据 + 对应算子(基本面库支持任意字段,差数据)
- carry / 期限结构 —— ❌ 缺数据(加密资金费、期货期限)

### B. 统计挖掘 + 严格性 —— **分水岭,我们最强**
- 多重检验惩罚:Deflated Sharpe、PBO、var_sr、BH-FDR —— ✅ `services/alpha/`,
  且进化/GP 放大搜索时门槛按试验数自动加重
- Harvey–Liu–Zhu 的 t>3 门槛 —— 🟡 有 t 值(ic_tstat),但主闸用 Deflated Sharpe,
  未设显式 "t>3" 硬规则
- Purged & Embargoed K-fold CV —— ✅ `services/alpha/walk_forward.py`(embargo 禁运)
  + `services/research/oos_validation.py`(无窥视滚动选择)

### C. 遗传编程 / 符号回归(GP)—— "机器自然发现公式"
- 表达式树 + 子树变异/交叉 —— ✅ `services/research/gp_tree.py` + `gp_genome.py`,
  作为既有"固定算子 + 2 腿交互"的真超集;经设计评审 + 对抗式代码评审硬化

### D. 机器学习(GBDT/XGBoost/神经网络)—— 现代主力
- ⛔ **未实现**:需 numpy/torch,与纯确定性 + 离线门禁冲突 → 依赖决策口子

### E. 深度学习前沿(IPCA / 条件自编码器 / FactorVAE / GNN / Transformer)
- ⛔ **未实现**:同上,需重型数值/深度学习依赖

### F. 强化学习前沿
- RL 协同 alpha 集(AlphaGen/RiskMiner 的"组合最优"思想)—— 🟡 **目标已达**:
  我们用确定性"增量-IC 前向选择"(`services/research/factor_set.py`)选协同因子集;
  **RL 方法本身** ❌(需 ML)。bandit/进化是 RL 的近亲 ✅
- RL 做发现/执行/配置 —— ❌(需 ML)

### G. 另类数据 + NLP / LLM
- ⛔ **未实现**:需新数据源(卫星/信用卡/链上/情绪)+ LLM 抽因子 → 数据决策口子

---

## 三、"容易被忽略但强且有用"逐项

| 方法 | 状态 | 位置 / 说明 |
|---|---|---|
| 协同因子集 ≠ 贪心 Top-N | ✅ | `factor_set.select_synergistic_set`(按增量信息前向选择) |
| 正交化驱动的**增量发现** | 🟡 | 增量"选择"✅;增量"**搜索奖励**"(发现当下就去重)❌ 待接 |
| Meta-labeling | ✅ | `services/research/labeling.py`(方向/下注分离) |
| 标签工程 / 三重门标签 | ✅ | `labeling.triple_barrier_labels`;**唯一性样本加权** ❌ |
| 因果(Double ML / IV / 因果发现) | ❌ | 多数需 ML |
| Regime 条件化 + 隐马尔可夫 | ❌ | 纯 Python 可做,**下一步候选** |
| 因子择时 | ❌ | 纯 Python 可做,待排期 |
| 分数阶差分 | ❌ | 纯 Python 可做,待排期 |
| 信息论选择(互信息/转移熵) | ❌ | 纯 Python 可做,待排期 |
| 容量 / 成本感知 alpha | ✅ | 成本模型 + 容量调整夏普(`services/alpha/capacity.py`) |
| 集成 / 堆叠 + 对抗鲁棒性 | 🟡 | 代码做了对抗式评审;**模型集成** ❌(需 ML) |

---

## 四、两个需要"人来拍板"的口子

1. **是否引入重型 ML 依赖**(numpy/torch):解锁 D/E/F(GBDT、神经网络、IPCA/自编码器、
   GNN、深度 RL)。代价:牺牲"纯确定性 + 离线门禁 + 可复现",是另一套工程范式,
   需要明确授权。
2. **是否接更好的数据**:解锁质量/carry 因子、另类数据、情绪、链上。代价:数据成本 +
   网络面(只读、门禁外抓取的安全模式已就绪)。

> 元真相:**边际 ≈ 20% 模型 + 80%(数据 + 严格性 + 执行 + 不过拟合)**。
> 我们已把"严格性 + 不自欺 + 机器发现 + 去冗余 + 标签工程"这条线做扎实;
> 再往上的天花板主要由**数据**和**是否破例引入 ML**决定,而非缺代码能力。

---

## 五、纯 Python 约束内的后续优先级(建议)

1. ✅ GP 表达式树 · ✅ 去冗余/协同集 · ✅ Purged/Embargoed CV · ✅ 三重门/元标签(已完成)
2. **增量搜索奖励**(把正交化接进进化/GP 的搜索奖励,发现当下就不重复挖)—— 便宜、立竿见影
3. **Regime 条件化评估**(按市场状态分档看因子)
4. 唯一性样本加权 · 因子择时 · 分数阶差分 · 信息论选择(零散增强)
