# 自主多维正交发现:能力落地 + 诚实的"薄空间"结论

> 日期 2026-06-28 · 全部为工具实测观察值,非捏造 · 容器化不变(genome 无 actor、驱动不碰 lifecycle、
> `auto_approved` 恒 0、无 live/flags)。
> 复现:`python -m tools.crypto_factory_orthogonal`(放大搜索)、`crypto_factory_loop`(loop-until-dry)。

## 目标(用户愿景)

让**机器自主在多维度、多方向上发现 edge,包括人类没指定的因子**,而不是只跑手写网格。

## 做了什么(三件套)

**① 能力硬化进引擎**(`services/research/alpha_factory.py`,已测、门绿):
`run_alpha_factory(basis_returns=已知边)` —— 给定已知边的对齐收益流时,搜索奖励从"经济 Sharpe"切到
**"对 basis 正交的增量 IR"**(novelty 加权)。机器被逼着去**未探索维度**找新边,而非重收敛 carry。
配套加 `alpha_expression.deserialize`(serialize 精确逆,round-trip 测)。cs_genome 本就字段无关。

**② 加 basis 维度 + loop-until-dry 穷举**:
- 第 9 维 `basis` = perp 标记价(`oi_value/oi`)/ 现货 − 1(真实 perp 溢价;发现 `crypto_funding.db` 的
  close 其实是现货源)。basis-carry 与资金费 carry **正交(R² 0.021)**——合法独立维度。
- `crypto_factory_loop.py`:每轮把过**完整 gauntlet**(正交 R²<0.2 + PIT + 滞后 lag1>0.5 + 成本 net10>0.4
  + 非 dup)的最佳新发现并入 basis,下轮对它也正交,直到连续 2 轮枯竭。

**③ 对最强机器发现跑 4 路对抗验证**(量×价跨维度因子)。

## 机器实际找到了什么(观察值)

- **8 维放大搜索(192 因子)**:机器自主长出**量×价跨维度交互**
  `cs_rank(add(cs_scale(volume), ts_rank(close,14)))` —— in-sample raw SR **1.38**、与 carry/crowd 完全
  正交(R² 0.011)、PIT 安全。**但这是脆弱假象(见下被证伪)。**
- **loop-until-dry(9 维)**:机器挖到**恰好 1 个**新正交边
  `cs_rank(sign(cs_rank(top_ls)))`(top-trader 持仓,SR 0.57 / lag1 0.56 / net10 0.49 / R² 0.004)后
  **枯竭**(round 2-3 挖不到)。全发现书无窥视 OOS mean 0.73。

## 量×价因子 = REFUTED(4 路对抗验证,全 kill-shot,全在真实数据复现)

| 角度 | kill-shot |
|---|---|
| **同 bar 时序** | gross SR 滞后 0→1 **1.41→0.66(−53%,成本前就崩)**——整条边靠同 bar 成交 |
| **成本** | lag1 净@10bps **−0.001**、@20bps −0.66(亏钱);盈亏平衡 ~10bps |
| **IC(决定性)** | 截面 rank-IC **每个 horizon、6/7 年都为负**——Sharpe 来自"买高量+近14日高点"的**做多偏置 tilt**(多腿 +14.68bps/天、空腿 −2.23 亏),不是平衡截面边 |
| **regime** | 正 Sharpe 只集中在 3 个强趋势年(2020/2022/2024),其余近零 |

**关键对照**:crowd_contra(lag1 净 0.498)、carry(0.516)在**同一**成本/滞后下**都扛住**——证明 gauntlet
校准正确,量×价是真不行。综合裁决:**REFUTED**——"高换手的同 bar 量×短动量**时机 tilt**,整条 Sharpe
只活在零滞后零成本;正交且新颖,但晚一天或付 10bps 就一文不值。"

## 诚实结论:**稳健正交 alpha 空间是"薄"的**

免费加密**日频**数据里,能扛住滞后+成本+跨 regime 的、互相正交的稳健 edge 就这么几个:
**资金费 carry(可部署 ~2.0)+ 散户持仓 crowd_contra(幸存者后 ~0.9)+ 1 个弱 top-trader 持仓边**,
然后机器就挖干了。漂亮的 in-sample 高 Sharpe(量×价 1.38)经严格验证多是同 bar 时序/选择假象。

**这恰恰是平台该有的样子**:机器广撒网自主找候选(含跨维度、人没写过的),诚实机器(IC/滞后/成本/
多重检验/幸存者)**有牙齿地毙掉假阳性**——哪怕它纸面 1.38 Sharpe。能力(自主多维发现)已落地并放大;
诚实的负结论(空间薄 + 量×价被证伪)和正结论(crowd_contra)一样如实归档。

## richer-data 试验 #1:日内持仓动态 = 也被挖尽(2026-06-28,`tools/crypto_factory_intraday.py`)

第一个 richer-data 方向:metrics 归档本是 **5 分钟**快照,此前只留 eod。提取 6 个**日内派生日频特征**
(oi_range/oi_trend/gls_trend/gls_range/taker_mean/oi_surge = eod 丢弃的持仓动态),panel 扩到 **15 维**,
对已知边 {carry, crowd_contra, top_ls} 跑正交自主搜索(230 个因子)。
**结果:0 个幸存者。** 日内派生因子纸面 SR 0.86–0.97 不错,但**全是同 bar 假象**——lag1 全 ≈0 或负
(−0.02/−0.12/−0.63)、net@10bps 深负(−0.81/−0.98/−1.56),无一过滞后+成本。
**诚实结论:更细的(日内)持仓数据没解锁新正交边——日内动态是同 bar 时序噪声,eod 的 crowd_contra 已把
那点信号吃干净。** 即:**同种数据 finer resolution 不是杠杆。**

## 下一步(数据维度是真瓶颈,且要"另一种"数据)

机器已能在给定维度穷举正交边;日频价/量/资金费/持仓/基差 + 日内持仓动态都已挖尽。要找**更多**真 edge,
瓶颈不是搜索、也不是同种数据更细,而是**另一种**数据:**盘口微结构(order-book imbalance/depth)、
链上流(交易所流入流出/巨鲸)、或跨资产**。引擎(自主多维正交搜索 + 诚实 gauntlet)已就绪,喂它一种
真正新的数据就能继续自主挖。
