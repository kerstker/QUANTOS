# CatalogEvidence 与 DSR 溯源收口

日期：2026-07-20
状态：已实现，L2 dry-run only

## 目标

Rank #12 要消除验证链中最后一类“调用方传入一个看似合理的数字”信任：DSR、试验数、
`var_sr`、有效试验数与 PBO 必须来自可重放、可防篡改的同一候选目录，而不是某个局部批次或
可选 override。完成标准是从候选生成到最终人工批准，每个承重裁决都能机器重算其来源。

## 设计

共享契约位于 `quantos/contracts/validation.py`：

- `CatalogEvidence` 保存候选定义哈希、精确收益观察及其 canonical SHA-256、完整 Sharpe
  population、可比较 trial-pair 相关性、原始/有效试验数、`var_sr`、observed Sharpe、DSR、
  trial-set/snapshot/evidence 哈希。
- `BatchPboEvidence` 保存候选定义哈希、对齐模式、日历摘要和精确 CSCV block×candidate
  矩阵，PBO 从矩阵重放。
- `AlphaCatalog.evidence()` 是目录证据的唯一铸造入口。缺少收益历史的 trial 仍按一项一个
  独立试验计数；只有拥有可比较相关证据的子集才允许通过既有 `effective_trials()` 折减。
- catalog snapshot 与评估 reproducibility 使用 exact-float canonical hashing；不再使用会把
  浮点格式化到固定小数位的通用稳定哈希。因此同一 trial-set 下的微小收益漂移也会失效。

## 承重路径

1. `AlphaFactory` 与 `ResearchAgentLoop` 先注册并准备完整候选池，再产生第一个 verdict。
2. `AlphaEvaluator`、`ValidationGate` 与 standalone research pipeline 只消费证据对象；裸
   DSR/PBO/trial-count 与本地 override 已删除。
3. `ExperimentRegistry.record_evaluation()` 在写入前重放两类证据并绑定 dataset/version；
   `verify_evaluation()` 读取后再次重放 DSR、PBO 与 reproducibility hash。
4. `VALIDATED`、`PAPER_TRADING` 和多方人工批准路径都校验 CatalogEvidence；最终批准要求
   当前完整 catalog snapshot 与评估时完全一致，不能只比试验数量或集合。
5. console 只展示证据哈希、trial/effective-trial 数、平均绝对相关、`var_sr` 等脱敏摘要；
   原始收益和 PBO 矩阵不出只读研究 API。

## 失败关闭语义

- 少于两个诚实 Sharpe 观察时不铸造 CatalogEvidence。
- 缺证据、定义不匹配、PBO 不含目标候选、目录增长、同数量集合替换、收益漂移、重算不一致
  或制品篡改均拒绝。
- 非 PASS 制品可诚实保存失败结果；PASS 制品必须同时具备可重放 CatalogEvidence 与 PBO。

## 安全边界与未解决项

这是进程内研究证据完整性，不是跨进程密钥信任边界。它不增加网络、凭据、券商、下单或
live 能力，也不把内部验证工程升级冒充外部模型风险审阅、持牌数据、跨主机基础设施或运营
历史。系统顶级机器判定因此仍可保持 `NOT_TOP_TIER`，直到那些独立要求获得真实证据。
