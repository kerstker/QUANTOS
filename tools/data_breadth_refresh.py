"""补充数据渠道刷新（只读、可中断、独立于核心五源 SLO）。

核心 ``live_data_refresh`` 负责已有研究主链；本驱动器负责历史能力有限或
需要实时捕获的补充维度：Binance 强平事件与 Binance/Bybit 同步公开盘口。
任何一个补充源失败只写入 ``supplemental_data_refresh.json``，不会把旧缓存
冒充新鲜数据，也不会降低主链 admission 门。
"""
from __future__ import annotations

import os
import time

from tools.binance_liquidations_fetch import fetch_daily_liquidations, update_cache
from tools.cache_io import atomic_write_json
from tools.cross_exchange_snapshot import append_snapshots, capture_snapshot
from tools.data_channel_inventory import build_inventory
from services.data.raw_archive import DEFAULT_RAW_ARCHIVE, summarize_archive
from tools.source_failures import classify_source_failure

_STATUS = os.path.join("data", "cache", "supplemental_data_refresh.json")
_LIQ_CACHE = os.path.join("data", "cache", "crypto_liquidations.json")
_BOOK_CACHE = os.path.join("data", "cache", "cross_exchange_snapshot.json")


def run_supplemental_refresh(*, tickers: tuple[str, ...] = ("BTC", "ETH", "SOL"),
                             now_ms: int | None = None, liquidation_days: int = 7,
                             liquidation_fetch=fetch_daily_liquidations,
                             snapshot_fetch=capture_snapshot,
                             status_path: str = _STATUS,
                             liquidation_path: str = _LIQ_CACHE,
                             snapshot_path: str = _BOOK_CACHE,
                             inventory_root: str = ".",
                             archive_root: str | None = DEFAULT_RAW_ARCHIVE) -> dict:
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    reports = []
    for ticker in tuple(dict.fromkeys(str(item).upper() for item in tickers)):
        try:
            rows = liquidation_fetch(ticker, days=liquidation_days,
                                     now_ms=now_ms, archive_root=archive_root,
                                     observed_at_ms=now_ms if archive_root else None)
            update_cache(liquidation_path, ticker=ticker, rows=rows,
                         fetched_at_ms=now_ms)
            reports.append({"channel": "binance_liquidations", "ticker": ticker,
                            "status": "PASS", "closed_days": len(rows)})
        except Exception as exc:  # noqa: BLE001 — per-channel fail-soft report
            reports.append({"channel": "binance_liquidations", "ticker": ticker,
                            "status": "BLOCK", "kind": classify_source_failure(exc),
                            "error_type": type(exc).__name__, "message": str(exc)})
    # 盘口抓取是同步两 venue 的原子单元；一边失败就不写半套快照。
    for ticker in tuple(dict.fromkeys(str(item).upper() for item in tickers)):
        try:
            rows = snapshot_fetch(ticker, observed_at_ms=now_ms,
                                  archive_root=archive_root)
            append_snapshots(snapshot_path, rows=rows)
            reports.append({"channel": "cross_exchange_book", "ticker": ticker,
                            "status": "PASS", "venue_rows": len(rows)})
        except Exception as exc:  # noqa: BLE001 — report and continue
            reports.append({"channel": "cross_exchange_book", "ticker": ticker,
                            "status": "BLOCK", "kind": classify_source_failure(exc),
                            "error_type": type(exc).__name__, "message": str(exc)})
    inventory = build_inventory(inventory_root, now_ms=now_ms)
    raw_lineage = summarize_archive(archive_root) if archive_root else None
    status = {
        "schema_version": 1,
        "generated_at_ms": int(now_ms),
        "purpose": "SUPPLEMENTAL_READ_ONLY_DATA_REFRESH",
        "reports": reports,
        "inventory_summary": inventory["summary"],
        "raw_lineage": raw_lineage,
        "paper_live_submission": False,
        "honesty": "supplemental source failure never becomes a synthetic row",
    }
    atomic_write_json(status_path, status, indent=2)
    return status


def main() -> None:
    status = run_supplemental_refresh()
    print("supplemental data refresh:",
          sum(1 for row in status["reports"] if row["status"] == "PASS"),
          "passed /", len(status["reports"]), "attempts")
    print(f"-> {_STATUS}")


if __name__ == "__main__":
    main()
