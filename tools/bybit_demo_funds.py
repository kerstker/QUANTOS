"""Bybit Demo Trading 模拟资金申领(2026-07-05)。

`/v5/account/demo-apply-money` **只存在于 demo 主机**(api-demo.bybit.com)——它铸造的是
模拟资金,不移动任何真实资产;testnet/mainnet 上没有这个端点。用法:

    python -m tools.bybit_demo_funds                # 默认申领 5000 USDT
    python -m tools.bybit_demo_funds --amount 20000

需要 `key/bybit_demo_key.json`(在 bybit.com 主站切到 Demo Trading 后创建的 API key;
真实账户的 key 在 demo 主机上无效,反之亦然)。签名复用适配器的同一套 header 机制。
"""
from __future__ import annotations

import json
import os
import sys

from live.exchange.bybit_futures import BybitFuturesAdapter

_KEY_FILE = os.path.join("key", "bybit_demo_key.json")


def main() -> None:
    amount = "5000"
    if "--amount" in sys.argv:
        amount = sys.argv[sys.argv.index("--amount") + 1]
    float(amount)                                    # 早失败:必须是数字

    if not os.path.exists(_KEY_FILE):
        raise SystemExit(f"missing {_KEY_FILE} — create a Demo Trading API key on bybit.com "
                         f"(switch to Demo Trading first) and save it as "
                         f'{{"api_key": ..., "api_secret": ...}}')
    creds = json.load(open(_KEY_FILE, encoding="utf-8"))
    v = BybitFuturesAdapter(api_key=creds["api_key"], api_secret=creds["api_secret"],
                            base_url=BybitFuturesAdapter.DEMO_BASE)
    v.sync_clock()
    status, data = v._post_signed("/v5/account/demo-apply-money", {
        "adjustType": 0,
        "utaDemoApplyMoney": [{"coin": "USDT", "amountStr": amount}],
    })
    print(f"demo-apply-money: HTTP {status} retCode={data.get('retCode')} "
          f"retMsg={data.get('retMsg')!r}", flush=True)
    if data.get("retCode") == 0:
        acct = v.account()
        rows = ((acct.get("result") or {}).get("list")) or [{}]
        print(f"balance now: totalEquity={rows[0].get('totalEquity')} "
              f"totalAvailableBalance={rows[0].get('totalAvailableBalance')}", flush=True)


if __name__ == "__main__":
    main()
