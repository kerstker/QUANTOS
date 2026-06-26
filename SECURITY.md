# 安全策略(SECURITY)

QUANTOS 是**安全关键**系统。这里的"安全"有两层含义:**信息安全**(密钥/凭证)与
**交易安全**(绝不发生未经人工审批的实盘行为)。两者都不可妥协。

## 就绪等级

当前:**L2_DRY_RUN_ONLY** —— 只做研究与干跑模拟。**无实盘、无券商连接、无真实凭证。**

## 不可让的安全不变量(机器强制)

- 实盘相关开关恒为关:`LIVE_TRADING_ENABLED=false`、`REAL_BROKER_ENABLED=false`、
  `ALLOW_ORDER_SUBMISSION=false`、`ALLOW_AI_DIRECT_TRADING=false`、`REQUIRE_HUMAN_APPROVAL=true`。
- 实盘在没有**人工多步审批**时必须**不可能发生**。
- 不提交任何密钥/券商凭证;不引入券商/CTP/FIX SDK;不绕过/削弱风控门;不使用未来数据(无前视)。
- AI/自动代理永远不下实盘单、不接触凭证;非人类 actor 永不可达 `APPROVED`。
- 网络访问只允许在 `tools/`(门禁不跑);`services/` 整棵树零网络 import。

这些由治理扫描器(`apps/research-console/scripts/run_paper_to_live_governance_check.py`)和
`tests/governance/` 在每次门禁与每个 PR 上自动检查。

## 密钥处理

- API key 只从环境变量 `COINGECKO_API_KEY` 或被 gitignore 的 `key/coinkey.txt` 读取。
- 密钥**绝不**打印、记录日志或提交。`key/` 目录与 `*coinkey*` 已被 `.gitignore` 排除,
  本地 `pre-commit` 钩子还会再拦一道。

## 漏洞/安全问题上报

发现安全问题(尤其是任何可能绕过上述不变量、或可能泄露凭证的路径):

- **不要**开公开 issue 公布细节。
- 私下联系仓库所有者(见 `.github/CODEOWNERS`),或通过 GitHub 的私密安全公告
  (Security advisories)上报。
- 请附:复现步骤、影响范围、以及它触及了哪条不变量。

## 支持范围

仅维护 `main` 分支。安全修复以补丁形式合入 `main`,并同步到 `master`。
