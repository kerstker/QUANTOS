# Demo 订单恢复状态与工作台

2026-09-12。承接[OMS 恢复](2026-09-12-demo-oms-resolution.md)和
[Demo 执行计划](../plans/2026-09-12-demo-execution-completion.md) D3。

受保护恢复入口把输入未齐、配对后等待投影、投影中断和整个批次恢复完成分别保存为
不可变显示记录。完成状态绑定实际投影结果、计划摘要、原预留和提案身份；缺行、
不同批次、数量冲突、未来时间和虚假的现金认证均拒绝。中断可能已有部分事件落盘，
完成数量保留未知，恢复时仍由原 OMS 事件去重，不从显示文件驱动订单恢复。

Console 增加 `oms_status`，对应 TypeScript 合同及 API 测试同步。Studio 展示固定
批次订单数、配对数、已恢复数、原编号、场所编号、终态及精确成交数量；逐单详情
默认折叠，手机按字段纵向排列。来源未认证、现金核对和经济准入保留各自状态。
刷新失败时保留历史记录，不将其描述为当前账户状态。

执行侧可用 `tools.demo_oms_status_publish` 把经过结构和内容摘要验证的显示副本
发布到研究缓存 `data/cache/binance_demo_oms_status`。来源文件只读，目标目录必须
独立；损坏、重叠目录和倒退时间不替换已保存视图。该工具没有密钥、网络、下单或
恢复入口。研究缓存中的显示数据也不能用于执行权威，实际受保护发布服务尚未安装。

```bash
python -m tools.demo_oms_status_publish \
  --source-dir /var/lib/quantos-demo-executor/receipt-reconciliation/views \
  --output-dir <research-state>/data/cache/binance_demo_oms_status
```

以上命令由有相应读取权限的执行侧发布者运行；研究 Console 只读副本。本次没有向
运行状态写入合成数据，没有发送普通订单，也没有安装独立签发配置。

验证材料位于 `/home/kerstker/quantos-dev-output/demo-receipt-binding-20260912/`：

- 后端相关 **102 通过 / 1 跳过**，`oms-status-final.log`。覆盖真实保护入口中的
  合成回执、原查询、事件写入中断、再次恢复、原输入不变及重复运行不增成交。
- 前端全量 **406 通过**，`oms-ui-all.log`；类型检查通过。首次 ESLint 因拒绝测试
  使用超精度数字字面量失败，保留 `oms-ui-eslint.log`；改用显式字符串转 Number
  构造非法输入后通过，未放宽产品编号合同。
- 四种合成恢复状态使用原 Console HTTP 处理器，桌面/手机/暗色共十二组核验；
  首次截图发现手机表格过密，修正纵向布局并再做浏览器核验。这里的成功仅覆盖
  隔离软件输入，不能当作真实 Demo 成交、现金闭合或盈利证据。

以上为提交前的增量检查点；当时实际运行 `95238a31d` / 源码 `2e1d8b9d1`。
独立签发和驻留执行、持仓后续批次/撤单、时钟合同、D4 真实工况、全规模恢复指标
及冻结自然研究验收继续推进；没有由这一显示增量宣称第 1–4 项整体完成。

## 18:00 已验收研究运行更新

源码 `6c8274d96dcb0847a0eb70c431c3a3322aff2362` 完整 **28/28 PASS**：主后端
8,125 通过 / 19 跳过，执行栈 3,738 通过 / 2 跳过，前端 406 通过；类型、ESLint、
构建、ARM64 两次冷构建、扫描和断网启动通过。812 的两项架构失败保留，没有放宽
原失败测试。实际 pre-commit 已运行，再提交原样生成的验收材料。

研究入口 `http://192.168.5.68:15911/studio/demo` 已更新为证据发布
`2753a0ecab8111ef8c51569596aba2d8475e955e`。干净检出的前端应用文件与完整验收
输出逐字节相同；单独绑定新证据提交的构建/运行元数据。部署前已核对 17 份具体用户
服务配置，保留旧 15 份用于回退；新增 V4 起点只读采集 service/timer，每轮结束后
60 秒再运行。现金窗口在途进程没有中断。两个冻结研究工作者的 PID、重启次数、
命令、源码目录和单元摘要在切换前后相同。

实际运行检查通过：六个组件使用同一已验收源码和原五个安全标志，五个采集定时器
保持运行；私有流重连后取得新心跳，保留此次重启缺口。两轮新版本自然五分钟现金
窗口各完成六次读取、无重试/流水，报告窗口相符；这不证明系统订单的唯一现金账本
闭合。桌面/手机/暗色的现金及起点/OMS 页面实际核验通过，零浏览器错误、写请求、
页面溢出和可访问性违规。没有订单恢复记录时显示等待，没有把合成记录写入运行状态。

起点采集截至保留快照共 15 轮，13 次完整、2 次失败：一次账户 GET 请求失败、一次
末端时钟检查失败。完整失败材料保留，不能只报告成功轮数，也未放宽原采集阈值。
前端构建准备期间的目录/日志碰撞失败亦保留，之后干净构建和字节比较通过。

材料：`/home/kerstker/quantos-dev-output/demo-oms-acceptance-20260912/`，包括
`canonical-fresh-6c8274d96.json`、`accepted-checkout-6c8274d96-binding.json`、
`accepted-frontend-comparison.json`、`runtime-review/installation-result.json`、
`verification-first.json` / `verification-continuous.json`（在 runtime-review 内）、
`natural-v2-two-cycle-result.json`、`origin-recurring-observations.json` 和两份实际浏览器报告。

本次发布不包含之后 `04723525f` 撤单增量或正在开发的时钟合同；没有真实普通提交、
撤单、独立签发安装、经济准入变更或主网操作。
