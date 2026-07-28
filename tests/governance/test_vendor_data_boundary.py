"""Licensed vendor-data admission has no acquisition or trading capability."""
from __future__ import annotations

import ast
from pathlib import Path

from quantos.contracts.vendor_data import (
    LicensedDataUse,
    VendorAdmissionPolicy,
    VendorDatasetContract,
    VendorDatasetKind,
    VendorDeliveryEvidence,
    VendorLicenseGrant,
)
from services.data.vendor_admission import (
    admit_and_archive_vendor_delivery,
    evaluate_vendor_delivery,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    ROOT / "quantos" / "contracts" / "vendor_data.py",
    ROOT / "services" / "data" / "vendor_admission.py",
    ROOT / "services" / "data" / "vendor_delivery_history.py",
)
FORBIDDEN_ROOTS = {
    "aiohttp",
    "boto3",
    "ccxt",
    "fix",
    "httpx",
    "ibapi",
    "ib_insync",
    "quickfix",
    "requests",
    "socket",
    "urllib",
    "websocket",
    "websockets",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_vendor_admission_imports_no_network_broker_or_cloud_sdk() -> None:
    offenders = []
    for path in SOURCES:
        for module in _imports(path):
            if module.split(".", 1)[0].lower() in FORBIDDEN_ROOTS:
                offenders.append((path.name, module))
    assert offenders == []


def test_no_external_license_verifier_means_no_admission() -> None:
    grant = VendorLicenseGrant(
        grant_id="synthetic-test-only",
        vendor_id="vendor",
        dataset_id="book",
        valid_from_ms=0,
        valid_until_ms=10_000,
        allowed_uses=(LicensedDataUse.RESEARCH,),
        contract_evidence_ref="test://not-external-proof",
        contract_evidence_sha256="a" * 64,
    )
    contract = VendorDatasetContract(
        contract_id="contract",
        vendor_id="vendor",
        dataset_id="book",
        dataset_kind=VendorDatasetKind.ORDER_BOOK,
        allowed_uses=(LicensedDataUse.RESEARCH,),
        required_fields=("bid", "ask"),
        max_delivery_lag_ms=1_000,
        min_history_span_ms=0,
        min_coverage_ratio="1",
        max_missing_ratio="0.01",
        max_duplicate_ratio="0.01",
        min_instrument_count=1,
        min_book_depth=1,
    )
    delivery = VendorDeliveryEvidence(
        delivery_id="delivery",
        vendor_id="vendor",
        dataset_id="book",
        observed_at_ms=100,
        event_time_min_ms=100,
        event_time_max_ms=100,
        knowledge_time_max_ms=100,
        expected_rows=1,
        received_rows=1,
        missing_rows=0,
        duplicate_rows=0,
        out_of_order_rows=0,
        sequence_gap_count=0,
        instrument_count=1,
        minimum_book_depth=1,
        fields=("bid", "ask"),
        content_sha256="b" * 64,
    )
    policy = VendorAdmissionPolicy(
        max_delivery_lag_ms=1_000,
        min_history_span_ms=0,
        min_coverage_ratio="1",
        max_missing_ratio="0.01",
        max_duplicate_ratio="0.01",
        min_instrument_count=1,
        min_book_depth=1,
    )

    decision = evaluate_vendor_delivery(
        grant=grant,
        contract=contract,
        delivery=delivery,
        usage=LicensedDataUse.RESEARCH,
        policy=policy,
        as_of_ms=100,
    )

    assert decision.admitted is False
    assert decision.reasons == ("LICENSE_EVIDENCE_UNVERIFIED",)


def test_archive_entrypoint_requires_delivery_history_authority() -> None:
    import inspect

    parameter = inspect.signature(
        admit_and_archive_vendor_delivery
    ).parameters["history_store"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
