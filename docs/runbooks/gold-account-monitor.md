# XAUUSDT只读监控：重连、补录与核对

用户已授权并安装独立后台服务，免终端运行见[后台服务说明](gold-account-resident.md)。下方人工命令保留供独立诊断使用。

## 已授权的凭证持久化（2026-09-22）

用户明确选择“本机私有文件：无需解锁，明文保存但仅当前用户/root可读”。这是对
此前只在内存中输入凭证约定的窄范围变更，仅供人工启动的黄金只读监控使用；不
授权代理读取真实凭证，不放开交易权限，也不把秘密写入仓库、日志或命令行参数。

首次由用户运行：

```bash
cd /home/kerstker/QUANTOS-backtest-runtime-integration
/home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python \
  -m tools.gold_credentials --operator-store
```

终端隐藏输入Key/Secret，保存为当前用户家目录下
`.local/share/quantos/credentials/credentials.json`。这是明文文件，不是加密密钥库；
目录0700、文件0600，拒绝软链接、硬链接、多用户可读权限或错误所有者。文件
独占写入、fsync后发布；已保存时默认拒绝覆盖，主动换Key才加`--replace`。
保存不访问Binance，真实只读/IP权限仍由每次监控的预检验证。

保存后，原来的`tools.gold_account_observe --operator-observe ...`命令默认自动读取；
无需添加文件路径或密钥参数。没有保存文件时保留原来的隐藏提示。显式加
`--credentials prompt`可临时手动输入，既有文件不被读取或修改。已有文件损坏或
权限错误会停止并输出固定错误码，不静默降级，也不在错误中暴露凭证内容。

代理仅以临时目录和虚构凭证验证保存/读取/覆盖/链接/权限/无回显/监控接线，不
执行真实operator-store或读取真实凭证。默认`tools.gold_credentials`是离线入口
检查，不打开凭证文件。新版源码修改后旧监控档案仍须对应源码复核。

2026-09-22开发增量。用户已实测旧版300秒数据流正常结束（0业务事件，1启动缺口）；
该结果验证连接与心跳，不验证真实成交或新版历史接口。新版通过离线模拟与前后端
测试，尚未运行真实监控模式，未切换常驻服务或启用交易。

## 人工短时验收

在真实终端沿用现有只读受限IP密钥。不要发送凭证或为测试专门交易。
下面运行300秒监控，随后执行结束快照、两次历史读取和本地对账；结束阶段还需
一些时间。进度会显示PREFLIGHT、CONNECTED、HISTORY_CHECK及最终摘要。

```bash
cd /home/kerstker/QUANTOS-backtest-runtime-integration
/home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python \
  -m tools.gold_account_observe --operator-observe --monitor --seconds 300 \
  --output-dir /home/kerstker/quantos-dev-output/gold-account-monitor-20260922-01
```

回传最后一行安全摘要。补录失败时查看该次run下history-error编号文件，其中只含
固定reason和有界HTTP状态/交易所code/端点；不包含URL、签名、响应原文或密钥。
默认无operator参数只做离线检查；Codex不可运行operator模式或读取真实凭证。

同一目录续接（新版monitor目录，旧版stream目录不兼容）：

```bash
/home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python \
  -m tools.gold_account_observe --operator-observe --monitor --seconds 28800 \
  --resume-dir /home/kerstker/quantos-dev-output/gold-account-monitor-20260922-01
```

单次最长86400秒，仍是前台人工进程。Ctrl-C保存已提交前缀并停止，不在退出时
偷偷继续补录。换Key会在联网前拒绝，不能自动并入原账户日志；账户绑定仅用随机
盐和Key的HMAC匹配，不保存Key/Secret，也不是独立交易所身份认证。

## 连接与持久化

- 默认每20秒发布安全状态，页面每15秒读取；业务事件逐条同步事务落盘。
- 连续连接失败最多5次，指数退避2/4/8/16秒，上限30秒；已验证心跳或有效事件
  才重置失败计数。历史缺口永久保留，连接恢复不清空它们。
- 首个检查点约60秒、此后每300秒主动关闭本次连接，记ROTATION缺口，执行快照/历史核对，再核实权限并
  创建/延长数据流租约重连。由此保持租约有效，但检查期间不是无缝订阅；成交和
  订单状态靠REST恢复，中间不可恢复的推送转换仍保留为未知。
- 原账户校时、只读权限、IP限制门继续生效；不要求enableFutures=true。
- 根目录0700，日志/档案0600；单写者锁允许并发读取。新run目录保留旧证据。
  journal.db是追加哈希链；每次结束manifest绑定该run文件摘要与日志前缀。
  崩溃的未封存run不被离线验收当成完整档案。
- account-monitor.json以临时文件+fsync+原子替换发布；停止状态不会被旧心跳覆盖。
  超过120秒未更新，页面显示过期。API不把保存的CONNECTED报告当独立连接认证。

## 事件与补录

解析ACCOUNT_UPDATE、ORDER_TRADE_UPDATE、ACCOUNT_CONFIG_UPDATE、ALGO_UPDATE、
CONDITIONAL_ORDER_TRIGGER_REJECT、MARGIN_CALL、TRADE_LITE、listenKeyExpired。
只保留白名单业务字段；自由文本拒绝原因、租约及未知字段不落盘。轻量成交不计为
完整成交；App/网页/其他API的来源不能单凭账户事件自动判断。未知事件仍停止并
留摘要，不将未识别推送当成已处理。

GET边界仅新增userTrades、income、allOrders、allAlgoOrders，以及查询已知起始
订单状态的order/algoOrder。所有订单端点都固定GET，没有下单/撤单/划转方法。
历史成交与订单按时间二分处理满页；同毫秒仍饱和则明确失败，不跳到下一毫秒
丢记录。资金流水按page遍历，保留全账户币种和原生incomeType/tranId复合身份。
重复页、冲突、越界、页数/条数/120秒网络阶段预算超限都保留前缀并标为未完成。

相同窗口读取两遍，保留两次事实及分页摘要；不同则CHANGED_DURING_READ。稳定
仍不证明交易所没有延迟入库或超出保留期的数据。资金以income汇总核对钱包差，
手续费/已实现盈亏与成交交叉核对，不将两份记录重复记为损益。XAUUSDT成交方向
和数量核对起止持仓；其他品种标OTHER_MARKETS_UNCHECKED，不声称完成全账户核对。

快照的发送/接收时间加校时容差构成边界区；该区存在成交或流水则BOUNDARY_ACTIVITY。
只有比较窗口通过，才把后续恢复起点推进到已保存的结束快照；摘要和缺口不抹去。
恢复起点带文件摘要，跨运行延续；长期未通过且窗口超过7天会明确拒绝，需独立
历史资料处理，不悄悄截掉旧缺口。订单历史能恢复查询时状态，不能重建断线期间
每次中间改单/撤单推送；因此order_lifecycle_complete/history_complete保持false。

## 离线复核与页面

离线复核不访问网络或凭证，检查每个封存run的文件摘要、源码版本、日志哈希链及
报告。旧源码档案用对应版本复核；不可重写旧manifest使它冒充新版本。

```bash
/home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python \
  -m tools.gold_account_observe --verify-monitor-dir \
  /home/kerstker/quantos-dev-output/gold-account-monitor-20260922-01
```

黄金页面已新增连接状态、最近更新、事件/成交/缺口、补录记录数、最近观察的持仓
及含补录的成交。冲突/同连接乱序时不挑一份持仓数据显示为正确。部分持仓推送
不清空未出现的持仓；持仓始终标为观察值，完整性未知，净盈亏未知。

为现有只读console配置`QUANTOS_GOLD_OBSERVATION_ROOT`为上述monitor目录即可读取
account-monitor.json；未配置则沿用原运行根下data/gold/observation。此变量由运维
启动配置提供，不接受HTTP用户路径输入。页面/API代码已构建，**本批没有设置运行
环境变量、重启服务或发布页面**；常驻服务切换要在人工真实验收后另行完成。

## 验证范围与余项

离线测试覆盖重复成交、断线恢复、同Key重启、换Key拒绝、满页/同毫秒饱和、延迟
流水、手续费不重复计账、钱包/数量差异、档案篡改、页面过期及未知状态。包括收到
成交→断线→重复成交→REST补录→核对→离线复核的模拟闭环。

最终后端275通过/1跳过、前端14通过，前端构建、导入图、治理/卫生与文档检查通过。
这些是开发增量证据，不是完整scripts/validate.py发布验收。真实成交/条件单事件、
主网历史响应、自然断网和长期稳定性尚须人工运行验证。常驻服务部署、策略模拟
经济验收及最终自动交易权限均未完成，现有人工门不变。

接口来源：[用户数据流](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams)、
[交易历史与订单](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)、
[资金流水](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account)。
ALGO字段对照官方[数据模型](https://github.com/binance/binance-connector-python/blob/master/clients/derivatives_trading_usds_futures/src/binance_sdk_derivatives_trading_usds_futures/websocket_streams/models/algo_update_o.py)；仅查阅规范，没有导入/安装交易SDK。
