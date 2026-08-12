from __future__ import annotations

from services.data.binance_book_depth_archive import DATASET, DAY_MS, HOUR_MS
from services.data.binance_systemic_store import BinanceSystemicStore
from tools import binance_book_depth_backfill as backfill


DAY = 1_786_320_000_000
OBSERVED = DAY + 10 * DAY_MS


def _rows(day_ms: int, observed_at_ms: int):
    for instrument in backfill.UNIVERSE:
        for hour in range(1, 25):
            yield {
                "dataset": DATASET,
                "instrument_id": instrument,
                "event_time_ms": day_ms + hour * HOUR_MS,
                "knowledge_time_ms": observed_at_ms,
                "external_price_alignment": "PASS",
                "value": 1,
            }


def test_backfill_is_resumable_and_skips_only_exact_complete_days(tmp_path):
    store_path = str(tmp_path / "depth.db")
    status_path = str(tmp_path / "status.json")
    calls = []

    def refresh(instruments, days, **kwargs):
        calls.append((tuple(instruments), tuple(days)))
        store = BinanceSystemicStore(kwargs["store_path"], create=True)
        try:
            inserted = store.append_many(_rows(days[0], kwargs["observed_at_ms"]))
        finally:
            store.close()
        return {"inserted_hours": inserted, "external_price_alignment": "PASS"}

    result = backfill.run_backfill(
        days=2,
        closed_day_ms=DAY,
        observed_at_ms=OBSERVED,
        store_path=store_path,
        archive_root=str(tmp_path / "raw"),
        status_path=status_path,
        max_workers=1,
        refresh=refresh,
    )
    replay = backfill.run_backfill(
        days=2,
        closed_day_ms=DAY,
        observed_at_ms=OBSERVED + 1,
        store_path=store_path,
        archive_root=str(tmp_path / "raw"),
        status_path=status_path,
        max_workers=1,
        refresh=refresh,
    )

    assert result["status"] == "PASS"
    assert result["completed_days"] == 2
    assert result["inserted_hours"] == 672
    assert replay["completed_days"] == 0
    assert replay["skipped_complete_days"] == 2
    assert len(calls) == 2


def test_backfill_stops_on_first_failed_day_and_records_failure(tmp_path):
    status_path = tmp_path / "status.json"

    def fail(*_args, **_kwargs):
        raise RuntimeError("source unavailable")

    try:
        backfill.run_backfill(
            days=2,
            closed_day_ms=DAY,
            observed_at_ms=OBSERVED,
            store_path=str(tmp_path / "depth.db"),
            archive_root=str(tmp_path / "raw"),
            status_path=str(status_path),
            max_workers=1,
            refresh=fail,
        )
    except RuntimeError as exc:
        assert str(exc) == "source unavailable"
    else:
        raise AssertionError("backfill failure must propagate")

    payload = __import__("json").loads(status_path.read_text(encoding="utf-8"))
    assert payload["status"] == "FAIL"
    assert payload["completed_days"] == 0
    assert payload["formal_forward_credit"] is False
