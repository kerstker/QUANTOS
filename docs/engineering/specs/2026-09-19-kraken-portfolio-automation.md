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


## 第四批 账户与市场归档关联（离线验证）

新增 `services/data/kraken_inventory.py` 和 `tools/kraken_inventory_report.py`。工具读取人工导出的账户快照及 V2 公开观察目录，核对内容哈希、捕获原文、产品身份、乘数请求和时间，然后关联余额与挂单。哈希只提供本地内容一致性，不是外部签名或经济授权。

- xStocks 的 `.T` 账户后缀仅映射到明确的 tokenized_asset 产品；不对 `.F/.S` 等其他后缀猜测归并。保留账户原始键，多个余额键映射同一资产时标歧义，不求和。
- 现金按产品目录的实际 quote 标识关联，保留余额、冻结、授信、已用授信、可用数量；不把可用额度或所有现金自动当成管理资金。
- 每项挂单保留未成交量并映射产品；未知余额、未知订单、信用敞口和旧账户/乘数明确列入 issues。当前轮询报告不能掩盖较早的账户扫描时间。
- 乘数从留存 Assets 原文重新解析，数量保持 Decimal 精度。输出 `INVENTORY_MAPPED` 只表示数量关联完成；NAV 为 null、成本未知、保证金持仓未观察、账户全范围未经核实、组合管理与下单均为 false。

调用方式（无网络、无凭证；输出文件必须不存在）：

```bash
python -m tools.kraken_inventory_report --account /absolute/account-observation/latest.json --observer-root /absolute/observer-units --output /absolute/private/inventory.json
```

需使用 DGX 已验证解释器；默认以执行时刻重新检查时效，旧导出会产生 UNRESOLVED，而不是当前完整组合。输出文件权限 0600。未修改既有人工账户导出冻结副本。

验证：新增回归先 RED（模块缺失），实现后账户/单位/观察/依赖图联合 **40 passed**。模拟账户归档完成端到端关联，篡改哈希、跨产品乘数、路径穿越和过期均拒绝或明确未解决。实际账户导出尚不存在，未读取真实持仓、未执行估值或调仓、未运行完整发布门。后续接实时价格及价格数量单位验证、组合估值、明确资产/资金约束和调仓准备。

账户后缀依据：[Kraken Extended Balance 文档](https://docs-legacy.kraken.com/api/docs/rest-api/get-extended-balance/)。


## 第五批 买卖报价与指示性现货估值

新增 `services/data/kraken_quotes.py` 与 `services/portfolio/kraken_valuation.py`。公开 GET 白名单加入 Ticker，观察报告升级 V3：每项保存报价请求、原文、哈希、接收时间及 Decimal 买卖价/中间价。要求精确产品身份、正价/正数量、非倒挂盘口，过期或接口错误使该项失败，原文仍留存；周期结束再次检查报价和乘数有效期。REST Ticker 未提供源事件时间，接收时间不冒充交易所事件时间。

已有 `tools/kraken_inventory_report.py` 新增可选 `--valuation-quote ZUSD`；消费 V2 时仍可完成数量关联，但不能凭空提供报价。消费 V3 时从报价原文重算，不信任报告中可编辑的派生价格。估值按指定的实际 quote 标识计算，不把 USDT/USDC 自动当 USD，不在无汇率数据时跨币种加总。

估值保留余额完整数量；冻结金额不重复扣减，挂单名义额不再次加到资产上。xStocks 用对应股票份数，XAUT 用原生数量，得到中间价/买一价的指示性现货总值；未知资产、缺价、重复映射、数量/币种不符或过期均令完整合计为 null。费用、市场冲击与历史成本未纳入，`net_asset_value` 仍为 null，执行资格 false。

价格口径限制：官方 [xStocks FAQ](https://support.kraken.com/articles/xstocks-faq) 描述股价和乘数关系，但 [Ticker 文档](https://docs.kraken.com/api-reference/market-data/get-ticker-information) 未单独声明 xStocks 价格分母。当前 xStocks 报价的股票份数口径明确标记 `INFERRED_FROM_KRAKEN_XSTOCKS_FAQ`，仅用于指示性展示，不能冒充已核准订单或会计合同。后续需进一步确认该口径及公司行动时的一致性。

隔离真实公开验证：`/home/kerstker/quantos-dev-output/kraken-automation-20260919/observer-quotes/status.json`，一轮 AAPLx、NVDAx、XAUT 均 CAPTURED，各720根闭合小时线，三项 Ticker 有效；结果哈希 `a5260e12e3a2ecccecd9d3c364174641459c8ae581570d81cd9e4e697d23d003`。这些是本次公开观测，不是账户交易或历史收益。

验证：报价与估值新模块先 RED 后实现；报价/估值/库存/报告/观察器/公开采集/依赖图联合 **37 passed**。端到端估值使用模拟账户，未读取真实账户。实际 pre-commit 钩子另行执行，未运行完整发布门，未切换驻留服务。下一步仍包括账户实接、成本与现金流、已明确资产/资金约束下的调仓准备、执行恢复及完整验收。


## 第六批 明确配置下的目标金额与资金约束

新增 `services/portfolio/kraken_allocation.py`，并接入既有库存工具的 `--policy /absolute/policy.json`。配置必须显式包含 `schema=KRAKEN_ALLOCATION_POLICY_V1`、`account_ref_sha256`、`quote_asset`、`capital_limit`、`cash_reserve`、`max_asset_weight`、`target_weights`（完整产品身份→十进制权重字符串）。不补默认资金/资产范围，不把配置当成人工 mandate 或策略经济授权。用户尚未给出这些实际值，因此没有创建真实账户配置。

- 配置绑定账户查询命名空间与计价币；严格字段、非负权重、总和≤1、单资产上限与正资本限额均校验。未知目标产品必须有当前公开报价才能进入草案，不把不存在的资产直接当空仓。
- 预算=min(资本上限，配置范围内已持仓指示性市值+可用自有现金)。自有现金按余额扣冻结，与报告可用额取较小值，再扣保留现金；授信不增加预算。范围外持仓保留且不自动清仓。
- 输出每项当前金额、目标金额和差额，使用高精度 Decimal。买入金额若依赖售出资金会明确标记，尚未完成的售出不作为已到账现金。减持超过可用持仓时阻止草案；存在任何挂单、旧库存或不完整估值时不输出目标。
- 报告为 `REVIEW_ONLY` 或 `BLOCKED`，无订单数量/价格/执行身份，`submission_enabled=false`；继承报价口径推断、未核准全账户范围、费用/冲击未计入等限制，不接预交易门或订单通道。

端到端工具从归档账户、产品、报价重新计算估值与目标，并记录配置内容哈希。CLI 输出分列库存、估值和配置草案状态，任一阻塞返回非零。模拟资金配置用于验证，不能解释为用户选择或投资建议。

验证：先新增回归得到模块缺失 RED，完成后资金约束/报告/估值/库存/依赖图联合 **30 passed**。覆盖预算、冻结现金、授信、账户错配、配置缺项/超重、挂单、过期、新产品、冻结持仓以及端到端配置关联。未运行完整发布门，未部署、读取凭证或发单。真实管理范围、策略证据、价格单位核准、费用/成本现金流与订单恢复仍未完成。


## 第七批 可续跑的组合审阅周期

新增 `tools/kraken_portfolio_cycle.py`，按公开市场采集→固定账户导出→明确资金配置→库存/估值/目标审阅顺序运行，可指定有限轮数。它消费人工账户快照，不查询私有 API、读取密钥或提交订单；尚不是自动账户采集或自动交易控制器。

- 公开采集可在账户/配置未到位时继续。每轮明确显示 `WAITING_ACCOUNT`、`WAITING_POLICY`、`MARKET_FAILED`、`INPUT_REJECTED`、`BLOCKED` 或 `REVIEW_ONLY`，并记录当前阶段；执行始终 DISABLED。
- 私有根目录要求当前用户所有及0700，账户/配置快照和周期记录为0600。状态目录绑定公开配置、账户路径、配置路径、计价币；首个账户身份和策略配置哈希固定，发生变化不自动切换。未配置文件可以在后续自然到位。
- 每轮重算库存时效，保存独立结果，不复用旧成功草案。账户读到后先存成固定内容快照再计算，减少文件更新导致读取不一致。配置原文也归档；内容哈希不代表外部签名或交易授权。
- 跨进程排它锁避免重叠；周期记录含递增序号及前轮哈希。先保存周期再发布状态，启动时可接续已归档但未发布的周期；损坏记录、身份切换或时钟回退拒绝。尚未宣称断电持久性或远端灾备已验收。

示例入口（使用 DGX 已验证 Python；不存在的 policy 路径表示等待输入，不生成默认参数）：

```bash
python -m tools.kraken_portfolio_cycle --config /absolute/observer-config.json --account /absolute/account-observation/latest.json --policy /absolute/managed-policy.json --state-root /absolute/private/review-runtime --valuation-quote ZUSD --cycles 1
```

验证：新模块先 RED，之后周期/报告/观察器/配置联合 **27 passed**，覆盖缺失输入后到位、归档后状态指针丢失恢复、配置与账户变化、市场失败不沿用旧结果、账户过期、私有权限、并发锁及固定配置。未安装驻留服务、未运行完整发布门。下一步需实际账户与配置到位、账户持续采集隔离、成本/成交/现金流和执行恢复等闭环。


隔离实跑：`/home/kerstker/quantos-dev-output/kraken-automation-20260919/review-runtime/status.json`。有限一轮结束退出码3，generation=1、market_status=CAPTURED、status=WAITING_ACCOUNT、execution_status=DISABLED；结果哈希 `f2f1409d4de08e5ade4728e56db7413672c42984f5311ff7cd0c23a9d1ee2233`。账户导出和 `managed-policy.json` 均未创建；退出码3代表输入未齐，不是公开采集失败。该进程已结束，不是常驻自动管理服务。


## 第八批 账户连续刷新与订单状态接入

用户再次明确“优先把闭环补齐”。后续优先级固定为目标仓位→受控订单提交/状态恢复→成交与费用→现金/持仓对账→下一轮决策；暂停扩展展示功能。现有公开轮询、估值、草案和 REVIEW_ONLY 均不视为闭环完成。

本批收尾账户新鲜度缺口：原人工只读工具新增 `--cycles`（1–1440，默认1）与 `--poll-seconds`（60–3600，默认60），人工一次隐藏输入后，凭证只留进程内存，按周期刷新两轮 BalanceEx/OpenOrders。会话排它锁、开始前账户身份校验，接口/网络首次失败即留存并结束，避免连续重试失效凭证；UNRESOLVED/STALE 仍保留，退出码反映本次是否有不稳定轮次。Ctrl-C结束，不自动重启。未增加私有端点或真实凭证访问，未启动真实会话。

修复同毫秒失败遮蔽：原保存逻辑只接受严格较新时间，导致与上一成功同时间的失败不能更新 latest。新增回归先复现，再改为同时间失败优先，重放同时间成功不能覆盖失败。

闭环核心开始接线：新增 `services/data/kraken_order_state.py`，复用 `quantos.contracts.order` canonical OMS，无另建状态机。Kraken pending/open/closed/canceled/expired 与成交量联合校验，open可分未成交/部分成交；未知状态、open但全成交、closed但未全成交和pending已成交均拒绝。成交量不能倒退，终态不能改写；撤销/过期保留已成交量，不代表费用与现金对账完成。现有账户挂单归一化已使用该映射输出 canonical_state；订单终态查询、成交归档、现金核算和执行恢复尚未接通。

复用核查：`live/execution/position_ledger.py` 的数量与价格为 SQLite REAL，`live/execution/reconciliation.py` 为浮点仓位比较；MEXC现货适配器不能直接充当 Kraken xStocks 的精确现金/费用账本。后续须接明确单位的成交及现金证据，不借旧模型宣称完成。

验证：账户连续采集回归3项先失败，同时间失败覆盖回归先失败，订单映射新模块先缺失；实现后订单状态/账户工具/账户模型/组合周期/原OMS状态/依赖图联合 **98 passed**。未跑完整发布门，未部署、读取真实凭证或下单。已冻结的9208995d8人工单次导出入口保持原样，本批会话功能尚未替换该入口。

订单格式依据：[Kraken Query Orders Info](https://docs.kraken.com/api-reference/account-data/query-orders-info)。下一步优先处理订单身份、部分成交、手续费、断点恢复和逐资产现金对账，再贯通执行；不继续堆叠显示功能。


## 第九批 已知订单恢复与下一轮阻断

新增 `services/data/kraken_order_query.py`：固定 QueryOrders 的 `trades=true`、`rebase_multiplier=base`，精确匹配请求订单集合，保留累计成交量/成交额/费用及成交编号。响应缺单、时间失效、重复编号、状态数量不一致拒绝。跨观察检查同一账户、单调时间、订单产品/方向/类型/原始数量一致、成交数量/金额/费用不倒退、终态不改写；改单或费用修订暂按未解决拒绝，需要专门证据处理。

人工账户采集工具新增可选 `--track-orders`，私有端点白名单扩至 BalanceEx、OpenOrders、QueryOrders，仍无下单/撤单路径。订单ID先保存，再查询；下一次即使订单从挂单列表消失，仍查询原ID。私有目录保存查询原文及标准化结果、最后成功观察和当前恢复状态；失败不抹掉已知订单，当前状态改为 UNRESOLVED，启用跟踪的会话停止重试。一次最多50个已知ID，超限拒绝，尚未做历史全量回补/分批归档；仅跟踪首次观察以来见过的挂单，不宣称观察了完整账户订单历史。

`tools/kraken_portfolio_cycle.py` 已消费账户目录下的 `order-recovery.json`。已接过该证据后若文件消失或过期，会阻止后续配置草案。只要有已跟踪订单，尚无成交/费用/现金核对就明确 `TRADE_AND_CASH_RECONCILIATION_REQUIRED`，即使订单已全成交或撤销也不生成下一轮配置草案。`cash_reconciliation_status=NOT_CONNECTED`，查询订单不是资金对账完成；无成交明细时不会把累计量创造为一笔 fill。未确定费用币种时 `fee_asset_status=UNKNOWN`。

验证：新增查询回归先 RED，完成后查询/OMS映射/账户工具/组合周期/服务依赖图联合 **46 passed**。覆盖已见挂单→撤单→重启后仍查询、漏单不归零、数量/金额倒退、订单身份变更、失败停会话，以及终态后仍阻止下一轮、删除恢复证据不能绕过阻断。全部使用模拟响应，未查询真实订单或读取凭证；未运行完整发布门、未部署。

本批完成已知订单恢复接线，成交与费用归档、逐资产现金/持仓对账仍未实现，是后续优先工作。原冻结9208995d8入口不包含新增 QueryOrders 能力，未被替换；新权限/入口仍需按独立人工流程接续。协议依据：[Query Orders Info](https://docs.kraken.com/api-reference/account-data/query-orders-info)。


## 第十批 逐笔成交与订单累计数核对

新增 `services/data/kraken_trade_query.py`，在已知订单恢复后按订单返回的成交编号查询 QueryTrades。固定 `rebase_multiplier=base`、`trades=false`，每请求最多20个ID、一次最多1000个成交/50批；账户身份一致、时间有序、全流程新鲜度限制。要求响应ID集合完整且无重复，逐筆绑定订单/产品/方向/类型，拒绝保证金成交与未来事件时间。保留十进制成交时间、数量、价格、成交额和费用；不以价格×数量替换交易所返回的成交额。

对每个订单分别求和数量/成交额/费用，精确匹配 QueryOrders 累计数，状态 `TRADE_TOTALS_MATCHED`。源端舍入或订单/成交快照不同步导致不等时仍拒绝，尚未定义金额容差，不能自动调整账本使其通过。当前产品标识须精确一致，尚未自动猜测内部/展示名称别名。

人工工具 `--track-orders` 已串入该步骤，私有只读白名单加入 QueryTrades；未提供交易写入。查询原文及归一结果留存，逐笔记录以成交ID归档，重复相同记录不新增，同ID变更拒绝覆盖。首次查询失败、缺笔或累计数不符使恢复 UNRESOLVED，采集会话停止；已持久化的订单/成交保留，重启可继续核对。

组合周期增加 `trade_reconciliation_status`；逐笔核对通过后阻断原因缩小为 `CASH_RECONCILIATION_REQUIRED`，不因此允许下一轮。`fee_asset_status=UNKNOWN`、`cash_reconciled=false` 保持：成交API中的费用合计不能证明实际扣费资产，仍缺账目流水及前后余额核对。手续费返佣等本模型不支持的表示也拒绝，不默认为零。

验证：新模型先RED（模块缺失），实现后成交/订单查询/人工工具/组合周期/依赖图联合 **46 passed**。覆盖漏笔、重复、串单、费用/金额/数量不符、未来时间、保证金成交、跨查询幂等与同ID冲突、20笔请求上限。全部模拟响应，未读真实凭证或查询私有API；未运行完整发布门、未部署。下一重点是逐资产账目与费用币种、现金/持仓余额衔接，以及受控执行的恢复闭环。

协议依据：[Query Trades Info](https://docs.kraken.com/api-reference/account-data/query-trades-info)。原冻结人工入口仍未替换，新功能未投入真实会话。


## 第十一批 账目分页与前后余额衔接

新增 `services/data/kraken_cash_reconciliation.py`。以两个稳定 base 口径账户快照为边界，读取 currency、tokenized_asset 两类全部账目（type=all，asset=all），验证范围、账户身份、每页50条/末页数量、连续offset、各类固定count、全局无重复ID及完整覆盖。窗口最长24小时、每类至多5000条；采集开始须在闭合窗口结束之后。start-exclusive/end-inclusive依官方接口语义，不把默认currency查询当成包含xStocks。

逐资产计算 `amount-fee`，核对每条账目后的余额及账户期末余额；费用保留在流水实际asset内，可处理资产内返还的负费用，不把base币费用硬扣成USD。保留账目ID/refid/type/时间便于下一步成交关联。相同资产同一时刻多条无法确定次序、账户快照采集期间有账目活动、缺页、计数变动或余额漂移均不通过。快照边界外的已查询账目保留但不计入两快照间变化。

人工工具新增 `--ledger-opening /absolute/earlier-account-snapshot.json`：本次账户采集结束后，以指定旧快照和新快照进行有限范围Ledgers查询及核对。不是自动选择期初或自动滚动现金账本；失败不伪造配平。签名白名单加入该只读端点，账户保持同一命名空间，范围/类别/分页参数固定校验；窗口及前后快照私有留存，独立锁避免并发写入。

组合周期读取 `cash-reconciliation.json`，核对账户、期末快照内容哈希和时效，显示 `BALANCE_MOVEMENTS_MATCHED` 或未解决/旧快照。该状态只证明观察到的默认钱包余额变化能由所取流水解释，未证明成交与流水逐笔关联、全钱包覆盖或执行授权；既有下一轮阻断保持。当前 `trade_linkage_verified=false`、`next_cycle_allowed=false`，不因为数学配平自动放行。

验证：新增回归先RED，账目/账户工具/组合周期/依赖图联合 **39 passed**；随后追加base资产扣费及多页正向重建两项通过（账目模块总10项）。未读取凭证、真实私有API或部署，未跑完整发布门。实际窗口边界、返佣/公司行动和真实账户数据尚需验证，成交refid关联是下一断点。

协议依据：[Get Ledgers Info](https://docs.kraken.com/api-reference/account-data/get-ledgers-info)。原冻结人工入口未替换，新账目参数尚未在真实账户运行。


## 第十二批 成交账目关联与返回下一轮规划

新增 `services/data/kraken_settlement.py` 与 `tools/kraken_settlement_review.py`，补齐“订单终态→逐笔成交→账目/余额→下一轮草案”的离线反馈链。余额衔接报告增加实际快照间的可确定时间边界，关联仅采用该范围内的交易流水。

- 要求已跟踪订单全部处于 closed/canceled/expired。逐笔成交集合与订单成交编号一致，按公开产品目录定位base/quote（xStocks显式处理`.T`），按refid连接账目。
- 每笔必须具有完整的base与quote记账，两侧带符号金额分别与成交数量/成交额一致；手续费以流水中的实际资产保存。报价资产手续费与成交费用合计核对；base资产费用尚缺成交摘要计量口径证据，明确阻断，不默认折算。
- 范围内无法关联的交易流水、非交易变动、缺腿、金额/费用不符、区间外成交或未终结订单均未解决。未覆盖的入金、收益等事件分类不能被忽略来配平。
- 恢复不是信任汇总状态：从cash-windows、order-queries、trade-queries按内容哈希加载原始证据，重新解析订单、成交并重算余额，再核对各指针与当前账户。损坏归档或重算不一致拒绝。
- 组合周期只在上述核对通过且账户/配置/行情等其他条件满足时，解除“待现金核对”阻断并重新运行现有目标金额计算。输出仍为 REVIEW_ONLY、execution_status=DISABLED；`next_review_allowed`与`next_cycle_allowed`分开，未授予实盘权限或经济权威。

验证：新关联模型先RED；关联、账目、组合周期、库存报告和依赖图联合 **41 passed**。端到端模拟场景实际经过只读查询工具与归档：部分成交后撤单→逐笔核对→前后余额→重算关联→下一轮目标金额200，随后修改账目归档验证INPUT_REJECTED且不返回旧草案。这里200为测试配置，并非用户投资金额。全部离线模拟，未真实查询、下单或部署，未跑完整发布门。

完成的是观察证据到再次规划的反馈链。仍缺受控订单构建/提交与恢复、真实账户与管理范围配置、全钱包范围确认、非交易现金流分类、已核准价格/费用口径、策略与经济证据，以及完整发布和实际运行验收，不能宣称完整自动组合管理已完成。

## 第十三批 精确订单协议离线预检

复核现有 live 管线发现请求数量使用浮点数，不能直接承载 Kraken 精确数量。新增 `live/exchange/kraken_wire.py`，只将明确的订单草案编码为离线 validate-only 请求：数量与价格保留十进制字符串，核对产品身份、在线状态、数量/价格精度、最小数量/成交额和tick；固定限价、IOC、报价币费用偏好与2至60秒期限，不接受额外杠杆参数。费用偏好不证明实际费用资产，仍以成交后的账目核对为准。

`tools/kraken_order_preflight.py` 重放新鲜公开产品证据，将草案、来源、编码请求分别绑定内容摘要，生成私有且不可覆盖的审阅文件。没有签名、凭证读取或网络调用；`venue_validated=false`、`submission_enabled=false`。nonce只是离线占位，不能当作已签发请求。未将指示性估值或目标金额自动转换为订单数量。

查询接口的 `rebase_multiplier` 不能直接移植到 AddOrder；下单单位语义、账户资金/费用绑定、人工mandate和风控、独立签发器均明确列为未完成。当前验证标记只编码 `validate=true`，没有实际请求交易所验证，更没有提交订单。协议依据：[Kraken Add Order](https://docs.kraken.com/api-reference/trading/add-order)。

验证：协议模块回归先RED；实现后协议、预检与账户工具联合26项通过，覆盖浮点拒绝、精度/金额/价格步长、身份、期限、额外字段及过期/修改来源。未部署、运行完整发布门或访问真实私有API。下一断点仍是核准单位后把目标、资金约束和受控执行连接起来，不能把协议编码当作完整执行适配器。

## 第十四批 中断刷新不能保留旧成功

断点检查复现：已有一次成功核对后，若 QueryOrders 或 QueryTrades 遇到进程中断，旧 `ORDERS_OBSERVED` 指针仍在时效范围内，可能被组合周期当成当前核对状态。账户工具现于网络查询前先原子发布带哈希的 `UNRESOLVED / REFRESH_IN_PROGRESS`，只有全部查询及成交核对完成才发布成功。既有组合周期会阻断未解决状态，旧成功归档仍保留。

恢复时合并跟踪文件和已校验恢复指针中的订单ID，避免新订单编号写入恢复文件后、跟踪文件写入前中断导致编号丢失；账户不一致或恢复摘要损坏拒绝。没有新增发单或重发行为，本修复只覆盖只读核对刷新，不能宣称已实现提交超时恢复。

验证：两个查询中断回归先复现失败；修复后增加文件写入间隙中断/无挂单重启恢复场景。账户工具与组合周期联合 **26 passed**。模拟中断后状态未解决、编号保留，续跑成功并核对摘要；未访问真实账户、部署或运行完整发布门。

## 第十五批 连续会话逐轮现金核对

原 `--ledger-opening` 只在整个账户会话结束后查询一次账目，期间组合周期无法获得对应当轮账户的现金证据。现接入 `run_session`：人工指定一次已导出的期初检查点后，每轮稳定账户采集及已启用的订单/成交恢复通过，立即查询两类账目并重算余额桥，结果随该轮事件发布；无需每轮重新执行命令。账户不稳定、订单恢复失败或现金核对失败即停，不跳过差异继续循环。

期初固定，期末逐轮刷新，窗口仍最长24小时；不自动以新快照覆盖期初，因为已跟踪成交尚需在同一窗口完成关联。跨会话可继续使用原检查点，但超过窗口须另行处理；长期检查点推进、历史订单归档和完整结算生命周期尚未实现。未把此功能表述为无限期自动管理。

现金查询前同样先发布带哈希的未解决状态，网络错误和中断不能留下旧成功；错误只记录类型，不输出传输细节。输入快照绑定账户及摘要，时间、稳定性和24小时范围在请求前校验。查询原文及每轮余额桥继续留存在原cash-windows归档；未新增签名端点或交易权限。

验证：连续核对回归先RED；账户工具、组合周期及现金模型联合 **40 passed**。实际模拟两轮读取四份账目响应、生成两个窗口归档，确认期初不变；查询失败、进程中断及过期检查点均阻断，后者不发起查询。未读取真实凭证或私有账户、部署或运行完整发布门。冻结人工入口仍未更新，这些能力尚未用于真实账户会话。

## 第十六批 无跟踪订单时的现金阻断与重算

连续会话接线检查发现：此前现金状态仅用于显示，订单ID列表非空才触发下一轮阻断。因此没有跟踪订单时，现金未解决、过期或绑定其他账户快照仍可能生成目标草案。新增现金独立阻断，不依赖订单列表；一旦组合周期读到现金证据便持久化必需标记，后续删除该证据不能退回未接线模式。未知状态同样阻断，带交易权限或权威声明的现金输入拒绝。

抽出 `replay_cash`，每次采用现金成功状态前从原始窗口归档重算余额并核对指针；不再只有订单关联分支才重算。无账目变动且余额重算一致可继续审阅；查询返回账目时必须完成成交/结算关联才解除新增阻断，不能仅靠净余额配平忽略资金事件。当前对查询范围内账目统一要求关联，未分类非交易现金流仍不能自动放行。原有完整成交关联成功路径保持通过。

验证：四类现金错误、损坏成功归档、存在交易账目但无订单关联共六个回归先失败；修复后组合周期、账户工具及结算模型联合 **44 passed**。覆盖正常无变动审阅、证据删除阻断、真实模拟归档重算及既有部分成交→撤单→对账→再规划。全部离线；未运行完整发布门、部署或访问真实账户。仍缺长期结算检查点推进与实盘受控执行。

## 第十七批 全部Kraken模块联合回归与单位协议复核

在源码 `6efd7c51a` 上一次性运行 `tests/data/test_kraken_*.py`、`tests/tools/test_kraken_*.py`、`tests/live/test_kraken_wire.py` 和服务依赖图测试，共 **169 passed in 11.28s**。覆盖目前全部Kraken专用测试文件，不是整个系统canonical发布门，也不包含真实账户或实际交易验证。前十六批不再只有各自局部通过记录；仍不能据此宣称完整自动组合管理完成。

2026-09-19重新核对官方资料：[xStocks FAQ](https://support.kraken.com/articles/xstocks-faq)说明订单数量通过乘数换算为链上数量，并给出8位数量示例；[Query Orders Info](https://docs.kraken.com/api-reference/account-data/query-orders-info)明确base为SPV token、rebased为底层股票数量；[Add Order](https://docs.kraken.com/api-reference/trading/add-order)仅将volume定义为基础资产数量，未在该字段说明与前述查询口径的精确对应，也未列出rebase_multiplier参数。FAQ示例与一般“round”描述不足以单独确定REST所有舍入行为。因此本次没有将数量语义标记为已核准，或把估值数量直接用于发送。

实际前置条件再次核查：预定只读导出位置 `quantos-dev-output/kraken-automation-20260919/account-observation/latest.json` 尚不存在；没有查询凭证或私有接口来代替人工导出。管理资产池、资金限额及实际账户证据仍待接入。下一步开发应集中结算检查点生命周期与精确执行适配；实际验收还需要账户证据、数量/费用语义确认、独立人工授权风控签发和完整发布门。现有冻结人工入口未更新，常驻系统未切换。

## 第十八批 自动建立现金起点与重启续接

只读账户工具新增 `--auto-cash`，与显式 `--ledger-opening` 互斥，并启用现有订单跟踪。第一次稳定且无挂单、无已跟踪订单的观察会私有保存为 `cash-baseline.json`；发布 `BASELINE_ONLY`，组合周期据此阻断，不能把建立起点当成已经核对资金。随后各轮使用同一起点自动查询账目与核对，重启从原文件恢复，不要求操作员手工找期初文件。初次有挂单或已跟踪订单时停止，需要先处理旧订单的结算范围，不会忽略起点以前的部分成交。

基线绑定账户、内容摘要和base数量口径；已有基线不能通过关闭自动模式或传入其他起点切换。基线删除而现金指针仍存在时拒绝重新初始化；基线损坏在读取账户之前拒绝。此为本机内容完整性和操作恢复约束，不是防御能够修改所有文件的受信代码。整个流程仍无交易写端点。

验证：自动建档/重启回归先RED；账户工具与组合周期联合 **41 passed**，覆盖首次不查询账目且不宣称完成、次次沿用同一起点核对、删除/篡改/模式切换拒绝以及有挂单不建立基线。未读取真实凭证、部署或运行完整发布门。24小时上限不变，本批不是结算后自动推进检查点或长期运行验收；已冻结人工入口也尚未替换。

## 第十九批 完整验收发现发布阻断与依赖修复

冻结源码 `5cd9d9515412fbf9e0a2754aa8d5bd8a5b0208ad` 在开发输出下独立检出运行完整 `scripts/validate.py`；原运行服务、数据库和凭证未作为夹具。日志保存在 `quantos-dev-output/kraken-automation-20260919/acceptance-5cd9d9515-results/validation.log`。旧ARM64证据及其派生评估不绑定该源码，不能通过；后端测试默认1800秒预算在约96%处超时，此前可见5个失败标记，未得到完整错误栈，不宣称均为证据失败。执行/交易/工具测试 **4469 passed, 2 skipped, 1 warning**；前端460项测试、类型检查、ESLint及生产构建通过。最终ALL报告为26项：20通过、5失败、1超时，进程exit 1；5个阶段失败为三类ARM64旧证据、供应链基线和顶层评估源码绑定。后端测试内的失败标记仍须在更长预算下取得详情，不能由阶段原因替代解释。下一完整验证需沿用历史验收的3600秒阶段预算，旧失败与超时保留。

另一个干净检出复用原生ARM64证据捕获流程，两个独立冷构建分别634.886秒、587.353秒完成。新Trivy数据库扫描运行镜像发现 AnyIO 4.14.1 的 CVE-2026-63374、CVE-2026-63349、CVE-2026-64847，分别标记CRITICAL/HIGH/MEDIUM，修复版本均为4.14.2。流程按原规则失败退出，未产生成功证据包或执行后续启动验收；拒绝报告保存在 `arm64-5cd9d9515/arm64-capture-5cd9d9515/rejected-audit-24ffab4516719284dda63b525296806c674e11013239e2fc3806dfc3eb1bf193.json`，所属临时构建资源及回环辅助已由捕获流程收尾。该镜像只包含只读Console，不能代表Kraken执行工具已部署。

核实[官方4.14.2发布说明](https://github.com/agronholm/anyio/releases/tag/4.14.2)后，需求文件升级AnyIO一项。两个架构分别保持原锁文件全部其他版本、wheel URL与hash，使用真实pip解析报告及原 `build_lock_document` 生成器重建；额外断言除了AnyIO和需求摘要，所有行与原锁文件一致。PyPI wheel SHA256为 `9f505dda5ac9f0c8309b5e8bd445a8c2bf7246f3ce950121e45ea15bc41d1494`，下载后实算匹配。生成输入、报告及重建驱动保存在 `anyio-security-fix`，不是人工改写锁文件通过检查。

使用独立 `anyio-security-venv/bin/python`，旧开发环境仍为4.14.1、新副本为4.14.2；pip check通过。Kraken全部专用测试、服务依赖图和Console接口合同联合 **257 passed, 1 skipped, 1既有弃用警告**；双架构锁文件检查通过，锁文件治理13项及Python审计证据3项通过。两次Python审计尝试因临时环境pip升级失败未返回JSON，失败日志保留；随后相同pip-audit参数的诊断运行（仅额外捕获子进程错误，不修改判断）成功，原证据构造/验证器留存69项依赖、0已知漏洞结果，`generate_python_audit_evidence.py --check`通过。

这是实际扫描阻断的依赖修复，不是发布通过。新源码仍须重新冻结、重建ARM64证据并完整验收；未部署、替换人工入口或进行真实交易。长期结算检查点、受控提交/恢复、真实账户及管理范围等原闭环缺口仍保留。
