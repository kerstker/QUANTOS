# 拥挤门校准 —— "平均分是错的统计量,那正确的值是什么?"(2026-07-07)

> 由 `services/portfolio/crowding_gate.py` 引用。工具:`tools/crowding_metric_research.py`。
> 2026-07-13 已升级为机器可复算证据：234 周冻结输入在
> `evidence/datasets/crowding_gate_calibration_v1.jsonl.gz`，manifest 绑定 4 个原始制品哈希，
> 汇总与实现哈希在 `evidence/crowding_gate_calibration.json`，统一门禁执行 `--check`。

## 用户的问题

Phase 3 校准发现 crowd_contra 的 gross 加权**平均拥挤分**结构性钉死在 ≈0.50。用户 2026-07-07 追问:
"承认平均分是错误统计量,但正确的值应该是多少?"——本文档用实证回答。

## 实证发现:不是"平均分该设多少",而是**所有 rank 阈值都不对**

234 周全历史中，干跑 trailing-global_ls 的 **rank-based 统计量结构性恒定**:

| 统计量(cross-sectional rank) | mean | min–max | 为什么无信息 |
|---|---|---|---|
| gross 加权**平均**拥挤 | 0.500 | 0.500–0.500(零方差) | 对称 L/S book + 腿内 rank 的**数学恒等式** |
| **max** 单名拥挤 | 1.000 | 1.000–1.000 | 截面 rank 天然有 top 名 |
| **短腿**加权拥挤 | 0.769 | 0.692–0.773 | 总是 short 最拥挤的(rank 顶端),结构性 |
| 长腿加权拥挤 | 0.231 | 0.227–0.308 | 结构性镜像 |
| **max 单名权重/gross** | 0.073 | 0.071–0.083 | book 极分散,**集中度判据从不触发**(远低于 0.25) |

live 路径的 rank 定义不同：它对**当期** `abs(global_ls-1)` 做全截面 rank。正式证据显示 live
gross-average mean=0.5028、p90=0.5031、range=0.5000–0.6553，short-leg mean=0.8692，
long-leg mean=0.1364。它不是严格常数，但仍只是相对排位；live 与 dry-run 都显式传
`average_warn_only=True`，因此 rank 平均分只产生诊断 WARN，绝不取得 BLOCK 权威。

**根本原因**:`_crowding_rank` 是 **cross-sectional rank**——天然落 [0,1],只测"本周相对排位",**不测绝对拥挤
程度**。每周必有一个最拥挤(rank 1.0)和最不拥挤(rank 0.0)的名字,无论市场当周是否真的拥挤。所以 rank 上的
任何阈值(平均/max/短腿)都不是有效的**绝对拥挤门**——它度量的维度根本不含"绝对拥挤水平"。集中度判据
(weight×crowding)对**分散** book 也从不触发(max 权重 ~0.07 << 0.25),对**集中** book 仍有效、保留。

## 正确统计量 = 绝对/时序度量(实测有信息)

对**被 short 的(拥挤多头)**名字算绝对量，234 周中 233 周具备足够 z 历史，真的区分正常/异常拥挤周:

| 统计量 | mean | p10 / p50 / p90 | min–max |
|---|---|---|---|
| 短腿 raw trailing global_ls | 3.30 | 2.51 / 3.24 / 4.15 | 1.99–6.58(变化 3.3×) |
| 短腿 mean **z-score** | 0.41 | -0.44 / 0.36 / 1.34 | -1.08–3.78 |
| **短腿 MAX z-score** | 1.50 | 0.21 / 1.40 / **2.91** | -0.50–**5.87** |

z-score = 该名当前 global_ls vs **它自己的时序历史**(≥12 期)——回答"这个名字**现在**是否异常拥挤"
(挤压风险),而非"它这周相对别人排第几"。**短腿 MAX z** 从 ~0 到 5.9σ 真的在动,p90≈2.9。

## 决定(用户 2026-07-07 拍板:WARN-only + z-score 门@3.0)—— ✅ 已落地

1. **rank 平均路径改 WARN-only**:平均分恒 = 0.500,把它当 BLOCK 门是把数学常数当风险信号。落地为 gate 加
   `average_warn_only` 参(默认 False 字节等价;crowd_contra 驱动器传 True → 平均分封顶 WARN,诊断非门禁)。
2. **集中度判据保留原样**:对本分散 book 从不触发,但对未来可能集中的 book 仍有效,无害保留。
3. **正确的 BLOCK 门 = 绝对 z-score 判据**(用户签 **z\*=3.0**):gate 加 `crowding_z_by_symbol`/`max_crowding_z`
   参(默认 None 字节等价),被 short 名字的 global_ls 时序 z > z\* → BLOCK(挤压风险);缺 z(历史不足)不评该
   名 z 维,非有限 z → fail-closed BLOCK。crowd_contra 驱动器算每名 z + 传 z\*=3.0。**最新重放:234 周 BLOCK
   21/8.9744%(对上 p90≈2.9052)**;敏感性 2.0/2.5/3.0/3.5/4.0 分别 BLOCK 61/38/21/9/5 周。
   历史干跑从 128.9%/夏普1.110/回撤9.8% → 123.8%/1.090/回撤8.9%(z-gate 跳过高挤压周,
   小幅用收益换更低尾部回撤;evidence digest 已随之重生成)。

落地:gate 参数/驱动器 z 证据/签署值 drift-lock + 冻结数据篡改测试。live、基准干跑、signal-adapter 干跑
三条入口均为 z\*=3.0，正式重放证明 21 个 BLOCK 周逐项一致。统一门禁校验 bundle/manifest/原始输入哈希/
实现哈希/三处常数/汇总。诚实边界：这是历史敏感性而非因果证明；L2 无 order-book、强平或生产成交样本；
通用 `0.60/0.45/0.25/5天` 默认值仍不是生产校准阈值。
