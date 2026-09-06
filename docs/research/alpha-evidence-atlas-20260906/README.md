# 六方向 Alpha 研究材料

日期：2026-09-06。状态：`EVIDENCE_REVIEW_AND_EXPERIMENT_DESIGN_ONLY`。

[主报告](../../reports/2026-09-06-alpha-all-directions-evidence-atlas.md) 将外部证据、我们的推断和未运行实验分开。

- [sources.json](sources.json)：22条本轮选定来源，另引用[前轮10条来源](../btc-mechanisms-20260906/sources.json)。同一论文的版本与镜像不算独立证据；阅读范围逐项标注。
- [search-log.json](search-log.json)：六族检索主题、一小部分准确查询文本、访问限制和未采用材料。不是完整搜索引擎原始日志，没有虚构筛选数量。
- [experiments.json](experiments.json)：6族各3张，共18张待冻结提案；全部未运行、无run_id、未建立权威。参数建议不构成可执行配置。
- [smc-source-capture.json](smc-source-capture.json)：只读获取的公共SMC提交、文件URL、SHA256和获取时间。外部源码未安装、未执行、未接入QUANTOS。

## 实验索引

| 家族 | 卡片 | 最先要解决的条件 |
| --- | --- | --- |
| 价格结构 | P1突破延续、P2扫掠回归、P3形态增量 | 严格确认时间、前缀不变、匹配价格基线 |
| 持仓/流量 | F1仓位交互、F2压力衰减、F3资金费条件效应 | 全窗OI、字段单位、消息可用时间 |
| carry | C1到期基差、C2现货永续、C3永续费率差 | 双腿现金流、各自费率、资本与保证金 |
| 做市 | M1库存基线、M2撤单保护、M3执行选择 | 队列、条件成交、真实延迟与故障 |
| 跨所 | X1外部信息、X2可执行价差、X3跨所做市 | 接收时间对齐；双腿另需资本和恢复 |
| 期权 | O1风险信息、O2有限损失溢价、O3曲面相对价值 | 事前报价、规格、合约结算和风险匹配 |

实验不是18个已发现Alpha，M3尤其属于执行质量研究。任何晋级仍走既有权威和人工流程。

## 可复核范围

本轮基线提交：`0482d3a1a2eb8f82510d9da066325e63a5716147`。仅新增研究文档及状态记录，没有改交易代码。前轮真实数值及其独立验证器仍由[原实验材料](../btc-mechanisms-20260906/README.md)维护，不能与本轮18张提案合计为已完成实验。

本机输出：`/home/kerstker/quantos-dev-output/alpha-evidence-atlas-20260906`。审查包：`QUANTOS-alpha-evidence-atlas.tar.gz`。包内保存本轮文档、前轮来源目录、验证摘要、独立材料检查脚本和SHA256清单；不包含外部论文全文，也不假装包含全部网页快照或新的行情回测。

解压后先执行 `sha256sum -c SHA256SUMS`，再用Python运行 `verify_materials.py`。检查覆盖JSON结构、来源ID引用、18张卡的未运行状态、六族覆盖及费用/权限口径；不验证外部论文结论，也不验证系统交易正确性。仓库内本轮Markdown链接由项目hygiene函数另行检查，结果在包内 `validation.json`。

SMC文件可按capture中的固定URL重新下载，只读核对SHA；网页或代码与摘要不符时，不作为同一批材料。源码确认范围：FVG负偏移、swing左右窗口、事后索引。未声称完成该库每个函数的金融审计。

报告内的在线材料按2026-09-06访问状态解释。仅摘要/索引正文可读、403/429、未审计的新论文均保留限制；重跑检索不能保证搜索引擎返回相同排序。

## 验证口径

文档变更运行材料一致性、Markdown本地链接、仓库hygiene、治理扫描及实际pre-commit hook。没有因为文档改动重跑build/typecheck/全套交易测试，亦没有宣称既有完整门禁从FAILED变成通过。

新增两份Markdown的本地链接通过。扩展检查发现 `PROJECT_STATE.md` 原有相对链接 `docs/engineering/specs/2026-08-02-alpha-production-workflow.md` 失效；与变更前HEAD比较完全相同，本轮未修改该历史区域。验证摘要同时保留变更前后问题，没有将全文件链接检查报成全绿。
