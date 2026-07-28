"""治理:机器强制加密数据集成的安全边界(对抗审查 P2)。

tools/ 在治理扫描器作用域之外(故意隔离网络+密钥),所以这里用 AST 直接钉住两条不变量:
1) 门禁外抓取器只含 stdlib + services,无任何交易所/券商 SDK、无下单/转账调用;
2) services/ 整棵树无网络 import —— 研究核心的离线确定性由机器强制,而非仅约定。
"""
import ast
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# every network-bearing out-of-gate fetcher (add new ones here so the boundary covers them)
_FETCHERS = [os.path.join(_ROOT, "tools", "coingecko_fetch.py"),
             os.path.join(_ROOT, "tools", "coingecko_volume_fetch.py"),
             os.path.join(_ROOT, "tools", "cryptocompare_fetch.py"),
             os.path.join(_ROOT, "tools", "binance_klines_fetch.py"),
             os.path.join(_ROOT, "tools", "binance_klines_1h_fetch.py"),   # 1H 小时线抓取器(2026-07-07)
             os.path.join(_ROOT, "tools", "binance_klines_1m_fetch.py"),   # 1m 分钟线抓取器(2026-07-09)
             os.path.join(_ROOT, "tools", "binance_perp_mark_fetch.py"),
             os.path.join(_ROOT, "tools", "binance_funding_fetch.py"),
             os.path.join(_ROOT, "tools", "binance_oi_metrics_fetch.py"),
             os.path.join(_ROOT, "tools", "binance_realtime_market.py"),
             os.path.join(_ROOT, "tools", "binance_realtime_stream.py"),
             # REST gap-fill(2026-07-26,消除 Data Vision 发布滞后的每日死窗):fapi 公共只读
             # markPriceKlines + futures/data 指标族,无 key、绝不下单——同一条边界钉住它。
             os.path.join(_ROOT, "tools", "binance_rest_gap_fill.py"),
             os.path.join(_ROOT, "tools", "crypto_sentiment_fetch.py"),
             os.path.join(_ROOT, "tools", "frankfurter_fetch.py"),
             os.path.join(_ROOT, "tools", "yahoo_equity_fetch.py"),
             # Stage 0 (实盘路线) 生产化刷新/前向跟踪/告警——同样的只读数据边界钉住它们
             os.path.join(_ROOT, "tools", "live_data_refresh.py"),
             os.path.join(_ROOT, "tools", "forward_track_advance.py"),
             os.path.join(_ROOT, "tools", "alert_sink.py"),
             # 宏观数据抓取器:fred_fetch 是补钉的既有缺口(带网络却一直不在名单里,2026-07-06 核查
             # 发现);alfred_fetch 是 vintage 抓取器(api.stlouisfed.org,key 只走 env / gitignored
             # key/ 目录,错误路径对 URL 脱敏)——同一条只读数据边界钉住两者
             os.path.join(_ROOT, "tools", "fred_fetch.py"),
             os.path.join(_ROOT, "tools", "alfred_fetch.py"),
             # 密钥权限校验工具(2026-07-08,安全 P0):独立、手动调用,查询交易所 mainnet 账户的
             # API key 权限位(提现是否禁用/IP 是否受限)。只读诊断,不接线(见其模块 docstring)——
             # 但它确实发起真实签名网络请求,同一条只读数据边界必须钉住它。
             os.path.join(_ROOT, "tools", "verify_key_permissions.py"),
             # Binance 期货 mainnet 只读首验工具(2026-07,镜像 mexc_readonly_smoke):人工
             # 执行,4 个只读 GET(time/exchangeInfo/premiumIndex/account),绝不下单——但它
             # 确实发起真实签名网络请求,同一条只读数据边界必须钉住它。
             os.path.join(_ROOT, "tools", "binance_readonly_smoke.py"),
             # Deribit 公开波动率/衍生品抓取器(2026-07-24,第四数据类 options/vol):
             # DVOL 日频 + 永续/到期期货日线 + 期权 book 快照,公共端点无 key 只读,
             # 绝不下单——同一条只读数据边界钉住它。
             os.path.join(_ROOT, "tools", "deribit_fetch.py"),
             # 补充数据维度(2026-07-25):近期强平事件 + Binance/Bybit 同步公开盘口。
             os.path.join(_ROOT, "tools", "binance_liquidations_fetch.py"),
             os.path.join(_ROOT, "tools", "cross_exchange_snapshot.py"),
             os.path.join(_ROOT, "tools", "data_breadth_refresh.py"),
             os.path.join(_ROOT, "tools", "data_channel_inventory.py"),
             # Bybit 公开 linear perp 历史抓取器(2026-07-25,W2 cross-venue 簇 Phase 1):
             # 日线 klines + funding history,公共端点无 key 只读,绝不下单——同一条只读
             # 数据边界钉住它。
             os.path.join(_ROOT, "tools", "bybit_klines_fetch.py"),
             # MEXC 集成两个门外工具(2026-07-27,Phase 8 M1 补登记——审计发现的既有缺口,
             # binance_readonly_smoke 在册而这两个不在):klines 公开行情抓取器(无 key 只读)
             # 与 mainnet 只读首验(人工执行,3 个只读 GET,绝不下单)——同一条只读数据边界钉住。
             os.path.join(_ROOT, "tools", "mexc_klines_fetch.py"),
             os.path.join(_ROOT, "tools", "mexc_readonly_smoke.py"),
             # SHADOW 轨道驱动器(2026-07-27,Phase 7 S3):网络面 = venue 适配器的
             # 无签名公共行情 GET(depth);空凭证构造适配器、结构性只调无签名方法、
             # 绝不下单——同一条只读数据边界钉住它。
             os.path.join(_ROOT, "tools", "shadow_track.py")]
_SERVICES = os.path.join(_ROOT, "services")
_QUANTOS = os.path.join(_ROOT, "quantos")


def _import_roots(path: str) -> set:
    tree = ast.parse(open(path, encoding="utf-8").read())
    roots = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                roots.add(a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            roots.add(n.module.split(".")[0])
    return roots


# stdlib + internal (services/tools) allowed in the network-bearing fetchers; nothing else.
# `io`/`zipfile` are pure-stdlib in-memory archive parsing (the Data Vision metrics archive ships ZIPs);
# `concurrent` is the stdlib thread pool that only OVERLAPS the high-latency archive GETs — none of these
# add network/trade/credential capability, so they do not weaken the boundary.
_ALLOWED_FETCHER = {"json", "os", "time", "sys", "urllib", "io", "zipfile", "concurrent",
                    "sqlite3",  # 纯 stdlib 本地存储(增量刷新读水位/PIT 库)——不增加网络/交易/凭证能力
                    "services", "tools", "__future__"}
# per-file 额外允许项(2026-07-06 对抗式复审 CONFIRMED 修复):此前 "csv"/"quantos" 加进
# 上面那个*全局*集合,意味着其余 12 个从不 import 它们的抓取器也被这条测试"允许"这么做——
# 测试无法区分"alfred_fetch 需要 quantos"与"任何抓取器都可以 import quantos"。按文件名精确
# 授权,新增抓取器需要新 import 时必须显式在这里加一行,而不是搭其他抓取器已获批准的便车。
_EXTRA_ALLOWED_BY_FILE = {
    # 纯 stdlib 有限数校验；只用于拒绝 NaN/Inf 源观测，不增加网络/交易/凭证能力。
    "coingecko_fetch.py": {"math"},
    "binance_funding_fetch.py": {"math"},
    "binance_perp_mark_fetch.py": {"math"},
    "binance_realtime_market.py": {
        "collections", "math", "pathlib", "quantos", "typing"
    },
    "binance_realtime_stream.py": {
        "argparse", "asyncio", "collections", "math", "quantos", "typing",
        "websockets",
    },
    # REST gap-fill:math 只做有限数校验(拒 NaN/Inf 源观测),不增加网络/交易/凭证能力。
    "binance_rest_gap_fill.py": {"math"},
    # Process isolation is the hard wall-clock budget: each source is spawned, then the parent
    # terminates it on deadline. Both imports are stdlib and add no network/trade capability.
    "live_data_refresh.py": {"math", "multiprocessing", "queue"},
    # Local artifact paths only; pathlib is stdlib and adds no network/trade capability.
    "forward_track_advance.py": {"pathlib"},
    # 纯 stdlib 文本解析(解析 fredgraph CSV)+ 内部契约包(FeatureValue 双时态契约,拒 lookahead)
    "fred_fetch.py": {"csv", "quantos"},
    "alfred_fetch.py": {"quantos"},  # 同一份内部契约包,vintage 抓取器同样需要
    # 密钥权限校验工具:HMAC 签名(hashlib/hmac,与既有 exchange adapter 同一算法的纯函数镜像)+
    # argparse(手动 CLI)+ dataclasses/enum/typing(结构化 SAFE/UNSAFE/UNKNOWN 结果)+ `live`
    # (复用 live/exchange/{binance,bybit}_futures.py 与 retry.py 里已导出的 ungated 辅助函数/常量,
    # 见模块 docstring"设计决定"——不实例化任何 gated 类,不碰任何 LIVE 安全旗标)。
    "verify_key_permissions.py": {"hashlib", "hmac", "argparse", "dataclasses", "enum", "typing", "live"},
    # Binance 只读首验:typing(注入签名)+ `live`(复用 binance_futures.py 的模块级
    # transport/常量与 retry——不实例化任何 gated 类,不碰任何 LIVE 安全旗标)。
    "binance_readonly_smoke.py": {"typing", "live"},
    # Deribit 抓取器:math(纯 stdlib 有限数校验,拒 NaN/Inf 源观测)+ argparse(CLI)——
    # 均不增加网络/交易/凭证能力。
    "deribit_fetch.py": {"math", "argparse"},
    # 补充公开行情/强平解析器:math 只做有限数校验，不增加交易能力。
    "binance_liquidations_fetch.py": {"math"},
    "cross_exchange_snapshot.py": {"math"},
    # Bybit 抓取器:math(纯 stdlib 有限数校验,拒 NaN/Inf 源观测)+ argparse(CLI)——
    # 均不增加网络/交易/凭证能力。
    "bybit_klines_fetch.py": {"math", "argparse"},
    # MEXC 公开 klines 抓取器:argparse(CLI)——不增加网络/交易/凭证能力(网络复用
    # tools/coingecko_fetch._http_get_json 的 urllib,已在 _ALLOWED_FETCHER 内)。
    "mexc_klines_fetch.py": {"argparse"},
    # MEXC 只读首验:argparse/typing(CLI 与注入签名)——签名 HMAC 走
    # tools/mexc_credentials 内部(同 verify_key_permissions 的 ungated 纯函数镜像设计),
    # 不实例化任何 gated 类。
    "mexc_readonly_smoke.py": {"argparse", "typing"},
    # SHADOW 轨道驱动器:math(有限数校验)+ argparse(CLI)+ typing(注入签名)+
    # `live`(live/execution/shadow 比较核 + shadow_store 账本 + live/exchange venue
    # 适配器——与 verify_key_permissions/binance_readonly_smoke 同款只读复用,
    # 空凭证构造,不碰任何 LIVE 安全旗标)。
    "shadow_track.py": {"math", "argparse", "typing", "live"},
}
# exchange / broker / trading SDKs that must NEVER appear (dual-use → live trading)
_FORBIDDEN_SDK = {"ccxt", "binance", "coinbase", "cbpro", "krakenex", "kraken", "ib_insync",
                  "ibapi", "alpaca", "alpaca_trade_api", "oandapyV20", "fix", "quickfix", "ctp", "vnpy"}
_FORBIDDEN_TRADE_CALLS = ["create_order", "place_order", "submit_order", "cancel_order",
                          "create_market_order", "create_limit_order", "fapiPrivate", "sapiPost",
                          ".withdraw(", "create_withdrawal", "transfer("]
_NETWORK_ROOTS = {"urllib", "socket", "requests", "httpx", "aiohttp", "websocket", "websockets",
                  "ftplib", "telnetlib", "http",
                  # 消息/异步网络/DB 客户端(审计 P3-1):同样赋予网络能力,必须一并挡在离线核之外。
                  # 此前缺席,使一个 aiokafka import 逃过了'services/ 零网络 import'守卫。
                  "aiokafka", "kafka", "confluent_kafka", "aio_pika", "pika", "nats",
                  "redis", "aioredis", "pymongo", "motor", "psycopg2", "asyncpg", "zmq", "pyzmq"}

# 已隔离的非功能模块(CLAUDE.md 明列 NON-FUNCTIONAL,被 services/risk/pre_trade 取代,不接线):
# 其 aiokafka import 正是它被隔离的原因。守卫扩展后会命中它,故在此显式豁免——豁免是精确到文件、
# 有据可查的,任何**别处**新增的同类 import 仍会被 test_services_tree_has_no_network_imports 挡下。
_QUARANTINED_NONFUNCTIONAL = {
    os.path.normpath(os.path.join(_SERVICES, "trading", "pre_trade_risk", "main.py")),
}


def test_tools_fetcher_only_stdlib_and_services_imports():
    for fetcher in _FETCHERS:
        roots = _import_roots(fetcher)
        allowed = _ALLOWED_FETCHER | _EXTRA_ALLOWED_BY_FILE.get(os.path.basename(fetcher), set())
        assert roots <= allowed, f"unexpected imports in {fetcher}: {sorted(roots - allowed)}"
        assert not (roots & _FORBIDDEN_SDK), f"FORBIDDEN exchange/broker SDK in {fetcher}: {sorted(roots & _FORBIDDEN_SDK)}"


def test_tools_fetcher_has_no_trade_or_transfer_calls():
    for fetcher in _FETCHERS:
        src = open(fetcher, encoding="utf-8").read()
        hits = [w for w in _FORBIDDEN_TRADE_CALLS if w in src]
        assert not hits, f"FORBIDDEN trade/transfer calls in {fetcher} (read-only DATA only!): {hits}"


def test_network_roots_cover_message_and_async_clients():
    """审计 P3-1:`_NETWORK_ROOTS` 此前只列 http/socket 系,不含 aiokafka/kafka/redis/pika 等
    消息/异步网络客户端——它们能悄悄进入 services/ 逃过离线核守卫(文档 §8 却称'零网络 import
    机器钉死')。唯一的现存使用者正是被隔离的 pre_trade_risk/main.py(其 aiokafka import 恰是
    它被隔离的原因);守卫必须能识别这类 import,才能挡住任何在别处新增的同类 import。"""
    for client in ("aiokafka", "kafka", "redis", "pika", "pymongo"):
        assert client in _NETWORK_ROOTS
    quarantined = os.path.join(_SERVICES, "trading", "pre_trade_risk", "main.py")
    assert _import_roots(quarantined) & _NETWORK_ROOTS, \
        "守卫必须识别被隔离模块的网络/消息 import(否则同类 import 在别处新增也检不出)"


def test_services_tree_has_no_network_imports():
    offenders = []
    for dirpath, _dirs, files in os.walk(_SERVICES):
        for fn in files:
            if fn.endswith(".py"):
                p = os.path.join(dirpath, fn)
                if os.path.normpath(p) in _QUARANTINED_NONFUNCTIONAL:
                    continue        # 已隔离的非功能模块(CLAUDE.md 明列),其网络 import 正是隔离原因
                bad = _import_roots(p) & _NETWORK_ROOTS
                if bad:
                    offenders.append(f"{os.path.relpath(p, _ROOT)}: {sorted(bad)}")
    assert not offenders, "network imports inside services/ break the offline-core guarantee:\n" + "\n".join(offenders)


def test_quantos_tree_has_no_network_imports():
    # 2026-07-06 对抗式复审 CONFIRMED 修复:`_ALLOWED_FETCHER`/`_EXTRA_ALLOWED_BY_FILE` 只查
    # 每个抓取器*自己*的直接 import——一个抓取器 `from quantos.x import y` 时,若 quantos/x.py
    # 内部本身 import 了网络库,这条边界测试看不到(它只看抓取器文件的 AST 根,不看被 import
    # 模块的内容)。quantos/ 是所有抓取器都可能间接触达的内部契约包,必须像 services/ 一样
    # 整棵树钉死零网络 import——这是那道"抓取器直接 import 检查"的必要补充,不是重复。
    offenders = []
    for dirpath, _dirs, files in os.walk(_QUANTOS):
        for fn in files:
            if fn.endswith(".py"):
                p = os.path.join(dirpath, fn)
                bad = _import_roots(p) & _NETWORK_ROOTS
                if bad:
                    offenders.append(f"{os.path.relpath(p, _ROOT)}: {sorted(bad)}")
    assert not offenders, ("network imports inside quantos/ would let any fetcher gain network "
                           "capability transitively via `from quantos.x import y` "
                           "(the per-fetcher allowlist only sees direct imports):\n"
                           + "\n".join(offenders))
