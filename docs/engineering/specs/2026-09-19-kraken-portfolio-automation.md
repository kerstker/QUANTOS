# Kraken xStocks / XAUT 自动化管理接续

日期：2026-09-19。状态：公开数据自动观察首批实现，未完成账户管理或实盘执行。

用户明确目标为自动管理完整组合，股票采用 Kraken xStocks，黄金使用 XAUT；用户声明已具备产品授权且已有 xStocks 持仓。不再要求地区信息，不将其持仓当作接口已验证事实。账户授权声明与程序内签名、风控、人工 mandate、账本及执行权限分别处理；本批不读取凭证、不修改实盘开关、不提交订单。

## 本批已实现

- `services/data/kraken_public.py`：纯离线产品身份与已收盘行情校验，严格区分 currency 与 tokenized_asset，保留 Kraken 返回的标识、精度、最小数量/金额和市场状态；公开上架不冒充账户准入。
- `tools/kraken_public_capture.py`：仅 AssetPairs/OHLC 两种公开 GET，无 SDK、私有路径或下单能力。限制响应量，拒绝重定向，保留原始响应摘要并从原始响应复算规范化结果。
- 复用 `SqlitePITStore` 与既有摄入入口。知识时间为真实接收时间，不把今天取得的历史价格回填为历史已知；不变数据保留最早知识时间，修订另增版本，旧记录不删除。持久化使用文件锁，绑定产品/周期配置，拒绝软链接；重放旧采集不回退最新状态。
- `tools/kraken_portfolio_observer.py`：显式1–32个产品配置、有界轮询、逐产品隔离失败、原始采集/周期记录留存、重启续接和时效判断。配置变化需独立状态根，不能无声更换原观察范围。
- 观察报告明确区分数据采集、账户未连接、策略未配置；`portfolio_management_ready=false`，不会把采集全绿称为组合管理完成。

## 真实接口验证

无密钥读取 AAPLx/USD、NVDAx/USD、XAUT/USD，每个取得720根已收盘小时线。它们是接线验证样本，不是推荐配置或用户实际持仓清单。

首轮AAPLx返回721条（720历史+1当前），其中零成交量时VWAP为0，先前严格形状校验拒绝了响应；已用回归复现并修复这两种真实格式，原失败捕获保留。最后一根无论时间如何始终排除；零量不补成可成交流动性。

实际材料在本机 `/home/kerstker/quantos-dev-output/kraken-automation-20260919/`：逐产品的原始采集/PIT库、观察配置，以及 `observer/cycles`、`observer/status.json`。本轮运行两个有界自然轮询周期后退出，不是已安装常驻生产服务。

## 复现入口

使用隔离开发Python，指定独立可写开发目录：

```bash
/home/kerstker/quantos-dev-output/dgx-engine-foundation-20260905/venv/bin/python -B -m tools.kraken_portfolio_observer \
  --config /home/kerstker/quantos-dev-output/kraken-automation-20260919/observer-config.json \
  --state-root /home/kerstker/quantos-dev-output/kraken-automation-20260919/observer \
  --cycles 2
```

仅公开市场研究观察；外部请求超时或格式错误会保留失败，不能伪造成功。该模式并不承诺完整长期历史，滚动接口不足以重建更早的数据；所有结果保持非权威。

## 仍需顺序接通

1. 账户只读接入：由既有人工控制的凭证隔离方式获取余额、锁定数量、挂单、成交与流水；覆盖用户已有持仓，不假定空仓或凭空设成本。
2. 组合控制器：绑定明确策略、产品池、风险预算与现有账户，复用组合优化和受支持风控；处理缺失价格、费用、币种估值、数量变化及配置身份。
3. Kraken执行与会计适配：现货/xStocks不能套用Binance永续资金费/保证金语义；复用原OMS与恢复机制，补齐现货现金、资产、手续费和回执的规范接口，不连接失效V1路径。
4. 自动循环：授权范围内决策→预留→提交→确认→对账→下一周期；发送结果未知只做原编号恢复，不能盲目重发。不能用简单定时器包装现有一次性控制器就宣称完成。
5. 运行验收：断网/重启、余额和挂单变化、备份失效、停机及授权到期均有确定行为；部署前完成具体版本验收和可审查安装材料。

原系统旁路失败已定位：实时特征来源full-audit证明无效；watchdog调度元数据检查失败；原备份驻留8d61版本封装重试额度耗尽。未重置额度、清理原失败或切换受保护服务。

## 官方接口依据

- [产品目录参数与产品类别](https://docs.kraken.com/api-reference/market-data/get-tradable-asset-pairs)
- [OHLC末尾未确认数据与滚动历史限制](https://docs.kraken.com/api-reference/market-data/get-ohlc-data)
- [xStocks产品说明](https://www.kraken.com/xstocks)

验证：新模块先复现缺失失败；真实721条/零量响应和旧捕获回放回退问题也先复现，再修复。新行为及既有PIT/摄入联合48项通过，治理扫描通过。三资产两轮实采均CAPTURED，每产品首轮720条、第二轮未重复新增。未运行完整canonical门禁，不继承旧版本完整发布验收。

## 第二批：已有账户只读观察（未实际读取）

新增 `services/data/kraken_account.py` 与人工入口 `tools/kraken_account_snapshot.py`。两轮按 BalanceEx→OpenOrders 读取，固定 `rebase_multiplier=base`，比较余额与挂单变化；保留资产原名，包括 `.T` 后缀，不自动映射普通股或合并奖励余额。使用独立80位Decimal上下文计算报告可用额、剩余挂单量；授信/借款、负可用额、杠杆挂单及变动分别标阻塞。稳定仅代表两次观察相同，不是原子快照、完整账户权威、成本基已知或交易许可。

人工工具只允许两个查询端点。交互隐藏输入API公钥/secret，签名和凭证不导出，响应仅保存白名单化的财务字段、时间与原文摘要；文件0600、目录0700。nonce按凭证命名空间加锁，先持久化后发送，重启/时钟回退不复用；应使用该工具专用的查询凭证，不能保证其他程序共享同一key的nonce顺序。首个API拒绝立即停止，不重复四次无效请求。当前未实现API二次验证口令输入；不要求用户关闭安全设置，若现有查询凭证需要该能力则另补支持。

测试：账户与工具新增15项通过（先复现模块缺失和拒绝后重复请求，再修复）；上批公开观察18项曾在本轮联合检查中通过。服务依赖图检查发现新增模块导致产物过期，已用原生成器重建，9项图检查通过、无循环。没有真实账户读取、凭证访问或下单；人工读取后仍须核对实际字段与持仓范围。主网执行、金额/风险限额和持仓管理范围仍需现有操作员流程明确，不能用只读导出代替。

官方依据：[BalanceEx可用额与数量口径](https://docs.kraken.com/api-reference/account-data/get-extended-balance)、[OpenOrders数量口径](https://docs.kraken.com/api-reference/account-data/get-open-orders)、[REST签名与nonce](https://docs.kraken.com/exchange/guides/rest/authentication)。


## 第三批 xStocks 数量口径与公开乘数接线

新增 `services/data/kraken_units.py`，保留 base 代币数量，按观测时刻乘数精确计算对应股票份数；独立 Decimal 精度避免受调用方上下文影响。XAUT 保留原生资产单位，不标成股票。这里只提供当前数量展示，不提供历史估值、收益、订单数量舍入或执行授权。

公开传输新增 Assets 白名单。既有组合观察器升级到 `KRAKEN_PORTFOLIO_OBSERVER_V2`，每项保存 Assets 请求、接收时间、原文和 SHA256，以及校验后的单位元数据。产品类别、资产身份、状态或乘数不符合要求时明确失败；缺失乘数不默认成 1。元数据有效期 120 秒，周期结束再检查一次，超时整体报告 PARTIAL。V1 行情原始记录合同保持，公开采集仍不代表账户权限。

隔离实采路径：`/home/kerstker/quantos-dev-output/kraken-automation-20260919/observer-units/status.json`。一轮三项 CAPTURED，各写入 720 根已闭合小时线；AAPLx 乘数 `1.0032690125398187`，NVDAx `1.001701196801074`，XAUT 原生数量乘数 1。这些数值仅代表本次接收时刻，不回填历史。

验证：先运行新增换算回归得到缺少模块的 RED，再实现；单位、公开数据、公开采集、观察器与服务依赖图联合 **37 passed**。未运行完整发布门，未切换服务、未读取真实账户或提交订单。账户只读导出、管理资产池与资金上限仍待人工输入；随后接账户数量映射、估值及受约束调仓准备。

口径参考：[Kraken xStocks FAQ](https://support.kraken.com/articles/xstocks-faq)、[Assets API](https://docs.kraken.com/api-reference/market-data/get-asset-info)。乘数随公司行动变化，不能将本次元数据用作此前时刻证据。
