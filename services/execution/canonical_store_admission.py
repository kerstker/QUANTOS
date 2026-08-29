"""Shared fail-closed admission for selection-bearing execution stores.

Unkeyed manifests and result hashes are tamper evidence, not producer
authority.  Canonical dry-run and paper ledgers may therefore retain a new
record only after the independently controlled backtest-authority verifier has
accepted it.  The verifier is intentionally closed until an external signed
authority is integrated.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.execution.backtest_authority import (
    CANONICAL_ENGINE_SEMANTICS,
    is_authoritative_backtest_record,
)


class CanonicalStoreAdmissionError(RuntimeError):
    """A caller attempted to mutate a canonical store without authority."""


def _sha256(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def require_authoritative_binding(
    authority_record: Mapping[str, Any] | None,
    *,
    run_id: str,
    manifest_hash: str,
    result_hash: str,
) -> None:
    """Require a verifier-admitted record and exact immutable identities.

    A caller-supplied V2-looking mapping is deliberately insufficient.  The
    external authority predicate is evaluated first and remains false until a
    governed signed-receipt verifier is integrated.
    """

    if not isinstance(authority_record, Mapping) or not is_authoritative_backtest_record(
        authority_record
    ):
        raise CanonicalStoreAdmissionError(
            "canonical persistence blocked: externally signed backtest authority "
            "is unavailable or did not admit the record"
        )
    detail = authority_record.get("detail")
    if not isinstance(detail, Mapping):
        raise CanonicalStoreAdmissionError(
            "canonical persistence blocked: authority detail is missing"
        )
    if (
        not isinstance(run_id, str)
        or not run_id
        or authority_record.get("run_id") != run_id
        or not _sha256(manifest_hash)
        or not _sha256(result_hash)
        or detail.get("manifest_hash") != manifest_hash
        or detail.get("result_hash") != result_hash
        or detail.get("engine_semantics") != CANONICAL_ENGINE_SEMANTICS
    ):
        raise CanonicalStoreAdmissionError(
            "canonical persistence blocked: run/manifest/result authority binding "
            "is invalid"
        )


__all__ = [
    "CanonicalStoreAdmissionError",
    "require_authoritative_binding",
]
