# 第二轮原始研究检索与去重

本轮只筛选来源和核对本地实现，不读取锁定结果、不修改冻结实验、不新增历史调参或交易。未发现可直接宣称盈利的新Alpha。

## 1. 注意力条件动量：已有冻结分支，不能重复开发

[Liu与Tsyvinski的研究记录](https://www.nber.org/papers/w24877)及[作者所在院系介绍](https://economics.yale.edu/news/180806/assessing-cryptocurrency-yale-economist-aleh-tsyvinski)讨论加密资产动量及投资者注意力。原研究结果不等于本地可执行收益。NBER正文此次访问受限，未据此声称完成全文复核。

本地证据：`services/research/attention_momentum.py:1`已有纯信号；`docs/engineering/specs/2026-08-25-crypto-attention-forward-channel.md:97`起已有冻结协议。它使用Wikimedia而非直接照搬Google/Twitter，存在代理变量迁移，须保留原控制组。

尤其该协议明确锁定：到2027-08-31 UTC且满足28日注意力预热后的252个独立日结果之前，禁止查看收益统计。降低机构设施要求不等于解除这项锁。旧文档记载采集部署，不足以证明今天仍正常采集；本轮没有访问该结果或改变服务。

发现的方法目录缺陷：`services/research/alpha_methodology.py`仍将该方法标为`NEW_EXPERIMENT_GAP`并指向泛化模块，容易重复排开发任务。交给Claude Code做限定元数据修正，改为已有但受限的实现，不改信号、评分权重或准入逻辑。

## 2. 波动率管理：已有同类工具，不冒充新的方向信号

[Moreira与Muir原论文（Yale公开稿）](https://law.yale.edu/sites/default/files/area/workshop/leo/leo17_moreira.pdf)研究依据滞后波动风险调整暴露，涉及股票因子和货币carry；§II.B及Table IV讨论交易成本与降低换手的变体。这不是BTC永续的直接盈利证据。

本地`services/research/robust_optimization.py:279`已有根据历史标准差缩放收益序列的函数，`tools/dry_run_btc_spot_multi.py`也有旧波动率目标路线。它们不与论文所有逆方差构造等价，且对收益序列事后缩放不能代替实际仓位、费用、保证金和资金费回放。旧V1隔离仍有效，不复用其收益权威。

处置：列为已有风险管理方向，不新增“又发现一个Alpha”的计数。本轮不根据已见RF2收益挑波动率窗口，也不改RF3冻结的25%目标。

## 3. 现货—永续价差回归：保留研究来源，当前单腿账本不能复现

[He等，Fundamentals of Perpetual Futures，v4](https://arxiv.org/html/2212.06888v4)的§3、§4.1和Table 3需要现货/永续两腿及融资成本；无固定到期日意味着不保证在预定时点收敛。论文费用分档采用maker，所谓高费用档对应VIP1及成交量要求，不能视作本项目1500 USDC账户的已核准taker成本。

本地`services/research/perpetual_alpha.py:41`只回放BTC单一报价币永续，`services/execution/perpetual_ledger_v2.py:1`声明单币种线性永续边界。只有资金费序列、不计现货本金与退出价差的“套利收益”不合格。已有`docs/engineering/plans/2026-09-06-perpetual-alpha-workflow.md`还登记过资金费trend/fade，不能混作两腿套利复现。

处置：等待两腿原始可知价格、融资/借券条件及统一现金占用核准；不把单腿funding收益包装为无风险利润。本轮不为此扩建跨场所执行系统。

## 本轮决策

保持RF2简单趋势的既有历史结果和RF3冻结自然前向路线，不因搜索到高Sharpe论文就更换候选。三条来源均有明确重复/输入/成本边界；没有新增回测或盈利结果。
下一步实际收益证据仍来自已准备好的自然前向窗口和人工授权后的测试网，而不是重复挖掘原577天历史。持续观察授权、测试网场所未收到确认，不能擅自开始调度或访问账户。

## 去重修正验证

Claude Code独立会话`e9ca1700-cc22-40ea-a358-2eb6da9e41c2`正常完成、exit0，仅修改方法目录及其测试。原错误标签先被回归复现，再改为`EXISTING_BLOCKED_IMPLEMENTATION`和两个具体实现路径，附原冻结边界说明。评分权重没有改动；标签变化会按既有权重降低其“新实验缺口”优先级，这正是避免重复派工，不是对该策略收益的新判断。

Codex审阅diff，将测试名称限定为实际覆盖的输入情形（不声称所有外部channel标签都受新硬门保护）；未改变audit准入逻辑。方法目录、CLI审计、注意力纯信号及原登记协议联合32项通过。未运行完整发布验收，未改冻结协议或查看其收益。
