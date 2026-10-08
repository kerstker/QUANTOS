"""Licensed vendor-data admission remains deterministic and fail-closed."""
from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from quantos.contracts.vendor_data import (
    LicensedDataUse,
    VendorAdmissionPolicy,
    VendorDatasetContract,
    VendorDatasetKind,
    VendorDeliveryEvidence,
    VendorLicenseGrant,
)
from services.data.vendor_admission import evaluate_vendor_delivery
from services.data.vendor_admission import (
    VendorDeliveryDenied,
    admit_and_archive_vendor_delivery,
    vendor_payload_sha256,
)
from services.data.raw_archive import summarize_archive
from services.data.vendor_delivery_history import VendorDeliveryHistoryStore


D = Decimal
DAY = 86_400_000


class _Verifier:
    def __init__(self, result: bool = True, *, raises: bool = False):
        self.result = result
        self.raises = raises

    def verify(self, grant: VendorLicenseGrant, *, as_of_ms: int) -> bool:
        del grant, as_of_ms
        if self.raises:
            raise RuntimeError("external verifier unavailable")
        return self.result


def _grant(**overrides) -> VendorLicenseGrant:
    values = {
        "grant_id": "grant-1",
        "vendor_id": "licensed-vendor",
        "dataset_id": "ticks-l2",
        "valid_from_ms": 1_000,
        "valid_until_ms": 10_000_000_000,
        "allowed_uses": (LicensedDataUse.RESEARCH, LicensedDataUse.PAPER),
        "contract_evidence_ref": "external://contracts/grant-1",
        "contract_evidence_sha256": "a" * 64,
    }
    values.update(overrides)
    return VendorLicenseGrant(**values)


def _contract(**overrides) -> VendorDatasetContract:
    values = {
        "contract_id": "contract-1",
        "vendor_id": "licensed-vendor",
        "dataset_id": "ticks-l2",
        "dataset_kind": VendorDatasetKind.ORDER_BOOK,
        "allowed_uses": (LicensedDataUse.RESEARCH, LicensedDataUse.PAPER),
        "required_fields": (
            "event_time",
            "knowledge_time",
            "sequence",
            "bid_price",
            "bid_size",
            "ask_price",
            "ask_size",
        ),
        "max_delivery_lag_ms": 5_000,
        "min_history_span_ms": 30 * DAY,
        "min_coverage_ratio": D("0.995"),
        "max_missing_ratio": D("0.005"),
        "max_duplicate_ratio": D("0.001"),
        "min_instrument_count": 100,
        "min_book_depth": 10,
        "max_sequence_gap_count": 0,
    }
    values.update(overrides)
    return VendorDatasetContract(**values)


def _policy(**overrides) -> VendorAdmissionPolicy:
    values = {
        "max_delivery_lag_ms": 10_000,
        "min_history_span_ms": 20 * DAY,
        "min_coverage_ratio": D("0.99"),
        "max_missing_ratio": D("0.01"),
        "max_duplicate_ratio": D("0.002"),
        "min_instrument_count": 50,
        "min_book_depth": 5,
        "max_sequence_gap_count": 0,
    }
    values.update(overrides)
    return VendorAdmissionPolicy(**values)


def _delivery(**overrides) -> VendorDeliveryEvidence:
    event_max = 4_000_000_000
    values = {
        "delivery_id": "delivery-1",
        "vendor_id": "licensed-vendor",
        "dataset_id": "ticks-l2",
        "observed_at_ms": event_max + 1_000,
        "event_time_min_ms": event_max - 31 * DAY,
        "event_time_max_ms": event_max,
        "knowledge_time_max_ms": event_max + 500,
        "expected_rows": 1_000_000,
        "received_rows": 999_500,
        "missing_rows": 500,
        "duplicate_rows": 10,
        "out_of_order_rows": 0,
        "sequence_gap_count": 0,
        "instrument_count": 120,
        "minimum_book_depth": 20,
        "fields": _contract().required_fields,
        "content_sha256": "b" * 64,
    }
    values.update(overrides)
    return VendorDeliveryEvidence(**values)


def _evaluate(**overrides):
    values = {
        "grant": _grant(),
        "contract": _contract(),
        "delivery": _delivery(),
        "usage": LicensedDataUse.RESEARCH,
        "policy": _policy(),
        "as_of_ms": 4_000_002_000,
        "verifier": _Verifier(),
    }
    values.update(overrides)
    return evaluate_vendor_delivery(**values)


def test_compliant_externally_verified_delivery_is_admitted() -> None:
    decision = _evaluate()

    assert decision.admitted is True
    assert decision.reasons == ()
    assert decision.coverage_ratio == D("0.9995")
    assert decision.missing_ratio == D("0.0005")
    assert decision.delivery_lag_ms == 1_000


@pytest.mark.parametrize("verifier", [None, _Verifier(False), _Verifier(raises=True)])
def test_missing_rejected_or_unavailable_license_verifier_denies(verifier) -> None:
    decision = _evaluate(verifier=verifier)

    assert decision.admitted is False
    assert "LICENSE_EVIDENCE_UNVERIFIED" in decision.reasons


def test_live_use_is_structurally_denied_even_if_claimed_by_grant() -> None:
    live_grant = _grant(
        allowed_uses=(
            LicensedDataUse.RESEARCH,
            LicensedDataUse.PAPER,
            LicensedDataUse.LIVE,
        )
    )
    live_contract = _contract(
        allowed_uses=(
            LicensedDataUse.RESEARCH,
            LicensedDataUse.PAPER,
            LicensedDataUse.LIVE,
        )
    )

    decision = _evaluate(
        grant=live_grant,
        contract=live_contract,
        usage=LicensedDataUse.LIVE,
    )

    assert decision.admitted is False
    assert decision.reasons == ("L2_LIVE_USE_FORBIDDEN",)


def test_contract_and_platform_policy_compose_using_stricter_threshold() -> None:
    delivery = _delivery(
        observed_at_ms=4_000_006_000,
        event_time_min_ms=4_000_000_000 - 25 * DAY,
        expected_rows=1_000,
        received_rows=994,
        missing_rows=6,
        duplicate_rows=3,
        instrument_count=80,
        minimum_book_depth=8,
    )

    decision = _evaluate(
        delivery=delivery,
        policy=_policy(
            max_delivery_lag_ms=4_000,
            min_history_span_ms=40 * DAY,
            min_coverage_ratio=D("0.999"),
            max_missing_ratio=D("0.004"),
            max_duplicate_ratio=D("0.001"),
            min_instrument_count=150,
            min_book_depth=25,
        ),
        as_of_ms=4_000_007_000,
    )

    assert decision.admitted is False
    assert decision.reasons == (
        "DELIVERY_LAG_SLA_BREACH",
        "HISTORY_DEPTH_SLA_BREACH",
        "COVERAGE_SLA_BREACH",
        "MISSINGNESS_SLA_BREACH",
        "DUPLICATE_SLA_BREACH",
        "INSTRUMENT_COVERAGE_SLA_BREACH",
        "ORDER_BOOK_DEPTH_SLA_BREACH",
    )


def test_identity_use_schema_ordering_and_sequence_fail_closed() -> None:
    decision = _evaluate(
        grant=_grant(vendor_id="other", allowed_uses=(LicensedDataUse.PAPER,)),
        delivery=_delivery(
            dataset_id="other-dataset",
            out_of_order_rows=1,
            sequence_gap_count=2,
            fields=("event_time",),
        ),
    )

    assert decision.admitted is False
    assert decision.reasons == (
        "VENDOR_ID_MISMATCH",
        "DATASET_ID_MISMATCH",
        "LICENSE_USE_NOT_GRANTED",
        "OUT_OF_ORDER_ROWS",
        "SEQUENCE_GAP_SLA_BREACH",
        "REQUIRED_FIELDS_MISSING",
    )


def test_pit_and_temporal_violations_fail_closed() -> None:
    event_max = 4_000_000_000
    decision = _evaluate(
        delivery=_delivery(
            observed_at_ms=event_max - 1,
            event_time_min_ms=event_max - 10 * DAY,
            event_time_max_ms=event_max,
            knowledge_time_max_ms=event_max + 1,
        ),
        as_of_ms=event_max - 2,
    )

    assert decision.admitted is False
    assert decision.reasons[:4] == (
        "FUTURE_DELIVERY",
        "FUTURE_KNOWLEDGE_TIME",
        "NEGATIVE_DELIVERY_LAG",
        "HISTORY_DEPTH_SLA_BREACH",
    )


def test_expired_license_and_contract_usage_are_both_reported() -> None:
    decision = _evaluate(
        grant=_grant(
            valid_until_ms=4_000_001_000,
            allowed_uses=(LicensedDataUse.RESEARCH,),
        ),
        contract=_contract(allowed_uses=(LicensedDataUse.RESEARCH,)),
        usage=LicensedDataUse.PAPER,
    )

    assert decision.admitted is False
    assert decision.reasons[:3] == (
        "LICENSE_NOT_ACTIVE",
        "LICENSE_USE_NOT_GRANTED",
        "CONTRACT_USE_NOT_ALLOWED",
    )


@pytest.mark.parametrize(
    "factory, message",
    [
        (
            lambda: _grant(valid_from_ms=10, valid_until_ms=10),
            "valid_until_ms must be after",
        ),
        (
            lambda: _contract(min_book_depth=0),
            "ORDER_BOOK contract requires",
        ),
        (
            lambda: _contract(
                dataset_kind=VendorDatasetKind.TICK,
                min_book_depth=1,
            ),
            "TICK contract must use",
        ),
        (
            lambda: _delivery(event_time_min_ms=20, event_time_max_ms=10),
            "event_time_max_ms must not precede",
        ),
    ],
)
def test_invalid_contract_values_are_rejected(factory, message) -> None:
    with pytest.raises(ValidationError, match=message):
        factory()


def _licensed_rows() -> list[dict]:
    return [
        {
            "event_time": 4_000_000_000,
            "knowledge_time": 4_000_000_001,
            "sequence": 1,
            "bid_price": "100.0",
            "bid_size": "2.0",
            "ask_price": "100.1",
            "ask_size": "3.0",
        },
        {
            "event_time": 4_000_000_001,
            "knowledge_time": 4_000_000_002,
            "sequence": 2,
            "bid_price": "100.1",
            "bid_size": "1.0",
            "ask_price": "100.2",
            "ask_size": "4.0",
        },
    ]


def _archive_delivery(rows: list[dict], **overrides) -> VendorDeliveryEvidence:
    values = _delivery(
        expected_rows=len(rows),
        received_rows=len(rows),
        missing_rows=0,
        duplicate_rows=0,
        content_sha256=vendor_payload_sha256(rows),
    ).model_dump()
    values.update(overrides)
    return VendorDeliveryEvidence(**values)


def test_admitted_batch_is_content_bound_and_archived_idempotently(tmp_path) -> None:
    rows = _licensed_rows()
    history = VendorDeliveryHistoryStore(
        str(tmp_path / "vendor-history.db"),
        create=True,
    )
    kwargs = {
        "archive_root": str(tmp_path),
        "source_uri": "https://licensed.example.test/order-book/BTC",
        "instrument_id": "BTC-USDT",
        "grant": _grant(),
        "contract": _contract(),
        "delivery": _archive_delivery(rows),
        "usage": LicensedDataUse.RESEARCH,
        "policy": _policy(),
        "as_of_ms": 4_000_002_000,
        "history_store": history,
        "verifier": _Verifier(),
    }

    first = admit_and_archive_vendor_delivery(rows, **kwargs)
    second = admit_and_archive_vendor_delivery(rows, **kwargs)

    assert first == second
    assert first.decision.admitted is True
    assert first.archive_ref["artifact_sha256"] == vendor_payload_sha256(rows)
    summary = summarize_archive(str(tmp_path))
    assert summary["status"] == "PASS"
    assert summary["observation_count"] == 1
    assert summary["providers"] == ["licensed-vendor"]
    assert summary["datasets"] == {"ticks-l2": 1}
    assert [record.verdict for record in history.records()] == [
        "ARCHIVE_PENDING",
        "ADMITTED",
    ]
    history.close()


@pytest.mark.parametrize(
    "mutation, expected_reason",
    [
        (
            lambda rows: _archive_delivery(
                rows,
                received_rows=len(rows) + 1,
            ),
            "DELIVERY_ROW_COUNT_MISMATCH",
        ),
        (
            lambda rows: _archive_delivery(
                rows,
                content_sha256="c" * 64,
            ),
            "DELIVERY_CONTENT_HASH_MISMATCH",
        ),
    ],
)
def test_row_count_or_content_tampering_is_denied_before_archive_write(
    tmp_path, mutation, expected_reason
) -> None:
    rows = _licensed_rows()
    history = VendorDeliveryHistoryStore(
        str(tmp_path / "vendor-history.db"),
        create=True,
    )

    with pytest.raises(VendorDeliveryDenied) as caught:
        admit_and_archive_vendor_delivery(
            rows,
            archive_root=str(tmp_path),
            source_uri="https://licensed.example.test/order-book/BTC",
            instrument_id="BTC-USDT",
            grant=_grant(),
            contract=_contract(),
            delivery=mutation(rows),
            usage=LicensedDataUse.RESEARCH,
            policy=_policy(),
            as_of_ms=4_000_002_000,
            history_store=history,
            verifier=_Verifier(),
        )

    assert expected_reason in caught.value.decision.reasons
    assert history.records()[0].verdict == "DENIED"
    assert history.records()[0].archive_artifact_sha256 == ""
    history.close()


def test_license_rejection_happens_before_archive_write(tmp_path) -> None:
    rows = _licensed_rows()
    history = VendorDeliveryHistoryStore(
        str(tmp_path / "vendor-history.db"),
        create=True,
    )

    with pytest.raises(VendorDeliveryDenied) as caught:
        admit_and_archive_vendor_delivery(
            rows,
            archive_root=str(tmp_path),
            source_uri="https://licensed.example.test/order-book/BTC",
            instrument_id="BTC-USDT",
            grant=_grant(),
            contract=_contract(),
            delivery=_archive_delivery(rows),
            usage=LicensedDataUse.RESEARCH,
            policy=_policy(),
            as_of_ms=4_000_002_000,
            history_store=history,
            verifier=None,
        )

    assert caught.value.decision.reasons == ("LICENSE_EVIDENCE_UNVERIFIED",)
    assert history.records()[0].reasons == ("LICENSE_EVIDENCE_UNVERIFIED",)
    history.close()


def test_nonfinite_or_non_object_rows_are_rejected_before_archive_write(
    tmp_path,
) -> None:
    history = VendorDeliveryHistoryStore(
        str(tmp_path / "vendor-history.db"),
        create=True,
    )
    with pytest.raises(TypeError, match="every vendor row"):
        admit_and_archive_vendor_delivery(
            [1],  # type: ignore[list-item]
            archive_root=str(tmp_path),
            source_uri="https://licensed.example.test/order-book/BTC",
            instrument_id="BTC-USDT",
            grant=_grant(),
            contract=_contract(),
            delivery=_delivery(),
            usage=LicensedDataUse.RESEARCH,
            policy=_policy(),
            as_of_ms=4_000_002_000,
            history_store=history,
            verifier=_Verifier(),
        )
    assert history.records() == ()
    history.close()


def test_archive_crash_leaves_pending_and_exact_retry_completes_history(
    tmp_path, monkeypatch
) -> None:
    import services.data.vendor_admission as module

    rows = _licensed_rows()
    history = VendorDeliveryHistoryStore(
        str(tmp_path / "vendor-history.db"),
        create=True,
    )
    original_archive = module.archive_response
    kwargs = {
        "archive_root": str(tmp_path / "raw"),
        "source_uri": "https://licensed.example.test/order-book/BTC",
        "instrument_id": "BTC-USDT",
        "grant": _grant(),
        "contract": _contract(),
        "delivery": _archive_delivery(rows),
        "usage": LicensedDataUse.RESEARCH,
        "policy": _policy(),
        "as_of_ms": 4_000_002_000,
        "history_store": history,
        "verifier": _Verifier(),
    }

    def crash(**_kwargs):
        raise OSError("simulated archive interruption")

    monkeypatch.setattr(module, "archive_response", crash)
    with pytest.raises(OSError, match="archive interruption"):
        admit_and_archive_vendor_delivery(rows, **kwargs)
    assert [record.verdict for record in history.records()] == [
        "ARCHIVE_PENDING"
    ]

    monkeypatch.setattr(module, "archive_response", original_archive)
    result = admit_and_archive_vendor_delivery(rows, **kwargs)
    assert result.decision.admitted is True
    assert [record.verdict for record in history.records()] == [
        "ARCHIVE_PENDING",
        "ADMITTED",
    ]
    history.close()
