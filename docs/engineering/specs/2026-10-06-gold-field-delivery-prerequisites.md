# Gold 现场交付前置条件实查

状态：FIELD_PREREQUISITES_INCOMPLETE。只读核对，不是发布、停写或恢复通过。
范围沿用[原 F1–F5](../plans/2026-09-28-ai-factory-closed-loop.md)、
[停写后切片](2026-10-05-gold-post-pause-layout.md)和
[恢复后的现金重放](2026-10-05-gold-restored-cash-context.md)。
不增加市场、策略研发或机构异机要求。

## 实际观察

2026-10-06 03:08（北京时间），从当前干净发布候选
`dbc22390fda634a5be47a76bbf84db247ccad86f` 的原
`demo_maintenance_services.GOLD_UNITS` 取得46个固定 unit，使用实际
`systemctl show` 读取状态、UID、cgroup和退出码元数据。按返回 Id
逐项绑定，集合完整，无服务操作；不是测试中的 service manager。

三个 unit 的 LoadState 实际为 not-found：

- quantos-demo-feedback.service
- quantos-demo-feedback.timer
- quantos-gold-pilot.service

九个 service 的 ActiveState 实际为 failed，Result=exit-code、
ExecMainCode=1、ExecMainStatus=3：

- quantos-demo-executor-observe.service
- quantos-demo-executor-origin-BTCUSDC.service
- quantos-demo-executor-origin-BTCUSDT.service
- quantos-demo-checkpoint-executor-capture.service
- quantos-demo-checkpoint-executor-seal.service
- quantos-demo-checkpoint-issuer-seal.service
- quantos-demo-checkpoint-controller-seal.service
- quantos-demo-checkpoint-research-capture.service
- quantos-demo-checkpoint-research-seal.service

执行流服务与研究 runner 实际 active，另有活动 timer。运行中的原执行
流 PID100172、UID1001；研究 runner PID2403、User=quantos。状态是
有限时点观察，不证明每个历史失败的根因或当前进程已排空。没有重启、
停止、reset-failed或安装 unit，不把缺失 unit 视为已成功停写。

非 root 开发用户读取两份固定公共 authority.json 均 PermissionError，
没有读取私钥、broker凭据或其他私有配置。非交互 root 身份探针
`sudo -n /usr/bin/id -u` 退出1，要求密码；没有提供密码或执行特权
操作。因此现场 source_commit、账户绑定及完整 research UID roster
继续 UNKNOWN，不能从 systemd User 或离线夹具推导已安装授权范围。

## 固定交付的具体关闭条件

1. 最终交付源码自身完整 ALL 门及匹配 release evidence 留存通过。
   dbc 的原终态为30 PASS/2 TIMEOUT；后续4cf5原终态为31 PASS/
   1 TIMEOUT（执行阶段3600172ms），两次失败保留。当前冻结bf1f56f2
   已启动自己的ARM64→canonical ALL流程；未取得终态，不提前视为
   accepted release，不要求失败旧版在相同原记录中变成成功。
2. 受控本地 operator 核对实际公共账户/source/UID/市场范围，补齐原
   黄金和反馈安装交接，明确维护计划涉及的46个 unit。安装与运行
   服务仍是单独授权动作，开发请求不授予签名、trust或交易权限。
3. 按原独占维护窗口停写、抑制自动启动并核对全部原 UID；停写之后
   冻结原 cut，保留原文件租约，执行原五个 worker/四角色捕获与冷恢复。
   原代码的进程元数据观察和文件租约各自不足以证明正式 writer 屏障。
4. 同一账户、同 cut、同原件重放唯一现金账本，核对实际期初/期末、
   成交/费用/资金费/外部现金事件。报表重放、长期采集完成、完整
   金额对账分别验收，不能把 COMPLETE 或零成交残差替代整组完成。
5. 沿原 F1–F5 验证获准 Demo 的现金反馈与下一研究轮，并验证正常、
   淘汰、授权拒绝和中断恢复。五秒链与实际持续验收按原计划核对，
   不用现场安装或合成签名替代独立经济准入。

以上是原交付条件的现场前置分解，未增加范围或执行授权。盈利策略
另列；亏损或不足证据必须真实淘汰/拒绝，不为了完成验收而晋级。
目前 writer_barrier_verified、whole_set_verified、cash_reconciled、
economic_authority和submission_enabled均无实际完成证明。

## 原件与限制

开发证据目录：
`gold-project-closure-20261003-3pr5mrly/gold-field-readiness-20261006`。
readiness.py与field-readiness.json保留实际46个Id对应的元数据，
root-metadata-probe.json保留非交互身份探针失败。
源码 roster 摘要随结果保存；此开发候选不是已安装源码证明。
没有读取受保护账户账本、修改服务/配置/生产状态或调用真实下单。
