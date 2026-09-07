# BTC 机制实验前后端工作流

日期：2026-09-07。开发基线 `1cbf196ca322368e43b073b56a0707a28df4a470`。
分支 `codex/alpha-factory-workflow-20260907`。本次按用户提供的《公开证据到可实施
Alpha 工厂》方案，优先贯通已有研究能力的可运行入口，不重建账本或 TrialLedger。

## 现况核实与交付范围

进程命令行核实 Binance/Bybit 采集、console、research runner 和前端服务均指向
`/opt/quantos/releases/1cbf196ca322368e43b073b56a0707a28df4a470`，不是旧手册中的
`cd7698f`。当前工作树开始时干净。独立 Python 3.12 / ARM64 依赖及 Node 校验通过。
本次没有切换这些服务或写入 `/srv/quantos/state`。

既有机制脚本已有四模型月度因果拟合和历史评价，但没有 runner/console 工作流。
研究页对需要 attempt 的永续/订单块实验发送空参数；缺输入时机制脚本也会在试验
登记前退出。这些是本次处理的具体缺口。

- 新增诊断白名单 `btc_mechanism`，复用 JobQueue、RunRegistry、TrialLedger 和既有
  `technical-replay` 只读接口。HTTP 参数仅允许整数 attempt，范围 1–1000。
- 输入固定在状态根 `data/cache/perpetual_alpha/input`；输出为
  `data/cache/btc_mechanism/results/<attempt>`，试验账本位于 results/trials.db。
  同编号重复执行会报错，保留已有结果；队列 invocation 重试沿用原有幂等机制。
- 输入文件缺失仍登记四个失败试验，failure.json 关联 trial_ids。成功结果在终态
  登记并校验账本后才写出；新增 schema、账本校验标记和返回值。
- 结果绑定输入、计划、源码和预测文件摘要；适配器校验完整结果摘要，控制台校验
  registry provenance。仅新机制运行使用预测研究身份，旧 V1 投影继续隔离。
- 研究页提供实验编号输入、非法编号提示、提交期间禁用按钮；修复三类实验参数。
  详情页新增全期/2025/2026切换、RMSE bp、相对 PRICE/零预测改善、全期 Holm p。
  未给出的 p 显示横线，不填零。失败显示编号/冻结输入排查说明。

对应用户方案：补齐 P0 的机制运行身份与 P1 的系统入口。本批未增加小时级预测、
OI/basis、经济回测、全工厂外层选择回放、前向证据或 P3–P6 执行能力。
现有日频四模型统计仍为 EXPLORATORY_PREDICTION_ONLY；技术接口沿用
TECHNICAL_VALIDATION_ONLY envelope，不计算账户收益。历史接收时间 UNKNOWN，
已查看历史不是锁定 OOS；重跑不增加独立样本。

## 操作与复现

开发版研究页面 `/research/runs`：选择新的实验编号，再点击 `btc_mechanism`。
需要 runner 与 console 使用同一独立状态根，前端指向这两个开发服务。
既有研究 token 配置保持原机制；没有增加审批步骤。常驻版本未包含本次改动。

冻结输入格式沿用[原机制研究说明](../../research/btc-mechanisms-20260906/README.md)：
`klines/BTCUSDC-1m-*.zip`、对应 `.zip.json`、`funding-capture.json`。
不需要重新下载已有材料；归档仍验证官方校验值及合约身份。
离线脚本仍支持原 `--input /绝对路径 --output /新目录 --attempt 1` 用法。

本机已完成的独立工作流验证目录：
`/home/kerstker/quantos-dev-output/alpha-factory-workflow-20260907`。
其中 smoke.py 为队列→真实计算→HTTP接口复现驱动，smoke.log 为结果，
console-result.json 为前端所用的实际接口响应。冻结公开输入已复制到该独立目录。
运行 `run_d575c74529804ba7af138bf9d09935b3` 完成，577标签的 predictions.csv 与前批
逐字节一致，全部窗口统计一致，HTTP 200。它是既有实验重放，不是新收益发现。

## 验证

先失败回归捕获未接白名单、缺数据未登记、前端未提供编号/预测页、新实验被标为
旧 V1 失效；修复后保留这些测试。后端首次相关集 136通过/1失败/1跳过，失败为
health 精确白名单尚未加入新类型；同步预期后最后受影响集56通过/1跳过。
另有机制数学/runner/边界专项24通过。前端全量266通过，尾改后相关13通过；
TypeScript 与生产构建通过。服务导入图和治理扫描两阶段通过。
完整 canonical gate 与 ARM64 发布验收本次未运行，不能用专项测试替代。
