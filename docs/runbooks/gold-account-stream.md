# 黄金账户有时限事件采集（开发验证）

新版重连/补录/核对模式见[监控运行说明](gold-account-monitor.md)。以下保留首版短时探测模式说明；旧模式仍可使用，新版事件解析已扩展。

2026-09-22：首次账户快照已由用户实测成功。本入口新增独立的主网用户事件流，
目前仅通过离线模拟测试，尚未实连。不是常驻服务或完整手动交易监控验收。

## 用户启动

只有用户在真实终端输入凭证；代理不可代执行operator模式。沿用已验证的只读、
受限IP密钥，不开启交易权限。不需要为了验证而下单或撤单。运行默认300秒，
Ctrl-C可提前退出；每次使用新目录，不复用或覆盖旧档案。

```bash
cd /home/kerstker/QUANTOS-backtest-runtime-integration
/home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python \
  -m tools.gold_account_observe --operator-observe --seconds 300 \
  --output-dir /home/kerstker/quantos-dev-output/gold-account-stream-20260922-01
```

先执行原账户快照的校时、只读权限和稳定性预检，成功后才创建用户数据流租约。
POST被固定限制为`https://fapi.binance.com/fapi/v1/listenKey`，只创建或延长数据流
租约；无下单、撤单、划转端点。WebSocket固定为
`wss://fstream.binance.com/private/ws/<listenKey>`，租约只在内存，不输出URL或
异常原文。依据[Binance用户数据流文档](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams)。

每次上限1800秒，未实现续租；不会删除可能被其他消费者使用的共享租约。
正常结束reason为DURATION_COMPLETE；网络失败STREAM_FAILED；未知或非法事件
EVENT_UNSUPPORTED_OR_INVALID；人工停止OPERATOR_STOPPED。回传最后一行摘要即可。
前置读取失败查看baseline中的安全report摘要，不能发送持仓文件或密钥。

默认无参数只做离线入口检查，不访问网络、不读取凭证。

## 记录和已验证行为

- 私有目录0700，SQLite文件0600，单写者锁与并发只读；每次事务同步提交。
- 哈希链及禁止修改/删除触发器检查可检测本地结构/内容漂移，但不构成外部签名
  或防管理员重写的证据。记录源码摘要，旧档案不按新源码静默改写。
- 支持ACCOUNT_UPDATE、ORDER_TRADE_UPDATE、ACCOUNT_CONFIG_UPDATE及租约到期事件。
  只保存白名单字段和原文摘要，账户更新未出现的持仓仍是未知，不能解释成平仓。
- 同一事件重复出现保留证据，逐笔成交按品种/成交ID比较；冲突和同类事件乱序
  单独计数。独立离线reconcile_trades函数可与标准成交文档逐笔比较数量、价格、
  手续费和交易所报告已实现盈亏，不生成第二套资金账本。
- 快照结束到连接建立之间明确记为BASELINE_TO_STREAM缺口；断线、非法事件及重启
  都保留缺口。日志文件保留已提交前缀，不能以重新连接抹去未知。

## 尚未完成的验收

当前断线即停止，不自动重连或REST分页补录；未完成完整现金、资金费和持仓对账。
TRADE_LITE、MARGIN_CALL、ALGO_UPDATE、条件单触发拒绝及其他尚未支持事件会留下
摘要和缺口并停止；因此不能用于条件单全过程监控验收。不会把丢弃的事件当成功。
新会话account_ref仅隔离本次基线和日志，不能证明真实身份或跨会话连续性。

没有手动/App/网页来源归属证据时，事件仅代表账户活动，不能自动归因为手动操作。
没有交易发生也可能完全正常；无事件不能证明订阅覆盖完整。
history_complete、source_authenticated、submission_enabled始终false，net_pnl未知。
无实时面板接线、后台启动、自动交易或发布。本次人工短时验证用于确定真实事件流
与现有代理能否连接，之后继续补齐事件种类、稳定账户绑定、补录及现金对账。
