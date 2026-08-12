from __future__ import annotations

import json

from services.data.binance_book_depth_archive import DATASET, DAY_MS, HOUR_MS
from services.data.binance_systemic_store import BinanceSystemicStore
from tools import binance_book_depth_backfill as backfill
from tools.binance_book_depth_fetch import BinanceBookDepthQualityRejected


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


def _quality_policy(maximum: int = 2) -> dict:
    return {
        "status": "FROZEN_BEFORE_CONFIRMATION_OUTCOME",
        "confirmation_study_id": "confirmation-test",
        "maximum_quality_excluded_days": maximum,
        "exclusion_unit": "ENTIRE_UTC_DAY_ALL_14_INSTRUMENTS",
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
        quality_policy=_quality_policy(),
    )

    assert calls == [DAY, DAY + DAY_MS]
    assert result["status"] == "PASS"
    assert result["mode"] == "L2_CONTRACT_BOUND_UNSEEN_CONFIRMATION_BACKFILL"
    assert result["preexisting_confirmation_observations"] == 0
    assert result["processed_day_count"] == 2
    assert json.loads((tmp_path / "status.json").read_text(encoding="utf-8")) == result


def test_contract_backfill_accepts_the_same_frozen_universe_in_contract_order(
    tmp_path,
):
    store_path = str(tmp_path / "depth.db")
    seed = BinanceSystemicStore(store_path, create=True)
    try:
        seed.append_many(
            [{
                "dataset": "prior",
                "instrument_id": "SYSTEM",
                "event_time_ms": 1,
                "knowledge_time_ms": 1,
                "value": 1,
            }]
        )
        head = seed.authority_head()
    finally:
        seed.close()
    contract = _confirmation_contract(DAY, OBSERVED)
    contract["universe"] = list(reversed(contract["universe"]))
    contract["frozen_unseen_window_evidence"]["authority_head_seq"] = head["seq"]
    contract["frozen_unseen_window_evidence"]["authority_head_row_hash"] = head["row_hash"]

    def refresh(instruments, days, **kwargs):
        assert tuple(instruments) == tuple(contract["universe"])
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
        status_path=str(tmp_path / "status.json"),
        refresh=refresh,
        quality_policy=_quality_policy(),
    )

    assert result["status"] == "PASS"


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
            quality_policy=_quality_policy(),
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

    policy_digest, policy = backfill._verify_confirmation_quality_policy(
        backfill.DEFAULT_CONFIRMATION_QUALITY_POLICY
    )
    assert len(policy_digest) == 64
    assert policy["maximum_quality_excluded_days"] == 4

    v2_contract_digest, v2_contract, v2_policy_digest, v2_policy = (
        backfill._verify_confirmation_v2_contracts()
    )
    assert len(v2_contract_digest) == len(v2_policy_digest) == 64
    assert v2_contract["schema_version"] == 2
    assert v2_contract["frozen_unseen_window_evidence"]["dataset_observation_count"] == 0
    assert v2_policy["maximum_quality_excluded_days"] == 12


def test_backfill_excludes_only_bounded_structured_quality_rejections(tmp_path):
    store_path = str(tmp_path / "depth.db")
    calls = []

    def refresh(instruments, days, **kwargs):
        calls.append(days[0])
        if len(calls) == 1:
            raise BinanceBookDepthQualityRejected(
                ({
                    "instrument_id": instruments[0],
                    "day_ms": days[0],
                    "day": "test-day",
                    "reason": "below daily quality floor",
                    "book_depth_checksum_verified": True,
                    "mark_price_checksum_verified": True,
                },)
            )
        store = BinanceSystemicStore(kwargs["store_path"], create=True)
        try:
            inserted = store.append_many(_rows(days[0], kwargs["observed_at_ms"]))
        finally:
            store.close()
        return {"inserted_hours": inserted, "external_price_alignment": "PASS"}

    result = backfill.run_backfill(
        days=2,
        closed_day_ms=DAY + DAY_MS,
        observed_at_ms=OBSERVED,
        store_path=store_path,
        status_path=str(tmp_path / "status.json"),
        refresh=refresh,
        maximum_quality_excluded_days=1,
    )

    assert result["status"] == "PASS"
    assert result["quality_excluded_day_count"] == 1
    assert result["quality_exclusions"][0]["day_ms"] == DAY
    assert result["processed_day_count"] == 2


def test_backfill_fifth_quality_rejection_blocks(tmp_path):
    def reject(instruments, days, **_kwargs):
        raise BinanceBookDepthQualityRejected(
            ({
                "instrument_id": instruments[0],
                "day_ms": days[0],
                "day": "test-day",
                "reason": "quality rejection",
                "book_depth_checksum_verified": True,
                "mark_price_checksum_verified": True,
            },)
        )

    with __import__("pytest").raises(
        backfill.BookDepthBackfillError, match="exclusion limit"
    ):
        backfill.run_backfill(
            days=5,
            closed_day_ms=DAY + 4 * DAY_MS,
            observed_at_ms=OBSERVED,
            store_path=str(tmp_path / "depth.db"),
            status_path=str(tmp_path / "status.json"),
            refresh=reject,
            maximum_quality_excluded_days=4,
        )

    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["failure_type"] == "QUALITY_EXCLUSION_LIMIT_EXCEEDED"
    assert status["quality_excluded_day_count"] == 5
