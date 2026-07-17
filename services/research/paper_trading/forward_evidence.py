from __future__ import annotations

from dataclasses import dataclass
from typing import Collection

from services.data.admission_store import DataQualityAdmissionStore


class ForwardEvidenceError(RuntimeError):
    """Forward evidence is missing, conflicting, blocked, or unverifiable."""


FORWARD_L2_DECISION_SOURCE = "FORWARD_L2_ATTESTED_DECISION"
FORWARD_L2_REALIZATION_SOURCE = "FORWARD_L2_ATTESTED_PERP_MARK_GROSS_PLUS_FUNDING"


@dataclass(frozen=True)
class ForwardAdmissionRef:
    admission_id: str
    evidence_sha256: str
    decision_hash: str
    recorded_at_ms: int
    price_basis: str
    instrument_scope: tuple[str, ...]


def load_forward_admission_ref(
    store: DataQualityAdmissionStore,
    *,
    admission_id: str,
    evidence_sha256: str,
    decision_hash: str,
    instruments: Collection[str],
) -> ForwardAdmissionRef:
    if not store.chain_valid():
        raise ForwardEvidenceError("data-admission authority chain is invalid")
    decision = next(
        (item for item in store.decisions() if item.admission_id == admission_id),
        None,
    )
    if decision is None:
        raise ForwardEvidenceError("data-admission decision is unavailable")
    if decision.evidence_sha256 != evidence_sha256 or decision.decision_hash != decision_hash:
        raise ForwardEvidenceError("data-admission digest binding is inconsistent")
    if (
        decision.readiness != "L2_DRY_RUN_ONLY"
        or decision.purpose != "FORWARD_EVIDENCE_DATA_ADMISSION"
        or decision.verdict != "PASS"
        or decision.quality_verdict != "PASS"
        or decision.lineage_verdict != "PASS"
        or decision.price_basis != "PERP_MARK_PRICE"
    ):
        raise ForwardEvidenceError("data-admission decision is not PASS for L2 forward evidence")
    required = {str(value) for value in instruments}
    if not required or not required.issubset(set(decision.instrument_scope)):
        raise ForwardEvidenceError("decision instruments are outside the admitted scope")
    return ForwardAdmissionRef(
        admission_id=decision.admission_id,
        evidence_sha256=decision.evidence_sha256,
        decision_hash=decision.decision_hash,
        recorded_at_ms=decision.recorded_at_ms,
        price_basis=decision.price_basis,
        instrument_scope=decision.instrument_scope,
    )
