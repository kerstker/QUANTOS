"""Exact-v3 Pass C C9 public-artifact publication contracts.

The values in this module form an intentionally acyclic exact-byte graph:

``ArtifactCore -> ArtifactCoreScanReceipt -> PublishedArtifactEnvelope``
``-> ArtifactPublicationReceipt -> ArtifactPublicationReceiptProjection``.

``DataSnoopingEvidenceRef`` binds the two independently content-addressed
artifacts after both byte sequences exist.  C8 grid history and the unique C8
family conclusion remain separate evidence identities throughout this graph.
None of these contracts grants promotion, order-submission, broker, credential,
or network authority.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from math import fsum, isfinite
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical import (
    canonical_json_bytes,
    canonical_sha256,
    float64_hex,
    load_canonical_json_bytes,
    parse_float64_hex,
    sha256_bytes,
)
from .data_snooping_projection_layouts import AUTHORITY_ROW_LAYOUTS


_NonEmptyText = Annotated[str, Field(min_length=1)]
_Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_PositiveEpochUs = Annotated[int, Field(gt=0)]
_PositiveOrdinal = Annotated[int, Field(gt=0)]
_CanonicalMediaType = Annotated[
    str,
    Field(
        min_length=3,
        max_length=127,
        pattern=(
            r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,62}/"
            r"[a-z0-9][a-z0-9!#$&^_.+-]{0,62}\z"
        ),
    ),
]
_PublicToken = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9][A-Z0-9_.-]{0,63}\z"),
]
_QuarantineId = Annotated[
    str,
    Field(min_length=36, max_length=36, pattern=r"^qtn_[0-9a-f]{32}\z"),
]
_LengthBucket = Literal[
    "LE_1_KIB",
    "GT_1_LE_4_KIB",
    "GT_4_LE_16_KIB",
    "GT_16_LE_64_KIB",
    "GT_64_LE_256_KIB",
    "GT_256_LE_1024_KIB",
    "GT_1_MIB",
]
_AssessmentStatus = Literal["ASSESSED", "NOT_ASSESSABLE"]
_ClosureStatus = Literal["COMPLETE", "NOT_ASSESSABLE"]
_DiagnosticVerdict = Literal[
    "SUPPORTS_SUPERIORITY",
    "DOES_NOT_SUPPORT_SUPERIORITY",
    "METHOD_DISAGREEMENT",
    "PENDING_MORE_REPLICATIONS",
    "MONTE_CARLO_UNRESOLVED",
    "NOT_APPLICABLE",
]
_ResolvedDiagnosticVerdict = Literal[
    "SUPPORTS_SUPERIORITY",
    "DOES_NOT_SUPPORT_SUPERIORITY",
    "METHOD_DISAGREEMENT",
    "MONTE_CARLO_UNRESOLVED",
]
_ENVELOPE_ROOT = "evidence/data_snooping"
_PROJECTION_ROOT = f"{_ENVELOPE_ROOT}/publication-receipts"
_SIDECAR_ROOT = f"{_ENVELOPE_ROOT}/sidecars"
_SIDECAR_TOTAL_MAX_BYTES = 4 * 1024 * 1024
_TYPED_EVIDENCE_MAX_BYTES = 512 * 1024
_SIDECAR_MAX_BYTES = {
    "FAMILY_SCHEMA_V3_MANIFEST": 512 * 1024,
    "TRIAL_LEDGER_HISTORY": 1024 * 1024,
    "TRIAL_LEDGER_SNAPSHOT_SQLITE": 2 * 1024 * 1024,
    "TRIAL_LEDGER_SEAL": 256 * 1024,
}
_SIDECAR_MEDIA_TYPES = {
    "FAMILY_SCHEMA_V3_MANIFEST": "application/vnd.quantos.family-schema-manifest+json",
    "TRIAL_LEDGER_HISTORY": "application/vnd.quantos.trial-ledger-history+json",
    "TRIAL_LEDGER_SNAPSHOT_SQLITE": "application/vnd.sqlite3",
    "TRIAL_LEDGER_SEAL": "application/vnd.quantos.trial-ledger-seal+json",
}


def _require_nfc(value: Any) -> Any:
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise ValueError("public authority strings must be NFC normalized")
        return value
    if isinstance(value, (tuple, list)):
        for item in value:
            _require_nfc(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            _require_nfc(key)
            _require_nfc(item)
    return value


class _FrozenPublicContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    @field_validator("*", mode="before")
    @classmethod
    def _validate_canonical_text(cls, value: Any) -> Any:
        return _require_nfc(value)


def _lineage_hash(authority_instance_id: str, genesis_record_hash: str) -> str:
    return canonical_sha256(
        {
            "authority_instance_id": authority_instance_id,
            "genesis_record_hash": genesis_record_hash,
        }
    )


def _content_path(root: str, digest: str) -> str:
    return f"{root}/sha256-{digest}.json"


def _scan_result_hash(
    *,
    scan_target: str,
    target_sha256: str,
    target_byte_length: int,
    public_artifact_scan_policy_id: str,
    dlp_policy_id: str,
    dlp_policy_version: str,
    dlp_policy_hash: str,
    scanner_implementation: str,
    scanner_version: str,
    scan_status: str,
) -> str:
    return canonical_sha256(
        {
            "scan_target": scan_target,
            "target_sha256": target_sha256,
            "target_byte_length": target_byte_length,
            "public_artifact_scan_policy_id": public_artifact_scan_policy_id,
            "dlp_policy_id": dlp_policy_id,
            "dlp_policy_version": dlp_policy_version,
            "dlp_policy_hash": dlp_policy_hash,
            "scanner_implementation": scanner_implementation,
            "scanner_version": scanner_version,
            "scan_status": scan_status,
        }
    )


def _sidecar_path(sidecar_kind: str, digest: str) -> str:
    return f"{_SIDECAR_ROOT}/{sidecar_kind.casefold()}/sha256-{digest}.bin"


def _typed_evidence_path(contract_type: str, digest: str) -> str:
    return f"{_SIDECAR_ROOT}/typed-{contract_type.casefold()}/sha256-{digest}.json"


_SidecarKind = Literal[
    "FAMILY_SCHEMA_V3_MANIFEST",
    "TRIAL_LEDGER_HISTORY",
    "TRIAL_LEDGER_SNAPSHOT_SQLITE",
    "TRIAL_LEDGER_SEAL",
    "CANONICAL_TYPED_EVIDENCE",
    "PROVIDER_AUTHORITY_PROJECTION",
]


class _ExactByteScanReceipt(_FrozenPublicContract):
    record_type: Literal["EXACT_BYTE_SIDECAR_SCAN_RECEIPT_V1"]
    sidecar_kind: _SidecarKind
    target_sha256: _Sha256Digest
    target_byte_length: int = Field(gt=0)
    public_artifact_scan_policy_id: _PublicToken
    dlp_policy_id: _PublicToken
    dlp_policy_version: _PublicToken
    dlp_policy_hash: _Sha256Digest
    scanner_implementation: _PublicToken
    scanner_version: _PublicToken
    scan_status: Literal["ALLOW"]
    scan_result_hash: _Sha256Digest
    scanned_at_us: _PositiveEpochUs
    scan_receipt_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_scan_receipt(self) -> "_ExactByteScanReceipt":
        if self.scan_result_hash != _scan_result_hash(
            scan_target=self.sidecar_kind,
            target_sha256=self.target_sha256,
            target_byte_length=self.target_byte_length,
            public_artifact_scan_policy_id=self.public_artifact_scan_policy_id,
            dlp_policy_id=self.dlp_policy_id,
            dlp_policy_version=self.dlp_policy_version,
            dlp_policy_hash=self.dlp_policy_hash,
            scanner_implementation=self.scanner_implementation,
            scanner_version=self.scanner_version,
            scan_status=self.scan_status,
        ):
            raise ValueError("sidecar scan result must bind the exact decoded bytes")
        if self.scan_receipt_hash != canonical_sha256(
            self.model_dump(mode="python", exclude={"scan_receipt_hash"})
        ):
            raise ValueError("sidecar scan receipt hash must bind the complete receipt")
        return self


class _ExactByteSidecar(_FrozenPublicContract):
    record_type: Literal["EXACT_BYTE_SIDECAR_V1"]
    sidecar_kind: Literal[
        "FAMILY_SCHEMA_V3_MANIFEST",
        "TRIAL_LEDGER_HISTORY",
        "TRIAL_LEDGER_SNAPSHOT_SQLITE",
        "TRIAL_LEDGER_SEAL",
    ]
    transport_encoding: Literal["BASE64_STANDARD_V1"]
    media_type: _CanonicalMediaType
    content_addressed_path: _NonEmptyText
    decoded_byte_length: int = Field(gt=0, le=_SIDECAR_TOTAL_MAX_BYTES)
    decoded_sha256: _Sha256Digest
    content_base64: Annotated[
        str,
        Field(min_length=4, max_length=((_SIDECAR_TOTAL_MAX_BYTES + 2) // 3) * 4),
    ]
    scan_receipt: _ExactByteScanReceipt

    @model_validator(mode="after")
    def _validate_exact_decoded_bytes(self) -> "_ExactByteSidecar":
        maximum = _SIDECAR_MAX_BYTES[self.sidecar_kind]
        if self.decoded_byte_length > maximum:
            raise ValueError("sidecar exceeds its closed per-kind decoded-byte limit")
        if self.media_type != _SIDECAR_MEDIA_TYPES[self.sidecar_kind]:
            raise ValueError("sidecar media type does not match its closed kind")
        if any(character.isspace() for character in self.content_base64):
            raise ValueError("sidecar base64 transport forbids whitespace")
        if len(self.content_base64) > ((maximum + 2) // 3) * 4:
            raise ValueError("sidecar encoded length exceeds its pre-decode resource bound")
        try:
            decoded = base64.b64decode(self.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("sidecar requires canonical standard base64") from exc
        if base64.b64encode(decoded).decode("ascii") != self.content_base64:
            raise ValueError("sidecar base64 padding or alphabet is noncanonical")
        if len(decoded) != self.decoded_byte_length:
            raise ValueError("sidecar declared length does not match decoded bytes")
        if sha256_bytes(decoded) != self.decoded_sha256:
            raise ValueError("sidecar digest does not match decoded bytes")
        if self.content_addressed_path != _sidecar_path(
            self.sidecar_kind,
            self.decoded_sha256,
        ):
            raise ValueError("sidecar path must content-address the exact decoded bytes")
        scan = self.scan_receipt
        if (
            scan.sidecar_kind != self.sidecar_kind
            or scan.target_sha256 != self.decoded_sha256
            or scan.target_byte_length != self.decoded_byte_length
        ):
            raise ValueError("sidecar scan receipt targets different decoded bytes")
        if self.sidecar_kind != "TRIAL_LEDGER_SNAPSHOT_SQLITE":
            load_canonical_json_bytes(decoded)
        return self


def _load_canonical_json_array_text(value: str) -> tuple[Any, ...]:
    loaded = load_canonical_json_bytes((value + "\n").encode("utf-8"))
    if not isinstance(loaded, list):
        raise ValueError("canonical JSON evidence field must encode an array")
    return tuple(loaded)


_CREDENTIAL_KEY_TOKENS = frozenset(
    {
        "accesskey",
        "apikey",
        "authorization",
        "bearer",
        "credential",
        "credentials",
        "password",
        "privatekey",
        "secret",
        "secretkey",
        "token",
    }
)
_CREDENTIAL_VALUE_PATTERNS = (
    re.compile(r"(?i)^\s*bearer\s+\S+"),
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
)


def _validate_public_json(value: Any) -> None:
    """Reject credential-shaped material before it can enter public bytes."""

    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("public proposal JSON forbids non-finite floats")
        return
    if isinstance(value, str):
        if any(pattern.search(value) for pattern in _CREDENTIAL_VALUE_PATTERNS):
            raise ValueError("public proposal JSON contains credential-shaped text")
        return
    if isinstance(value, (tuple, list)):
        for item in value:
            _validate_public_json(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("public proposal JSON object keys must be text")
            compact_key = re.sub(r"[^a-z0-9]", "", key.casefold())
            if compact_key in _CREDENTIAL_KEY_TOKENS or any(
                compact_key.endswith(token) for token in ("apikey", "accesskey", "secretkey", "privatekey")
            ):
                raise ValueError("public proposal JSON contains a credential-shaped field")
            _validate_public_json(item)
        return
    raise ValueError("public proposal must contain canonical JSON values only")


_AUTHORITY_PRIMARY_KEY_FIELDS: dict[str, tuple[str, ...]] = {
    # C4
    "research_questions": ("research_question_id",),
    "research_question_aliases": ("alias_hash",),
    "strategy_lineage_roots": ("strategy_lineage_root_id",),
    "strategy_lineage_nodes": ("strategy_lineage_node_id",),
    "strategy_config_lineage_bindings": ("config_hash",),
    "research_authority_events": ("event_id",),
    "selection_scopes": ("selection_scope_id",),
    "selection_scope_aliases": ("alias_hash",),
    "selection_scope_publishable_decisions": ("selection_scope_id",),
    "selection_scope_outcomes": ("selection_scope_id",),
    "research_programs": ("research_program_id",),
    "selection_decisions": ("selection_decision_id",),
    "global_oos_exposures": ("oos_exposure_id",),
    "global_oos_exposure_consumptions": ("oos_exposure_id",),
    # C5
    "data_snooping_family_searches": ("search_id",),
    "data_snooping_proposal_registrations": ("proposal_id",),
    "data_snooping_proposals": ("proposal_id",),
    "data_snooping_hypotheses": ("family_id", "config_hash"),
    "data_snooping_search_emission_seals": ("family_id", "search_id"),
    # C7
    "data_snooping_oos_releases": ("oos_release_id",),
    "data_snooping_oos_access_attempts": ("attempt_event_id",),
    "data_snooping_candidate_dispositions": ("family_id", "proposal_id"),
    "data_snooping_evaluation_batches": ("evaluation_batch_id",),
    "data_snooping_family_oos_evaluations": ("family_id", "config_hash"),
    "data_snooping_evaluation_receipts": ("evaluation_receipt_hash",),
    "data_snooping_benchmark_returns": ("benchmark_receipt_hash",),
    # C8
    "data_snooping_declarations": ("declaration_id",),
    "data_snooping_family_freezes": ("freeze_id",),
    "data_snooping_family_closures": ("closure_id",),
    "data_snooping_grid_assessments": ("grid_assessment_id",),
    "data_snooping_assessments": ("assessment_id",),
}

if set(AUTHORITY_ROW_LAYOUTS) != set(_AUTHORITY_PRIMARY_KEY_FIELDS):
    raise RuntimeError("C9 authority projection whitelist diverges from C4-C8 tables")

_AUTHORITY_CHAIN_FIELDS: dict[str, tuple[tuple[str, ...], str, str, int]] = {
    "research_authority_events": (
        ("authority_instance_id",),
        "event_ordinal",
        "prev_record_hash",
        1,
    ),
    "selection_decisions": (
        ("authority_instance_id", "selection_scope_id"),
        "decision_ordinal",
        "prev_record_hash",
        1,
    ),
    "global_oos_exposures": (
        ("authority_instance_id", "selection_scope_id"),
        "exposure_ordinal",
        "prev_record_hash",
        1,
    ),
    "data_snooping_family_searches": (
        ("family_id",),
        "link_ordinal",
        "prev_record_hash",
        1,
    ),
    "data_snooping_proposal_registrations": (
        ("family_id", "search_id"),
        "emission_ordinal",
        "prev_record_hash",
        1,
    ),
    "data_snooping_oos_access_attempts": (
        ("authority_instance_id", "attempt_id"),
        "sequence_no",
        "prev_record_hash",
        0,
    ),
    "data_snooping_grid_assessments": (
        ("authority_instance_id", "family_id", "block_length"),
        "schedule_ordinal",
        "prev_record_hash",
        1,
    ),
}


def _validate_exact_authority_row_layout(table_name: str, payload: dict[str, Any]) -> None:
    layout = AUTHORITY_ROW_LAYOUTS.get(table_name)
    if layout is None:
        raise ValueError("authority projection contains an unsupported authority table")
    record_type, fields, integer_fields, nullable_fields = layout
    if set(payload) != set(fields):
        raise ValueError("authority projection row must contain the complete exact row-hash domain")
    if payload.get("record_type") != record_type:
        raise ValueError("authority projection row record type does not match its table")
    integer_field_set = set(integer_fields)
    nullable_field_set = set(nullable_fields)
    for name in fields:
        value = payload[name]
        if value is None:
            if name not in nullable_field_set:
                raise ValueError("authority projection row omits a non-null authority column")
            continue
        if name in integer_field_set:
            if type(value) is not int:
                raise ValueError("authority INTEGER columns require exact integer values")
        else:
            if type(value) is not str:
                raise ValueError("authority TEXT columns require exact text values")
        if name.endswith("_json"):
            load_canonical_json_bytes((value + "\n").encode("utf-8"))
        if (
            name.endswith(("_hash", "_head", "_fingerprint"))
            or name in {"prev_record_hash", "genesis_record_hash"}
        ) and re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("authority digest columns require canonical SHA-256 text")


class _AuthorityProjectionRow(_FrozenPublicContract):
    table_name: _NonEmptyText
    primary_key: dict[str, Any]
    canonical_row_payload: dict[str, Any]
    record_hash: _Sha256Digest
    chain_partition_key: dict[str, Any] | None
    chain_ordinal: Annotated[int, Field(ge=0)] | None
    chain_previous_record_hash: _Sha256Digest | None

    @field_validator("canonical_row_payload")
    @classmethod
    def _validate_safe_canonical_row(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_public_json(value)
        return value

    @field_validator("primary_key")
    @classmethod
    def _validate_safe_primary_key(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_public_json(value)
        if not value:
            raise ValueError("authority projection primary key cannot be empty")
        return value

    @model_validator(mode="after")
    def _validate_exact_row_hash(self) -> "_AuthorityProjectionRow":
        primary_key_fields = _AUTHORITY_PRIMARY_KEY_FIELDS.get(self.table_name)
        if primary_key_fields is None:
            raise ValueError("authority projection contains an unsupported authority table")
        if set(self.primary_key) != set(primary_key_fields) or any(
            self.canonical_row_payload.get(field_name) != self.primary_key[field_name]
            for field_name in primary_key_fields
        ):
            raise ValueError("authority projection primary key does not match its canonical row")
        _validate_exact_authority_row_layout(self.table_name, self.canonical_row_payload)
        if "record_hash" in self.canonical_row_payload:
            raise ValueError("canonical row payload must exclude its self hash")
        if canonical_sha256(self.canonical_row_payload) != self.record_hash:
            raise ValueError("authority projection row hash must bind the exact canonical row payload")
        chain_values = (
            self.chain_partition_key,
            self.chain_ordinal,
            self.chain_previous_record_hash,
        )
        chain_fields = _AUTHORITY_CHAIN_FIELDS.get(self.table_name)
        if chain_fields is not None:
            if any(value is None for value in chain_values):
                raise ValueError("chained projection rows require complete partition metadata")
            _validate_public_json(self.chain_partition_key)
            partition_fields, ordinal_field, previous_hash_field, _ = chain_fields
            expected_partition = {
                field_name: self.canonical_row_payload.get(field_name)
                for field_name in partition_fields
            }
            if self.chain_partition_key != expected_partition:
                raise ValueError("projection chain partition must match the exact row payload")
            if self.chain_ordinal != self.canonical_row_payload.get(ordinal_field):
                raise ValueError("projection chain ordinal must match the exact row payload")
            if self.chain_previous_record_hash != self.canonical_row_payload.get(
                previous_hash_field
            ):
                raise ValueError("projection chain predecessor must match the exact row payload")
        elif any(value is not None for value in chain_values):
            raise ValueError("unchained authority tables forbid invented chain metadata")
        return self


class _AuthorityProjectionChainHead(_FrozenPublicContract):
    table_name: Literal[
        "research_authority_events",
        "selection_decisions",
        "global_oos_exposures",
        "data_snooping_family_searches",
        "data_snooping_proposal_registrations",
        "data_snooping_oos_access_attempts",
        "data_snooping_grid_assessments",
    ]
    partition_key: dict[str, Any]
    row_count: int = Field(gt=0)
    head_record_hash: _Sha256Digest

    @field_validator("partition_key")
    @classmethod
    def _validate_safe_partition(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_public_json(value)
        if not value:
            raise ValueError("chain head requires a nonempty partition key")
        return value

    @model_validator(mode="after")
    def _validate_partition_domain(self) -> "_AuthorityProjectionChainHead":
        expected_fields = set(_AUTHORITY_CHAIN_FIELDS[self.table_name][0])
        if set(self.partition_key) != expected_fields:
            raise ValueError("chain head requires the exact table partition-key domain")
        return self


class _AuthorityProjection(_FrozenPublicContract):
    projection_schema_version: Literal["DATA_SNOOPING_AUTHORITY_PROJECTION_V1"]
    authority_instance_id: _NonEmptyText
    genesis_record_hash: _Sha256Digest
    selection_scope_history_head: _Sha256Digest
    canonical_rows: Annotated[tuple[_AuthorityProjectionRow, ...], Field(min_length=1)]
    ordered_row_hashes: Annotated[tuple[_Sha256Digest, ...], Field(min_length=1)]
    ordered_row_hashes_hash: _Sha256Digest
    chain_heads: tuple[_AuthorityProjectionChainHead, ...]
    chain_heads_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_exact_topology(self) -> "_AuthorityProjection":
        projected_hashes = tuple(row.record_hash for row in self.canonical_rows)
        if self.ordered_row_hashes != projected_hashes:
            raise ValueError("ordered row-hash vector must bind every exact projected row")
        if self.ordered_row_hashes_hash != canonical_sha256(self.ordered_row_hashes):
            raise ValueError("ordered row-hash vector requires its exact canonical hash")
        if self.chain_heads_hash != canonical_sha256(self.chain_heads):
            raise ValueError("chain-head hash must bind every real partition head")
        identities = tuple(
            (row.table_name, canonical_json_bytes(row.primary_key)) for row in self.canonical_rows
        )
        if len(set(identities)) != len(identities):
            raise ValueError("authority projection rows must have unique table identities")
        if any(
            row.canonical_row_payload.get("authority_instance_id") != self.authority_instance_id
            or row.canonical_row_payload.get("genesis_record_hash") != self.genesis_record_hash
            for row in self.canonical_rows
        ):
            raise ValueError("authority projection canonical rows belong to a foreign lineage")

        expected_order = tuple(
            sorted(
                self.canonical_rows,
                key=lambda row: (
                    row.table_name,
                    canonical_json_bytes(row.primary_key),
                ),
            )
        )
        if self.canonical_rows != expected_order:
            raise ValueError("authority projection rows require canonical table/partition order")
        for table_name in (
            "data_snooping_declarations",
            "data_snooping_family_freezes",
            "data_snooping_family_closures",
            "data_snooping_assessments",
        ):
            if sum(row.table_name == table_name for row in self.canonical_rows) != 1:
                raise ValueError("authority projection requires one exact family topology row")

        chain_partitions: dict[str, list[_AuthorityProjectionRow]] = {}
        for row in self.canonical_rows:
            if row.table_name in _AUTHORITY_CHAIN_FIELDS:
                partition_identity = canonical_sha256(
                    {
                        "table_name": row.table_name,
                        "partition_key": row.chain_partition_key,
                    }
                )
                chain_partitions.setdefault(
                    partition_identity,
                    [],
                ).append(row)
        heads_by_partition = {
            canonical_sha256(
                {
                    "table_name": head.table_name,
                    "partition_key": head.partition_key,
                }
            ): head
            for head in self.chain_heads
        }
        if len(heads_by_partition) != len(self.chain_heads):
            raise ValueError("chain heads require unique table-partition identities")
        if set(heads_by_partition) != set(chain_partitions):
            raise ValueError("chain heads must cover exactly the projected chained partitions")
        expected_heads = tuple(
            sorted(
                self.chain_heads,
                key=lambda head: (head.table_name, canonical_json_bytes(head.partition_key)),
            )
        )
        if self.chain_heads != expected_heads:
            raise ValueError("chain heads require canonical table/partition ordering")
        for partition_hash, rows in chain_partitions.items():
            ordered_partition_rows = sorted(rows, key=lambda row: row.chain_ordinal or 0)
            table_name = ordered_partition_rows[0].table_name
            first_ordinal = _AUTHORITY_CHAIN_FIELDS[table_name][3]
            for offset, row in enumerate(ordered_partition_rows):
                expected_ordinal = first_ordinal + offset
                if row.chain_ordinal != expected_ordinal:
                    raise ValueError("projection chain must contain a complete ordinal prefix")
                expected_previous = (
                    self.genesis_record_hash
                    if offset == 0
                    else ordered_partition_rows[offset - 1].record_hash
                )
                if row.chain_previous_record_hash != expected_previous:
                    raise ValueError("projection predecessor chain is incomplete")
            if (
                table_name == "data_snooping_oos_access_attempts"
                and ordered_partition_rows[-1].chain_ordinal != 1
            ):
                raise ValueError("OOS access attempt projection requires its terminal event")
            head = heads_by_partition[partition_hash]
            if (
                head.row_count != len(ordered_partition_rows)
                or head.head_record_hash != ordered_partition_rows[-1].record_hash
            ):
                raise ValueError("grid chain head must bind the exact terminal row and count")
        research_head = next(
            (
                head
                for head in self.chain_heads
                if head.table_name == "research_authority_events"
                and head.partition_key
                == {"authority_instance_id": self.authority_instance_id}
            ),
            None,
        )
        research_event_hashes = {
            row.record_hash
            for row in self.canonical_rows
            if row.table_name == "research_authority_events"
        }
        if research_head is None or self.selection_scope_history_head not in (
            research_event_hashes
        ):
            raise ValueError(
                "selection-scope history head must be a real projected event-chain ancestor"
            )
        return self


class _NonQuarantinedPublicProposal(_FrozenPublicContract):
    projection_kind: Literal["NON_QUARANTINED"]
    proposal_id: _Sha256Digest
    disposition: Literal[
        "PERFORMANCE_EVALUATED",
        "PERFORMANCE_ATTEMPTED_INCOMPLETE",
        "PRE_EVALUATION_INVALID",
    ]
    media_type: _CanonicalMediaType
    byte_length: int = Field(gt=0)
    dlp_policy_id: _PublicToken
    dlp_policy_version: _PublicToken
    registration_record_hash: _Sha256Digest
    canonical_proposal_hash: _Sha256Digest
    canonical_proposal: dict[str, Any]

    @field_validator("canonical_proposal")
    @classmethod
    def _validate_safe_public_proposal(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_public_json(value)
        return value

    @model_validator(mode="after")
    def _validate_exact_canonical_proposal(self) -> "_NonQuarantinedPublicProposal":
        if self.canonical_proposal_hash != canonical_sha256(self.canonical_proposal):
            raise ValueError("canonical proposal hash must bind the exact safe proposal")
        if self.byte_length != len(canonical_json_bytes(self.canonical_proposal)):
            raise ValueError("proposal byte length must equal its exact canonical public bytes")
        return self


class _InvalidPublicProposal(_FrozenPublicContract):
    projection_kind: Literal["NON_QUARANTINED_INVALID"]
    proposal_id: _Sha256Digest
    disposition: Literal["PRE_EVALUATION_INVALID"]
    registration_record_hash: _Sha256Digest
    media_type: _CanonicalMediaType
    raw_byte_length: int = Field(gt=0)
    length_bucket: _LengthBucket
    dlp_policy_id: _PublicToken
    dlp_policy_version: _PublicToken
    reason_code: Literal[
        "INVALID_UTF8",
        "MALFORMED_JSON",
        "DUPLICATE_JSON_KEY",
        "NON_NFC_TEXT",
        "SCHEMA_INVALID",
        "TYPE_INVALID",
        "GUARDRAIL_REJECTED",
    ]


class _QuarantinedPublicProposal(_FrozenPublicContract):
    projection_kind: Literal["QUARANTINED"]
    proposal_id: _Sha256Digest
    disposition: Literal["SECRET_QUARANTINED"]
    registration_record_hash: _Sha256Digest
    quarantine_record_id: _QuarantineId
    length_bucket: _LengthBucket
    media_type: _CanonicalMediaType
    dlp_policy_id: _PublicToken
    dlp_policy_version: _PublicToken
    reason_code: Literal[
        "DLP_SECRET_DETECTED",
        "DLP_SCANNER_UNAVAILABLE",
        "DLP_SCANNER_ERROR",
    ]


_PublicProposal = (
    _NonQuarantinedPublicProposal
    | _InvalidPublicProposal
    | _QuarantinedPublicProposal
)

_EMPTY_SEARCH_CANDIDATE_CHAIN_DOMAIN = (
    "QUANTOS_EMPTY_SEARCH_CANDIDATE_CHAIN_V1"
)


def _canonical_json_column(value: Any, *, label: str) -> Any:
    if type(value) is not str:
        raise ValueError(f"{label} must be canonical JSON text")
    return load_canonical_json_bytes((value + "\n").encode("utf-8"))


def _empty_search_candidate_chain_head(
    authority_instance_id: str,
    family_id: str,
    search_id: str,
) -> str:
    return canonical_sha256(
        {
            "domain_separator": _EMPTY_SEARCH_CANDIDATE_CHAIN_DOMAIN,
            "authority_instance_id": authority_instance_id,
            "family_id": family_id,
            "search_id": search_id,
        }
    )


def _validate_artifact_proposal_graph(
    core: "ArtifactCore",
    *,
    freeze_payload: dict[str, Any],
    closure_payload: dict[str, Any],
) -> None:
    """Prove the complete C5 registration/result graph inside one artifact."""

    rows_by_table: dict[str, list[_AuthorityProjectionRow]] = {}
    for row in core.authority_projection.canonical_rows:
        rows_by_table.setdefault(row.table_name, []).append(row)

    def family_rows(table_name: str) -> tuple[_AuthorityProjectionRow, ...]:
        rows = tuple(
            row
            for row in rows_by_table.get(table_name, ())
            if row.canonical_row_payload.get("family_id") == core.family_id
        )
        if len(rows) != len(rows_by_table.get(table_name, ())):
            raise ValueError("proposal authority graph contains a foreign family row")
        return rows

    searches = tuple(
        sorted(
            family_rows("data_snooping_family_searches"),
            key=lambda row: (
                row.canonical_row_payload["link_ordinal"],
                row.canonical_row_payload["search_id"],
            ),
        )
    )
    search_ids = tuple(row.canonical_row_payload["search_id"] for row in searches)
    if (
        len(searches) != freeze_payload["search_count"]
        or len(set(search_ids)) != len(search_ids)
        or tuple(
            _canonical_json_column(
                freeze_payload["ordered_search_ids_json"],
                label="ordered search IDs",
            )
        )
        != search_ids
        or freeze_payload["ordered_search_ids_hash"] != canonical_sha256(search_ids)
    ):
        raise ValueError("family freeze must bind every projected family search")
    search_by_id = {
        row.canonical_row_payload["search_id"]: row for row in searches
    }

    registrations = tuple(
        sorted(
            family_rows("data_snooping_proposal_registrations"),
            key=lambda row: (
                row.canonical_row_payload["search_id"],
                row.canonical_row_payload["emission_ordinal"],
                row.canonical_row_payload["proposal_id"],
            ),
        )
    )
    terminal_results = family_rows("data_snooping_proposals")
    hypotheses = family_rows("data_snooping_hypotheses")
    seals = family_rows("data_snooping_search_emission_seals")
    dispositions = family_rows("data_snooping_candidate_dispositions")

    registration_by_id = {
        row.canonical_row_payload["proposal_id"]: row for row in registrations
    }
    result_by_id = {
        row.canonical_row_payload["proposal_id"]: row for row in terminal_results
    }
    disposition_by_id = {
        row.canonical_row_payload["proposal_id"]: row for row in dispositions
    }
    seal_by_search = {
        row.canonical_row_payload["search_id"]: row for row in seals
    }
    if (
        len(registration_by_id) != len(registrations)
        or len(result_by_id) != len(terminal_results)
        or len(disposition_by_id) != len(dispositions)
        or len(seal_by_search) != len(seals)
        or set(result_by_id) != set(registration_by_id)
        or set(disposition_by_id) != set(registration_by_id)
        or set(seal_by_search) != set(search_by_id)
    ):
        raise ValueError(
            "proposal registrations require exact terminal-result, disposition, and seal totality"
        )

    registration_ids = tuple(
        row.canonical_row_payload["proposal_id"] for row in registrations
    )
    if tuple(proposal.proposal_id for proposal in core.public_proposals) != registration_ids:
        raise ValueError("public proposals must use exact registration-derived order")
    public_by_id = {
        proposal.proposal_id: proposal for proposal in core.public_proposals
    }
    if len(public_by_id) != len(core.public_proposals):
        raise ValueError("public proposal identities must be unique")
    if tuple(core.candidate_disposition_record_hashes) != tuple(
        disposition_by_id[proposal_id].record_hash for proposal_id in registration_ids
    ):
        raise ValueError("disposition vector must follow exact registration order")

    accepted_by_config: dict[str, tuple[_AuthorityProjectionRow, _AuthorityProjectionRow]] = {}
    accepted_config_by_proposal: dict[str, str] = {}
    for registration in registrations:
        registered = registration.canonical_row_payload
        proposal_id = registered["proposal_id"]
        if registered["search_id"] not in search_by_id:
            raise ValueError("proposal registration belongs to an unprojected search")
        result = result_by_id[proposal_id]
        parsed = result.canonical_row_payload
        if any(
            parsed[name] != registered[name]
            for name in ("family_id", "search_id", "emission_ordinal", "proposal_id")
        ) or parsed["registration_record_hash"] != registration.record_hash:
            raise ValueError("terminal proposal result is detached from its registration")

        parsed_fields = (
            parsed["canonical_proposal_json"],
            parsed["canonical_proposal_hash"],
            parsed["config_hash"],
            parsed["alpha_id"],
        )
        parse_status = parsed["parse_status"]
        registration_branch = registered["proposal_branch"]
        if parse_status == "ACCEPTED":
            if registration_branch != "CLEAN" or any(value is None for value in parsed_fields):
                raise ValueError("accepted terminal result requires one clean complete registration")
            canonical_proposal = _canonical_json_column(
                parsed["canonical_proposal_json"],
                label="canonical proposal",
            )
            if (
                not isinstance(canonical_proposal, dict)
                or parsed["canonical_proposal_hash"]
                != canonical_sha256(canonical_proposal)
            ):
                raise ValueError("accepted proposal canonical bytes are detached")
            config_hash = parsed["config_hash"]
            accepted_config_by_proposal[proposal_id] = config_hash
            accepted_by_config.setdefault(config_hash, (registration, result))
        elif parse_status == "INVALID":
            if registration_branch != "CLEAN" or any(value is not None for value in parsed_fields):
                raise ValueError("invalid terminal result must be clean and contain no parsed identity")
        elif parse_status == "SECRET_QUARANTINED":
            if registration_branch != "SECRET_QUARANTINED" or any(
                value is not None for value in parsed_fields
            ):
                raise ValueError("quarantined terminal result must remain opaque")
        else:
            raise ValueError("terminal proposal result has an unsupported parse status")

        disposition = disposition_by_id[proposal_id].canonical_row_payload
        public = public_by_id[proposal_id]
        if public.registration_record_hash != registration.record_hash:
            raise ValueError("public proposal must bind its exact registration row")
        if disposition["family_id"] != core.family_id:
            raise ValueError("proposal disposition belongs to another family")
        if parse_status == "ACCEPTED":
            if (
                disposition["disposition"]
                not in {"PERFORMANCE_EVALUATED", "PERFORMANCE_ATTEMPTED_INCOMPLETE"}
                or disposition["config_hash"] != parsed["config_hash"]
                or not isinstance(public, _NonQuarantinedPublicProposal)
                or public.disposition != disposition["disposition"]
                or public.media_type != registered["media_type"]
                or public.dlp_policy_id != registered["dlp_policy_id"]
                or public.dlp_policy_version != registered["dlp_policy_version"]
                or public.canonical_proposal_hash != parsed["canonical_proposal_hash"]
                or public.canonical_proposal
                != _canonical_json_column(
                    parsed["canonical_proposal_json"],
                    label="canonical proposal",
                )
            ):
                raise ValueError("accepted public proposal is detached from result or disposition")
        elif parse_status == "INVALID":
            if (
                disposition["disposition"] != "PRE_EVALUATION_INVALID"
                or not isinstance(public, _InvalidPublicProposal)
                or public.reason_code != disposition["reason_code"]
                or public.media_type != registered["media_type"]
                or public.raw_byte_length != registered["raw_byte_length"]
                or public.length_bucket != registered["length_bucket"]
                or public.dlp_policy_id != registered["dlp_policy_id"]
                or public.dlp_policy_version != registered["dlp_policy_version"]
            ):
                raise ValueError("invalid public proposal is detached from safe registration metadata")
        elif (
            disposition["disposition"] != "SECRET_QUARANTINED"
            or not isinstance(public, _QuarantinedPublicProposal)
            or public.quarantine_record_id != registered["quarantine_record_id"]
            or public.quarantine_record_id != disposition["quarantine_record_id"]
            or public.length_bucket != registered["length_bucket"]
            or public.media_type != registered["media_type"]
            or public.dlp_policy_id != registered["dlp_policy_id"]
            or public.dlp_policy_version != registered["dlp_policy_version"]
            or public.reason_code != disposition["reason_code"]
        ):
            raise ValueError("quarantined public proposal is detached from its opaque registration")

    hypothesis_by_config = {
        row.canonical_row_payload["config_hash"]: row for row in hypotheses
    }
    if len(hypothesis_by_config) != len(hypotheses) or set(hypothesis_by_config) != set(
        accepted_by_config
    ):
        raise ValueError("accepted terminal results require exact unique hypothesis totality")
    for config_hash, (first_registration, first_result) in accepted_by_config.items():
        hypothesis = hypothesis_by_config[config_hash].canonical_row_payload
        registration_payload = first_registration.canonical_row_payload
        result_payload = first_result.canonical_row_payload
        if (
            hypothesis["first_search_id"] != registration_payload["search_id"]
            or hypothesis["first_proposal_id"] != registration_payload["proposal_id"]
            or hypothesis["canonical_hypothesis_hash"]
            != result_payload["canonical_proposal_hash"]
            or hypothesis["alpha_id"] != result_payload["alpha_id"]
        ):
            raise ValueError("hypothesis origin is detached from the first accepted result")

    seal_hashes: list[str] = []
    candidate_heads: list[str] = []
    for search_id in search_ids:
        seal = seal_by_search[search_id]
        sealed = seal.canonical_row_payload
        search = search_by_id[search_id].canonical_row_payload
        search_registrations = tuple(
            row
            for row in registrations
            if row.canonical_row_payload["search_id"] == search_id
        )
        search_results = tuple(
            result_by_id[row.canonical_row_payload["proposal_id"]]
            for row in search_registrations
        )
        proposal_ids = [
            row.canonical_row_payload["proposal_id"] for row in search_registrations
        ]
        clean_raw_hashes = [
            row.canonical_row_payload["raw_envelope_hash"]
            for row in search_registrations
            if row.canonical_row_payload["proposal_branch"] == "CLEAN"
        ]
        canonical_hashes = [
            row.canonical_row_payload["canonical_proposal_hash"]
            for row in search_results
            if row.canonical_row_payload["parse_status"] == "ACCEPTED"
        ]
        quarantine_ids = [
            row.canonical_row_payload["quarantine_record_id"]
            for row in search_registrations
            if row.canonical_row_payload["proposal_branch"]
            == "SECRET_QUARANTINED"
        ]
        accepted_configs = sorted(
            {
                row.canonical_row_payload["config_hash"]
                for row in search_results
                if row.canonical_row_payload["parse_status"] == "ACCEPTED"
            }
        )
        candidate_count = len(accepted_configs)
        empty_head = _empty_search_candidate_chain_head(
            core.authority_instance_id,
            core.family_id,
            search_id,
        )
        if (
            sealed["declared_proposal_budget"] != search["proposal_budget"]
            or sealed["actual_raw_proposal_count"] != len(search_registrations)
            or sealed["ordered_proposal_ids_hash"] != canonical_sha256(proposal_ids)
            or sealed["ordered_non_quarantined_raw_hashes_hash"]
            != canonical_sha256(clean_raw_hashes)
            or sealed["ordered_canonical_proposal_hashes_hash"]
            != canonical_sha256(canonical_hashes)
            or sealed["ordered_quarantine_record_ids_hash"]
            != canonical_sha256(quarantine_ids)
            or sealed["accepted_config_set_hash"] != canonical_sha256(accepted_configs)
            or sealed["candidate_count"] != candidate_count
            or (
                sealed["candidate_chain_head"] != empty_head
                if candidate_count == 0
                else sealed["candidate_chain_head"] == empty_head
            )
        ):
            raise ValueError("emission seal does not close registration and terminal-result rows")
        seal_hashes.append(seal.record_hash)
        candidate_heads.append(sealed["candidate_chain_head"])

    seal_hash_tuple = tuple(seal_hashes)
    candidate_head_tuple = tuple(candidate_heads)
    accepted_configs = tuple(sorted(accepted_by_config))
    if (
        tuple(
            _canonical_json_column(
                freeze_payload["ordered_emission_seal_hashes_json"],
                label="ordered emission seal hashes",
            )
        )
        != seal_hash_tuple
        or freeze_payload["ordered_emission_seal_hashes_hash"]
        != canonical_sha256(seal_hash_tuple)
        or tuple(
            _canonical_json_column(
                freeze_payload["ordered_candidate_chain_heads_json"],
                label="ordered candidate chain heads",
            )
        )
        != candidate_head_tuple
        or freeze_payload["ordered_candidate_chain_heads_hash"]
        != canonical_sha256(candidate_head_tuple)
        or freeze_payload["raw_proposal_count"] != len(registrations)
        or freeze_payload["unique_performance_hypothesis_count"]
        != len(accepted_configs)
        or freeze_payload["frozen_config_set_hash"] != canonical_sha256(accepted_configs)
    ):
        raise ValueError("family freeze does not close the exact proposal graph")

    disposition_hashes = tuple(
        disposition_by_id[proposal_id].record_hash for proposal_id in registration_ids
    )
    if (
        closure_payload["ordered_search_ids_hash"]
        != freeze_payload["ordered_search_ids_hash"]
        or closure_payload["ordered_emission_seal_hashes_hash"]
        != freeze_payload["ordered_emission_seal_hashes_hash"]
        or closure_payload["raw_proposal_count"] != len(registrations)
        or closure_payload["unique_performance_hypothesis_count"]
        != len(accepted_configs)
        or closure_payload["ordered_proposal_ids_hash"]
        != canonical_sha256(registration_ids)
        or closure_payload["ordered_disposition_record_hashes_hash"]
        != canonical_sha256(disposition_hashes)
    ):
        raise ValueError("family closure does not bind registration and terminal result totality")
    if core.closure_status == "COMPLETE" and core.ordered_candidate_config_hashes != accepted_configs:
        raise ValueError("complete artifact candidate count must equal unique accepted terminal results")


def _validate_float_vector(values: tuple[str, ...]) -> None:
    for value in values:
        parsed = parse_float64_hex(value)
        if not isfinite(parsed) or float64_hex(parsed) != value:
            raise ValueError("return evidence must use finite canonical float64 hex")


def _validate_net_return_vectors(
    gross_values: tuple[str, ...],
    costs: tuple[str, ...],
    net_values: tuple[str, ...],
    turnovers: tuple[str, ...],
) -> None:
    for vector in (gross_values, costs, net_values, turnovers):
        _validate_float_vector(vector)
    if any(parse_float64_hex(value) < 0.0 for value in (*costs, *turnovers)):
        raise ValueError("cost and turnover evidence must be nonnegative")
    if any(
        float64_hex(parse_float64_hex(gross) - parse_float64_hex(cost)) != net
        for gross, cost, net in zip(gross_values, costs, net_values)
    ):
        raise ValueError("net returns must exactly reconcile gross returns less costs")


_EmbeddedResearchContractType = Literal[
    "SelectionScopeRef",
    "SelectionDecisionRef",
    "FamilyEvaluationBatchReceipt",
    "CandidateReturnSeries",
    "BenchmarkReturnSeries",
    "TrialLedgerProof",
    "CandidateDisposition",
    "ObservedPitDatasetHandle",
    "CodeManifestEvidence",
    "ExecutionCodeAttestation",
    "ImmutableArtifactLease",
]

_ExternalProviderContractType = Literal[
    "ObservedPitDatasetHandle",
    "CodeManifestEvidence",
    "ExecutionCodeAttestation",
    "ImmutableArtifactLease",
]
_EXTERNAL_PROVIDER_TYPES = {
    "ObservedPitDatasetHandle",
    "CodeManifestEvidence",
    "ExecutionCodeAttestation",
    "ImmutableArtifactLease",
}


_ProviderAuthorityKind = Literal[
    "OBSERVED_PIT_DATA_AUTHORITY",
    "CODE_MANIFEST_AUTHORITY",
    "EXECUTION_ATTESTATION_AUTHORITY",
    "IMMUTABLE_ARTIFACT_LEASE_AUTHORITY",
]
_PROVIDER_AUTHORITY_KIND_BY_CONTRACT = {
    "ObservedPitDatasetHandle": "OBSERVED_PIT_DATA_AUTHORITY",
    "CodeManifestEvidence": "CODE_MANIFEST_AUTHORITY",
    "ExecutionCodeAttestation": "EXECUTION_ATTESTATION_AUTHORITY",
    "ImmutableArtifactLease": "IMMUTABLE_ARTIFACT_LEASE_AUTHORITY",
}


class _ObservedPitProviderSubjectContext(_FrozenPublicContract):
    context_type: Literal["OBSERVED_PIT_DATASET_HANDLE_CONTEXT_V1"]
    family_id: _NonEmptyText


class _AuthorityActivationCodeManifestContext(_FrozenPublicContract):
    context_type: Literal["CODE_MANIFEST_AUTHORITY_ACTIVATION_CONTEXT_V1"]
    authority_instance_id: _NonEmptyText
    sealed_v3_manifest_hash: _Sha256Digest
    migration_id: Literal["FAMILY_SCHEMA_V2_TO_V3_V1"]


class _ResearchExecutionCodeManifestContext(_FrozenPublicContract):
    context_type: Literal["CODE_MANIFEST_RESEARCH_EXECUTION_CONTEXT_V1"]
    family_id: _NonEmptyText


class _ExecutionProviderSubjectContext(_FrozenPublicContract):
    context_type: Literal["EXECUTION_CODE_ATTESTATION_CONTEXT_V1"]
    family_id: _NonEmptyText
    role: Literal["START", "TERMINAL"]
    attempt_id: _NonEmptyText
    evaluation_batch_id: _NonEmptyText


class _LeaseProviderSubjectContext(_FrozenPublicContract):
    context_type: Literal["IMMUTABLE_ARTIFACT_LEASE_CONTEXT_V1"]
    family_id: _NonEmptyText
    attempt_id: _NonEmptyText
    evaluation_batch_id: _NonEmptyText


_ProviderSubjectContext = (
    _ObservedPitProviderSubjectContext
    | _AuthorityActivationCodeManifestContext
    | _ResearchExecutionCodeManifestContext
    | _ExecutionProviderSubjectContext
    | _LeaseProviderSubjectContext
)


class _ProviderAuthorityGenesis(_FrozenPublicContract):
    record_type: Literal["EXTERNAL_PROVIDER_AUTHORITY_GENESIS_V1"]
    authority_id: _NonEmptyText
    provider_contract_type: _ExternalProviderContractType
    provider_authority_kind: _ProviderAuthorityKind
    provider_policy_id: _PublicToken
    provider_policy_version: _PublicToken
    provider_instance_binding_hash: _Sha256Digest
    activated_at_us: _PositiveEpochUs
    record_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_genesis(self) -> "_ProviderAuthorityGenesis":
        if (
            self.provider_authority_kind
            != _PROVIDER_AUTHORITY_KIND_BY_CONTRACT[self.provider_contract_type]
        ):
            raise ValueError("provider authority kind does not match its closed contract type")
        if self.record_hash != canonical_sha256(
            self.model_dump(mode="python", exclude={"record_hash"})
        ):
            raise ValueError("provider authority genesis hash must bind the exact trust root")
        return self


class _ProviderAuthorityEvent(_FrozenPublicContract):
    record_type: Literal["EXTERNAL_PROVIDER_AUTHORITY_EVENT_V1"]
    authority_id: _NonEmptyText
    event_ordinal: _PositiveOrdinal
    subject_contract_type: _ExternalProviderContractType
    subject_sha256: _Sha256Digest
    event_at_us: _PositiveEpochUs
    previous_record_hash: _Sha256Digest
    record_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_event_hash(self) -> "_ProviderAuthorityEvent":
        if self.record_hash != canonical_sha256(
            self.model_dump(mode="python", exclude={"record_hash"})
        ):
            raise ValueError("provider authority event hash must bind the exact event")
        return self


class _ProviderAuthorityProjection(_FrozenPublicContract):
    record_type: Literal["EXTERNAL_PROVIDER_AUTHORITY_PROJECTION_V1"]
    provider_contract_type: _ExternalProviderContractType
    authority_id: _NonEmptyText
    authority_genesis: _ProviderAuthorityGenesis
    genesis_record_hash: _Sha256Digest
    ordered_events: Annotated[tuple[_ProviderAuthorityEvent, ...], Field(min_length=1)]
    authority_head: _Sha256Digest
    canonical_projection_sha256: _Sha256Digest
    canonical_projection_byte_length: int = Field(
        gt=0,
        le=_TYPED_EVIDENCE_MAX_BYTES,
    )
    content_addressed_path: _NonEmptyText
    scan_receipt: _ExactByteScanReceipt

    def _canonical_projection_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="python",
            exclude={
                "canonical_projection_sha256",
                "canonical_projection_byte_length",
                "content_addressed_path",
                "scan_receipt",
            },
        )

    @model_validator(mode="after")
    def _validate_complete_projection(self) -> "_ProviderAuthorityProjection":
        if (
            self.authority_genesis.authority_id != self.authority_id
            or self.authority_genesis.provider_contract_type
            != self.provider_contract_type
            or self.authority_genesis.record_hash != self.genesis_record_hash
        ):
            raise ValueError("provider projection must embed its exact typed authority genesis")
        previous_hash = self.genesis_record_hash
        previous_time = self.authority_genesis.activated_at_us
        for expected_ordinal, event in enumerate(self.ordered_events, start=1):
            if (
                event.authority_id != self.authority_id
                or event.subject_contract_type != self.provider_contract_type
                or event.event_ordinal != expected_ordinal
                or event.previous_record_hash != previous_hash
                or event.event_at_us <= previous_time
            ):
                raise ValueError("provider authority projection must be one complete chain prefix")
            previous_hash = event.record_hash
            previous_time = event.event_at_us
        if self.authority_head != previous_hash:
            raise ValueError("provider authority head must equal the exact terminal event")
        projection_bytes = canonical_json_bytes(self._canonical_projection_payload())
        if (
            self.canonical_projection_sha256 != sha256_bytes(projection_bytes)
            or self.canonical_projection_byte_length != len(projection_bytes)
        ):
            raise ValueError("provider projection locator must bind its exact canonical bytes")
        expected_path = (
            f"{_SIDECAR_ROOT}/provider-{self.provider_contract_type.casefold()}/"
            f"sha256-{self.canonical_projection_sha256}.json"
        )
        if self.content_addressed_path != expected_path:
            raise ValueError("provider projection path must content-address its exact bytes")
        if (
            self.scan_receipt.sidecar_kind != "PROVIDER_AUTHORITY_PROJECTION"
            or self.scan_receipt.target_sha256 != self.canonical_projection_sha256
            or self.scan_receipt.target_byte_length
            != self.canonical_projection_byte_length
        ):
            raise ValueError("provider projection scan targets different exact bytes")
        return self


class _CanonicalTypedEvidence(_FrozenPublicContract):
    """Exact public payload plus its independently recomputable type and digest."""

    contract_type: _EmbeddedResearchContractType
    canonical_payload: dict[str, Any]
    canonical_payload_sha256: _Sha256Digest
    canonical_payload_byte_length: int = Field(gt=0, le=_TYPED_EVIDENCE_MAX_BYTES)
    content_addressed_path: _NonEmptyText
    scan_receipt: _ExactByteScanReceipt
    provider_subject_context: _ProviderSubjectContext | None = None
    provider_authority_projection: _ProviderAuthorityProjection | None = None

    @field_validator("canonical_payload")
    @classmethod
    def _validate_safe_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_public_json(value)
        return value

    @model_validator(mode="after")
    def _validate_exact_typed_payload(self) -> "_CanonicalTypedEvidence":
        payload_bytes = canonical_json_bytes(self.canonical_payload)
        if sha256_bytes(payload_bytes) != self.canonical_payload_sha256:
            raise ValueError("embedded evidence digest must bind its exact canonical payload")
        if len(payload_bytes) != self.canonical_payload_byte_length:
            raise ValueError("embedded evidence length must bind its exact canonical payload")
        if self.content_addressed_path != _typed_evidence_path(
            self.contract_type,
            self.canonical_payload_sha256,
        ):
            raise ValueError("embedded evidence path must content-address its exact bytes")
        if (
            self.scan_receipt.sidecar_kind != "CANONICAL_TYPED_EVIDENCE"
            or self.scan_receipt.target_sha256 != self.canonical_payload_sha256
            or self.scan_receipt.target_byte_length != self.canonical_payload_byte_length
        ):
            raise ValueError("embedded evidence scan targets different canonical bytes")

        # Lazy import preserves direct importability of this C9 module while still
        # applying the authoritative C4-C7 contract validators at construction.
        from . import research as research_contracts

        contract = getattr(research_contracts, self.contract_type, None)
        if contract is None:
            raise ValueError("embedded evidence names an unavailable research contract")
        contract.model_validate(self.canonical_payload, strict=True)
        is_external = self.contract_type in _EXTERNAL_PROVIDER_TYPES
        if is_external != (self.provider_authority_projection is not None):
            raise ValueError("external provider evidence requires one exact authority projection")
        if is_external != (self.provider_subject_context is not None):
            raise ValueError("external provider evidence requires one closed subject context")
        if is_external:
            projection = self.provider_authority_projection
            context = self.provider_subject_context
            assert projection is not None and context is not None
            if projection.provider_contract_type != self.contract_type:
                raise ValueError("provider projection contract type mismatch")
            payload_authority_id = self.canonical_payload["authority_id"]
            payload_authority_head = self.canonical_payload["authority_head"]
            excluded_self_hash = {
                "CodeManifestEvidence": "evidence_hash",
                "ExecutionCodeAttestation": "attestation_hash",
                "ImmutableArtifactLease": "lease_hash",
            }.get(self.contract_type)
            stable_provider_payload = {
                key: value
                for key, value in self.canonical_payload.items()
                if key not in {"authority_id", "authority_head", excluded_self_hash}
            }
            expected_subject_hash = canonical_sha256(
                {
                    "contract_type": self.contract_type,
                    "stable_provider_payload": stable_provider_payload,
                    "provider_subject_context": context.model_dump(mode="python"),
                }
            )
            if (
                projection.authority_id != payload_authority_id
                or projection.authority_head != payload_authority_head
                or projection.ordered_events[-1].subject_sha256
                != expected_subject_hash
            ):
                raise ValueError("provider authority chain must issue the exact contextual evidence")
            expected_context_type: type[_FrozenPublicContract] | tuple[
                type[_FrozenPublicContract], ...
            ] = {
                "ObservedPitDatasetHandle": _ObservedPitProviderSubjectContext,
                "CodeManifestEvidence": (
                    _AuthorityActivationCodeManifestContext,
                    _ResearchExecutionCodeManifestContext,
                ),
                "ExecutionCodeAttestation": _ExecutionProviderSubjectContext,
                "ImmutableArtifactLease": _LeaseProviderSubjectContext,
            }[self.contract_type]
            if not isinstance(context, expected_context_type):
                raise ValueError("provider evidence requires its exact closed subject context")

        return self


def _require_evidence_type(
    evidence: _CanonicalTypedEvidence,
    expected: _EmbeddedResearchContractType,
) -> dict[str, Any]:
    if evidence.contract_type != expected:
        raise ValueError(f"embedded evidence requires {expected}")
    return evidence.canonical_payload


def _provider_projection_strictly_extends(
    ancestor: _ProviderAuthorityProjection,
    descendant: _ProviderAuthorityProjection,
) -> bool:
    """Return whether two exact provider projections prove one strict prefix."""

    return (
        ancestor.provider_contract_type == descendant.provider_contract_type
        and ancestor.authority_id == descendant.authority_id
        and ancestor.authority_genesis == descendant.authority_genesis
        and ancestor.genesis_record_hash == descendant.genesis_record_hash
        and len(ancestor.ordered_events) < len(descendant.ordered_events)
        and ancestor.ordered_events
        == descendant.ordered_events[: len(ancestor.ordered_events)]
    )


def _decoded_sidecar_json(sidecar: _ExactByteSidecar) -> dict[str, Any]:
    decoded = base64.b64decode(sidecar.content_base64, validate=True)
    loaded = load_canonical_json_bytes(decoded)
    if not isinstance(loaded, dict):
        raise ValueError("schema manifest sidecar must contain one canonical JSON object")
    return loaded


def _iter_manifest_keys(value: Any) -> tuple[str, ...]:
    if isinstance(value, dict):
        return tuple(value) + tuple(
            nested
            for child in value.values()
            for nested in _iter_manifest_keys(child)
        )
    if isinstance(value, list):
        return tuple(
            nested for child in value for nested in _iter_manifest_keys(child)
        )
    return ()


def _validate_deployment_neutral_schema_manifest(
    schema_manifest: dict[str, Any],
) -> None:
    required_manifest_fields = {
        "manifest_schema_version": "FAMILY_SCHEMA_V3_MANIFEST_V1",
        "component_order": ["C4", "C5", "C6", "C7", "C8", "C9"],
        "authority_table_count": 33,
        "external_provider_contract_types": sorted(_EXTERNAL_PROVIDER_TYPES),
        "external_provider_proof_schema_version": (
            "EXTERNAL_PROVIDER_AUTHORITY_PROJECTION_V1"
        ),
        "semantic_verifier_registry_version": (
            "FAMILY_SCHEMA_V3_SEMANTIC_VERIFIER_REGISTRY_V1"
        ),
    }
    if any(
        schema_manifest.get(name) != expected
        for name, expected in required_manifest_fields.items()
    ):
        raise ValueError("sealed schema manifest lacks the frozen deployment-neutral fields")

    forbidden_deployment_keys = {
        "authorized_external_provider_genesis_hashes",
        "provider_trust_roots",
        "deployment_id",
        "authority_instance_id",
    }
    if forbidden_deployment_keys.intersection(_iter_manifest_keys(schema_manifest)):
        raise ValueError("canonical schema manifest must remain deployment neutral")


class DataSnoopingEvidenceRef(_FrozenPublicContract):
    record_type: Literal["DATA_SNOOPING_EVIDENCE_REF_V1"]
    artifact_kind: Literal["DATA_SNOOPING_FAMILY_ARTIFACT_V1"]
    artifact_schema_version: Literal["DATA_SNOOPING_ARTIFACT_CORE_V1"]
    authority_instance_id: _NonEmptyText
    genesis_record_hash: _Sha256Digest
    authority_lineage_hash: _Sha256Digest
    family_id: _NonEmptyText
    selection_scope_id: _NonEmptyText
    selection_decision_id: _NonEmptyText
    decision_class: Literal["PUBLISHABLE"]
    winner_config_hash: _Sha256Digest
    closure_manifest_hash: _Sha256Digest
    family_assessment_conclusion_hash: _Sha256Digest
    assessment_status: Literal["ASSESSED"]
    diagnostic_verdict: _ResolvedDiagnosticVerdict
    promotion_effect: Literal["NONE"]
    published_artifact_envelope_hash: _Sha256Digest
    published_artifact_path: _NonEmptyText
    artifact_publication_receipt_id: _NonEmptyText
    artifact_publication_receipt_hash: _Sha256Digest
    publication_projection_hash: _Sha256Digest
    publication_projection_path: _NonEmptyText
    publication_chain_head: _Sha256Digest

    @model_validator(mode="after")
    def _validate_ref(self) -> "DataSnoopingEvidenceRef":
        if self.authority_lineage_hash != _lineage_hash(
            self.authority_instance_id,
            self.genesis_record_hash,
        ):
            raise ValueError("evidence reference authority lineage mismatch")
        if self.published_artifact_path != _content_path(
            _ENVELOPE_ROOT,
            self.published_artifact_envelope_hash,
        ):
            raise ValueError("published artifact path must address its exact envelope bytes")
        if self.publication_projection_path != _content_path(
            _PROJECTION_ROOT,
            self.publication_projection_hash,
        ):
            raise ValueError("publication projection path must address its exact bytes")
        if self.publication_chain_head != self.artifact_publication_receipt_hash:
            raise ValueError("evidence reference must bind the receipt at the publication chain head")
        return self


class ArtifactPublicationProjectionRef(_FrozenPublicContract):
    """Neutral locator for every immutable publication projection branch.

    Unlike ``DataSnoopingEvidenceRef`` this value is not an assertion that the
    family is assessed or publishable.  It can therefore address both the
    assessed evidence branch and an honestly diagnostic-only publication.
    """

    record_type: Literal["ARTIFACT_PUBLICATION_PROJECTION_REF_V1"]
    projection_schema_version: Literal[
        "ARTIFACT_PUBLICATION_RECEIPT_PROJECTION_V1"
    ]
    authority_instance_id: _NonEmptyText
    genesis_record_hash: _Sha256Digest
    authority_lineage_hash: _Sha256Digest
    family_id: _NonEmptyText
    decision_class: Literal["PUBLISHABLE", "DIAGNOSTIC_ONLY"]
    assessment_status: _AssessmentStatus
    artifact_publication_receipt_id: _NonEmptyText
    artifact_publication_receipt_hash: _Sha256Digest
    publication_projection_hash: _Sha256Digest
    publication_projection_path: _NonEmptyText
    publication_chain_head: _Sha256Digest

    @model_validator(mode="after")
    def _validate_projection_ref(self) -> "ArtifactPublicationProjectionRef":
        if self.authority_lineage_hash != _lineage_hash(
            self.authority_instance_id,
            self.genesis_record_hash,
        ):
            raise ValueError("publication projection reference authority lineage mismatch")
        if self.publication_projection_path != _content_path(
            _PROJECTION_ROOT,
            self.publication_projection_hash,
        ):
            raise ValueError("publication projection reference path must address exact bytes")
        if self.publication_chain_head != self.artifact_publication_receipt_hash:
            raise ValueError("publication projection reference must bind the chain head")
        if (self.assessment_status, self.decision_class) not in {
            ("ASSESSED", "PUBLISHABLE"),
            ("NOT_ASSESSABLE", "DIAGNOSTIC_ONLY"),
        }:
            raise ValueError("publication projection reference branch is inconsistent")
        return self


class ArtifactCore(_FrozenPublicContract):
    record_type: Literal["DATA_SNOOPING_ARTIFACT_CORE_V1"]
    artifact_kind: Literal["DATA_SNOOPING_FAMILY_ARTIFACT_V1"]
    artifact_schema_version: Literal["DATA_SNOOPING_ARTIFACT_CORE_V1"]
    canonical_json_version: Literal["QUANTOS_CANONICAL_JSON_V1"]
    authority_instance_id: _NonEmptyText
    genesis_record_hash: _Sha256Digest
    authority_lineage_hash: _Sha256Digest
    family_id: _NonEmptyText
    selection_scope_id: _NonEmptyText
    selection_decision_id: _NonEmptyText
    winner_config_hash: _Sha256Digest | None
    winner_evaluation_receipt_hash: _Sha256Digest | None
    decision_class: Literal["PUBLISHABLE", "DIAGNOSTIC_ONLY"]
    declaration_record_hash: _Sha256Digest
    freeze_record_hash: _Sha256Digest
    closure_manifest_hash: _Sha256Digest
    closure_status: _ClosureStatus
    assessment_status: _AssessmentStatus
    diagnostic_verdict: _DiagnosticVerdict
    promotion_effect: Literal["NONE"]
    ordered_grid_assessment_record_hashes: tuple[_Sha256Digest, ...]
    family_assessment_conclusion_hash: _Sha256Digest
    authority_projection: _AuthorityProjection
    authority_projection_hash: _Sha256Digest
    pre_publication_research_authority_head: _Sha256Digest
    selection_scope_evidence: _CanonicalTypedEvidence
    selection_decision_evidence: _CanonicalTypedEvidence
    evaluation_batch_evidence: _CanonicalTypedEvidence | None
    ordered_candidate_receipt_evidence: tuple[_CanonicalTypedEvidence, ...]
    benchmark_receipt_evidence: _CanonicalTypedEvidence | None
    trial_ledger_proof_evidence: _CanonicalTypedEvidence
    family_schema_v3_manifest_sidecar: _ExactByteSidecar
    trial_ledger_history_sidecar: _ExactByteSidecar
    trial_ledger_snapshot_sidecar: _ExactByteSidecar
    trial_ledger_seal_sidecar: _ExactByteSidecar
    observed_pit_evidence: _CanonicalTypedEvidence
    activation_code_manifest_evidence: _CanonicalTypedEvidence
    code_manifest_evidence: _CanonicalTypedEvidence
    start_execution_attestation_evidence: _CanonicalTypedEvidence | None
    terminal_execution_attestation_evidence: _CanonicalTypedEvidence | None
    immutable_artifact_lease_evidence: _CanonicalTypedEvidence | None
    ordered_candidate_disposition_evidence: tuple[_CanonicalTypedEvidence, ...]
    public_proposals: tuple[_PublicProposal, ...]
    candidate_disposition_record_hashes: tuple[_Sha256Digest, ...]
    ordered_period_keys: tuple[Any, ...]
    ordered_candidate_config_hashes: tuple[_Sha256Digest, ...]
    candidate_gross_returns: tuple[tuple[str, ...], ...]
    candidate_costs: tuple[tuple[str, ...], ...]
    candidate_net_returns: tuple[tuple[str, ...], ...]
    candidate_turnovers: tuple[tuple[str, ...], ...]
    benchmark_gross_returns: tuple[str, ...]
    benchmark_costs: tuple[str, ...]
    benchmark_net_returns: tuple[str, ...]
    benchmark_turnovers: tuple[str, ...]
    evaluation_batch_receipt_hash: _Sha256Digest | None
    benchmark_receipt_hash: _Sha256Digest | None
    ordered_candidate_receipt_hashes: tuple[_Sha256Digest, ...]
    trial_ledger_proof_hash: _Sha256Digest
    failure_reasons: tuple[_PublicToken, ...]

    @field_validator("ordered_period_keys", mode="before")
    @classmethod
    def _validate_typed_period_keys(cls, value: Any) -> tuple[Any, ...]:
        from .research import PeriodKey

        if not isinstance(value, (tuple, list)):
            raise ValueError("ordered period keys must be a finite sequence")
        normalized: list[PeriodKey] = []
        for item in value:
            if isinstance(item, PeriodKey):
                normalized.append(item)
            elif isinstance(item, dict):
                normalized.append(PeriodKey.model_validate(item, strict=True))
            else:
                raise ValueError("ordered period keys require exact PeriodKey values")
        return tuple(normalized)

    @model_validator(mode="after")
    def _validate_core(self) -> "ArtifactCore":
        if self.authority_lineage_hash != _lineage_hash(
            self.authority_instance_id,
            self.genesis_record_hash,
        ):
            raise ValueError("artifact core authority lineage mismatch")
        if (
            self.authority_projection.authority_instance_id != self.authority_instance_id
            or self.authority_projection.genesis_record_hash != self.genesis_record_hash
        ):
            raise ValueError("authority projection belongs to a different authority lineage")
        if self.authority_projection_hash != canonical_sha256(self.authority_projection):
            raise ValueError("authority_projection_hash must bind the exact projection")
        projected_by_table = {
            row.table_name: row
            for row in self.authority_projection.canonical_rows
            if row.table_name != "data_snooping_grid_assessments"
        }
        if any(
            row.canonical_row_payload.get("family_id", self.family_id) != self.family_id
            for row in self.authority_projection.canonical_rows
        ):
            raise ValueError("authority projection contains a foreign family row")
        top_level_parent_hashes = {
            "data_snooping_declarations": self.declaration_record_hash,
            "data_snooping_family_freezes": self.freeze_record_hash,
            "data_snooping_family_closures": self.closure_manifest_hash,
            "data_snooping_assessments": self.family_assessment_conclusion_hash,
        }
        if any(
            projected_by_table[table_name].record_hash != expected_hash
            for table_name, expected_hash in top_level_parent_hashes.items()
        ):
            raise ValueError("artifact core parent hashes must equal the exact projected rows")
        closure_payload = projected_by_table[
            "data_snooping_family_closures"
        ].canonical_row_payload
        declaration_payload = projected_by_table[
            "data_snooping_declarations"
        ].canonical_row_payload
        freeze_payload = projected_by_table[
            "data_snooping_family_freezes"
        ].canonical_row_payload
        assessment_payload = projected_by_table[
            "data_snooping_assessments"
        ].canonical_row_payload
        if (
            declaration_payload["family_id"] != self.family_id
            or declaration_payload["selection_scope_id"] != self.selection_scope_id
            or declaration_payload["selection_decision_id"]
            != self.selection_decision_id
            or declaration_payload["decision_class"] != self.decision_class
            or freeze_payload["family_id"] != self.family_id
            or freeze_payload["declaration_id"] != declaration_payload["declaration_id"]
            or freeze_payload["declaration_record_hash"]
            != self.declaration_record_hash
            or closure_payload["family_id"] != self.family_id
            or closure_payload["declaration_id"]
            != declaration_payload["declaration_id"]
            or closure_payload["declaration_record_hash"]
            != self.declaration_record_hash
            or closure_payload["freeze_id"] != freeze_payload["freeze_id"]
            or closure_payload["freeze_record_hash"] != self.freeze_record_hash
            or closure_payload["selection_scope_id"] != self.selection_scope_id
            or closure_payload["selection_decision_id"]
            != self.selection_decision_id
            or closure_payload["closure_status"] != self.closure_status
            or assessment_payload["family_id"] != self.family_id
            or assessment_payload["closure_manifest_hash"]
            != self.closure_manifest_hash
            or assessment_payload["assessment_status"] != self.assessment_status
            or assessment_payload["diagnostic_verdict"] != self.diagnostic_verdict
            or assessment_payload["promotion_effect"] != self.promotion_effect
        ):
            raise ValueError("artifact summary must equal its exact family authority topology")
        research_chain_head = next(
            head
            for head in self.authority_projection.chain_heads
            if head.table_name == "research_authority_events"
            and head.partition_key == {"authority_instance_id": self.authority_instance_id}
        )
        if (
            closure_payload["selection_scope_history_head"]
            != self.authority_projection.selection_scope_history_head
            or self.pre_publication_research_authority_head
            != research_chain_head.head_record_hash
        ):
            raise ValueError("artifact core must bind the closure ancestor and captured event head")
        projected_grid_rows = sorted(
            (
                row
                for row in self.authority_projection.canonical_rows
                if row.table_name == "data_snooping_grid_assessments"
            ),
            key=lambda row: (
                row.canonical_row_payload["block_length"],
                row.canonical_row_payload["schedule_ordinal"],
            ),
        )
        if any(
            row.canonical_row_payload["family_id"] != self.family_id
            or row.canonical_row_payload["closure_manifest_hash"]
            != self.closure_manifest_hash
            or row.canonical_row_payload["promotion_effect"] != self.promotion_effect
            for row in projected_grid_rows
        ):
            raise ValueError("grid assessment history belongs to another family closure")
        if self.ordered_grid_assessment_record_hashes != tuple(
            row.record_hash for row in projected_grid_rows
        ):
            raise ValueError("artifact core grid history must equal every projected grid row in order")
        if self.family_assessment_conclusion_hash in self.ordered_grid_assessment_record_hashes:
            raise ValueError("family conclusion must be separate from grid assessment history")
        if len(set(self.ordered_grid_assessment_record_hashes)) != len(
            self.ordered_grid_assessment_record_hashes
        ):
            raise ValueError("ordered grid assessment hashes must be unique")
        proposal_ids = tuple(proposal.proposal_id for proposal in self.public_proposals)
        if len(set(proposal_ids)) != len(proposal_ids):
            raise ValueError("public proposal identities must be unique")
        if len(self.candidate_disposition_record_hashes) != len(self.public_proposals):
            raise ValueError("every public proposal requires one disposition record")
        if len(set(self.candidate_disposition_record_hashes)) != len(self.candidate_disposition_record_hashes):
            raise ValueError("candidate disposition record hashes must be unique")
        if (
            freeze_payload["raw_proposal_count"] != len(self.public_proposals)
            or closure_payload["raw_proposal_count"] != len(self.public_proposals)
            or closure_payload["ordered_proposal_ids_hash"]
            != canonical_sha256(proposal_ids)
            or closure_payload["ordered_disposition_record_hashes_hash"]
            != canonical_sha256(self.candidate_disposition_record_hashes)
        ):
            raise ValueError("proposal and disposition vectors must close the authority counts")
        _validate_artifact_proposal_graph(
            self,
            freeze_payload=freeze_payload,
            closure_payload=closure_payload,
        )
        if self.failure_reasons != tuple(sorted(self.failure_reasons)) or len(set(self.failure_reasons)) != len(
            self.failure_reasons
        ):
            raise ValueError("failure reasons must be sorted and unique")

        scope_payload = _require_evidence_type(
            self.selection_scope_evidence,
            "SelectionScopeRef",
        )
        decision_payload = _require_evidence_type(
            self.selection_decision_evidence,
            "SelectionDecisionRef",
        )
        trial_payload = _require_evidence_type(
            self.trial_ledger_proof_evidence,
            "TrialLedgerProof",
        )
        if scope_payload["selection_scope_id"] != self.selection_scope_id:
            raise ValueError("selection scope summary must equal its exact embedded evidence")
        if (
            decision_payload["selection_decision_id"] != self.selection_decision_id
            or decision_payload["selection_scope_id"] != self.selection_scope_id
            or decision_payload["family_id"] != self.family_id
            or decision_payload["decision_class"] != self.decision_class
        ):
            raise ValueError("selection decision summary must equal its exact embedded evidence")
        if trial_payload["proof_hash"] != self.trial_ledger_proof_hash:
            raise ValueError("trial-ledger summary hash must equal its exact embedded proof")
        sidecars = (
            self.family_schema_v3_manifest_sidecar,
            self.trial_ledger_history_sidecar,
            self.trial_ledger_snapshot_sidecar,
            self.trial_ledger_seal_sidecar,
        )
        if tuple(sidecar.sidecar_kind for sidecar in sidecars) != (
            "FAMILY_SCHEMA_V3_MANIFEST",
            "TRIAL_LEDGER_HISTORY",
            "TRIAL_LEDGER_SNAPSHOT_SQLITE",
            "TRIAL_LEDGER_SEAL",
        ):
            raise ValueError("artifact core requires exactly one sidecar in every closed slot")
        typed_evidence = (
            self.selection_scope_evidence,
            self.selection_decision_evidence,
            self.trial_ledger_proof_evidence,
            *self.ordered_candidate_disposition_evidence,
            *self.ordered_candidate_receipt_evidence,
            *(
                (self.evaluation_batch_evidence,)
                if self.evaluation_batch_evidence is not None
                else ()
            ),
            *(
                (self.benchmark_receipt_evidence,)
                if self.benchmark_receipt_evidence is not None
                else ()
            ),
        )
        if sum(sidecar.decoded_byte_length for sidecar in sidecars) + sum(
            evidence.canonical_payload_byte_length for evidence in typed_evidence
        ) > _SIDECAR_TOTAL_MAX_BYTES:
            raise ValueError("artifact sidecar set exceeds its closed total decoded-byte limit")
        if (
            trial_payload["history_artifact_id"]
            != f"sha256:{self.trial_ledger_history_sidecar.decoded_sha256}"
            or trial_payload["history_artifact_sha256"]
            != self.trial_ledger_history_sidecar.decoded_sha256
            or trial_payload["snapshot_artifact_id"]
            != f"sha256:{self.trial_ledger_snapshot_sidecar.decoded_sha256}"
            or trial_payload["snapshot_artifact_sha256"]
            != self.trial_ledger_snapshot_sidecar.decoded_sha256
            or trial_payload["seal_artifact_id"]
            != f"sha256:{self.trial_ledger_seal_sidecar.decoded_sha256}"
            or trial_payload["seal_artifact_sha256"]
            != self.trial_ledger_seal_sidecar.decoded_sha256
        ):
            raise ValueError("trial-ledger proof must bind all three exact-byte sidecars")

        observed_payload = _require_evidence_type(
            self.observed_pit_evidence,
            "ObservedPitDatasetHandle",
        )
        activation_manifest_payload = _require_evidence_type(
            self.activation_code_manifest_evidence,
            "CodeManifestEvidence",
        )
        code_manifest_payload = _require_evidence_type(
            self.code_manifest_evidence,
            "CodeManifestEvidence",
        )
        mandatory_external_evidence = (
            self.observed_pit_evidence,
            self.activation_code_manifest_evidence,
            self.code_manifest_evidence,
        )
        observed_context = self.observed_pit_evidence.provider_subject_context
        activation_context = self.activation_code_manifest_evidence.provider_subject_context
        code_context = self.code_manifest_evidence.provider_subject_context
        if (
            not isinstance(observed_context, _ObservedPitProviderSubjectContext)
            or not isinstance(
                activation_context,
                _AuthorityActivationCodeManifestContext,
            )
            or not isinstance(
                code_context,
                _ResearchExecutionCodeManifestContext,
            )
            or observed_context.family_id != self.family_id
            or code_context.family_id != self.family_id
            or activation_context.authority_instance_id != self.authority_instance_id
            or activation_context.sealed_v3_manifest_hash
            != self.family_schema_v3_manifest_sidecar.decoded_sha256
            or activation_context.migration_id != "FAMILY_SCHEMA_V2_TO_V3_V1"
        ):
            raise ValueError("external provider evidence requires its exact closed subject context")
        if (
            activation_manifest_payload["worktree_state"] != "CLEAN"
            or code_manifest_payload["worktree_state"] != "CLEAN"
        ):
            raise ValueError("publishable evidence requires clean activation and execution code")
        activation_projection = (
            self.activation_code_manifest_evidence.provider_authority_projection
        )
        code_projection = self.code_manifest_evidence.provider_authority_projection
        if (
            activation_projection is None
            or code_projection is None
            or not _provider_projection_strictly_extends(
                activation_projection,
                code_projection,
            )
        ):
            raise ValueError("research code authority must strictly extend its activation evidence")

        schema_manifest = _decoded_sidecar_json(self.family_schema_v3_manifest_sidecar)
        _validate_deployment_neutral_schema_manifest(schema_manifest)

        observed_bindings = (
            ("observed_pit_authority_id", "authority_id"),
            ("observed_pit_handle_id", "handle_id"),
            ("observed_pit_content_hash", "content_hash"),
            ("knowledge_time_policy_id", "knowledge_time_policy_id"),
            ("knowledge_time_policy_version", "knowledge_time_policy_version"),
            ("observed_pit_authority_head", "authority_head"),
        )
        code_bindings = (
            ("code_manifest_policy_id", ("policy_ref", "policy_id")),
            ("code_manifest_policy_version", ("policy_ref", "policy_version")),
            ("code_manifest_policy_hash", ("policy_ref", "policy_hash")),
            ("code_manifest_evidence_hash", ("evidence_hash",)),
            ("code_manifest_authority_id", ("authority_id",)),
            ("code_manifest_authority_head", ("authority_head",)),
        )

        def nested_value(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
            current: Any = payload
            for part in path:
                current = current[part]
            return current

        for authority_payload in (declaration_payload, closure_payload):
            if any(
                authority_payload[local_name] != observed_payload[provider_name]
                for local_name, provider_name in observed_bindings
            ) or any(
                authority_payload[local_name]
                != nested_value(code_manifest_payload, provider_path)
                for local_name, provider_path in code_bindings
            ):
                raise ValueError("authority rows must bind the exact observed-PIT and code evidence")

        start_evidence = self.start_execution_attestation_evidence
        terminal_evidence = self.terminal_execution_attestation_evidence
        lease_evidence = self.immutable_artifact_lease_evidence
        closure_start_hash = closure_payload["start_execution_attestation_hash"]
        closure_terminal_hash = closure_payload["terminal_execution_attestation_hash"]
        closure_lease_hash = closure_payload["immutable_artifact_lease_hash"]
        if self.closure_status == "COMPLETE":
            if any(value is None for value in (start_evidence, terminal_evidence, lease_evidence)):
                raise ValueError("complete closure requires start, terminal, and lease evidence")
        elif closure_start_hash is None and closure_terminal_hash is None and closure_lease_hash is None:
            if any(value is not None for value in (start_evidence, terminal_evidence, lease_evidence)):
                raise ValueError("not-started closure forbids execution provider evidence")
        elif closure_start_hash is not None and closure_terminal_hash is None and closure_lease_hash is not None:
            if start_evidence is None or lease_evidence is None or terminal_evidence is not None:
                raise ValueError("incomplete closure requires start and lease but no terminal evidence")
        else:
            raise ValueError("closure execution reference branch is structurally incomplete")

        external_evidence = (
            *mandatory_external_evidence,
            *(evidence for evidence in (start_evidence, terminal_evidence, lease_evidence) if evidence is not None),
        )
        provider_projection_bytes = sum(
            evidence.provider_authority_projection.canonical_projection_byte_length
            for evidence in external_evidence
            if evidence.provider_authority_projection is not None
        )
        if (
            sum(sidecar.decoded_byte_length for sidecar in sidecars)
            + sum(evidence.canonical_payload_byte_length for evidence in (*typed_evidence, *external_evidence))
            + provider_projection_bytes
            > _SIDECAR_TOTAL_MAX_BYTES
        ):
            raise ValueError("artifact exact-byte evidence set exceeds its closed total limit")

        if start_evidence is not None and lease_evidence is not None:
            start_payload = _require_evidence_type(
                start_evidence,
                "ExecutionCodeAttestation",
            )
            lease_payload = _require_evidence_type(
                lease_evidence,
                "ImmutableArtifactLease",
            )
            start_context = start_evidence.provider_subject_context
            lease_context = lease_evidence.provider_subject_context
            if (
                not isinstance(start_context, _ExecutionProviderSubjectContext)
                or not isinstance(lease_context, _LeaseProviderSubjectContext)
                or start_context.role != "START"
                or start_context.family_id != self.family_id
                or lease_context.family_id != self.family_id
                or (start_context.attempt_id, start_context.evaluation_batch_id)
                != (lease_context.attempt_id, lease_context.evaluation_batch_id)
            ):
                raise ValueError("start and lease contexts must bind one family attempt and batch")
            expected_attempt_id = closure_payload["attempt_id"]
            expected_batch_id = closure_payload["evaluation_batch_id"]
            if expected_batch_id is None:
                matching_attempt_rows = tuple(
                    row.canonical_row_payload
                    for row in self.authority_projection.canonical_rows
                    if row.table_name == "data_snooping_oos_access_attempts"
                    and row.canonical_row_payload["attempt_id"] == expected_attempt_id
                    and row.canonical_row_payload["attempt_event_id"]
                    == closure_payload["terminal_attempt_event_id"]
                )
                if len(matching_attempt_rows) != 1:
                    raise ValueError("incomplete closure requires its exact terminal attempt row")
                expected_batch_id = matching_attempt_rows[0]["evaluation_batch_id"]
            if (start_context.attempt_id, start_context.evaluation_batch_id) != (
                expected_attempt_id,
                expected_batch_id,
            ):
                raise ValueError("execution provider context belongs to another closure attempt")
            if (
                closure_start_hash != start_payload["attestation_hash"]
                or closure_lease_hash != lease_payload["lease_hash"]
                or closure_payload["immutable_artifact_lease_id"] != lease_payload["lease_id"]
                or closure_payload["execution_attestation_authority_id"]
                != start_payload["authority_id"]
                or closure_payload["start_execution_attestation_authority_head"]
                != start_payload["authority_head"]
                or closure_payload["immutable_artifact_lease_authority_id"]
                != lease_payload["authority_id"]
                or closure_payload["immutable_artifact_lease_authority_head"]
                != lease_payload["authority_head"]
            ):
                raise ValueError("closure execution references must bind exact provider payloads")
            code_start_pairs = (
                ("manifest_hash", "code_manifest_hash"),
                ("source_commit", "source_commit"),
                ("runtime_identity", "runtime_identity"),
                ("dependency_lock_hash", "dependency_lock_hash"),
            )
            lease_start_pairs = (
                ("lease_id", "lease_id"),
                ("code_manifest_hash", "code_manifest_hash"),
                ("artifact_id", "immutable_artifact_id"),
                ("artifact_digest", "immutable_artifact_digest"),
                ("process_image_hash", "process_image_hash"),
            )
            if any(
                code_manifest_payload[code_name] != start_payload[start_name]
                for code_name, start_name in code_start_pairs
            ) or any(
                lease_payload[lease_name] != start_payload[start_name]
                for lease_name, start_name in lease_start_pairs
            ):
                raise ValueError("code, start attestation, and lease continuity is broken")
            if terminal_evidence is None:
                if (
                    closure_payload["start_execution_attestation_authority_head"]
                    != start_payload["authority_head"]
                    or not (
                        lease_payload["acquired_at_us"]
                        <= start_payload["issued_at_us"]
                        <= closure_payload["closed_at_us"]
                        <= lease_payload["expires_at_us"]
                    )
                ):
                    raise ValueError("incomplete attempt chronology or authority head is invalid")
            else:
                terminal_payload = _require_evidence_type(
                    terminal_evidence,
                    "ExecutionCodeAttestation",
                )
                terminal_context = terminal_evidence.provider_subject_context
                start_projection = start_evidence.provider_authority_projection
                terminal_projection = terminal_evidence.provider_authority_projection
                if (
                    not isinstance(terminal_context, _ExecutionProviderSubjectContext)
                    or terminal_context.role != "TERMINAL"
                    or (
                        terminal_context.family_id,
                        terminal_context.attempt_id,
                        terminal_context.evaluation_batch_id,
                    )
                    != (
                        self.family_id,
                        start_context.attempt_id,
                        start_context.evaluation_batch_id,
                    )
                    or start_projection is None
                    or terminal_projection is None
                    or not _provider_projection_strictly_extends(
                        start_projection,
                        terminal_projection,
                    )
                ):
                    raise ValueError("terminal attestation must strictly extend the same start authority chain")
                continuity_fields = (
                    "code_manifest_hash",
                    "source_commit",
                    "runtime_identity",
                    "dependency_lock_hash",
                    "immutable_artifact_id",
                    "immutable_artifact_digest",
                    "entrypoint",
                    "process_image_hash",
                    "lease_id",
                )
                batch_payload_for_time = _require_evidence_type(
                    self.evaluation_batch_evidence,
                    "FamilyEvaluationBatchReceipt",
                ) if self.evaluation_batch_evidence is not None else None
                if (
                    start_payload["attestation_hash"] == terminal_payload["attestation_hash"]
                    or any(start_payload[name] != terminal_payload[name] for name in continuity_fields)
                    or closure_terminal_hash != terminal_payload["attestation_hash"]
                    or closure_payload["execution_attestation_authority_id"]
                    != terminal_payload["authority_id"]
                    or closure_payload["terminal_execution_attestation_authority_head"]
                    != terminal_payload["authority_head"]
                    or batch_payload_for_time is None
                    or not (
                        lease_payload["acquired_at_us"]
                        <= start_payload["issued_at_us"]
                        < terminal_payload["issued_at_us"]
                        <= batch_payload_for_time["committed_at_us"]
                        <= closure_payload["closed_at_us"]
                        <= lease_payload["expires_at_us"]
                    )
                ):
                    raise ValueError("complete execution evidence continuity or chronology is invalid")

        if len(self.ordered_candidate_disposition_evidence) != len(self.public_proposals):
            raise ValueError("every public proposal requires one exact disposition payload")
        projected_disposition_rows = {
            (
                row.canonical_row_payload["family_id"],
                row.canonical_row_payload["proposal_id"],
            ): row
            for row in self.authority_projection.canonical_rows
            if row.table_name == "data_snooping_candidate_dispositions"
        }
        if len(projected_disposition_rows) != len(self.public_proposals):
            raise ValueError("authority projection requires one disposition row per proposal")
        for proposal, disposition_hash, disposition_evidence in zip(
            self.public_proposals,
            self.candidate_disposition_record_hashes,
            self.ordered_candidate_disposition_evidence,
        ):
            disposition_payload = _require_evidence_type(
                disposition_evidence,
                "CandidateDisposition",
            )
            projected_disposition = projected_disposition_rows.get(
                (self.family_id, proposal.proposal_id)
            )
            business_fields = (
                "family_id",
                "proposal_id",
                "disposition",
                "config_hash",
                "evaluation_receipt_hash",
                "quarantine_record_id",
                "reason_code",
                "performance_accessed",
            )
            if (
                projected_disposition is None
                or projected_disposition.record_hash != disposition_hash
                or any(
                    projected_disposition.canonical_row_payload[name]
                    != (
                        int(disposition_payload[name])
                        if name == "performance_accessed"
                        else disposition_payload[name]
                    )
                    for name in business_fields
                )
            ):
                raise ValueError("candidate disposition must bind its exact C7 authority row")
            if (
                disposition_payload["family_id"] != self.family_id
                or disposition_payload["proposal_id"] != proposal.proposal_id
                or disposition_payload["disposition"] != proposal.disposition
            ):
                raise ValueError("candidate disposition payload must match its public proposal")
            if isinstance(proposal, _QuarantinedPublicProposal):
                if disposition_payload["quarantine_record_id"] != proposal.quarantine_record_id:
                    raise ValueError("quarantine disposition must bind the public opaque record id")

        performance_values: tuple[object, ...] = (
            self.ordered_period_keys,
            self.ordered_candidate_config_hashes,
            self.candidate_gross_returns,
            self.candidate_costs,
            self.candidate_net_returns,
            self.candidate_turnovers,
            self.benchmark_gross_returns,
            self.benchmark_costs,
            self.benchmark_net_returns,
            self.benchmark_turnovers,
            self.ordered_candidate_receipt_hashes,
            self.ordered_candidate_receipt_evidence,
        )
        optional_performance_hashes = (
            self.evaluation_batch_receipt_hash,
            self.benchmark_receipt_hash,
        )
        optional_performance_evidence = (
            self.evaluation_batch_evidence,
            self.benchmark_receipt_evidence,
        )
        if self.closure_status == "NOT_ASSESSABLE":
            if (
                self.assessment_status != "NOT_ASSESSABLE"
                or self.diagnostic_verdict != "NOT_APPLICABLE"
                or not self.failure_reasons
            ):
                raise ValueError("not-assessable closure requires a diagnostic-only deficiency branch")
            if (
                any(performance_values)
                or any(value is not None for value in optional_performance_hashes)
                or any(value is not None for value in optional_performance_evidence)
            ):
                raise ValueError("not-assessable closure forbids invented performance evidence")
            if self.winner_config_hash is not None or self.winner_evaluation_receipt_hash is not None:
                raise ValueError("not-assessable closure forbids an invented winner")
            return self

        if self.assessment_status == "ASSESSED":
            if self.diagnostic_verdict in {"NOT_APPLICABLE", "PENDING_MORE_REPLICATIONS"}:
                raise ValueError("assessed artifact core requires an effective diagnostic verdict")
            if not self.ordered_grid_assessment_record_hashes:
                raise ValueError("assessed artifact core requires grid assessment history")
            if self.failure_reasons:
                raise ValueError("assessed artifact core forbids failure reasons")
        elif (
            self.diagnostic_verdict != "NOT_APPLICABLE"
            or not self.failure_reasons
        ):
            raise ValueError("complete not-assessable core requires a diagnostic-only kernel failure")
        if any(value is None for value in optional_performance_hashes) or any(
            value is None for value in optional_performance_evidence
        ):
            raise ValueError("complete closure requires all receipt and proof hashes")
        if self.winner_config_hash is None or self.winner_evaluation_receipt_hash is None:
            raise ValueError("complete closure requires an exact selected winner")

        candidate_count = len(self.ordered_candidate_config_hashes)
        period_count = len(self.ordered_period_keys)
        if candidate_count == 0 or period_count == 0:
            raise ValueError("complete closure requires candidates and held-out periods")
        if self.ordered_candidate_config_hashes != tuple(sorted(self.ordered_candidate_config_hashes)):
            raise ValueError("candidate configs must be ordered by hash")
        if len(set(self.ordered_candidate_config_hashes)) != candidate_count:
            raise ValueError("candidate configs must be unique")
        if (
            len(self.ordered_candidate_receipt_hashes) != candidate_count
            or len(set(self.ordered_candidate_receipt_hashes)) != candidate_count
        ):
            raise ValueError("complete closure requires one unique receipt per candidate")
        if len(self.ordered_candidate_receipt_evidence) != candidate_count:
            raise ValueError("complete closure requires one exact payload per candidate receipt")
        matrices = (
            self.candidate_gross_returns,
            self.candidate_costs,
            self.candidate_net_returns,
            self.candidate_turnovers,
        )
        if any(len(matrix) != candidate_count for matrix in matrices):
            raise ValueError("candidate evidence matrices must match the candidate count")
        if any(len(row) != period_count for matrix in matrices for row in matrix):
            raise ValueError("candidate evidence rows must cover every ordered period")
        benchmark_vectors = (
            self.benchmark_gross_returns,
            self.benchmark_costs,
            self.benchmark_net_returns,
            self.benchmark_turnovers,
        )
        if any(len(vector) != period_count for vector in benchmark_vectors):
            raise ValueError("benchmark evidence must cover every ordered period")

        evaluation_batch_evidence = self.evaluation_batch_evidence
        benchmark_receipt_evidence = self.benchmark_receipt_evidence
        if evaluation_batch_evidence is None or benchmark_receipt_evidence is None:
            raise ValueError("complete closure requires exact batch and benchmark payloads")
        batch_payload = _require_evidence_type(
            evaluation_batch_evidence,
            "FamilyEvaluationBatchReceipt",
        )
        benchmark_payload = _require_evidence_type(
            benchmark_receipt_evidence,
            "BenchmarkReturnSeries",
        )
        candidate_payloads = tuple(
            _require_evidence_type(evidence, "CandidateReturnSeries")
            for evidence in self.ordered_candidate_receipt_evidence
        )
        if (
            batch_payload["authority_instance_id"] != self.authority_instance_id
            or batch_payload["genesis_record_hash"] != self.genesis_record_hash
            or batch_payload["family_id"] != self.family_id
            or batch_payload["batch_receipt_hash"] != self.evaluation_batch_receipt_hash
            or tuple(batch_payload["candidate_order"]) != self.ordered_candidate_config_hashes
            or tuple(batch_payload["candidate_receipt_hashes"])
            != self.ordered_candidate_receipt_hashes
            or batch_payload["benchmark_receipt_hash"] != self.benchmark_receipt_hash
            or batch_payload["ordered_period_keys_hash"]
            != canonical_sha256(self.ordered_period_keys)
        ):
            raise ValueError("evaluation batch summary must equal its exact embedded payload")
        if (
            benchmark_payload["family_id"] != self.family_id
            or benchmark_payload["evaluation_batch_id"] != batch_payload["evaluation_batch_id"]
            or benchmark_payload["benchmark_receipt_hash"] != self.benchmark_receipt_hash
            or scope_payload["deployment_mandate"]["benchmark_id"]
            != benchmark_payload["benchmark_id"]
        ):
            raise ValueError("benchmark summary must equal its exact embedded payload")

        disposition_by_proposal_id = {
            evidence.canonical_payload["proposal_id"]: evidence.canonical_payload
            for evidence in self.ordered_candidate_disposition_evidence
        }
        if len(disposition_by_proposal_id) != len(self.ordered_candidate_disposition_evidence):
            raise ValueError("candidate disposition payloads must have unique proposal identities")
        period_payloads = tuple(period.model_dump(mode="python") for period in self.ordered_period_keys)
        if tuple(benchmark_payload["series"]["ordered_period_keys"]) != period_payloads:
            raise ValueError("benchmark payload must bind the exact ordered period keys")
        if (
            tuple(benchmark_payload["series"]["gross_returns"])
            != self.benchmark_gross_returns
            or tuple(benchmark_payload["series"]["costs"]) != self.benchmark_costs
            or tuple(benchmark_payload["series"]["net_returns"]) != self.benchmark_net_returns
            or tuple(benchmark_payload["series"]["turnovers"])
            != self.benchmark_turnovers
        ):
            raise ValueError("benchmark vectors must equal their exact embedded series")
        if benchmark_payload["series"]["cost_contract_hash"] != batch_payload["cost_contract_hash"]:
            raise ValueError("benchmark cost contract must equal the evaluation batch contract")

        evaluated_dispositions: set[str] = set()
        for row_index, (candidate_evidence, candidate_payload) in enumerate(
            zip(self.ordered_candidate_receipt_evidence, candidate_payloads)
        ):
            series_payload = candidate_payload["series"]
            expected_receipt_hash = self.ordered_candidate_receipt_hashes[row_index]
            proposal_id = candidate_payload["source_proposal_id"]
            disposition_payload = disposition_by_proposal_id.get(proposal_id)
            if (
                candidate_payload["family_id"] != self.family_id
                or candidate_payload["evaluation_batch_id"] != batch_payload["evaluation_batch_id"]
                or candidate_payload["config_hash"]
                != self.ordered_candidate_config_hashes[row_index]
                or candidate_payload["evaluation_receipt_hash"] != expected_receipt_hash
                or candidate_evidence.canonical_payload_sha256
                != canonical_sha256(candidate_payload)
                or disposition_payload is None
                or disposition_payload["disposition"] != "PERFORMANCE_EVALUATED"
                or disposition_payload["config_hash"] != candidate_payload["config_hash"]
                or disposition_payload["evaluation_receipt_hash"] != expected_receipt_hash
            ):
                raise ValueError("candidate summary must equal its exact receipt and disposition")
            if proposal_id in evaluated_dispositions:
                raise ValueError("a public proposal may supply at most one candidate receipt")
            evaluated_dispositions.add(proposal_id)
            if tuple(series_payload["ordered_period_keys"]) != period_payloads:
                raise ValueError("candidate payload must bind the exact ordered period keys")
            if (
                tuple(series_payload["gross_returns"])
                != self.candidate_gross_returns[row_index]
                or tuple(series_payload["costs"]) != self.candidate_costs[row_index]
                or tuple(series_payload["net_returns"])
                != self.candidate_net_returns[row_index]
                or tuple(series_payload["turnovers"])
                != self.candidate_turnovers[row_index]
            ):
                raise ValueError("candidate vectors must equal their exact embedded series")
            if series_payload["cost_contract_hash"] != batch_payload["cost_contract_hash"]:
                raise ValueError("candidate cost contract must equal the evaluation batch contract")
        if {
            evidence.canonical_payload["proposal_id"]
            for evidence in self.ordered_candidate_disposition_evidence
            if evidence.canonical_payload["disposition"] == "PERFORMANCE_EVALUATED"
        } != evaluated_dispositions:
            raise ValueError("every evaluated disposition requires exactly one candidate receipt")
        if trial_payload["global_unique_config_count"] < candidate_count:
            raise ValueError("trial-ledger proof cannot undercount the evaluated candidate set")

        event_times = tuple(period.event_time for period in self.ordered_period_keys)
        if any(left >= right for left, right in zip(event_times, event_times[1:])):
            raise ValueError("period keys must be strictly increasing")
        if (
            len({period.frequency_id for period in self.ordered_period_keys}) != 1
            or len({period.label_horizon for period in self.ordered_period_keys}) != 1
        ):
            raise ValueError("period keys must share frequency and label horizon")
        for row_index in range(candidate_count):
            _validate_net_return_vectors(
                self.candidate_gross_returns[row_index],
                self.candidate_costs[row_index],
                self.candidate_net_returns[row_index],
                self.candidate_turnovers[row_index],
            )
        _validate_net_return_vectors(*benchmark_vectors)
        mean_net_differentials = tuple(
            fsum(
                parse_float64_hex(candidate_value) - parse_float64_hex(benchmark_value)
                for candidate_value, benchmark_value in zip(
                    self.candidate_net_returns[row_index],
                    self.benchmark_net_returns,
                )
            )
            / period_count
            for row_index in range(candidate_count)
        )
        winner_index = min(
            range(candidate_count),
            key=lambda index: (
                -mean_net_differentials[index],
                self.ordered_candidate_config_hashes[index],
            ),
        )
        if (
            self.winner_config_hash != self.ordered_candidate_config_hashes[winner_index]
            or self.winner_evaluation_receipt_hash
            != self.ordered_candidate_receipt_hashes[winner_index]
        ):
            raise ValueError("winner must be the exact mean-net-differential argmax")
        return self


class ArtifactCoreScanReceipt(_FrozenPublicContract):
    record_type: Literal["ARTIFACT_CORE_SCAN_RECEIPT_V1"]
    scan_receipt_schema_version: Literal["ARTIFACT_CORE_SCAN_RECEIPT_V1"]
    scan_target: Literal["ARTIFACT_CORE"]
    artifact_core_hash: _Sha256Digest
    artifact_core_byte_length: int = Field(gt=0)
    public_artifact_scan_policy_id: _PublicToken
    dlp_policy_id: _PublicToken
    dlp_policy_version: _PublicToken
    dlp_policy_hash: _Sha256Digest
    scanner_implementation: _PublicToken
    scanner_version: _PublicToken
    scan_status: Literal["ALLOW"]
    scan_result_hash: _Sha256Digest
    scanned_at_us: _PositiveEpochUs
    scan_receipt_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_receipt_hash(self) -> "ArtifactCoreScanReceipt":
        if self.scan_result_hash != _scan_result_hash(
            scan_target=self.scan_target,
            target_sha256=self.artifact_core_hash,
            target_byte_length=self.artifact_core_byte_length,
            public_artifact_scan_policy_id=self.public_artifact_scan_policy_id,
            dlp_policy_id=self.dlp_policy_id,
            dlp_policy_version=self.dlp_policy_version,
            dlp_policy_hash=self.dlp_policy_hash,
            scanner_implementation=self.scanner_implementation,
            scanner_version=self.scanner_version,
            scan_status=self.scan_status,
        ):
            raise ValueError("scan_result_hash must bind the exact artifact-core scan target")
        if self.scan_receipt_hash != canonical_sha256(self.model_dump(mode="python", exclude={"scan_receipt_hash"})):
            raise ValueError("scan_receipt_hash must cover the complete core scan receipt")
        return self


class PublishedArtifactEnvelope(_FrozenPublicContract):
    record_type: Literal["PUBLISHED_ARTIFACT_ENVELOPE_V1"]
    envelope_schema_version: Literal["PUBLISHED_ARTIFACT_ENVELOPE_V1"]
    artifact_core: ArtifactCore
    artifact_core_scan_receipt: ArtifactCoreScanReceipt

    @model_validator(mode="after")
    def _validate_exact_core_scan_binding(self) -> "PublishedArtifactEnvelope":
        core_bytes = canonical_json_bytes(self.artifact_core)
        if self.artifact_core_scan_receipt.artifact_core_hash != sha256_bytes(core_bytes):
            raise ValueError("core scan receipt must bind the exact embedded core bytes")
        if self.artifact_core_scan_receipt.artifact_core_byte_length != len(core_bytes):
            raise ValueError("core scan receipt byte length must match the embedded core")
        return self


class ArtifactPublicationReceipt(_FrozenPublicContract):
    record_type: Literal["ARTIFACT_PUBLICATION_RECEIPT_V1"]
    receipt_schema_version: Literal["ARTIFACT_PUBLICATION_RECEIPT_V1"]
    authority_instance_id: _NonEmptyText
    genesis_record_hash: _Sha256Digest
    observed_pit_authority_id: _NonEmptyText
    observed_pit_handle_id: _NonEmptyText
    observed_pit_content_hash: _Sha256Digest
    knowledge_time_policy_id: _NonEmptyText
    knowledge_time_policy_version: _NonEmptyText
    observed_pit_authority_head: _Sha256Digest
    oos_exposure_id: _NonEmptyText | None
    exposure_fingerprint: _Sha256Digest | None
    exposure_authority_id: _NonEmptyText | None
    exposure_authority_head: _Sha256Digest | None
    code_manifest_policy_id: _NonEmptyText
    code_manifest_policy_version: _NonEmptyText
    code_manifest_policy_hash: _Sha256Digest
    code_manifest_evidence_hash: _Sha256Digest
    code_manifest_authority_id: _NonEmptyText
    code_manifest_authority_head: _Sha256Digest
    start_execution_attestation_hash: _Sha256Digest | None
    terminal_execution_attestation_hash: _Sha256Digest | None
    execution_attestation_authority_id: _NonEmptyText | None
    start_execution_attestation_authority_head: _Sha256Digest | None
    terminal_execution_attestation_authority_head: _Sha256Digest | None
    immutable_artifact_lease_id: _NonEmptyText | None
    immutable_artifact_lease_hash: _Sha256Digest | None
    immutable_artifact_lease_authority_id: _NonEmptyText | None
    immutable_artifact_lease_authority_head: _Sha256Digest | None
    authority_lineage_hash: _Sha256Digest
    publication_receipt_id: _NonEmptyText
    publication_ordinal: _PositiveOrdinal
    previous_record_hash: _Sha256Digest
    family_id: _NonEmptyText
    selection_scope_id: _NonEmptyText
    selection_decision_id: _NonEmptyText
    winner_config_hash: _Sha256Digest | None
    winner_evaluation_receipt_hash: _Sha256Digest | None
    closure_manifest_hash: _Sha256Digest
    closure_status: _ClosureStatus
    attempt_status: Literal["COMPLETED", "NOT_STARTED", "INCOMPLETE"]
    family_assessment_conclusion_hash: _Sha256Digest
    ordered_grid_assessment_record_hashes_json: _NonEmptyText
    ordered_grid_assessment_record_hashes_hash: _Sha256Digest
    minimum_replication_count: Annotated[int, Field(ge=10_000)] | None
    assessment_status: _AssessmentStatus
    diagnostic_verdict: _DiagnosticVerdict
    decision_class: Literal["PUBLISHABLE", "DIAGNOSTIC_ONLY"]
    promotion_effect: Literal["NONE"]
    artifact_kind: Literal["DATA_SNOOPING_FAMILY_ARTIFACT_V1"]
    artifact_core_hash: _Sha256Digest
    artifact_core_scan_receipt_hash: _Sha256Digest
    published_artifact_envelope_hash: _Sha256Digest
    target_content_addressed_path: _NonEmptyText
    public_artifact_scan_policy_id: _PublicToken
    dlp_policy_id: _PublicToken
    dlp_policy_version: _PublicToken
    dlp_policy_hash: _Sha256Digest
    scanner_implementation: _PublicToken
    scanner_version: _PublicToken
    scan_status: Literal["ALLOW"]
    scan_result_hash: _Sha256Digest
    envelope_scanned_at_us: _PositiveEpochUs
    exporter_event_hash: _Sha256Digest
    exporter_authority_head: _Sha256Digest
    failure_reasons_json: _NonEmptyText
    published_at_us: _PositiveEpochUs
    receipt_record_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_publication_receipt(self) -> "ArtifactPublicationReceipt":
        if self.envelope_scanned_at_us > self.published_at_us:
            raise ValueError("final envelope scan must not occur after publication")
        if self.authority_lineage_hash != _lineage_hash(
            self.authority_instance_id,
            self.genesis_record_hash,
        ):
            raise ValueError("publication receipt authority lineage mismatch")
        if self.target_content_addressed_path != _content_path(
            _ENVELOPE_ROOT,
            self.published_artifact_envelope_hash,
        ):
            raise ValueError("publication target must content-address the exact envelope")

        grid_hashes = _load_canonical_json_array_text(
            self.ordered_grid_assessment_record_hashes_json
        )
        if any(
            not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in grid_hashes
        ):
            raise ValueError("publication grid history must contain only SHA-256 digests")
        if len(set(grid_hashes)) != len(grid_hashes):
            raise ValueError("publication grid history must contain unique rows")
        if canonical_sha256(grid_hashes) != self.ordered_grid_assessment_record_hashes_hash:
            raise ValueError("publication grid-history hash must bind the exact canonical array")
        failure_reasons = _load_canonical_json_array_text(self.failure_reasons_json)
        if any(
            not isinstance(value, str) or re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]{0,63}", value) is None
            for value in failure_reasons
        ):
            raise ValueError("publication failure reasons require public reason tokens")
        if failure_reasons != tuple(sorted(set(failure_reasons))):
            raise ValueError("publication failure reasons must be sorted and unique")

        optional_groups = (
            (
                self.oos_exposure_id,
                self.exposure_fingerprint,
                self.exposure_authority_id,
                self.exposure_authority_head,
            ),
            (
                self.start_execution_attestation_hash,
                self.execution_attestation_authority_id,
                self.start_execution_attestation_authority_head,
            ),
            (
                self.terminal_execution_attestation_hash,
                self.terminal_execution_attestation_authority_head,
            ),
            (
                self.immutable_artifact_lease_id,
                self.immutable_artifact_lease_hash,
                self.immutable_artifact_lease_authority_id,
                self.immutable_artifact_lease_authority_head,
            ),
        )
        if any(
            any(value is None for value in group) and any(value is not None for value in group)
            for group in optional_groups
        ):
            raise ValueError("publication external reference groups must be all present or all absent")
        oos_present, start_present, terminal_present, lease_present = (
            all(value is not None for value in group) for group in optional_groups
        )
        if self.closure_status == "COMPLETE":
            if self.attempt_status != "COMPLETED" or not all(
                (oos_present, start_present, terminal_present, lease_present)
            ):
                raise ValueError("complete closure publication requires complete OOS execution evidence")
        elif self.attempt_status == "NOT_STARTED":
            if any((oos_present, start_present, terminal_present, lease_present)):
                raise ValueError("not-started publication forbids OOS execution evidence")
        elif self.attempt_status == "INCOMPLETE":
            if not all((oos_present, start_present, lease_present)) or terminal_present:
                raise ValueError("incomplete publication requires start/exposure/lease but no terminal")
        else:
            raise ValueError("not-assessable closure requires NOT_STARTED or INCOMPLETE attempt status")

        winner_values = (self.winner_config_hash, self.winner_evaluation_receipt_hash)
        if any(value is None for value in winner_values) and any(
            value is not None for value in winner_values
        ):
            raise ValueError("publication winner fields must be both present or both absent")
        if self.assessment_status == "ASSESSED":
            if (
                self.closure_status != "COMPLETE"
                or self.diagnostic_verdict not in {
                    "SUPPORTS_SUPERIORITY",
                    "DOES_NOT_SUPPORT_SUPERIORITY",
                    "METHOD_DISAGREEMENT",
                    "MONTE_CARLO_UNRESOLVED",
                }
                or self.minimum_replication_count is None
                or not grid_hashes
                or any(value is None for value in winner_values)
                or failure_reasons
            ):
                raise ValueError("assessed publication requires the exact resolved evidence branch")
        elif (
            self.diagnostic_verdict != "NOT_APPLICABLE"
            or self.minimum_replication_count is not None
            or not failure_reasons
        ):
            raise ValueError("not-assessable publication requires a diagnostic-only deficiency")
        if self.closure_status == "NOT_ASSESSABLE" and (
            grid_hashes or any(value is not None for value in winner_values)
        ):
            raise ValueError("not-assessable closure forbids grid history and winner evidence")
        if self.closure_status == "COMPLETE" and any(value is None for value in winner_values):
            raise ValueError("complete closure publication requires the exact selected winner")
        if self.publication_ordinal == 1:
            if self.previous_record_hash != self.genesis_record_hash:
                raise ValueError("the first publication receipt must extend genesis")
        elif self.previous_record_hash == self.genesis_record_hash:
            raise ValueError("later publication receipts require a non-genesis predecessor")
        if self.receipt_record_hash != canonical_sha256(
            self.model_dump(mode="python", exclude={"receipt_record_hash"})
        ):
            raise ValueError("receipt_record_hash must cover the complete publication receipt")
        return self


class _FamilyAuthorityGenesis(_FrozenPublicContract):
    singleton_key: Literal[1]
    authority_instance_id: _NonEmptyText
    v2_origin_fingerprint: _Sha256Digest
    sealed_v3_manifest_hash: _Sha256Digest
    activation_code_manifest_evidence_hash: _Sha256Digest
    migration_id: Literal["FAMILY_SCHEMA_V2_TO_V3_V1"]
    activated_at_us: _PositiveEpochUs
    record_type: Literal["FAMILY_AUTHORITY_GENESIS_V1"]
    record_hash: _Sha256Digest

    @model_validator(mode="after")
    def _validate_record_hash(self) -> "_FamilyAuthorityGenesis":
        if self.record_hash != canonical_sha256(self.model_dump(mode="python", exclude={"record_hash"})):
            raise ValueError("authority genesis record hash mismatch")
        return self


class ArtifactPublicationReceiptProjection(_FrozenPublicContract):
    record_type: Literal["ARTIFACT_PUBLICATION_RECEIPT_PROJECTION_V1"]
    projection_schema_version: Literal["ARTIFACT_PUBLICATION_RECEIPT_PROJECTION_V1"]
    publication_receipt_schema_version: Literal["ARTIFACT_PUBLICATION_RECEIPT_V1"]
    authority_instance_id: _NonEmptyText
    genesis_record_hash: _Sha256Digest
    authority_lineage_hash: _Sha256Digest
    authority_genesis: _FamilyAuthorityGenesis
    publication_receipt: ArtifactPublicationReceipt
    publication_chain_prefix: Annotated[
        tuple[ArtifactPublicationReceipt, ...],
        Field(min_length=1),
    ]
    pre_publication_research_authority_head: _Sha256Digest
    pre_publication_research_authority_row_count: int = Field(gt=0)
    exporter_authority_event_suffix: Annotated[
        tuple[_AuthorityProjectionRow, ...],
        Field(min_length=1),
    ]
    exporter_authority_head: _Sha256Digest
    terminal_publication_receipt_id: _NonEmptyText
    terminal_publication_record_hash: _Sha256Digest
    terminal_chain_head: _Sha256Digest

    @model_validator(mode="after")
    def _validate_complete_chain(self) -> "ArtifactPublicationReceiptProjection":
        if (
            self.authority_genesis.authority_instance_id != self.authority_instance_id
            or self.authority_genesis.record_hash != self.genesis_record_hash
        ):
            raise ValueError("publication projection must embed its exact authority genesis")
        expected_lineage = _lineage_hash(self.authority_instance_id, self.genesis_record_hash)
        if self.authority_lineage_hash != expected_lineage:
            raise ValueError("publication projection authority lineage mismatch")
        if len(self.exporter_authority_event_suffix) != len(
            self.publication_chain_prefix
        ):
            raise ValueError("every publication receipt requires one exporter authority event")

        previous_event_hash = self.pre_publication_research_authority_head
        previous_event_ordinal = self.pre_publication_research_authority_row_count
        previous_event_at_us = 0
        for event_row, receipt in zip(
            self.exporter_authority_event_suffix,
            self.publication_chain_prefix,
        ):
            event_payload = event_row.canonical_row_payload
            if (
                event_row.table_name != "research_authority_events"
                or event_payload["authority_instance_id"]
                != self.authority_instance_id
                or event_payload["genesis_record_hash"]
                != self.genesis_record_hash
                or event_row.chain_partition_key
                != {"authority_instance_id": self.authority_instance_id}
                or event_row.chain_previous_record_hash != previous_event_hash
            ):
                raise ValueError("exporter authority suffix breaks its exact lineage")
            if event_row.chain_ordinal != previous_event_ordinal + 1:
                raise ValueError("exporter authority suffix ordinals must be contiguous")
            if (
                event_payload["action"]
                != "PUBLISH_DATA_SNOOPING_FAMILY_ARTIFACT_V1"
                or event_payload["subject_record_type"]
                != "PUBLISHED_ARTIFACT_ENVELOPE_V1"
                or event_payload["subject_id"] != receipt.publication_receipt_id
                or event_payload["subject_record_hash"]
                != receipt.published_artifact_envelope_hash
                or event_row.record_hash != receipt.exporter_event_hash
                or event_row.record_hash != receipt.exporter_authority_head
                or not (
                    previous_event_at_us
                    < event_payload["event_at_us"]
                    and receipt.envelope_scanned_at_us
                    <= event_payload["event_at_us"]
                    <= receipt.published_at_us
                )
            ):
                raise ValueError("each exporter event must follow its exact scan and bind its receipt")
            previous_event_hash = event_row.record_hash
            previous_event_ordinal = event_row.chain_ordinal
            previous_event_at_us = event_payload["event_at_us"]

        exporter_event = self.exporter_authority_event_suffix[-1]
        exporter_payload = exporter_event.canonical_row_payload
        if (
            exporter_event.record_hash != self.exporter_authority_head
            or exporter_event.record_hash != self.publication_receipt.exporter_event_hash
            or self.exporter_authority_head
            != self.publication_receipt.exporter_authority_head
            or exporter_payload["action"]
            != "PUBLISH_DATA_SNOOPING_FAMILY_ARTIFACT_V1"
            or exporter_payload["subject_record_type"]
            != "PUBLISHED_ARTIFACT_ENVELOPE_V1"
            or exporter_payload["subject_id"]
            != self.publication_receipt.publication_receipt_id
            or exporter_payload["subject_record_hash"]
            != self.publication_receipt.published_artifact_envelope_hash
            or exporter_payload["event_at_us"]
            > self.publication_receipt.published_at_us
        ):
            raise ValueError("terminal exporter event must bind the exact published envelope")

        previous_hash = self.genesis_record_hash
        prior_published_at_us = 0
        receipt_ids: set[str] = set()
        envelope_hashes: set[str] = set()
        target_paths: set[str] = set()
        for expected_ordinal, receipt in enumerate(self.publication_chain_prefix, start=1):
            if (
                receipt.authority_instance_id != self.authority_instance_id
                or receipt.genesis_record_hash != self.genesis_record_hash
                or receipt.authority_lineage_hash != expected_lineage
            ):
                raise ValueError("publication chain contains a foreign authority receipt")
            if receipt.publication_ordinal != expected_ordinal:
                raise ValueError("publication chain must be a complete ordinal prefix")
            if receipt.previous_record_hash != previous_hash:
                raise ValueError("publication chain predecessor hash mismatch")
            if receipt.published_at_us <= prior_published_at_us:
                raise ValueError("publication chain timestamps must be strictly increasing")
            if receipt.publication_receipt_id in receipt_ids:
                raise ValueError("publication receipt identities must be unique")
            if receipt.published_artifact_envelope_hash in envelope_hashes:
                raise ValueError("an exact artifact envelope can be published only once")
            if receipt.target_content_addressed_path in target_paths:
                raise ValueError("publication target identities must be unique")
            receipt_ids.add(receipt.publication_receipt_id)
            envelope_hashes.add(receipt.published_artifact_envelope_hash)
            target_paths.add(receipt.target_content_addressed_path)
            previous_hash = receipt.receipt_record_hash
            prior_published_at_us = receipt.published_at_us

        terminal = self.publication_chain_prefix[-1]
        if self.publication_receipt != terminal:
            raise ValueError("embedded publication receipt must be the terminal chain row")
        if self.terminal_publication_receipt_id != terminal.publication_receipt_id:
            raise ValueError("terminal receipt identity mismatch")
        if self.terminal_publication_record_hash != terminal.receipt_record_hash:
            raise ValueError("terminal publication record hash mismatch")
        if self.terminal_chain_head != terminal.receipt_record_hash:
            raise ValueError("terminal chain head must equal the terminal receipt hash")
        return self


def _verify_exact_byte_graph_splice(
    envelope: PublishedArtifactEnvelope,
    publication_receipt: ArtifactPublicationReceipt,
    projection: ArtifactPublicationReceiptProjection,
    projection_ref: ArtifactPublicationProjectionRef,
    evidence_ref: DataSnoopingEvidenceRef | None = None,
) -> bool:
    """Structurally splice both exact-byte roots without claiming A9 semantics.

    The envelope and projection remain independently content addressed.  This
    verifier is their only cross-root splice point and rejects any substitution
    of a lineage, parent row, scan receipt, publication row, or exporter event.
    """

    if not isinstance(envelope, PublishedArtifactEnvelope):
        raise TypeError("bundle envelope must be a PublishedArtifactEnvelope")
    if not isinstance(publication_receipt, ArtifactPublicationReceipt):
        raise TypeError("bundle receipt must be an ArtifactPublicationReceipt")
    if not isinstance(projection, ArtifactPublicationReceiptProjection):
        raise TypeError("bundle projection must be an ArtifactPublicationReceiptProjection")
    if not isinstance(projection_ref, ArtifactPublicationProjectionRef):
        raise TypeError(
            "bundle projection reference must be an ArtifactPublicationProjectionRef"
        )
    if evidence_ref is not None and not isinstance(evidence_ref, DataSnoopingEvidenceRef):
        raise TypeError("optional evidence reference must be a DataSnoopingEvidenceRef")

    core = envelope.artifact_core
    scan_receipt = envelope.artifact_core_scan_receipt
    core_hash = sha256_bytes(canonical_json_bytes(core))
    scan_receipt_hash = sha256_bytes(canonical_json_bytes(scan_receipt))
    envelope_hash = sha256_bytes(canonical_json_bytes(envelope))
    projection_hash = sha256_bytes(canonical_json_bytes(projection))
    lineage = (
        core.authority_instance_id,
        core.genesis_record_hash,
        core.authority_lineage_hash,
    )
    if lineage != (
        publication_receipt.authority_instance_id,
        publication_receipt.genesis_record_hash,
        publication_receipt.authority_lineage_hash,
    ) or lineage != (
        projection.authority_instance_id,
        projection.genesis_record_hash,
        projection.authority_lineage_hash,
    ) or lineage != (
        projection_ref.authority_instance_id,
        projection_ref.genesis_record_hash,
        projection_ref.authority_lineage_hash,
    ):
        raise ValueError("bundle roots must share one exact authority lineage")
    activation_payload = core.activation_code_manifest_evidence.canonical_payload
    activation_projection = (
        core.activation_code_manifest_evidence.provider_authority_projection
    )
    activation_context = core.activation_code_manifest_evidence.provider_subject_context
    if (
        projection.authority_genesis.sealed_v3_manifest_hash
        != core.family_schema_v3_manifest_sidecar.decoded_sha256
        or projection.authority_genesis.activation_code_manifest_evidence_hash
        != activation_payload["evidence_hash"]
        or not isinstance(
            activation_context,
            _AuthorityActivationCodeManifestContext,
        )
        or activation_context.authority_instance_id != core.authority_instance_id
        or activation_context.sealed_v3_manifest_hash
        != projection.authority_genesis.sealed_v3_manifest_hash
        or activation_context.migration_id != projection.authority_genesis.migration_id
        or activation_projection is None
        or activation_projection.ordered_events[-1].event_at_us
        > projection.authority_genesis.activated_at_us
    ):
        raise ValueError("family genesis must bind the exact schema and activation evidence")
    core_research_head = next(
        head
        for head in core.authority_projection.chain_heads
        if head.table_name == "research_authority_events"
        and head.partition_key == {"authority_instance_id": core.authority_instance_id}
    )
    if (
        projection.pre_publication_research_authority_head
        != core.pre_publication_research_authority_head
        or core.pre_publication_research_authority_head
        != core_research_head.head_record_hash
        or projection.pre_publication_research_authority_row_count
        != core_research_head.row_count
    ):
        raise ValueError("projection exporter suffix must extend the core authority head")
    core_terminal_event = next(
        row
        for row in core.authority_projection.canonical_rows
        if row.table_name == "research_authority_events"
        and row.record_hash == core.pre_publication_research_authority_head
    )
    if (
        publication_receipt.artifact_core_hash != core_hash
        or publication_receipt.artifact_core_scan_receipt_hash != scan_receipt_hash
        or publication_receipt.published_artifact_envelope_hash != envelope_hash
        or publication_receipt.target_content_addressed_path
        != _content_path(_ENVELOPE_ROOT, envelope_hash)
        or projection.publication_receipt != publication_receipt
        or projection.terminal_publication_record_hash
        != publication_receipt.receipt_record_hash
        or projection_ref.artifact_publication_receipt_hash
        != publication_receipt.receipt_record_hash
        or projection_ref.publication_projection_hash != projection_hash
        or projection_ref.publication_projection_path
        != _content_path(_PROJECTION_ROOT, projection_hash)
    ):
        raise ValueError("bundle content-addressed roots or terminal receipt are detached")
    scan_pairs = (
        ("public_artifact_scan_policy_id", "public_artifact_scan_policy_id"),
        ("dlp_policy_id", "dlp_policy_id"),
        ("dlp_policy_version", "dlp_policy_version"),
        ("dlp_policy_hash", "dlp_policy_hash"),
        ("scanner_implementation", "scanner_implementation"),
        ("scanner_version", "scanner_version"),
        ("scan_status", "scan_status"),
    )
    if any(
        getattr(scan_receipt, scan_field) != getattr(publication_receipt, receipt_field)
        for scan_field, receipt_field in scan_pairs
    ):
        raise ValueError("publication receipt must equal the exact core scan receipt")

    typed_evidence = (
        core.selection_scope_evidence,
        core.selection_decision_evidence,
        core.trial_ledger_proof_evidence,
        core.observed_pit_evidence,
        core.activation_code_manifest_evidence,
        core.code_manifest_evidence,
        *core.ordered_candidate_disposition_evidence,
        *core.ordered_candidate_receipt_evidence,
        *((core.evaluation_batch_evidence,) if core.evaluation_batch_evidence else ()),
        *((core.benchmark_receipt_evidence,) if core.benchmark_receipt_evidence else ()),
        *(
            (core.start_execution_attestation_evidence,)
            if core.start_execution_attestation_evidence
            else ()
        ),
        *(
            (core.terminal_execution_attestation_evidence,)
            if core.terminal_execution_attestation_evidence
            else ()
        ),
        *(
            (core.immutable_artifact_lease_evidence,)
            if core.immutable_artifact_lease_evidence
            else ()
        ),
    )
    exact_scan_receipts = (
        core.family_schema_v3_manifest_sidecar.scan_receipt,
        core.trial_ledger_history_sidecar.scan_receipt,
        core.trial_ledger_snapshot_sidecar.scan_receipt,
        core.trial_ledger_seal_sidecar.scan_receipt,
        *(evidence.scan_receipt for evidence in typed_evidence),
        *(
            evidence.provider_authority_projection.scan_receipt
            for evidence in typed_evidence
            if evidence.provider_authority_projection is not None
        ),
    )
    scan_policy_fields = (
        "public_artifact_scan_policy_id",
        "dlp_policy_id",
        "dlp_policy_version",
        "dlp_policy_hash",
        "scanner_implementation",
        "scanner_version",
        "scan_status",
    )
    if any(
        any(
            getattr(receipt, name) != getattr(scan_receipt, name)
            for name in scan_policy_fields
        )
        for receipt in exact_scan_receipts
    ):
        raise ValueError("all exact-byte inputs must use the frozen publication scan policy")
    first_exact_scan_at_us = min(receipt.scanned_at_us for receipt in exact_scan_receipts)
    last_exact_scan_at_us = max(receipt.scanned_at_us for receipt in exact_scan_receipts)
    exporter_event_at_us = projection.exporter_authority_event_suffix[-1].canonical_row_payload[
        "event_at_us"
    ]
    if not (
        core_terminal_event.canonical_row_payload["event_at_us"]
        <= first_exact_scan_at_us
        <= last_exact_scan_at_us
        <= scan_receipt.scanned_at_us
        <= publication_receipt.envelope_scanned_at_us
        <= exporter_event_at_us
        <= publication_receipt.published_at_us
    ):
        raise ValueError("publication scans and exporter event violate causal time order")
    if publication_receipt.scan_result_hash != _scan_result_hash(
        scan_target="PUBLISHED_ARTIFACT_ENVELOPE",
        target_sha256=envelope_hash,
        target_byte_length=len(canonical_json_bytes(envelope)),
        public_artifact_scan_policy_id=publication_receipt.public_artifact_scan_policy_id,
        dlp_policy_id=publication_receipt.dlp_policy_id,
        dlp_policy_version=publication_receipt.dlp_policy_version,
        dlp_policy_hash=publication_receipt.dlp_policy_hash,
        scanner_implementation=publication_receipt.scanner_implementation,
        scanner_version=publication_receipt.scanner_version,
        scan_status=publication_receipt.scan_status,
    ):
        raise ValueError("publication final scan must bind the exact envelope bytes")

    observed_payload = core.observed_pit_evidence.canonical_payload
    code_payload = core.code_manifest_evidence.canonical_payload
    closure_payload = next(
        row.canonical_row_payload
        for row in core.authority_projection.canonical_rows
        if row.table_name == "data_snooping_family_closures"
    )
    external_receipt_pairs = (
        (publication_receipt.observed_pit_authority_id, observed_payload["authority_id"]),
        (publication_receipt.observed_pit_handle_id, observed_payload["handle_id"]),
        (publication_receipt.observed_pit_content_hash, observed_payload["content_hash"]),
        (
            publication_receipt.knowledge_time_policy_id,
            observed_payload["knowledge_time_policy_id"],
        ),
        (
            publication_receipt.knowledge_time_policy_version,
            observed_payload["knowledge_time_policy_version"],
        ),
        (publication_receipt.observed_pit_authority_head, observed_payload["authority_head"]),
        (
            publication_receipt.code_manifest_policy_id,
            code_payload["policy_ref"]["policy_id"],
        ),
        (
            publication_receipt.code_manifest_policy_version,
            code_payload["policy_ref"]["policy_version"],
        ),
        (
            publication_receipt.code_manifest_policy_hash,
            code_payload["policy_ref"]["policy_hash"],
        ),
        (publication_receipt.code_manifest_evidence_hash, code_payload["evidence_hash"]),
        (publication_receipt.code_manifest_authority_id, code_payload["authority_id"]),
        (publication_receipt.code_manifest_authority_head, code_payload["authority_head"]),
        *(
            (
                getattr(publication_receipt, name),
                closure_payload[name],
            )
            for name in (
                "oos_exposure_id",
                "exposure_fingerprint",
                "exposure_authority_id",
                "exposure_authority_head",
                "start_execution_attestation_hash",
                "terminal_execution_attestation_hash",
                "execution_attestation_authority_id",
                "start_execution_attestation_authority_head",
                "terminal_execution_attestation_authority_head",
                "immutable_artifact_lease_id",
                "immutable_artifact_lease_hash",
                "immutable_artifact_lease_authority_id",
                "immutable_artifact_lease_authority_head",
            )
        ),
    )
    if any(left != right for left, right in external_receipt_pairs):
        raise ValueError("publication receipt external summaries detach from the exact core")

    grid_hashes = tuple(
        _load_canonical_json_array_text(
            publication_receipt.ordered_grid_assessment_record_hashes_json
        )
    )
    failure_reasons = tuple(
        _load_canonical_json_array_text(publication_receipt.failure_reasons_json)
    )
    core_receipt_pairs = (
        (core.artifact_kind, publication_receipt.artifact_kind),
        (core.family_id, publication_receipt.family_id),
        (core.selection_scope_id, publication_receipt.selection_scope_id),
        (core.selection_decision_id, publication_receipt.selection_decision_id),
        (core.winner_config_hash, publication_receipt.winner_config_hash),
        (
            core.winner_evaluation_receipt_hash,
            publication_receipt.winner_evaluation_receipt_hash,
        ),
        (core.closure_manifest_hash, publication_receipt.closure_manifest_hash),
        (core.closure_status, publication_receipt.closure_status),
        (
            core.family_assessment_conclusion_hash,
            publication_receipt.family_assessment_conclusion_hash,
        ),
        (core.assessment_status, publication_receipt.assessment_status),
        (core.diagnostic_verdict, publication_receipt.diagnostic_verdict),
        (core.decision_class, publication_receipt.decision_class),
        (core.promotion_effect, publication_receipt.promotion_effect),
        (core.ordered_grid_assessment_record_hashes, grid_hashes),
        (core.failure_reasons, failure_reasons),
    )
    if any(left != right for left, right in core_receipt_pairs):
        raise ValueError("publication receipt summaries must equal the exact artifact core")

    projection_ref_pairs = (
        (projection_ref.family_id, core.family_id),
        (projection_ref.decision_class, core.decision_class),
        (projection_ref.assessment_status, core.assessment_status),
        (
            projection_ref.artifact_publication_receipt_id,
            publication_receipt.publication_receipt_id,
        ),
        (projection_ref.publication_chain_head, projection.terminal_chain_head),
    )
    if any(left != right for left, right in projection_ref_pairs):
        raise ValueError("projection reference must equal the verified terminal bundle values")
    if evidence_ref is not None:
        ref_pairs = (
            (evidence_ref.artifact_kind, core.artifact_kind),
            (evidence_ref.artifact_schema_version, core.artifact_schema_version),
            (evidence_ref.family_id, core.family_id),
            (evidence_ref.selection_scope_id, core.selection_scope_id),
            (evidence_ref.selection_decision_id, core.selection_decision_id),
            (evidence_ref.decision_class, core.decision_class),
            (evidence_ref.winner_config_hash, core.winner_config_hash),
            (evidence_ref.closure_manifest_hash, core.closure_manifest_hash),
            (
                evidence_ref.family_assessment_conclusion_hash,
                core.family_assessment_conclusion_hash,
            ),
            (evidence_ref.assessment_status, core.assessment_status),
            (evidence_ref.diagnostic_verdict, core.diagnostic_verdict),
            (evidence_ref.promotion_effect, core.promotion_effect),
            (evidence_ref.published_artifact_envelope_hash, envelope_hash),
            (
                evidence_ref.published_artifact_path,
                publication_receipt.target_content_addressed_path,
            ),
            (
                evidence_ref.artifact_publication_receipt_id,
                publication_receipt.publication_receipt_id,
            ),
            (evidence_ref.publication_chain_head, projection.terminal_chain_head),
            (
                evidence_ref.artifact_publication_receipt_hash,
                projection_ref.artifact_publication_receipt_hash,
            ),
            (evidence_ref.publication_projection_hash, projection_hash),
            (
                evidence_ref.publication_projection_path,
                projection_ref.publication_projection_path,
            ),
        )
        if any(left != right for left, right in ref_pairs):
            raise ValueError("evidence reference must equal the verified terminal bundle values")
    return True


__all__ = [
    "ArtifactCore",
    "ArtifactCoreScanReceipt",
    "ArtifactPublicationReceipt",
    "ArtifactPublicationReceiptProjection",
    "ArtifactPublicationProjectionRef",
    "DataSnoopingEvidenceRef",
    "PublishedArtifactEnvelope",
]
