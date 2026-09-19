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
