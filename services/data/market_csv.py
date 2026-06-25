"""离线公开-CSV 适配器:把真实历史日线 CSV(Date,OHLCV)映射进 PIT 数据湖。

无网络、无券商、无凭证 —— 仅读用户离线自带的本地 CSV(BYO-CSV)。复用共享的失败关闭
接入核心(normalize/reject-future/dedup/hash/quality)。一个文件 = 一个标的
(instrument_id 由调用方注入,因真实 CSV 通常无此列)。

event_time = YYYYMMDD 整数(单调可排序;panel_builder 按位置取前向,故非均匀间距安全)。
knowledge_time = event_time(EOD 当日收盘即可知;as_of 过滤无 lookahead)。
"""
from __future__ import annotations

import csv
from datetime import datetime

from .file_ingest import IngestionReport, _ingest_records

DEFAULT_COLUMN_MAP = {"date": "Date", "open": "Open", "high": "High",
                      "low": "Low", "close": "Close", "volume": "Volume"}

_REQUIRED_KEYS = ("date", "open", "high", "low", "close", "volume")


def ingest_market_csv(path: str, store, *, instrument_id: str, column_map=None,
                      date_format: str = "%Y-%m-%d", now=None,
                      dataset_id: str = "market", version: str = "") -> IngestionReport:
    cmap = dict(DEFAULT_COLUMN_MAP if column_map is None else column_map)
    missing = [k for k in _REQUIRED_KEYS if k not in cmap]
    if missing:
        raise ValueError(f"column_map missing keys: {missing}")

    def parse(raw):
        dt = datetime.strptime(raw[cmap["date"]], date_format)
        event_time = dt.year * 10000 + dt.month * 100 + dt.day
        canonical = {
            "instrument_id": instrument_id,
            "event_time": event_time,
            "open": float(raw[cmap["open"]]),
            "high": float(raw[cmap["high"]]),
            "low": float(raw[cmap["low"]]),
            "close": float(raw[cmap["close"]]),
            "volume": float(raw[cmap["volume"]]),
        }
        return canonical, event_time     # knowledge_time = event_time(EOD)

    with open(path, newline="", encoding="utf-8") as fh:
        records = list(enumerate(csv.DictReader(fh)))
    return _ingest_records(records, parse, store, now=now, dataset_id=dataset_id, version=version)
