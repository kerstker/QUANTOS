from __future__ import annotations

import json

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


def _confirmation_contract(start_day: int, frozen_at: int) -> dict:
    return {
        "study_id": "confirmation-test",
        "status": "PRE_REGISTERED_BEFORE_CONFIRMATION_DATA_ACCESS",
        "dataset_id": DATASET,
        "universe": list(backfill.UNIVERSE),
        "confirmation_window": {
            "start_day_ms": start_day,
            "end_exclusive_ms": start_day + 2 * DAY_MS,
            "day_count": 2,
            "relation_to_exploration": "STRICTLY_EARLIER_NON_OVERLAPPING",
            "exploration_start_exclusive_ms": start_day + 2 * DAY_MS,
        },
        "frozen_unseen_window_evidence": {
            "observed_at_ms": frozen_at,
            "dataset_observation_count": 0,
            "authority_head_seq": 1,
            "authority_head_row_hash": "",
            "authority_chain_valid": True,
        },
    }


def test_contract_backfill_requires_frozen_empty_head_then_exact_window(tmp_path):
    store_path = str(tmp_path / "depth.db")
    seed = BinanceSystemicStore(store_path, create=True)
    try:
        seed.append_many(
            [
                {
                    "dataset": "prior",
                    "instrument_id": "SYSTEM",
                    "event_time_ms": 1,
                    "knowledge_time_ms": 1,
                    "value": 1,
                }
            ]
        )
        head = seed.authority_head()
    finally:
        seed.close()
    contract = _confirmation_contract(DAY, OBSERVED)
    contract["frozen_unseen_window_evidence"]["authority_head_seq"] = head["seq"]
    contract["frozen_unseen_window_evidence"]["authority_head_row_hash"] = head["row_hash"]
    calls = []

    def refresh(instruments, days, **kwargs):
        calls.append(days[0])
        store = BinanceSystemicStore(kwargs["store_path"])
        try:
            inserted = store.append_many(_rows(days[0], kwargs["observed_at_ms"]))
        finally:
            store.close()
        return {"inserted_hours": inserted, "external_price_alignment": "PASS"}

    result = backfill.run_contract_backfill(
        contract=contract,
        observed_at_ms=OBSERVED + 1,
        store_path=store_path,
        archive_root=str(tmp_path / "raw"),
        status_path=str(tmp_path / "status.json"),
        max_workers=1,
        refresh=refresh,
    )

    assert calls == [DAY, DAY + DAY_MS]
    assert result["status"] == "PASS"
    assert result["mode"] == "L2_CONTRACT_BOUND_UNSEEN_CONFIRMATION_BACKFILL"
    assert result["preexisting_confirmation_observations"] == 0
    assert result["processed_day_count"] == 2
    assert json.loads((tmp_path / "status.json").read_text(encoding="utf-8")) == result


def test_contract_backfill_rejects_pre_freeze_observations(tmp_path):
    store_path = str(tmp_path / "depth.db")
    store = BinanceSystemicStore(store_path, create=True)
    try:
        store.append_many(_rows(DAY, OBSERVED - 1))
    finally:
        store.close()
    contract = _confirmation_contract(DAY, OBSERVED)

    try:
        backfill.run_contract_backfill(
            contract=contract,
            observed_at_ms=OBSERVED + 1,
            store_path=store_path,
            status_path=str(tmp_path / "status.json"),
        )
    except backfill.BookDepthBackfillError as exc:
        assert "observed before freeze" in str(exc)
    else:
        raise AssertionError("pre-freeze confirmation rows must be rejected")


def test_real_confirmation_contract_matches_immutable_commit() -> None:
    digest, payload = backfill._verify_confirmation_contract(
        backfill.DEFAULT_CONFIRMATION_CONTRACT
    )

    assert len(digest) == 64
    assert payload["status"] == "PRE_REGISTERED_BEFORE_CONFIRMATION_DATA_ACCESS"
    assert payload["frozen_unseen_window_evidence"]["dataset_observation_count"] == 0
