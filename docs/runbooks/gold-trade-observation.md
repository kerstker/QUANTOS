# 黄金成交事实离线核对

当前仅接受标准化 JSON，不直接接受 Binance CSV。真实 CSV 转换需脱敏表头及样本
验证，不能靠猜测列名、数量单位或时区。输入由操作员提供，不含凭证。
此入口不联网、不修改输入、不生成 paper fills，也不写账户状态；默认只输出诊断，
显式指定 `--output-dir` 时创建新的私有离线档案。

更新：另支持保存的 Binance `userTrades` 原生 JSON 成交行，使用
`--format binance-user-trades`。这不是实时账户连接或CSV导入。
输入沿用下方标准化示例的账户/窗口头部，将schema改为
`GOLD_BINANCE_USER_TRADES_V1`，fills替换为原生响应数组（id/orderId/time、symbol、
side/positionSide、price/qty/quoteQty、commission/commissionAsset、realizedPnl、
buyer/maker；可选pair/marginAsset/baseQty）。不要放入请求头、签名或密钥。
整数编号原样保存，金额为十进制字符串；仅XAUUSDT，其他品种拒绝而非静默丢弃。
账户、环境和窗口属于操作员声明，不能凭JSON证明来源。缺分页/快照证据时仍是
不完整成交事实；不判断是否人工下单。原文件需由操作员保留，诊断输出不代替归档。

## 私有成交档案与复核

用户已确认普通USDⓈ-M账户，手机App与网页均使用。未来监控按账户事实覆盖两个
入口，不推断每笔来自哪台设备。当前工具尚不连接账户；此处操作只用于已经保存的
无凭证输入文件。不要为验证工具专门下单，也不要把真实档案提交到仓库。

```bash
/home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python \
  -m tools.gold_trade_observation --input /private/path/native-fills.json \
  --format binance-user-trades --output-dir /private/path/new-fill-archive

/home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python \
  -m tools.gold_trade_observation --verify-dir /private/path/new-fill-archive
```

输出父目录须已存在，目标档案目录必须不存在。目录0700、文件0600，写入独占，
保存input.json精确输入、fills.json标准化事实、report.json及最后写入的manifest.json；
manifest绑定文件和当前解析/CLI源码摘要。先校验后落盘，非法字段或输入不复制进
新档案。写入中断留下的部分档案不删除，也不能视为完整；修复后使用新目录。

复核只读重算输入→事实→报告，核对摘要，拒绝缺件、链接、损坏或源码版本变化；
不会执行归档代码、联网或覆写旧记录。复核成功只表示本机同版本重算一致，不能
证明交易所来源或未被整体替换；本地摘要不等于外部认证，也不证明历史完整。
源码升级后需使用对应源码版本复核，不能改写旧manifest绕过版本校验。

此批只保存单次导入；连续采集、跨批合并、订单/现金归档和持仓对账仍待实现。

在仓库根目录使用[开发环境](dgx-engine-development.md)的 Python：

```bash
/home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python \
  -m tools.gold_trade_observation --input /path/to/operator-normalized-fills.json
```

成功退出 0，JSON 报告输出到 stdout；拒绝退出 2，只输出固定原因码。成功表示
输入格式可核对，不证明交易真实性、数据完整性或账户盈利。私人文件与输出不得提交
仓库；account_ref 使用操作员本地生成的不透明 64 位十六进制别名，避免邮箱/UID。

## 标准化示例

以下完全合成，金额与时间不代表真实交易。时间为 UTC 毫秒，窗口为左闭右开。

```json
{
  "schema": "GOLD_EXTERNAL_FILLS_V1",
  "venue": "BINANCE",
  "environment": "MAINNET",
  "account_ref": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "symbol": "XAUUSDT",
  "start_ms": 1000,
  "end_ms": 2000,
  "exported_at_ms": 3000,
  "fills": [{
    "trade_id": "123",
    "order_id": "456",
    "time_ms": 1500,
    "position_side": "BOTH",
    "side": "BUY",
    "price": "4000.10",
    "quantity": "0.01",
    "commission": "0.0200005",
    "commission_asset": "USDT",
    "reported_realized_pnl": "0"
  }]
}
```

数量采用场所该合约的基础数量单位，转换器必须确认其与导出字段的关系；不能把
GC/MGC 手数或任意美元名义额直接填进 quantity。reported_realized_pnl 必须是
该成交的交易所报告 USDT 已实现盈亏，不能填账户余额变动或自行估算值。
commission 为场所报告的有符号费用，正值表示收取，负值表示返还。

数值用十进制字符串，不接受浮点、指数表示、NaN/Infinity。最多 24 位整数与
18 位小数、20,000 条输入成交、8 MB 文件；超出拒绝。相同成交编号且事实相同
去重；事实冲突拒绝。该版仅支持一个账户、一个实盘 XAUUSDT 窗口。

## 结果含义

- input_sha256 绑定原始输入字节；facts_sha256 绑定标准化去重事实及账户窗口。
- commission_by_asset 分币种汇总，不自动把 BNB 等费用换算成 USDT。
- reported_realized_pnl_usdt 仅合计输入成交的报告值，不是净收益；空输入状态为
  NO_FILL_FACTS，即使汇总值为零，也不表示零收益账户。
- 不识别 BUY/SELL 是否开平仓，不重建仓位；保留 LONG/SHORT/BOTH 分类。
- history_complete 与 source_authenticated 始终 false；net_pnl_usdt、
  account_return、strategy_pnl 均为空。该版本没有资金费、现金流水、初始头寸和
  策略关联，不能用它画完整权益曲线或计算最大回撤。

实施目标与剩余项见[黄金研究设计](../engineering/specs/2026-09-22-gold-human-quant-research.md)。

## 原始聚合成交到研究信号的离线回放

```bash
/home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python \
  -m tools.gold_signal_replay --input /path/to/normalized-public-archive.json
```

此命令的输入不是上面的私人成交文件。它需要公开 WebSocket aggTrade 原文和
本地接收时间，结构为：

```json
{
  "schema": "GOLD_SIGNAL_REPLAY_INPUT_V1",
  "start_ms": 1000,
  "rule": {"window_ms": 1000, "max_age_ms": 100, "sell_threshold": "10", "confirm_windows": 2},
  "zone": {"lower": "99", "upper": "100", "registered_at_ms": 500, "expires_at_ms": 10000},
  "decision_times_ms": [2010, 3010],
  "packets": []
}
```

这里只展示合同结构；空 packets 会返回 INPUT_INCOMPLETE，不是成功运行样例。
每条 packet 恰有 received_at_ms 和 payload；payload 为 XAUUSDT 原始 aggTrade。
上述规则数值仅用于合成测试，不是黄金交易推荐参数。

归档按接收时间排列；每窗要求窗前聚合成交及窗末以后已收到的聚合成交，两者
之间聚合编号连续。右边界消息只作覆盖证据，不进入当前窗数量。数量按每个聚合
事件 T 时间归属，不能解释为恢复了每一笔底层撮合的原始时间。空成交窗口拒绝，
不自动补成平价 K 线。该版本上限8MB、20,000包、1,000个决策时刻，适合有界回放，
并非生产长时流处理器。

decision_times_ms 是记录的实际或事先规定的研究决策时刻，必须严格递增；不能
看完结果后挑选有利时点而仍声称前向验证。未来收到的包不参与当时计算。
stdout 报告保存每窗字段、来源摘要、规则身份与决策摘要链；输入缺口保留已经
生成的前缀记录并返回退出码2。没有成交模拟、资金费、策略收益或 COMEX 信息。
程序不会自动写持久归档；需要保存报告时由操作员选择独立输出位置。

## 有界公开成交流采集

```bash
/home/kerstker/quantos-dev-output/kraken-automation-20260919/anyio-security-venv/bin/python \
  -m tools.gold_public_capture --output /task/output/new-capture.json --seconds 20
```

输出目录需预先存在，文件必须不存在。默认15秒、允许1至60秒；只有固定 Binance
XAUUSDT 公开 aggTrade 地址，无账户、认证、订单参数和常驻模式。不自动重连；
断开则保存 INTERRUPTED，序列/格式错误为 INVALID_STREAM，无数据为 NO_DATA。
崩溃可能留下不完整 JSON，不能把文件存在当成功。旧文件在联网前拒绝覆盖。

归档为 GOLD_PUBLIC_CAPTURE_V1，含 raw_frames、packets、本地起止时间、摘要及
限制。每条字符串原文保留；parsed packet 带接收毫秒。原文预算3MB、最多20,000包。
CAPTURED_PUBLIC_PREFIX 仅证明本次有界前缀通过校验，不证明完整交易历史。
将 packets 接回放时必须保留原始归档关联，另行固定规则、区域及决策时间计划，
不能根据刚采完的行情倒填区域后声称实时信号成功。

## 控制台历史成交观察

开发代码新增 `GET /api/v1/console/gold-observation`，在 Studio 的运行监控页显示
黄金观察面板。该接口只读固定的 runtime state root 下
`data/gold/observation/external-fills.json`，内容须为上文的
`GOLD_EXTERNAL_FILLS_V1` 输入文档，不是 CLI 输出报告，也不直接接受 Binance CSV。
当前没有上传接口、后台账户采集或任意路径参数；未部署或写入生产状态。

接口重新校验文档、去重，返回覆盖窗口、导出时间、成交数量、按币种费用及声明
已实现盈亏；前端只展示时间和数量，不把声明盈亏展示成账户净收益。
不返回账户标识、订单号和逐笔资料。资料校验失败为 DATA_BLOCKED；缺资料为
NO_SNAPSHOT。覆盖终点超过120秒即 STALE，近期重新导出旧数据不能刷新覆盖年龄。
REAL只表示成功读取资料，不证明交易所真实性、完整历史或实时连接。

面板中的账户连接恒为未连接，当前持仓/净收益未知，信号仅展示历史重算。
导入空成交窗口也不能证明账户无交易或空仓。刷新页面只重读资料，
不会向 Binance 请求账户或发单。

公开采集结果可由操作人员放在同目录固定文件 `public-capture.json`，必须是
`gold_public_capture.py` 生成的完整 `GOLD_PUBLIC_CAPTURE_V1` 文档。
接口重新验证原始帧、解析结果、序列、摘要和接收时间；页面显示最后记录价格、
事件时间及聚合成交数量。超过最后一笔唯一成交事件30秒为 STALE，重复旧成交或
刷新文件结束时间不能更新年龄。中断、无成交和损坏资料分别显示独立状态。
这是保存资料的观察视图，不表示持续连接或数据源已认证；账户与行情状态分别
计算，因此账户 NO_SNAPSHOT 时仍可能显示公开行情，行情损坏也不遮掉有效成交。

资金流水使用同目录固定文件 `external-cash.json`，内容为后文的
`GOLD_EXTERNAL_CASH_V1` 输入文档。资料按覆盖终点120秒判断陈旧，未来导出、
链接文件或非法结构拒绝。独立 `cash_status` 不改变成交快照状态；流水损坏不
遮掉有效成交，缺成交时仍可显示流水计数和未独立对账的资金费。
`cash_reconciliation_status` 区分未提供资料、等待有效成交、账户/时间窗不符、
金额差异、类别缺失与声明总额一致。声明总额一致不表示完整对账或资金费正确。
接口不返回账户标识、逐笔流水编号；当前没有生产资料安装或自动采集入口。

历史信号使用同目录 `signal-input.json`，内容为 `GOLD_SIGNAL_REPLAY_INPUT_V1`
原始输入，不接受保存的结果冒充重算。控制台用纯研究函数重新计算，单次最多
60个决策时间，且全部不得晚于当前读取时间；更长回放使用研究任务入口。
输入不完整时保留已完成决策数量，非法/链接/未来时间资料显示DATA_BLOCKED。
独立signal状态不改变成交快照状态；没有账户资料也可展示历史信号。
页面不展示“现在买入”，不证明事前登记、信号送达或策略盈利。input_digest绑定
规范化输入内容，不认证来源；本视图不是归档源码与账本的完整核验入口。

## 保存和复核研究信号回放

在现有回放命令上增加 `--output-dir`，目录必须尚不存在，父目录需已存在：

```bash
python -m tools.gold_signal_replay --input /task/input.json --output-dir /task/new-replay
python -m tools.gold_signal_replay --verify-dir /task/new-replay
```

此处python须为本文前述已验证解释器。归档包含原始input.json、report.json、
manifest.json、source/源码和锁文件副本、trials.db。新目录不覆盖旧尝试；不得删除
失败尝试来筛选结果。无manifest或账本仍RUNNING表示未完成，不自动续写或重启。
运行中输入缺口仍保存先前决策，终态为FAILED；非法JSON保留原文件但报告不回显
内容。输入可能包含原始资料，输出目录须按输入同样管理，不提交到仓库。

验证成功返回VERIFIED_LOCAL_REPLAY，指当前匹配源码重算一致。它可能对应一个
完整保存的FAILED回放，须同时查看replay_status。验证不联网、不执行档案代码、
不改文件；当前源码或Python版本/架构不同会拒绝。只保存选定源码及锁文件，
不等于完整可发布环境认证或交易所来源认证。

回放计算现在由纯研究模块 `services.research.gold_replay` 提供，CLI接口不变。
新归档源码清单包含该模块；重构前的旧归档须用匹配的原源码核验，不能修改
旧清单或删除源码检查来使其通过。

归档始终标为历史回放，不能作为事前预登记证明。账本PASSED仅指计算完成，
不表示盈利。该本地试验账本尚未并入全局研究注册，也不会将信号发布至实盘。

## 将自己的成交标注到研究信号

先有标准化成交文件和可复核的回放目录，再准备独立JSON标注：

```json
{
  "schema": "GOLD_TRADE_LINKS_V1",
  "fills_facts_sha256": "替换为成交观察CLI输出的facts_sha256",
  "replay_sha256": "替换为回放目录内规范report.json的SHA256",
  "links": [
    {
      "trade_id": "123",
      "decision_id": "替换为LONG_RESEARCH_SIGNAL决策的decision_id",
      "role": "ENTRY",
      "annotated_at_ms": 1790010000000
    }
  ]
}
```

摘要必须是64位小写十六进制；上述值和时间仅为格式示意，不能直接作为实际标注。
role允许ENTRY或EXIT；时间为你做标注时的UTC毫秒，不得倒填到已知成交/信号之前。
没有关联的成交无需填写，links允许空列表。一个成交不能同时给出相冲突的标注。

```bash
python -m tools.gold_trade_links --fills /task/fills.json \
  --replay-dir /task/replay --annotations /task/annotations.json
```

此处python同样使用已验证解释器。命令只读文件、重算并输出JSON，不访问账户、
不保存或修改资料、不下单。报告含成交编号，应与私人输入同样管理。
DECLARED_LINK只是声明关联，不能证明真实采用策略；其它状态保留未知和冲突。
signal之前或同毫秒的成交不确认先后关系，孤立标注不会补造成交。EXIT不证明
平仓或满足退出规则。信号未关联也不表示你拒绝过它；不计算策略归属收益。

复盘时读取 `review_counts`：分别列出各状态成交数、声明入场/退出成交数、
入场信号数、具有声明入场成交的信号数及孤立标注数。成交和标注重复不重复计数。
`signals_without_declared_entry` 保留只有退出标注的信号；它与
`signals_without_declared_trade` 含义不同。每笔 `trade_minus_signal_ms` 是交易所
成交时间减研究决策时间，可为负数；没有已识别信号时为 null，不能当作执行延迟。
缺少初始持仓和对账，`position_effect` 恒为 UNKNOWN，不从 BUY/SELL 推断开平仓。

### 控制台关联复盘

将同一份标准化成交放在固定 `external-fills.json`，把回放目录的 `input.json`
按原字节复制为 `signal-input.json`，把对应 `GOLD_TRADE_LINKS_V1` 标注放在
`trade-links.json`。这只是文件布置说明，本次开发未安装资料到生产状态。
信号输入仍受控制台60次决策上限约束。不要重新排版原输入；其精确字节摘要
参与回放结果绑定，换行或字段排列改变后旧标注会拒绝。

页面从当前输入重算信号，再验证成交/信号摘要与标注时刻，输出汇总、不返回
账户标识或逐笔编号。未关联、早于信号、同毫秒、方向冲突、未知信号和孤立
标注不会被丢掉。没有成交或信号资料时显示等待证据，绑定不符时拒绝关联，
其它有效面板不受影响。evidence_mode明确为CURRENT_SOURCE_RECOMPUTATION，
不等同于CLI的VERIFIED_LOCAL_REPLAY；页面没有核验历史归档源码和试验账本。
两种方式都只能得到事后声明关联，不能证明信号采纳、实际开平仓或收益归属。

## 研究工作台入口

开发代码中的新任务类型为gold_replay，显示名“黄金订单流信号回放”。在已由用户
部署并启动的研究工作台中，可以选择此类型并提供1–1000的实验编号。开发本身
没有启动或切换服务，也没有向运行中的生产任务队列提交研究。

输入须事先按回放输入格式放在研究服务工作目录的
data/cache/gold_replay/inputs/{编号}.json。任务参数只接受编号，不接受文件路径、
API密钥或网络地址。没有输入文件时任务显示失败，不联网补数据。

归档保存于data/cache/gold_replay/results/{编号}/。成功结果显示决策数、信号数、
输入/规则/结果摘要；不模拟成交、不计算净收益。不完整输入在结果目录保留
已知决策前缀，任务状态为error。相同请求身份可幂等重查；重新提交另一个请求
不能覆盖已使用编号，应保留失败并使用新编号准备新尝试。

任务登记只提供研究运行历史，不代表已经完成前向预登记或全局候选试验登记。

## 有界前向入口（尚未真实运行）

```bash
python -m tools.gold_forward_observe --plan /task/future-plan.json --output-dir /task/new-forward
```

使用已验证解释器。计划结构与回放输入相近：schema改为GOLD_FORWARD_PLAN_V1，
移除packets，新增整数max_lateness_ms；保留start_ms、rule、zone及decision_times_ms。
所有UTC毫秒时刻须在未来：本机实际登记时间严格早于zone.registered_at_ms，该值
不晚于start_ms。每个决策须在对应窗口结束后，包含迟到容忍后的数据年龄不得
超过rule.max_age_ms；最多60个决策，最后期限在登记后60秒内。

不提供默认价格区间、阈值或自动选参。运行前应明确研究假设和准备时间；此命令
只允许一次短时公开行情观察，不等于授权常驻服务。当前没有填写真实方案或运行
该命令，只有模拟连接测试。不要事后移动时刻重新标记旧观察为成功。

输出目录必须全新，包含registration.json、events.jsonl、decisions.jsonl及结束后的
result.json。缺result为未完成；断线、超时或窗口不完整为失败/不足。计算结果真正
可用时间另记decision_available_at_ms；超期时decision为空，诊断结果不能用来假装
按时入场。它未接V2收益评价、账户、COMEX或全局候选登记，结果不证明盈利。

## 离线复核三个市场来源的报价

```bash
python -m tools.gold_cross_market --input /task/three-source-quotes.json
```

使用本文指定解释器；命令只读文件并输出JSON，不连接行情商或账户，不写文件。
输入为一个JSON对象，必须包含且只能包含以下字段：

| 字段 | 内容 |
|---|---|
| schema | `GOLD_CROSS_MARKET_INPUT_V1` |
| external_contract | 具体GC月份，例如 `GCZ2026`；不能使用连续合约代号 |
| decision_ms | 本次历史决策的UTC毫秒 |
| max_age_ms | 允许报价事件距决策的最大毫秒数，正整数 |
| max_skew_ms | 三源事件时间的最大差，非负整数 |
| external_session_open | 布尔值；由操作人员声明，入口不验证交易日历 |
| local / external / fx | 三个报价数组，分别最多20000条；缺失来源用空数组保留缺口 |

每条报价必须恰有 `venue,instrument,unit,bid,ask,event_ms,received_ms,source_hash`。
价格为普通十进制字符串，时间为UTC毫秒整数，source_hash为来源原文的64位小写
SHA256。来源原文需另行保存，入口不联网认证摘要。三个来源身份分别为：

| 数组 | venue | instrument | unit |
|---|---|---|---|
| local | BINANCE | XAUUSDT | USDT_PER_TROY_OUNCE |
| external | COMEX | 与external_contract相同 | USD_PER_TROY_OUNCE |
| fx | FX_REFERENCE | USDTUSD | USD_PER_USDT |

使用决策时已经接收到的最新报价，拒绝陈旧、跨源时间差超限、同接收时刻冲突或
币种/月份错误。USD报价按汇率买卖两侧换算，不默认USDT等于USD。
成功退出码0仅代表 ALIGNED_QUOTES_ONLY，不能证明价差可套利、价格驱动因果或
订单流确认。缺数据、休市及无效比较退出码2并保留明确状态和输入摘要；文件格式
错误输出REJECTED，不回显原始资料。当前没有真实COMEX或汇率资料，不能用合成
测试宣称完成多市场策略验证。

## 离线资金流水与成交分项核对

```bash
python -m tools.gold_cash_observation --cash /task/cash.json --fills /task/fills.json
```

`--fills` 可省略，仅观察流水。采用本文指定解释器；不读账户、联网或写文件。
输出包含账户摘要标识，应与私人输入一同管理，不提交仓库。退出码0只表示观察
计算完成，不是完整对账成功；缺类别仍显示MISSING。发现金额不符或非法输入
退出码2，金额不符时保留分项结果而不隐藏差异。

流水采用自定义标准化合同，不是直接接受Binance导出CSV或原生API响应。
顶层必须恰有：schema=`GOLD_EXTERNAL_CASH_V1`、venue=`BINANCE`、environment=
`MAINNET`、symbol=`XAUUSDT`、account_ref、start_ms、end_ms、exported_at_ms、events。
账户摘要和时间窗约束与成交文件相同。每条events恰有：

| 字段 | 约束 |
|---|---|
| record_id | 文档内唯一的数字字符串；标准化适配器须保留原生记录身份映射，不能直接假定API某字段全局唯一 |
| time_ms | UTC毫秒，属于[start_ms,end_ms) |
| kind | COMMISSION、REALIZED_PNL、FUNDING、TRANSFER或ADJUSTMENT |
| asset | 大写币种，例如USDT或BNB |
| amount | 带符号普通十进制字符串；余额增加为正、减少为负 |
| symbol | 前三类必须为XAUUSDT；账户级TRANSFER/ADJUSTMENT必须为null |

充值、提现及账户划转统一作为TRANSFER保留有符号变化，不能当作黄金收益；其它
尚未归属黄金的账户调整使用ADJUSTMENT。不要把其它品种交易收入伪装成黄金或
账户调整。实际源格式与上述类别的映射仍须真实脱敏样本验证。

结果按类别和币种汇总，不换汇。手续费流水负值表示支出，成交文件commission
正值表示成本，核对时两者相加应为零；返佣符号相反。已实现盈亏仅核对USDT
声明总额，其它币种单独保留未比较。资金费单独列出，没有历史仓位与费率证据时
不声称资金费正确。仅相同账户、品种、环境及覆盖窗口才能交叉核对。

成交和流水中重复表达的费用/已实现盈亏不会再次相加；总额相等不证明逐笔记录
相同或历史完整，缺项不按零处理。净收益、账户权益及策略归属仍未知。
