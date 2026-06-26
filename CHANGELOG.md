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

---

> 历史提交见 `git log`;本变更日志自机构级流程落地起开始维护。
