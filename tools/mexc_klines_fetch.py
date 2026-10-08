"""MEXC 公开行情抓取器(门外、只读、无 key)—— MEXC Spot API v3 是 Binance 兼容的公开
klines 接口(`https://api.mexc.com/api/v3/klines`),无需 key、无账户、无任何下单能力。
本模块是 **DATA ONLY**:stdlib urllib GET 公开行情(复用共享 `_http_get_json`),不 import
任何交易所 SDK(ccxt / python-binance 等),不做 order / account / withdraw 调用。L2 安全
不变量约束的是**交易能力**(下单、凭证、券商连接),读公开价格不触碰——治理边界测试钉死的
正是这一点。网络只活在这里(门外);研究核心保持离线/确定。这里没有也不应该有交易函数。

MEXC 与 Binance 的两个工程差异(诚实记录):
1. MEXC klines 对 `startTime` 支持不稳(部分 symbol 直接忽略)——分页只用 `endTime`
   从最近向最早**向后**翻页(与 binance_klines_fetch 同法),每页 limit=1000,按
   openTime 对齐 bucket 去重(与 binance 同一 event_time 口径);绝不依赖 startTime。
2. MEXC **没有 spot testnet**——所以这里只产出研究/干跑用的行情数据;真钱路径只能是
   干跑/paper + 人工 mandate 下的小额,不在这个文件里。

落库:PIT sqlite `data/cache/crypto_mexc.db`(key='BTC'),经
`services/data/market_provider.py::ingest_provider_rows` 幂等写入(主键去重 +
未来数据守卫)。`--out` 可落 JSON 快照(行数/起止/来源/抓取时间戳)。
"""
from __future__ import annotations

import argparse
import json
import os
import time

from services.data.adapters import (SourceSpec, VolumeSemantics, VolumeUnit,
                                    normalize_records)
from services.data.raw_archive import (DEFAULT_RAW_ARCHIVE,
                                       archive_json_response)
from tools.cache_io import atomic_write_json
from tools.coingecko_fetch import _http_get_json

_HOST = "https://api.mexc.com"
_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000
INTERVALS_MS = {"1d": _DAY_MS, "1h": _HOUR_MS}
DEFAULT_DB = os.path.join("data", "cache", "crypto_mexc.db")
DEFAULT_SNAPSHOT = os.path.join("data", "cache", "mexc_btc_klines.json")
SOURCE_NAME = "MEXC public spot klines (https://api.mexc.com/api/v3/klines)"

MEXC_SPEC = SourceSpec(
    name="mexc-klines", asset_class="CRYPTO", time_unit="ms",
    field_map={"instrument_id": "sym", "event_time": "ts", "open": "o", "high": "h",
               "low": "l", "close": "c", "volume": "v"},
    knowledge_lag=0,
    volume_unit=VolumeUnit.BASE_ASSET,          # MEXC klines volume = 标的(base)成交量/根
    volume_semantics=VolumeSemantics.PER_BAR,
)


class MexcFetchError(RuntimeError):
    """MEXC 公共接口返回错误(JSON {"code","msg"})或响应不是 klines 数组。"""


def _get(path: str, http_get=_http_get_json, source_sink=None):
    source_uri = _HOST + path
    payload = http_get(source_uri, {"accept": "application/json"})
    if source_sink is not None:
        source_sink.append(source_uri)
    if isinstance(payload, dict) and "code" in payload:      # MEXC 错误体
        raise MexcFetchError(f"MEXC error {payload.get('code')}: {payload.get('msg')}")
    return payload


def fetch_klines(symbol: str = "BTCUSDT", *, interval: str = "1d", limit: int = 1000,
                 end_ms=None, http_get=_http_get_json, source_sink=None):
    """GET /api/v3/klines。只用 endTime 分页(startTime 在 MEXC 上不可靠,见模块 docstring)。"""
    if interval not in INTERVALS_MS:
        raise ValueError(f"unsupported interval {interval!r} (expected {sorted(INTERVALS_MS)})")
    path = f"/api/v3/klines?symbol={symbol}&interval={interval}&limit={int(limit)}"
    if end_ms is not None:
        path += f"&endTime={int(end_ms)}"
    return _get(path, http_get, source_sink=source_sink)


def parse_klines(batch, *, instrument_id: str, interval_ms: int = _DAY_MS,
                 diagnostics=None) -> dict:
    """纯函数:klines 数组 -> {bucket_ms: row}。kline = [openTime,o,h,l,c,v,closeTime,...];
    按 **openTime** 对齐 interval bucket 去重(与 tools/binance_klines_fetch 同一口径——
    event_time = bar 的开盘日界,不是收盘界;同一 bucket 后到者覆盖,重复计数进 diagnostics)。"""
    out = {}
    rejected = 0
    duplicates = 0
    if not isinstance(batch, list):
        if diagnostics is not None:
            diagnostics.update({"status": "REJECTED", "input_rows": 0, "accepted_rows": 0,
                                "rejected_rows": 0, "duplicate_rows": 0})
        return out
    for k in batch:
        if not (isinstance(k, (list, tuple)) and len(k) >= 6):
            rejected += 1
            continue
        try:
            open_ts = int(k[0])
            o, h, low, c, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
        except (TypeError, ValueError):
            rejected += 1
            continue
        if c <= 0 or o <= 0:
            rejected += 1
            continue
        bucket = (open_ts // interval_ms) * interval_ms
        if bucket in out:
            duplicates += 1
        out[bucket] = {"sym": instrument_id, "ts": bucket, "o": o, "h": h, "l": low,
                       "c": c, "v": v}
    if diagnostics is not None:
        diagnostics.update({"status": "ACCEPTED", "input_rows": len(batch),
                            "accepted_rows": len(out), "rejected_rows": rejected,
                            "duplicate_rows": duplicates})
    return out


def fetch_rows(ticker: str = "BTC", *, interval: str = "1d", bars: int = 2000,
               http_get=_http_get_json, now_ms=None, max_pages: int = 10,
               sleep: float = 0.25, archive_root=None, observed_at_ms=None) -> list:
    """用 endTime 向后翻页抓全历史 klines(limit=1000/页),对齐 UTC interval 网格,
    丢掉未收盘的当根(无前视)。返回 SourceSpec 规范化行。无 key。fail-soft:pair 不存在/网络
    失败由调用方决定(抛异常,供刷新器 fail-soft 记录)。"""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if archive_root is not None and observed_at_ms is None:
        raise ValueError("observed_at_ms is required when archive_root is set")
    interval_ms = INTERVALS_MS[interval]
    symbol = ticker.upper() + "USDT"
    by_bucket, end = {}, now_ms
    for _ in range(max_pages):                       # 日线 10*1000 天 ≫ BTC 全历史
        source_uris = []
        batch = fetch_klines(symbol, interval=interval, limit=1000, end_ms=end,
                             http_get=http_get, source_sink=source_uris)
        diagnostics = {}
        parsed = parse_klines(batch, instrument_id=ticker, interval_ms=interval_ms,
                              diagnostics=diagnostics)
        overlap = len(set(by_bucket).intersection(parsed))
        if overlap:
            diagnostics["duplicate_rows"] += overlap
            diagnostics["accepted_rows"] -= overlap
        if archive_root is not None:
            archive_json_response(
                root=archive_root, payload=batch, source_uri=source_uris[-1],
                provider="MEXC public spot klines", dataset_id=f"spot_klines_{interval}",
                instrument_id=ticker, observed_at_ms=int(observed_at_ms),
                parse_diagnostics=diagnostics,
            )
        if not isinstance(batch, list) or not batch:
            break
        by_bucket.update(parsed)
        if len(batch) < 1000 or len(by_bucket) >= bars:
            break                                      # 已到历史起点 / 抓够了
        end = int(batch[0][0]) - 1                     # 下一页止于本页最老一根之前
        if sleep:
            time.sleep(sleep)
    current = (now_ms // interval_ms) * interval_ms
    raw = [by_bucket[b] for b in sorted(by_bucket) if b < current]   # 丢未收盘当根(无前视)
    return normalize_records(raw[-bars:], MEXC_SPEC)


def fetch_daily_rows(ticker: str = "BTC", *, days: int = 2000, http_get=_http_get_json,
                     now_ms=None, archive_root=None, observed_at_ms=None) -> list:
    """与 tools/binance_klines_fetch.fetch_daily_rows 同签名 —— live_data_refresh 的第 5 个
    幂等 fetcher 直接复用这个入口。"""
    return fetch_rows(ticker, interval="1d", bars=days, http_get=http_get, now_ms=now_ms,
                      archive_root=archive_root, observed_at_ms=observed_at_ms)


def build_snapshot(rows, *, ticker: str, interval: str, fetched_at_ms: int,
                   db_path=None, dataset_id=None, bars_ingested=None) -> dict:
    """纯函数:抓取结果 -> 快照 dict(行数/起止/来源/抓取时间戳)。无行时 span 为 None(诚实)。"""
    def _d(ms):
        return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))
    return {
        "schema_version": 1,
        "source": SOURCE_NAME,
        "provenance": "REAL_MEXC_PUBLIC_KLINES",
        "ticker": ticker,
        "symbol": ticker.upper() + "USDT",
        "interval": interval,
        "rows": len(rows),
        "first_event_time": rows[0]["event_time"] if rows else None,
        "last_event_time": rows[-1]["event_time"] if rows else None,
        "first_date": _d(rows[0]["event_time"]) if rows else None,
        "last_date": _d(rows[-1]["event_time"]) if rows else None,
        "fetched_at_ms": int(fetched_at_ms),
        "fetched_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(fetched_at_ms / 1000)),
        "db_path": db_path,
        "dataset_id": dataset_id,
        "bars_ingested": bars_ingested,
    }


def run(argv=None) -> dict:
    ap = argparse.ArgumentParser(description="MEXC 公开 klines 抓取(无 key,只读)→ PIT 库 + 快照")
    ap.add_argument("--ticker", default="BTC")
    ap.add_argument("--interval", default="1d", choices=sorted(INTERVALS_MS))
    ap.add_argument("--days", type=int, default=2000, help="日线回看根数(1h 时视为小时数)")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument(
        "--out",
        default=DEFAULT_SNAPSHOT,
        help=f"快照 JSON 路径(默认 {DEFAULT_SNAPSHOT})",
    )
    ap.add_argument("--no-ingest", action="store_true", help="只抓+快照,不落 PIT 库")
    args = ap.parse_args(argv)

    from services.data.persistent_store import SqlitePITStore
    from services.data.market_provider import ingest_provider_rows

    interval_ms = INTERVALS_MS[args.interval]
    now_ms = int(time.time() * 1000)
    os.makedirs(os.path.dirname(args.db) or ".", exist_ok=True)
    rows = fetch_rows(args.ticker, interval=args.interval, bars=args.days, now_ms=now_ms,
                      archive_root=DEFAULT_RAW_ARCHIVE, observed_at_ms=now_ms)

    bars_ingested = None
    dataset_id = f"mexc-{args.ticker}" + ("" if args.interval == "1d" else f"-{args.interval}")
    if not args.no_ingest:
        store = SqlitePITStore(args.db, create=True)
        rep = ingest_provider_rows(
            rows, store, dataset_id=dataset_id, now=now_ms, time_unit="ms", strict=True,
            expected_interval=interval_ms,
            expected_end=(now_ms // interval_ms) * interval_ms - interval_ms,
        )
        bars_ingested = rep.bars_ingested

    snap = build_snapshot(rows, ticker=args.ticker, interval=args.interval,
                          fetched_at_ms=now_ms, db_path=None if args.no_ingest else args.db,
                          dataset_id=None if args.no_ingest else dataset_id,
                          bars_ingested=bars_ingested)
    if args.out:
        atomic_write_json(args.out, snap, indent=2)
    span = f"{snap['first_date']} .. {snap['last_date']}" if rows else "—"
    print(f"{snap['symbol']} {args.interval}: {len(rows)} bars [{span}]"
          + (f", ingested {bars_ingested} -> {args.db}" if bars_ingested is not None else "")
          + (f", snapshot -> {args.out}" if args.out else ""), flush=True)
    return snap


def main(argv=None) -> int:
    """Run the MEXC refresh CLI and return an unambiguous process exit code."""
    run(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
