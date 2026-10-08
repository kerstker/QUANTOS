# 黄金账户前端接入（2026-09-22）

用户要求把后台监控接入当前网页，授权本次只读显示服务与网页更新。

## 接线

- 页面：`http://192.168.5.68:15911/studio/gold`，侧栏“黄金账户”。
- 已有运行监控页面也保留黄金卡片，每15秒刷新。
- `quantos-gold-observation-api.service`在127.0.0.1:15844仅提供
  GET `/api/v1/console/gold-observation`，读取后台`account-monitor.json`。
- 网页服务仅将上述精确路径交给新接口，其余console/runner路径继续走15840/15842。
  GET/HEAD限制和原LAN网段限制保留；新接口不读API凭证、不连接交易所、不提供写操作。
- 数据根为`/home/kerstker/quantos-dev-output/gold-account-resident-20260922/state`。
- 部署档案在`/home/kerstker/quantos-dev-output/gold-frontend-20260922`；
  `release-7`同时提供只读投影API，`release-7`为带账户资金及历史资料的图表网页/网关（旧版本保留）。包含实际源码、前端资源和SHA256清单。
  基础Git HEAD不能代表未提交改动，实际内容以integration-manifest及其摘要绑定。
- 网页通过独立systemd drop-in更新。保留原版本证据验证，新增本次文件清单验证；
  既有研究console、runner、账户采集进程不因网页更新重启。

## 验证和边界

后端接口/代理159通过1跳过；前端全量474通过，新增导航后相关14项再次通过；
TypeScript/生产构建、服务导入图、治理扫描、仓库卫生通过。
正式15911浏览器实测CONNECTED、13事件/2成交、WINDOW_CHECKED、下单关闭；
桌面/390px手机无页面脚本错误或横向溢出。三个既有后台PID更新前后一致。
这属于只读接入增量，没有声称全平台发布门通过或取得经济授权。

页面显示采集状态、事件及成交数、历史缺口、补录核对、最近持仓观察值及成交。
缺失、过期、断线分别显示；完整历史、净盈亏和订单生命周期仍不据此认定。

## 回退

移除本次`quantos-next-web.service.d/gold-observation.conf`后daemon-reload并重启
网页服务即可恢复原网页/网关；原不可变版本保留。后台账户记录不删除。
独立投影接口可用`systemctl --user disable --now quantos-gold-observation-api.service`停用。

## 图表版增量

用户要求图表化后，增加账户概览、价格散点、逐笔数量柱状图、方向筛选、成交时间线
和持仓卡片，原详细资料默认折叠。仅绘制最近账户成交，不补造行情K线、持仓曲线
或净盈亏。新增筛选/缺失测试，相关19项通过，生产构建通过。真实浏览器桌面、
390px手机、暗色及方向筛选验证通过；只更新网页服务。

历史记录现已接入，见[导入范围及结果](gold-history-backfill.md)。

同一Binance的合约、现货、资金账户USDT/USDC见[资金总览](binance-wallets.md)。
