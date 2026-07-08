# 变更日志

格式参考 [Keep a Changelog](https://keepachangelog.com/);叙述用中文。
本项目就绪等级 **L2_DRY_RUN_ONLY**(只研究 + 干跑模拟),安全红线见 `SECURITY.md`。

## [未发布] - Unreleased

### 新增(工程/流程)
- 机构级开发流程落地:升级 CI(并发取消、最小权限、pip/npm 缓存、显式步骤名)、
  Dependabot(pip/npm/actions 周更)、`.editorconfig`、`CONTRIBUTING.md`、`SECURITY.md`、
  `CODEOWNERS`、PR 模板、Issue 模板、`Makefile`、本地 git 钩子(pre-commit 治理扫描 + 密钥防呆;
  pre-push 完整门禁)、远端搭建与分支保护手册 `docs/REMOTE_SETUP.md`。

### 新增(研究/因子方法)
- GP 表达式树发现(`gp_tree` / `gp_genome`)+ 进化搜索接入 'T' 分支(+ 对抗式评审硬化)。
- 去冗余:协同集前向选择 + 增量 IR 搜索奖励(`factor_set`)。
- MCTS/UCT 公式空间导航(`mcts_search`)+ 频繁子树规避(`subtree_stats`)。
- 不自欺验证:无窥视滚动选择 OOS(`oos_validation`)、Purged/Embargoed CV(walk-forward 加 embargo)。
- 三重门 + 元标签(`labeling`)、波动率状态分档(`regime`,opt-in 接评估器)、互信息(`information`)、
  唯一性样本加权 / 因子择时 / 分数阶差分。
- 真实数据接缝:声明式数据源适配器(`services/data/adapters`)、CoinGecko 真实成交量
  (`tools/coingecko_volume_fetch`,时间戳对齐到整点网格)+ 量能/流动性因子(`volume_signals`)。
- 前端:Crypto Live 页(真实加密因子 + OOS 裁决 + 协同集面板)+ console API 真实数据端点
  (provenance=`REAL_CRYPTO_COINGECKO_OFFLINE_SNAPSHOT`,契约两侧守护)。

### 实证结论(诚实记录)
- 短周期加密(4h/小时线、小样本)价格/GP/量能因子**无稳健 OOS edge**;样本内 Deflated 夏普 0.586
  被无窥视 OOS 证伪(选择性过拟合)。诚实机制如实拒绝(auto_approved=0)。

### 新增(组合原语,机构级完成度 S1-S3,2026-07-01~03)
- Barra-lite 结构化风险诊断、协方差感知资本分配器、Brinson 归因、冲击成本校准、fail-closed
  拥挤度门(绝对 z-score,非任意 rank 阈值)——四项均先诊断后接线(crowding gate/capital allocator
  已接入实际决策路径,其余保持只读展示)。PIT 只读边界机器强制、数据质量强制门(严格摄入+
  volume 单位守卫)、持久特征注册表、`verify_evaluation` 篡改检测、可复现证据摘要(evidence
  digests,漂移可检测)、durable 哈希链治理审计日志。console IA 由 42 页重构为 30 页。

### 新增(实盘执行路线,2026-07-04 用户拍板)
- 目标改为经 Phase 3A 人工门的小资金($3k-5k)加密实盘,Binance 优先、venue 可插拔。
- Stage 0:真前向 paper-track(决策先落库、结果后兑现,无前视),证据钟 2026-07-02 起跑。
- Stage 1:`live/` 独立执行栈(services/ 零改动)——双 venue(Binance+Bybit)测试网真实下单、
  两级订单契约、`PreTradeRiskGate` 复用、`mandate.py` 用户签署分级授权、position ledger + 对账、
  emergency reduce-only 通道、kill-switch 跨进程持久化。经 15-agent 安全审查(无 HIGH/MEDIUM
  遗留)、48-agent institutional gap 审计 + P0/P1 修复。
- 轨道3 通往实盘的三个安全脚手架:broker adapter 接缝(结构性拒绝 mainnet)、多方人工批准
  状态机(≥3 不同真人 + 哈希链)、Phase 14 出进程签名设计(拒绝式,诚实标注单进程内挡不住
  受信代码)。

### 新增(研究方向:现实主义优先于数据广度,2026-07-05~06)
- RE-1 现实主义引擎(Almgren-Chriss 冲击 + 容量衰减带):抓到约 300× 容量高估,参与率是绑定
  约束。RE-2(历史情景重演 + 尾部风险):确认美元中性、真尾部内生、揪出 volume 单位跨币不一致
  bug。FRED 宏观 regime 接入:发现 carry 与 crowd_contra 宏观画像互补(强分散故事的机制解释)。
- 组合分散验证:corr(carry,crowd_contra)≈-0.04,理想化(gross)夏普 1.95/回撤 8%;风险平衡
  统一执行 book 诚实结论——**净成本执行下分散不敌单条 crowd_contra**(VT 1.20 vs 1.38),
  deployable = crowd_contra 单条。

### 新增(四条战线 + 对抗式审查,2026-07-06)
- 晋级闭环(3 人独立批准 + `promotion_api`)、LIMIT 订单执行(marketable-limit 定价 + 超时撤单)、
  策略级仓位隔离(`strategy_id` 分区账本)、IC 面板多持有期口径 + ALFRED vintage 数据方向。
  33-agent 对抗式审查发现并修复 6 处真实缺陷(最严重:mandate LIMIT 卖单名义额计算方向性
  bug,`min()`/`max()` 反了)。

### 新增(安全加固,2026-07-07~09)
- 10-agent 全维度顶级量化标尺审计(见 `docs/audit/2026-07-07-live-readiness-top-tier-gap-audit.md`)
  ——综合评分 2.2/5,核心结论:关键机制(pre-trade gate/mandate/kill-switch/统计纪律)真实,
  差距集中在"建好未打开"(死人开关未注册、告警未配置、VaR/CVaR 零消费、kill-switch 仪表盘
  读错对象等)。审计过程中现场发现并修复一次真实生产故障(排程任务因磁盘分支切换静默失败
  21 小时)。
- 后续修复:kill-switch 仪表盘 bug(两个"REAL"安全看板此前构造全新未持久化对象,即便真实
  跳闸也显示健康)、VaR/CVaR/HHI 接入只读展示、alpha 挖掘循环并行化(结果逐位一致性验证)、
  mainnet API key 权限校验工具(独立,未接线)、灾备 AES-256-GCM 加密(fail-closed,含对抗式
  审查抓到的"新加密闸门会让计划任务静默每日失败"运维地雷并同步修复)、`key/` 目录 ACL 收紧、
  仓库自带版本化 git 钩子重新激活。

---

> 历史提交见 `git log`;本变更日志自机构级流程落地起开始维护(2026-06-26 起有意保持"重大
> 里程碑摘要"粒度,不逐提交记录——完整提交历史请查 `git log --oneline`)。
