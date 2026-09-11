# 独立研究栈配套运行配置

2026-09-11。状态：配置已生成并核验，未安装、未启动。
这是R6运行交付准备，不是新的引擎或实盘准入。

已有ARM64包覆盖运行源码 `3f65322c527cbb693e1af477ef69f90029c4c0da`，
发布与证据提交 `244dd6fbe3e1ffd88c21da4882602f4602bfea82`。
原生与完整28/28软件验收见[研究空间交付](2026-09-11-research-workspaces.md)。
本次仅准备使用该源码的具体运行配置，没有借旧软件验收证明新服务已经运行。

## 配套方案

现有14911研究入口及其五个服务保持原样。拟新增以下独立用户服务，统一由
`quantos-next-research.target`管理；停止该target不涉及既有服务。

| 服务 | 职责 | 监听或调度 |
|---|---|---|
| quantos-next-console | 只读Console及五个历史研究空间 | 127.0.0.1:15840 |
| quantos-next-runner | 新任务队列、有限计划与研究操作 | 127.0.0.1:15842 |
| quantos-next-web | 新版Studio、同源API与Runner代理 | 192.168.5.68:15911，仅192.168.5.0/24 |
| quantos-next-observer | 新空间中已显式启用模型的观察及检查点 | 60秒 |
| quantos-next-public-market | 既有BTC公开行情采集配置 | 原82800秒周期，退出后重新启动 |

拟用入口为 `http://192.168.5.68:15911/studio/factory`，当前尚不能访问。
五个组件使用同一已验收源码、同一新任务目录；前端直接使用已核验发布包产物。
Console和Runner均配置本地 `qwen3.8-27b`，LOCAL_HTTP端点为127.0.0.1:8001。
仅Console读取旧研究空间清单；Runner、观察器和采集器只写新目录。
旧实验不会复制到新任务空间，也不会被新引擎恢复；原冻结工作者继续其原计划。
首次启动的新空间没有研究任务或预测，不能把它的空状态称为盈利证据。

拟用新任务目录：
`/home/kerstker/quantos-dev-output/research-runtime-244dd6fbe/state`。
该目录尚未创建；Runner启动时按现有机制初始化独立研究控制令牌，不复制旧令牌，
不接入交易所凭证。控制台选择额外空间时仍为只读。

## 已核验的交付物

目录：`/home/kerstker/quantos-dev-output/remaining-roadmap-audit-20260911-9caa6b138/research-runtime-review`。
包含五个service、一个target、`research-workspaces.json`、`RUNTIME_PLAN.json`和`SHA256SUMS`。

- systemd 255的`systemd-analyze --user verify`通过，未调用安装、启动或daemon-reload。
- 逐文件摘要、五个组件源码与新目录绑定、前端同源端口、只读空间、公共采集写路径通过。
- 启动前源码校验命令已实际执行，确认发布提交、完整软件证据及来源一致。
- 本地`/v1/models`返回qwen3.8-27b；只读取模型清单，零生成调用，连接配置仍显示未验证调用。
- 新端口在检查时可绑定；没有为此启动服务器，启动时仍须重新确认端口和服务状态。
- 已有代理环境文件仅复用代理变量，未读取或复制模型/交易所密钥；本地模型不发送Authorization。

核验材料为同级`runtime-verification.json`、`systemd-verify-attempt1.log`、
`actual-source-preflight.log`与`qwen-metadata-check.json`。
配置可审阅不等于已完成进程启动、浏览器连通、连续运行和恢复验收。

## 启动与回退范围

用户授权部署后，先重新核对源码、包摘要、端口、新目录未被占用及现有服务状态，
再将本包六个unit逐一安装到用户systemd目录，启动并启用此新target。
验收检查五个新进程实际源码与目录、Console/Runner健康、桌面/手机同源接线、
历史空间只读、新空间空任务状态、采集/观察心跳与错误可见性。
启动本身不创建模型搜索计划；已失败研究不自动重跑。

回退仅停止并禁用`quantos-next-research.target`；新任务、日志和结果保留，
不删除目录，不停止旧14911服务及f378/0c1冻结观察，不接触正式3100环境。
当前用户增量明确不包含部署，因此本次不执行这些操作。
实际下单、经济准入及独立受控权威接入仍属于另行验收的范围。

## 同时核实的自然研究

09:00:54实际核验PID1890754及启动身份、冻结f378源码和原计划均一致。
USDC、USDT各3/504行有效特征，无缺口、0新试验；09:00批次12请求、2.949秒、无失败或重试。
最早成熟条件仍为10月2日10:06，还须通过质量和成本后比较，不是盈利日期。
回执见`/home/kerstker/quantos-dev-output/backup-contract-v16-20260911/frozen-window-observation-1789088454030.json`。
真实P5账户/订单导出位置尚待用户提供，来源未经核验时继续保留未知。
