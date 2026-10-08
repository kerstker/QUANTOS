"""Bybit 公开行情抓取器(门外、只读、无 key)—— Bybit V5 公共接口
(`https://api.bybit.com/v5/market/kline` + `/v5/market/funding/history`),无需 key、无账户、
无任何下单能力。本模块是 **DATA ONLY**:stdlib urllib GET 公开行情(复用共享 `_http_get_json`),
不 import 任何交易所 SDK(ccxt / bybit-sdk 等),不做 order / account / withdraw 调用。L2 安全
不变量约束的是**交易能力**(下单、凭证、券商连接),读公开价格不触碰——治理边界测试钉死的正是
这一点。网络只活在这里(门外);研究核心保持离线/确定。这里没有也不应该有交易函数。

用途(2026-07-25,Profit Maximization W2 cross-venue 簇 Phase 1):跨所偏离/价格发现研究需要
**第二 venue 的历史日线与资金费**——Binance 历史已在 crypto_funding.db,本抓取器补齐 Bybit 一侧。
Phase 0 实测深度(2026-07-25,见 evidence/cross_venue_research.json phase0 块):
- kline(category=linear, interval=D):BTCUSDT 2020-03-25 起;24 个 OI 大币全部有历史,最浅
  APT 2022-10-19(~3.8 年 ≥ 2 年目标)。分页:`start`/`end`(ms)+ `limit≤1000`,响应
  `result.list` **最新在前**;行 = [start_ms, open, high, low, close, volume(base), turnover(USD)]。
- funding/history:`limit≤200`,用 `endTime` 向后翻页;BTC 6939 条(2020-03-25 起,8h 节奏),
  SOL 5917 条(2021-06-29 起)——深度足够做跨所资金费价差。
- 速率:探测全程 sleep 0.2–0.3s 无 403/429;全量资金费 BTC ~35 页。

落库:PIT sqlite `data/cache/bybit_perp.db`(key=ticker),经
`services/data/market_provider.py::ingest_provider_rows` 幂等写入(主键去重 + 未来数据守卫)。
资金费聚合成**日频求和**(与 tools/binance_funding_fetch.py 同一口径:当日 3 次 8h funding 之和,
UTC 日网格),落 JSON 快照 `data/cache/bybit_funding_rates.json`(镜像 crypto_funding_rates.json
的 {ticker: {day_ms_str: rate}} 形状)。`--out` 落 klines 快照(每 ticker 行数/起止/来源)。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time

from services.data.adapters import (SourceSpec, VolumeSemantics, VolumeUnit,
                                    normalize_records)
from services.data.raw_archive import (DEFAULT_RAW_ARCHIVE,
                                       archive_json_response)
from tools.cache_io import atomic_write_json
from tools.coingecko_fetch import _http_get_json

_HOST = "https://api.bybit.com"
_DAY_MS = 86_400_000
INTERVALS_MS = {"1d": _DAY_MS}
DEFAULT_DB = os.path.join("data", "cache", "bybit_perp.db")
DEFAULT_SNAPSHOT = os.path.join("data", "cache", "bybit_perp_klines.json")
DEFAULT_FUNDING_SNAPSHOT = os.path.join("data", "cache", "bybit_funding_rates.json")
SOURCE_NAME = "Bybit public linear perp klines (https://api.bybit.com/v5/market/kline)"
FUNDING_SOURCE_NAME = "Bybit public linear perp funding history (https://api.bybit.com/v5/market/funding/history)"

# 当前 24 个 OI 大币(crypto_oi_research.build_panel 宇宙,2026-07-25 快照;Phase 0 已逐币验证
# Bybit linear perp 历史 ≥2 年)。扩宇宙 = 先跑 Phase 0 深度验证再加名。
DEFAULT_TICKERS = ("AAVE", "ADA", "APT", "ATOM", "AVAX", "BCH", "BNB", "BTC", "DOGE", "DOT",
                   "ETC", "ETH", "FIL", "ICP", "INJ", "LINK", "LTC", "NEAR", "OP", "SOL",
                   "TRX", "UNI", "XLM", "XRP")

BYBIT_SPEC = SourceSpec(
    name="bybit-klines", asset_class="CRYPTO", time_unit="ms",
    field_map={"instrument_id": "sym", "event_time": "ts", "open": "o", "high": "h",
               "low": "l", "close": "c", "volume": "v"},
    knowledge_lag=0,
    volume_unit=VolumeUnit.BASE_ASSET,          # Bybit klines volume = 标的(base)成交量/根
    volume_semantics=VolumeSemantics.PER_BAR,
)


class BybitFetchError(RuntimeError):
    """Bybit 公共接口返回错误(retCode != 0)或响应形状不符契约。"""


def _get(path: str, http_get=_http_get_json, source_sink=None):
    source_uri = _HOST + path
    payload = http_get(source_uri, {"accept": "application/json"})
    if source_sink is not None:
        source_sink.append(source_uri)
    if not isinstance(payload, dict):
        raise BybitFetchError(f"Bybit response is not an object: {type(payload).__name__}")
    if payload.get("retCode") != 0:
        raise BybitFetchError(f"Bybit error {payload.get('retCode')}: {payload.get('retMsg')}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise BybitFetchError("Bybit response has no result object")
    return result


def fetch_klines(symbol: str = "BTCUSDT", *, interval: str = "1d", limit: int = 1000,
                 end_ms=None, http_get=_http_get_json, source_sink=None):
    """GET /v5/market/kline(category=linear)。只用 `end` 向后分页(与 mexc/binance 同法)。"""
    if interval not in INTERVALS_MS:
        raise ValueError(f"unsupported interval {interval!r} (expected {sorted(INTERVALS_MS)})")
    path = (f"/v5/market/kline?category=linear&symbol={symbol}"
            f"&interval={'D' if interval == '1d' else interval}&limit={int(limit)}")
    if end_ms is not None:
        path += f"&end={int(end_ms)}"
    result = _get(path, http_get, source_sink=source_sink)
    batch = result.get("list")
    if not isinstance(batch, list):
        raise BybitFetchError("Bybit kline response has no list")
    return batch


def parse_klines(batch, *, instrument_id: str, interval_ms: int = _DAY_MS,
                 diagnostics=None) -> dict:
    """纯函数:klines 数组(最新在前)-> {bucket_ms: row}。kline = [start,o,h,l,c,v,turnover];
    按 **start** 对齐 interval bucket 去重(与 binance/mexc 同一口径——event_time = bar 的
    开盘日界,不是收盘界;同一 bucket 后到者覆盖,重复计数进 diagnostics)。"""
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
        if not all(math.isfinite(x) for x in (o, h, low, c, v)) or c <= 0 or o <= 0:
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


def fetch_rows(ticker: str = "BTC", *, interval: str = "1d", bars: int = 2400,
               http_get=_http_get_json, now_ms=None, max_pages: int = 10,
               sleep: float = 0.25, archive_root=None, observed_at_ms=None) -> list:
    """用 `end` 向后翻页抓全历史 klines(limit=1000/页,响应最新在前),对齐 UTC interval 网格,
    丢掉未收盘的当根(无前视)。返回 SourceSpec 规范化行。无 key。fail-soft:pair 不存在/网络
    失败由调用方决定(抛异常,供刷新器 fail-soft 记录)。"""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if archive_root is not None and observed_at_ms is None:
        raise ValueError("observed_at_ms is required when archive_root is set")
    interval_ms = INTERVALS_MS[interval]
    symbol = ticker.upper() + "USDT"
    by_bucket, end = {}, now_ms
    for _ in range(max_pages):                       # 日线 10*1000 天 ≫ Bybit 全历史(2020 起)
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
                provider="Bybit public linear perp klines", dataset_id=f"perp_klines_{interval}",
                instrument_id=ticker, observed_at_ms=int(observed_at_ms),
                parse_diagnostics=diagnostics,
            )
        if not batch:
            break
        by_bucket.update(parsed)
        if len(batch) < 1000 or len(by_bucket) >= bars:
            break                                      # 已到历史起点 / 抓够了
        end = int(batch[-1][0]) - 1                    # 最新在前 → 最老一根是末位;下页止于其前
        if sleep:
            time.sleep(sleep)
    current = (now_ms // interval_ms) * interval_ms
    raw = [by_bucket[b] for b in sorted(by_bucket) if b < current]   # 丢未收盘当根(无前视)
    return normalize_records(raw[-bars:], BYBIT_SPEC)


def fetch_daily_rows(ticker: str = "BTC", *, days: int = 2400, http_get=_http_get_json,
                     now_ms=None, archive_root=None, observed_at_ms=None) -> list:
    """与 tools/binance_klines_fetch.fetch_daily_rows 同签名 —— 与既有刷新器入口对齐。"""
    return fetch_rows(ticker, interval="1d", bars=days, http_get=http_get, now_ms=now_ms,
                      archive_root=archive_root, observed_at_ms=observed_at_ms)


# ---------------------------------------------------------------------------
# 资金费历史(8h 节奏 → 日频求和,与 tools/binance_funding_fetch.py 同一口径)
# ---------------------------------------------------------------------------


def fetch_funding_history(symbol: str = "BTCUSDT", *, limit: int = 200, end_ms=None,
                          http_get=_http_get_json, source_sink=None):
    """GET /v5/market/funding/history(category=linear)。`endTime` 向后分页;响应最新在前。"""
    path = f"/v5/market/funding/history?category=linear&symbol={symbol}&limit={int(limit)}"
    if end_ms is not None:
        path += f"&endTime={int(end_ms)}"
    result = _get(path, http_get, source_sink=source_sink)
    batch = result.get("list")
    if not isinstance(batch, list):
        raise BybitFetchError("Bybit funding response has no list")
    return batch


def fetch_daily_funding(ticker: str = "BTC", *, days: int = 2400, http_get=_http_get_json,
                        now_ms=None, archive_root=None, observed_at_ms=None,
                        sleep: float = 0.2, max_pages: int = 60) -> dict:
    """公共 funding 历史 -> {day_bucket_ms: 当日 fundingRate 求和}(镜像 binance_funding_fetch
    .fetch_daily_funding 口径:funding 当日结算当日可知,无前视;丢未完结的当天)。无 key。"""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if archive_root is not None and observed_at_ms is None:
        raise ValueError("observed_at_ms is required when archive_root is set")
    symbol = ticker.upper() + "USDT"
    by_day = {}
    end = now_ms
    start_floor = max(0, now_ms - days * _DAY_MS)
    for _ in range(max_pages):
        source_uris = []
        batch = fetch_funding_history(symbol, limit=200, end_ms=end, http_get=http_get,
                                      source_sink=source_uris)
        parsed = []
        rejected = 0
        for it in batch:
            try:
                ts, rate = int(it["fundingRateTimestamp"]), float(it["fundingRate"])
            except (KeyError, TypeError, ValueError):
                rejected += 1
                continue
            if ts < 0 or ts > now_ms or not math.isfinite(rate):
                rejected += 1
                continue
            parsed.append((ts, rate))
        if archive_root is not None:
            archive_json_response(
                root=archive_root, payload=batch, source_uri=source_uris[-1],
                provider="Bybit public linear perp funding history", dataset_id="funding",
                instrument_id=ticker, observed_at_ms=int(observed_at_ms),
                parse_diagnostics={"status": "ACCEPTED", "input_rows": len(batch),
                                   "accepted_rows": len(parsed), "rejected_rows": rejected,
                                   "duplicate_rows": 0},
            )
        if not batch or not parsed:
            break
        for ts, rate in parsed:
            bucket = (ts // _DAY_MS) * _DAY_MS
            by_day[bucket] = by_day.get(bucket, 0.0) + rate     # 当日 8h fundings 求和
        oldest = min(ts for ts, _ in parsed)                   # 响应最新在前 → 最老一条
        if len(batch) < 200 or oldest <= start_floor:
            break                                              # 历史起点 / 覆盖够深
        end = oldest - 1                                       # 下一页止于本页最老一条之前
        if sleep:
            time.sleep(sleep)
    today = (now_ms // _DAY_MS) * _DAY_MS
    return {b: v for b, v in by_day.items() if b < today}      # 丢未完结的当天(无前视)


# ---------------------------------------------------------------------------
# 快照 + 主流程
# ---------------------------------------------------------------------------


def build_snapshot(rows_by_ticker, funding_by_ticker, *, interval: str, fetched_at_ms: int,
                   db_path=None, bars_ingested=None) -> dict:
    """纯函数:多 ticker 抓取结果 -> 快照 dict(行数/起止/来源/抓取时间戳)。无行时 span None(诚实)。"""
    def _d(ms):
        return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))

    tickers = {}
    for tk, rows in sorted(rows_by_ticker.items()):
        tickers[tk] = {
            "rows": len(rows),
            "first_event_time": rows[0]["event_time"] if rows else None,
            "last_event_time": rows[-1]["event_time"] if rows else None,
            "first_date": _d(rows[0]["event_time"]) if rows else None,
            "last_date": _d(rows[-1]["event_time"]) if rows else None,
        }
    funding = {}
    for tk, by_day in sorted(funding_by_ticker.items()):
        days = sorted(by_day)
        funding[tk] = {
            "days": len(by_day),
            "first_date": _d(days[0]) if days else None,
            "last_date": _d(days[-1]) if days else None,
        }
    return {
        "schema_version": 1,
        "source": SOURCE_NAME,
        "funding_source": FUNDING_SOURCE_NAME,
        "provenance": "REAL_BYBIT_PUBLIC_KLINES_FUNDING",
        "interval": interval,
        "tickers": tickers,
        "funding": funding,
        "funding_semantics": "daily SUM of the day's 8h funding rates, UTC day grid "
                             "(same convention as tools/binance_funding_fetch.py)",
        "fetched_at_ms": int(fetched_at_ms),
        "fetched_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(fetched_at_ms / 1000)),
        "db_path": db_path,
        "bars_ingested": bars_ingested,
    }


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description="Bybit 公开 klines+funding 抓取(无 key,只读)→ PIT 库 + 快照")
    ap.add_argument("--tickers", default=",".join(DEFAULT_TICKERS),
                    help="逗号分隔(默认 24 个 OI 大币)")
    ap.add_argument("--interval", default="1d", choices=sorted(INTERVALS_MS))
    ap.add_argument("--days", type=int, default=2400, help="日线回看根数")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=None, help=f"klines 快照 JSON 路径(如 {DEFAULT_SNAPSHOT})")
    ap.add_argument("--funding-out", default=None,
                    help=f"funding 快照 JSON 路径(如 {DEFAULT_FUNDING_SNAPSHOT})")
    ap.add_argument("--no-ingest", action="store_true", help="只抓+快照,不落 PIT 库")
    ap.add_argument("--no-funding", action="store_true", help="跳过 funding 历史")
    args = ap.parse_args(argv)

    from services.data.persistent_store import SqlitePITStore
    from services.data.market_provider import ingest_provider_rows

    interval_ms = INTERVALS_MS[args.interval]
    now_ms = int(time.time() * 1000)
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    os.makedirs(os.path.dirname(args.db) or ".", exist_ok=True)

    rows_by_ticker, funding_by_ticker = {}, {}
    total_ingested = 0
    store = None if args.no_ingest else SqlitePITStore(args.db, create=True)
    for tk in tickers:
        rows = fetch_rows(tk, interval=args.interval, bars=args.days, now_ms=now_ms,
                          archive_root=DEFAULT_RAW_ARCHIVE, observed_at_ms=now_ms)
        rows_by_ticker[tk] = rows
        if store is not None:
            rep = ingest_provider_rows(
                rows, store, dataset_id=f"bybit-{tk}", now=now_ms, time_unit="ms", strict=True,
                expected_interval=interval_ms,
                expected_end=(now_ms // interval_ms) * interval_ms - interval_ms)
            total_ingested += rep.bars_ingested
            ing = f", ingested {rep.bars_ingested}"
        else:
            ing = ""
        span = (f"{rows[0]['event_time']}..{rows[-1]['event_time']}" if rows else "—")
        print(f"{tk}USDT {args.interval}: {len(rows)} bars [{span}]{ing}", flush=True)
        if not args.no_funding:
            funding_by_ticker[tk] = fetch_daily_funding(
                tk, days=args.days, now_ms=now_ms,
                archive_root=DEFAULT_RAW_ARCHIVE, observed_at_ms=now_ms)
            fd = funding_by_ticker[tk]
            print(f"{tk}USDT funding: {len(fd)} days", flush=True)

    snap = build_snapshot(rows_by_ticker, funding_by_ticker, interval=args.interval,
                          fetched_at_ms=now_ms,
                          db_path=None if args.no_ingest else args.db,
                          bars_ingested=None if args.no_ingest else total_ingested)
    if args.out:
        atomic_write_json(args.out, snap, indent=2)
    if args.funding_out and funding_by_ticker:
        atomic_write_json(args.funding_out,
                          {tk: {str(day): rate for day, rate in sorted(by_day.items())}
                           for tk, by_day in sorted(funding_by_ticker.items())}, indent=2)
    print(f"snapshot -> {args.out or '(none)'}; funding -> {args.funding_out or '(none)'}"
          f"; total ingested {snap['bars_ingested']}", flush=True)
    return snap


if __name__ == "__main__":
    main()
