"""抓 ALFRED(FRED archival vintage)经济发布序列(CPIAUCSL/UNRATE/GDPC1)的**全部 vintage**,
填进独立的 data/cache/macro_alfred.db(不混 macro_fred.db——那个库每次 fetch drop 重建,且只存
"最新修正值、knowledge=event"的市场型日频序列;经济发布类必须走 vintage 才没有前视)。

PIT 语义(与 fred_fetch 的关键差别):
- knowledge_time = 该 vintage 的 `realtime_start`(该数值**首次真实可知**的日子),绝不用
  event_time(期首)冒充 knowledge_time——那是前视。
- event_time 沿用 FRED 的 observation_date(期首,如 2020 年 1 月 CPI = 20200101),与 fred_fetch
  约定一致;发布恒晚于期首 → knowledge>=event 不变量天然成立(FeatureValue 契约仍兜底强制)。
- 同一 event 的历次修正各占一行(restatement 天然保留);ALFRED 档案起点之前的老观测,
  realtime_start = 档案首个 vintage 日 —— 比真实发布日更晚 = 保守方向,PIT 安全(对 crypto
  样本 2020+ 无实际影响)。
- 时间戳约定:yyyymmdd int(macro 库既有约定,沿用 fred_fetch/fundamental_store 存量数据;
  非 quantos/contracts/temporal.py docstring 的 epoch-µs 原义——BitemporalRecord 只强制
  knowledge>=event 的**序关系**,与单位无关,库内自洽即可)。

密钥与网络边界:
- 网络只在 tools/(services/ 无网络 import,治理钉死;本文件已列入
  tests/governance/test_crypto_data_boundary.py 的 _FETCHERS)。
- api.stlouisfed.org 必须**免费 API key**(https://fredaccount.stlouisfed.org/apikeys)。只从
  FRED_API_KEY 环境变量或 key/fred_api_key.txt(gitignored)读;缺失/空白 → 诚实说明 + 非零退出、
  **零网络请求**,绝不回退 fredgraph(免费 CSV 只给最新修正值,冒充 vintage = 前视,宁缺毋滥)。
- key 以 query 参数进 URL → 所有异常/日志路径**只带 series_id+offset+状态码,绝不带完整 URL**
  (测试钉死:fake transport 抛错时消息与 stdout/stderr 不含 key)。

fail-closed:分页装配行数与 count 不齐 → raise 非零退出(宁失败勿半库)。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

from quantos.contracts.feature import FeatureValue
from services.data.fundamental_store import SqliteFundamentalStore

_API = "https://api.stlouisfed.org/fred/series/observations"
_SERIES = ["CPIAUCSL", "UNRATE", "GDPC1"]
_DB = os.path.join("data", "cache", "macro_alfred.db")
_KEY_PATH = os.path.join("key", "fred_api_key.txt")
_PAGE_LIMIT = 100000                       # API 单页上限;CPIAUCSL 全 vintage 必超一页,分页必须实现
_REALTIME_START = "1776-07-04"             # ALFRED 约定的"全部历史 vintage"哨兵起点
_REALTIME_END = "9999-12-31"


def _load_key(key_path: str = "") -> str:
    """FRED_API_KEY 环境变量优先,其次 key/fred_api_key.txt;都没有 → 空串(调用方 fail-closed)。"""
    env = os.environ.get("FRED_API_KEY", "").strip()
    if env:
        return env
    kp = key_path or _KEY_PATH
    if os.path.exists(kp):
        try:
            return open(kp, encoding="utf-8").read().strip()
        except OSError:
            return ""
    return ""


def _err(series_id: str, offset: int, msg: str) -> str:
    """错误消息模板:只带 series_id+offset+原因,绝不带 URL(URL 含 api_key)。"""
    return f"ALFRED {series_id} offset={offset} {msg}"


def _fetch_page(series_id: str, offset: int, key: str,
                opener=urllib.request.urlopen, page_limit: int = _PAGE_LIMIT) -> dict:
    """单页请求(transport 可注入)。任何失败都转成脱敏 RuntimeError(from None 切断含 URL 的原异常链)。"""
    qs = urllib.parse.urlencode({
        "series_id": series_id, "api_key": key, "file_type": "json",
        "realtime_start": _REALTIME_START, "realtime_end": _REALTIME_END,
        "limit": page_limit, "offset": offset, "sort_order": "asc",
    })
    req = urllib.request.Request(f"{_API}?{qs}", headers={"User-Agent": "quantos-research/1.0"})
    try:
        with opener(req, timeout=120) as resp:
            status = getattr(resp, "status", 200)
            body = resp.read()
    except Exception as exc:                                    # noqa: BLE001 —— 必须捕全再脱敏
        code = getattr(exc, "code", None)
        detail = f"request failed: {type(exc).__name__}" + (f" HTTP {code}" if code else "")
        raise RuntimeError(_err(series_id, offset, detail)) from None
    if status != 200:
        raise RuntimeError(_err(series_id, offset, f"HTTP {status}"))
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:                                           # noqa: BLE001
        raise RuntimeError(_err(series_id, offset, "non-JSON response")) from None


def parse_observations(payload: dict) -> tuple:
    """纯函数:payload["observations"] -> ([(event_yyyymmdd, knowledge_yyyymmdd, value)], rejected)。
    knowledge = realtime_start(该 vintage 首次可知日);value=="." = 缺失,跳过(不算坏行);
    值不可解析 / realtime_start<date(理论不可能,防御)→ 进 rejected,不入库。"""
    recs, rejected = [], []
    for ob in payload.get("observations", []):
        raw = ob.get("value")
        if raw in (".", "", None):
            continue
        date_s, rt_s = ob.get("date", ""), ob.get("realtime_start", "")
        try:
            v = float(raw)
            event = int(date_s.replace("-", ""))
            know = int(rt_s.replace("-", ""))
        except (ValueError, AttributeError):
            rejected.append((date_s, rt_s, f"unparseable row: value={raw!r}"))
            continue
        if know < event:
            rejected.append((date_s, rt_s, "lookahead: realtime_start < date"))
            continue
        recs.append((event, know, v))
    return recs, rejected


def fetch_series(series_id: str, key: str, opener=urllib.request.urlopen,
                 page_limit: int = _PAGE_LIMIT) -> tuple:
    """分页拉一条序列的全部 vintage。装配行数(含 '.' 缺失行)必须与 API 的 count 对齐,
    不齐 → raise(宁失败勿半库)。返回 (records, rejected)。"""
    recs, rejected = [], []
    raw_rows = 0
    count = None
    offset = 0
    while True:
        payload = _fetch_page(series_id, offset, key, opener=opener, page_limit=page_limit)
        obs = payload.get("observations", [])
        count = int(payload.get("count", 0))
        raw_rows += len(obs)
        r, rej = parse_observations(payload)
        recs.extend(r)
        rejected.extend(rej)
        if raw_rows >= count or not obs:
            break
        offset = raw_rows
    if raw_rows != count:
        raise RuntimeError(_err(series_id, offset,
                                f"paging mismatch: assembled {raw_rows} rows but count={count}"))
    return recs, rejected


def store_record(store: SqliteFundamentalStore, series_id: str,
                 event: int, know: int, value: float) -> str:
    """单行入库:FeatureValue 契约本身拒 lookahead(knowledge<event)——契约拒收计为
    'contract-rejected' 上报,绝不悄悄改时间。返回 added|duplicate|contract-rejected。"""
    try:
        fv = FeatureValue(feature_id=series_id, instrument_id="MACRO",
                          event_time=event, knowledge_time=know, value=value)
    except Exception:                                           # noqa: BLE001 —— 契约拒收是数据问题,不炸整跑
        return "contract-rejected"
    return "added" if store.add(fv) else "duplicate"


def main(opener=urllib.request.urlopen) -> None:
    key = _load_key()
    if not key:
        print("ALFRED fetch SKIPPED (fail-closed): 没有 FRED API key。", file=sys.stderr)
        print("  1) 免费注册:https://fredaccount.stlouisfed.org/apikeys", file=sys.stderr)
        print("  2) 放进环境变量 FRED_API_KEY 或 key/fred_api_key.txt(gitignored,勿提交)。", file=sys.stderr)
        print("  绝不回退 fredgraph 最新修正值冒充 vintage(那是前视)——宁缺毋滥。", file=sys.stderr)
        sys.exit(1)

    if os.path.exists(_DB):
        os.remove(_DB)
    store = SqliteFundamentalStore(_DB, create=True)
    total = 0
    for s in _SERIES:
        recs, rejected = fetch_series(s, key, opener=opener)
        counts = {"added": 0, "duplicate": 0, "contract-rejected": 0}
        for event, know, v in recs:
            counts[store_record(store, s, event, know, v)] += 1
        total += counts["added"]
        rows = store.as_of(s, "MACRO", 99999999)
        span = f"{rows[0].event_time}-{rows[-1].event_time}" if rows else "-"
        print(f"  {s:10s} vintage rows added {counts['added']} · dup {counts['duplicate']} · "
              f"parse-rejected {len(rejected)} · contract-rejected {counts['contract-rejected']} · "
              f"event span {span}", flush=True)
    print(f"=== ALFRED vintage 填库完成 · {len(_SERIES)} 序列 · {total} 行 -> {_DB} ===", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:                                    # noqa: BLE001
        # exc 全部出自本文件的脱敏 RuntimeError(或本地 IO/sqlite),不含 URL/key
        print(f"ALFRED fetch FAILED (fail-closed): {exc}", file=sys.stderr)
        sys.exit(1)
