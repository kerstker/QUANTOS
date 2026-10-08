# Binance账户资金总览

2026-09-22用户明确选择同一Binance账户下合约、现货、资金账户的USDT/USDC，
授权后台沿用私有凭证自动读取。现有凭证权限仍为只读且有IP限制；没有划转、
提现、下单或权限变更，代理没有打开凭证文件。

## 展示

入口`http://192.168.5.68:15911/studio/wallets`，侧栏“账户资金”。
USDT/USDC分别显示；不做1:1折算，不把两币种相加。账户总额为分次观察的钱包余额
合计，不加合约浮动盈亏，不宣称原子一致。任一账户失败或过期，合计未知；
缺失合约资产不猜零。现货/资金的成功稀疏空列表可表示零余额。

合约显示钱包、可用、未实现盈亏、保证金余额、初始保证金占用，不把保证金当作
冻结现金。现货显示可用和锁定；资金账户另显示冻结和提现中，钱包字段合计采用
free+locked+freeze+withdrawing。范围不含理财、杠杆、币本位及子账户。

## 查询和运行

每轮先刷新`/sapi/v1/account/apiRestrictions`只读/IP权限检查，然后固定查询：

- 合约`GET /fapi/v3/account`。
- 现货`GET /api/v3/account`。
- 资金`POST /sapi/v1/asset/get-funding-asset`。

最后一项虽然使用POST，却是官方[Funding Wallet查询](https://developers.binance.com/en/docs/catalog/core-trading-wallet/api/rest-api/asset#funding-wallet)，不是转账。
网络模块只能接收固定kind，不能传任意URL、端点或参数；私密错误原文不落盘。

`quantos-binance-wallets.timer`开机启动、每次完成后60秒触发下一次；由用户manager
既有Linger支持退出终端后运行。service是有界oneshot，密钥仅其进程自行加载。
当前固定源码release-7位于`/home/kerstker/quantos-dev-output/gold-frontend-20260922`，
启动前验证全文件清单；网页与投影也更新为release-7，既有研究执行后台未切换。

结果保存在实时监控state下`account-wallets.json`和`wallet-observations/`历史快照，
0700/0600权限；网页15秒刷新，180秒判陈旧，不读取凭证。

## 验证

后端122通过1跳过，包含币种隔离、缺失不补零、资金POST查询白名单/越界拒绝及
只读权限守门；前端11通过，生产构建、服务导入图、治理和仓库卫生通过。
实际三账户均成功返回，定时器自然运行多轮；一次临时失败明确UNAVAILABLE，
随后自然恢复OBSERVED。浏览器验证金额与API一致、USDT/USDC切换及手机/暗色布局。
没有把本次增量称为全平台发布门通过或经济授权。

暂停：`systemctl --user stop quantos-binance-wallets.timer`；恢复：
`systemctl --user start quantos-binance-wallets.timer`。原账户记录仍保留。
