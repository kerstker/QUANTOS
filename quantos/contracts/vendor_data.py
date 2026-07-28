"""Licensed vendor-data contracts.

These values carry no network or credential capability.  They describe the
license grant, dataset service-level contract and one delivered batch so the
data admission layer can make a deterministic, fail-closed decision.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LicensedDataUse(str, Enum):
    RESEARCH = "RESEARCH"
    PAPER = "PAPER"
    LIVE = "LIVE"


class VendorDatasetKind(str, Enum):
    TICK = "TICK"
    ORDER_BOOK = "ORDER_BOOK"


class VendorLicenseGrant(BaseModel):
    model_config = ConfigDict(frozen=True)

    grant_id: str = Field(min_length=1)
    vendor_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    valid_from_ms: int = Field(ge=0)
    valid_until_ms: int = Field(gt=0)
    allowed_uses: tuple[LicensedDataUse, ...] = Field(min_length=1)
    contract_evidence_ref: str = Field(min_length=1)
    contract_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _validate_grant(self) -> "VendorLicenseGrant":
        if self.valid_until_ms <= self.valid_from_ms:
            raise ValueError("valid_until_ms must be after valid_from_ms")
        if len(set(self.allowed_uses)) != len(self.allowed_uses):
            raise ValueError("allowed_uses must not contain duplicates")
        return self


class VendorDatasetContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_id: str = Field(min_length=1)
    vendor_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_kind: VendorDatasetKind
    allowed_uses: tuple[LicensedDataUse, ...] = Field(min_length=1)
    required_fields: tuple[str, ...] = Field(min_length=1)
    max_delivery_lag_ms: int = Field(gt=0)
    min_history_span_ms: int = Field(ge=0)
    min_coverage_ratio: Decimal = Field(gt=0, le=1)
    max_missing_ratio: Decimal = Field(ge=0, lt=1)
    max_duplicate_ratio: Decimal = Field(ge=0, lt=1)
    min_instrument_count: int = Field(ge=1)
    min_book_depth: int = Field(default=0, ge=0)
    max_sequence_gap_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_contract(self) -> "VendorDatasetContract":
        if len(set(self.allowed_uses)) != len(self.allowed_uses):
            raise ValueError("allowed_uses must not contain duplicates")
        if len(set(self.required_fields)) != len(self.required_fields):
            raise ValueError("required_fields must not contain duplicates")
        if any(not field.strip() for field in self.required_fields):
            raise ValueError("required_fields must be non-empty")
        if (
            self.dataset_kind == VendorDatasetKind.ORDER_BOOK
            and self.min_book_depth < 1
        ):
            raise ValueError("ORDER_BOOK contract requires min_book_depth >= 1")
        if self.dataset_kind == VendorDatasetKind.TICK and self.min_book_depth != 0:
            raise ValueError("TICK contract must use min_book_depth=0")
        return self


class VendorDeliveryEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    delivery_id: str = Field(min_length=1)
    vendor_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    observed_at_ms: int = Field(ge=0)
    event_time_min_ms: int = Field(ge=0)
    event_time_max_ms: int = Field(ge=0)
    knowledge_time_max_ms: int = Field(ge=0)
    expected_rows: int = Field(gt=0)
    received_rows: int = Field(ge=0)
    missing_rows: int = Field(ge=0)
    duplicate_rows: int = Field(ge=0)
    out_of_order_rows: int = Field(ge=0)
    sequence_gap_count: int = Field(ge=0)
    instrument_count: int = Field(ge=0)
    minimum_book_depth: int = Field(ge=0)
    fields: tuple[str, ...] = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _validate_delivery(self) -> "VendorDeliveryEvidence":
        if self.event_time_max_ms < self.event_time_min_ms:
            raise ValueError("event_time_max_ms must not precede event_time_min_ms")
        if self.missing_rows > self.expected_rows:
            raise ValueError("missing_rows cannot exceed expected_rows")
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("fields must not contain duplicates")
        if any(not field.strip() for field in self.fields):
            raise ValueError("fields must be non-empty")
        return self


class VendorAdmissionPolicy(BaseModel):
    """Platform minimums; contracts may be stricter but never weaker."""

    model_config = ConfigDict(frozen=True)

    max_delivery_lag_ms: int = Field(gt=0)
    min_history_span_ms: int = Field(ge=0)
    min_coverage_ratio: Decimal = Field(gt=0, le=1)
    max_missing_ratio: Decimal = Field(ge=0, lt=1)
    max_duplicate_ratio: Decimal = Field(ge=0, lt=1)
    min_instrument_count: int = Field(ge=1)
    min_book_depth: int = Field(default=0, ge=0)
    max_sequence_gap_count: int = Field(default=0, ge=0)


__all__ = [
    "LicensedDataUse",
    "VendorAdmissionPolicy",
    "VendorDatasetContract",
    "VendorDatasetKind",
    "VendorDeliveryEvidence",
    "VendorLicenseGrant",
]
