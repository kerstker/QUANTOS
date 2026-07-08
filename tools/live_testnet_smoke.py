"""Binance 期货测试网真实联调烟测(手动运行,一次性验证 live/exchange/binance_futures.py 对真实
测试网响应的假设——`binance_futures.py` 的单元测试全部注入假 transport,这是本仓库第一次真正
打到 testnet.binancefuture.com)。

凭证:env `BINANCE_TESTNET_API_KEY`/`BINANCE_TESTNET_API_SECRET`,或 gitignored
`key/binance_test_key.json`({"api_key": "...", "api_secret": "..."})——绝不打印密钥内容,
只用于构造签名请求。

用法:
  python -m tools.live_testnet_smoke                只读步骤(零风险,验证连接/签名/解析)
  python -m tools.live_testnet_smoke --place-order   额外在只读步骤后下一笔最小 MARKET 单
                                                     (测试网假钱,零真实资本)

网络/墙钟只在此(out-of-gate,与其它 tools/ 驱动器同一惯例);live/exchange/binance_futures.py
本身零网络逻辑改动,这里只是调用它。
"""
from __future__ import annotations

import json
import os
import sys
import time

from live.contracts import LiveOrderRequest
from live.exchange.binance_futures import BinanceFuturesAdapter
from quantos.contracts.trading import OrderSide

_KEY_FILE = os.path.join("key", "binance_test_key.json")


def _load_credentials():
    api_key = os.environ.get("BINANCE_TESTNET_API_KEY")
    api_secret = os.environ.get("BINANCE_TESTNET_API_SECRET")
    if api_key and api_secret:
        return api_key.strip(), api_secret.strip()
    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        k, s = data.get("api_key"), data.get("api_secret")
        if k and s:
            return k.strip(), s.strip()
    raise RuntimeError(
        "Binance testnet credentials not found "
        "(set $BINANCE_TESTNET_API_KEY/$BINANCE_TESTNET_API_SECRET, or key/binance_test_key.json)")


def _step(label, fn):
    print(f"\n--- {label} ---", flush=True)
    try:
        result = fn()
        print(f"OK: {result}", flush=True)
        return True, result
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", flush=True)
        return False, None


def main() -> None:
    api_key, api_secret = _load_credentials()
    client = BinanceFuturesAdapter(api_key=api_key, api_secret=api_secret,
                                   base_url=BinanceFuturesAdapter.TESTNET_BASE)
    print(f"venue_mode={client.venue_mode}")

    ok, filters = _step("exchange_filters (公开,不签名)", client.exchange_filters)
    if ok:
        print(f"  {len(filters)} 个 USDT-M 符号;BTC={filters.get('BTC')}", flush=True)

    ok, acct = _step("account (签名)", client.account)
    if ok:
        print(f"  keys={sorted(acct.keys())}", flush=True)
        print(f"  totalWalletBalance={acct.get('totalWalletBalance')} "
              f"availableBalance={acct.get('availableBalance')}", flush=True)

    ok, positions = _step("positions (签名,规范化)", client.positions)

    _step("get_order(不存在的单) -> 期望 None",
         lambda: client.get_order("BTC", "quantos-smoke-ghost-order"))

    if "--place-order" not in sys.argv:
        print("\n(只读步骤完成;加 --place-order 额外测试下单)", flush=True)
        return

    if not filters or "BTC" not in filters:
        print("\nBTC 不在 exchange_filters 里,跳过下单测试", flush=True)
        return
    btc = filters["BTC"]
    # min_qty(0.0001 BTC)本身在真实价位下大概率低于 min_notional($50)——20x 安全边际,
    # 覆盖任何合理 BTC 价格区间(测试网假钱,数量本身不影响任何真实风险)。
    qty = round(max(btc.min_qty * 20, btc.step_size), 8)
    cid = f"quantos-smoke-{int(time.time())}"

    def _place():
        order = LiveOrderRequest(instrument_id="BTC", side=OrderSide.BUY, quantity=qty,
                                 client_order_id=cid)
        return client.place_order(order)

    ok, result = _step(f"place_order(BUY {qty} BTC, cid={cid})", _place)
    if ok:
        print(f"  venue_order_id={result.venue_order_id} status={result.status} "
              f"filled_qty={result.filled_qty} avg_price={result.avg_price}", flush=True)
        _step("get_order(核对刚提交的单)", lambda: client.get_order("BTC", cid))


if __name__ == "__main__":
    main()
