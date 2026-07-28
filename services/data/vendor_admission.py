"""Fail-closed admission for licensed tick and order-book deliveries.

The core is offline and deterministic.  It does not acquire data, parse a legal
contract or decide that a signature is trustworthy.  A caller must provide a
separately deployed ``VendorLicenseVerifier``; absence, exception or rejection
from that verifier denies admission.

This is an L2 readiness seam, not proof that QUANTOS currently owns licensed
data.  The in-process verifier protocol is also not a defense against trusted
code constructing a dishonest verifier; M4 requires an external authority and
retained contractual/SLA evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from typing import Any, Protocol, Sequence, runtime_checkable

from quantos.contracts.vendor_data import (
    LicensedDataUse,
    VendorAdmissionPolicy,
    VendorDatasetContract,
    VendorDatasetKind,
    VendorDeliveryEvidence,
    VendorLicenseGrant,
)
from services.data.raw_archive import archive_response
from services.data.vendor_delivery_history import (
    VendorDeliveryHistoryStore,
    VendorDeliveryRecord,
    vendor_delivery_event_id,
)


@runtime_checkable
class VendorLicenseVerifier(Protocol):
    """External trust seam; implementations verify retained license evidence."""

    def verify(self, grant: VendorLicenseGrant, *, as_of_ms: int) -> bool:
        ...


class DenyAllVendorLicenseVerifier:
    """Safe L2 default: no configured external authority means no admission."""

    def verify(self, grant: VendorLicenseGrant, *, as_of_ms: int) -> bool:
        del grant, as_of_ms
        return False


@dataclass(frozen=True)
class VendorAdmissionDecision:
    admitted: bool
    reasons: tuple[str, ...]
    coverage_ratio: Decimal
    missing_ratio: Decimal
    duplicate_ratio: Decimal
    delivery_lag_ms: int
    history_span_ms: int


@dataclass(frozen=True)
class VendorArchivedDelivery:
    decision: VendorAdmissionDecision
    archive_ref: dict[str, Any]


class VendorDeliveryDenied(RuntimeError):
    """Raised before archive mutation when license, content or SLA is denied."""

    def __init__(self, decision: VendorAdmissionDecision):
        self.decision = decision
        super().__init__("vendor delivery denied: " + ",".join(decision.reasons))


def _ratio(numerator: int, denominator: int) -> Decimal:
    return Decimal(numerator) / Decimal(denominator)


def canonical_vendor_payload(rows: Sequence[dict[str, Any]]) -> bytes:
    """Canonical bytes retained by the archive and bound to delivery evidence."""

    if isinstance(rows, (str, bytes, bytearray)) or not isinstance(rows, Sequence):
        raise TypeError("rows must be a sequence of objects")
    materialized = list(rows)
    if any(not isinstance(row, dict) for row in materialized):
        raise TypeError("every vendor row must be an object")
    try:
        return json.dumps(
            materialized,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "vendor rows must be JSON serializable without non-finite values"
        ) from exc


def vendor_payload_sha256(rows: Sequence[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_vendor_payload(rows)).hexdigest()


def _model_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _history_record(
    *,
    instrument_id: str,
    grant: VendorLicenseGrant,
    contract: VendorDatasetContract,
    delivery: VendorDeliveryEvidence,
    usage: LicensedDataUse,
    policy: VendorAdmissionPolicy,
    as_of_ms: int,
    decision: VendorAdmissionDecision,
    verdict: str,
    archive_artifact_sha256: str = "",
) -> VendorDeliveryRecord:
    return VendorDeliveryRecord(
        event_id=vendor_delivery_event_id(delivery.delivery_id, verdict),
        delivery_id=delivery.delivery_id,
        recorded_at_ms=as_of_ms,
        vendor_id=delivery.vendor_id,
        dataset_id=delivery.dataset_id,
        instrument_id=instrument_id,
        grant_id=grant.grant_id,
        contract_id=contract.contract_id,
        usage=usage.value,
        content_sha256=delivery.content_sha256,
        grant_sha256=_model_sha256(grant),
        contract_sha256=_model_sha256(contract),
        policy_sha256=_model_sha256(policy),
        verdict=verdict,
        reasons=decision.reasons,
        coverage_ratio=str(decision.coverage_ratio),
        missing_ratio=str(decision.missing_ratio),
        duplicate_ratio=str(decision.duplicate_ratio),
        delivery_lag_ms=decision.delivery_lag_ms,
        history_span_ms=decision.history_span_ms,
        archive_artifact_sha256=archive_artifact_sha256,
    )


def _with_additional_reasons(
    decision: VendorAdmissionDecision,
    reasons: tuple[str, ...],
) -> VendorAdmissionDecision:
    return VendorAdmissionDecision(
        admitted=decision.admitted and not reasons,
        reasons=decision.reasons + reasons,
        coverage_ratio=decision.coverage_ratio,
        missing_ratio=decision.missing_ratio,
        duplicate_ratio=decision.duplicate_ratio,
        delivery_lag_ms=decision.delivery_lag_ms,
        history_span_ms=decision.history_span_ms,
    )


def evaluate_vendor_delivery(
    *,
    grant: VendorLicenseGrant,
    contract: VendorDatasetContract,
    delivery: VendorDeliveryEvidence,
    usage: LicensedDataUse,
    policy: VendorAdmissionPolicy,
    as_of_ms: int,
    verifier: VendorLicenseVerifier | None = None,
) -> VendorAdmissionDecision:
    """Evaluate license, PIT integrity, schema, quality, depth and SLA.

    All applicable reasons are returned in a stable order.  ``LIVE`` is
    structurally denied at L2 even when a supplied grant claims to allow it.
    """

    if type(as_of_ms) is not int or as_of_ms < 0:
        raise ValueError("as_of_ms must be a non-negative integer")
    if not isinstance(usage, LicensedDataUse):
        raise TypeError("usage must be LicensedDataUse")
    for name, value, expected in (
        ("grant", grant, VendorLicenseGrant),
        ("contract", contract, VendorDatasetContract),
        ("delivery", delivery, VendorDeliveryEvidence),
        ("policy", policy, VendorAdmissionPolicy),
    ):
        if not isinstance(value, expected):
            raise TypeError(f"{name} must be {expected.__name__}")

    coverage = min(
        Decimal("1"),
        _ratio(delivery.received_rows, delivery.expected_rows),
    )
    missing = _ratio(delivery.missing_rows, delivery.expected_rows)
    duplicate = _ratio(
        delivery.duplicate_rows,
        max(delivery.received_rows, 1),
    )
    delivery_lag = delivery.observed_at_ms - delivery.event_time_max_ms
    history_span = delivery.event_time_max_ms - delivery.event_time_min_ms
    reasons: list[str] = []

    if usage == LicensedDataUse.LIVE:
        reasons.append("L2_LIVE_USE_FORBIDDEN")
    if (
        grant.vendor_id != contract.vendor_id
        or delivery.vendor_id != contract.vendor_id
    ):
        reasons.append("VENDOR_ID_MISMATCH")
    if (
        grant.dataset_id != contract.dataset_id
        or delivery.dataset_id != contract.dataset_id
    ):
        reasons.append("DATASET_ID_MISMATCH")
    if not (grant.valid_from_ms <= as_of_ms < grant.valid_until_ms):
        reasons.append("LICENSE_NOT_ACTIVE")
    if usage not in grant.allowed_uses:
        reasons.append("LICENSE_USE_NOT_GRANTED")
    if usage not in contract.allowed_uses:
        reasons.append("CONTRACT_USE_NOT_ALLOWED")

    effective_verifier = verifier or DenyAllVendorLicenseVerifier()
    try:
        license_verified = bool(
            effective_verifier.verify(grant, as_of_ms=as_of_ms)
        )
    except Exception:
        license_verified = False
    if not license_verified:
        reasons.append("LICENSE_EVIDENCE_UNVERIFIED")

    if delivery.observed_at_ms > as_of_ms:
        reasons.append("FUTURE_DELIVERY")
    if delivery.knowledge_time_max_ms > delivery.observed_at_ms:
        reasons.append("FUTURE_KNOWLEDGE_TIME")
    if delivery.knowledge_time_max_ms < delivery.event_time_max_ms:
        reasons.append("KNOWLEDGE_PRECEDES_EVENT")
    if delivery_lag < 0:
        reasons.append("NEGATIVE_DELIVERY_LAG")

    max_lag = min(contract.max_delivery_lag_ms, policy.max_delivery_lag_ms)
    if delivery_lag > max_lag:
        reasons.append("DELIVERY_LAG_SLA_BREACH")
    min_history = max(
        contract.min_history_span_ms,
        policy.min_history_span_ms,
    )
    if history_span < min_history:
        reasons.append("HISTORY_DEPTH_SLA_BREACH")
    min_coverage = max(
        contract.min_coverage_ratio,
        policy.min_coverage_ratio,
    )
    if coverage < min_coverage:
        reasons.append("COVERAGE_SLA_BREACH")
    max_missing = min(
        contract.max_missing_ratio,
        policy.max_missing_ratio,
    )
    if missing > max_missing:
        reasons.append("MISSINGNESS_SLA_BREACH")
    max_duplicate = min(
        contract.max_duplicate_ratio,
        policy.max_duplicate_ratio,
    )
    if duplicate > max_duplicate:
        reasons.append("DUPLICATE_SLA_BREACH")
    if delivery.out_of_order_rows:
        reasons.append("OUT_OF_ORDER_ROWS")
    max_gaps = min(
        contract.max_sequence_gap_count,
        policy.max_sequence_gap_count,
    )
    if delivery.sequence_gap_count > max_gaps:
        reasons.append("SEQUENCE_GAP_SLA_BREACH")
    required_fields = set(contract.required_fields)
    if not required_fields.issubset(delivery.fields):
        reasons.append("REQUIRED_FIELDS_MISSING")
    min_instruments = max(
        contract.min_instrument_count,
        policy.min_instrument_count,
    )
    if delivery.instrument_count < min_instruments:
        reasons.append("INSTRUMENT_COVERAGE_SLA_BREACH")
    if contract.dataset_kind == VendorDatasetKind.ORDER_BOOK:
        min_depth = max(contract.min_book_depth, policy.min_book_depth)
        if delivery.minimum_book_depth < min_depth:
            reasons.append("ORDER_BOOK_DEPTH_SLA_BREACH")

    return VendorAdmissionDecision(
        admitted=not reasons,
        reasons=tuple(reasons),
        coverage_ratio=coverage,
        missing_ratio=missing,
        duplicate_ratio=duplicate,
        delivery_lag_ms=delivery_lag,
        history_span_ms=history_span,
    )


def admit_and_archive_vendor_delivery(
    rows: Sequence[dict[str, Any]],
    *,
    archive_root: str,
    source_uri: str,
    instrument_id: str,
    grant: VendorLicenseGrant,
    contract: VendorDatasetContract,
    delivery: VendorDeliveryEvidence,
    usage: LicensedDataUse,
    policy: VendorAdmissionPolicy,
    as_of_ms: int,
    history_store: VendorDeliveryHistoryStore,
    verifier: VendorLicenseVerifier | None = None,
) -> VendorArchivedDelivery:
    """Bind admission to exact canonical bytes, then archive once admitted.

    No archive function is called until license/SLA evaluation, row-count
    binding and content-hash binding all pass.
    """

    if not isinstance(history_store, VendorDeliveryHistoryStore):
        raise TypeError("history_store must be a VendorDeliveryHistoryStore")
    materialized = list(rows)
    payload = canonical_vendor_payload(materialized)
    payload_sha256 = hashlib.sha256(payload).hexdigest()
    decision = evaluate_vendor_delivery(
        grant=grant,
        contract=contract,
        delivery=delivery,
        usage=usage,
        policy=policy,
        as_of_ms=as_of_ms,
        verifier=verifier,
    )
    content_reasons: list[str] = []
    if len(materialized) != delivery.received_rows:
        content_reasons.append("DELIVERY_ROW_COUNT_MISMATCH")
    if payload_sha256 != delivery.content_sha256:
        content_reasons.append("DELIVERY_CONTENT_HASH_MISMATCH")
    decision = _with_additional_reasons(decision, tuple(content_reasons))
    if not decision.admitted:
        history_store.append(
            _history_record(
                instrument_id=instrument_id,
                grant=grant,
                contract=contract,
                delivery=delivery,
                usage=usage,
                policy=policy,
                as_of_ms=as_of_ms,
                decision=decision,
                verdict="DENIED",
            ),
            expected_head=history_store.head(),
        )
        raise VendorDeliveryDenied(decision)

    history_store.append(
        _history_record(
            instrument_id=instrument_id,
            grant=grant,
            contract=contract,
            delivery=delivery,
            usage=usage,
            policy=policy,
            as_of_ms=as_of_ms,
            decision=decision,
            verdict="ARCHIVE_PENDING",
        ),
        expected_head=history_store.head(),
    )
    archive_ref = archive_response(
        root=archive_root,
        payload=payload,
        source_uri=source_uri,
        provider=contract.vendor_id,
        dataset_id=contract.dataset_id,
        instrument_id=instrument_id,
        observed_at_ms=delivery.observed_at_ms,
        representation="CANONICAL_JSON_UTF8",
        parse_diagnostics={
            "status": "ACCEPTED",
            "admission": "LICENSE_AND_SLA_VERIFIED",
            "grant_id": grant.grant_id,
            "contract_id": contract.contract_id,
            "delivery_id": delivery.delivery_id,
            "licensed_use": usage.value,
            "input_rows": len(materialized),
            "accepted_rows": len(materialized),
            "rejected_rows": 0,
            "duplicate_rows": delivery.duplicate_rows,
            "sequence_gap_count": delivery.sequence_gap_count,
        },
    )
    if archive_ref["artifact_sha256"] != delivery.content_sha256:
        raise RuntimeError("archived vendor payload digest mismatch")
    history_store.append(
        _history_record(
            instrument_id=instrument_id,
            grant=grant,
            contract=contract,
            delivery=delivery,
            usage=usage,
            policy=policy,
            as_of_ms=as_of_ms,
            decision=decision,
            verdict="ADMITTED",
            archive_artifact_sha256=archive_ref["artifact_sha256"],
        ),
        expected_head=history_store.head(),
    )
    return VendorArchivedDelivery(decision=decision, archive_ref=archive_ref)


__all__ = [
    "DenyAllVendorLicenseVerifier",
    "VendorArchivedDelivery",
    "VendorAdmissionDecision",
    "VendorDeliveryDenied",
    "VendorLicenseVerifier",
    "admit_and_archive_vendor_delivery",
    "canonical_vendor_payload",
    "evaluate_vendor_delivery",
    "vendor_payload_sha256",
]
