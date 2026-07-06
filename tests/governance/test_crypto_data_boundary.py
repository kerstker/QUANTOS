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
             os.path.join(_ROOT, "tools", "binance_funding_fetch.py"),
             os.path.join(_ROOT, "tools", "binance_oi_metrics_fetch.py"),
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
             os.path.join(_ROOT, "tools", "alfred_fetch.py")]
_SERVICES = os.path.join(_ROOT, "services")


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
                    "csv",      # 纯 stdlib 文本解析(fred_fetch 解析 fredgraph CSV)——不增加网络/交易/凭证能力
                    "quantos",  # 内部契约包(FeatureValue 双时态契约,拒 lookahead)——不增加网络/交易/凭证能力
                    "services", "tools", "__future__"}
# exchange / broker / trading SDKs that must NEVER appear (dual-use → live trading)
_FORBIDDEN_SDK = {"ccxt", "binance", "coinbase", "cbpro", "krakenex", "kraken", "ib_insync",
                  "ibapi", "alpaca", "alpaca_trade_api", "oandapyV20", "fix", "quickfix", "ctp", "vnpy"}
_FORBIDDEN_TRADE_CALLS = ["create_order", "place_order", "submit_order", "cancel_order",
                          "create_market_order", "create_limit_order", "fapiPrivate", "sapiPost",
                          ".withdraw(", "create_withdrawal", "transfer("]
_NETWORK_ROOTS = {"urllib", "socket", "requests", "httpx", "aiohttp", "websocket", "websockets",
                  "ftplib", "telnetlib", "http"}


def test_tools_fetcher_only_stdlib_and_services_imports():
    for fetcher in _FETCHERS:
        roots = _import_roots(fetcher)
        assert roots <= _ALLOWED_FETCHER, f"unexpected imports in {fetcher}: {sorted(roots - _ALLOWED_FETCHER)}"
        assert not (roots & _FORBIDDEN_SDK), f"FORBIDDEN exchange/broker SDK in {fetcher}: {sorted(roots & _FORBIDDEN_SDK)}"


def test_tools_fetcher_has_no_trade_or_transfer_calls():
    for fetcher in _FETCHERS:
        src = open(fetcher, encoding="utf-8").read()
        hits = [w for w in _FORBIDDEN_TRADE_CALLS if w in src]
        assert not hits, f"FORBIDDEN trade/transfer calls in {fetcher} (read-only DATA only!): {hits}"


def test_services_tree_has_no_network_imports():
    offenders = []
    for dirpath, _dirs, files in os.walk(_SERVICES):
        for fn in files:
            if fn.endswith(".py"):
                p = os.path.join(dirpath, fn)
                bad = _import_roots(p) & _NETWORK_ROOTS
                if bad:
                    offenders.append(f"{os.path.relpath(p, _ROOT)}: {sorted(bad)}")
    assert not offenders, "network imports inside services/ break the offline-core guarantee:\n" + "\n".join(offenders)
