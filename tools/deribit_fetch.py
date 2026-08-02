"""Deribit 公开波动率/衍生品抓取器(门外、只读、无 key)—— 第四数据类(options/vol)。

Deribit API v2 公共端点(`https://www.deribit.com/api/v2/public/*`)无需 key、无账户、无任何
下单能力:stdlib urllib GET(复用共享 `_http_get_json`),不 import 任何交易所 SDK,不做
order/account/withdraw 调用——治理边界测试钉死这一点。网络只活在这里(门外);研究核心保持
离线/确定。这里没有也不应该有交易函数。

三个数据面(2026-07-24 Phase 0 实测):
1. **DVOL 日频 OHLC**(BTC/ETH):`get_volatility_index_data?resolution=1D`,每页最多 1000 行,
   `continuation` 字段向前翻页;BTC 实测 2021-03-24 起(1949 行)。DVOL 是年化波动率的
   **百分数**(54.21 = 54.21%)。落 PIT 库 key=`DVOL-<CCY>`。
2. **永续 + 到期期货日线**:`get_tradingview_chart_data?resolution=1D`,单次调用即给全历史
   (BTC-PERPETUAL 实测 2018-08-14 起 2902 行,不分页)。**关键 Phase 0 发现**:
   `get_instruments?expired=true` 已只回最近到期的一只,但**已知名字的已结算合约仍给全量历史
   日线**(BTC-25MAR22 给 2021-08-31..2022-03-25 共 207 行)——所以历史期限结构/基差可重建,
   名字按 Deribit 到期约定自生成(每月最后一个周五 08:00 UTC 到期,`BTC-25MAR22` 格式)。
   **污染警告(2026-07-24 实测)**:已结算合约的 chart 响应会把最后结算价**常数前填到今天**
   (BTC-25MAR22 自 expiry-1 起 44019.0 重复至 2026-07,1500+ 假日线;三个合约验证,规则一致)
   ——`fetch_chart_rows` 对到期合约硬截到期日 + `trim_constant_tail` 去常数尾,填充行绝不入库;
   上市前无后填(实测首行即真实首根)。落 PIT 库 key=合约名。月复一月向前滚动时,水位线
   (watermark)按合约记忆:已到期且数据覆盖到最后真实交易日的合约永久跳过,活跃合约只取增量。
3. **期权 book summary 快照**(BTC/ETH):`get_book_summary_by_currency?kind=option` 给当前
   全部上市期权的 mark_iv/mark_price/bid/ask/volume/OI/underlying_price(同到期 forward),
   **无 greeks**。25Δ skew 用 `services/research/vol_factors.bs_delta`(Black-76,forward=
   同到期 mark)自算 delta 后取最近 ±0.25 行——模型近似,非交易所官方 greeks,诚实标注。
   **免费 skew 历史不存在**:快照按日 key 落 JSON,从今天开始**向前积累**(同日重跑覆盖同
   一个 date key,幂等)。

PIT 纪律:knowledge_time = event_time(与全库 EOD 日线口径一致,见
services/data/market_provider.py 与各 klines 抓取器);未收盘的当根(今天)一律丢弃;
ingest 的 `now` 守卫再拒一切 kt > 抓取时刻的行(双层)。幂等:PIT 主键去重 + 水位线增量;
重跑零副作用。volume 刻意**不入库**(Deribit 合约 volume 单位 USD/张数口径易混淆,本批研究
只用 close——不冒双乘/错单位的风险,与 VolumeUnit 纪律一致)。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
import urllib.error

from services.data.adapters import SourceSpec, normalize_records
from tools.cache_io import atomic_write_json
from tools.coingecko_fetch import _http_get_json

_HOST = "https://www.deribit.com"
_API = "/api/v2"
_DAY_MS = 86_400_000
_HOUR_MS = 3_600_000
_YEAR_MS = 365 * _DAY_MS
DEFAULT_DB = os.path.join("data", "cache", "deribit_vol.db")
DEFAULT_SNAPSHOT = os.path.join("data", "cache", "deribit_vol_snapshot.json")
DEFAULT_OPTIONS_SNAPSHOT = os.path.join("data", "cache", "deribit_options_skew.json")
SOURCE_NAME = "Deribit public API v2 (https://www.deribit.com/api/v2/public, no key, read-only)"
_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_MONTH_BY_NAME = {m: i + 1 for i, m in enumerate(_MONTHS)}

# 无量纲 OHLC 行(volume 刻意不映射,见模块 docstring);kt=et(EOD 日线全库统一口径)。
DERIBIT_SPEC = SourceSpec(
    name="deribit-vol", asset_class="CRYPTO_DERIV", time_unit="ms",
    field_map={"instrument_id": "sym", "event_time": "ts", "open": "o", "high": "h",
               "low": "l", "close": "c"},
    knowledge_lag=0,
)


class DeribitFetchError(RuntimeError):
    """Deribit 公共接口返回错误体({"error": {...}})或响应形状不合预期。"""


def _get(path: str, http_get=_http_get_json, source_sink=None):
    """GET + 解 {"result": ...} 信封;{"error": ...} → DeribitFetchError(调用方对
    「未上市/不存在合约」fail-soft 跳过)。HTTP 层错误(如 start_timestamp 太旧给 400)
    同样翻译成 DeribitFetchError——单合约失败不应炸掉整批抓取。"""
    source_uri = _HOST + _API + path
    try:
        payload = http_get(source_uri, {"accept": "application/json"})
    except urllib.error.HTTPError as exc:
        raise DeribitFetchError(f"HTTP {exc.code} for {path}") from exc
    if source_sink is not None:
        source_sink.append(source_uri)
    if isinstance(payload, dict):
        if "error" in payload and payload["error"] is not None:
            err = payload["error"]
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise DeribitFetchError(f"Deribit error for {path}: {msg}")
        if "result" in payload:
            return payload["result"]
    raise DeribitFetchError(f"Deribit response for {path} is not a result envelope")


# --- 纯解析(无网络)---

def _finite(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def parse_dvol(result, *, currency: str, diagnostics=None) -> dict:
    """纯函数:DVOL result {"data": [[ts_ms,o,h,l,c],...], "continuation":...} -> {bucket: row}。
    按日网格对齐 bucket 去重;拒非有限/非正 close。row 是 DERIBIT_SPEC 的 raw 形状。"""
    out, rejected, duplicates = {}, 0, 0
    data = result.get("data", []) if isinstance(result, dict) else []
    if not isinstance(data, list):
        data = []
    for row in data:
        if not (isinstance(row, (list, tuple)) and len(row) >= 5):
            rejected += 1
            continue
        ts = _finite(row[0])
        o, h, low, c = (_finite(row[i]) for i in range(1, 5))
        if ts is None or c is None or c <= 0 or o is None or o <= 0:
            rejected += 1
            continue
        bucket = (int(ts) // _DAY_MS) * _DAY_MS
        if bucket in out:
            duplicates += 1
        out[bucket] = {"sym": f"DVOL-{currency}", "ts": bucket, "o": o, "h": h,
                       "l": low if low is not None else c, "c": c}
    if diagnostics is not None:
        diagnostics.update({"status": "ACCEPTED", "input_rows": len(data),
                            "accepted_rows": len(out), "rejected_rows": rejected,
                            "duplicate_rows": duplicates})
    return out


def parse_chart(result, *, instrument: str, diagnostics=None) -> dict:
    """纯函数:tradingview result {"status","ticks","open","high","low","close",...}
    -> {bucket: row}。status != "ok" 或长度不齐 → 空(fail-soft;已结算/未上市合约由
    _get 的错误体在更上层处理)。"""
    out, rejected, duplicates = {}, 0, 0
    ok = isinstance(result, dict) and result.get("status") == "ok"
    ticks = result.get("ticks", []) if ok else []
    opens = result.get("open", []) if ok else []
    highs = result.get("high", []) if ok else []
    lows = result.get("low", []) if ok else []
    closes = result.get("close", []) if ok else []
    n = len(ticks)
    for i in range(n):
        try:
            ts = int(ticks[i])
            o, h, low, c = (_finite(x[i]) for x in (opens, highs, lows, closes))
        except (IndexError, TypeError, ValueError):
            rejected += 1
            continue
        if c is None or c <= 0 or o is None or o <= 0:
            rejected += 1
            continue
        bucket = (ts // _DAY_MS) * _DAY_MS
        if bucket in out:
            duplicates += 1
        out[bucket] = {"sym": instrument, "ts": bucket, "o": o, "h": h,
                       "l": low if low is not None else c, "c": c}
    if diagnostics is not None:
        diagnostics.update({"status": "ACCEPTED" if ok else "REJECTED",
                            "input_rows": n, "accepted_rows": len(out),
                            "rejected_rows": rejected, "duplicate_rows": duplicates})
    return out


# --- 抓取(网络;http_get/now/sleep 注入,测试零网络)---

def fetch_dvol(currency: str, *, since_ms=None, http_get=_http_get_json, now_ms=None,
               max_pages: int = 6, sleep: float = 0.25) -> list:
    """DVOL 全历史(或 since_ms 增量):`continuation` 向前翻页直到历史起点/水位。
    丢未收盘当根(今天,无前视)。返回 DERIBIT_SPEC 规范化行(升序、主键无重)。"""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    start = _EPOCH_FLOOR_MS if since_ms is None else int(since_ms) + 1
    end = now_ms
    by_bucket = {}
    for _ in range(max_pages):
        path = (f"/public/get_volatility_index_data?currency={currency}"
                f"&start_timestamp={start}&end_timestamp={int(end)}&resolution=1D")
        result = _get(path, http_get)
        parsed = parse_dvol(result, currency=currency)
        if not parsed:
            break
        by_bucket.update(parsed)
        oldest = min(parsed)
        cont = result.get("continuation") if isinstance(result, dict) else None
        if not cont or oldest <= start:
            break                                   # 到历史起点 / 已覆盖水位之前
        end = int(cont)
        if sleep:
            time.sleep(sleep)
    today = (now_ms // _DAY_MS) * _DAY_MS
    raw = [by_bucket[b] for b in sorted(by_bucket) if b < today]
    return normalize_records(raw, DERIBIT_SPEC)


def trim_constant_tail(rows: list) -> list:
    """去掉结尾的常数填充段(纯函数):get_tradingview_chart_data 对已结算合约会把最后结算价
    **向前常数填充到今天**(2026-07-24 实测:BTC-25MAR22 自 expiry-1 起 44019.0 重复 1500+ 天)。
    从尾部起所有 close 相同的行全部视为填充丢弃;整列常数 → [](从未真实交易)。"""
    if not rows:
        return rows
    last = rows[-1]["c"]
    i = len(rows) - 1
    while i >= 0 and rows[i]["c"] == last:
        i -= 1
    return rows[:i + 1]


def fetch_chart_rows(instrument: str, *, since_ms=None, end_ms=None,
                     expiry_day_ms=None, http_get=_http_get_json, now_ms=None) -> list:
    """单合约日频全历史(单次调用,该端点不分页——实测 8 年日线一次返回)。
    丢未收盘当根。到期合约(expiry_day_ms 给定):**截断到期日之后的填充**(bucket >
    expiry 不可能真实)+ 去常数尾(结算价前填,见 trim_constant_tail)。未知/未上市合约的
    错误体向上抛(DeribitFetchError,调用方 fail-soft)。"""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    start = _EPOCH_FLOOR_MS if since_ms is None else int(since_ms) + 1
    result = fetch_chart(instrument, start_ms=start, end_ms=end_ms or now_ms,
                         http_get=http_get)
    parsed = parse_chart(result, instrument=instrument)
    today = (now_ms // _DAY_MS) * _DAY_MS
    raw = [parsed[b] for b in sorted(parsed) if b < today]
    if expiry_day_ms is not None:
        raw = [r for r in raw if r["ts"] <= expiry_day_ms]
        raw = trim_constant_tail(raw)
    return normalize_records(raw, DERIBIT_SPEC)


def fetch_chart(instrument: str, *, start_ms, end_ms, http_get=_http_get_json):
    path = (f"/public/get_tradingview_chart_data?instrument_name={instrument}"
            f"&start_timestamp={int(start_ms)}&end_timestamp={int(end_ms)}&resolution=1D")
    return _get(path, http_get)


def fetch_option_book(currency: str, *, http_get=_http_get_json) -> list:
    """当前上市全部期权的 book summary(mark_iv 等,无 greeks)。"""
    result = _get(f"/public/get_book_summary_by_currency?currency={currency}&kind=option",
                  http_get)
    return result if isinstance(result, list) else []


# --- 合约名生成/解析(纯函数;Deribit 到期约定:每月最后一个周五 08:00 UTC)---

def _ymd_to_day_ms(y: int, m: int, d: int) -> int:
    """days-from-civil 纯整数算法(避免 calendar/datetime import):UTC 当日 00:00 的 epoch ms。"""
    y -= 1 if m <= 2 else 0
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m - 3 if m > 2 else m + 9) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return (era * 146097 + doe - 719468) * _DAY_MS


# 历史地板:start_timestamp=0 会被 get_tradingview_chart_data 拒(HTTP 400,2026-07-24 实测);
# Deribit perp 历史起于 2018-08,全量抓取从 2018-01-01 起即可覆盖一切。
_EPOCH_FLOOR_MS = _ymd_to_day_ms(2018, 1, 1)


def _month_len(y: int, m: int) -> int:
    if m == 2:
        leap = (y % 4 == 0 and y % 100 != 0) or y % 400 == 0
        return 29 if leap else 28
    return 30 if m in (4, 6, 9, 11) else 31


def monthly_future_names(currency: str, start_year: int, start_month: int,
                         end_ms: int) -> list:
    """生成 [(name, expiry_day_ms)]:start 年月到 end_ms 之间每月最后一个周五到期的期货名
    (`BTC-25MAR22` 格式;最后周五恒在 25-31 日,day 恒两位)。用于探测已结算合约历史
    (get_instruments?expired=true 已不再回全量名单,但已知名字的合约历史仍可取——Phase 0
    实测)与向前滚动的新上市月度/季度合约。"""
    out = []
    y, m = start_year, start_month
    while True:
        last_day = _month_len(y, m)
        z = _ymd_to_day_ms(y, m, last_day) // _DAY_MS
        weekday = (z + 3) % 7                        # 1970-01-01 = 周四(Mon=0 -> 3)
        expiry_day = last_day - (weekday - 4) % 7    # 回拨到周五(Fri=4)
        expiry_ms = _ymd_to_day_ms(y, m, expiry_day)
        if expiry_ms > end_ms:
            break
        name = f"{currency}-{expiry_day:02d}{_MONTHS[m - 1]}{y % 100:02d}"
        out.append((name, expiry_ms))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def parse_instrument_name(name: str):
    """`BTC-28JUL26-70000-C` -> (currency, expiry_day_ms, strike, is_call);
    `BTC-PERPETUAL`/`BTC-25MAR22` -> strike/is_call 为 None。形状不合 → None。"""
    parts = name.split("-")
    if len(parts) < 2:
        return None
    ccy = parts[0]
    if parts[1] == "PERPETUAL":
        return (ccy, None, None, None)
    code = parts[1]
    i = 0
    while i < len(code) and code[i].isdigit():
        i += 1
    day_str, rest = code[:i], code[i:]
    if not day_str or len(rest) != 5 or rest[:3] not in _MONTH_BY_NAME:
        return None
    try:
        day = int(day_str)
        month = _MONTH_BY_NAME[rest[:3]]
        year = 2000 + int(rest[3:5])
    except ValueError:
        return None
    expiry_ms = _ymd_to_day_ms(year, month, day)
    if len(parts) == 2:                              # 到期期货(无 strike 段)
        return (ccy, expiry_ms, None, None)
    if len(parts) == 4 and parts[3] in ("C", "P"):
        strike = _finite(parts[2])
        if strike is None:
            return None
        return (ccy, expiry_ms, strike, parts[3] == "C")
    return None


# --- 期权快照:解析 + Black-76 delta + 25Δ skew(纯计算,数学在 services/research/vol_factors)---

def parse_option_entries(book, *, currency: str) -> list:
    """纯函数:book summary list ->  trimmed entries(mark_iv/forward/strike/expiry/方向)。"""
    out = []
    for item in book if isinstance(book, list) else []:
        if not isinstance(item, dict):
            continue
        name = item.get("instrument_name")
        parsed = parse_instrument_name(name) if isinstance(name, str) else None
        if parsed is None or parsed[3] is None:
            continue                                # 非本币种期权形状
        ccy, expiry_ms, strike, is_call = parsed
        if ccy != currency:
            continue
        iv = _finite(item.get("mark_iv"))
        fwd = _finite(item.get("underlying_price"))
        out.append({
            "instrument_name": name,
            "expiry": name.split("-")[1],
            "expiry_day_ms": expiry_ms,
            "strike": strike,
            "is_call": is_call,
            "mark_iv": iv,
            "underlying_price": fwd,
            "bid_price": _finite(item.get("bid_price")),
            "ask_price": _finite(item.get("ask_price")),
            "volume": _finite(item.get("volume")),
            "open_interest": _finite(item.get("open_interest")),
        })
    return out


def build_skew_snapshot(entries: list, *, now_ms: int) -> dict:
    """纯计算:entries -> {expiries: [...], entries: [...(带 delta)]}。
    delta = Black-76(forward=同到期 underlying mark,iv=mark_iv/100);到期日 08:00 UTC 结算,
    dte<=0 的 expiry 跳过(不对外推)。skew 由 services/research/vol_factors.skew_25d 计算。"""
    from services.research.vol_factors import bs_delta, skew_25d
    by_expiry: dict = {}
    enriched = []
    for e in entries:
        dte = (e["expiry_day_ms"] + 8 * _HOUR_MS - now_ms) / _YEAR_MS
        delta = None
        if dte > 0 and e["mark_iv"] and e["underlying_price"]:
            delta = bs_delta(e["underlying_price"], e["strike"], dte,
                             e["mark_iv"] / 100.0, e["is_call"])
        row = dict(e)
        row["delta"] = delta
        enriched.append(row)
        if dte > 0 and delta is not None:
            by_expiry.setdefault(e["expiry"], []).append(row)
    expiries = []
    for expiry in sorted(by_expiry,
                         key=lambda x: min(r["expiry_day_ms"] for r in by_expiry[x])):
        rows = by_expiry[expiry]
        s = skew_25d(rows)
        fwd = next((r["underlying_price"] for r in rows if r["underlying_price"]), None)
        dte_days = round((rows[0]["expiry_day_ms"] + 8 * _HOUR_MS - now_ms) / _DAY_MS, 2)
        out = {"expiry": expiry, "dte_days": dte_days, "forward": fwd, **s}
        expiries.append(out)
    for row in enriched:
        row.pop("expiry_day_ms", None)
    return {"expiries": expiries, "entries": enriched}


def update_options_snapshot(path: str, currency_snaps: dict, *, now_ms: int,
                            provenance: str = "REAL_DERIBIT_OPTIONS_BOOK") -> dict:
    """幂等落期权快照:按 UTC 日 key 覆盖(同日重跑无副作用),向前积累。
    currency_snaps = {ccy: build_skew_snapshot(...)}。"""
    date_key = time.strftime("%Y-%m-%d", time.gmtime(now_ms / 1000))
    doc = {"schema_version": 1, "provenance": provenance}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                prior = json.load(fh)
            if isinstance(prior, dict) and isinstance(prior.get("snapshots"), dict):
                doc["snapshots"] = prior["snapshots"]
        except (OSError, ValueError):
            pass                                    # 坏文件 → 重建(不冒充历史)
    doc.setdefault("snapshots", {})
    doc["note"] = ("免费 skew 历史不存在——本文件从首次抓取日起向前积累;delta 为 Black-76 "
                   "自算(mark_iv + 同到期 forward),非交易所官方 greeks;skew = 最近 ±0.25 "
                   "delta 行的 IV 差(tolerance 0.20)。")
    doc["snapshots"][date_key] = {
        "fetched_at_ms": int(now_ms),
        **{ccy: snap for ccy, snap in currency_snaps.items()},
    }
    atomic_write_json(path, doc, indent=1)
    return doc


# --- 水位线(幂等增量;纯 sqlite3 读,与 price_watermarks 同模式)---

def _watermarks(db_path: str) -> dict:
    """{key: max(event_time)};库缺失/坏 → {}(fail-soft,全量重抓由主键去重保持幂等)。"""
    if not os.path.exists(db_path):
        return {}
    try:
        con = sqlite3.connect(db_path)
        rows = con.execute("SELECT key, MAX(event_time) FROM bars GROUP BY key").fetchall()
        con.close()
        return {k: v for k, v in rows if v is not None}
    except sqlite3.DatabaseError:
        return {}


def _d(ms) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description="Deribit 公开 vol/衍生品抓取(无 key,只读)→ PIT 库 + 快照")
    ap.add_argument("--currencies", default="BTC,ETH")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=DEFAULT_SNAPSHOT)
    ap.add_argument("--options-out", default=DEFAULT_OPTIONS_SNAPSHOT)
    ap.add_argument("--start-year", type=int, default=2021, help="已结算合约探测起点(DVOL 起点 2021-03)")
    ap.add_argument("--months-ahead", type=int, default=12, help="向前探测未到期月度的月数")
    ap.add_argument("--sleep", type=float, default=0.25)
    ap.add_argument("--no-ingest", action="store_true")
    ap.add_argument("--no-options", action="store_true")
    args = ap.parse_args(argv)

    from services.data.market_provider import ingest_provider_rows
    from services.data.persistent_store import SqlitePITStore

    now_ms = int(time.time() * 1000)
    today = (now_ms // _DAY_MS) * _DAY_MS
    currencies = [c.strip().upper() for c in args.currencies.split(",") if c.strip()]
    watermarks = _watermarks(args.db)
    store = None if args.no_ingest else SqlitePITStore(args.db, create=True)
    datasets: dict = {}
    unavailable: list = []

    def _ingest(rows, dataset_id, *, expected_interval=None, expected_end=None):
        if args.no_ingest or not rows:
            return 0
        rep = ingest_provider_rows(rows, store, dataset_id=dataset_id, now=now_ms,
                                   time_unit="ms", strict=True,
                                   expected_interval=expected_interval,
                                   expected_end=expected_end)
        return rep.bars_ingested

    for ccy in currencies:
        # 1) DVOL 全历史/增量
        key = f"DVOL-{ccy}"
        rows = fetch_dvol(ccy, since_ms=watermarks.get(key), now_ms=now_ms, sleep=args.sleep)
        n = _ingest(rows, f"deribit-dvol-{ccy}", expected_interval=_DAY_MS,
                    expected_end=today - _DAY_MS)
        datasets[key] = {"rows": len(rows), "ingested": n,
                         "first_date": _d(rows[0]["event_time"]) if rows else None,
                         "last_date": _d(rows[-1]["event_time"]) if rows else None}
        print(f"{key}: {len(rows)} bars [{datasets[key]['first_date']} .. "
              f"{datasets[key]['last_date']}], ingested {n}", flush=True)
        if args.sleep:
            time.sleep(args.sleep)

        # 2) 永续 + 月度/季度到期期货(已结算合约按名探测;活跃合约水位增量)
        candidates = [(f"{ccy}-PERPETUAL", None)]
        grid_end = today + args.months_ahead * 31 * _DAY_MS
        candidates += monthly_future_names(ccy, args.start_year, 1, grid_end)
        for name, expiry_ms in candidates:
            wm = watermarks.get(name)
            if expiry_ms is not None and wm is not None and wm >= expiry_ms - 2 * _DAY_MS:
                continue                            # 已结算且真实数据覆盖到最后交易日:永久跳过
            try:
                rows = fetch_chart_rows(name, since_ms=wm, now_ms=now_ms,
                                        expiry_day_ms=expiry_ms,
                                        http_get=_http_get_json)
            except DeribitFetchError as exc:
                unavailable.append({"instrument": name, "reason": str(exc)[:120]})
                continue
            if not rows:
                unavailable.append({"instrument": name, "reason": "no daily rows served"})
                continue
            n = _ingest(rows, f"deribit-fut-{name}",
                        expected_interval=_DAY_MS if expiry_ms is None else None,
                        expected_end=today - _DAY_MS if expiry_ms is None else None)
            datasets[name] = {"rows": len(rows), "ingested": n,
                              "first_date": _d(rows[0]["event_time"]),
                              "last_date": _d(rows[-1]["event_time"]),
                              "expiry_date": _d(expiry_ms) if expiry_ms else None}
            print(f"{name}: {len(rows)} bars [{datasets[name]['first_date']} .. "
                  f"{datasets[name]['last_date']}], ingested {n}", flush=True)
            if args.sleep:
                time.sleep(args.sleep)

    # 3) 期权 book 快照(向前积累;同日幂等)
    options_info = None
    if not args.no_options:
        currency_snaps = {}
        for ccy in currencies:
            entries = parse_option_entries(fetch_option_book(ccy), currency=ccy)
            snap = build_skew_snapshot(entries, now_ms=now_ms)
            currency_snaps[ccy] = snap
            if args.sleep:
                time.sleep(args.sleep)
        doc = update_options_snapshot(args.options_out, currency_snaps, now_ms=now_ms)
        options_info = {ccy: {"entries": len(s["entries"]), "expiries": len(s["expiries"])}
                        for ccy, s in currency_snaps.items()}
        print(f"options snapshot -> {args.options_out} "
              f"({len(doc['snapshots'])} dated snapshots)", flush=True)

    snap = {
        "schema_version": 1,
        "source": SOURCE_NAME,
        "provenance": "REAL_DERIBIT_PUBLIC",
        "fetched_at_ms": now_ms,
        "fetched_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ms / 1000)),
        "db_path": None if args.no_ingest else args.db,
        "datasets": datasets,
        "unavailable": unavailable,
        "options": options_info,
        "options_snapshot": None if args.no_options else args.options_out,
    }
    if args.out:
        atomic_write_json(args.out, snap, indent=2)
        print(f"-> wrote {args.out}", flush=True)
    return snap


if __name__ == "__main__":
    main()
