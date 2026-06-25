"""离线公开-CSV 适配器:把真实历史日线 CSV(Date,OHLCV)映射进 PIT 数据湖。

无网络、无券商、无凭证 —— 仅读用户离线自带的本地 CSV(BYO-CSV)。复用共享的失败关闭
接入核心(normalize/reject-future/dedup/hash/quality)。一个文件 = 一个标的
(instrument_id 由调用方注入,因真实 CSV 通常无此列)。

event_time = YYYYMMDD 整数(单调可排序;panel_builder 按位置取前向,故非均匀间距安全)。

knowledge_time:默认 = event_time(EOD 当日收盘即可知;as_of 过滤无 lookahead)。**厂商
修正(restatement):** 默认下同一日期的修正行 PK 相同 → store 去重丢弃修正(首见为准)。
要把修正作为新的 bitemporal 行保留,显式传 `knowledge_time=<更晚的接入日>`(必须 >= 该文件
所有 event_time),修正行即以不同 knowledge_time 入库,store 保留两版、as_of 按时点取最新已知。

标度一致性(脚枪提醒):一个 store 内**所有**时间整数必须同标度。真实数据全程用 YYYYMMDD,
则 `now`/`as_of` 也必须传 YYYYMMDD(如 20240601);切勿与合成小整数 event_time 的 store 混用,
否则 as_of 会静默过滤为空、或 now 把全部真实 bar 当未来拒掉。
"""
from __future__ import annotations

import csv
from datetime import datetime

from .file_ingest import IngestionReport, _ingest_records

DEFAULT_COLUMN_MAP = {"date": "Date", "open": "Open", "high": "High",
                      "low": "Low", "close": "Close", "volume": "Volume"}

_REQUIRED_KEYS = ("date", "open", "high", "low", "close", "volume")


def _validate_date_format(date_format: str) -> None:
    """守卫静默错序的歧义格式(审查 P2):必须用 4 位年 %Y + %m + %d,禁用 2 位年 %y
    (1969/2068 翻转)与缺日格式(不同交易日塌缩成同一 YYYYMMDD)。"""
    if "%y" in date_format:
        raise ValueError(f"date_format 禁用两位年 %y(会在世纪边界翻转排序);用 %Y。得到 {date_format!r}")
    for d in ("%Y", "%m", "%d"):
        if d not in date_format:
            raise ValueError(f"date_format 必须含 {d}(需明确的 年/月/日 → 单调 YYYYMMDD)。得到 {date_format!r}")


def ingest_market_csv(path: str, store, *, instrument_id: str, column_map=None,
                      date_format: str = "%Y-%m-%d", knowledge_time: int | None = None,
                      now=None, dataset_id: str = "market", version: str = "") -> IngestionReport:
    cmap = dict(DEFAULT_COLUMN_MAP if column_map is None else column_map)
    missing = [k for k in _REQUIRED_KEYS if k not in cmap]
    if missing:
        raise ValueError(f"column_map missing keys: {missing}")
    _validate_date_format(date_format)

    def parse(raw):
        dt = datetime.strptime(raw[cmap["date"]], date_format)
        event_time = dt.year * 10000 + dt.month * 100 + dt.day
        # 默认 EOD 当日可知;显式 knowledge_time 让 restatement 作为新 bitemporal 行保留
        kt = event_time if knowledge_time is None else int(knowledge_time)
        canonical = {
            "instrument_id": instrument_id,
            "event_time": event_time,
            "open": float(raw[cmap["open"]]),
            "high": float(raw[cmap["high"]]),
            "low": float(raw[cmap["low"]]),
            "close": float(raw[cmap["close"]]),
            "volume": float(raw[cmap["volume"]]),
        }
        return canonical, kt

    with open(path, newline="", encoding="utf-8") as fh:
        records = list(enumerate(csv.DictReader(fh)))
    return _ingest_records(records, parse, store, now=now, dataset_id=dataset_id, version=version)
