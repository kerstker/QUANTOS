# 黄金账户后台只读监控

**2026-09-28 后续诊断观察**：新增event_name_sha256，仅保留e字段值的SHA-256，不保留未知名称或原报文。固定副本`2a11fef19c32b2692574`（完整摘要`2a11fef19c32b2692574eeb2dbbaa8fcc1efd9112cd38c9fb37c2d6587c5c6e4`）已按原只读授权替换停止的诊断副本并启动；55项相关测试、规范Ruff、治理及unit校验通过。解析拒绝与退出78不变。`quantos-dev-output/gold-event-fingerprint-20260928/watch.py`有界观察20分钟，摘要只与[Binance官方事件列表](https://github.com/binance/binance-connector-python/blob/master/clients/derivatives_trading_usds_futures/src/binance_sdk_derivatives_trading_usds_futures/websocket_streams/models/user_data_stream_events_response.py)比对，未安装或导入SDK。尚待新的事件指纹，不宣称根因已修复；此前失败和缺口保留。

**2026-09-28 最新现场状态：failed/78。** d5d68da6258f64805774在run-0058连接、窗口补录后再次拒绝不支持事件。诊断位于state/run-0058/event-error.json：EVENT_OBJECT、UNKNOWN、UNSUPPORTED_EVENT。不能继续以以下历史CONNECTED作为当前健康状态；未重复重启，具体事件尚未定位。

2026-09-28 状态更新：原run-0057仅留下UNKNOWN/UNSUPPORTED_EVENT摘要，不能识别具体事件。新增固定枚举message_shape诊断，区分事件对象、包装数据、结果对象、错误对象及其他结构，不保存原始内容或未知字段值；不改变解析接受规则或退出78策略。54项相关测试、Ruff E9/F821、治理及unit校验通过。固定副本`d5d68da6258f64805774`已按原只读常驻授权安装并启动，完整摘要`d5d68da6258f648057747ba12364641271df6f67c565ff03254df57cdd11272e`。启动预检后实际CONNECTED，日志累计events=149、gaps=310（历史累计，不能当作此次新增）；尚未证明长期稳定或根因修复。回退unit与部署记录在`quantos-dev-output/gold-message-shape-20260928`；旧副本保留。

2026-09-22 用户明确要求自动化、持久化运行，授权将此前的终端监控改为独立用户
服务。该服务自行读取用户已经保存的私有凭证，代理不打开凭证文件或获得其值。
没有下单、撤单、划转或修改交易权限，也不切换既有平台服务。

## 安装与运行位置

2026-09-25 诊断更新：当前固定副本为
`/home/kerstker/quantos-dev-output/gold-account-resident-20260922/releases/2a26906f743b3da7d824`，
源码摘要`2a26906f743b3da7d824203123ec0ef5126e19719cf38d9533a1b5c314e9fea8`。
只更新monitor的拒绝事件诊断，其他运行文件与下面旧副本逐字节相同。
95项相关测试、规范采用的Ruff F821/E9、治理检查及unit离线校验通过；
已按原持续只读监控授权更新用户unit并恢复，观察到CONNECTED。
旧unit保存在`quantos-dev-output/gold-monitor-diagnostic-20260925/original.service`。
原故障为EVENT_UNSUPPORTED_OR_INVALID，具体事件仍未知，不把重启视为根因修复。
新故障会在对应run的私有`event-error.json`记录固定枚举与哈希，不保存拒绝报文；
仍停止自动重启并保留缺口。下列位置与103项测试是原安装历史。

- 用户服务：`quantos-gold-account-monitor.service`。
- 固定代码副本：`/home/kerstker/quantos-dev-output/gold-account-resident-20260922/releases/8edec26b7c96d3216736`。
- 源码摘要：`8edec26b7c96d321673697258763f5d95e2089e6d60677ed8253e8b9d21e0554`。
- 持久化数据：`/home/kerstker/quantos-dev-output/gold-account-resident-20260922/state`。
- 用户unit：`/home/kerstker/.config/systemd/user/quantos-gold-account-monitor.service`。
- 沿用已验证Python3.12独立环境与本机HTTP代理127.0.0.1:7890。

安装时用户manager已有`Linger=yes`。服务enabled后随用户manager启动，退出终端
无需停止；机器重启后会自动尝试连接，代理或网络暂时未就绪由重试处理。未改变
系统网络/代理服务或任何Binance API权限。

## 行为

`tools.gold_account_resident`默认只做离线入口/源码检查。unit显式带`--run`并绑定
固定源码摘要；任何源码不匹配先停止，发生在凭证加载之前。没有TTY要求，不调用
getpass；凭证不存在则NEEDS_ATTENTION并退出78，不挂在输入提示上。

监控以一小时为单个run，正常结束后systemd自动续接同一数据目录。首次连接约一分钟后、之后每五分钟
进行快照、历史补录和核对；进程重启保留旧日志及缺口。首次网络预检失败不被当
空账户，后来恢复时使用第一个有效观察基线，并保留此前未覆盖状态。

网络故障、校时预检未通过（CLOCK_UNRELIABLE）及请求时间窗口过期（-1021）返回75，systemd等待60秒重试；源码/凭证/账户绑定/日志完整性异常或
未支持事件退出78并停止自动重启。停机不是放宽校验的理由。核心转储关闭，unit
不含密钥，不把凭证放进环境变量或参数。代码副本文件0444/目录0555，运行时校验
摘要；这不声称抵御同用户的恶意管理操作或root。

## 日常查看（无需输入API）

```bash
systemctl --user status quantos-gold-account-monitor.service
journalctl --user -u quantos-gold-account-monitor.service -n 20 --no-pager
```

暂停或恢复：

```bash
systemctl --user stop quantos-gold-account-monitor.service
systemctl --user start quantos-gold-account-monitor.service
```

彻底停用自动启动：

```bash
systemctl --user disable --now quantos-gold-account-monitor.service
```

这些操作不删除任何账户记录。不要再对该state目录启动第二个手动采集器；单写者
锁会拒绝并发。开发仓库修改不会自动替换运行代码；更新必须重新测试、生成固定
副本并更新unit的摘要。旧副本和旧证据保留。

## 条件单补录兼容

实测`allAlgoOrders`返回创建时间在请求窗口之外的记录。保留这些记录为订单状态
观察，并在页证据标明`ORDER_STATE_OBSERVATIONS`与窗口外创建数量；不把它们
计为窗口成交。返回满页时拒绝推断时间分页完整性。成交、资金和普通订单仍严格
校验各自窗口。订单生命周期与全历史完整性仍为false，旧失败档案不改写。

## 验收范围

103项相关测试通过，覆盖源码校验先于凭证、无交互读取、同目录续接、网络重试与
永久错误停止、首次失败后的恢复；unit通过systemd-analyze --user verify，固定
副本离线检查通过，治理和仓库卫生检查通过。未运行整个平台的发布验收门。

安装后已确认enabled/active/running，Binance连接和后续心跳成功；安全投影记录7条事件、1笔完整成交、0冲突/0乱序、下单关闭。修复后自动历史核对已得到WINDOW_CHECKED：1笔REST/流成交匹配，钱包、持仓与现金费用差额为零，保留2条普通单与2条条件单状态。随后TLS失败返回75，已实测systemd自动重启1次并恢复CONNECTED和心跳；具体后续状态看安全日志，
active不等于账户数据完整。没有真实成交事件时，不据此声称成交全链验收完成。
持仓、完整历史和对账的限制仍见[监控手册](gold-account-monitor.md)。黄金前端现已通过独立只读投影接入，见[接入说明](gold-frontend.md)；原console保持原版本，未启用策略交易。
