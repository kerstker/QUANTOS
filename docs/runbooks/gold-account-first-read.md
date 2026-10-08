# XAUUSDT账户首次只读接入

## 首次真实成功检查点

2026-09-22 用户在白名单追加第二个候选出口IP后运行目录08，返回权限ACCEPTED、
10项READS_COMPLETE、STABLE_OBSERVATIONS；合约/现货交易及划转等已检查写权限
仍关闭，读取和IP限制开启。Codex执行同版本--verify-dir仅做离线档案重算，exit0，
结果与用户摘要一致；未读取凭证、未再查询私人接口，私有账户明细不写入项目文档。

资料位置由用户掌控，为开发输出gold-account-first-read-20260922-08；保留之前所有
失败目录。本次只证明一段有界只读查询与本地重算完成，不是持续监控启动、完整
历史、原子快照或资金持仓对账。收益仍未知，下单权限仍关闭。新源码升级后复核
旧档案须使用对应源码，不能改写旧manifest。

两家IP查询服务曾返回不同地址且本机代理链相同；追加白名单后本次成功与IP限制
问题相符，但未取得Binance实际来源IP记录，不能宣称某个地址是唯一出口或唯一
根因。无需为研究监控开启合约交易权限。

下一开发阶段：在已验证的普通USD-M只读边界上接持续事件、断线补录、稳定的跨
会话账户绑定及逐笔/现金对账，最后接实时面板。首次成功不解除这些验收缺口。

适用范围：用户确认的普通Binance USDⓈ-M账户，App与网页手动操作。当前为首次
有界账户检查，不是连续监控服务，不下单、不撤单、不改杠杆或账户模式。

## 已准备的入口

`tools.gold_account_capture`默认离线检查。只有用户在自己的真实终端明确传入
`--operator-read`才提示隐藏输入API Key和Secret；没有凭证文件、环境变量或命令行
密钥参数。Codex不可执行该模式、读取凭证或采集真实账户。

```bash
cd /home/kerstker/QUANTOS-backtest-runtime-integration
/home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python \
  -m tools.gold_account_capture --check
```

`READY_FOR_OPERATOR_READ`只表示本地入口准备好，不表示密钥有效、账户连通或完成
软件发布验收。首次实际接入需由用户操作，真实权限及响应尚未验证。

## 用户操作

1. 在Binance准备独立、只读的HMAC API凭证，允许读取并限制为本机出口IP。
   关闭交易、提现、内部/通用划转等写权限。不要发送到聊天、写进命令行或仓库，
   不复用用于交易的API Key。用户网站/App手动交易不通过这把监控密钥。
2. 在本机终端运行下方命令，按隐藏提示输入Key和Secret。输出父目录已存在；
   目标目录必须全新，失败也保留原目录，重试时改为新编号，不删除旧证据。
3. 返回终端最后一行状态摘要即可，不发送capture.json、订单编号、余额或凭证。
   有持仓或挂单无需为了采集平仓/撤单，也不要专门下测试单。

```bash
cd /home/kerstker/QUANTOS-backtest-runtime-integration
/home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python \
  -m tools.gold_account_capture --operator-read \
  --output-dir /home/kerstker/quantos-dev-output/gold-account-first-read-20260922-01
```

入口先访问公开校时，再查询API权限，之后执行10次固定GET：前置账户模式/账户/
普通挂单/条件单，持仓及品种配置，后置普通挂单/条件单/账户/账户模式。保留全部
品种，不能只取XAUUSDT后声称账户级对账。条件单与普通挂单独立查询。
每次网络读取超时10秒，逐请求校验时钟与总采集时间预算；无自动重试和后台循环。
超时/断网/限流/权限不符/不支持字段均终止，已有校验后的前缀保留。尚未有独立
进程的硬墙钟超时，因此不得视为常驻服务的故障隔离方案。

权限查询要求所有已知写权限明确false，读取和IP限制明确true；缺字段也停止。
若返回READ_ONLY_PERMISSION_REQUIRED，新版摘要中的permission_check逐项显示固定
权限名的ENABLED/DISABLED/MISSING/INVALID_TYPE；unknown_permission_fields只显示
布尔值，不回显未知字段名或值。该诊断以permission-check.json保留并参与复核。
MISSING不是“用户没有关闭权限”的证据，不应据此盲目修改密钥。首次用户实测仅
返回旧版笼统拒绝、read_count=0，具体原因尚待新诊断；旧档案保留，新运行改目录
编号为02。新版源码不能冒充旧版档案对应源码，旧档案复核需原版本。
若只读凭证被合约查询拒绝，不要为通过而开启交易权限。仅回传`reason`，后续
核对账户权限模型，必要时转为用户导出的资料；不能宣称只读接入已经成功。

## 结果与复核

- `STABLE_OBSERVATIONS`：前后配置、钱包/交叉钱包、持仓数量和两类挂单一致。
  浮动盈亏的市场变化不视为现金交易；一致仍非原子快照、完整历史或实时监控。
- `CHANGED_DURING_CAPTURE`：保留发生变化的快照，需要后续补录与对账，不强行
  重跑直到得到稳定结果来覆盖旧资料。
- `INCOMPLETE`：某项失败/中断，read_count表示保留的有效读取数，不把未知当空仓。
- `FAILED`：本地入口/文件操作未完成。不要删旧目录，也不要发送私有错误原文。

网络失败细分reason：NETWORK_TIMEOUT、NETWORK_UNAVAILABLE、TLS_FAILED、
REDIRECT_REFUSED、EXCHANGE_UNAVAILABLE、REQUEST_REJECTED；不输出URL、签名、
代理地址或服务端错误原文。第三次用户接入03未留下clock/permission文件，随后
独立公开time GET成功，但不能追认旧失败根因。原档案保留，后续人工运行使用04。

第四次04已完成公开校时，在权限查询返回HTTP拒绝。新版摘要新增request_error，
只包含http_status、有效有界负整数exchange_code（否则null）、固定白名单endpoint。
错误正文、msg、URL查询串、签名及任何未知字段都不保存。诊断与档案绑定并重算；
后续人工运行用05，不覆盖旧档案。只读权限门仍保持，先依据数字code定位，
不要凭REQUEST_REJECTED猜测原因或放宽权限。代理可能按域名分流，公网IP查询
网站的出口仅供参考，不能证明Binance请求采用相同出口。
错误定义参考[Binance官方错误码](https://github.com/binance/binance-spot-api-docs/blob/master/errors.md)。

目录权限0700，文件0600；保存白名单字段与原始响应摘要，**不保存完整原始响应**，
避免未知/账户标识字段被直接落盘。每项读取独占写入并fsync，最后完成清单绑定
所有文件与源码摘要。部分目录缺manifest不能视为成功。随机account_ref仅用于
本次档案隔离，不证明交易所账户身份，不能据此跨会话合并账户历史。

```bash
/home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python \
  -m tools.gold_account_capture \
  --verify-dir /home/kerstker/quantos-dev-output/gold-account-first-read-20260922-01
```

复核不联网，检查同版本源码、文件摘要、读取前缀、权限/校时与报告重算。
结果对应采集时刻，不是当前实时状态；本地摘要不能认证交易所来源或防止整体
替换，`source_authenticated/history_complete`始终false。

## 后续接线边界

首次真实只读权限/字段确认后，再验证持续私有事件流、断线补录、分页成交与资金
流水、跨会话账户绑定及快照对账，然后接实时面板。目前旧黄金面板仍是离线资料
面板，不能把本工具的检查成功改成“连续监控已连接”。自动交易仍需独立策略、
风控与人工权限验收。

本批仅模拟传输验收，未运行真实操作模式、部署或切换服务。
接口依据：[账户查询](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account)、
[普通/条件订单](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)、
[API权限](https://developers.binance.com/en/docs/catalog/core-trading-wallet/api/rest-api/account)。
