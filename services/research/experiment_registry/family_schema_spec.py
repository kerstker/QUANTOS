"""Closed, immutable schema-semantics vocabulary for family authority v3.

This Pass-C module contains values only.  It deliberately has no SQLite IO,
schema renderer, executable DDL, manifest sealing, migration, activation, or
authority verification entry point.  Pass-M code may consume these closed
nodes only after every component specification has been frozen and reviewed.
"""

from __future__ import annotations

import math as _math
import re as _re
from dataclasses import dataclass as _dataclass
from enum import StrEnum as _StrEnum
from typing import TypeAlias as _TypeAlias


_IDENTIFIER_RE = _re.compile(r"^[a-z][a-z0-9_]*$")
_TOKEN_RE = _re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
_PROVIDER_FIELD_PATH_RE = _re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_STATE_RE = _re.compile(r"^[A-Z][A-Z0-9_]*$")
_AUTHORITY_ID_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/|@-]{0,254}$")
_SHA256_RE = _re.compile(r"^[0-9a-f]{64}$")


def validate_authority_instance_id(value: object) -> str:
    """Return one exact deployment identity or reject it fail-closed."""

    if type(value) is not str or _AUTHORITY_ID_RE.fullmatch(value) is None:
        raise ValueError(
            "authority_instance_id must be a non-empty deployment identity"
        )
    return value


def _ensure_identifier(value: object, label: str) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SQL identifier")
    return value


def _ensure_token(value: object, label: str) -> str:
    if type(value) is not str or _TOKEN_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a closed contract token")
    return value


def _ensure_provider_field_path(value: object, label: str) -> str:
    if type(value) is not str or _PROVIDER_FIELD_PATH_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a dotted lowercase provider field path")
    return value


def _ensure_state(value: object, label: str) -> str:
    if type(value) is not str or _STATE_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be an uppercase state token")
    return value


def _ensure_sha256(value: object, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _ensure_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{label} must be bool")
    return value


def _ensure_tuple(value: object, label: str, *, nonempty: bool = False) -> tuple:
    if type(value) is not tuple:
        raise TypeError(f"{label} must be an immutable tuple")
    if nonempty and not value:
        raise ValueError(f"{label} must not be empty")
    return value


def _ensure_exact_items(value: tuple, expected_type: type, label: str) -> None:
    if any(type(item) is not expected_type for item in value):
        raise TypeError(f"{label} must contain only {expected_type.__name__} values")


def _ensure_unique(value: tuple, label: str) -> None:
    if len(set(value)) != len(value):
        raise ValueError(f"{label} must be unique")


def _ensure_enum(value: object, expected_type: type, label: str) -> None:
    if type(value) is not expected_type:
        raise TypeError(f"{label} must be {expected_type.__name__}")


class SqlType(_StrEnum):
    INTEGER = "INTEGER"
    REAL = "REAL"
    TEXT = "TEXT"
    BLOB = "BLOB"


class DefaultKind(_StrEnum):
    NULL = "NULL"
    INTEGER = "INTEGER"
    REAL = "REAL"
    TEXT = "TEXT"
    BLOB = "BLOB"


@_dataclass(frozen=True, slots=True)
class ColumnDefault:
    kind: DefaultKind
    value: object = None

    def __post_init__(self) -> None:
        _ensure_enum(self.kind, DefaultKind, "default kind")
        if self.kind is DefaultKind.NULL:
            valid = self.value is None
        elif self.kind is DefaultKind.INTEGER:
            valid = type(self.value) is int
        elif self.kind is DefaultKind.REAL:
            valid = type(self.value) is float and _math.isfinite(self.value)
        elif self.kind is DefaultKind.TEXT:
            valid = type(self.value) is str
        else:
            valid = type(self.value) is bytes
        if not valid:
            raise TypeError(f"default value does not match DefaultKind.{self.kind.name}")


@_dataclass(frozen=True, slots=True)
class ColumnSpec:
    name: str
    sql_type: SqlType
    nullable: bool = True
    default: ColumnDefault | None = None

    def __post_init__(self) -> None:
        _ensure_identifier(self.name, "column identifier")
        _ensure_enum(self.sql_type, SqlType, "column type")
        _ensure_bool(self.nullable, "column nullable")
        if self.default is not None and type(self.default) is not ColumnDefault:
            raise TypeError("column default must be ColumnDefault or None")
        if not self.nullable and self.default is not None and self.default.kind is DefaultKind.NULL:
            raise ValueError("a non-nullable column cannot default to NULL")


class RowSource(_StrEnum):
    CURRENT = "CURRENT"
    NEW = "NEW"
    OLD = "OLD"
    RELATED = "RELATED"


class ComparisonOperator(_StrEnum):
    EQ = "EQ"
    NE = "NE"
    LT = "LT"
    LE = "LE"
    GT = "GT"
    GE = "GE"


class LogicalOperator(_StrEnum):
    AND = "AND"
    OR = "OR"


class NullOperator(_StrEnum):
    IS_NULL = "IS_NULL"
    IS_NOT_NULL = "IS_NOT_NULL"


class SortDirection(_StrEnum):
    ASC = "ASC"
    DESC = "DESC"


class Collation(_StrEnum):
    BINARY = "BINARY"
    NOCASE = "NOCASE"
    RTRIM = "RTRIM"


@_dataclass(frozen=True, slots=True)
class ColumnValue:
    column: str
    source: RowSource = RowSource.CURRENT

    def __post_init__(self) -> None:
        _ensure_identifier(self.column, "column identifier")
        _ensure_enum(self.source, RowSource, "row source")


@_dataclass(frozen=True, slots=True)
class LiteralValue:
    value: None | int | float | str | bytes

    def __post_init__(self) -> None:
        if type(self.value) not in (type(None), int, float, str, bytes):
            raise TypeError("literal values are limited to SQLite scalar value types")
        if type(self.value) is float and not _math.isfinite(self.value):
            raise ValueError("REAL literal must be finite")


@_dataclass(frozen=True, slots=True)
class QualifiedColumnValue:
    """A column owned by one named relation inside a closed join aggregate."""

    relation_alias: str
    column: str
    source: RowSource = RowSource.RELATED

    def __post_init__(self) -> None:
        _ensure_identifier(self.relation_alias, "qualified relation alias")
        _ensure_identifier(self.column, "qualified column identifier")
        _ensure_enum(self.source, RowSource, "qualified column source")
        if self.source is not RowSource.RELATED:
            raise ValueError("qualified columns may read RELATED rows only")


class CanonicalJsonScalarType(_StrEnum):
    TEXT = "TEXT"
    INTEGER = "INTEGER"
    REAL = "REAL"


class CanonicalJsonFailurePolicy(_StrEnum):
    REJECT = "REJECT"


class RowHashAlgorithm(_StrEnum):
    SHA256 = "SHA256"


class CanonicalEncoding(_StrEnum):
    QUANTOS_CANONICAL_JSON_V1 = "QUANTOS_CANONICAL_JSON_V1"


@_dataclass(frozen=True, slots=True)
class CanonicalJsonFieldValue:
    """Fail-closed scalar extraction from a canonical JSON object.

    Pass M must reject malformed JSON, a missing path, a non-canonical payload,
    or a scalar whose SQLite type does not match ``scalar_type``.
    """

    document: ColumnValue
    path: tuple[str, ...]
    scalar_type: CanonicalJsonScalarType
    require_canonical_json: bool = True
    failure_policy: CanonicalJsonFailurePolicy = CanonicalJsonFailurePolicy.REJECT

    def __post_init__(self) -> None:
        if type(self.document) is not ColumnValue:
            raise TypeError("canonical JSON document must be a row column")
        if self.document.source not in (RowSource.CURRENT, RowSource.NEW, RowSource.RELATED):
            raise ValueError("canonical JSON document requires CURRENT, NEW, or RELATED source")
        path = _ensure_tuple(self.path, "canonical JSON field path", nonempty=True)
        for item in path:
            _ensure_identifier(item, "canonical JSON path item")
        _ensure_enum(self.scalar_type, CanonicalJsonScalarType, "canonical JSON scalar type")
        _ensure_bool(self.require_canonical_json, "canonical JSON requirement")
        if not self.require_canonical_json:
            raise ValueError("canonical JSON extraction must reject non-canonical documents")
        _ensure_enum(self.failure_policy, CanonicalJsonFailurePolicy, "canonical JSON failure policy")
        if self.failure_policy is not CanonicalJsonFailurePolicy.REJECT:
            raise ValueError("malformed JSON, missing paths, and scalar type mismatches must reject")


@_dataclass(frozen=True, slots=True)
class OrdinalOffsetValue:
    """A local ordinal shifted by the sole closed predecessor offset."""

    value: ColumnValue
    offset: int

    def __post_init__(self) -> None:
        if type(self.value) is not ColumnValue:
            raise TypeError("ordinal offsets require a row column")
        if self.value.source not in (RowSource.CURRENT, RowSource.NEW):
            raise ValueError("ordinal offsets require a CURRENT or NEW row column")
        if type(self.offset) is not int or self.offset != -1:
            raise ValueError("the closed ordinal-offset grammar permits predecessor offset -1 only")


ScalarExpression: _TypeAlias = (
    ColumnValue | LiteralValue | QualifiedColumnValue | CanonicalJsonFieldValue | OrdinalOffsetValue
)


def _ensure_scalar(value: object, label: str) -> None:
    allowed_types: tuple[type, ...] = (
        ColumnValue,
        LiteralValue,
        QualifiedColumnValue,
        CanonicalJsonFieldValue,
        OrdinalOffsetValue,
    )
    related_array_type = globals().get("RelatedCanonicalJsonArrayValueSpec")
    related_array_length_type = globals().get(
        "RelatedCanonicalJsonArrayLengthValueSpec"
    )
    if type(related_array_type) is type:
        allowed_types = (*allowed_types, related_array_type)
    if type(related_array_length_type) is type:
        allowed_types = (*allowed_types, related_array_length_type)
    if type(value) not in allowed_types:
        raise TypeError(f"{label} must be a closed scalar expression")
    if (
        related_array_type is not None
        and type(value) is related_array_type
        and value.projection is CanonicalJsonArrayProjection.CANONICAL_ARRAY
    ):
        raise ValueError("canonical JSON array projections are domain values, not SQLite scalars")


@_dataclass(frozen=True, slots=True)
class ComparisonExpression:
    left: ScalarExpression
    operator: ComparisonOperator
    right: ScalarExpression

    def __post_init__(self) -> None:
        _ensure_scalar(self.left, "comparison left operand")
        _ensure_enum(self.operator, ComparisonOperator, "comparison operator")
        _ensure_scalar(self.right, "comparison right operand")


@_dataclass(frozen=True, slots=True)
class NullExpression:
    value: ScalarExpression
    operator: NullOperator

    def __post_init__(self) -> None:
        _ensure_scalar(self.value, "null-test operand")
        _ensure_enum(self.operator, NullOperator, "null-test operator")


@_dataclass(frozen=True, slots=True)
class MembershipExpression:
    value: ScalarExpression
    values: tuple[LiteralValue, ...]
    negated: bool = False

    def __post_init__(self) -> None:
        _ensure_scalar(self.value, "membership operand")
        values = _ensure_tuple(self.values, "membership values", nonempty=True)
        _ensure_exact_items(values, LiteralValue, "membership values")
        _ensure_unique(tuple(item.value for item in values), "membership literal values")
        _ensure_bool(self.negated, "membership negated")


@_dataclass(frozen=True, slots=True)
class LogicalExpression:
    operator: LogicalOperator
    operands: tuple[PredicateExpression, ...]

    def __post_init__(self) -> None:
        _ensure_enum(self.operator, LogicalOperator, "logical operator")
        operands = _ensure_tuple(self.operands, "logical operands", nonempty=True)
        if len(operands) < 2:
            raise ValueError("logical expressions require at least two operands")
        for operand in operands:
            _ensure_predicate(operand)


@_dataclass(frozen=True, slots=True)
class NotExpression:
    operand: PredicateExpression

    def __post_init__(self) -> None:
        _ensure_predicate(self.operand)


def _ensure_lookup_binding_value(value: object) -> None:
    _ensure_scalar(value, "lookup binding value")
    if type(value) is QualifiedColumnValue:
        raise ValueError("lookup bindings cannot reference qualified RELATED columns")
    if type(value) is ColumnValue and value.source not in (RowSource.CURRENT, RowSource.NEW):
        raise ValueError("lookup bindings require CURRENT or NEW row columns")
    if type(value) is CanonicalJsonFieldValue and value.document.source not in (
        RowSource.CURRENT,
        RowSource.NEW,
    ):
        raise ValueError("lookup bindings cannot extract from an unbound RELATED JSON document")


@_dataclass(frozen=True, slots=True)
class LookupBinding:
    related_column: str
    value: ScalarExpression

    def __post_init__(self) -> None:
        _ensure_identifier(self.related_column, "related column identifier")
        _ensure_lookup_binding_value(self.value)


class RelatedLookupCardinality(_StrEnum):
    EXACTLY_ONE = "EXACTLY_ONE"


class RelatedLookupKeyRequirement(_StrEnum):
    PRIMARY_KEY_OR_EXACT_UNIQUE = "PRIMARY_KEY_OR_EXACT_UNIQUE"


@_dataclass(frozen=True, slots=True)
class RelatedValueLookupSpec:
    """A fail-closed scalar lookup with no predicate or recursive subquery surface.

    Pass M must prove that the related binding columns contain every column of
    one exact primary key or UNIQUE key. Additional bindings may narrow the same
    row, but cannot substitute for that key proof.
    """

    table: str
    bindings: tuple[LookupBinding, ...]
    value: str
    source: RowSource = RowSource.RELATED
    cardinality: RelatedLookupCardinality = RelatedLookupCardinality.EXACTLY_ONE
    key_requirement: RelatedLookupKeyRequirement = RelatedLookupKeyRequirement.PRIMARY_KEY_OR_EXACT_UNIQUE

    def __post_init__(self) -> None:
        _ensure_identifier(self.table, "related-value table identifier")
        bindings = _ensure_tuple(self.bindings, "related-value bindings", nonempty=True)
        _ensure_exact_items(bindings, LookupBinding, "related-value bindings")
        _ensure_unique(
            tuple(binding.related_column for binding in bindings),
            "related-value binding columns",
        )
        if any(
            type(binding.value) is not ColumnValue or binding.value.source not in (RowSource.NEW, RowSource.CURRENT)
            for binding in bindings
        ):
            raise ValueError("related-value bindings require NEW or CURRENT row columns")
        _ensure_identifier(self.value, "related-value column identifier")
        _ensure_enum(self.source, RowSource, "related-value source")
        if self.source is not RowSource.RELATED:
            raise ValueError("related-value lookups may read RELATED values only")
        _ensure_enum(
            self.cardinality,
            RelatedLookupCardinality,
            "related-value cardinality",
        )
        if self.cardinality is not RelatedLookupCardinality.EXACTLY_ONE:
            raise ValueError("related-value lookups are fail-closed EXACTLY_ONE lookups")
        _ensure_enum(
            self.key_requirement,
            RelatedLookupKeyRequirement,
            "related-value key requirement",
        )
        if self.key_requirement is not RelatedLookupKeyRequirement.PRIMARY_KEY_OR_EXACT_UNIQUE:
            raise ValueError("related-value bindings must resolve to a primary key or exact UNIQUE key")


class CanonicalJsonArrayProjection(_StrEnum):
    ELEMENT_AT_ORDINAL = "ELEMENT_AT_ORDINAL"
    CANONICAL_ARRAY = "CANONICAL_ARRAY"


@_dataclass(frozen=True, slots=True)
class RelatedCanonicalJsonArrayValueSpec:
    """An exact-row canonical JSON array projection.

    ``ordinal`` is one-based and ``ordinal_offset`` converts it to a zero-based
    JSON array index: ``-1`` selects the current schedule point and ``-2`` its
    predecessor. Pass M must reject zero/multiple parent rows, malformed or
    non-canonical JSON, non-array payloads, element type drift,
    missing/out-of-range ordinals, and duplicate elements when a canonical
    domain array is requested.
    """

    table: str
    bindings: tuple[LookupBinding, ...]
    column: str
    element_type: CanonicalJsonScalarType
    projection: CanonicalJsonArrayProjection
    ordinal: ScalarExpression | None = None
    ordinal_offset: int = 0
    source: RowSource = RowSource.RELATED
    cardinality: RelatedLookupCardinality = RelatedLookupCardinality.EXACTLY_ONE
    key_requirement: RelatedLookupKeyRequirement = RelatedLookupKeyRequirement.PRIMARY_KEY_OR_EXACT_UNIQUE
    require_canonical_json: bool = True
    failure_policy: CanonicalJsonFailurePolicy = CanonicalJsonFailurePolicy.REJECT

    def __post_init__(self) -> None:
        _ensure_identifier(self.table, "related JSON table identifier")
        bindings = _ensure_tuple(self.bindings, "related JSON bindings", nonempty=True)
        _ensure_exact_items(bindings, LookupBinding, "related JSON bindings")
        _ensure_unique(
            tuple(binding.related_column for binding in bindings),
            "related JSON binding columns",
        )
        if any(
            type(binding.value) is not ColumnValue or binding.value.source not in (RowSource.NEW, RowSource.CURRENT)
            for binding in bindings
        ):
            raise ValueError("related JSON bindings require NEW or CURRENT row columns")
        _ensure_identifier(self.column, "related JSON column identifier")
        _ensure_enum(self.element_type, CanonicalJsonScalarType, "related JSON element type")
        _ensure_enum(self.projection, CanonicalJsonArrayProjection, "related JSON projection")
        _ensure_enum(self.source, RowSource, "related JSON source")
        if self.source is not RowSource.RELATED:
            raise ValueError("related JSON arrays may read RELATED values only")
        _ensure_enum(self.cardinality, RelatedLookupCardinality, "related JSON cardinality")
        if self.cardinality is not RelatedLookupCardinality.EXACTLY_ONE:
            raise ValueError("related JSON arrays are fail-closed EXACTLY_ONE lookups")
        _ensure_enum(
            self.key_requirement,
            RelatedLookupKeyRequirement,
            "related JSON key requirement",
        )
        if self.key_requirement is not RelatedLookupKeyRequirement.PRIMARY_KEY_OR_EXACT_UNIQUE:
            raise ValueError("related JSON bindings must resolve to a primary key or exact UNIQUE key")
        _ensure_bool(self.require_canonical_json, "related JSON canonical requirement")
        if not self.require_canonical_json:
            raise ValueError("related JSON array projections require canonical JSON")
        _ensure_enum(self.failure_policy, CanonicalJsonFailurePolicy, "related JSON failure policy")
        if self.failure_policy is not CanonicalJsonFailurePolicy.REJECT:
            raise ValueError("invalid related JSON arrays must reject")
        if self.projection is CanonicalJsonArrayProjection.ELEMENT_AT_ORDINAL:
            if self.ordinal is None:
                raise ValueError("ELEMENT_AT_ORDINAL requires a local ordinal")
            _ensure_scalar(self.ordinal, "related JSON ordinal")
            if type(self.ordinal) is not ColumnValue or self.ordinal.source not in (
                RowSource.NEW,
                RowSource.CURRENT,
            ):
                raise ValueError("related JSON ordinals require a NEW or CURRENT row column")
            if type(self.ordinal_offset) is not int or self.ordinal_offset not in (-2, -1):
                raise ValueError(
                    "one-based related JSON ordinals require current offset -1 or predecessor offset -2"
                )
        elif self.ordinal is not None or self.ordinal_offset != 0:
            raise ValueError("CANONICAL_ARRAY forbids ordinal selection")


@_dataclass(frozen=True, slots=True)
class RelatedCanonicalJsonArrayLengthValueSpec:
    """Length of one exact typed canonical JSON array.

    Pass M must reject a missing or multiply matched parent, malformed or
    non-canonical JSON, a non-array document, or any element whose SQLite type
    differs from ``element_type`` before returning the integer cardinality.
    """

    table: str
    bindings: tuple[LookupBinding, ...]
    column: str
    element_type: CanonicalJsonScalarType
    source: RowSource = RowSource.RELATED
    cardinality: RelatedLookupCardinality = RelatedLookupCardinality.EXACTLY_ONE
    key_requirement: RelatedLookupKeyRequirement = (
        RelatedLookupKeyRequirement.PRIMARY_KEY_OR_EXACT_UNIQUE
    )
    require_canonical_json: bool = True
    failure_policy: CanonicalJsonFailurePolicy = CanonicalJsonFailurePolicy.REJECT

    def __post_init__(self) -> None:
        _ensure_identifier(self.table, "related JSON length table identifier")
        bindings = _ensure_tuple(
            self.bindings,
            "related JSON length bindings",
            nonempty=True,
        )
        _ensure_exact_items(bindings, LookupBinding, "related JSON length bindings")
        _ensure_unique(
            tuple(binding.related_column for binding in bindings),
            "related JSON length binding columns",
        )
        if any(
            type(binding.value) is not ColumnValue
            or binding.value.source not in (RowSource.NEW, RowSource.CURRENT)
            for binding in bindings
        ):
            raise ValueError(
                "related JSON length bindings require NEW or CURRENT row columns"
            )
        _ensure_identifier(self.column, "related JSON length column identifier")
        _ensure_enum(
            self.element_type,
            CanonicalJsonScalarType,
            "related JSON length element type",
        )
        _ensure_enum(self.source, RowSource, "related JSON length source")
        if self.source is not RowSource.RELATED:
            raise ValueError("related JSON lengths may read RELATED values only")
        _ensure_enum(
            self.cardinality,
            RelatedLookupCardinality,
            "related JSON length cardinality",
        )
        if self.cardinality is not RelatedLookupCardinality.EXACTLY_ONE:
            raise ValueError("related JSON lengths are fail-closed EXACTLY_ONE lookups")
        _ensure_enum(
            self.key_requirement,
            RelatedLookupKeyRequirement,
            "related JSON length key requirement",
        )
        if (
            self.key_requirement
            is not RelatedLookupKeyRequirement.PRIMARY_KEY_OR_EXACT_UNIQUE
        ):
            raise ValueError(
                "related JSON length bindings must resolve to a primary key or exact UNIQUE key"
            )
        _ensure_bool(
            self.require_canonical_json,
            "related JSON length canonical requirement",
        )
        if not self.require_canonical_json:
            raise ValueError("related JSON lengths require canonical JSON")
        _ensure_enum(
            self.failure_policy,
            CanonicalJsonFailurePolicy,
            "related JSON length failure policy",
        )
        if self.failure_policy is not CanonicalJsonFailurePolicy.REJECT:
            raise ValueError("invalid related JSON lengths must reject")


ScalarExpression = (
    ScalarExpression
    | RelatedCanonicalJsonArrayValueSpec
    | RelatedCanonicalJsonArrayLengthValueSpec
)


@_dataclass(frozen=True, slots=True)
class RelatedCanonicalJsonArrayContainsExpression:
    """Fail-closed membership over one exact canonical JSON array."""

    table: str
    bindings: tuple[LookupBinding, ...]
    column: str
    element_type: CanonicalJsonScalarType
    element: ScalarExpression
    negated: bool
    source: RowSource = RowSource.RELATED
    cardinality: RelatedLookupCardinality = RelatedLookupCardinality.EXACTLY_ONE
    key_requirement: RelatedLookupKeyRequirement = RelatedLookupKeyRequirement.PRIMARY_KEY_OR_EXACT_UNIQUE
    require_canonical_json: bool = True
    failure_policy: CanonicalJsonFailurePolicy = CanonicalJsonFailurePolicy.REJECT

    def __post_init__(self) -> None:
        _ensure_identifier(self.table, "related JSON table identifier")
        bindings = _ensure_tuple(self.bindings, "related JSON bindings", nonempty=True)
        _ensure_exact_items(bindings, LookupBinding, "related JSON bindings")
        _ensure_unique(
            tuple(binding.related_column for binding in bindings),
            "related JSON binding columns",
        )
        if any(
            type(binding.value) is not ColumnValue or binding.value.source not in (RowSource.NEW, RowSource.CURRENT)
            for binding in bindings
        ):
            raise ValueError("related JSON bindings require NEW or CURRENT row columns")
        _ensure_identifier(self.column, "related JSON column identifier")
        _ensure_enum(self.element_type, CanonicalJsonScalarType, "related JSON element type")
        _ensure_scalar(self.element, "related JSON membership element")
        if type(self.element) is ColumnValue and self.element.source not in (
            RowSource.NEW,
            RowSource.CURRENT,
        ):
            raise ValueError("related JSON membership elements require NEW or CURRENT row columns")
        if type(self.element) in (
            QualifiedColumnValue,
            RelatedCanonicalJsonArrayValueSpec,
            RelatedCanonicalJsonArrayLengthValueSpec,
        ):
            raise ValueError("related JSON membership elements cannot read another relation")
        if type(self.element) is CanonicalJsonFieldValue and self.element.document.source not in (
            RowSource.NEW,
            RowSource.CURRENT,
        ):
            raise ValueError("related JSON membership elements cannot read RELATED JSON")
        _ensure_bool(self.negated, "related JSON membership negated")
        _ensure_enum(self.source, RowSource, "related JSON source")
        if self.source is not RowSource.RELATED:
            raise ValueError("related JSON arrays may read RELATED values only")
        _ensure_enum(self.cardinality, RelatedLookupCardinality, "related JSON cardinality")
        if self.cardinality is not RelatedLookupCardinality.EXACTLY_ONE:
            raise ValueError("related JSON arrays are fail-closed EXACTLY_ONE lookups")
        _ensure_enum(
            self.key_requirement,
            RelatedLookupKeyRequirement,
            "related JSON key requirement",
        )
        if self.key_requirement is not RelatedLookupKeyRequirement.PRIMARY_KEY_OR_EXACT_UNIQUE:
            raise ValueError("related JSON bindings must resolve to a primary key or exact UNIQUE key")
        _ensure_bool(self.require_canonical_json, "related JSON canonical requirement")
        if not self.require_canonical_json:
            raise ValueError("related JSON membership requires canonical JSON")
        _ensure_enum(self.failure_policy, CanonicalJsonFailurePolicy, "related JSON failure policy")
        if self.failure_policy is not CanonicalJsonFailurePolicy.REJECT:
            raise ValueError("invalid related JSON arrays must reject")


class RelationAggregateKind(_StrEnum):
    COUNT_ROWS = "COUNT_ROWS"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    CANONICAL_ORDERED_VALUE_HASH = "CANONICAL_ORDERED_VALUE_HASH"


@_dataclass(frozen=True, slots=True)
class RelationAggregateOrderSpec:
    column: str
    direction: SortDirection = SortDirection.ASC
    collation: Collation = Collation.BINARY

    def __post_init__(self) -> None:
        _ensure_identifier(self.column, "relation-aggregate order column")
        _ensure_enum(self.direction, SortDirection, "relation-aggregate order direction")
        _ensure_enum(self.collation, Collation, "relation-aggregate order collation")


AggregateExpected: _TypeAlias = ColumnValue | LiteralValue | RelatedValueLookupSpec


@_dataclass(frozen=True, slots=True)
class RelationAggregateExpression:
    """Closed comparison over one related row set.

    Pass M must resolve a RelatedValueLookupSpec binding to an exact primary-key
    or UNIQUE key and treat zero or multiple matches as a guard violation.
    """

    table: str
    bindings: tuple[LookupBinding, ...]
    aggregate: RelationAggregateKind
    source: RowSource
    value: str | None
    order: tuple[RelationAggregateOrderSpec, ...]
    operator: ComparisonOperator
    expected: AggregateExpected
    predicate: PredicateExpression | None = None

    def __post_init__(self) -> None:
        _ensure_identifier(self.table, "relation-aggregate table identifier")
        bindings = _ensure_tuple(self.bindings, "relation-aggregate bindings", nonempty=True)
        _ensure_exact_items(bindings, LookupBinding, "relation-aggregate bindings")
        _ensure_unique(
            tuple(binding.related_column for binding in bindings),
            "relation-aggregate binding columns",
        )
        if any(
            type(binding.value) is not ColumnValue or binding.value.source not in (RowSource.NEW, RowSource.CURRENT)
            for binding in bindings
        ):
            raise ValueError("relation-aggregate bindings require NEW or CURRENT row columns")
        _ensure_enum(self.aggregate, RelationAggregateKind, "relation-aggregate kind")
        _ensure_enum(self.source, RowSource, "relation-aggregate source")
        if self.source is not RowSource.RELATED:
            raise ValueError("relation aggregates may read RELATED rows only")
        order = _ensure_tuple(self.order, "relation-aggregate order")
        _ensure_exact_items(order, RelationAggregateOrderSpec, "relation-aggregate order")
        _ensure_unique(tuple(item.column for item in order), "relation-aggregate order columns")
        _ensure_enum(self.operator, ComparisonOperator, "relation-aggregate operator")
        if type(self.expected) not in (ColumnValue, LiteralValue, RelatedValueLookupSpec):
            raise TypeError(
                "relation-aggregate expected must be a NEW/CURRENT column, closed literal, or EXACTLY_ONE lookup"
            )
        if type(self.expected) is ColumnValue and self.expected.source not in (
            RowSource.NEW,
            RowSource.CURRENT,
        ):
            raise ValueError("relation-aggregate expected columns require NEW or CURRENT source")
        if self.predicate is not None:
            _ensure_predicate(self.predicate)
            if _contains_relation(self.predicate):
                raise ValueError("nested relation lookups are not part of the closed grammar")
            if _qualified_column_values(self.predicate):
                raise ValueError("single-relation aggregate predicates forbid qualified columns")
            if any(value.source is not RowSource.RELATED for value in _column_values(self.predicate)):
                raise ValueError("relation-aggregate predicates may reference RELATED columns only")

        expected_literal = self.expected.value if type(self.expected) is LiteralValue else None
        if self.aggregate is RelationAggregateKind.COUNT_ROWS:
            if self.value is not None or order:
                raise ValueError("COUNT_ROWS forbids value and order fields")
            if type(self.expected) is LiteralValue and (type(expected_literal) is not int or expected_literal < 0):
                raise ValueError("COUNT_ROWS literal expected must be a non-negative integer")
        elif self.aggregate is RelationAggregateKind.COUNT_DISTINCT:
            if self.value is None:
                raise ValueError("COUNT_DISTINCT requires a value column")
            _ensure_identifier(self.value, "COUNT_DISTINCT value column")
            if order:
                raise ValueError("COUNT_DISTINCT forbids order fields")
            if type(self.expected) is LiteralValue and (type(expected_literal) is not int or expected_literal < 0):
                raise ValueError("COUNT_DISTINCT literal expected must be a non-negative integer")
        else:
            if self.value is None:
                raise ValueError("CANONICAL_ORDERED_VALUE_HASH requires a value column")
            _ensure_identifier(self.value, "canonical ordered value column")
            if not order:
                raise ValueError("CANONICAL_ORDERED_VALUE_HASH requires fixed ordering")
            if any(item.direction is not SortDirection.ASC or item.collation is not Collation.BINARY for item in order):
                raise ValueError("canonical ordered hashes require BINARY ASC ordering")
            if self.operator not in (ComparisonOperator.EQ, ComparisonOperator.NE):
                raise ValueError("canonical ordered hashes support EQ or NE only")
            if type(self.expected) is LiteralValue:
                _ensure_sha256(expected_literal, "canonical ordered hash expected")


class RelationJoinCardinality(_StrEnum):
    """Every left/anchor row must match exactly one right row after predicate."""

    EXACTLY_ONE = "EXACTLY_ONE"


@_dataclass(frozen=True, slots=True)
class RelationJoinEqualitySpec:
    left_alias: str
    left_column: str
    right_alias: str
    right_column: str

    def __post_init__(self) -> None:
        _ensure_identifier(self.left_alias, "join left alias")
        _ensure_identifier(self.left_column, "join left column")
        _ensure_identifier(self.right_alias, "join right alias")
        _ensure_identifier(self.right_column, "join right column")
        if self.left_alias == self.right_alias:
            raise ValueError("join equality must connect two distinct relation aliases")


@_dataclass(frozen=True, slots=True)
class RelationJoinSpec:
    table: str
    alias: str
    on: tuple[RelationJoinEqualitySpec, ...]
    predicate: PredicateExpression | None = None
    cardinality: RelationJoinCardinality = RelationJoinCardinality.EXACTLY_ONE
    key_requirement: RelatedLookupKeyRequirement = RelatedLookupKeyRequirement.PRIMARY_KEY_OR_EXACT_UNIQUE

    def __post_init__(self) -> None:
        _ensure_identifier(self.table, "join table identifier")
        _ensure_identifier(self.alias, "join relation alias")
        on = _ensure_tuple(self.on, "join equality bindings", nonempty=True)
        _ensure_exact_items(on, RelationJoinEqualitySpec, "join equality bindings")
        _ensure_unique(
            tuple((item.left_alias, item.left_column, item.right_alias, item.right_column) for item in on),
            "join equality bindings",
        )
        _ensure_unique(
            tuple((item.left_alias, item.left_column) for item in on),
            "join equality left fields",
        )
        _ensure_unique(
            tuple(item.right_column for item in on),
            "join equality right columns",
        )
        if any(item.right_alias != self.alias for item in on):
            raise ValueError("join equality right alias must name the joined relation")
        if self.predicate is not None:
            _ensure_predicate(self.predicate)
            if _contains_relation(self.predicate):
                raise ValueError("join predicates cannot contain nested relation lookups")
            qualified = _qualified_column_values(self.predicate)
            if not qualified:
                raise ValueError("unqualified join-predicate columns cannot replace a qualified RELATED column")
            if not any(value.relation_alias == self.alias for value in qualified):
                raise ValueError("join predicate must reference the current joined relation alias")
            if any(value.source is not RowSource.RELATED for value in qualified):
                raise ValueError("join predicates may read RELATED columns only")
            if any(value.source is not RowSource.NEW for value in _column_values(self.predicate)):
                raise ValueError("unqualified join-predicate columns require NEW source")
        _ensure_enum(self.cardinality, RelationJoinCardinality, "join cardinality")
        if self.cardinality is not RelationJoinCardinality.EXACTLY_ONE:
            raise ValueError("joined evidence is fail-closed EXACTLY_ONE per left row")
        _ensure_enum(
            self.key_requirement,
            RelatedLookupKeyRequirement,
            "join key requirement",
        )
        if self.key_requirement is not RelatedLookupKeyRequirement.PRIMARY_KEY_OR_EXACT_UNIQUE:
            raise ValueError("join right columns must resolve to a primary key or exact UNIQUE key")


@_dataclass(frozen=True, slots=True)
class RelationJoinAggregateOrderSpec:
    relation_alias: str
    column: str
    direction: SortDirection = SortDirection.ASC
    collation: Collation = Collation.BINARY

    def __post_init__(self) -> None:
        _ensure_identifier(self.relation_alias, "join-aggregate order alias")
        _ensure_identifier(self.column, "join-aggregate order column")
        _ensure_enum(self.direction, SortDirection, "join-aggregate order direction")
        _ensure_enum(self.collation, Collation, "join-aggregate order collation")


@_dataclass(frozen=True, slots=True)
class RelationJoinAggregateExpression:
    """Closed aggregate over an anchored relation and exact inner joins."""

    anchor_table: str
    anchor_alias: str
    bindings: tuple[LookupBinding, ...]
    joins: tuple[RelationJoinSpec, ...]
    aggregate: RelationAggregateKind
    source: RowSource
    value: QualifiedColumnValue | None
    order: tuple[RelationJoinAggregateOrderSpec, ...]
    operator: ComparisonOperator
    expected: AggregateExpected

    def __post_init__(self) -> None:
        _ensure_identifier(self.anchor_table, "join-aggregate anchor table")
        _ensure_identifier(self.anchor_alias, "join-aggregate anchor alias")
        bindings = _ensure_tuple(self.bindings, "join-aggregate anchor bindings", nonempty=True)
        _ensure_exact_items(bindings, LookupBinding, "join-aggregate anchor bindings")
        _ensure_unique(
            tuple(binding.related_column for binding in bindings),
            "join-aggregate anchor binding columns",
        )
        if any(
            type(binding.value) is not ColumnValue or binding.value.source not in (RowSource.NEW, RowSource.CURRENT)
            for binding in bindings
        ):
            raise ValueError("join-aggregate bindings require NEW or CURRENT row columns")

        joins = _ensure_tuple(self.joins, "join-aggregate joins", nonempty=True)
        _ensure_exact_items(joins, RelationJoinSpec, "join-aggregate joins")
        aliases = {self.anchor_alias}
        for join in joins:
            if join.alias in aliases:
                raise ValueError("join relation aliases must be unique")
            if any(item.left_alias not in aliases for item in join.on):
                raise ValueError("join left aliases must already belong to the relation graph")
            aliases.add(join.alias)
            if join.predicate is not None and any(
                value.relation_alias not in aliases for value in _qualified_column_values(join.predicate)
            ):
                raise ValueError("join predicate references an unknown relation alias")

        _ensure_enum(self.aggregate, RelationAggregateKind, "join-aggregate kind")
        _ensure_enum(self.source, RowSource, "join-aggregate source")
        if self.source is not RowSource.RELATED:
            raise ValueError("join aggregates may read RELATED rows only")
        if self.value is not None:
            if type(self.value) is not QualifiedColumnValue:
                raise TypeError("join-aggregate value must be a qualified column")
            if self.value.relation_alias not in aliases:
                raise ValueError("join-aggregate value references an unknown relation alias")
        order = _ensure_tuple(self.order, "join-aggregate order")
        _ensure_exact_items(order, RelationJoinAggregateOrderSpec, "join-aggregate order")
        _ensure_unique(
            tuple((item.relation_alias, item.column) for item in order),
            "join-aggregate order fields",
        )
        if any(item.relation_alias not in aliases for item in order):
            raise ValueError("join-aggregate order references an unknown relation alias")
        _ensure_enum(self.operator, ComparisonOperator, "join-aggregate operator")
        if type(self.expected) not in (ColumnValue, LiteralValue, RelatedValueLookupSpec):
            raise TypeError(
                "join-aggregate expected must be a NEW/CURRENT column, closed literal, or EXACTLY_ONE lookup"
            )
        if type(self.expected) is ColumnValue and self.expected.source not in (
            RowSource.NEW,
            RowSource.CURRENT,
        ):
            raise ValueError("join-aggregate expected columns require NEW or CURRENT source")

        expected_literal = self.expected.value if type(self.expected) is LiteralValue else None
        if self.aggregate is RelationAggregateKind.COUNT_ROWS:
            if self.value is not None or order:
                raise ValueError("COUNT_ROWS forbids value and order fields")
            if type(self.expected) is LiteralValue and (type(expected_literal) is not int or expected_literal < 0):
                raise ValueError("COUNT_ROWS literal expected must be a non-negative integer")
        elif self.aggregate is RelationAggregateKind.COUNT_DISTINCT:
            if self.value is None:
                raise ValueError("COUNT_DISTINCT requires a value column")
            if order:
                raise ValueError("COUNT_DISTINCT forbids order fields")
            if type(self.expected) is LiteralValue and (type(expected_literal) is not int or expected_literal < 0):
                raise ValueError("COUNT_DISTINCT literal expected must be a non-negative integer")
        else:
            if self.value is None:
                raise ValueError("CANONICAL_ORDERED_VALUE_HASH requires a value column")
            if not order:
                raise ValueError("CANONICAL_ORDERED_VALUE_HASH requires fixed ordering")
            if any(item.direction is not SortDirection.ASC or item.collation is not Collation.BINARY for item in order):
                raise ValueError("canonical ordered hashes require BINARY ASC ordering")
            if self.operator not in (ComparisonOperator.EQ, ComparisonOperator.NE):
                raise ValueError("canonical ordered hashes support EQ or NE only")
            if type(self.expected) is LiteralValue:
                _ensure_sha256(expected_literal, "canonical ordered hash expected")


@_dataclass(frozen=True, slots=True)
class RelationExistsExpression:
    table: str
    bindings: tuple[LookupBinding, ...]
    predicate: PredicateExpression | None = None
    negated: bool = False

    def __post_init__(self) -> None:
        _ensure_identifier(self.table, "related table identifier")
        bindings = _ensure_tuple(self.bindings, "relation bindings", nonempty=True)
        _ensure_exact_items(bindings, LookupBinding, "relation bindings")
        _ensure_unique(
            tuple(binding.related_column for binding in bindings),
            "related binding columns",
        )
        if self.predicate is not None:
            _ensure_predicate(self.predicate)
            if _contains_relation(self.predicate):
                raise ValueError("nested relation lookups are not part of the closed grammar")
            if _qualified_column_values(self.predicate):
                raise ValueError("single-relation predicates forbid qualified columns")
            if any(value.source not in (RowSource.RELATED, RowSource.NEW) for value in _column_values(self.predicate)):
                raise ValueError("single-relation predicates may reference RELATED and NEW columns only")
        _ensure_bool(self.negated, "relation negated")


@_dataclass(frozen=True, slots=True)
class CanonicalJsonPayloadHashExpression:
    """Recompute a canonical JSON document hash with optional owned exclusions.

    Empty ``excluded_paths`` hashes the complete canonical document.  A related
    expected hash must be obtained through one fail-closed exact-row lookup.
    """

    document: ColumnValue
    excluded_paths: tuple[tuple[str, ...], ...]
    encoding: CanonicalEncoding
    algorithm: RowHashAlgorithm
    operator: ComparisonOperator
    expected_hash: ColumnValue | RelatedValueLookupSpec
    require_canonical_json: bool = True
    failure_policy: CanonicalJsonFailurePolicy = CanonicalJsonFailurePolicy.REJECT

    def __post_init__(self) -> None:
        if type(self.document) is not ColumnValue:
            raise TypeError("canonical JSON payload hash requires a row column")
        if self.document.source not in (RowSource.CURRENT, RowSource.NEW):
            raise ValueError("canonical JSON payload hash requires a CURRENT or NEW document")
        paths = _ensure_tuple(self.excluded_paths, "canonical JSON excluded paths")
        normalized: list[tuple[str, ...]] = []
        for path in paths:
            path = _ensure_tuple(path, "canonical JSON excluded path", nonempty=True)
            for item in path:
                _ensure_identifier(item, "canonical JSON excluded path item")
            normalized.append(path)
        _ensure_unique(tuple(normalized), "canonical JSON excluded paths")
        _ensure_enum(self.encoding, CanonicalEncoding, "canonical JSON hash encoding")
        if self.encoding is not CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1:
            raise ValueError("canonical JSON payload hashes require QUANTOS canonical JSON v1")
        _ensure_enum(self.algorithm, RowHashAlgorithm, "canonical JSON hash algorithm")
        if self.algorithm is not RowHashAlgorithm.SHA256:
            raise ValueError("canonical JSON payload hashes require SHA256")
        _ensure_enum(self.operator, ComparisonOperator, "canonical JSON hash operator")
        if self.operator not in (ComparisonOperator.EQ, ComparisonOperator.NE):
            raise ValueError("canonical JSON payload hash comparisons support EQ or NE only")
        if type(self.expected_hash) is ColumnValue:
            if self.expected_hash.source is not self.document.source:
                raise ValueError("canonical JSON document and expected hash must use the same row source")
        elif type(self.expected_hash) is not RelatedValueLookupSpec:
            raise TypeError("canonical JSON expected hash must be a row column or EXACTLY_ONE lookup")
        _ensure_bool(self.require_canonical_json, "canonical JSON hash requirement")
        if not self.require_canonical_json:
            raise ValueError("canonical JSON payload hashing must reject non-canonical documents")
        _ensure_enum(self.failure_policy, CanonicalJsonFailurePolicy, "canonical JSON hash failure policy")
        if self.failure_policy is not CanonicalJsonFailurePolicy.REJECT:
            raise ValueError("malformed JSON, missing exclusions, and hash failures must reject")


class RelationExtremum(_StrEnum):
    MAX = "MAX"
    MIN = "MIN"


@_dataclass(frozen=True, slots=True)
class RelationMetricSpec:
    metric_id: str
    inputs: tuple[QualifiedColumnValue, ...]

    def __post_init__(self) -> None:
        _ensure_token(self.metric_id, "relation metric ID")
        if self.metric_id != "MEAN_NET_RETURN_DIFFERENTIAL_V1":
            raise ValueError("the closed relation metric is MEAN_NET_RETURN_DIFFERENTIAL_V1")
        inputs = _ensure_tuple(self.inputs, "relation metric inputs", nonempty=True)
        _ensure_exact_items(inputs, QualifiedColumnValue, "relation metric inputs")
        if len(inputs) != 2:
            raise ValueError("mean net-return differential requires candidate and benchmark inputs")
        _ensure_unique(
            tuple((item.relation_alias, item.column) for item in inputs),
            "relation metric inputs",
        )


@_dataclass(frozen=True, slots=True)
class RelationArgExtremumExpression:
    """Compare a caller result with the deterministic arg-extremum of exact joined evidence."""

    anchor_table: str
    anchor_alias: str
    bindings: tuple[LookupBinding, ...]
    joins: tuple[RelationJoinSpec, ...]
    metric: RelationMetricSpec
    extremum: RelationExtremum
    tie_break: tuple[RelationJoinAggregateOrderSpec, ...]
    result: tuple[QualifiedColumnValue, ...]
    operator: ComparisonOperator
    expected: tuple[ScalarExpression, ...]

    def __post_init__(self) -> None:
        _ensure_identifier(self.anchor_table, "arg-extremum anchor table")
        _ensure_identifier(self.anchor_alias, "arg-extremum anchor alias")
        bindings = _ensure_tuple(self.bindings, "arg-extremum bindings", nonempty=True)
        _ensure_exact_items(bindings, LookupBinding, "arg-extremum bindings")
        _ensure_unique(
            tuple(binding.related_column for binding in bindings),
            "arg-extremum binding columns",
        )
        if any(
            type(binding.value) is not ColumnValue or binding.value.source not in (RowSource.NEW, RowSource.CURRENT)
            for binding in bindings
        ):
            raise ValueError("arg-extremum bindings require NEW or CURRENT row columns")
        joins = _ensure_tuple(self.joins, "arg-extremum joins", nonempty=True)
        _ensure_exact_items(joins, RelationJoinSpec, "arg-extremum joins")
        if len(joins) != 1:
            raise ValueError("mean net-return differential requires one exact benchmark join")
        aliases = {self.anchor_alias}
        for join in joins:
            if join.alias in aliases:
                raise ValueError("arg-extremum relation aliases must be unique")
            if any(item.left_alias not in aliases for item in join.on):
                raise ValueError("arg-extremum join left aliases must already belong to the graph")
            aliases.add(join.alias)
            if join.predicate is not None and any(
                value.relation_alias not in aliases for value in _qualified_column_values(join.predicate)
            ):
                raise ValueError("arg-extremum join predicate references an unknown alias")
        if type(self.metric) is not RelationMetricSpec:
            raise TypeError("arg-extremum metric must be a closed RelationMetricSpec")
        if any(item.relation_alias not in aliases for item in self.metric.inputs):
            raise ValueError("arg-extremum metric input references an unknown alias")
        if tuple((item.relation_alias, item.column) for item in self.metric.inputs) != (
            (self.anchor_alias, "series_payload_json"),
            (joins[0].alias, "series_payload_json"),
        ):
            raise ValueError(
                "mean net-return differential requires anchor and benchmark series_payload_json"
            )
        _ensure_enum(self.extremum, RelationExtremum, "relation extremum")
        if self.extremum is not RelationExtremum.MAX:
            raise ValueError("winner selection requires the MAX relation extremum")
        tie_break = _ensure_tuple(self.tie_break, "arg-extremum tie break", nonempty=True)
        _ensure_exact_items(tie_break, RelationJoinAggregateOrderSpec, "arg-extremum tie break")
        _ensure_unique(
            tuple((item.relation_alias, item.column) for item in tie_break),
            "arg-extremum tie-break fields",
        )
        if any(item.relation_alias not in aliases for item in tie_break):
            raise ValueError("arg-extremum tie break references an unknown alias")
        if any(item.direction is not SortDirection.ASC or item.collation is not Collation.BINARY for item in tie_break):
            raise ValueError("arg-extremum tie breaks require BINARY ASC ordering")
        if len(tie_break) != 1 or (
            tie_break[0].relation_alias,
            tie_break[0].column,
        ) != (self.anchor_alias, "config_hash"):
            raise ValueError("winner ties require anchor config_hash BINARY ASC")
        result = _ensure_tuple(self.result, "arg-extremum result", nonempty=True)
        _ensure_exact_items(result, QualifiedColumnValue, "arg-extremum result")
        if len(result) != 2:
            raise ValueError("winner arg-extremum returns config and evaluation receipt hashes")
        if any(item.relation_alias not in aliases for item in result):
            raise ValueError("arg-extremum result references an unknown alias")
        if tuple((item.relation_alias, item.column) for item in result) != (
            (self.anchor_alias, "config_hash"),
            (self.anchor_alias, "evaluation_receipt_hash"),
        ):
            raise ValueError("winner result must be anchor config and evaluation receipt hashes")
        _ensure_enum(self.operator, ComparisonOperator, "arg-extremum operator")
        if self.operator not in (ComparisonOperator.EQ, ComparisonOperator.NE):
            raise ValueError("arg-extremum comparisons support EQ or NE only")
        expected = _ensure_tuple(self.expected, "arg-extremum expected values", nonempty=True)
        if len(expected) != len(result):
            raise ValueError("arg-extremum result and expected values require equal cardinality")
        for value in expected:
            _ensure_scalar(value, "arg-extremum expected value")
            if type(value) is not ColumnValue or value.source not in (
                RowSource.NEW,
                RowSource.CURRENT,
            ):
                raise ValueError("arg-extremum expected values require NEW or CURRENT row columns")


@_dataclass(frozen=True, slots=True)
class RelationAggregateIncludeValueSpec:
    column: str
    value: ScalarExpression

    def __post_init__(self) -> None:
        _ensure_identifier(self.column, "policy-aggregate included column")
        _ensure_scalar(self.value, "policy-aggregate included value")
        if type(self.value) is not ColumnValue or self.value.source not in (
            RowSource.NEW,
            RowSource.CURRENT,
        ):
            raise ValueError("policy-aggregate included values require NEW or CURRENT row columns")


class RelationLatestPerDomainCardinality(_StrEnum):
    """Closed row-selection rule for a declared-domain policy aggregate."""

    EXACTLY_ONE_PER_DECLARED_DOMAIN_VALUE = "EXACTLY_ONE_PER_DECLARED_DOMAIN_VALUE"


@_dataclass(frozen=True, slots=True)
class RelationPolicyAggregateExpression:
    """Evaluate one named deterministic policy over the latest complete domain.

    Rows are partitioned by ``domain_column`` and the maximum
    ``latest_ordinal_column`` is selected after logically including the current
    row.  Exactly one selected row must exist for every value in
    ``declared_domain`` and no undeclared value may participate.  This permits a
    mixed replication round: resolved grid points retain their latest earlier
    row while only ambiguous points advance.
    """

    table: str
    bindings: tuple[LookupBinding, ...]
    policy_id: str
    source: RowSource
    inputs: tuple[str, ...]
    order: tuple[RelationAggregateOrderSpec, ...]
    operator: ComparisonOperator
    expected: ScalarExpression
    predicate: PredicateExpression | None
    include_row: tuple[RelationAggregateIncludeValueSpec, ...]
    declared_domain: RelatedCanonicalJsonArrayValueSpec
    domain_column: str
    latest_ordinal_column: str
    include_latest_ordinal: ScalarExpression
    latest_cardinality: RelationLatestPerDomainCardinality

    def __post_init__(self) -> None:
        _ensure_identifier(self.table, "policy-aggregate table identifier")
        bindings = _ensure_tuple(self.bindings, "policy-aggregate bindings", nonempty=True)
        _ensure_exact_items(bindings, LookupBinding, "policy-aggregate bindings")
        _ensure_unique(
            tuple(binding.related_column for binding in bindings),
            "policy-aggregate binding columns",
        )
        if any(
            type(binding.value) is not ColumnValue or binding.value.source not in (RowSource.NEW, RowSource.CURRENT)
            for binding in bindings
        ):
            raise ValueError("policy-aggregate bindings require NEW or CURRENT row columns")
        if tuple(binding.related_column for binding in bindings) != (
            "authority_instance_id",
            "family_id",
            "algorithm_version",
        ):
            raise ValueError("assessment policy bindings require authority, family, and algorithm identity")
        _ensure_token(self.policy_id, "policy-aggregate policy ID")
        if self.policy_id != "ANY_GRID_DISAGREEMENT_MAX_P_V1":
            raise ValueError("the closed assessment policy is ANY_GRID_DISAGREEMENT_MAX_P_V1")
        _ensure_enum(self.source, RowSource, "policy-aggregate source")
        if self.source is not RowSource.RELATED:
            raise ValueError("policy aggregates may read RELATED rows only")
        inputs = _ensure_tuple(self.inputs, "policy-aggregate inputs", nonempty=True)
        for column in inputs:
            _ensure_identifier(column, "policy-aggregate input column")
        _ensure_unique(inputs, "policy-aggregate input columns")
        expected_inputs = (
            "block_length",
            "replication_count",
            "reality_check_result_hash",
            "spa_result_hash",
            "grid_point_status",
            "diagnostic_verdict",
        )
        if inputs != expected_inputs:
            raise ValueError("assessment policy inputs must use the exact declared grid evidence tuple")
        order = _ensure_tuple(self.order, "policy-aggregate order", nonempty=True)
        _ensure_exact_items(order, RelationAggregateOrderSpec, "policy-aggregate order")
        if tuple(item.column for item in order) != ("block_length",) or any(
            item.direction is not SortDirection.ASC or item.collation is not Collation.BINARY for item in order
        ):
            raise ValueError("assessment policy requires block_length BINARY ASC ordering")
        _ensure_enum(self.operator, ComparisonOperator, "policy-aggregate operator")
        if self.operator not in (ComparisonOperator.EQ, ComparisonOperator.NE):
            raise ValueError("policy-aggregate comparisons support EQ or NE only")
        _ensure_scalar(self.expected, "policy-aggregate expected value")
        if type(self.expected) is not ColumnValue or self.expected.source not in (
            RowSource.NEW,
            RowSource.CURRENT,
        ):
            raise ValueError("policy-aggregate expected values require NEW or CURRENT row columns")
        if self.predicate is not None:
            _ensure_predicate(self.predicate)
            if _contains_relation(self.predicate):
                raise ValueError("policy-aggregate predicates cannot contain nested relation lookups")
            if _qualified_column_values(self.predicate):
                raise ValueError("policy-aggregate predicates forbid qualified columns")
            if any(value.source is not RowSource.RELATED for value in _column_values(self.predicate)):
                raise ValueError("policy-aggregate predicates may reference RELATED columns only")
        include_row = _ensure_tuple(self.include_row, "policy-aggregate included row", nonempty=True)
        _ensure_exact_items(
            include_row,
            RelationAggregateIncludeValueSpec,
            "policy-aggregate included row",
        )
        if tuple(item.column for item in include_row) != inputs:
            raise ValueError("policy-aggregate included row must cover every policy input in order")
        if type(self.declared_domain) is not RelatedCanonicalJsonArrayValueSpec:
            raise TypeError("policy-aggregate declared domain must be a canonical related JSON array")
        if self.declared_domain.projection is not CanonicalJsonArrayProjection.CANONICAL_ARRAY:
            raise ValueError("policy-aggregate declared domain requires CANONICAL_ARRAY projection")
        if self.declared_domain.element_type is not CanonicalJsonScalarType.INTEGER:
            raise ValueError("assessment block-length domain requires INTEGER elements")
        _ensure_identifier(self.domain_column, "policy-aggregate domain column")
        if self.domain_column != "block_length":
            raise ValueError("assessment policy domain column must be block_length")
        _ensure_identifier(
            self.latest_ordinal_column,
            "policy-aggregate latest ordinal column",
        )
        if self.latest_ordinal_column != "schedule_ordinal":
            raise ValueError("assessment policy latest ordinal column must be schedule_ordinal")
        _ensure_scalar(
            self.include_latest_ordinal,
            "policy-aggregate current schedule ordinal",
        )
        if (
            type(self.include_latest_ordinal) is not ColumnValue
            or self.include_latest_ordinal.column != "schedule_ordinal"
            or self.include_latest_ordinal.source not in (RowSource.NEW, RowSource.CURRENT)
        ):
            raise ValueError("policy aggregate requires the current schedule_ordinal")
        _ensure_enum(
            self.latest_cardinality,
            RelationLatestPerDomainCardinality,
            "latest-per-domain cardinality",
        )
        if self.latest_cardinality is not (
            RelationLatestPerDomainCardinality.EXACTLY_ONE_PER_DECLARED_DOMAIN_VALUE
        ):
            raise ValueError("latest-per-domain cardinality must be exact")


@_dataclass(frozen=True, slots=True)
class RelationLatestPerDomainPolicyExpression:
    """Derive one family conclusion from the latest row in every grid domain.

    Unlike the checkpoint-oriented predecessor node, this expression never
    injects the row currently being inserted.  It is evaluated only while a
    separate family-conclusion row is inserted.  Pass M must select exactly one
    maximum-ordinal row for every declared block length, reject undeclared or
    duplicate latest rows, apply the fixed family policy, and bind the canonical
    ordered terminal ``record_hash`` vector to the conclusion row.
    """

    table: str
    bindings: tuple[LookupBinding, ...]
    policy_id: str
    source: RowSource
    inputs: tuple[str, ...]
    order: tuple[RelationAggregateOrderSpec, ...]
    expected_verdict: ScalarExpression
    expected_ordered_record_hashes_hash: ScalarExpression
    declared_domain: RelatedCanonicalJsonArrayValueSpec
    domain_column: str
    latest_ordinal_column: str
    latest_cardinality: RelationLatestPerDomainCardinality
    negated: bool

    def __post_init__(self) -> None:
        _ensure_identifier(self.table, "latest-policy table identifier")
        bindings = _ensure_tuple(
            self.bindings,
            "latest-policy bindings",
            nonempty=True,
        )
        _ensure_exact_items(bindings, LookupBinding, "latest-policy bindings")
        _ensure_unique(
            tuple(binding.related_column for binding in bindings),
            "latest-policy binding columns",
        )
        if any(
            type(binding.value) is not ColumnValue
            or binding.value.source not in (RowSource.NEW, RowSource.CURRENT)
            for binding in bindings
        ):
            raise ValueError("latest-policy bindings require NEW or CURRENT row columns")
        if tuple(binding.related_column for binding in bindings) != (
            "authority_instance_id",
            "family_id",
            "algorithm_version",
        ):
            raise ValueError(
                "latest-policy bindings require authority, family, and algorithm identity"
            )
        _ensure_token(self.policy_id, "latest-policy policy ID")
        if self.policy_id != "ANY_GRID_DISAGREEMENT_MAX_P_V1":
            raise ValueError("the closed assessment policy is ANY_GRID_DISAGREEMENT_MAX_P_V1")
        _ensure_enum(self.source, RowSource, "latest-policy source")
        if self.source is not RowSource.RELATED:
            raise ValueError("latest-policy expressions may read RELATED rows only")
        inputs = _ensure_tuple(self.inputs, "latest-policy inputs", nonempty=True)
        for column in inputs:
            _ensure_identifier(column, "latest-policy input column")
        _ensure_unique(inputs, "latest-policy input columns")
        expected_inputs = (
            "block_length",
            "replication_count",
            "reality_check_result_hash",
            "spa_result_hash",
            "grid_point_status",
            "grid_point_verdict",
            "record_hash",
        )
        if inputs != expected_inputs:
            raise ValueError(
                "latest-policy inputs must use the exact declared grid evidence tuple"
            )
        order = _ensure_tuple(self.order, "latest-policy order", nonempty=True)
        _ensure_exact_items(order, RelationAggregateOrderSpec, "latest-policy order")
        if tuple(item.column for item in order) != ("block_length",) or any(
            item.direction is not SortDirection.ASC
            or item.collation is not Collation.BINARY
            for item in order
        ):
            raise ValueError("latest-policy requires block_length BINARY ASC ordering")
        for value, expected_column, label in (
            (
                self.expected_verdict,
                "diagnostic_verdict",
                "latest-policy expected diagnostic_verdict",
            ),
            (
                self.expected_ordered_record_hashes_hash,
                "ordered_latest_grid_assessment_hashes_hash",
                "latest-policy expected ordered_latest_grid_assessment_hashes_hash",
            ),
        ):
            _ensure_scalar(value, label)
            if (
                type(value) is not ColumnValue
                or value.column != expected_column
                or value.source not in (RowSource.NEW, RowSource.CURRENT)
            ):
                raise ValueError(f"{label} requires the {expected_column} conclusion column")
        if type(self.declared_domain) is not RelatedCanonicalJsonArrayValueSpec:
            raise TypeError("latest-policy declared domain must be a canonical related JSON array")
        if self.declared_domain.projection is not CanonicalJsonArrayProjection.CANONICAL_ARRAY:
            raise ValueError("latest-policy declared domain requires CANONICAL_ARRAY projection")
        if self.declared_domain.element_type is not CanonicalJsonScalarType.INTEGER:
            raise ValueError("latest-policy block-length domain requires INTEGER elements")
        _ensure_identifier(self.domain_column, "latest-policy domain column")
        if self.domain_column != "block_length":
            raise ValueError("latest-policy domain column must be block_length")
        _ensure_identifier(
            self.latest_ordinal_column,
            "latest-policy ordinal column",
        )
        if self.latest_ordinal_column != "schedule_ordinal":
            raise ValueError("latest-policy ordinal column must be schedule_ordinal")
        _ensure_enum(
            self.latest_cardinality,
            RelationLatestPerDomainCardinality,
            "latest-per-domain cardinality",
        )
        if self.latest_cardinality is not (
            RelationLatestPerDomainCardinality.EXACTLY_ONE_PER_DECLARED_DOMAIN_VALUE
        ):
            raise ValueError("latest-per-domain cardinality must be exact")
        _ensure_bool(self.negated, "latest-policy negated")
        if not self.negated:
            raise ValueError("latest-policy guard must reject a mismatched conclusion")


class PrefixPredecessorCardinality(_StrEnum):
    ZERO_AT_GENESIS_ELSE_EXACTLY_ONE = "ZERO_AT_GENESIS_ELSE_EXACTLY_ONE"


@_dataclass(frozen=True, slots=True)
class PrefixStreamExtensionExpression:
    """Prove one exact deterministic bootstrap-prefix transition.

    At ``first_ordinal`` the previous hash must equal ``genesis_hash`` and the
    deterministic stream starts empty. Later ordinals require exactly one row
    matching ``previous_bindings`` and must extend, never replace, its complete
    prefix. The same declaration algorithm tuple and block length govern both
    branches.
    """

    previous_table: str
    previous_bindings: tuple[LookupBinding, ...]
    previous_prefix_hash_column: str
    previous_replication_count_column: str
    previous_period_count_column: str
    declaration_table: str
    declaration_bindings: tuple[LookupBinding, ...]
    algorithm_columns: tuple[str, ...]
    current_prefix_hash: ScalarExpression
    current_replication_count: ScalarExpression
    current_period_count: ScalarExpression
    block_length: ScalarExpression
    current_ordinal: ScalarExpression
    genesis_hash: ScalarExpression
    negated: bool
    first_ordinal: int = 1
    predecessor_cardinality: PrefixPredecessorCardinality = (
        PrefixPredecessorCardinality.ZERO_AT_GENESIS_ELSE_EXACTLY_ONE
    )
    genesis_empty_prefix: bool = True

    def __post_init__(self) -> None:
        _ensure_identifier(self.previous_table, "prefix predecessor table")
        previous = _ensure_tuple(self.previous_bindings, "prefix predecessor bindings", nonempty=True)
        _ensure_exact_items(previous, LookupBinding, "prefix predecessor bindings")
        _ensure_unique(
            tuple(binding.related_column for binding in previous),
            "prefix predecessor binding columns",
        )
        if any(
            type(binding.value) is not ColumnValue or binding.value.source not in (RowSource.NEW, RowSource.CURRENT)
            for binding in previous
        ):
            raise ValueError("prefix predecessor bindings require NEW or CURRENT row columns")
        _ensure_identifier(self.previous_prefix_hash_column, "previous prefix hash column")
        _ensure_identifier(
            self.previous_replication_count_column,
            "previous replication-count column",
        )
        _ensure_identifier(
            self.previous_period_count_column,
            "previous period-count column",
        )
        if self.previous_period_count_column != "period_count":
            raise ValueError("prefix extension period-count column is fixed at period_count")
        _ensure_identifier(self.declaration_table, "prefix declaration table")
        declaration = _ensure_tuple(
            self.declaration_bindings,
            "prefix declaration bindings",
            nonempty=True,
        )
        _ensure_exact_items(declaration, LookupBinding, "prefix declaration bindings")
        _ensure_unique(
            tuple(binding.related_column for binding in declaration),
            "prefix declaration binding columns",
        )
        if any(
            type(binding.value) is not ColumnValue or binding.value.source not in (RowSource.NEW, RowSource.CURRENT)
            for binding in declaration
        ):
            raise ValueError("prefix declaration bindings require NEW or CURRENT row columns")
        algorithms = _ensure_tuple(self.algorithm_columns, "prefix algorithm columns", nonempty=True)
        for column in algorithms:
            _ensure_identifier(column, "prefix algorithm column")
        _ensure_unique(algorithms, "prefix algorithm columns")
        expected_algorithms = (
            "bootstrap_seed",
            "bootstrap_algorithm_version",
            "prng_algorithm_version",
            "canonical_json_version",
            "block_length_policy_id",
        )
        if algorithms != expected_algorithms:
            raise ValueError("prefix extension requires the exact declared bootstrap algorithm tuple")
        for label, value in (
            ("current prefix hash", self.current_prefix_hash),
            ("current replication count", self.current_replication_count),
            ("current period count", self.current_period_count),
            ("block length", self.block_length),
            ("current ordinal", self.current_ordinal),
            ("genesis hash", self.genesis_hash),
        ):
            _ensure_scalar(value, label)
            if type(value) is not ColumnValue or value.source not in (
                RowSource.NEW,
                RowSource.CURRENT,
            ):
                raise ValueError(f"{label} requires a NEW or CURRENT row column")
        _ensure_bool(self.negated, "prefix-extension negated")
        if type(self.first_ordinal) is not int or self.first_ordinal != 1:
            raise ValueError("prefix-extension first ordinal is fixed at one")
        _ensure_enum(
            self.predecessor_cardinality,
            PrefixPredecessorCardinality,
            "prefix predecessor cardinality",
        )
        if (
            self.predecessor_cardinality
            is not PrefixPredecessorCardinality.ZERO_AT_GENESIS_ELSE_EXACTLY_ONE
        ):
            raise ValueError("prefix predecessor is zero at genesis and EXACTLY_ONE thereafter")
        _ensure_bool(self.genesis_empty_prefix, "genesis empty-prefix rule")
        if not self.genesis_empty_prefix:
            raise ValueError("the first bootstrap prefix must extend the empty genesis stream")


@_dataclass(frozen=True, slots=True)
class ChainPredecessorExpression:
    """Match genesis at the first ordinal or one exact immediately preceding row."""

    table: str
    partition_bindings: tuple[LookupBinding, ...]
    ordinal_column: str
    current_ordinal: ScalarExpression
    record_hash_column: str
    current_previous_hash: ScalarExpression
    genesis_hash: ScalarExpression
    negated: bool

    def __post_init__(self) -> None:
        _ensure_identifier(self.table, "chain predecessor table")
        bindings = _ensure_tuple(self.partition_bindings, "chain partition bindings", nonempty=True)
        _ensure_exact_items(bindings, LookupBinding, "chain partition bindings")
        _ensure_unique(
            tuple(binding.related_column for binding in bindings),
            "chain partition binding columns",
        )
        if any(
            type(binding.value) is not ColumnValue or binding.value.source not in (RowSource.NEW, RowSource.CURRENT)
            for binding in bindings
        ):
            raise ValueError("chain partition bindings require NEW or CURRENT row columns")
        _ensure_identifier(self.ordinal_column, "chain ordinal column")
        _ensure_identifier(self.record_hash_column, "chain record-hash column")
        for label, value in (
            ("current ordinal", self.current_ordinal),
            ("current previous hash", self.current_previous_hash),
            ("genesis hash", self.genesis_hash),
        ):
            _ensure_scalar(value, label)
            if type(value) is not ColumnValue or value.source not in (
                RowSource.NEW,
                RowSource.CURRENT,
            ):
                raise ValueError(f"{label} requires a NEW or CURRENT row column")
        _ensure_bool(self.negated, "chain predecessor negated")


class RelationChainPrefixProof(_StrEnum):
    COMPLETE_ANCESTOR_TO_CURRENT_TIP = "COMPLETE_ANCESTOR_TO_CURRENT_TIP"


@_dataclass(frozen=True, slots=True)
class RelationChainPrefixExpression:
    """Prove an exact ancestor-to-current-tip segment of one hash chain.

    Pass M must resolve ``ancestor_hash`` through its exact lookup, prove both
    hashes belong to the bound partition, require ``head_hash`` to be the
    maximum ordinal currently present, and verify every intervening ordinal and
    previous-hash link without gaps or forks.
    """

    table: str
    partition_bindings: tuple[LookupBinding, ...]
    ordinal_column: str
    record_hash_column: str
    previous_hash_column: str
    ancestor_hash: RelatedValueLookupSpec
    head_hash: ScalarExpression
    source: RowSource
    proof: RelationChainPrefixProof
    negated: bool

    def __post_init__(self) -> None:
        _ensure_identifier(self.table, "chain-prefix table")
        bindings = _ensure_tuple(
            self.partition_bindings,
            "chain-prefix partition bindings",
            nonempty=True,
        )
        _ensure_exact_items(bindings, LookupBinding, "chain-prefix partition bindings")
        _ensure_unique(
            tuple(binding.related_column for binding in bindings),
            "chain-prefix partition binding columns",
        )
        if any(
            type(binding.value) is not ColumnValue
            or binding.value.source not in (RowSource.NEW, RowSource.CURRENT)
            for binding in bindings
        ):
            raise ValueError("chain-prefix partition bindings require NEW or CURRENT row columns")
        for column, label in (
            (self.ordinal_column, "chain-prefix ordinal column"),
            (self.record_hash_column, "chain-prefix record-hash column"),
            (self.previous_hash_column, "chain-prefix previous-hash column"),
        ):
            _ensure_identifier(column, label)
        if type(self.ancestor_hash) is not RelatedValueLookupSpec:
            raise TypeError("chain-prefix ancestor hash requires an EXACTLY_ONE lookup")
        _ensure_scalar(self.head_hash, "chain-prefix head hash")
        if type(self.head_hash) is not ColumnValue or self.head_hash.source not in (
            RowSource.NEW,
            RowSource.CURRENT,
        ):
            raise ValueError("chain-prefix head hash requires a NEW or CURRENT row column")
        _ensure_enum(self.source, RowSource, "chain-prefix source")
        if self.source is not RowSource.RELATED:
            raise ValueError("chain-prefix rows require RELATED source")
        _ensure_enum(self.proof, RelationChainPrefixProof, "chain-prefix proof")
        if self.proof is not RelationChainPrefixProof.COMPLETE_ANCESTOR_TO_CURRENT_TIP:
            raise ValueError("chain-prefix proof must be complete ancestor to current tip")
        _ensure_bool(self.negated, "chain-prefix negated")


@_dataclass(frozen=True, slots=True)
class RelationSetEqualityExpression:
    """Compare two complete canonical relation sets, optionally including the NEW row."""

    left_table: str
    left_bindings: tuple[LookupBinding, ...]
    left_value: str
    left_predicate: PredicateExpression | None
    left_include_values: tuple[ScalarExpression, ...]
    right_table: str
    right_bindings: tuple[LookupBinding, ...]
    right_value: str
    right_predicate: PredicateExpression | None
    order: tuple[RelationAggregateOrderSpec, ...]
    negated: bool

    def __post_init__(self) -> None:
        for table, label in (
            (self.left_table, "left set table"),
            (self.right_table, "right set table"),
        ):
            _ensure_identifier(table, label)
        for bindings, label in (
            (self.left_bindings, "left set bindings"),
            (self.right_bindings, "right set bindings"),
        ):
            bindings = _ensure_tuple(bindings, label, nonempty=True)
            _ensure_exact_items(bindings, LookupBinding, label)
            _ensure_unique(
                tuple(binding.related_column for binding in bindings),
                f"{label} columns",
            )
        _ensure_identifier(self.left_value, "left set value column")
        _ensure_identifier(self.right_value, "right set value column")
        for predicate, label in (
            (self.left_predicate, "left set predicate"),
            (self.right_predicate, "right set predicate"),
        ):
            if predicate is not None:
                _ensure_predicate(predicate)
                if _contains_relation(predicate):
                    raise ValueError(f"{label} cannot contain nested relation lookups")
                if _qualified_column_values(predicate):
                    raise ValueError(f"{label} forbids qualified columns")
                if any(value.source is not RowSource.RELATED for value in _column_values(predicate)):
                    raise ValueError(f"{label} may reference RELATED columns only")
        included = _ensure_tuple(self.left_include_values, "left included set values")
        for value in included:
            _ensure_scalar(value, "left included set value")
            if type(value) is not ColumnValue or value.source not in (
                RowSource.NEW,
                RowSource.CURRENT,
            ):
                raise ValueError("left included set values require NEW or CURRENT row columns")
        order = _ensure_tuple(self.order, "set equality order", nonempty=True)
        _ensure_exact_items(order, RelationAggregateOrderSpec, "set equality order")
        _ensure_unique(tuple(item.column for item in order), "set equality order columns")
        if any(item.direction is not SortDirection.ASC or item.collation is not Collation.BINARY for item in order):
            raise ValueError("relation set equality requires BINARY ASC ordering")
        _ensure_bool(self.negated, "relation set equality negated")


PredicateExpression: _TypeAlias = (
    ComparisonExpression
    | NullExpression
    | MembershipExpression
    | LogicalExpression
    | NotExpression
    | CanonicalJsonPayloadHashExpression
    | RelatedCanonicalJsonArrayContainsExpression
    | RelationExistsExpression
    | RelationAggregateExpression
    | RelationJoinAggregateExpression
    | RelationArgExtremumExpression
    | RelationPolicyAggregateExpression
    | RelationLatestPerDomainPolicyExpression
    | PrefixStreamExtensionExpression
    | ChainPredecessorExpression
    | RelationChainPrefixExpression
    | RelationSetEqualityExpression
)
CheckExpression: _TypeAlias = PredicateExpression

_PREDICATE_TYPES = (
    ComparisonExpression,
    NullExpression,
    MembershipExpression,
    LogicalExpression,
    NotExpression,
    CanonicalJsonPayloadHashExpression,
    RelatedCanonicalJsonArrayContainsExpression,
    RelationExistsExpression,
    RelationAggregateExpression,
    RelationJoinAggregateExpression,
    RelationArgExtremumExpression,
    RelationPolicyAggregateExpression,
    RelationLatestPerDomainPolicyExpression,
    PrefixStreamExtensionExpression,
    ChainPredecessorExpression,
    RelationChainPrefixExpression,
    RelationSetEqualityExpression,
)


def _ensure_predicate(value: object) -> None:
    if type(value) not in _PREDICATE_TYPES:
        raise TypeError("expression must be a closed predicate node")


def _walk_scalar(value: ScalarExpression):
    if type(value) not in (
        ColumnValue,
        LiteralValue,
        QualifiedColumnValue,
        CanonicalJsonFieldValue,
        OrdinalOffsetValue,
        RelatedCanonicalJsonArrayValueSpec,
        RelatedCanonicalJsonArrayLengthValueSpec,
    ):
        raise TypeError("value must be a closed scalar or canonical domain expression")
    yield value
    if type(value) is CanonicalJsonFieldValue:
        yield value.document
    elif type(value) is OrdinalOffsetValue:
        yield from _walk_scalar(value.value)
    elif type(value) is RelatedCanonicalJsonArrayValueSpec:
        for binding in value.bindings:
            yield from _walk_scalar(binding.value)
        if value.ordinal is not None:
            yield from _walk_scalar(value.ordinal)
    elif type(value) is RelatedCanonicalJsonArrayLengthValueSpec:
        for binding in value.bindings:
            yield from _walk_scalar(binding.value)


def _walk_expression(value: PredicateExpression):
    _ensure_predicate(value)
    yield value
    if type(value) is ComparisonExpression:
        yield from _walk_scalar(value.left)
        yield from _walk_scalar(value.right)
    elif type(value) is NullExpression:
        yield from _walk_scalar(value.value)
    elif type(value) is MembershipExpression:
        yield from _walk_scalar(value.value)
        yield from value.values
    elif type(value) is LogicalExpression:
        for operand in value.operands:
            yield from _walk_expression(operand)
    elif type(value) is NotExpression:
        yield from _walk_expression(value.operand)
    elif type(value) is CanonicalJsonPayloadHashExpression:
        yield from _walk_scalar(value.document)
        if type(value.expected_hash) is ColumnValue:
            yield from _walk_scalar(value.expected_hash)
        else:
            for binding in value.expected_hash.bindings:
                yield from _walk_scalar(binding.value)
    elif type(value) is RelatedCanonicalJsonArrayContainsExpression:
        for binding in value.bindings:
            yield from _walk_scalar(binding.value)
        yield from _walk_scalar(value.element)
    elif type(value) is RelationExistsExpression:
        for binding in value.bindings:
            yield from _walk_scalar(binding.value)
        if value.predicate is not None:
            yield from _walk_expression(value.predicate)
    elif type(value) is RelationAggregateExpression:
        for binding in value.bindings:
            yield from _walk_scalar(binding.value)
        if value.predicate is not None:
            yield from _walk_expression(value.predicate)
        if type(value.expected) is ColumnValue:
            yield from _walk_scalar(value.expected)
        elif type(value.expected) is RelatedValueLookupSpec:
            for binding in value.expected.bindings:
                yield from _walk_scalar(binding.value)
    elif type(value) is RelationJoinAggregateExpression:
        for binding in value.bindings:
            yield from _walk_scalar(binding.value)
        for join in value.joins:
            if join.predicate is not None:
                yield from _walk_expression(join.predicate)
        if value.value is not None:
            yield from _walk_scalar(value.value)
        if type(value.expected) is ColumnValue:
            yield from _walk_scalar(value.expected)
        elif type(value.expected) is RelatedValueLookupSpec:
            for binding in value.expected.bindings:
                yield from _walk_scalar(binding.value)
    elif type(value) is RelationArgExtremumExpression:
        for binding in value.bindings:
            yield from _walk_scalar(binding.value)
        for join in value.joins:
            if join.predicate is not None:
                yield from _walk_expression(join.predicate)
        yield from value.metric.inputs
        yield from value.result
        for expected in value.expected:
            yield from _walk_scalar(expected)
    elif type(value) is RelationPolicyAggregateExpression:
        for binding in value.bindings:
            yield from _walk_scalar(binding.value)
        if value.predicate is not None:
            yield from _walk_expression(value.predicate)
        yield from _walk_scalar(value.expected)
        for included in value.include_row:
            yield from _walk_scalar(included.value)
        yield from _walk_scalar(value.declared_domain)
        yield from _walk_scalar(value.include_latest_ordinal)
    elif type(value) is RelationLatestPerDomainPolicyExpression:
        for binding in value.bindings:
            yield from _walk_scalar(binding.value)
        yield from _walk_scalar(value.expected_verdict)
        yield from _walk_scalar(value.expected_ordered_record_hashes_hash)
        yield from _walk_scalar(value.declared_domain)
    elif type(value) is PrefixStreamExtensionExpression:
        for binding in (*value.previous_bindings, *value.declaration_bindings):
            yield from _walk_scalar(binding.value)
        yield from _walk_scalar(value.current_prefix_hash)
        yield from _walk_scalar(value.current_replication_count)
        yield from _walk_scalar(value.current_period_count)
        yield from _walk_scalar(value.block_length)
        yield from _walk_scalar(value.current_ordinal)
        yield from _walk_scalar(value.genesis_hash)
    elif type(value) is ChainPredecessorExpression:
        for binding in value.partition_bindings:
            yield from _walk_scalar(binding.value)
        yield from _walk_scalar(value.current_ordinal)
        yield from _walk_scalar(value.current_previous_hash)
        yield from _walk_scalar(value.genesis_hash)
    elif type(value) is RelationChainPrefixExpression:
        for binding in value.partition_bindings:
            yield from _walk_scalar(binding.value)
        for binding in value.ancestor_hash.bindings:
            yield from _walk_scalar(binding.value)
        yield from _walk_scalar(value.head_hash)
    else:
        for binding in (*value.left_bindings, *value.right_bindings):
            yield from _walk_scalar(binding.value)
        if value.left_predicate is not None:
            yield from _walk_expression(value.left_predicate)
        if value.right_predicate is not None:
            yield from _walk_expression(value.right_predicate)
        for included in value.left_include_values:
            yield from _walk_scalar(included)


def _contains_relation(value: PredicateExpression) -> bool:
    return any(
        type(node)
        in (
            RelationExistsExpression,
            RelationAggregateExpression,
            RelationJoinAggregateExpression,
            RelatedCanonicalJsonArrayValueSpec,
            RelatedCanonicalJsonArrayLengthValueSpec,
            RelatedCanonicalJsonArrayContainsExpression,
            RelationArgExtremumExpression,
            RelationPolicyAggregateExpression,
            RelationLatestPerDomainPolicyExpression,
            PrefixStreamExtensionExpression,
            ChainPredecessorExpression,
            RelationChainPrefixExpression,
            RelationSetEqualityExpression,
        )
        for node in _walk_expression(value)
    )


def _column_values(value: PredicateExpression) -> tuple[ColumnValue, ...]:
    return tuple(node for node in _walk_expression(value) if type(node) is ColumnValue)


def _qualified_column_values(value: PredicateExpression) -> tuple[QualifiedColumnValue, ...]:
    return tuple(node for node in _walk_expression(value) if type(node) is QualifiedColumnValue)


def _ensure_insert_guard_scalar(value: ScalarExpression, label: str) -> None:
    if type(value) is LiteralValue:
        return
    if type(value) is ColumnValue:
        if value.source is not RowSource.NEW:
            raise ValueError(f"{label} requires a NEW row column")
        return
    if type(value) is QualifiedColumnValue:
        raise ValueError(f"{label} contains a free qualified relation column")
    if type(value) is CanonicalJsonFieldValue:
        if value.document.source is not RowSource.NEW:
            raise ValueError(f"{label} requires a NEW canonical JSON document")
        return
    if type(value) is OrdinalOffsetValue:
        if value.value.source is not RowSource.NEW:
            raise ValueError(f"{label} requires a NEW ordinal")
        return
    if type(value) is RelatedCanonicalJsonArrayValueSpec:
        for binding in value.bindings:
            _ensure_insert_guard_scalar(binding.value, f"{label} related JSON binding")
        if value.ordinal is not None:
            _ensure_insert_guard_scalar(value.ordinal, f"{label} related JSON ordinal")
        return
    if type(value) is RelatedCanonicalJsonArrayLengthValueSpec:
        for binding in value.bindings:
            _ensure_insert_guard_scalar(
                binding.value,
                f"{label} related JSON length binding",
            )
        return
    raise TypeError(f"{label} is not a closed insert-guard scalar")


def _ensure_insert_guard_scope(value: PredicateExpression) -> None:
    """Reject every row source that is not owned by a typed relation scope."""

    _ensure_predicate(value)
    if type(value) is ComparisonExpression:
        _ensure_insert_guard_scalar(value.left, "insert-guard comparison")
        _ensure_insert_guard_scalar(value.right, "insert-guard comparison")
    elif type(value) is NullExpression:
        _ensure_insert_guard_scalar(value.value, "insert-guard null test")
    elif type(value) is MembershipExpression:
        _ensure_insert_guard_scalar(value.value, "insert-guard membership")
    elif type(value) is LogicalExpression:
        for operand in value.operands:
            _ensure_insert_guard_scope(operand)
    elif type(value) is NotExpression:
        _ensure_insert_guard_scope(value.operand)
    elif type(value) is CanonicalJsonPayloadHashExpression:
        _ensure_insert_guard_scalar(value.document, "canonical JSON payload hash")
        if type(value.expected_hash) is ColumnValue:
            _ensure_insert_guard_scalar(value.expected_hash, "canonical JSON payload hash")
        else:
            for binding in value.expected_hash.bindings:
                _ensure_insert_guard_scalar(
                    binding.value,
                    "canonical JSON expected-hash lookup binding",
                )
    elif type(value) is RelatedCanonicalJsonArrayContainsExpression:
        for binding in value.bindings:
            _ensure_insert_guard_scalar(binding.value, "related JSON membership binding")
        _ensure_insert_guard_scalar(value.element, "related JSON membership element")
    elif type(value) is RelationExistsExpression:
        for binding in value.bindings:
            _ensure_insert_guard_scalar(binding.value, "relation binding")
    elif type(value) is RelationAggregateExpression:
        for binding in value.bindings:
            _ensure_insert_guard_scalar(binding.value, "relation-aggregate binding")
        if type(value.expected) is ColumnValue:
            _ensure_insert_guard_scalar(value.expected, "relation-aggregate expected value")
        elif type(value.expected) is RelatedValueLookupSpec:
            for binding in value.expected.bindings:
                _ensure_insert_guard_scalar(
                    binding.value,
                    "relation-aggregate expected lookup binding",
                )
    elif type(value) is RelationJoinAggregateExpression:
        for binding in value.bindings:
            _ensure_insert_guard_scalar(binding.value, "join-aggregate binding")
        for join in value.joins:
            if join.predicate is not None:
                for column in _column_values(join.predicate):
                    _ensure_insert_guard_scalar(column, "join predicate local column")
        if type(value.expected) is ColumnValue:
            _ensure_insert_guard_scalar(value.expected, "join-aggregate expected value")
        elif type(value.expected) is RelatedValueLookupSpec:
            for binding in value.expected.bindings:
                _ensure_insert_guard_scalar(
                    binding.value,
                    "join-aggregate expected lookup binding",
                )
    elif type(value) is RelationArgExtremumExpression:
        for binding in value.bindings:
            _ensure_insert_guard_scalar(binding.value, "arg-extremum binding")
        for join in value.joins:
            if join.predicate is not None:
                for column in _column_values(join.predicate):
                    _ensure_insert_guard_scalar(column, "arg-extremum join local column")
        for expected in value.expected:
            _ensure_insert_guard_scalar(expected, "arg-extremum expected value")
    elif type(value) is RelationPolicyAggregateExpression:
        for binding in value.bindings:
            _ensure_insert_guard_scalar(binding.value, "policy-aggregate binding")
        _ensure_insert_guard_scalar(value.expected, "policy-aggregate expected value")
        for included in value.include_row:
            _ensure_insert_guard_scalar(included.value, "policy-aggregate included value")
        _ensure_insert_guard_scalar(value.declared_domain, "policy-aggregate declared domain")
        _ensure_insert_guard_scalar(
            value.include_latest_ordinal,
            "policy-aggregate current schedule ordinal",
        )
    elif type(value) is RelationLatestPerDomainPolicyExpression:
        for binding in value.bindings:
            _ensure_insert_guard_scalar(binding.value, "latest-policy binding")
        _ensure_insert_guard_scalar(
            value.expected_verdict,
            "latest-policy expected verdict",
        )
        _ensure_insert_guard_scalar(
            value.expected_ordered_record_hashes_hash,
            "latest-policy expected terminal hashes",
        )
        _ensure_insert_guard_scalar(
            value.declared_domain,
            "latest-policy declared domain",
        )
    elif type(value) is PrefixStreamExtensionExpression:
        for binding in (*value.previous_bindings, *value.declaration_bindings):
            _ensure_insert_guard_scalar(binding.value, "prefix-extension binding")
        _ensure_insert_guard_scalar(value.current_prefix_hash, "current prefix hash")
        _ensure_insert_guard_scalar(
            value.current_replication_count,
            "current replication count",
        )
        _ensure_insert_guard_scalar(
            value.current_period_count,
            "current period count",
        )
        _ensure_insert_guard_scalar(value.block_length, "current block length")
        _ensure_insert_guard_scalar(value.current_ordinal, "current schedule ordinal")
        _ensure_insert_guard_scalar(value.genesis_hash, "prefix genesis hash")
    elif type(value) is ChainPredecessorExpression:
        for binding in value.partition_bindings:
            _ensure_insert_guard_scalar(binding.value, "chain predecessor binding")
        _ensure_insert_guard_scalar(value.current_ordinal, "chain current ordinal")
        _ensure_insert_guard_scalar(value.current_previous_hash, "chain previous hash")
        _ensure_insert_guard_scalar(value.genesis_hash, "chain genesis hash")
    elif type(value) is RelationChainPrefixExpression:
        for binding in value.partition_bindings:
            _ensure_insert_guard_scalar(binding.value, "chain-prefix partition binding")
        for binding in value.ancestor_hash.bindings:
            _ensure_insert_guard_scalar(
                binding.value,
                "chain-prefix ancestor lookup binding",
            )
        _ensure_insert_guard_scalar(value.head_hash, "chain-prefix head hash")
    else:
        for binding in (*value.left_bindings, *value.right_bindings):
            _ensure_insert_guard_scalar(binding.value, "set-equality binding")
        for included in value.left_include_values:
            _ensure_insert_guard_scalar(included, "set-equality included value")


@_dataclass(frozen=True, slots=True)
class CheckConstraintSpec:
    name: str
    expression: CheckExpression

    def __post_init__(self) -> None:
        _ensure_identifier(self.name, "check identifier")
        _ensure_predicate(self.expression)
        if _contains_relation(self.expression):
            raise ValueError("table CHECK expressions cannot contain relation lookups")
        if _qualified_column_values(self.expression):
            raise ValueError("table CHECK expressions cannot contain qualified relation columns")
        if any(value.source is not RowSource.CURRENT for value in _column_values(self.expression)):
            raise ValueError("table CHECK expressions may reference CURRENT columns only")


class ForeignKeyAction(_StrEnum):
    NO_ACTION = "NO_ACTION"
    RESTRICT = "RESTRICT"
    CASCADE = "CASCADE"
    SET_NULL = "SET_NULL"
    SET_DEFAULT = "SET_DEFAULT"


class ForeignKeyDeferrability(_StrEnum):
    NOT_DEFERRABLE = "NOT_DEFERRABLE"
    DEFERRABLE_INITIALLY_IMMEDIATE = "DEFERRABLE_INITIALLY_IMMEDIATE"
    DEFERRABLE_INITIALLY_DEFERRED = "DEFERRABLE_INITIALLY_DEFERRED"


@_dataclass(frozen=True, slots=True)
class ForeignKeySpec:
    columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str, ...]
    on_update: ForeignKeyAction = ForeignKeyAction.NO_ACTION
    on_delete: ForeignKeyAction = ForeignKeyAction.NO_ACTION
    deferrability: ForeignKeyDeferrability = ForeignKeyDeferrability.NOT_DEFERRABLE

    def __post_init__(self) -> None:
        columns = _ensure_tuple(self.columns, "foreign-key columns", nonempty=True)
        referenced = _ensure_tuple(self.referenced_columns, "referenced columns", nonempty=True)
        for column in columns:
            _ensure_identifier(column, "foreign-key column identifier")
        for column in referenced:
            _ensure_identifier(column, "referenced column identifier")
        _ensure_unique(columns, "foreign-key columns")
        _ensure_unique(referenced, "referenced columns")
        if len(columns) != len(referenced):
            raise ValueError("foreign-key and referenced columns must have the same cardinality")
        _ensure_identifier(self.referenced_table, "referenced table identifier")
        _ensure_enum(self.on_update, ForeignKeyAction, "foreign-key update action")
        _ensure_enum(self.on_delete, ForeignKeyAction, "foreign-key delete action")
        _ensure_enum(
            self.deferrability,
            ForeignKeyDeferrability,
            "foreign-key deferrability",
        )


@_dataclass(frozen=True, slots=True)
class IndexColumnSpec:
    column: str
    direction: SortDirection = SortDirection.ASC
    collation: Collation = Collation.BINARY

    def __post_init__(self) -> None:
        _ensure_identifier(self.column, "index column identifier")
        _ensure_enum(self.direction, SortDirection, "index direction")
        _ensure_enum(self.collation, Collation, "index collation")


@_dataclass(frozen=True, slots=True)
class IndexSpec:
    name: str
    columns: tuple[IndexColumnSpec, ...]
    unique: bool = False
    predicate: CheckExpression | None = None

    def __post_init__(self) -> None:
        _ensure_identifier(self.name, "index identifier")
        columns = _ensure_tuple(self.columns, "index columns", nonempty=True)
        _ensure_exact_items(columns, IndexColumnSpec, "index columns")
        _ensure_unique(tuple(item.column for item in columns), "index columns")
        _ensure_bool(self.unique, "index unique")
        if self.predicate is not None:
            _ensure_predicate(self.predicate)
            if _contains_relation(self.predicate):
                raise ValueError("partial-index predicates cannot contain relation lookups")
            if _qualified_column_values(self.predicate):
                raise ValueError("partial-index predicates cannot contain qualified relation columns")
            if any(value.source is not RowSource.CURRENT for value in _column_values(self.predicate)):
                raise ValueError("partial-index predicates may reference CURRENT columns only")


class TriggerEvent(_StrEnum):
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class TriggerPolicyKind(_StrEnum):
    NO_UPDATE = "NO_UPDATE"
    NO_DELETE = "NO_DELETE"
    INSERT_GUARD = "INSERT_GUARD"


class TriggerTiming(_StrEnum):
    BEFORE = "BEFORE"


class TriggerEffect(_StrEnum):
    ABORT = "ABORT"


@_dataclass(frozen=True, slots=True)
class InsertStateTransition:
    previous_state: str
    next_state: str

    def __post_init__(self) -> None:
        _ensure_state(self.previous_state, "previous state")
        _ensure_state(self.next_state, "next state")
        if self.previous_state == self.next_state:
            raise ValueError("state transitions must advance to a different state")


@_dataclass(frozen=True, slots=True)
class InsertStateMachineSpec:
    state_column: str
    initial_states: tuple[str, ...]
    transitions: tuple[InsertStateTransition, ...]
    terminal_states: tuple[str, ...]
    sequence_column: str | None = None
    identity_columns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _ensure_identifier(self.state_column, "state column identifier")
        initial = _ensure_tuple(self.initial_states, "initial states", nonempty=True)
        transitions = _ensure_tuple(self.transitions, "state transitions")
        terminal = _ensure_tuple(self.terminal_states, "terminal states", nonempty=True)
        for state in (*initial, *terminal):
            _ensure_state(state, "state")
        _ensure_exact_items(transitions, InsertStateTransition, "state transitions")
        _ensure_unique(initial, "initial states")
        _ensure_unique(terminal, "terminal states")
        transition_pairs = tuple((item.previous_state, item.next_state) for item in transitions)
        _ensure_unique(transition_pairs, "state transitions")
        known_states = set(initial) | set(terminal)
        for transition in transitions:
            known_states.add(transition.previous_state)
            known_states.add(transition.next_state)
        if not set(terminal).issubset(known_states):
            raise ValueError("terminal states must belong to the state machine")
        if any(item.previous_state in terminal for item in transitions):
            raise ValueError("terminal states cannot have outgoing transitions")
        if self.sequence_column is not None:
            _ensure_identifier(self.sequence_column, "sequence column identifier")
        identity = _ensure_tuple(self.identity_columns, "state-machine identity columns")
        for column in identity:
            _ensure_identifier(column, "identity column identifier")
        _ensure_unique(identity, "state-machine identity columns")


@_dataclass(frozen=True, slots=True)
class TriggerPolicySpec:
    name: str
    kind: TriggerPolicyKind
    event: TriggerEvent
    error_code: str
    violation_when: PredicateExpression | None = None
    state_machine: InsertStateMachineSpec | None = None
    timing: TriggerTiming = TriggerTiming.BEFORE
    effect: TriggerEffect = TriggerEffect.ABORT

    def __post_init__(self) -> None:
        _ensure_identifier(self.name, "trigger identifier")
        _ensure_enum(self.kind, TriggerPolicyKind, "trigger policy kind")
        _ensure_enum(self.event, TriggerEvent, "trigger event")
        _ensure_state(self.error_code, "trigger error code")
        _ensure_enum(self.timing, TriggerTiming, "trigger timing")
        _ensure_enum(self.effect, TriggerEffect, "trigger effect")
        if self.kind is TriggerPolicyKind.NO_UPDATE:
            if self.event is not TriggerEvent.UPDATE:
                raise ValueError("NO_UPDATE policy must target UPDATE")
            if self.violation_when is not None or self.state_machine is not None:
                raise ValueError("NO_UPDATE policy is an unconditional rejection")
        elif self.kind is TriggerPolicyKind.NO_DELETE:
            if self.event is not TriggerEvent.DELETE:
                raise ValueError("NO_DELETE policy must target DELETE")
            if self.violation_when is not None or self.state_machine is not None:
                raise ValueError("NO_DELETE policy is an unconditional rejection")
        else:
            if self.event is not TriggerEvent.INSERT:
                raise ValueError("INSERT_GUARD policy must target INSERT")
            if self.violation_when is None:
                raise ValueError("INSERT_GUARD policy requires a typed violation predicate")
            _ensure_predicate(self.violation_when)
            _ensure_insert_guard_scope(self.violation_when)
            if self.state_machine is not None and type(self.state_machine) is not InsertStateMachineSpec:
                raise TypeError("state_machine must be InsertStateMachineSpec or None")


@_dataclass(frozen=True, slots=True)
class RowHashSpec:
    canonical_fields: tuple[str, ...]
    version_literal: str
    record_hash_column: str
    algorithm: RowHashAlgorithm = RowHashAlgorithm.SHA256
    encoding: CanonicalEncoding = CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1

    def __post_init__(self) -> None:
        fields = _ensure_tuple(self.canonical_fields, "canonical row-hash fields", nonempty=True)
        for field_name in fields:
            _ensure_identifier(field_name, "canonical field identifier")
        _ensure_unique(fields, "canonical row-hash fields")
        _ensure_state(self.version_literal, "row-hash version literal")
        _ensure_identifier(self.record_hash_column, "record-hash column identifier")
        if self.record_hash_column in fields:
            raise ValueError("record-hash output must be excluded from its canonical fields")
        _ensure_enum(self.algorithm, RowHashAlgorithm, "row-hash algorithm")
        _ensure_enum(self.encoding, CanonicalEncoding, "canonical encoding")


class ChainGenesisRule(_StrEnum):
    FAMILY_AUTHORITY_GENESIS_RECORD_HASH = "FAMILY_AUTHORITY_GENESIS_RECORD_HASH"


class ChainHeadRule(_StrEnum):
    LATEST_RECORD_HASH = "LATEST_RECORD_HASH"
    LATEST_TERMINAL_RECORD_HASH = "LATEST_TERMINAL_RECORD_HASH"


@_dataclass(frozen=True, slots=True)
class ChainSpec:
    partition_key_columns: tuple[str, ...]
    ordinal_column: str
    genesis_rule: ChainGenesisRule
    previous_hash_column: str
    record_hash_column: str
    terminal_head_rule: ChainHeadRule

    def __post_init__(self) -> None:
        partition = _ensure_tuple(self.partition_key_columns, "chain partition columns", nonempty=True)
        for column in partition:
            _ensure_identifier(column, "chain partition column identifier")
        _ensure_unique(partition, "chain partition columns")
        _ensure_identifier(self.ordinal_column, "chain ordinal column identifier")
        _ensure_enum(self.genesis_rule, ChainGenesisRule, "chain genesis rule")
        _ensure_identifier(self.previous_hash_column, "chain previous-hash column identifier")
        _ensure_identifier(self.record_hash_column, "chain record-hash column identifier")
        if self.previous_hash_column == self.record_hash_column:
            raise ValueError("chain previous-hash and record-hash columns must differ")
        _ensure_enum(self.terminal_head_rule, ChainHeadRule, "chain head rule")


class ExternalAuthorityReferenceKind(_StrEnum):
    OBSERVED_PIT = "OBSERVED_PIT"
    OOS_EXPOSURE = "OOS_EXPOSURE"
    CODE_MANIFEST = "CODE_MANIFEST"
    EXECUTION_ATTESTATION = "EXECUTION_ATTESTATION"
    IMMUTABLE_LEASE = "IMMUTABLE_LEASE"
    MULTI_PARTY_GRANT = "MULTI_PARTY_GRANT"
    ACTOR_PROVENANCE = "ACTOR_PROVENANCE"


@_dataclass(frozen=True, slots=True)
class ExternalFieldBindingSpec:
    """Exact local-column to provider-contract field-path binding."""

    local_column: str
    provider_field_path: str

    def __post_init__(self) -> None:
        _ensure_identifier(self.local_column, "external-reference local column")
        _ensure_provider_field_path(
            self.provider_field_path,
            "external-reference provider field path",
        )


@_dataclass(frozen=True, slots=True)
class ExternalProviderFieldEqualitySpec:
    """One exact equality between fields loaded from two verified providers."""

    left_verifier_code: str
    left_provider_field_path: str
    right_verifier_code: str
    right_provider_field_path: str

    def __post_init__(self) -> None:
        _ensure_token(self.left_verifier_code, "left external verifier code")
        _ensure_provider_field_path(self.left_provider_field_path, "left provider field path")
        _ensure_token(self.right_verifier_code, "right external verifier code")
        _ensure_provider_field_path(self.right_provider_field_path, "right provider field path")
        if self.left_verifier_code == self.right_verifier_code:
            raise ValueError("external continuity must compare distinct provider references")


class ExternalContinuitySemanticKind(_StrEnum):
    START_TERMINAL_STRICT_PROVIDER_PREFIX = (
        "START_TERMINAL_STRICT_PROVIDER_PREFIX"
    )
    START_LEASE_SUBJECT_TIME_WINDOW = "START_LEASE_SUBJECT_TIME_WINDOW"


class ExternalContinuityProviderInputSourceKind(_StrEnum):
    OUTER_REQUIRED = "OUTER_REQUIRED"
    BRANCH_ADDITIONAL = "BRANCH_ADDITIONAL"


@_dataclass(frozen=True, slots=True)
class ExternalContinuityProviderInputSpec:
    """Canonical alias binding to one verified external-provider result."""

    alias: str
    verifier_code: str
    source_kind: ExternalContinuityProviderInputSourceKind
    required_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        _ensure_identifier(self.alias, "external continuity provider-input alias")
        _ensure_token(self.verifier_code, "external continuity provider verifier code")
        _ensure_enum(
            self.source_kind,
            ExternalContinuityProviderInputSourceKind,
            "external continuity provider-input source kind",
        )
        fields = _ensure_tuple(
            self.required_fields,
            "external continuity provider-input fields",
            nonempty=True,
        )
        for field_name in fields:
            _ensure_identifier(field_name, "external continuity provider-input field")
        _ensure_unique(fields, "external continuity provider-input fields")
        expected_code_fragment = {
            "start": "START_EXECUTION_ATTESTATION",
            "terminal": "TERMINAL_EXECUTION_ATTESTATION",
            "lease": "IMMUTABLE_ARTIFACT_LEASE",
        }.get(self.alias)
        if expected_code_fragment is not None and expected_code_fragment not in (
            self.verifier_code
        ):
            raise ValueError(
                "external continuity provider alias must bind its exact verifier kind"
            )


class ExternalContinuityRowInputKind(_StrEnum):
    SUBJECT_ROW = "SUBJECT_ROW"
    EXACT_RELATED_ROW = "EXACT_RELATED_ROW"


@_dataclass(frozen=True, slots=True)
class ExternalContinuityReadBindingSpec:
    """One exact related-row key binding sourced from an earlier row input."""

    related_column: str
    source_path: str

    def __post_init__(self) -> None:
        _ensure_identifier(self.related_column, "external continuity related column")
        _ensure_provider_field_path(
            self.source_path,
            "external continuity related binding source",
        )
        if len(self.source_path.split(".")) != 2:
            raise ValueError(
                "external continuity related binding sources must be alias.field paths"
            )


@_dataclass(frozen=True, slots=True)
class ExternalContinuityRowInputSpec:
    """A closed subject or fail-closed exact related-row handler input."""

    alias: str
    source_kind: ExternalContinuityRowInputKind
    table: str
    bindings: tuple[ExternalContinuityReadBindingSpec, ...]
    required_fields: tuple[str, ...]
    required_verification_rule_ids: tuple[str, ...]
    cardinality: RelatedLookupCardinality | None = RelatedLookupCardinality.EXACTLY_ONE
    key_requirement: RelatedLookupKeyRequirement | None = (
        RelatedLookupKeyRequirement.PRIMARY_KEY_OR_EXACT_UNIQUE
    )

    def __post_init__(self) -> None:
        _ensure_identifier(self.alias, "external continuity row-input alias")
        _ensure_enum(
            self.source_kind,
            ExternalContinuityRowInputKind,
            "external continuity row-input source kind",
        )
        _ensure_identifier(self.table, "external continuity row-input table")
        bindings = _ensure_tuple(
            self.bindings,
            "external continuity row-input bindings",
        )
        _ensure_exact_items(
            bindings,
            ExternalContinuityReadBindingSpec,
            "external continuity row-input bindings",
        )
        _ensure_unique(
            tuple(binding.related_column for binding in bindings),
            "external continuity related binding columns",
        )
        fields = _ensure_tuple(
            self.required_fields,
            "external continuity required row fields",
            nonempty=True,
        )
        for field_name in fields:
            _ensure_identifier(field_name, "external continuity required row field")
        _ensure_unique(fields, "external continuity required row fields")
        rules = _ensure_tuple(
            self.required_verification_rule_ids,
            "external continuity required verification rules",
            nonempty=True,
        )
        for rule_id in rules:
            _ensure_identifier(rule_id, "external continuity verification rule id")
        _ensure_unique(rules, "external continuity required verification rules")

        if self.source_kind is ExternalContinuityRowInputKind.SUBJECT_ROW:
            if bindings:
                raise ValueError("subject row inputs cannot declare related bindings")
            if self.cardinality is not None or self.key_requirement is not None:
                raise ValueError(
                    "subject row inputs do not declare related-row cardinality or keys"
                )
            return

        if not bindings:
            raise ValueError("exact related row inputs require key bindings")
        if self.cardinality is not RelatedLookupCardinality.EXACTLY_ONE:
            raise ValueError("exact related row inputs require EXACTLY_ONE cardinality")
        if self.key_requirement is not (
            RelatedLookupKeyRequirement.PRIMARY_KEY_OR_EXACT_UNIQUE
        ):
            raise ValueError(
                "exact related row inputs require a primary key or exact UNIQUE key"
            )


@_dataclass(frozen=True, slots=True)
class ExternalContinuityTimeComparisonSpec:
    """One typed edge in a closed chronology path."""

    left_path: str
    operator: ComparisonOperator
    right_path: str

    def __post_init__(self) -> None:
        for path, label in (
            (self.left_path, "left"),
            (self.right_path, "right"),
        ):
            _ensure_provider_field_path(
                path,
                f"external continuity chronology {label} path",
            )
            if len(path.split(".")) != 2:
                raise ValueError(
                    "external continuity chronology paths must be alias.field paths"
                )
        _ensure_enum(
            self.operator,
            ComparisonOperator,
            "external continuity chronology operator",
        )
        if self.operator not in (ComparisonOperator.LT, ComparisonOperator.LE):
            raise ValueError("external continuity chronology permits only LT or LE")


class ExternalContinuityStatusBranchKind(_StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


@_dataclass(frozen=True, slots=True)
class ExternalContinuityBranchSelectorSpec:
    """Exact persisted closure/attempt status selector for one chronology branch."""

    closure_status_path: str
    closure_status: str
    attempt_status_path: str
    attempt_status: str

    def __post_init__(self) -> None:
        for path, label in (
            (self.closure_status_path, "closure"),
            (self.attempt_status_path, "attempt"),
        ):
            _ensure_provider_field_path(
                path,
                f"external continuity {label} status path",
            )
            if len(path.split(".")) != 2:
                raise ValueError(
                    "external continuity status selectors must be alias.field paths"
                )
        _ensure_state(self.closure_status, "external continuity closure status")
        _ensure_state(self.attempt_status, "external continuity attempt status")


@_dataclass(frozen=True, slots=True)
class ExternalContinuityChronologyBranchSpec:
    """Complete handler-input closure for one START/LEASE status branch."""

    branch_kind: ExternalContinuityStatusBranchKind
    selector: ExternalContinuityBranchSelectorSpec
    required_additional_external_verifier_codes: tuple[str, ...]
    row_inputs: tuple[ExternalContinuityRowInputSpec, ...]
    comparisons: tuple[ExternalContinuityTimeComparisonSpec, ...]
    fail_closed: bool = True
    default_pass: bool = False

    def __post_init__(self) -> None:
        _ensure_enum(
            self.branch_kind,
            ExternalContinuityStatusBranchKind,
            "external continuity chronology branch kind",
        )
        if type(self.selector) is not ExternalContinuityBranchSelectorSpec:
            raise TypeError("external continuity branch selector must be exact typed metadata")
        codes = _ensure_tuple(
            self.required_additional_external_verifier_codes,
            "external continuity additional verifier dependencies",
        )
        for code in codes:
            _ensure_token(code, "external continuity additional verifier code")
        _ensure_unique(codes, "external continuity additional verifier dependencies")
        row_inputs = _ensure_tuple(
            self.row_inputs,
            "external continuity chronology row inputs",
            nonempty=True,
        )
        _ensure_exact_items(
            row_inputs,
            ExternalContinuityRowInputSpec,
            "external continuity chronology row inputs",
        )
        aliases = tuple(row.alias for row in row_inputs)
        _ensure_unique(aliases, "external continuity chronology row-input aliases")
        rows_by_alias = {row.alias: row for row in row_inputs}
        binding_provider_fields = {
            "start": {"attestation_hash"},
            "terminal": {"attestation_hash"},
            "lease": {"lease_hash"},
        }
        prior_aliases: set[str] = set()
        for row in row_inputs:
            for binding in row.bindings:
                source_alias, source_field = binding.source_path.split(".")
                if source_alias in binding_provider_fields:
                    if source_field not in binding_provider_fields[source_alias]:
                        raise ValueError(
                            "external continuity related binding provider field is not closed"
                        )
                    continue
                if source_alias not in prior_aliases:
                    raise ValueError(
                        "external continuity related bindings must use an earlier row input"
                    )
                if source_field not in rows_by_alias[source_alias].required_fields:
                    raise ValueError(
                        "external continuity related binding sources must be declared required fields"
                    )
            prior_aliases.add(row.alias)
        comparisons = _ensure_tuple(
            self.comparisons,
            "external continuity chronology comparisons",
            nonempty=True,
        )
        _ensure_exact_items(
            comparisons,
            ExternalContinuityTimeComparisonSpec,
            "external continuity chronology comparisons",
        )
        _ensure_unique(comparisons, "external continuity chronology comparisons")
        _ensure_bool(self.fail_closed, "external continuity chronology fail closed")
        _ensure_bool(self.default_pass, "external continuity chronology default pass")
        if not self.fail_closed:
            raise ValueError("external continuity chronology branches must fail closed")
        if self.default_pass:
            raise ValueError("external continuity chronology branches forbid default pass")

        allowed_provider_fields = {
            "lease": {"acquired_at_us", "expires_at_us"},
            "start": {"issued_at_us"},
            "terminal": {"issued_at_us"},
        }
        used_paths = (
            self.selector.closure_status_path,
            self.selector.attempt_status_path,
            *(path for comparison in comparisons for path in (comparison.left_path, comparison.right_path)),
        )
        for path in used_paths:
            alias, field_name = path.split(".")
            if alias in allowed_provider_fields:
                if field_name not in allowed_provider_fields[alias]:
                    raise ValueError("external continuity provider input field is not closed")
                continue
            row = rows_by_alias.get(alias)
            if row is None:
                missing = "evaluation batch" if alias == "evaluation_batch" else alias
                raise ValueError(
                    "external continuity path must resolve to a declared handler input; "
                    f"missing {missing} input"
                )
            if field_name not in row.required_fields:
                raise ValueError(
                    "external continuity path must resolve to a declared required row field"
                )

        closure_alias = (
            "closure"
            if "closure" in rows_by_alias
            else "related_closure"
            if "related_closure" in rows_by_alias
            else None
        )
        if closure_alias is None:
            raise ValueError("external continuity chronology requires an explicit closure row")
        closure_row = rows_by_alias[closure_alias]
        if closure_row.table != "data_snooping_family_closures":
            raise ValueError("external continuity closure input must use the closure table")
        attempt_row = rows_by_alias.get("attempt")
        if attempt_row is None or attempt_row.table != "data_snooping_oos_access_attempts":
            raise ValueError("external continuity chronology requires the exact attempt row")
        if self.selector.closure_status_path != f"{closure_alias}.closure_status":
            raise ValueError(
                "external continuity selector must use the exact closure-row status"
            )
        if self.selector.attempt_status_path != "attempt.status":
            raise ValueError(
                "external continuity selector must use the exact attempt-row status"
            )
        closure_clock = f"{closure_alias}.closed_at_us"

        if self.branch_kind is ExternalContinuityStatusBranchKind.COMPLETE:
            if (
                self.selector.closure_status,
                self.selector.attempt_status,
            ) != ("COMPLETE", "COMPLETED"):
                raise ValueError(
                    "COMPLETE chronology requires closure_status COMPLETE and attempt_status COMPLETED"
                )
            if len(codes) != 1 or "TERMINAL_EXECUTION_ATTESTATION" not in codes[0]:
                raise ValueError(
                    "COMPLETE chronology requires the terminal execution-attestation verifier"
                )
            batch_row = rows_by_alias.get("evaluation_batch")
            if batch_row is None or batch_row.table != "data_snooping_evaluation_batches":
                raise ValueError("COMPLETE chronology requires the exact evaluation batch row")
            expected = (
                ("lease.acquired_at_us", ComparisonOperator.LE, "start.issued_at_us"),
                ("start.issued_at_us", ComparisonOperator.LT, "terminal.issued_at_us"),
                (
                    "terminal.issued_at_us",
                    ComparisonOperator.LE,
                    "evaluation_batch.committed_at_us",
                ),
                (
                    "evaluation_batch.committed_at_us",
                    ComparisonOperator.LE,
                    closure_clock,
                ),
                (closure_clock, ComparisonOperator.LE, "lease.expires_at_us"),
            )
            if tuple(
                (item.left_path, item.operator, item.right_path)
                for item in comparisons
            ) != expected:
                raise ValueError(
                    "COMPLETE chronology must contain exactly five comparisons over six nodes"
                )
            return

        if (
            self.selector.closure_status,
            self.selector.attempt_status,
        ) != ("NOT_ASSESSABLE", "INCOMPLETE"):
            raise ValueError(
                "INCOMPLETE chronology requires closure_status NOT_ASSESSABLE and attempt_status INCOMPLETE"
            )
        if codes:
            raise ValueError("INCOMPLETE chronology forbids terminal verifier dependencies")
        if "evaluation_batch" in rows_by_alias:
            raise ValueError("INCOMPLETE chronology forbids evaluation batch inputs")
        expected = (
            ("lease.acquired_at_us", ComparisonOperator.LE, "start.issued_at_us"),
            ("start.issued_at_us", ComparisonOperator.LE, closure_clock),
            (closure_clock, ComparisonOperator.LE, "lease.expires_at_us"),
        )
        if tuple(
            (item.left_path, item.operator, item.right_path) for item in comparisons
        ) != expected:
            raise ValueError(
                "INCOMPLETE chronology must contain exactly three comparisons over four nodes"
            )


@_dataclass(frozen=True, slots=True)
class ExternalContinuitySemanticProfileSpec:
    """Closed semantics not expressible as provider-field equalities alone."""

    semantic_kind: ExternalContinuitySemanticKind
    required_subject_context_fields: tuple[str, ...]
    required_subject_roles: tuple[tuple[str, str], ...]
    require_distinct_evidence_hashes: bool
    require_same_provider_authority: bool
    require_same_provider_genesis: bool
    require_right_projection_strict_extension: bool
    require_strict_issued_chronology: bool
    require_full_lease_time_window: bool
    strict_time_order_paths: tuple[str, ...]
    provider_inputs: tuple[ExternalContinuityProviderInputSpec, ...]
    chronology_branches: tuple[ExternalContinuityChronologyBranchSpec, ...] = ()
    fail_closed: bool = True
    default_pass: bool = False

    def __post_init__(self) -> None:
        _ensure_enum(
            self.semantic_kind,
            ExternalContinuitySemanticKind,
            "external continuity semantic kind",
        )
        context = _ensure_tuple(
            self.required_subject_context_fields,
            "external continuity subject context",
            nonempty=True,
        )
        for field_name in context:
            _ensure_identifier(field_name, "external continuity subject field")
        _ensure_unique(context, "external continuity subject context")
        if context != ("family_id", "attempt_id", "evaluation_batch_id"):
            raise ValueError(
                "external continuity must bind family, attempt, and evaluation batch"
            )
        roles = _ensure_tuple(
            self.required_subject_roles,
            "external continuity subject roles",
            nonempty=True,
        )
        for role in roles:
            if type(role) is not tuple or len(role) != 2:
                raise TypeError("external continuity subject roles must be exact pairs")
            alias, state = role
            _ensure_identifier(alias, "external continuity subject-role alias")
            _ensure_state(state, "external continuity subject role")
        _ensure_unique(roles, "external continuity subject roles")
        paths = _ensure_tuple(
            self.strict_time_order_paths,
            "external continuity strict time order",
        )
        for path in paths:
            _ensure_provider_field_path(path, "external continuity strict time path")
        _ensure_unique(paths, "external continuity strict time order")
        provider_inputs = _ensure_tuple(
            self.provider_inputs,
            "external continuity provider inputs",
            nonempty=True,
        )
        _ensure_exact_items(
            provider_inputs,
            ExternalContinuityProviderInputSpec,
            "external continuity provider inputs",
        )
        _ensure_unique(
            tuple(item.alias for item in provider_inputs),
            "external continuity provider-input aliases",
        )
        _ensure_unique(
            tuple(item.verifier_code for item in provider_inputs),
            "external continuity provider-input verifier codes",
        )
        providers_by_alias = {item.alias: item for item in provider_inputs}
        branches = _ensure_tuple(
            self.chronology_branches,
            "external continuity chronology branches",
        )
        _ensure_exact_items(
            branches,
            ExternalContinuityChronologyBranchSpec,
            "external continuity chronology branches",
        )
        _ensure_unique(
            tuple(branch.branch_kind for branch in branches),
            "external continuity chronology branch kinds",
        )
        for label, value in (
            ("distinct evidence hashes", self.require_distinct_evidence_hashes),
            ("same provider authority", self.require_same_provider_authority),
            ("same provider genesis", self.require_same_provider_genesis),
            (
                "right projection strict extension",
                self.require_right_projection_strict_extension,
            ),
            ("strict issued chronology", self.require_strict_issued_chronology),
            ("full lease time window", self.require_full_lease_time_window),
            ("fail closed", self.fail_closed),
            ("default pass", self.default_pass),
        ):
            _ensure_bool(value, f"external continuity {label}")
        if not self.fail_closed:
            raise ValueError("external continuity semantic profiles must fail closed")
        if self.default_pass:
            raise ValueError("external continuity semantic profiles forbid default pass")

        if self.semantic_kind is (
            ExternalContinuitySemanticKind.START_TERMINAL_STRICT_PROVIDER_PREFIX
        ):
            if roles != (("left", "START"), ("right", "TERMINAL")):
                raise ValueError(
                    "start-terminal continuity requires exact START and TERMINAL roles"
                )
            if not self.require_distinct_evidence_hashes:
                raise ValueError("start-terminal continuity requires distinct evidence hashes")
            if not self.require_same_provider_authority:
                raise ValueError("start-terminal continuity requires the same provider authority")
            if not self.require_same_provider_genesis:
                raise ValueError("start-terminal continuity requires the same provider genesis")
            if not self.require_right_projection_strict_extension:
                raise ValueError("start-terminal continuity requires a strict extension")
            if not self.require_strict_issued_chronology:
                raise ValueError("start-terminal continuity requires strict issued chronology")
            if self.require_full_lease_time_window:
                raise ValueError("start-terminal continuity does not declare a lease time window")
            if paths != ("start.issued_at_us", "terminal.issued_at_us"):
                raise ValueError(
                    "start-terminal continuity requires exact issued chronology paths"
                )
            if branches:
                raise ValueError("start-terminal continuity does not use status branches")
            if tuple(
                (item.alias, item.source_kind, item.required_fields)
                for item in provider_inputs
            ) != (
                (
                    "start",
                    ExternalContinuityProviderInputSourceKind.OUTER_REQUIRED,
                    ("attestation_hash", "issued_at_us"),
                ),
                (
                    "terminal",
                    ExternalContinuityProviderInputSourceKind.OUTER_REQUIRED,
                    ("attestation_hash", "issued_at_us"),
                ),
            ):
                raise ValueError(
                    "start-terminal continuity requires exact start/terminal provider inputs"
                )
            return

        if roles != (("left", "START"),):
            raise ValueError("start-lease continuity requires the exact START role")
        if self.require_distinct_evidence_hashes:
            raise ValueError("start-lease continuity declares no distinct hash rule")
        if self.require_same_provider_authority:
            raise ValueError("start-lease continuity uses a distinct lease authority")
        if self.require_same_provider_genesis:
            raise ValueError("start-lease continuity uses a distinct lease genesis")
        if self.require_right_projection_strict_extension:
            raise ValueError("start-lease continuity declares no strict extension")
        if self.require_strict_issued_chronology:
            raise ValueError("start-lease continuity uses a full time window, not issued chronology")
        if not self.require_full_lease_time_window:
            raise ValueError("start-lease continuity requires the full lease time window")
        if paths:
            raise ValueError(
                "start-lease continuity chronology must be carried by typed status branches"
            )
        if tuple(branch.branch_kind for branch in branches) != (
            ExternalContinuityStatusBranchKind.COMPLETE,
            ExternalContinuityStatusBranchKind.INCOMPLETE,
        ):
            raise ValueError(
                "start-lease continuity requires exact COMPLETE then INCOMPLETE branches"
            )
        if tuple(
            (item.alias, item.source_kind, item.required_fields)
            for item in provider_inputs
        ) != (
            (
                "start",
                ExternalContinuityProviderInputSourceKind.OUTER_REQUIRED,
                ("attestation_hash", "issued_at_us"),
            ),
            (
                "lease",
                ExternalContinuityProviderInputSourceKind.OUTER_REQUIRED,
                ("lease_hash", "acquired_at_us", "expires_at_us"),
            ),
            (
                "terminal",
                ExternalContinuityProviderInputSourceKind.BRANCH_ADDITIONAL,
                ("attestation_hash", "issued_at_us"),
            ),
        ):
            raise ValueError(
                "start-lease continuity requires exact start/lease/terminal provider inputs"
            )
        complete, incomplete = branches
        if complete.required_additional_external_verifier_codes != (
            providers_by_alias["terminal"].verifier_code,
        ):
            raise ValueError(
                "COMPLETE chronology terminal alias must bind its additional verifier result"
            )
        if incomplete.required_additional_external_verifier_codes:
            raise ValueError("INCOMPLETE chronology cannot bind a terminal provider")
        for branch in branches:
            for row in branch.row_inputs:
                for binding in row.bindings:
                    alias, field_name = binding.source_path.split(".")
                    if alias in ("start", "lease", "terminal") and (
                        alias not in providers_by_alias
                        or field_name not in providers_by_alias[alias].required_fields
                    ):
                        raise ValueError(
                            "external continuity related binding must resolve to a bound provider result"
                        )
            for comparison in branch.comparisons:
                for path in (comparison.left_path, comparison.right_path):
                    alias, field_name = path.split(".")
                    if alias in ("start", "lease", "terminal") and (
                        alias not in providers_by_alias
                        or field_name not in providers_by_alias[alias].required_fields
                    ):
                        raise ValueError(
                            "external continuity chronology must resolve to a bound provider result"
                        )


@_dataclass(frozen=True, slots=True)
class ExternalAuthorityContinuitySpec:
    """Closed cross-provider identity/content continuity for one table extension."""

    verifier_code: str
    required_external_verifier_codes: tuple[str, ...]
    provider_field_equalities: tuple[ExternalProviderFieldEqualitySpec, ...]
    reference_presence_column: str | None
    semantic_profile: ExternalContinuitySemanticProfileSpec | None = None

    def __post_init__(self) -> None:
        _ensure_token(self.verifier_code, "external continuity verifier code")
        codes = _ensure_tuple(
            self.required_external_verifier_codes,
            "external continuity verifier dependencies",
            nonempty=True,
        )
        if len(codes) < 2:
            raise ValueError("external continuity requires at least two provider references")
        for code in codes:
            _ensure_token(code, "external continuity dependency code")
        _ensure_unique(codes, "external continuity verifier dependencies")
        equalities = _ensure_tuple(
            self.provider_field_equalities,
            "external continuity field equalities",
            nonempty=True,
        )
        _ensure_exact_items(
            equalities,
            ExternalProviderFieldEqualitySpec,
            "external continuity field equalities",
        )
        _ensure_unique(equalities, "external continuity field equalities")
        referenced_codes = {
            code for equality in equalities for code in (equality.left_verifier_code, equality.right_verifier_code)
        }
        if referenced_codes != set(codes):
            raise ValueError(
                "external continuity equalities must use every required provider reference exactly by code"
            )
        if self.reference_presence_column is not None:
            _ensure_identifier(
                self.reference_presence_column,
                "external continuity presence column",
            )
        if self.semantic_profile is not None and type(self.semantic_profile) is not (
            ExternalContinuitySemanticProfileSpec
        ):
            raise TypeError(
                "external continuity semantic_profile must be exact typed metadata"
            )
        if self.semantic_profile is not None:
            outer_codes = tuple(
                provider.verifier_code
                for provider in self.semantic_profile.provider_inputs
                if provider.source_kind
                is ExternalContinuityProviderInputSourceKind.OUTER_REQUIRED
            )
            if outer_codes != codes:
                raise ValueError(
                    "external continuity outer provider aliases must map exactly to required verifier codes"
                )
            additional_codes = tuple(
                provider.verifier_code
                for provider in self.semantic_profile.provider_inputs
                if provider.source_kind
                is ExternalContinuityProviderInputSourceKind.BRANCH_ADDITIONAL
            )
            declared_additional = tuple(
                dict.fromkeys(
                    code
                    for branch in self.semantic_profile.chronology_branches
                    for code in branch.required_additional_external_verifier_codes
                )
            )
            if additional_codes != declared_additional:
                raise ValueError(
                    "external continuity branch provider aliases must map exactly to additional verifier codes"
                )
            if set(outer_codes) & set(additional_codes):
                raise ValueError(
                    "external continuity outer and branch provider results must be distinct"
                )


class ExternalProviderResolutionStage(_StrEnum):
    C8_PASS_M = "C8_PASS_M"


@_dataclass(frozen=True, slots=True)
class ExternalProviderCapabilityRequirementSpec:
    """Provider contract/capability that the later Pass-M registry must resolve."""

    provider_contract_type: str
    provider_capability: str
    resolution_stage: ExternalProviderResolutionStage

    def __post_init__(self) -> None:
        _ensure_token(self.provider_contract_type, "external provider contract type")
        _ensure_token(self.provider_capability, "external provider capability")
        _ensure_enum(
            self.resolution_stage,
            ExternalProviderResolutionStage,
            "external provider resolution stage",
        )


class ExternalProviderClaimOperator(_StrEnum):
    EQUALS = "EQUALS"
    AT_LEAST = "AT_LEAST"
    IS_TRUE = "IS_TRUE"


@_dataclass(frozen=True, slots=True)
class ExternalProviderSemanticClaimSpec:
    """One typed, fail-closed semantic claim returned by a verified provider."""

    claim_id: str
    provider_field_path: str
    operator: ExternalProviderClaimOperator
    expected_value: bool | int | str

    def __post_init__(self) -> None:
        _ensure_identifier(self.claim_id, "external provider claim ID")
        _ensure_provider_field_path(
            self.provider_field_path,
            "external provider claim field path",
        )
        _ensure_enum(
            self.operator,
            ExternalProviderClaimOperator,
            "external provider claim operator",
        )
        if self.operator is ExternalProviderClaimOperator.IS_TRUE:
            if self.expected_value is not True:
                raise ValueError("IS_TRUE provider claims must require literal true")
        elif self.operator is ExternalProviderClaimOperator.AT_LEAST:
            if type(self.expected_value) is not int or self.expected_value < 1:
                raise ValueError("AT_LEAST provider claims require a positive integer")
        elif type(self.expected_value) not in (bool, int, str):
            raise TypeError("EQUALS provider claims require a closed scalar literal")
        if type(self.expected_value) is str and not self.expected_value:
            raise ValueError("provider claim string literals must be non-empty")


@_dataclass(frozen=True, slots=True)
class ExternalAuthorityClaimProfileSpec:
    """Required provider-result semantics for one external authority capability."""

    provider_contract_type: str
    provider_capability: str
    external_reference_kind: ExternalAuthorityReferenceKind
    required_binding_columns: tuple[str, ...]
    required_claims: tuple[ExternalProviderSemanticClaimSpec, ...]
    fail_closed: bool = True
    default_pass: bool = False

    def __post_init__(self) -> None:
        _ensure_token(self.provider_contract_type, "claim-profile provider contract")
        _ensure_token(self.provider_capability, "claim-profile provider capability")
        _ensure_enum(
            self.external_reference_kind,
            ExternalAuthorityReferenceKind,
            "claim-profile external reference kind",
        )
        columns = _ensure_tuple(
            self.required_binding_columns,
            "claim-profile binding columns",
            nonempty=True,
        )
        for column in columns:
            _ensure_identifier(column, "claim-profile binding column")
        _ensure_unique(columns, "claim-profile binding columns")
        claims = _ensure_tuple(
            self.required_claims,
            "claim-profile semantic claims",
            nonempty=True,
        )
        _ensure_exact_items(
            claims,
            ExternalProviderSemanticClaimSpec,
            "claim-profile semantic claims",
        )
        _ensure_unique(
            tuple(claim.claim_id for claim in claims),
            "claim-profile semantic claim IDs",
        )
        _ensure_unique(
            tuple(claim.provider_field_path for claim in claims),
            "claim-profile semantic claim field paths",
        )
        _ensure_bool(self.fail_closed, "claim-profile fail-closed flag")
        _ensure_bool(self.default_pass, "claim-profile default-pass flag")
        if not self.fail_closed or self.default_pass:
            raise ValueError("external authority claim profiles must fail closed")


class AuxiliarySemanticRecipeKind(_StrEnum):
    ACTION_POLICY = "ACTION_POLICY"
    IDENTITY_DERIVATION = "IDENTITY_DERIVATION"
    CANONICAL_CONTENT_HASH = "CANONICAL_CONTENT_HASH"


@_dataclass(frozen=True, slots=True)
class ActionPolicySemanticRecipeSpec:
    action: str
    subject_table: str
    grant_required: bool

    def __post_init__(self) -> None:
        _ensure_state(self.action, "action-policy action")
        _ensure_identifier(self.subject_table, "action-policy subject table")
        _ensure_bool(self.grant_required, "action-policy grant-required flag")

    @property
    def recipe_id(self) -> str:
        return f"c4.action_policy.{self.action.lower()}.v1"

    @property
    def recipe_kind(self) -> AuxiliarySemanticRecipeKind:
        return AuxiliarySemanticRecipeKind.ACTION_POLICY

    @property
    def table_name(self) -> str:
        return "research_authority_events"

    @property
    def columns(self) -> tuple[str, ...]:
        return (
            "action",
            "subject_record_type",
            "grant_id",
            "grant_record_hash",
            "grant_policy_id",
            "grant_policy_version",
            "grant_policy_hash",
            "grant_authority_id",
            "grant_authority_head",
        )

    @property
    def referenced_tables(self) -> tuple[str, ...]:
        return (self.subject_table,)

    @property
    def required_rule_ids(self) -> tuple[str, ...]:
        return (
            "research_authority_events_action_subject_match",
            "research_authority_events_grant_policy",
        )

    @property
    def required_verifier_codes(self) -> tuple[str, ...]:
        return ("c4.verify_research_authority_events_grant_policy_v1",)


class IdentityIssuance(_StrEnum):
    AUTHORITY_DERIVED = "AUTHORITY_DERIVED"


class IdentityDerivationPayloadShape(_StrEnum):
    DOMAIN_SEPARATED_PREDECESSOR_ORDERED_FIELD_VALUES_V1 = (
        "DOMAIN_SEPARATED_PREDECESSOR_ORDERED_FIELD_VALUES_V1"
    )


class IdentityPredecessorSourceKind(_StrEnum):
    LOCAL_COLUMN = "LOCAL_COLUMN"
    EXACT_AUTHORITY_EVENT_LOOKUP = "EXACT_AUTHORITY_EVENT_LOOKUP"


@_dataclass(frozen=True, slots=True)
class IdentityPredecessorSourceSpec:
    kind: IdentityPredecessorSourceKind
    local_column: str | None = None
    binding_field: str | None = None
    source_table: str | None = None
    source_key_field: str | None = None
    source_value_field: str | None = None

    def __post_init__(self) -> None:
        _ensure_enum(
            self.kind,
            IdentityPredecessorSourceKind,
            "identity predecessor source kind",
        )
        relation_fields = (
            self.binding_field,
            self.source_table,
            self.source_key_field,
            self.source_value_field,
        )
        if self.kind is IdentityPredecessorSourceKind.LOCAL_COLUMN:
            if self.local_column != "prev_record_hash" or any(
                value is not None for value in relation_fields
            ):
                raise ValueError(
                    "local identity predecessor must use only prev_record_hash"
                )
            return
        if self.local_column is not None or relation_fields != (
            "authority_event_id",
            "research_authority_events",
            "event_id",
            "prev_record_hash",
        ):
            raise ValueError(
                "subject identity predecessor must exactly lookup authority event prev_record_hash"
            )


class IdentityDigestRepresentation(_StrEnum):
    LOWERCASE_HEX_64 = "LOWERCASE_HEX_64"


@_dataclass(frozen=True, slots=True)
class IdentityDerivationGoldenVectorSpec:
    predecessor_record_hash: str
    source_values: tuple[str | int | None, ...]
    expected_sha256: str

    def __post_init__(self) -> None:
        _ensure_sha256(
            self.predecessor_record_hash,
            "golden identity predecessor hash",
        )
        _ensure_sha256(self.expected_sha256, "golden identity digest")
        values = _ensure_tuple(
            self.source_values,
            "identity golden source values",
            nonempty=True,
        )
        if any(
            value is not None and type(value) not in (str, int)
            for value in values
        ):
            raise TypeError("identity golden source values must be canonical scalars")


@_dataclass(frozen=True, slots=True)
class IdentityDerivationSemanticRecipeSpec:
    table_name: str
    field_name: str
    issuance: IdentityIssuance
    verification_rule_id: str
    domain_separator: str
    source_fields: tuple[str, ...]
    predecessor_source: IdentityPredecessorSourceSpec
    golden_vector: IdentityDerivationGoldenVectorSpec
    payload_shape: IdentityDerivationPayloadShape = (
        IdentityDerivationPayloadShape.DOMAIN_SEPARATED_PREDECESSOR_ORDERED_FIELD_VALUES_V1
    )
    payload_schema_version: str = "QUANTOS_AUTHORITY_DERIVED_ID_PAYLOAD_V1"
    payload_schema_version_field: str = "schema_version"
    domain_separator_field: str = "domain_separator"
    predecessor_field: str = "authority_chain_predecessor"
    ordered_fields_field: str = "ordered_source_fields"
    ordered_field_name_field: str = "field_name"
    ordered_field_value_field: str = "field_value"
    encoding: CanonicalEncoding = CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1
    algorithm: RowHashAlgorithm = RowHashAlgorithm.SHA256
    digest_representation: IdentityDigestRepresentation = (
        IdentityDigestRepresentation.LOWERCASE_HEX_64
    )

    def __post_init__(self) -> None:
        _ensure_identifier(self.table_name, "identity recipe table")
        _ensure_identifier(self.field_name, "identity recipe field")
        _ensure_enum(self.issuance, IdentityIssuance, "identity issuance")
        _ensure_identifier(
            self.verification_rule_id,
            "identity verification rule",
        )
        _ensure_token(self.domain_separator, "identity domain separator")
        source_fields = _ensure_tuple(
            self.source_fields,
            "identity source fields",
            nonempty=True,
        )
        for field in source_fields:
            _ensure_identifier(field, "identity source field")
        _ensure_unique(source_fields, "identity source fields")
        if self.field_name in source_fields:
            raise ValueError("identity derivation cannot be self-referential")
        if type(self.predecessor_source) is not IdentityPredecessorSourceSpec:
            raise TypeError("identity predecessor source must be exact metadata")
        if type(self.golden_vector) is not IdentityDerivationGoldenVectorSpec:
            raise TypeError("identity derivation requires one typed golden vector")
        if len(self.golden_vector.source_values) != len(source_fields):
            raise ValueError("identity golden values must match ordered source fields")
        if (
            self.payload_shape
            is not IdentityDerivationPayloadShape.DOMAIN_SEPARATED_PREDECESSOR_ORDERED_FIELD_VALUES_V1
        ):
            raise ValueError("identity derivation payload shape is fixed")
        if (
            self.payload_schema_version,
            self.payload_schema_version_field,
            self.domain_separator_field,
            self.predecessor_field,
            self.ordered_fields_field,
            self.ordered_field_name_field,
            self.ordered_field_value_field,
        ) != (
            "QUANTOS_AUTHORITY_DERIVED_ID_PAYLOAD_V1",
            "schema_version",
            "domain_separator",
            "authority_chain_predecessor",
            "ordered_source_fields",
            "field_name",
            "field_value",
        ):
            raise ValueError("identity canonical JSON field recipe is fixed")
        if self.encoding is not CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1:
            raise ValueError("identity derivation canonical encoding is fixed")
        if self.algorithm is not RowHashAlgorithm.SHA256:
            raise ValueError("identity derivation algorithm is fixed")
        if self.digest_representation is not IdentityDigestRepresentation.LOWERCASE_HEX_64:
            raise ValueError("identity digest representation is fixed")

    @property
    def recipe_id(self) -> str:
        return f"c4.identity.{self.table_name}.{self.field_name}.v1"

    @property
    def recipe_kind(self) -> AuxiliarySemanticRecipeKind:
        return AuxiliarySemanticRecipeKind.IDENTITY_DERIVATION

    @property
    def columns(self) -> tuple[str, ...]:
        predecessor_column = (
            self.predecessor_source.local_column
            or self.predecessor_source.binding_field
        )
        if predecessor_column is None:
            raise ValueError("identity predecessor source has no readable column")
        return tuple(
            dict.fromkeys(
                (self.field_name, *self.source_fields, predecessor_column)
            )
        )

    @property
    def referenced_tables(self) -> tuple[str, ...]:
        source_table = self.predecessor_source.source_table
        return () if source_table is None else (source_table,)

    @property
    def required_rule_ids(self) -> tuple[str, ...]:
        return (self.verification_rule_id,)

    @property
    def required_verifier_codes(self) -> tuple[str, ...]:
        return (f"c4.verify_{self.verification_rule_id}_v1",)


class CanonicalContentSourceKind(_StrEnum):
    CANONICAL_JSON_DOCUMENT = "CANONICAL_JSON_DOCUMENT"
    CANONICAL_JSON_SINGLE_FIELD_OBJECT = "CANONICAL_JSON_SINGLE_FIELD_OBJECT"


@_dataclass(frozen=True, slots=True)
class CanonicalContentHashGoldenVectorSpec:
    source_value: str
    expected_sha256: str

    def __post_init__(self) -> None:
        if type(self.source_value) is not str or not self.source_value:
            raise ValueError("content-hash golden source must be non-empty text")
        _ensure_sha256(self.expected_sha256, "content-hash golden digest")


@_dataclass(frozen=True, slots=True)
class CanonicalContentHashSemanticRecipeSpec:
    table_name: str
    source_field: str
    hash_field: str
    source_kind: CanonicalContentSourceKind
    verification_rule_id: str
    object_field_name: str | None = None
    golden_vector: CanonicalContentHashGoldenVectorSpec | None = None
    encoding: CanonicalEncoding = CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1
    algorithm: RowHashAlgorithm = RowHashAlgorithm.SHA256

    def __post_init__(self) -> None:
        _ensure_identifier(self.table_name, "content-hash recipe table")
        _ensure_identifier(self.source_field, "content-hash source field")
        _ensure_identifier(self.hash_field, "content-hash target field")
        _ensure_identifier(
            self.verification_rule_id,
            "content-hash verification rule",
        )
        if self.source_field == self.hash_field:
            raise ValueError("content hash source and target fields must differ")
        _ensure_enum(
            self.source_kind,
            CanonicalContentSourceKind,
            "content hash source kind",
        )
        if self.source_kind is CanonicalContentSourceKind.CANONICAL_JSON_DOCUMENT:
            if self.object_field_name is not None or self.golden_vector is not None:
                raise ValueError("canonical JSON documents forbid scalar wrapper metadata")
        else:
            if self.object_field_name != self.source_field:
                raise ValueError(
                    "single-field content hashes must wrap the source under its exact field name"
                )
            if type(self.golden_vector) is not CanonicalContentHashGoldenVectorSpec:
                raise TypeError("single-field content hashes require a typed golden vector")
        if self.encoding is not CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1:
            raise ValueError("content hash canonical encoding is fixed")
        if self.algorithm is not RowHashAlgorithm.SHA256:
            raise ValueError("content hash algorithm is fixed")

    @property
    def recipe_id(self) -> str:
        return f"c4.content_hash.{self.table_name}.{self.hash_field}.v1"

    @property
    def recipe_kind(self) -> AuxiliarySemanticRecipeKind:
        return AuxiliarySemanticRecipeKind.CANONICAL_CONTENT_HASH

    @property
    def columns(self) -> tuple[str, ...]:
        return (self.source_field, self.hash_field)

    @property
    def referenced_tables(self) -> tuple[str, ...]:
        return ()

    @property
    def required_rule_ids(self) -> tuple[str, ...]:
        return (self.verification_rule_id,)

    @property
    def required_verifier_codes(self) -> tuple[str, ...]:
        if self.source_kind is CanonicalContentSourceKind.CANONICAL_JSON_DOCUMENT:
            return ()
        return (f"c4.verify_{self.verification_rule_id}_v1",)


AuxiliarySemanticRecipeSpec: _TypeAlias = (
    ActionPolicySemanticRecipeSpec
    | IdentityDerivationSemanticRecipeSpec
    | CanonicalContentHashSemanticRecipeSpec
)
_AUXILIARY_SEMANTIC_RECIPE_TYPES = (
    ActionPolicySemanticRecipeSpec,
    IdentityDerivationSemanticRecipeSpec,
    CanonicalContentHashSemanticRecipeSpec,
)


class VerificationRuleKind(_StrEnum):
    ROW_HASH = "ROW_HASH"
    ORDERED_CHAIN = "ORDERED_CHAIN"
    FOREIGN_KEY = "FOREIGN_KEY"
    CROSS_TABLE_REFERENCE = "CROSS_TABLE_REFERENCE"
    CROSS_TABLE_TOTALITY = "CROSS_TABLE_TOTALITY"
    STATE_BRANCH = "STATE_BRANCH"
    TERMINAL_HEAD = "TERMINAL_HEAD"
    GENESIS_BINDING = "GENESIS_BINDING"
    SELECTION_SCOPE = "SELECTION_SCOPE"
    EXPOSURE_CONSUMPTION = "EXPOSURE_CONSUMPTION"
    ATTEMPT_BINDING = "ATTEMPT_BINDING"
    FREEZE_CLOSURE_ASSESSMENT = "FREEZE_CLOSURE_ASSESSMENT"
    PUBLICATION_TOTALITY = "PUBLICATION_TOTALITY"
    CONTENT_BINDING = "CONTENT_BINDING"
    EXTERNAL_AUTHORITY_REFERENCE = "EXTERNAL_AUTHORITY_REFERENCE"


class StructuralVerificationSourceKind(_StrEnum):
    ROW_HASH_SPEC = "ROW_HASH_SPEC"
    ALL_FOREIGN_KEYS = "ALL_FOREIGN_KEYS"
    GENESIS_FOREIGN_KEY = "GENESIS_FOREIGN_KEY"
    CHAIN_SPEC = "CHAIN_SPEC"
    TERMINAL_HEAD = "TERMINAL_HEAD"
    PRIMARY_KEY = "PRIMARY_KEY"
    UNIQUE_INDEX = "UNIQUE_INDEX"
    CHECK_CONSTRAINT = "CHECK_CONSTRAINT"
    TRIGGER_POLICY = "TRIGGER_POLICY"
    EXACT_FOREIGN_KEY = "EXACT_FOREIGN_KEY"
    DELEGATED_EXTENSION = "DELEGATED_EXTENSION"


@_dataclass(frozen=True, slots=True)
class StructuralVerificationSourceSpec:
    """Closed pointer from a read obligation to one exact composed-schema source."""

    kind: StructuralVerificationSourceKind
    source_id: str | None = None
    source_table: str | None = None
    columns: tuple[str, ...] = ()
    referenced_table: str | None = None
    referenced_columns: tuple[str, ...] = ()
    required_rule_ids: tuple[str, ...] = ()
    required_verifier_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _ensure_enum(
            self.kind,
            StructuralVerificationSourceKind,
            "structural verification source kind",
        )
        columns = _ensure_tuple(self.columns, "structural source columns")
        referenced_columns = _ensure_tuple(
            self.referenced_columns,
            "structural source referenced columns",
        )
        required_rule_ids = _ensure_tuple(
            self.required_rule_ids,
            "structural source required rule IDs",
        )
        required_verifier_codes = _ensure_tuple(
            self.required_verifier_codes,
            "structural source required verifier codes",
        )
        for column in (*columns, *referenced_columns):
            _ensure_identifier(column, "structural source column")
        for rule_id in required_rule_ids:
            _ensure_identifier(rule_id, "structural source required rule ID")
        for verifier_code in required_verifier_codes:
            _ensure_token(verifier_code, "structural source required verifier code")
        if self.source_table is not None:
            _ensure_identifier(
                self.source_table,
                "structural verification source table",
            )
            if self.kind is not StructuralVerificationSourceKind.TRIGGER_POLICY:
                raise ValueError(
                    "only a remote trigger policy may name a structural source table"
                )
            if columns:
                raise ValueError(
                    "remote trigger structural sources derive their read columns from the trigger AST"
                )
        _ensure_unique(columns, "structural source columns")
        _ensure_unique(referenced_columns, "structural source referenced columns")
        _ensure_unique(required_rule_ids, "structural source required rule IDs")
        _ensure_unique(
            required_verifier_codes,
            "structural source required verifier codes",
        )

        named_sources = {
            StructuralVerificationSourceKind.UNIQUE_INDEX,
            StructuralVerificationSourceKind.CHECK_CONSTRAINT,
            StructuralVerificationSourceKind.TRIGGER_POLICY,
            StructuralVerificationSourceKind.DELEGATED_EXTENSION,
        }
        if self.kind in named_sources:
            if self.source_id is None:
                raise ValueError("named structural verification sources require source_id")
            _ensure_token(self.source_id, "structural verification source ID")
        elif self.source_id is not None:
            raise ValueError("table-derived structural verification sources forbid source_id")

        if self.kind is StructuralVerificationSourceKind.EXACT_FOREIGN_KEY:
            if not columns or not referenced_columns or self.referenced_table is None:
                raise ValueError("exact foreign-key sources require both exact column tuples")
            _ensure_identifier(
                self.referenced_table,
                "structural source referenced table",
            )
            if len(columns) != len(referenced_columns):
                raise ValueError("exact foreign-key source column arity must match")
        elif self.referenced_table is not None or referenced_columns:
            raise ValueError("only exact foreign-key sources may name a parent table")

        if self.kind is StructuralVerificationSourceKind.DELEGATED_EXTENSION:
            if not required_rule_ids and not required_verifier_codes:
                raise ValueError(
                    "delegated extension sources require exact rule or verifier dependencies"
                )
        elif required_rule_ids or required_verifier_codes:
            raise ValueError(
                "only delegated extension sources may name verifier dependencies"
            )


@_dataclass(frozen=True, slots=True)
class VerificationRuleSpec:
    rule_id: str
    kind: VerificationRuleKind
    columns: tuple[str, ...] = ()
    referenced_table: str | None = None
    external_reference_kind: ExternalAuthorityReferenceKind | None = None
    provider_contract_type: str | None = None
    provider_capability: str | None = None
    provider_component_id: str | None = None
    external_bindings: tuple[ExternalFieldBindingSpec, ...] = ()
    nullable_reference: bool = False
    reference_presence_column: str | None = None
    predicate: PredicateExpression | None = None
    verifier_code: str | None = None
    structural_source: StructuralVerificationSourceSpec | None = None
    required_rule_ids: tuple[str, ...] = ()
    required_verifier_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _ensure_identifier(self.rule_id, "verification rule identifier")
        _ensure_enum(self.kind, VerificationRuleKind, "verification rule kind")
        columns = _ensure_tuple(self.columns, "verification rule columns")
        for column in columns:
            _ensure_identifier(column, "verification column identifier")
        _ensure_unique(columns, "verification rule columns")
        if self.referenced_table is not None:
            _ensure_identifier(self.referenced_table, "verification table identifier")
        if self.verifier_code is not None:
            _ensure_token(self.verifier_code, "verification rule code")
        required_rule_ids = _ensure_tuple(
            self.required_rule_ids,
            "named verifier required rule IDs",
        )
        required_verifier_codes = _ensure_tuple(
            self.required_verifier_codes,
            "named verifier required verifier codes",
        )
        for rule_id in required_rule_ids:
            _ensure_identifier(rule_id, "named verifier required rule ID")
        for verifier_code in required_verifier_codes:
            _ensure_token(verifier_code, "named verifier required verifier code")
        _ensure_unique(required_rule_ids, "named verifier required rule IDs")
        _ensure_unique(
            required_verifier_codes,
            "named verifier required verifier codes",
        )
        if (required_rule_ids or required_verifier_codes) and self.verifier_code is None:
            raise ValueError("named verifier dependencies require a verifier code")
        if self.structural_source is not None:
            if type(self.structural_source) is not StructuralVerificationSourceSpec:
                raise TypeError(
                    "structural_source must be StructuralVerificationSourceSpec or None"
                )
            if self.verifier_code is not None:
                raise ValueError(
                    "verification rules cannot combine a structural source and named verifier"
                )
            if required_rule_ids or required_verifier_codes:
                raise ValueError(
                    "structural verification sources cannot declare named dependencies"
                )
        if self.predicate is not None:
            _ensure_predicate(self.predicate)
            if self.kind is not VerificationRuleKind.STATE_BRANCH:
                raise ValueError("closed predicates are reserved for state-branch verification")
            if _contains_relation(self.predicate):
                raise ValueError("state-branch predicates cannot contain relation lookups")
            if _qualified_column_values(self.predicate):
                raise ValueError("state-branch predicates cannot contain qualified relation columns")
            predicate_columns = tuple(value.column for value in _column_values(self.predicate))
            if any(value.source is not RowSource.CURRENT for value in _column_values(self.predicate)):
                raise ValueError("state-branch predicates may reference CURRENT columns only")
            if not set(predicate_columns).issubset(columns):
                raise ValueError("state-branch predicate references an undeclared verification column")
        if (
            self.kind
            in (
                VerificationRuleKind.CROSS_TABLE_REFERENCE,
                VerificationRuleKind.CROSS_TABLE_TOTALITY,
            )
            and self.referenced_table is None
        ):
            raise ValueError("cross-table verification requires a referenced table")
        bindings = _ensure_tuple(self.external_bindings, "external-reference field bindings")
        _ensure_exact_items(
            bindings,
            ExternalFieldBindingSpec,
            "external-reference field bindings",
        )
        _ensure_bool(self.nullable_reference, "external-reference nullable flag")
        if self.kind is VerificationRuleKind.EXTERNAL_AUTHORITY_REFERENCE:
            if self.referenced_table is not None:
                raise ValueError("external-authority verification cannot name a local table")
            if self.external_reference_kind is None:
                raise ValueError("external-authority verification requires a reference kind")
            _ensure_enum(
                self.external_reference_kind,
                ExternalAuthorityReferenceKind,
                "external-authority reference kind",
            )
            if self.provider_contract_type is None:
                raise ValueError("external-authority verification requires a provider contract")
            if self.provider_capability is None:
                raise ValueError("external-authority verification requires a provider capability")
            _ensure_token(
                self.provider_contract_type,
                "external-authority provider contract",
            )
            _ensure_token(
                self.provider_capability,
                "external-authority provider capability",
            )
            if self.provider_component_id is not None:
                _ensure_token(
                    self.provider_component_id,
                    "external-authority provider component",
                )
            if not bindings:
                raise ValueError("external-authority verification requires exact field bindings")
            local_columns = tuple(binding.local_column for binding in bindings)
            _ensure_unique(local_columns, "external-reference local columns")
            if columns != local_columns:
                raise ValueError("external-authority verification columns must exactly match bindings")
            if self.nullable_reference:
                if self.reference_presence_column is None:
                    raise ValueError("nullable external reference requires a presence column")
                _ensure_identifier(
                    self.reference_presence_column,
                    "external-reference presence column",
                )
                if self.reference_presence_column not in columns:
                    raise ValueError("external-reference presence column must be verified")
            elif self.reference_presence_column is not None:
                raise ValueError("required external reference cannot name a presence column")
        elif any(
            (
                self.external_reference_kind is not None,
                self.provider_contract_type is not None,
                self.provider_capability is not None,
                self.provider_component_id is not None,
                bool(bindings),
                self.nullable_reference,
                self.reference_presence_column is not None,
            )
        ):
            raise ValueError("provider metadata is reserved for external-authority verification")


def _ensure_known_columns(columns: tuple[str, ...], known: set[str], context: str) -> None:
    missing = tuple(column for column in columns if column not in known)
    if missing:
        raise ValueError(f"{context} references unknown column {missing[0]!r}")


@_dataclass(frozen=True, slots=True)
class AuthorityTableSpec:
    table_name: str
    columns: tuple[ColumnSpec, ...]
    primary_key_columns: tuple[str, ...]
    row_hash: RowHashSpec
    triggers: tuple[TriggerPolicySpec, ...]
    foreign_keys: tuple[ForeignKeySpec, ...] = ()
    indexes: tuple[IndexSpec, ...] = ()
    checks: tuple[CheckConstraintSpec, ...] = ()
    chain: ChainSpec | None = None
    verification_rules: tuple[VerificationRuleSpec, ...] = ()

    def __post_init__(self) -> None:
        _ensure_identifier(self.table_name, "table identifier")
        columns = _ensure_tuple(self.columns, "table columns", nonempty=True)
        _ensure_exact_items(columns, ColumnSpec, "table columns")
        column_names = tuple(column.name for column in columns)
        _ensure_unique(column_names, "table columns")
        known = set(column_names)

        primary_key = _ensure_tuple(self.primary_key_columns, "primary-key columns", nonempty=True)
        for column in primary_key:
            _ensure_identifier(column, "primary-key column identifier")
        _ensure_unique(primary_key, "primary-key columns")
        _ensure_known_columns(primary_key, known, "primary key")
        nullable = {column.name: column.nullable for column in columns}
        if any(nullable[column] for column in primary_key):
            raise ValueError("primary-key columns must be non-nullable")

        if type(self.row_hash) is not RowHashSpec:
            raise TypeError("row_hash must be RowHashSpec")
        _ensure_known_columns(
            (*self.row_hash.canonical_fields, self.row_hash.record_hash_column),
            known,
            "row hash",
        )

        foreign_keys = _ensure_tuple(self.foreign_keys, "foreign keys")
        indexes = _ensure_tuple(self.indexes, "indexes")
        checks = _ensure_tuple(self.checks, "checks")
        triggers = _ensure_tuple(self.triggers, "triggers", nonempty=True)
        rules = _ensure_tuple(self.verification_rules, "verification rules", nonempty=True)
        _ensure_exact_items(foreign_keys, ForeignKeySpec, "foreign keys")
        _ensure_exact_items(indexes, IndexSpec, "indexes")
        _ensure_exact_items(checks, CheckConstraintSpec, "checks")
        _ensure_exact_items(triggers, TriggerPolicySpec, "triggers")
        _ensure_exact_items(rules, VerificationRuleSpec, "verification rules")

        for foreign_key in foreign_keys:
            _ensure_known_columns(foreign_key.columns, known, "foreign key")
        for index in indexes:
            _ensure_known_columns(tuple(item.column for item in index.columns), known, "index")
            if index.predicate is not None:
                _ensure_known_columns(
                    tuple(item.column for item in _column_values(index.predicate)),
                    known,
                    "partial index",
                )
        for check in checks:
            _ensure_known_columns(
                tuple(item.column for item in _column_values(check.expression)),
                known,
                "check",
            )
        for trigger in triggers:
            if trigger.violation_when is not None:
                local_columns = tuple(
                    item.column
                    for item in _column_values(trigger.violation_when)
                    if item.source is not RowSource.RELATED
                )
                _ensure_known_columns(local_columns, known, "trigger")
            if trigger.state_machine is not None:
                state_machine_columns = (
                    trigger.state_machine.state_column,
                    *trigger.state_machine.identity_columns,
                )
                if trigger.state_machine.sequence_column is not None:
                    state_machine_columns = (
                        *state_machine_columns,
                        trigger.state_machine.sequence_column,
                    )
                _ensure_known_columns(state_machine_columns, known, "trigger state machine")

        kinds = tuple(trigger.kind for trigger in triggers)
        if kinds.count(TriggerPolicyKind.NO_UPDATE) != 1:
            raise ValueError("AuthorityTableSpec requires exactly one NO_UPDATE policy")
        if kinds.count(TriggerPolicyKind.NO_DELETE) != 1:
            raise ValueError("AuthorityTableSpec requires exactly one NO_DELETE policy")
        _ensure_unique(tuple(index.name for index in indexes), "index names")
        _ensure_unique(tuple(check.name for check in checks), "check names")
        _ensure_unique(tuple(trigger.name for trigger in triggers), "trigger names")
        _ensure_unique(tuple(rule.rule_id for rule in rules), "verification rule IDs")
        _ensure_unique(
            tuple(rule.verifier_code for rule in rules if rule.verifier_code is not None),
            "verification rule codes",
        )
        for rule in rules:
            _ensure_known_columns(rule.columns, known, "verification rule")
            source = rule.structural_source
            if source is None:
                continue
            if source.source_table is not None:
                if source.source_table == self.table_name:
                    raise ValueError(
                        "local structural trigger sources must omit source_table"
                    )
                continue
            _ensure_known_columns(
                source.columns,
                known,
                "structural verification source",
            )
            if source.kind is StructuralVerificationSourceKind.ALL_FOREIGN_KEYS:
                if not foreign_keys:
                    raise ValueError(
                        "all-foreign-keys structural source requires a non-empty foreign-key catalog"
                    )
            elif source.kind is StructuralVerificationSourceKind.GENESIS_FOREIGN_KEY:
                genesis_key = (
                    ("authority_instance_id", "genesis_record_hash"),
                    "family_authority_genesis",
                    ("authority_instance_id", "record_hash"),
                )
                if not any(
                    (item.columns, item.referenced_table, item.referenced_columns)
                    == genesis_key
                    for item in foreign_keys
                ):
                    raise ValueError(
                        "genesis foreign-key structural source requires the exact authority binding"
                    )
            elif source.kind in (
                StructuralVerificationSourceKind.CHAIN_SPEC,
                StructuralVerificationSourceKind.TERMINAL_HEAD,
            ):
                if self.chain is None:
                    raise ValueError("chain source requires an owned ChainSpec")
            elif source.kind is StructuralVerificationSourceKind.UNIQUE_INDEX:
                matching_indexes = tuple(
                    item for item in indexes if item.name == source.source_id
                )
                if len(matching_indexes) != 1 or not matching_indexes[0].unique:
                    raise ValueError(
                        "structural verification unique index must resolve to one exact UNIQUE index"
                    )
            elif source.kind is StructuralVerificationSourceKind.CHECK_CONSTRAINT:
                if sum(item.name == source.source_id for item in checks) != 1:
                    raise ValueError(
                        "structural verification check constraint must resolve exactly"
                    )
            elif source.kind is StructuralVerificationSourceKind.TRIGGER_POLICY:
                matching_triggers = tuple(
                    item for item in triggers if item.name == source.source_id
                )
                if len(matching_triggers) != 1:
                    raise ValueError(
                        "structural verification trigger policy must resolve exactly"
                    )
                trigger = matching_triggers[0]
                if (
                    trigger.kind is not TriggerPolicyKind.INSERT_GUARD
                    or trigger.event is not TriggerEvent.INSERT
                    or trigger.violation_when is None
                ):
                    raise ValueError(
                        "structural verification trigger source must be a closed insert guard"
                    )
            elif source.kind is StructuralVerificationSourceKind.EXACT_FOREIGN_KEY:
                exact_key = (
                    source.columns,
                    source.referenced_table,
                    source.referenced_columns,
                )
                if sum(
                    (item.columns, item.referenced_table, item.referenced_columns)
                    == exact_key
                    for item in foreign_keys
                ) != 1:
                    raise ValueError(
                        "structural verification exact foreign key must resolve exactly"
                    )

        if self.chain is not None:
            if type(self.chain) is not ChainSpec:
                raise TypeError("chain must be ChainSpec or None")
            _ensure_known_columns(
                (
                    *self.chain.partition_key_columns,
                    self.chain.ordinal_column,
                    self.chain.previous_hash_column,
                    self.chain.record_hash_column,
                ),
                known,
                "chain",
            )
            if self.chain.record_hash_column != self.row_hash.record_hash_column:
                raise ValueError("chain and row hash must use the same record-hash column")
            required_hash_fields = {
                *self.chain.partition_key_columns,
                self.chain.ordinal_column,
                self.chain.previous_hash_column,
            }
            if not required_hash_fields.issubset(self.row_hash.canonical_fields):
                raise ValueError("chain identity must be inside canonical row-hash fields")


class DependencyKind(_StrEnum):
    CROSS_TABLE_REFERENCE = "CROSS_TABLE_REFERENCE"
    LATE_INSERT_GUARD = "LATE_INSERT_GUARD"
    CHAIN_HEAD = "CHAIN_HEAD"
    EXTERNAL_AUTHORITY = "EXTERNAL_AUTHORITY"
    CONTRACT_LINEAGE = "CONTRACT_LINEAGE"


class CoverageKind(_StrEnum):
    COLUMN = "COLUMN"
    CANONICAL_PAYLOAD = "CANONICAL_PAYLOAD"
    NON_PERSISTED_DERIVATION = "NON_PERSISTED_DERIVATION"


class ContractDerivationKind(_StrEnum):
    IMMUTABLE_ROW_LOOKUP = "IMMUTABLE_ROW_LOOKUP"
    ORDERED_ROW_AGGREGATION = "ORDERED_ROW_AGGREGATION"


class ArtifactGraphDerivationKind(_StrEnum):
    CONTENT_ADDRESSED_CANONICAL_EXACT_BYTES = "CONTENT_ADDRESSED_CANONICAL_EXACT_BYTES"


class ArtifactGraphLocatorSourceKind(_StrEnum):
    PERSISTED_ROW = "PERSISTED_ROW"
    CONTRACT_INPUT = "CONTRACT_INPUT"


@_dataclass(frozen=True, slots=True)
class ComponentDependencySpec:
    component_id: str
    kind: DependencyKind
    required_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _ensure_token(self.component_id, "dependency component ID")
        _ensure_enum(self.kind, DependencyKind, "dependency kind")
        capabilities = _ensure_tuple(self.required_capabilities, "required capabilities")
        for capability in capabilities:
            _ensure_token(capability, "required capability")
        _ensure_unique(capabilities, "required capabilities")


@_dataclass(frozen=True, slots=True)
class ContractFieldCoverageSpec:
    contract_type: str
    field_name: str
    kind: CoverageKind
    table_name: str | None = None
    target_field: str | None = None
    derivation_id: str | None = None

    def __post_init__(self) -> None:
        _ensure_token(self.contract_type, "contract type")
        _ensure_identifier(self.field_name, "contract field identifier")
        _ensure_enum(self.kind, CoverageKind, "coverage kind")
        if self.kind in (CoverageKind.COLUMN, CoverageKind.CANONICAL_PAYLOAD):
            if self.table_name is None or self.target_field is None:
                raise ValueError("persisted coverage requires table_name and target_field")
            _ensure_identifier(self.table_name, "coverage table identifier")
            _ensure_identifier(self.target_field, "coverage target identifier")
            if self.derivation_id is not None:
                raise ValueError("persisted coverage cannot name a derivation")
        else:
            if self.table_name is not None or self.target_field is not None:
                raise ValueError("non-persisted coverage cannot name a table target")
            if self.derivation_id is None:
                raise ValueError("non-persisted coverage requires derivation_id")
            _ensure_token(self.derivation_id, "derivation ID")


@_dataclass(frozen=True, slots=True)
class ContractFieldDerivationSpec:
    """Closed metadata for one non-persisted contract-field derivation.

    ``IMMUTABLE_ROW_LOOKUP`` means the persisted ``binding_field`` is matched
    to ``source_key_field`` and the contract value is read from
    ``source_value_field``.  Pass M can therefore verify the derivation without
    inferring semantics from a free-form string. ``ORDERED_ROW_AGGREGATION``
    matches every source row by the same immutable key, sorts by the exact
    ``order_by_fields``, and collects ``source_value_field`` in that order.
    """

    derivation_id: str
    contract_type: str
    field_name: str
    kind: ContractDerivationKind
    binding_table: str
    binding_field: str
    source_table: str
    source_key_field: str
    source_value_field: str
    order_by_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _ensure_token(self.derivation_id, "derivation ID")
        _ensure_token(self.contract_type, "contract type")
        _ensure_identifier(self.field_name, "contract field identifier")
        _ensure_enum(self.kind, ContractDerivationKind, "contract derivation kind")
        _ensure_identifier(self.binding_table, "derivation binding table")
        _ensure_identifier(self.binding_field, "derivation binding field")
        _ensure_identifier(self.source_table, "derivation source table")
        _ensure_identifier(self.source_key_field, "derivation source key field")
        _ensure_identifier(self.source_value_field, "derivation source value field")
        order_by_fields = _ensure_tuple(self.order_by_fields, "derivation order-by fields")
        for field_name in order_by_fields:
            _ensure_identifier(field_name, "derivation order-by field")
        _ensure_unique(order_by_fields, "derivation order-by fields")
        if self.kind is ContractDerivationKind.IMMUTABLE_ROW_LOOKUP:
            if order_by_fields:
                raise ValueError("immutable row lookup cannot declare ordering")
        elif not order_by_fields:
            raise ValueError("ordered row aggregation requires fixed ordering")


@_dataclass(frozen=True, slots=True)
class ArtifactGraphContractSpec:
    """One closed contract-field surface reconstructed from an artifact graph."""

    contract_type: str
    field_names: tuple[str, ...]

    def __post_init__(self) -> None:
        _ensure_token(self.contract_type, "artifact graph contract type")
        field_names = _ensure_tuple(self.field_names, "artifact graph contract fields", nonempty=True)
        for field_name in field_names:
            _ensure_identifier(field_name, "artifact graph contract field identifier")
        _ensure_unique(field_names, "artifact graph contract fields")


@_dataclass(frozen=True, slots=True)
class ArtifactGraphLocatorSpec:
    """Closed locator source for one canonical exact-byte artifact root."""

    kind: ArtifactGraphLocatorSourceKind
    hash_field: str
    path_field: str
    binding_table: str | None = None
    contract_type: str | None = None

    def __post_init__(self) -> None:
        _ensure_enum(self.kind, ArtifactGraphLocatorSourceKind, "artifact graph locator source kind")
        _ensure_identifier(self.hash_field, "artifact graph locator hash field")
        _ensure_identifier(self.path_field, "artifact graph locator path field")
        if self.hash_field == self.path_field:
            raise ValueError("artifact graph locator hash and path fields must differ")
        if self.kind is ArtifactGraphLocatorSourceKind.PERSISTED_ROW:
            if self.binding_table is None:
                raise ValueError("persisted-row artifact locator requires a binding table")
            _ensure_identifier(self.binding_table, "artifact graph locator binding table")
            if self.contract_type is not None:
                raise ValueError("persisted-row artifact locator cannot name a contract input")
        else:
            if self.contract_type is None:
                raise ValueError("contract-input artifact locator requires a contract type")
            _ensure_token(self.contract_type, "artifact graph locator contract type")
            if self.binding_table is not None:
                raise ValueError("contract-input artifact locator cannot name a binding table")


@_dataclass(frozen=True, slots=True)
class ContentAddressedArtifactGraphDerivationSpec:
    """Fail-closed non-persisted derivation from one canonical exact-byte graph.

    A closed locator takes its immutable content hash and content-addressed path
    either from owned authority columns or from an explicit contract input. Pass M
    resolves exactly one artifact, verifies its canonical bytes with the named
    verifier, and reconstructs every declared contract field from that verified
    graph. This metadata deliberately has no expression, predicate, SQL, implicit
    directory scan, or JSON-payload target surface.
    """

    derivation_id: str
    locator: ArtifactGraphLocatorSpec
    contract_surfaces: tuple[ArtifactGraphContractSpec, ...]
    verifier_code: str
    kind: ArtifactGraphDerivationKind = ArtifactGraphDerivationKind.CONTENT_ADDRESSED_CANONICAL_EXACT_BYTES
    canonical_encoding: CanonicalEncoding = CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1
    hash_algorithm: RowHashAlgorithm = RowHashAlgorithm.SHA256
    cardinality: RelatedLookupCardinality = RelatedLookupCardinality.EXACTLY_ONE
    failure_policy: CanonicalJsonFailurePolicy = CanonicalJsonFailurePolicy.REJECT

    def __post_init__(self) -> None:
        _ensure_token(self.derivation_id, "artifact graph derivation ID")
        if type(self.locator) is not ArtifactGraphLocatorSpec:
            raise TypeError("artifact graph locator must be ArtifactGraphLocatorSpec")
        contract_surfaces = _ensure_tuple(
            self.contract_surfaces,
            "artifact graph contract surfaces",
            nonempty=True,
        )
        _ensure_exact_items(
            contract_surfaces,
            ArtifactGraphContractSpec,
            "artifact graph contract surfaces",
        )
        _ensure_unique(
            tuple(surface.contract_type for surface in contract_surfaces),
            "artifact graph contract types",
        )
        _ensure_unique(
            tuple(
                (surface.contract_type, field_name)
                for surface in contract_surfaces
                for field_name in surface.field_names
            ),
            "artifact graph contract fields",
        )
        _ensure_token(self.verifier_code, "artifact graph verifier code")
        _ensure_enum(self.kind, ArtifactGraphDerivationKind, "artifact graph derivation kind")
        _ensure_enum(self.canonical_encoding, CanonicalEncoding, "artifact graph canonical encoding")
        _ensure_enum(self.hash_algorithm, RowHashAlgorithm, "artifact graph hash algorithm")
        _ensure_enum(self.cardinality, RelatedLookupCardinality, "artifact graph cardinality")
        _ensure_enum(self.failure_policy, CanonicalJsonFailurePolicy, "artifact graph failure policy")
        if self.kind is not ArtifactGraphDerivationKind.CONTENT_ADDRESSED_CANONICAL_EXACT_BYTES:
            raise ValueError("artifact graph derivation must use canonical exact-byte content addressing")
        if self.canonical_encoding is not CanonicalEncoding.QUANTOS_CANONICAL_JSON_V1:
            raise ValueError("artifact graph encoding must be QUANTOS_CANONICAL_JSON_V1")
        if self.hash_algorithm is not RowHashAlgorithm.SHA256:
            raise ValueError("artifact graph hashing must use SHA256")
        if self.cardinality is not RelatedLookupCardinality.EXACTLY_ONE:
            raise ValueError("artifact graph resolution cardinality must be EXACTLY_ONE")
        if self.failure_policy is not CanonicalJsonFailurePolicy.REJECT:
            raise ValueError("artifact graph failure policy must be REJECT")


@_dataclass(frozen=True, slots=True)
class RequiredContractSurfaceSpec:
    """Contract fields delegated to another component for Pass-M closure."""

    contract_type: str
    field_names: tuple[str, ...]
    provider_component_id: str
    required_capability: str

    def __post_init__(self) -> None:
        _ensure_token(self.contract_type, "required contract type")
        fields = _ensure_tuple(self.field_names, "required contract fields", nonempty=True)
        for field_name in fields:
            _ensure_identifier(field_name, "required contract field identifier")
        _ensure_unique(fields, "required contract fields")
        _ensure_token(self.provider_component_id, "required contract surface provider")
        _ensure_token(self.required_capability, "required contract capability")


class InsertionPosition(_StrEnum):
    BEFORE = "BEFORE"
    AFTER = "AFTER"


@_dataclass(frozen=True, slots=True)
class OrderedColumnInsertionSpec:
    position: InsertionPosition
    anchor_column: str
    columns: tuple[ColumnSpec, ...]

    def __post_init__(self) -> None:
        _ensure_enum(self.position, InsertionPosition, "column insertion position")
        _ensure_identifier(self.anchor_column, "column insertion anchor")
        columns = _ensure_tuple(self.columns, "column insertion values", nonempty=True)
        _ensure_exact_items(columns, ColumnSpec, "column insertion values")
        names = tuple(column.name for column in columns)
        _ensure_unique(names, "column insertion names")
        if self.anchor_column in names:
            raise ValueError("column insertion anchor cannot be an inserted column")


@_dataclass(frozen=True, slots=True)
class OrderedFieldInsertionSpec:
    position: InsertionPosition
    anchor_field: str
    fields: tuple[str, ...]

    def __post_init__(self) -> None:
        _ensure_enum(self.position, InsertionPosition, "field insertion position")
        _ensure_identifier(self.anchor_field, "field insertion anchor")
        fields = _ensure_tuple(self.fields, "field insertion values", nonempty=True)
        for field_name in fields:
            _ensure_identifier(field_name, "field insertion identifier")
        _ensure_unique(fields, "field insertion values")
        if self.anchor_field in fields:
            raise ValueError("field insertion anchor cannot be an inserted field")


@_dataclass(frozen=True, slots=True)
class AuthorityTableExtensionSpec:
    """A typed, non-executable addition to a table owned by another component.

    Anchor-based ordered insertions let Pass C freeze embedded C6/C7 fields
    without mutating the C4 core declaration.  This value does not compose the
    fragments; completeness and anchor resolution remain Pass-M work.
    """

    extension_id: str
    target_table: str
    column_insertion: OrderedColumnInsertionSpec | None = None
    row_hash_field_insertion: OrderedFieldInsertionSpec | None = None
    foreign_keys: tuple[ForeignKeySpec, ...] = ()
    indexes: tuple[IndexSpec, ...] = ()
    checks: tuple[CheckConstraintSpec, ...] = ()
    triggers: tuple[TriggerPolicySpec, ...] = ()
    verification_rules: tuple[VerificationRuleSpec, ...] = ()
    external_continuity_rules: tuple[ExternalAuthorityContinuitySpec, ...] = ()

    def __post_init__(self) -> None:
        _ensure_token(self.extension_id, "table extension ID")
        _ensure_identifier(self.target_table, "extension target table")
        if self.column_insertion is not None and type(self.column_insertion) is not OrderedColumnInsertionSpec:
            raise TypeError("column_insertion must be OrderedColumnInsertionSpec or None")
        if (
            self.row_hash_field_insertion is not None
            and type(self.row_hash_field_insertion) is not OrderedFieldInsertionSpec
        ):
            raise TypeError("row_hash_field_insertion must be OrderedFieldInsertionSpec or None")
        if (self.column_insertion is None) != (self.row_hash_field_insertion is None):
            raise ValueError("column additions and row-hash field additions must be declared together")
        if self.column_insertion is not None:
            column_names = tuple(column.name for column in self.column_insertion.columns)
            if column_names != self.row_hash_field_insertion.fields:
                raise ValueError("row-hash field additions must exactly cover extension columns in order")

        foreign_keys = _ensure_tuple(self.foreign_keys, "extension foreign keys")
        indexes = _ensure_tuple(self.indexes, "extension indexes")
        checks = _ensure_tuple(self.checks, "extension checks")
        triggers = _ensure_tuple(self.triggers, "extension triggers")
        rules = _ensure_tuple(self.verification_rules, "extension verification rules")
        continuity_rules = _ensure_tuple(
            self.external_continuity_rules,
            "extension external continuity rules",
        )
        _ensure_exact_items(foreign_keys, ForeignKeySpec, "extension foreign keys")
        _ensure_exact_items(indexes, IndexSpec, "extension indexes")
        _ensure_exact_items(checks, CheckConstraintSpec, "extension checks")
        _ensure_exact_items(triggers, TriggerPolicySpec, "extension triggers")
        _ensure_exact_items(rules, VerificationRuleSpec, "extension verification rules")
        _ensure_exact_items(
            continuity_rules,
            ExternalAuthorityContinuitySpec,
            "extension external continuity rules",
        )
        if any(trigger.kind is not TriggerPolicyKind.INSERT_GUARD for trigger in triggers):
            raise ValueError("table extensions may add INSERT_GUARD policies only")
        _ensure_unique(tuple(index.name for index in indexes), "extension index names")
        _ensure_unique(tuple(check.name for check in checks), "extension check names")
        _ensure_unique(tuple(trigger.name for trigger in triggers), "extension trigger names")
        _ensure_unique(
            tuple(rule.rule_id for rule in rules),
            "extension verification rule IDs",
        )
        _ensure_unique(
            tuple(rule.verifier_code for rule in rules if rule.verifier_code is not None),
            "extension verification rule codes",
        )
        _ensure_unique(
            tuple(rule.verifier_code for rule in continuity_rules),
            "extension external continuity verifier codes",
        )
        ordinary_codes = {rule.verifier_code: rule for rule in rules if rule.verifier_code is not None}
        if set(ordinary_codes) & {rule.verifier_code for rule in continuity_rules}:
            raise ValueError("continuity verifier codes must not collide with ordinary verifier codes")
        for continuity in continuity_rules:
            missing = tuple(code for code in continuity.required_external_verifier_codes if code not in ordinary_codes)
            if missing:
                raise ValueError(f"external continuity references unknown verifier code {missing[0]!r}")
            referenced_rules = tuple(ordinary_codes[code] for code in continuity.required_external_verifier_codes)
            if any(rule.kind is not VerificationRuleKind.EXTERNAL_AUTHORITY_REFERENCE for rule in referenced_rules):
                raise ValueError("external continuity may depend on external-authority rules only")
            presence_column = continuity.reference_presence_column
            if presence_column is None:
                if any(rule.nullable_reference for rule in referenced_rules):
                    raise ValueError("nullable continuity requires an explicit presence column")
            elif not any(
                rule.nullable_reference and rule.reference_presence_column == presence_column
                for rule in referenced_rules
            ):
                raise ValueError("continuity presence column must control one required nullable reference")
        additions = (
            self.column_insertion is not None,
            bool(foreign_keys),
            bool(indexes),
            bool(checks),
            bool(triggers),
            bool(rules),
            bool(continuity_rules),
        )
        if not any(additions):
            raise ValueError("a table extension must declare at least one typed addition")


@_dataclass(frozen=True, slots=True)
class RequiredTableExtensionSpec:
    extension_id: str
    target_table: str
    provider_component_id: str
    required_capability: str

    def __post_init__(self) -> None:
        _ensure_token(self.extension_id, "required extension ID")
        _ensure_identifier(self.target_table, "required extension target table")
        _ensure_token(self.provider_component_id, "extension provider component ID")
        _ensure_token(self.required_capability, "required extension capability")


@_dataclass(frozen=True, slots=True)
class SchemaComponentSpec:
    component_id: str
    capabilities: tuple[str, ...]
    dependencies: tuple[ComponentDependencySpec, ...] = ()
    tables: tuple[AuthorityTableSpec, ...] = ()
    extensions: tuple[AuthorityTableExtensionSpec, ...] = ()
    required_extensions: tuple[RequiredTableExtensionSpec, ...] = ()
    coverage: tuple[ContractFieldCoverageSpec, ...] = ()
    contract_derivations: tuple[ContractFieldDerivationSpec, ...] = ()
    artifact_graph_derivations: tuple[ContentAddressedArtifactGraphDerivationSpec, ...] = ()
    required_contract_surfaces: tuple[RequiredContractSurfaceSpec, ...] = ()
    required_external_provider_capabilities: tuple[ExternalProviderCapabilityRequirementSpec, ...] = ()
    external_authority_claim_profiles: tuple[ExternalAuthorityClaimProfileSpec, ...] = ()
    auxiliary_semantic_recipes: tuple[AuxiliarySemanticRecipeSpec, ...] = ()

    def __post_init__(self) -> None:
        _ensure_token(self.component_id, "component ID")
        capabilities = _ensure_tuple(self.capabilities, "component capabilities", nonempty=True)
        dependencies = _ensure_tuple(self.dependencies, "component dependencies")
        tables = _ensure_tuple(self.tables, "component tables")
        extensions = _ensure_tuple(self.extensions, "component table extensions")
        required_extensions = _ensure_tuple(self.required_extensions, "required table extensions")
        coverage = _ensure_tuple(self.coverage, "contract coverage")
        contract_derivations = _ensure_tuple(self.contract_derivations, "contract derivation metadata")
        artifact_graph_derivations = _ensure_tuple(
            self.artifact_graph_derivations,
            "artifact graph derivation metadata",
        )
        required_contract_surfaces = _ensure_tuple(self.required_contract_surfaces, "required contract surfaces")
        external_provider_requirements = _ensure_tuple(
            self.required_external_provider_capabilities,
            "required external provider capabilities",
        )
        external_claim_profiles = _ensure_tuple(
            self.external_authority_claim_profiles,
            "external authority claim profiles",
        )
        auxiliary_semantic_recipes = _ensure_tuple(
            self.auxiliary_semantic_recipes,
            "auxiliary semantic recipes",
        )
        for capability in capabilities:
            _ensure_token(capability, "component capability")
        _ensure_unique(capabilities, "component capabilities")
        _ensure_exact_items(dependencies, ComponentDependencySpec, "component dependencies")
        _ensure_exact_items(tables, AuthorityTableSpec, "component tables")
        _ensure_exact_items(extensions, AuthorityTableExtensionSpec, "component table extensions")
        _ensure_exact_items(
            required_extensions,
            RequiredTableExtensionSpec,
            "required table extensions",
        )
        _ensure_exact_items(coverage, ContractFieldCoverageSpec, "contract coverage")
        _ensure_exact_items(
            contract_derivations,
            ContractFieldDerivationSpec,
            "contract derivation metadata",
        )
        _ensure_exact_items(
            artifact_graph_derivations,
            ContentAddressedArtifactGraphDerivationSpec,
            "artifact graph derivation metadata",
        )
        _ensure_exact_items(
            required_contract_surfaces,
            RequiredContractSurfaceSpec,
            "required contract surfaces",
        )
        _ensure_exact_items(
            external_provider_requirements,
            ExternalProviderCapabilityRequirementSpec,
            "required external provider capabilities",
        )
        _ensure_exact_items(
            external_claim_profiles,
            ExternalAuthorityClaimProfileSpec,
            "external authority claim profiles",
        )
        if any(
            type(recipe) not in _AUXILIARY_SEMANTIC_RECIPE_TYPES
            for recipe in auxiliary_semantic_recipes
        ):
            raise TypeError(
                "auxiliary semantic recipes must use one exact closed recipe type"
            )
        provider_requirement_keys = tuple(
            (item.provider_contract_type, item.provider_capability) for item in external_provider_requirements
        )
        _ensure_unique(provider_requirement_keys, "required external provider capabilities")
        claim_profile_keys = tuple(
            (item.provider_contract_type, item.provider_capability)
            for item in external_claim_profiles
        )
        _ensure_unique(claim_profile_keys, "external authority claim profiles")
        recipe_ids = tuple(recipe.recipe_id for recipe in auxiliary_semantic_recipes)
        for recipe_id in recipe_ids:
            _ensure_token(recipe_id, "auxiliary semantic recipe ID")
        _ensure_unique(recipe_ids, "auxiliary semantic recipe IDs")
        dependency_ids = tuple(item.component_id for item in dependencies)
        _ensure_unique(dependency_ids, "component dependency IDs")
        if self.component_id in dependency_ids:
            raise ValueError("component self-dependency is forbidden")
        _ensure_unique(tuple(item.table_name for item in tables), "component table names")
        extension_ids = tuple(item.extension_id for item in extensions)
        required_extension_ids = tuple(item.extension_id for item in required_extensions)
        _ensure_unique(extension_ids, "provided extension IDs")
        _ensure_unique(required_extension_ids, "required extension IDs")
        if set(extension_ids) & set(required_extension_ids):
            raise ValueError("a component cannot both provide and require one extension")
        if any(item.provider_component_id == self.component_id for item in required_extensions):
            raise ValueError("a component cannot require an extension from itself")
        owned_tables = {item.table_name for item in tables}
        if any(item.target_table not in owned_tables for item in required_extensions):
            raise ValueError("required extensions must target a table owned by the component")
        column_anchor_claims = tuple(
            (
                item.target_table,
                item.column_insertion.position,
                item.column_insertion.anchor_column,
            )
            for item in extensions
            if item.column_insertion is not None
        )
        hash_anchor_claims = tuple(
            (
                item.target_table,
                item.row_hash_field_insertion.position,
                item.row_hash_field_insertion.anchor_field,
            )
            for item in extensions
            if item.row_hash_field_insertion is not None
        )
        if len(set(column_anchor_claims)) != len(column_anchor_claims):
            raise ValueError("component extensions must not share a column insertion anchor")
        if len(set(hash_anchor_claims)) != len(hash_anchor_claims):
            raise ValueError("component extensions must not share a row-hash insertion anchor")
        extension_columns = tuple(
            (item.target_table, column.name)
            for item in extensions
            if item.column_insertion is not None
            for column in item.column_insertion.columns
        )
        if len(set(extension_columns)) != len(extension_columns):
            raise ValueError("component extension column additions must be unique")
        coverage_keys = tuple((item.contract_type, item.field_name) for item in coverage)
        if len(set(coverage_keys)) != len(coverage_keys):
            raise ValueError("every contract field must have exactly one coverage mapping")
        derivation_ids = tuple(item.derivation_id for item in contract_derivations)
        _ensure_unique(derivation_ids, "contract derivation IDs")
        artifact_graph_derivation_ids = tuple(item.derivation_id for item in artifact_graph_derivations)
        _ensure_unique(artifact_graph_derivation_ids, "artifact graph derivation IDs")
        if set(derivation_ids) & set(artifact_graph_derivation_ids):
            raise ValueError("row and artifact graph derivation IDs must be disjoint")
        derivation_keys = tuple((item.contract_type, item.field_name) for item in contract_derivations)
        _ensure_unique(derivation_keys, "contract derivation fields")
        artifact_graph_derivation_keys = tuple(
            (surface.contract_type, field_name)
            for graph in artifact_graph_derivations
            for surface in graph.contract_surfaces
            for field_name in surface.field_names
        )
        _ensure_unique(artifact_graph_derivation_keys, "artifact graph derivation fields")
        if set(derivation_keys) & set(artifact_graph_derivation_keys):
            raise ValueError("row and artifact graph derivation fields must be disjoint")
        artifact_graph_locator_keys = tuple(
            (
                graph.locator.kind,
                graph.locator.binding_table,
                graph.locator.contract_type,
                graph.locator.hash_field,
                graph.locator.path_field,
            )
            for graph in artifact_graph_derivations
        )
        _ensure_unique(artifact_graph_locator_keys, "artifact graph root locators")
        contract_input_locator_fields = {
            (graph.locator.contract_type, field_name)
            for graph in artifact_graph_derivations
            if graph.locator.kind is ArtifactGraphLocatorSourceKind.CONTRACT_INPUT
            for field_name in (graph.locator.hash_field, graph.locator.path_field)
        }
        if contract_input_locator_fields & set(artifact_graph_derivation_keys):
            raise ValueError("contract-input artifact locators cannot be derived by artifact graphs")
        coverage_derivations = {
            (item.contract_type, item.field_name, item.derivation_id)
            for item in coverage
            if item.kind is CoverageKind.NON_PERSISTED_DERIVATION
        }
        declared_derivations = {
            (item.contract_type, item.field_name, item.derivation_id) for item in contract_derivations
        }
        declared_derivations.update(
            (surface.contract_type, field_name, graph.derivation_id)
            for graph in artifact_graph_derivations
            for surface in graph.contract_surfaces
            for field_name in surface.field_names
        )
        if coverage_derivations != declared_derivations:
            raise ValueError("non-persisted coverage must match contract derivation metadata exactly")
        table_columns = {table.table_name: {column.name for column in table.columns} for table in tables}
        table_rules = {
            table.table_name: {
                rule.rule_id: rule for rule in table.verification_rules
            }
            for table in tables
        }
        for recipe in auxiliary_semantic_recipes:
            _ensure_enum(
                recipe.recipe_kind,
                AuxiliarySemanticRecipeKind,
                "auxiliary semantic recipe kind",
            )
            _ensure_identifier(recipe.table_name, "auxiliary semantic recipe table")
            if recipe.table_name not in table_columns:
                raise ValueError("auxiliary semantic recipe table must be component-owned")
            columns = _ensure_tuple(
                recipe.columns,
                "auxiliary semantic recipe columns",
                nonempty=True,
            )
            for column in columns:
                _ensure_identifier(column, "auxiliary semantic recipe column")
            _ensure_unique(columns, "auxiliary semantic recipe columns")
            _ensure_known_columns(
                columns,
                table_columns[recipe.table_name],
                "auxiliary semantic recipe",
            )
            referenced_tables = _ensure_tuple(
                recipe.referenced_tables,
                "auxiliary semantic recipe referenced tables",
            )
            for referenced_table in referenced_tables:
                _ensure_identifier(
                    referenced_table,
                    "auxiliary semantic recipe referenced table",
                )
            _ensure_unique(
                referenced_tables,
                "auxiliary semantic recipe referenced tables",
            )
            required_rule_ids = _ensure_tuple(
                recipe.required_rule_ids,
                "auxiliary semantic recipe rule IDs",
                nonempty=True,
            )
            for rule_id in required_rule_ids:
                _ensure_identifier(rule_id, "auxiliary semantic recipe rule ID")
            _ensure_unique(required_rule_ids, "auxiliary semantic recipe rule IDs")
            owned_rules = table_rules[recipe.table_name]
            missing_rule_ids = set(required_rule_ids) - owned_rules.keys()
            if missing_rule_ids:
                raise ValueError(
                    "auxiliary semantic recipe rule IDs must resolve on the owned table"
                )
            required_verifier_codes = _ensure_tuple(
                recipe.required_verifier_codes,
                "auxiliary semantic recipe verifier codes",
            )
            for verifier_code in required_verifier_codes:
                _ensure_token(
                    verifier_code,
                    "auxiliary semantic recipe verifier code",
                )
            _ensure_unique(
                required_verifier_codes,
                "auxiliary semantic recipe verifier codes",
            )
            resolved_codes = {
                owned_rules[rule_id].verifier_code
                for rule_id in required_rule_ids
                if owned_rules[rule_id].verifier_code is not None
            }
            if set(required_verifier_codes) != resolved_codes:
                raise ValueError(
                    "auxiliary semantic recipe verifier codes must exactly match its rules"
                )
            readable_rule_columns = {
                column
                for rule_id in required_rule_ids
                for column in (
                    table_columns[recipe.table_name]
                    if owned_rules[rule_id].structural_source is not None
                    else set(owned_rules[rule_id].columns)
                )
            }
            if not set(columns).issubset(readable_rule_columns):
                raise ValueError(
                    "auxiliary semantic recipe columns must be readable by its rules"
                )
        for derivation in contract_derivations:
            if derivation.binding_table not in table_columns:
                raise ValueError("derivation binding table must be owned by the component")
            if derivation.source_table not in table_columns:
                raise ValueError("derivation source table must be owned by the component")
            if derivation.binding_field not in table_columns[derivation.binding_table]:
                raise ValueError("derivation binding field must exist in the binding table")
            if derivation.source_key_field not in table_columns[derivation.source_table]:
                raise ValueError("derivation source key must exist in the source table")
            if derivation.source_value_field not in table_columns[derivation.source_table]:
                raise ValueError("derivation source value must exist in the source table")
            if any(
                field_name not in table_columns[derivation.source_table] for field_name in derivation.order_by_fields
            ):
                raise ValueError("derivation order-by fields must exist in the source table")
        table_column_specs = {
            table.table_name: {column.name: column for column in table.columns} for table in tables
        }
        table_specs_by_name = {table.table_name: table for table in tables}
        for graph in artifact_graph_derivations:
            locator = graph.locator
            if locator.kind is ArtifactGraphLocatorSourceKind.CONTRACT_INPUT:
                continue
            binding_table = locator.binding_table
            if binding_table is None or binding_table not in table_columns:
                raise ValueError("artifact graph binding table must be owned by the component")
            binding_columns = table_columns[binding_table]
            if locator.hash_field not in binding_columns:
                raise ValueError("artifact graph hash field must exist in the binding table")
            if locator.path_field not in binding_columns:
                raise ValueError("artifact graph path field must exist in the binding table")
            if locator.hash_field == table_specs_by_name[binding_table].row_hash.record_hash_column:
                raise ValueError("artifact graph content hash cannot use an authority row record hash")
            if table_column_specs[binding_table][locator.hash_field].sql_type is not SqlType.TEXT:
                raise ValueError("artifact graph hash field must be TEXT")
            if table_column_specs[binding_table][locator.path_field].sql_type is not SqlType.TEXT:
                raise ValueError("artifact graph path field must be TEXT")
        artifact_locator_columns = {
            (graph.locator.binding_table, field_name)
            for graph in artifact_graph_derivations
            if graph.locator.kind is ArtifactGraphLocatorSourceKind.PERSISTED_ROW
            for field_name in (graph.locator.hash_field, graph.locator.path_field)
        }
        canonical_payload_targets = {
            (item.table_name, item.target_field)
            for item in coverage
            if item.kind is CoverageKind.CANONICAL_PAYLOAD
        }
        if artifact_locator_columns & canonical_payload_targets:
            raise ValueError("artifact locator columns cannot be canonical payloads")
        required_surface_keys = tuple(
            (surface.contract_type, field_name)
            for surface in required_contract_surfaces
            for field_name in surface.field_names
        )
        _ensure_unique(required_surface_keys, "required contract fields")
        if set(required_surface_keys) & set(coverage_keys):
            raise ValueError("required contract fields cannot overlap owned contract coverage")
        if any(surface.provider_component_id == self.component_id for surface in required_contract_surfaces):
            raise ValueError("required contract surface provider cannot be the component itself")
        declared_external_providers = {
            (rule.provider_contract_type, rule.provider_capability)
            for table in tables
            for rule in table.verification_rules
            if rule.kind is VerificationRuleKind.EXTERNAL_AUTHORITY_REFERENCE
            and rule.provider_component_id is None
        } | {
            (rule.provider_contract_type, rule.provider_capability)
            for extension in extensions
            for rule in extension.verification_rules
            if rule.kind is VerificationRuleKind.EXTERNAL_AUTHORITY_REFERENCE and rule.provider_component_id is None
        }
        if set(provider_requirement_keys) != declared_external_providers:
            raise ValueError("external provider requirements must exactly cover non-component providers")
        if not set(claim_profile_keys).issubset(provider_requirement_keys):
            raise ValueError(
                "external authority claim profiles require a declared external provider capability"
            )
        non_component_provider_rules = tuple(
            rule
            for table in tables
            for rule in table.verification_rules
            if rule.kind is VerificationRuleKind.EXTERNAL_AUTHORITY_REFERENCE
            and rule.provider_component_id is None
        ) + tuple(
            rule
            for extension in extensions
            for rule in extension.verification_rules
            if rule.kind is VerificationRuleKind.EXTERNAL_AUTHORITY_REFERENCE
            and rule.provider_component_id is None
        )
        for profile in external_claim_profiles:
            matching_rules = tuple(
                rule
                for rule in non_component_provider_rules
                if (
                    rule.provider_contract_type,
                    rule.provider_capability,
                )
                == (
                    profile.provider_contract_type,
                    profile.provider_capability,
                )
            )
            if not matching_rules:
                raise ValueError(
                    "external authority claim profile must resolve one declared provider rule"
                )
            rule_surfaces = {
                (rule.external_reference_kind, rule.columns)
                for rule in matching_rules
            }
            if rule_surfaces != {
                (
                    profile.external_reference_kind,
                    profile.required_binding_columns,
                )
            }:
                raise ValueError(
                    "external authority claim-profile binding columns must exactly match provider rules"
                )
        dependency_by_id = {dependency.component_id: dependency for dependency in dependencies}
        component_provider_rules = tuple(
            rule
            for table in tables
            for rule in table.verification_rules
            if rule.kind is VerificationRuleKind.EXTERNAL_AUTHORITY_REFERENCE
            and rule.provider_component_id is not None
        ) + tuple(
            rule
            for extension in extensions
            for rule in extension.verification_rules
            if rule.kind is VerificationRuleKind.EXTERNAL_AUTHORITY_REFERENCE and rule.provider_component_id is not None
        )
        for rule in component_provider_rules:
            if rule.provider_component_id == self.component_id:
                raise ValueError("component-backed provider rule cannot reference its consumer")
            dependency = dependency_by_id.get(rule.provider_component_id)
            if dependency is None:
                raise ValueError("component-backed provider rule requires an explicit dependency")
            if rule.provider_capability not in dependency.required_capabilities:
                raise ValueError("component-backed provider capability must be required by the dependency")


class RendererKeywordCase(_StrEnum):
    UPPER = "UPPER"


class RendererIdentifierQuoting(_StrEnum):
    DOUBLE_QUOTE = "DOUBLE_QUOTE"


class RendererWhitespace(_StrEnum):
    SINGLE_ASCII_SPACE = "SINGLE_ASCII_SPACE"


class RendererLineEnding(_StrEnum):
    LF = "LF"


class RendererStatementTermination(_StrEnum):
    SEMICOLON_LF = "SEMICOLON_LF"


class RendererObjectOrder(_StrEnum):
    COMPONENT_TABLE_INDEX_TRIGGER = "COMPONENT_TABLE_INDEX_TRIGGER"


class SqliteRuntimeCapability(_StrEnum):
    FOREIGN_KEY_ENFORCEMENT = "FOREIGN_KEY_ENFORCEMENT"
    DEFERRED_FOREIGN_KEYS = "DEFERRED_FOREIGN_KEYS"
    PARTIAL_INDEXES = "PARTIAL_INDEXES"
    STRICT_TABLES = "STRICT_TABLES"
    JSON1 = "JSON1"
    WINDOW_FUNCTIONS = "WINDOW_FUNCTIONS"
    PERSISTENT_TRIGGERS = "PERSISTENT_TRIGGERS"
    APPLICATION_DEFINED_SCALAR_FUNCTIONS = "APPLICATION_DEFINED_SCALAR_FUNCTIONS"
    APPLICATION_DEFINED_AGGREGATES = "APPLICATION_DEFINED_AGGREGATES"
    TRUSTED_SCHEMA_PRAGMA = "TRUSTED_SCHEMA_PRAGMA"
    AUTHORIZER_CALLBACK = "AUTHORIZER_CALLBACK"
    FUNCTION_LIST_METADATA = "FUNCTION_LIST_METADATA"


class SqliteTableMode(_StrEnum):
    STRICT = "STRICT"


class SqliteAffinityPolicy(_StrEnum):
    STRICT_LOSSLESS_COERCION = "STRICT_LOSSLESS_COERCION"


class SqliteTrustedSchemaProfile(_StrEnum):
    VERIFY_SEALED_THEN_TRUST_TRANSACTION_V1 = (
        "VERIFY_SEALED_THEN_TRUST_TRANSACTION_V1"
    )


class SqliteNativeExtensionPolicy(_StrEnum):
    INNOCUOUS_EXTENSION_OFF = "INNOCUOUS_EXTENSION_OFF"


class SqliteTrustTransitionStep(_StrEnum):
    FRESH_NON_POOLED_CONNECTION = "FRESH_NON_POOLED_CONNECTION"
    DISABLE_EXTENSION_LOADING = "DISABLE_EXTENSION_LOADING"
    SET_TRUSTED_SCHEMA_OFF = "SET_TRUSTED_SCHEMA_OFF"
    VERIFY_MAIN_AND_EMPTY_TEMP = "VERIFY_MAIN_AND_EMPTY_TEMP"
    BEGIN_IMMEDIATE = "BEGIN_IMMEDIATE"
    CAS_EXCLUSIVE_OWNER_TOKEN = "CAS_EXCLUSIVE_OWNER_TOKEN"
    VERIFY_EXACT_SEALED_CATALOG = "VERIFY_EXACT_SEALED_CATALOG"
    READ_SCHEMA_VERSION_BEFORE_TRUST = "READ_SCHEMA_VERSION_BEFORE_TRUST"
    INSTALL_DENY_BY_DEFAULT_AUTHORIZER = "INSTALL_DENY_BY_DEFAULT_AUTHORIZER"
    SET_TRUSTED_SCHEMA_ON_ONCE = "SET_TRUSTED_SCHEMA_ON_ONCE"
    READ_SCHEMA_VERSION_AFTER_TRUST = "READ_SCHEMA_VERSION_AFTER_TRUST"
    REGISTER_MANIFEST_BOUND_FUNCTIONS = "REGISTER_MANIFEST_BOUND_FUNCTIONS"
    EXECUTE_CONTROLLED_DML = "EXECUTE_CONTROLLED_DML"
    STORE_OWNED_TERMINAL_COMMIT_OR_ROLLBACK = (
        "STORE_OWNED_TERMINAL_COMMIT_OR_ROLLBACK"
    )
    UNREGISTER_FUNCTIONS_IN_FINALLY = "UNREGISTER_FUNCTIONS_IN_FINALLY"
    SET_TRUSTED_SCHEMA_OFF_IN_FINALLY = "SET_TRUSTED_SCHEMA_OFF_IN_FINALLY"
    CLOSE_CONNECTION_IN_FINALLY = "CLOSE_CONNECTION_IN_FINALLY"


class SqliteAuthorizerDeniedOperation(_StrEnum):
    SCHEMA_CREATE = "SCHEMA_CREATE"
    SCHEMA_DROP = "SCHEMA_DROP"
    SCHEMA_ALTER = "SCHEMA_ALTER"
    TEMP_SCHEMA_CREATE = "TEMP_SCHEMA_CREATE"
    TEMP_SCHEMA_DROP = "TEMP_SCHEMA_DROP"
    ATTACH = "ATTACH"
    DETACH = "DETACH"
    LOAD_EXTENSION_FUNCTION = "LOAD_EXTENSION_FUNCTION"
    DANGEROUS_PRAGMA = "DANGEROUS_PRAGMA"
    ANALYZE = "ANALYZE"
    REINDEX = "REINDEX"
    VACUUM = "VACUUM"
    UNCONTROLLED_TRANSACTION = "UNCONTROLLED_TRANSACTION"
    UNCONTROLLED_SAVEPOINT = "UNCONTROLLED_SAVEPOINT"
    UNKNOWN_FUNCTION = "UNKNOWN_FUNCTION"
    UNKNOWN_TABLE_WRITE = "UNKNOWN_TABLE_WRITE"


class SqliteAuthorizerTransactionPolicy(_StrEnum):
    STORE_OWNED_TERMINAL_ONLY_V1 = "STORE_OWNED_TERMINAL_ONLY_V1"


@_dataclass(frozen=True, slots=True)
class SqliteRuntimeProfile:
    """Closed L2 connection profile for future persistent-schema UDF use.

    Python's stdlib binding cannot tag functions ``SQLITE_INNOCUOUS``.  The
    current L2 profile therefore begins with an untrusted schema, takes the
    single-writer lock, verifies the exact sealed catalog without application
    functions, and enables trust only on that fresh, authorizer-confined
    transaction.  Pass M must still provide the implementation and executable
    state machine; this declaration does not open a database.
    """

    profile_version: str = "QUANTOS_SQLITE_RUNTIME_PROFILE_V1"
    minimum_sqlite_version: tuple[int, int, int] = (3, 38, 0)
    required_capabilities: tuple[SqliteRuntimeCapability, ...] = tuple(
        SqliteRuntimeCapability
    )
    table_mode: SqliteTableMode = SqliteTableMode.STRICT
    affinity_policy: SqliteAffinityPolicy = (
        SqliteAffinityPolicy.STRICT_LOSSLESS_COERCION
    )
    trusted_schema_profile: SqliteTrustedSchemaProfile = (
        SqliteTrustedSchemaProfile.VERIFY_SEALED_THEN_TRUST_TRANSACTION_V1
    )
    native_extension_policy: SqliteNativeExtensionPolicy = (
        SqliteNativeExtensionPolicy.INNOCUOUS_EXTENSION_OFF
    )
    connection_cached_statements: int = 0
    trust_transition_steps: tuple[SqliteTrustTransitionStep, ...] = tuple(
        SqliteTrustTransitionStep
    )
    authorizer_denied_operations: tuple[SqliteAuthorizerDeniedOperation, ...] = (
        tuple(SqliteAuthorizerDeniedOperation)
    )
    transaction_authorizer_policy: SqliteAuthorizerTransactionPolicy = (
        SqliteAuthorizerTransactionPolicy.STORE_OWNED_TERMINAL_ONLY_V1
    )
    requires_fresh_connection: bool = True
    requires_main_and_empty_temp_only: bool = True
    requires_begin_immediate: bool = True
    requires_owner_token_cas: bool = True
    requires_exact_sealed_catalog: bool = True
    requires_no_unknown_objects: bool = True
    requires_schema_version_double_read: bool = True
    authorizer_deny_by_default: bool = True
    requires_manifest_bound_function_hashes: bool = True
    allows_load_extension: bool = False
    allows_connection_reuse: bool = False
    unregisters_functions_before_close: bool = True
    closes_connection_on_every_exit: bool = True

    def __post_init__(self) -> None:
        if self.profile_version != "QUANTOS_SQLITE_RUNTIME_PROFILE_V1":
            raise ValueError("unknown SQLite runtime profile version")
        if self.minimum_sqlite_version != (3, 38, 0):
            raise ValueError("minimum SQLite version must be exactly 3.38.0")
        capabilities = _ensure_tuple(
            self.required_capabilities,
            "required SQLite runtime capabilities",
            nonempty=True,
        )
        _ensure_exact_items(
            capabilities,
            SqliteRuntimeCapability,
            "required SQLite runtime capabilities",
        )
        if capabilities != tuple(SqliteRuntimeCapability):
            raise ValueError("required SQLite runtime capabilities must be exact")
        _ensure_enum(self.table_mode, SqliteTableMode, "SQLite table mode")
        if self.table_mode is not SqliteTableMode.STRICT:
            raise ValueError("family authority tables require STRICT mode")
        _ensure_enum(
            self.affinity_policy,
            SqliteAffinityPolicy,
            "SQLite affinity policy",
        )
        if self.affinity_policy is not SqliteAffinityPolicy.STRICT_LOSSLESS_COERCION:
            raise ValueError("SQLite affinity policy must use STRICT lossless coercion")
        _ensure_enum(
            self.trusted_schema_profile,
            SqliteTrustedSchemaProfile,
            "trusted-schema profile",
        )
        if self.trusted_schema_profile is not (
            SqliteTrustedSchemaProfile.VERIFY_SEALED_THEN_TRUST_TRANSACTION_V1
        ):
            raise ValueError("trusted-schema profile must verify before transaction trust")
        _ensure_enum(
            self.native_extension_policy,
            SqliteNativeExtensionPolicy,
            "native-extension policy",
        )
        if self.native_extension_policy is not (
            SqliteNativeExtensionPolicy.INNOCUOUS_EXTENSION_OFF
        ):
            raise ValueError("native INNOCUOUS extensions are not a current L2 capability")
        if type(self.connection_cached_statements) is not int or (
            self.connection_cached_statements != 0
        ):
            raise ValueError("SQLite cached statements must be exactly zero")
        steps = _ensure_tuple(
            self.trust_transition_steps,
            "SQLite trust transition",
            nonempty=True,
        )
        _ensure_exact_items(
            steps,
            SqliteTrustTransitionStep,
            "SQLite trust transition",
        )
        if steps != tuple(SqliteTrustTransitionStep):
            raise ValueError("SQLite trust transition must use the exact closed sequence")
        denied = _ensure_tuple(
            self.authorizer_denied_operations,
            "SQLite authorizer deny set",
            nonempty=True,
        )
        _ensure_exact_items(
            denied,
            SqliteAuthorizerDeniedOperation,
            "SQLite authorizer deny set",
        )
        if denied != tuple(SqliteAuthorizerDeniedOperation):
            raise ValueError("SQLite authorizer deny set must be exact")
        _ensure_enum(
            self.transaction_authorizer_policy,
            SqliteAuthorizerTransactionPolicy,
            "SQLite authorizer transaction policy",
        )
        if self.transaction_authorizer_policy is not (
            SqliteAuthorizerTransactionPolicy.STORE_OWNED_TERMINAL_ONLY_V1
        ):
            raise ValueError(
                "SQLite authorizer must allow only one store-owned terminal action"
            )
        required_true = (
            ("fresh connection", self.requires_fresh_connection),
            ("main and empty temp", self.requires_main_and_empty_temp_only),
            ("immediate write transaction", self.requires_begin_immediate),
            ("owner token CAS", self.requires_owner_token_cas),
            ("exact sealed catalog", self.requires_exact_sealed_catalog),
            ("unknown-object rejection", self.requires_no_unknown_objects),
            ("schema-version double read", self.requires_schema_version_double_read),
            ("deny-by-default authorizer", self.authorizer_deny_by_default),
            ("manifest implementation hashes", self.requires_manifest_bound_function_hashes),
            ("function unregistration", self.unregisters_functions_before_close),
            ("connection close", self.closes_connection_on_every_exit),
        )
        for label, value in required_true:
            _ensure_bool(value, f"SQLite runtime {label}")
            if not value:
                raise ValueError(f"SQLite runtime requires {label}")
        _ensure_bool(self.allows_load_extension, "SQLite runtime allows_load_extension")
        if self.allows_load_extension:
            raise ValueError("SQLite runtime forbids load_extension")
        _ensure_bool(self.allows_connection_reuse, "SQLite runtime allows_connection_reuse")
        if self.allows_connection_reuse:
            raise ValueError("SQLite runtime forbids connection reuse")


FAMILY_SCHEMA_SQLITE_RUNTIME_PROFILE = SqliteRuntimeProfile()


class RendererSemanticFunctionKind(_StrEnum):
    SCALAR = "SCALAR"
    AGGREGATE = "AGGREGATE"


class RendererSemanticOperation(_StrEnum):
    CANONICAL_JSON_FIELD_VALUE = "CANONICAL_JSON_FIELD_VALUE"
    RELATED_CANONICAL_JSON_ARRAY_VALUE = "RELATED_CANONICAL_JSON_ARRAY_VALUE"
    RELATED_CANONICAL_JSON_ARRAY_LENGTH = "RELATED_CANONICAL_JSON_ARRAY_LENGTH"
    RELATED_CANONICAL_JSON_ARRAY_CONTAINS = "RELATED_CANONICAL_JSON_ARRAY_CONTAINS"
    CANONICAL_JSON_PAYLOAD_HASH = "CANONICAL_JSON_PAYLOAD_HASH"
    CANONICAL_ORDERED_VALUE_HASH = "CANONICAL_ORDERED_VALUE_HASH"
    MEAN_NET_RETURN_DIFFERENTIAL = "MEAN_NET_RETURN_DIFFERENTIAL"
    PREFIX_STREAM_EXTENSION = "PREFIX_STREAM_EXTENSION"
    LATEST_PER_DOMAIN_POLICY = "LATEST_PER_DOMAIN_POLICY"


class RendererAbiValueKind(_StrEnum):
    SQL_SCALAR = "SQL_SCALAR"
    TEXT = "TEXT"
    INTEGER = "INTEGER"
    OPTIONAL_INTEGER = "OPTIONAL_INTEGER"
    REAL_IEEE754 = "REAL_IEEE754"
    BOOLEAN_INTEGER = "BOOLEAN_INTEGER"
    CANONICAL_JSON_TEXT = "CANONICAL_JSON_TEXT"
    JSON_PATH_TEXT = "JSON_PATH_TEXT"
    TYPE_TAG_TEXT = "TYPE_TAG_TEXT"
    PROJECTION_TAG_TEXT = "PROJECTION_TAG_TEXT"
    SHA256_HEX_TEXT = "SHA256_HEX_TEXT"


class RendererAggregateInputOrdering(_StrEnum):
    NONE = "NONE"
    ORDERED_SUBQUERY_BINARY_ASC = "ORDERED_SUBQUERY_BINARY_ASC"


class RendererFunctionFailurePolicy(_StrEnum):
    RETURN_NULL_FOR_OUTER_FAIL_CLOSED = "RETURN_NULL_FOR_OUTER_FAIL_CLOSED"


@_dataclass(frozen=True, slots=True)
class RendererSemanticFunctionAbi:
    operation: RendererSemanticOperation
    sqlite_name: str
    kind: RendererSemanticFunctionKind
    argument_types: tuple[RendererAbiValueKind, ...]
    result_type: RendererAbiValueKind
    semantic_contract_id: str
    input_ordering: RendererAggregateInputOrdering = (
        RendererAggregateInputOrdering.NONE
    )
    failure_policy: RendererFunctionFailurePolicy = (
        RendererFunctionFailurePolicy.RETURN_NULL_FOR_OUTER_FAIL_CLOSED
    )
    deterministic: bool = True
    pure_input_only: bool = True
    manifest_implementation_hash_required: bool = True

    @property
    def arity(self) -> int:
        return len(self.argument_types)

    def __post_init__(self) -> None:
        _ensure_enum(
            self.operation,
            RendererSemanticOperation,
            "renderer semantic operation",
        )
        _ensure_identifier(self.sqlite_name, "renderer semantic function name")
        if not self.sqlite_name.startswith("quantos_"):
            raise ValueError("renderer semantic function names require the quantos_ prefix")
        _ensure_enum(self.kind, RendererSemanticFunctionKind, "semantic function kind")
        arguments = _ensure_tuple(
            self.argument_types,
            "semantic function ABI arguments",
            nonempty=True,
        )
        _ensure_exact_items(
            arguments,
            RendererAbiValueKind,
            "semantic function ABI arguments",
        )
        _ensure_enum(self.result_type, RendererAbiValueKind, "semantic function result")
        _ensure_token(self.semantic_contract_id, "semantic function contract ID")
        _ensure_enum(
            self.input_ordering,
            RendererAggregateInputOrdering,
            "semantic function input ordering",
        )
        if self.kind is RendererSemanticFunctionKind.SCALAR:
            if self.input_ordering is not RendererAggregateInputOrdering.NONE:
                raise ValueError("scalar semantic functions forbid aggregate input ordering")
        elif self.input_ordering is not (
            RendererAggregateInputOrdering.ORDERED_SUBQUERY_BINARY_ASC
        ):
            raise ValueError("aggregate semantic functions require ordered subquery input")
        _ensure_enum(
            self.failure_policy,
            RendererFunctionFailurePolicy,
            "semantic function failure policy",
        )
        if self.failure_policy is not (
            RendererFunctionFailurePolicy.RETURN_NULL_FOR_OUTER_FAIL_CLOSED
        ):
            raise ValueError("semantic functions must return NULL for outer fail-closed handling")
        for label, value in (
            ("deterministic", self.deterministic),
            ("pure input", self.pure_input_only),
            ("manifest implementation hash", self.manifest_implementation_hash_required),
        ):
            _ensure_bool(value, f"semantic function {label}")
            if not value:
                raise ValueError(f"semantic function requires {label}")


def _renderer_semantic_function_abi(
    operation: RendererSemanticOperation,
    sqlite_name: str,
    kind: RendererSemanticFunctionKind,
    argument_types: tuple[RendererAbiValueKind, ...],
    result_type: RendererAbiValueKind,
    semantic_contract_id: str,
) -> RendererSemanticFunctionAbi:
    return RendererSemanticFunctionAbi(
        operation=operation,
        sqlite_name=sqlite_name,
        kind=kind,
        argument_types=argument_types,
        result_type=result_type,
        semantic_contract_id=semantic_contract_id,
        input_ordering=(
            RendererAggregateInputOrdering.ORDERED_SUBQUERY_BINARY_ASC
            if kind is RendererSemanticFunctionKind.AGGREGATE
            else RendererAggregateInputOrdering.NONE
        ),
    )


FAMILY_SCHEMA_RENDERER_SEMANTIC_FUNCTION_ABIS = (
    _renderer_semantic_function_abi(
        RendererSemanticOperation.CANONICAL_JSON_FIELD_VALUE,
        "quantos_canonical_json_field_v1",
        RendererSemanticFunctionKind.SCALAR,
        (
            RendererAbiValueKind.CANONICAL_JSON_TEXT,
            RendererAbiValueKind.JSON_PATH_TEXT,
            RendererAbiValueKind.TYPE_TAG_TEXT,
        ),
        RendererAbiValueKind.SQL_SCALAR,
        "quantos.renderer.canonical-json-field.v1",
    ),
    _renderer_semantic_function_abi(
        RendererSemanticOperation.RELATED_CANONICAL_JSON_ARRAY_VALUE,
        "quantos_canonical_json_array_value_v1",
        RendererSemanticFunctionKind.SCALAR,
        (
            RendererAbiValueKind.CANONICAL_JSON_TEXT,
            RendererAbiValueKind.JSON_PATH_TEXT,
            RendererAbiValueKind.TYPE_TAG_TEXT,
            RendererAbiValueKind.PROJECTION_TAG_TEXT,
            RendererAbiValueKind.OPTIONAL_INTEGER,
        ),
        RendererAbiValueKind.SQL_SCALAR,
        "quantos.renderer.canonical-json-array-value.v1",
    ),
    _renderer_semantic_function_abi(
        RendererSemanticOperation.RELATED_CANONICAL_JSON_ARRAY_LENGTH,
        "quantos_canonical_json_array_length_v1",
        RendererSemanticFunctionKind.SCALAR,
        (
            RendererAbiValueKind.CANONICAL_JSON_TEXT,
            RendererAbiValueKind.JSON_PATH_TEXT,
            RendererAbiValueKind.TYPE_TAG_TEXT,
        ),
        RendererAbiValueKind.INTEGER,
        "quantos.renderer.canonical-json-array-length.v1",
    ),
    _renderer_semantic_function_abi(
        RendererSemanticOperation.RELATED_CANONICAL_JSON_ARRAY_CONTAINS,
        "quantos_canonical_json_array_contains_v1",
        RendererSemanticFunctionKind.SCALAR,
        (
            RendererAbiValueKind.CANONICAL_JSON_TEXT,
            RendererAbiValueKind.JSON_PATH_TEXT,
            RendererAbiValueKind.TYPE_TAG_TEXT,
            RendererAbiValueKind.SQL_SCALAR,
        ),
        RendererAbiValueKind.BOOLEAN_INTEGER,
        "quantos.renderer.canonical-json-array-contains.v1",
    ),
    _renderer_semantic_function_abi(
        RendererSemanticOperation.CANONICAL_JSON_PAYLOAD_HASH,
        "quantos_canonical_json_sha256_v1",
        RendererSemanticFunctionKind.SCALAR,
        (
            RendererAbiValueKind.CANONICAL_JSON_TEXT,
            RendererAbiValueKind.CANONICAL_JSON_TEXT,
        ),
        RendererAbiValueKind.SHA256_HEX_TEXT,
        "quantos.renderer.canonical-json-sha256.v1",
    ),
    _renderer_semantic_function_abi(
        RendererSemanticOperation.CANONICAL_ORDERED_VALUE_HASH,
        "quantos_canonical_ordered_value_sha256_v1",
        RendererSemanticFunctionKind.AGGREGATE,
        (RendererAbiValueKind.SQL_SCALAR,),
        RendererAbiValueKind.SHA256_HEX_TEXT,
        "quantos.renderer.canonical-ordered-value-sha256.v1",
    ),
    _renderer_semantic_function_abi(
        RendererSemanticOperation.MEAN_NET_RETURN_DIFFERENTIAL,
        "quantos_mean_net_return_differential_v1",
        RendererSemanticFunctionKind.SCALAR,
        (
            RendererAbiValueKind.CANONICAL_JSON_TEXT,
            RendererAbiValueKind.CANONICAL_JSON_TEXT,
        ),
        RendererAbiValueKind.REAL_IEEE754,
        "quantos.renderer.mean-net-return-differential.v1",
    ),
    _renderer_semantic_function_abi(
        RendererSemanticOperation.PREFIX_STREAM_EXTENSION,
        "quantos_prefix_stream_extension_v1",
        RendererSemanticFunctionKind.SCALAR,
        (
            RendererAbiValueKind.SHA256_HEX_TEXT,
            RendererAbiValueKind.INTEGER,
            RendererAbiValueKind.INTEGER,
            RendererAbiValueKind.SHA256_HEX_TEXT,
            RendererAbiValueKind.INTEGER,
            RendererAbiValueKind.INTEGER,
            RendererAbiValueKind.INTEGER,
            RendererAbiValueKind.INTEGER,
            RendererAbiValueKind.SHA256_HEX_TEXT,
            RendererAbiValueKind.INTEGER,
            RendererAbiValueKind.TEXT,
            RendererAbiValueKind.TEXT,
            RendererAbiValueKind.TEXT,
            RendererAbiValueKind.TEXT,
        ),
        RendererAbiValueKind.BOOLEAN_INTEGER,
        "quantos.renderer.prefix-stream-extension.v1",
    ),
    _renderer_semantic_function_abi(
        RendererSemanticOperation.LATEST_PER_DOMAIN_POLICY,
        "quantos_any_grid_disagreement_max_p_v1",
        RendererSemanticFunctionKind.AGGREGATE,
        (
            RendererAbiValueKind.INTEGER,
            RendererAbiValueKind.INTEGER,
            RendererAbiValueKind.SHA256_HEX_TEXT,
            RendererAbiValueKind.SHA256_HEX_TEXT,
            RendererAbiValueKind.TEXT,
            RendererAbiValueKind.TEXT,
            RendererAbiValueKind.SHA256_HEX_TEXT,
        ),
        RendererAbiValueKind.CANONICAL_JSON_TEXT,
        "quantos.renderer.any-grid-disagreement-max-p.v1",
    ),
)


class StructuralVerificationOperation(_StrEnum):
    RECOMPUTE_ROW_HASH = "RECOMPUTE_ROW_HASH"
    VERIFY_ALL_FOREIGN_KEYS = "VERIFY_ALL_FOREIGN_KEYS"
    VERIFY_GENESIS_FOREIGN_KEY = "VERIFY_GENESIS_FOREIGN_KEY"
    VERIFY_HASH_CHAIN = "VERIFY_HASH_CHAIN"
    VERIFY_TERMINAL_HEAD = "VERIFY_TERMINAL_HEAD"
    VERIFY_PRIMARY_KEY_TOTALITY = "VERIFY_PRIMARY_KEY_TOTALITY"
    VERIFY_UNIQUE_INDEX = "VERIFY_UNIQUE_INDEX"
    VERIFY_CHECK_CONSTRAINT = "VERIFY_CHECK_CONSTRAINT"
    REPLAY_INSERT_GUARD = "REPLAY_INSERT_GUARD"
    VERIFY_EXACT_FOREIGN_KEY = "VERIFY_EXACT_FOREIGN_KEY"
    VERIFY_DELEGATED_EXTENSION = "VERIFY_DELEGATED_EXTENSION"


STRUCTURAL_VERIFICATION_OPERATION_BY_SOURCE = (
    (
        StructuralVerificationSourceKind.ROW_HASH_SPEC,
        StructuralVerificationOperation.RECOMPUTE_ROW_HASH,
    ),
    (
        StructuralVerificationSourceKind.ALL_FOREIGN_KEYS,
        StructuralVerificationOperation.VERIFY_ALL_FOREIGN_KEYS,
    ),
    (
        StructuralVerificationSourceKind.GENESIS_FOREIGN_KEY,
        StructuralVerificationOperation.VERIFY_GENESIS_FOREIGN_KEY,
    ),
    (
        StructuralVerificationSourceKind.CHAIN_SPEC,
        StructuralVerificationOperation.VERIFY_HASH_CHAIN,
    ),
    (
        StructuralVerificationSourceKind.TERMINAL_HEAD,
        StructuralVerificationOperation.VERIFY_TERMINAL_HEAD,
    ),
    (
        StructuralVerificationSourceKind.PRIMARY_KEY,
        StructuralVerificationOperation.VERIFY_PRIMARY_KEY_TOTALITY,
    ),
    (
        StructuralVerificationSourceKind.UNIQUE_INDEX,
        StructuralVerificationOperation.VERIFY_UNIQUE_INDEX,
    ),
    (
        StructuralVerificationSourceKind.CHECK_CONSTRAINT,
        StructuralVerificationOperation.VERIFY_CHECK_CONSTRAINT,
    ),
    (
        StructuralVerificationSourceKind.TRIGGER_POLICY,
        StructuralVerificationOperation.REPLAY_INSERT_GUARD,
    ),
    (
        StructuralVerificationSourceKind.EXACT_FOREIGN_KEY,
        StructuralVerificationOperation.VERIFY_EXACT_FOREIGN_KEY,
    ),
    (
        StructuralVerificationSourceKind.DELEGATED_EXTENSION,
        StructuralVerificationOperation.VERIFY_DELEGATED_EXTENSION,
    ),
)


class ImplementationTargetKind(_StrEnum):
    SQLITE_SEMANTIC_FUNCTION = "SQLITE_SEMANTIC_FUNCTION"
    NAMED_VERIFIER = "NAMED_VERIFIER"
    STRUCTURAL_HANDLER = "STRUCTURAL_HANDLER"
    EXTERNAL_PROVIDER_TRUST_ROOT = "EXTERNAL_PROVIDER_TRUST_ROOT"


class ImplementationEvidencePolicy(_StrEnum):
    REQUIRE_ACTUAL_SHA256 = "REQUIRE_ACTUAL_SHA256"
    EXCLUDED_DEPLOYMENT_TRUST_ROOT = "EXCLUDED_DEPLOYMENT_TRUST_ROOT"


class ImplementationEvidenceHashMethod(_StrEnum):
    PYTHON_CALLABLE_AND_CANONICAL_BINDING_SHA256_V1 = (
        "PYTHON_CALLABLE_AND_CANONICAL_BINDING_SHA256_V1"
    )
    NONE = "NONE"


_IMPLEMENTATION_EVIDENCE_HASH_VERSION = "QUANTOS_IMPLEMENTATION_EVIDENCE_HASH_V1"


@_dataclass(frozen=True, slots=True)
class ImplementationEvidenceRequirementSpec:
    """One exact implementation target that a sealed Pass-M manifest must bind.

    Internal hashes cover both the callable source closure and the canonical
    bytes of every bound ABI/rule/profile/plan.  Hashing only a dispatcher or
    only the ABI is explicitly insufficient.  External providers are recorded
    as deployment trust roots and never masquerade as local implementation
    evidence.
    """

    target_kind: ImplementationTargetKind
    target_id: str
    implementation_id: str | None
    evidence_policy: ImplementationEvidencePolicy
    hash_method: ImplementationEvidenceHashMethod
    hash_method_version: str
    canonical_binding_keys: tuple[str, ...]
    semantic_operation: RendererSemanticOperation | None = None
    sqlite_name: str | None = None
    semantic_contract_id: str | None = None
    verifier_code: str | None = None
    structural_operation: StructuralVerificationOperation | None = None
    provider_contract_type: str | None = None
    provider_capability: str | None = None
    requires_callable_closure: bool = True
    requires_canonical_bound_spec: bool = True

    def __post_init__(self) -> None:
        _ensure_enum(
            self.target_kind,
            ImplementationTargetKind,
            "implementation target kind",
        )
        _ensure_token(self.target_id, "implementation target ID")
        if self.implementation_id is not None:
            _ensure_token(self.implementation_id, "implementation ID")
        _ensure_enum(
            self.evidence_policy,
            ImplementationEvidencePolicy,
            "implementation evidence policy",
        )
        _ensure_enum(
            self.hash_method,
            ImplementationEvidenceHashMethod,
            "implementation evidence hash method",
        )
        if type(self.hash_method_version) is not str or not self.hash_method_version:
            raise ValueError("implementation evidence hash method version is required")
        binding_keys = _ensure_tuple(
            self.canonical_binding_keys,
            "implementation canonical binding keys",
            nonempty=True,
        )
        for binding_key in binding_keys:
            _ensure_token(binding_key, "implementation canonical binding key")
        _ensure_unique(binding_keys, "implementation canonical binding keys")
        _ensure_bool(
            self.requires_callable_closure,
            "implementation requires callable closure",
        )
        _ensure_bool(
            self.requires_canonical_bound_spec,
            "implementation requires canonical bound spec",
        )
        if not self.requires_canonical_bound_spec:
            raise ValueError("implementation evidence must bind canonical target bytes")

        optional_fields = (
            self.semantic_operation,
            self.sqlite_name,
            self.semantic_contract_id,
            self.verifier_code,
            self.structural_operation,
            self.provider_contract_type,
            self.provider_capability,
        )
        if self.target_kind is ImplementationTargetKind.SQLITE_SEMANTIC_FUNCTION:
            if type(self.semantic_operation) is not RendererSemanticOperation:
                raise TypeError("semantic implementation requires its exact operation")
            if self.sqlite_name is None or self.semantic_contract_id is None:
                raise ValueError("semantic implementation requires SQLite name and contract")
            _ensure_identifier(self.sqlite_name, "semantic implementation SQLite name")
            _ensure_token(self.semantic_contract_id, "semantic implementation contract")
            if any(value is not None for value in optional_fields[3:]):
                raise ValueError("semantic implementation forbids non-semantic target metadata")
            expected_target = f"SQLITE_SEMANTIC::{self.semantic_operation.value}"
            expected_implementation = f"FAMILY_SCHEMA_SQLITE_SEMANTIC::{self.sqlite_name}"
        elif self.target_kind is ImplementationTargetKind.NAMED_VERIFIER:
            if self.verifier_code is None:
                raise ValueError("named verifier implementation requires verifier_code")
            _ensure_token(self.verifier_code, "named verifier code")
            if any(
                value is not None
                for value in (
                    self.semantic_operation,
                    self.sqlite_name,
                    self.semantic_contract_id,
                    self.structural_operation,
                    self.provider_contract_type,
                    self.provider_capability,
                )
            ):
                raise ValueError("named verifier forbids unrelated target metadata")
            expected_target = f"NAMED_VERIFIER::{self.verifier_code}"
            expected_implementation = f"FAMILY_SCHEMA_NAMED_VERIFIER::{self.verifier_code}"
        elif self.target_kind is ImplementationTargetKind.STRUCTURAL_HANDLER:
            if type(self.structural_operation) is not StructuralVerificationOperation:
                raise TypeError("structural implementation requires its exact operation")
            if any(
                value is not None
                for value in (
                    self.semantic_operation,
                    self.sqlite_name,
                    self.semantic_contract_id,
                    self.verifier_code,
                    self.provider_contract_type,
                    self.provider_capability,
                )
            ):
                raise ValueError("structural handler forbids unrelated target metadata")
            expected_target = f"STRUCTURAL_HANDLER::{self.structural_operation.value}"
            expected_implementation = (
                f"FAMILY_SCHEMA_STRUCTURAL_HANDLER::{self.structural_operation.value}"
            )
        else:
            if self.provider_contract_type is None or self.provider_capability is None:
                raise ValueError("external trust root requires contract and capability")
            _ensure_token(self.provider_contract_type, "external provider contract type")
            _ensure_token(self.provider_capability, "external provider capability")
            if any(value is not None for value in optional_fields[:5]):
                raise ValueError("external trust root forbids local implementation metadata")
            expected_target = (
                "EXTERNAL_PROVIDER::"
                f"{self.provider_contract_type}::{self.provider_capability}"
            )
            expected_implementation = None

        if self.target_id != expected_target:
            raise ValueError("implementation target ID does not match typed target metadata")
        if self.implementation_id != expected_implementation:
            raise ValueError("implementation ID does not match typed target metadata")

        if self.target_kind is ImplementationTargetKind.EXTERNAL_PROVIDER_TRUST_ROOT:
            if self.evidence_policy is not (
                ImplementationEvidencePolicy.EXCLUDED_DEPLOYMENT_TRUST_ROOT
            ):
                raise ValueError("external provider must be an excluded deployment trust root")
            if self.hash_method is not ImplementationEvidenceHashMethod.NONE:
                raise ValueError("external provider cannot claim a local implementation hash")
            if self.hash_method_version != "NONE":
                raise ValueError("external provider hash method version must be NONE")
            if self.requires_callable_closure:
                raise ValueError("external provider cannot require a local callable closure")
            return

        if self.evidence_policy is not ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256:
            raise ValueError("internal implementation targets require actual SHA-256 evidence")
        if self.hash_method is not (
            ImplementationEvidenceHashMethod.PYTHON_CALLABLE_AND_CANONICAL_BINDING_SHA256_V1
        ):
            raise ValueError(
                "internal implementation evidence must hash callable and canonical bindings"
            )
        if self.hash_method_version != _IMPLEMENTATION_EVIDENCE_HASH_VERSION:
            raise ValueError("unknown implementation evidence hash method version")
        if not self.requires_callable_closure:
            raise ValueError("internal implementation evidence requires callable closure")


@_dataclass(frozen=True, slots=True)
class ImplementationEvidenceRequirementCatalog:
    entries: tuple[ImplementationEvidenceRequirementSpec, ...]
    catalog_version: str = "QUANTOS_IMPLEMENTATION_EVIDENCE_REQUIREMENTS_V1"

    def __post_init__(self) -> None:
        if self.catalog_version != "QUANTOS_IMPLEMENTATION_EVIDENCE_REQUIREMENTS_V1":
            raise ValueError("unknown implementation evidence requirement catalog version")
        entries = _ensure_tuple(
            self.entries,
            "implementation evidence requirements",
            nonempty=True,
        )
        _ensure_exact_items(
            entries,
            ImplementationEvidenceRequirementSpec,
            "implementation evidence requirements",
        )
        _ensure_unique(
            tuple((entry.target_kind, entry.target_id) for entry in entries),
            "implementation evidence requirement targets",
        )


@_dataclass(frozen=True, slots=True)
class ImplementationEvidenceBinding:
    target_kind: ImplementationTargetKind
    target_id: str
    implementation_id: str
    implementation_sha256: str
    hash_method: ImplementationEvidenceHashMethod
    hash_method_version: str

    def __post_init__(self) -> None:
        _ensure_enum(self.target_kind, ImplementationTargetKind, "evidence target kind")
        _ensure_token(self.target_id, "evidence target ID")
        _ensure_token(self.implementation_id, "evidence implementation ID")
        _ensure_sha256(self.implementation_sha256, "implementation evidence hash")
        _ensure_enum(
            self.hash_method,
            ImplementationEvidenceHashMethod,
            "implementation evidence hash method",
        )
        if self.hash_method is ImplementationEvidenceHashMethod.NONE:
            raise ValueError("actual implementation evidence cannot use hash method NONE")
        if self.hash_method_version != _IMPLEMENTATION_EVIDENCE_HASH_VERSION:
            raise ValueError("unknown actual implementation evidence hash method version")


@_dataclass(frozen=True, slots=True)
class ImplementationEvidenceCatalog:
    """Actual evidence bound one-for-one to all internal requirements."""

    requirements: ImplementationEvidenceRequirementCatalog
    bindings: tuple[ImplementationEvidenceBinding, ...]
    catalog_version: str = "QUANTOS_BOUND_IMPLEMENTATION_EVIDENCE_V1"

    def __post_init__(self) -> None:
        if type(self.requirements) is not ImplementationEvidenceRequirementCatalog:
            raise TypeError("bound evidence requires the exact requirement catalog")
        if self.catalog_version != "QUANTOS_BOUND_IMPLEMENTATION_EVIDENCE_V1":
            raise ValueError("unknown bound implementation evidence catalog version")
        bindings = _ensure_tuple(self.bindings, "implementation evidence bindings")
        _ensure_exact_items(
            bindings,
            ImplementationEvidenceBinding,
            "implementation evidence bindings",
        )
        keys = tuple((item.target_kind, item.target_id) for item in bindings)
        _ensure_unique(keys, "implementation evidence binding targets")
        required = tuple(
            item
            for item in self.requirements.entries
            if item.evidence_policy is ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256
        )
        external_keys = {
            (item.target_kind, item.target_id)
            for item in self.requirements.entries
            if item.evidence_policy
            is ImplementationEvidencePolicy.EXCLUDED_DEPLOYMENT_TRUST_ROOT
        }
        if external_keys.intersection(keys):
            raise ValueError("external provider trust roots forbid implementation evidence")
        required_keys = tuple((item.target_kind, item.target_id) for item in required)
        if keys != required_keys:
            raise ValueError(
                "actual implementation evidence must exactly cover internal requirements"
            )
        for requirement, binding in zip(required, bindings, strict=True):
            if (
                binding.implementation_id != requirement.implementation_id
                or binding.hash_method is not requirement.hash_method
                or binding.hash_method_version != requirement.hash_method_version
            ):
                raise ValueError(
                    "actual implementation evidence does not match its requirement"
                )


class RendererNullLowering(_StrEnum):
    CHECK_VALIDITY_NULL_IS_FALSE = "CHECK_VALIDITY_NULL_IS_FALSE"
    INSERT_VIOLATION_NULL_IS_TRUE = "INSERT_VIOLATION_NULL_IS_TRUE"
    VERIFICATION_NON_TRUE_IS_FAILURE = "VERIFICATION_NON_TRUE_IS_FAILURE"
    FUNCTION_FAILURE_RETURNS_NULL = "FUNCTION_FAILURE_RETURNS_NULL"


@_dataclass(frozen=True, slots=True)
class RendererNullLoweringContract:
    check_constraint: RendererNullLowering = (
        RendererNullLowering.CHECK_VALIDITY_NULL_IS_FALSE
    )
    insert_guard: RendererNullLowering = (
        RendererNullLowering.INSERT_VIOLATION_NULL_IS_TRUE
    )
    verification: RendererNullLowering = (
        RendererNullLowering.VERIFICATION_NON_TRUE_IS_FAILURE
    )
    function_failure: RendererNullLowering = (
        RendererNullLowering.FUNCTION_FAILURE_RETURNS_NULL
    )

    def __post_init__(self) -> None:
        expected = (
            RendererNullLowering.CHECK_VALIDITY_NULL_IS_FALSE,
            RendererNullLowering.INSERT_VIOLATION_NULL_IS_TRUE,
            RendererNullLowering.VERIFICATION_NON_TRUE_IS_FAILURE,
            RendererNullLowering.FUNCTION_FAILURE_RETURNS_NULL,
        )
        actual = (
            self.check_constraint,
            self.insert_guard,
            self.verification,
            self.function_failure,
        )
        for value in actual:
            _ensure_enum(value, RendererNullLowering, "renderer NULL lowering")
        if actual != expected:
            raise ValueError("renderer NULL lowering must remain fail-closed")


FAMILY_SCHEMA_RENDERER_NULL_LOWERING = RendererNullLoweringContract()


class RelatedLookupSqlLowering(_StrEnum):
    EXACT_COUNT_THEN_SCALAR_SUBQUERY_V1 = "EXACT_COUNT_THEN_SCALAR_SUBQUERY_V1"


@_dataclass(frozen=True, slots=True)
class RelatedLookupLoweringContract:
    sql_lowering: RelatedLookupSqlLowering = (
        RelatedLookupSqlLowering.EXACT_COUNT_THEN_SCALAR_SUBQUERY_V1
    )
    cardinality: RelatedLookupCardinality = RelatedLookupCardinality.EXACTLY_ONE
    key_requirement: RelatedLookupKeyRequirement = (
        RelatedLookupKeyRequirement.PRIMARY_KEY_OR_EXACT_UNIQUE
    )
    rejects_zero_rows: bool = True
    rejects_multiple_rows: bool = True
    requires_compile_time_key_proof: bool = True

    def __post_init__(self) -> None:
        _ensure_enum(
            self.sql_lowering,
            RelatedLookupSqlLowering,
            "related lookup SQL lowering",
        )
        if self.sql_lowering is not (
            RelatedLookupSqlLowering.EXACT_COUNT_THEN_SCALAR_SUBQUERY_V1
        ):
            raise ValueError("related lookup SQL lowering must prove exact cardinality")
        _ensure_enum(self.cardinality, RelatedLookupCardinality, "related lookup cardinality")
        if self.cardinality is not RelatedLookupCardinality.EXACTLY_ONE:
            raise ValueError("related lookup cardinality must be EXACTLY_ONE")
        _ensure_enum(
            self.key_requirement,
            RelatedLookupKeyRequirement,
            "related lookup key requirement",
        )
        if self.key_requirement is not (
            RelatedLookupKeyRequirement.PRIMARY_KEY_OR_EXACT_UNIQUE
        ):
            raise ValueError("related lookup requires a primary or exact UNIQUE key")
        for label, value in (
            ("zero rows", self.rejects_zero_rows),
            ("multiple rows", self.rejects_multiple_rows),
            ("compile-time key proof", self.requires_compile_time_key_proof),
        ):
            _ensure_bool(value, f"related lookup rejects {label}")
            if not value:
                raise ValueError(f"related lookup must reject or prove {label}")


FAMILY_SCHEMA_RELATED_LOOKUP_LOWERING = RelatedLookupLoweringContract()


class InsertStateMachineSqlLowering(_StrEnum):
    EXACT_PREDECESSOR_TRANSITION_V1 = "EXACT_PREDECESSOR_TRANSITION_V1"


class InsertStateMachinePredicateComposition(_StrEnum):
    OR_WITH_DECLARED_VIOLATION = "OR_WITH_DECLARED_VIOLATION"


@_dataclass(frozen=True, slots=True)
class InsertStateMachineLoweringContract:
    sql_lowering: InsertStateMachineSqlLowering = (
        InsertStateMachineSqlLowering.EXACT_PREDECESSOR_TRANSITION_V1
    )
    predicate_composition: InsertStateMachinePredicateComposition = (
        InsertStateMachinePredicateComposition.OR_WITH_DECLARED_VIOLATION
    )
    initial_sequence_value: int = 0
    transition_sequence_step: int = 1
    requires_sequence_column: bool = True
    matches_all_identity_columns: bool = True
    rejects_missing_or_ambiguous_predecessor: bool = True
    rejects_outgoing_transition_from_terminal: bool = True

    def __post_init__(self) -> None:
        _ensure_enum(
            self.sql_lowering,
            InsertStateMachineSqlLowering,
            "insert state-machine SQL lowering",
        )
        if self.sql_lowering is not (
            InsertStateMachineSqlLowering.EXACT_PREDECESSOR_TRANSITION_V1
        ):
            raise ValueError("insert state-machine SQL lowering must be exact")
        _ensure_enum(
            self.predicate_composition,
            InsertStateMachinePredicateComposition,
            "insert state-machine predicate composition",
        )
        if self.predicate_composition is not (
            InsertStateMachinePredicateComposition.OR_WITH_DECLARED_VIOLATION
        ):
            raise ValueError("state-machine rejection must combine with the declared violation")
        if type(self.initial_sequence_value) is not int or self.initial_sequence_value != 0:
            raise ValueError("state-machine initial sequence must be exactly zero")
        if type(self.transition_sequence_step) is not int or self.transition_sequence_step != 1:
            raise ValueError("state-machine sequence step must be exactly one")
        for label, value in (
            ("sequence column", self.requires_sequence_column),
            ("all identity columns", self.matches_all_identity_columns),
            (
                "missing or ambiguous predecessor rejection",
                self.rejects_missing_or_ambiguous_predecessor,
            ),
            (
                "terminal outgoing-transition rejection",
                self.rejects_outgoing_transition_from_terminal,
            ),
        ):
            _ensure_bool(value, f"state-machine {label}")
            if not value:
                raise ValueError(f"state-machine requires {label}")


FAMILY_SCHEMA_INSERT_STATE_MACHINE_LOWERING = (
    InsertStateMachineLoweringContract()
)


FAMILY_SCHEMA_UNSUPPORTED_PREDICATE_NODES = (
    RelationPolicyAggregateExpression.__name__,
)


@_dataclass(frozen=True, slots=True)
class RendererGrammarContract:
    schema_version: int = 3
    ast_version: str = "QUANTOS_FAMILY_SCHEMA_AST_V1"
    renderer_version: str = "QUANTOS_SQLITE_SCHEMA_RENDERER_V1"
    keyword_case: RendererKeywordCase = RendererKeywordCase.UPPER
    identifier_quoting: RendererIdentifierQuoting = RendererIdentifierQuoting.DOUBLE_QUOTE
    whitespace: RendererWhitespace = RendererWhitespace.SINGLE_ASCII_SPACE
    line_ending: RendererLineEnding = RendererLineEnding.LF
    statement_termination: RendererStatementTermination = RendererStatementTermination.SEMICOLON_LF
    object_order: RendererObjectOrder = RendererObjectOrder.COMPONENT_TABLE_INDEX_TRIGGER
    allows_if_not_exists: bool = False
    allows_executescript: bool = False
    allows_transaction_control: bool = False
    sqlite_runtime: SqliteRuntimeProfile = FAMILY_SCHEMA_SQLITE_RUNTIME_PROFILE
    semantic_function_abis: tuple[RendererSemanticFunctionAbi, ...] = (
        FAMILY_SCHEMA_RENDERER_SEMANTIC_FUNCTION_ABIS
    )
    null_lowering: RendererNullLoweringContract = (
        FAMILY_SCHEMA_RENDERER_NULL_LOWERING
    )
    related_lookup_lowering: RelatedLookupLoweringContract = (
        FAMILY_SCHEMA_RELATED_LOOKUP_LOWERING
    )
    state_machine_lowering: InsertStateMachineLoweringContract = (
        FAMILY_SCHEMA_INSERT_STATE_MACHINE_LOWERING
    )
    unsupported_predicate_nodes: tuple[str, ...] = (
        FAMILY_SCHEMA_UNSUPPORTED_PREDICATE_NODES
    )

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 3:
            raise ValueError("renderer schema_version must be exactly 3")
        if self.ast_version != "QUANTOS_FAMILY_SCHEMA_AST_V1":
            raise ValueError("unknown family schema AST version")
        if self.renderer_version != "QUANTOS_SQLITE_SCHEMA_RENDERER_V1":
            raise ValueError("unknown family schema renderer version")
        _ensure_enum(self.keyword_case, RendererKeywordCase, "renderer keyword case")
        _ensure_enum(
            self.identifier_quoting,
            RendererIdentifierQuoting,
            "renderer identifier quoting",
        )
        _ensure_enum(self.whitespace, RendererWhitespace, "renderer whitespace")
        _ensure_enum(self.line_ending, RendererLineEnding, "renderer line ending")
        _ensure_enum(
            self.statement_termination,
            RendererStatementTermination,
            "renderer statement termination",
        )
        _ensure_enum(self.object_order, RendererObjectOrder, "renderer object order")
        for label, value in (
            ("allows_if_not_exists", self.allows_if_not_exists),
            ("allows_executescript", self.allows_executescript),
            ("allows_transaction_control", self.allows_transaction_control),
        ):
            _ensure_bool(value, label)
            if value:
                raise ValueError(f"canonical renderer contract forbids {label}")
        if type(self.sqlite_runtime) is not SqliteRuntimeProfile or (
            self.sqlite_runtime != FAMILY_SCHEMA_SQLITE_RUNTIME_PROFILE
        ):
            raise ValueError("renderer SQLite runtime profile must be exact")
        abis = _ensure_tuple(
            self.semantic_function_abis,
            "renderer semantic function ABI",
            nonempty=True,
        )
        _ensure_exact_items(
            abis,
            RendererSemanticFunctionAbi,
            "renderer semantic function ABI",
        )
        if abis != FAMILY_SCHEMA_RENDERER_SEMANTIC_FUNCTION_ABIS:
            raise ValueError("renderer semantic function ABI set must be exact")
        if len({item.sqlite_name for item in abis}) != len(abis):
            raise ValueError("renderer semantic function ABI names must be unique")
        if tuple(item.operation for item in abis) != tuple(RendererSemanticOperation):
            raise ValueError("renderer semantic function ABI operations must be complete")
        if type(self.null_lowering) is not RendererNullLoweringContract or (
            self.null_lowering != FAMILY_SCHEMA_RENDERER_NULL_LOWERING
        ):
            raise ValueError("renderer NULL lowering contract must be exact")
        if type(self.related_lookup_lowering) is not RelatedLookupLoweringContract or (
            self.related_lookup_lowering != FAMILY_SCHEMA_RELATED_LOOKUP_LOWERING
        ):
            raise ValueError("renderer related lookup lowering contract must be exact")
        if type(self.state_machine_lowering) is not InsertStateMachineLoweringContract or (
            self.state_machine_lowering != FAMILY_SCHEMA_INSERT_STATE_MACHINE_LOWERING
        ):
            raise ValueError("renderer state-machine lowering contract must be exact")
        if self.unsupported_predicate_nodes != (
            FAMILY_SCHEMA_UNSUPPORTED_PREDICATE_NODES
        ):
            raise ValueError("renderer unsupported predicate node set must be exact")


FAMILY_SCHEMA_RENDERER_GRAMMAR = RendererGrammarContract()


class SqliteValueTag(_StrEnum):
    NULL = "NULL"
    INTEGER = "INTEGER"
    REAL_IEEE754 = "REAL_IEEE754"
    TEXT = "TEXT"
    BLOB = "BLOB"


class OriginTableOrder(_StrEnum):
    EXACT_V2_CATALOG_ORDER = "EXACT_V2_CATALOG_ORDER"


class OriginRowOrder(_StrEnum):
    PRIMARY_KEY_ORDER = "PRIMARY_KEY_ORDER"


class OriginBindingLifetime(_StrEnum):
    ACTIVATION_ONLY = "ACTIVATION_ONLY"


@_dataclass(frozen=True, slots=True)
class OriginProjectionContract:
    projection_version: str = "QUANTOS_V2_ORIGIN_PROJECTION_V1"
    table_order: OriginTableOrder = OriginTableOrder.EXACT_V2_CATALOG_ORDER
    row_order: OriginRowOrder = OriginRowOrder.PRIMARY_KEY_ORDER
    value_tags: tuple[SqliteValueTag, ...] = tuple(SqliteValueTag)
    includes_exact_schema_catalog: bool = True
    includes_table_names: bool = True
    includes_row_counts: bool = True
    includes_chain_heads: bool = True
    includes_owned_sqlite_sequence: bool = True
    normalizes_legacy_values: bool = False
    binding_lifetime: OriginBindingLifetime = OriginBindingLifetime.ACTIVATION_ONLY

    def __post_init__(self) -> None:
        if self.projection_version != "QUANTOS_V2_ORIGIN_PROJECTION_V1":
            raise ValueError("unknown origin projection version")
        _ensure_enum(self.table_order, OriginTableOrder, "origin table order")
        _ensure_enum(self.row_order, OriginRowOrder, "origin row order")
        value_tags = _ensure_tuple(self.value_tags, "origin value tags", nonempty=True)
        _ensure_exact_items(value_tags, SqliteValueTag, "origin value tags")
        if value_tags != tuple(SqliteValueTag):
            raise ValueError("origin projection must cover every SQLite value tag exactly")
        for label, value, expected in (
            ("includes_exact_schema_catalog", self.includes_exact_schema_catalog, True),
            ("includes_table_names", self.includes_table_names, True),
            ("includes_row_counts", self.includes_row_counts, True),
            ("includes_chain_heads", self.includes_chain_heads, True),
            ("includes_owned_sqlite_sequence", self.includes_owned_sqlite_sequence, True),
            ("normalizes_legacy_values", self.normalizes_legacy_values, False),
        ):
            _ensure_bool(value, label)
            if value is not expected:
                raise ValueError(f"origin projection contract fixes {label}={expected}")
        _ensure_enum(
            self.binding_lifetime,
            OriginBindingLifetime,
            "origin binding lifetime",
        )


_GENESIS_PAYLOAD_FIELDS = (
    "singleton_key",
    "authority_instance_id",
    "v2_origin_fingerprint",
    "sealed_v3_manifest_hash",
    "activation_code_manifest_evidence_hash",
    "migration_id",
    "activated_at_us",
    "record_type",
)

_GENESIS_ROW_HASH = RowHashSpec(
    canonical_fields=_GENESIS_PAYLOAD_FIELDS,
    version_literal="FAMILY_AUTHORITY_GENESIS_V1",
    record_hash_column="record_hash",
)


_GENESIS_COLUMNS = (
    ColumnSpec("singleton_key", SqlType.INTEGER, nullable=False),
    ColumnSpec("authority_instance_id", SqlType.TEXT, nullable=False),
    ColumnSpec("v2_origin_fingerprint", SqlType.TEXT, nullable=False),
    ColumnSpec("sealed_v3_manifest_hash", SqlType.TEXT, nullable=False),
    ColumnSpec("activation_code_manifest_evidence_hash", SqlType.TEXT, nullable=False),
    ColumnSpec("migration_id", SqlType.TEXT, nullable=False),
    ColumnSpec("activated_at_us", SqlType.INTEGER, nullable=False),
    ColumnSpec("record_type", SqlType.TEXT, nullable=False),
    ColumnSpec("record_hash", SqlType.TEXT, nullable=False),
)

_GENESIS_CHECKS = (
    CheckConstraintSpec(
        "family_authority_genesis_singleton_check",
        ComparisonExpression(
            ColumnValue("singleton_key"),
            ComparisonOperator.EQ,
            LiteralValue(1),
        ),
    ),
    CheckConstraintSpec(
        "family_authority_genesis_record_type_check",
        ComparisonExpression(
            ColumnValue("record_type"),
            ComparisonOperator.EQ,
            LiteralValue("FAMILY_AUTHORITY_GENESIS_V1"),
        ),
    ),
    CheckConstraintSpec(
        "family_authority_genesis_migration_id_check",
        ComparisonExpression(
            ColumnValue("migration_id"),
            ComparisonOperator.EQ,
            LiteralValue("FAMILY_SCHEMA_V2_TO_V3_V1"),
        ),
    ),
    CheckConstraintSpec(
        "family_authority_genesis_activated_at_us_positive_check",
        ComparisonExpression(
            ColumnValue("activated_at_us"),
            ComparisonOperator.GT,
            LiteralValue(0),
        ),
    ),
)

_GENESIS_INDEXES = (
    IndexSpec(
        name="family_authority_genesis_lineage_unique",
        columns=(
            IndexColumnSpec("authority_instance_id"),
            IndexColumnSpec("record_hash"),
        ),
        unique=True,
    ),
)

_GENESIS_TRIGGERS = (
    TriggerPolicySpec(
        name="family_authority_genesis_no_update",
        kind=TriggerPolicyKind.NO_UPDATE,
        event=TriggerEvent.UPDATE,
        error_code="FAMILY_AUTHORITY_GENESIS_IMMUTABLE",
    ),
    TriggerPolicySpec(
        name="family_authority_genesis_no_delete",
        kind=TriggerPolicyKind.NO_DELETE,
        event=TriggerEvent.DELETE,
        error_code="FAMILY_AUTHORITY_GENESIS_IMMUTABLE",
    ),
)

_GENESIS_VERIFICATION_RULES = (
    VerificationRuleSpec(
        rule_id="family_authority_genesis_row_hash",
        kind=VerificationRuleKind.ROW_HASH,
        columns=_GENESIS_PAYLOAD_FIELDS,
        verifier_code="family_schema.verify_genesis_row_hash_v1",
    ),
    VerificationRuleSpec(
        rule_id="family_authority_genesis_read",
        kind=VerificationRuleKind.GENESIS_BINDING,
        columns=tuple(column.name for column in _GENESIS_COLUMNS),
        verifier_code="family_schema.verify_genesis_read_v1",
    ),
)

FAMILY_AUTHORITY_GENESIS_TABLE_SPEC: AuthorityTableSpec = AuthorityTableSpec(
    table_name="family_authority_genesis",
    columns=_GENESIS_COLUMNS,
    primary_key_columns=("singleton_key",),
    row_hash=_GENESIS_ROW_HASH,
    indexes=_GENESIS_INDEXES,
    triggers=_GENESIS_TRIGGERS,
    checks=_GENESIS_CHECKS,
    verification_rules=_GENESIS_VERIFICATION_RULES,
)


@_dataclass(frozen=True, slots=True)
class FamilyAuthorityGenesisContract:
    table_name: str = "family_authority_genesis"
    singleton_key_column: str = "singleton_key"
    singleton_key_value: int = 1
    record_type_literal: str = "FAMILY_AUTHORITY_GENESIS_V1"
    migration_id_literal: str = "FAMILY_SCHEMA_V2_TO_V3_V1"
    payload_fields: tuple[str, ...] = _GENESIS_PAYLOAD_FIELDS
    record_hash_spec: RowHashSpec = _GENESIS_ROW_HASH
    table_spec: AuthorityTableSpec = FAMILY_AUTHORITY_GENESIS_TABLE_SPEC
    origin_projection: OriginProjectionContract = OriginProjectionContract()
    first_chain_previous_hash_rule: ChainGenesisRule = ChainGenesisRule.FAMILY_AUTHORITY_GENESIS_RECORD_HASH
    requires_expected_authority_instance_id: bool = True
    claims_physical_clone_uniqueness: bool = False

    def __post_init__(self) -> None:
        if self.table_name != "family_authority_genesis":
            raise ValueError("genesis table name is fixed")
        if self.singleton_key_column != "singleton_key":
            raise ValueError("genesis singleton-key column is fixed")
        if type(self.singleton_key_value) is not int or self.singleton_key_value != 1:
            raise ValueError("genesis singleton-key value is fixed at 1")
        if self.record_type_literal != "FAMILY_AUTHORITY_GENESIS_V1":
            raise ValueError("genesis record type is fixed")
        if self.migration_id_literal != "FAMILY_SCHEMA_V2_TO_V3_V1":
            raise ValueError("genesis migration ID is fixed")
        payload = _ensure_tuple(self.payload_fields, "genesis payload fields", nonempty=True)
        if payload != _GENESIS_PAYLOAD_FIELDS:
            raise ValueError("genesis payload field order is fixed")
        if type(self.record_hash_spec) is not RowHashSpec:
            raise TypeError("genesis record_hash_spec must be RowHashSpec")
        if self.record_hash_spec != _GENESIS_ROW_HASH:
            raise ValueError("genesis row-hash contract is fixed")
        if type(self.table_spec) is not AuthorityTableSpec:
            raise TypeError("genesis table_spec must be AuthorityTableSpec")
        if self.table_spec != FAMILY_AUTHORITY_GENESIS_TABLE_SPEC:
            raise ValueError("genesis table spec is fixed")
        if type(self.origin_projection) is not OriginProjectionContract:
            raise TypeError("genesis origin projection contract is required")
        _ensure_enum(
            self.first_chain_previous_hash_rule,
            ChainGenesisRule,
            "first-chain previous-hash rule",
        )
        _ensure_bool(
            self.requires_expected_authority_instance_id,
            "requires_expected_authority_instance_id",
        )
        _ensure_bool(
            self.claims_physical_clone_uniqueness,
            "claims_physical_clone_uniqueness",
        )
        if not self.requires_expected_authority_instance_id:
            raise ValueError("runtime genesis verification requires expected authority identity")
        if self.claims_physical_clone_uniqueness:
            raise ValueError("L2 genesis cannot claim physical-clone uniqueness")


FAMILY_AUTHORITY_GENESIS_CONTRACT = FamilyAuthorityGenesisContract()


@_dataclass(frozen=True, slots=True)
class FamilySchemaActivationInputs:
    authority_instance_id: str
    activation_code_manifest_hash: str
    activated_at_us: int

    def __post_init__(self) -> None:
        validate_authority_instance_id(self.authority_instance_id)
        _ensure_sha256(
            self.activation_code_manifest_hash,
            "activation code-manifest evidence hash",
        )
        if type(self.activated_at_us) is not int or self.activated_at_us <= 0:
            raise ValueError("activated_at_us must be a positive epoch-microsecond integer")


class ActivationSourceCheck(_StrEnum):
    EXACT_SCHEMA_CATALOG = "EXACT_SCHEMA_CATALOG"
    EXACT_OBJECT_NAMESPACE = "EXACT_OBJECT_NAMESPACE"
    INTEGRITY_CHECK = "INTEGRITY_CHECK"
    FOREIGN_KEY_CHECK = "FOREIGN_KEY_CHECK"
    OWNED_SQLITE_SEQUENCE = "OWNED_SQLITE_SEQUENCE"
    EVALUATION_AND_SEARCH_LINK_CHAINS = "EVALUATION_AND_SEARCH_LINK_CHAINS"
    SEARCH_EVENT_CHAINS = "SEARCH_EVENT_CHAINS"
    OUTBOX_AND_DISPATCH_CHAINS = "OUTBOX_AND_DISPATCH_CHAINS"
    IDEMPOTENCY_AND_REFERENTIAL_TOTALITY = "IDEMPOTENCY_AND_REFERENTIAL_TOTALITY"
    LEGACY_LOGICAL_STATE = "LEGACY_LOGICAL_STATE"
    TYPE_TAGGED_ORIGIN_PROJECTION = "TYPE_TAGGED_ORIGIN_PROJECTION"


@_dataclass(frozen=True, slots=True)
class ExactV2ActivationSourceContract:
    source_version: int = 2
    checks: tuple[ActivationSourceCheck, ...] = tuple(ActivationSourceCheck)
    rejects_unknown_objects: bool = True
    preserves_sequence_values: bool = True
    permits_portable_subset_as_substitute: bool = False
    origin_projection: OriginProjectionContract = OriginProjectionContract()

    def __post_init__(self) -> None:
        if type(self.source_version) is not int or self.source_version != 2:
            raise ValueError("activation source_version must be exactly 2")
        checks = _ensure_tuple(self.checks, "activation-source checks", nonempty=True)
        _ensure_exact_items(checks, ActivationSourceCheck, "activation-source checks")
        if checks != tuple(ActivationSourceCheck):
            raise ValueError("exact-v2 activation-source checks are fixed and ordered")
        for label, value, expected in (
            ("rejects_unknown_objects", self.rejects_unknown_objects, True),
            ("preserves_sequence_values", self.preserves_sequence_values, True),
            (
                "permits_portable_subset_as_substitute",
                self.permits_portable_subset_as_substitute,
                False,
            ),
        ):
            _ensure_bool(value, label)
            if value is not expected:
                raise ValueError(f"exact-v2 source contract fixes {label}={expected}")
        if type(self.origin_projection) is not OriginProjectionContract:
            raise TypeError("exact-v2 source requires the origin projection contract")


class ActivationEntrypointPolicy(_StrEnum):
    OFFLINE_DEPLOYMENT_ONLY = "OFFLINE_DEPLOYMENT_ONLY"


class DdlExecutionPolicy(_StrEnum):
    INDIVIDUAL_STATEMENTS = "INDIVIDUAL_STATEMENTS"


class ActivationStep(_StrEnum):
    ENABLE_AND_CONFIRM_FOREIGN_KEYS = "ENABLE_AND_CONFIRM_FOREIGN_KEYS"
    REJECT_EXISTING_TRANSACTION_OR_OWNER = "REJECT_EXISTING_TRANSACTION_OR_OWNER"
    BEGIN_OWNED_IMMEDIATE = "BEGIN_OWNED_IMMEDIATE"
    REVERIFY_EXACT_V2_SOURCE = "REVERIFY_EXACT_V2_SOURCE"
    REJECT_V3_OBJECT_COLLISIONS = "REJECT_V3_OBJECT_COLLISIONS"
    APPLY_SEALED_DDL_STATEMENTS = "APPLY_SEALED_DDL_STATEMENTS"
    INSERT_SINGLE_GENESIS = "INSERT_SINGLE_GENESIS"
    SET_EXACT_V3_VERSION = "SET_EXACT_V3_VERSION"
    VERIFY_IN_TRANSACTION_SCHEMA_AND_GENESIS = "VERIFY_IN_TRANSACTION_SCHEMA_AND_GENESIS"
    FINAL_FAULT_CHECKPOINT = "FINAL_FAULT_CHECKPOINT"
    COMMIT_ONCE = "COMMIT_ONCE"


_ACTIVATION_KILL_CHECKPOINTS = (
    ActivationStep.APPLY_SEALED_DDL_STATEMENTS,
    ActivationStep.INSERT_SINGLE_GENESIS,
    ActivationStep.SET_EXACT_V3_VERSION,
    ActivationStep.VERIFY_IN_TRANSACTION_SCHEMA_AND_GENESIS,
    ActivationStep.FINAL_FAULT_CHECKPOINT,
)


@_dataclass(frozen=True, slots=True)
class ActivationContract:
    source_version: int = 2
    target_version: int = 3
    entrypoint_policy: ActivationEntrypointPolicy = ActivationEntrypointPolicy.OFFLINE_DEPLOYMENT_ONLY
    source_verification: ExactV2ActivationSourceContract = ExactV2ActivationSourceContract()
    steps: tuple[ActivationStep, ...] = tuple(ActivationStep)
    kill_checkpoints: tuple[ActivationStep, ...] = _ACTIVATION_KILL_CHECKPOINTS
    ddl_execution_policy: DdlExecutionPolicy = DdlExecutionPolicy.INDIVIDUAL_STATEMENTS
    foreign_keys_enabled_outside_transaction: bool = True
    implicit_family_migration_allowed: bool = False
    allows_executescript: bool = False
    allows_if_not_exists: bool = False
    commit_count: int = 1
    rollback_count_on_failure: int = 1

    def __post_init__(self) -> None:
        if type(self.source_version) is not int or self.source_version != 2:
            raise ValueError("activation source_version must be exactly 2")
        if type(self.target_version) is not int or self.target_version != 3:
            raise ValueError("activation target_version must be exactly 3")
        _ensure_enum(
            self.entrypoint_policy,
            ActivationEntrypointPolicy,
            "activation entrypoint policy",
        )
        if type(self.source_verification) is not ExactV2ActivationSourceContract:
            raise TypeError("activation source verification contract is required")
        steps = _ensure_tuple(self.steps, "activation steps", nonempty=True)
        _ensure_exact_items(steps, ActivationStep, "activation steps")
        if steps != tuple(ActivationStep):
            raise ValueError("activation transaction sequence is fixed and ordered")
        checkpoints = _ensure_tuple(self.kill_checkpoints, "activation kill checkpoints", nonempty=True)
        _ensure_exact_items(checkpoints, ActivationStep, "activation kill checkpoints")
        if checkpoints != _ACTIVATION_KILL_CHECKPOINTS:
            raise ValueError("activation kill checkpoints are fixed and ordered")
        _ensure_enum(
            self.ddl_execution_policy,
            DdlExecutionPolicy,
            "DDL execution policy",
        )
        for label, value, expected in (
            (
                "foreign_keys_enabled_outside_transaction",
                self.foreign_keys_enabled_outside_transaction,
                True,
            ),
            (
                "implicit_family_migration_allowed",
                self.implicit_family_migration_allowed,
                False,
            ),
            ("allows_executescript", self.allows_executescript, False),
            ("allows_if_not_exists", self.allows_if_not_exists, False),
        ):
            _ensure_bool(value, label)
            if value is not expected:
                raise ValueError(f"activation contract fixes {label}={expected}")
        if type(self.commit_count) is not int or self.commit_count != 1:
            raise ValueError("activation must commit exactly once")
        if type(self.rollback_count_on_failure) is not int or self.rollback_count_on_failure != 1:
            raise ValueError("activation failure must roll back exactly once")


FAMILY_SCHEMA_V3_ACTIVATION_CONTRACT = ActivationContract()


class SchemaVersionBand(_StrEnum):
    BELOW_V2 = "BELOW_V2"
    EXACT_V2 = "EXACT_V2"
    EXACT_V3 = "EXACT_V3"
    FUTURE = "FUTURE"


class VersionOperation(_StrEnum):
    PORTABLE_V2_INSPECTION = "PORTABLE_V2_INSPECTION"
    SEARCH_WRITE = "SEARCH_WRITE"
    FAMILY_ACTIVATE = "FAMILY_ACTIVATE"
    FAMILY_VERIFY = "FAMILY_VERIFY"
    FAMILY_WRITE = "FAMILY_WRITE"


class VersionPermission(_StrEnum):
    REJECT = "REJECT"
    READ_ONLY = "READ_ONLY"
    VERIFY_ONLY = "VERIFY_ONLY"
    WRITE = "WRITE"
    MIGRATE_SEARCH_TO_V2_THEN_REEVALUATE = "MIGRATE_SEARCH_TO_V2_THEN_REEVALUATE"
    EXPLICIT_OFFLINE_ACTIVATION = "EXPLICIT_OFFLINE_ACTIVATION"


class VerifierRequirement(_StrEnum):
    PORTABLE_V2_SUBSET = "PORTABLE_V2_SUBSET"
    EXISTING_SEARCH_MIGRATION = "EXISTING_SEARCH_MIGRATION"
    EXACT_V2_SEARCH_WRITER = "EXACT_V2_SEARCH_WRITER"
    EXACT_V2_ACTIVATION_SOURCE = "EXACT_V2_ACTIVATION_SOURCE"
    FULL_FAMILY_AUTHORITY = "FULL_FAMILY_AUTHORITY"
    EXPECTED_AUTHORITY_INSTANCE_ID = "EXPECTED_AUTHORITY_INSTANCE_ID"
    OFFLINE_DEPLOYMENT_ACTION = "OFFLINE_DEPLOYMENT_ACTION"


@_dataclass(frozen=True, slots=True)
class VersionPermissionRule:
    operation: VersionOperation
    version_band: SchemaVersionBand
    permission: VersionPermission
    verifier_requirements: tuple[VerifierRequirement, ...] = ()

    def __post_init__(self) -> None:
        _ensure_enum(self.operation, VersionOperation, "version operation")
        _ensure_enum(self.version_band, SchemaVersionBand, "schema version band")
        _ensure_enum(self.permission, VersionPermission, "version permission")
        requirements = _ensure_tuple(self.verifier_requirements, "verifier requirements")
        _ensure_exact_items(requirements, VerifierRequirement, "verifier requirements")
        _ensure_unique(requirements, "verifier requirements")
        if self.permission is VersionPermission.REJECT and requirements:
            raise ValueError("rejected version operations cannot name verifier requirements")


_PHASE_A_VERSION_RULES = (
    VersionPermissionRule(
        VersionOperation.PORTABLE_V2_INSPECTION,
        SchemaVersionBand.BELOW_V2,
        VersionPermission.REJECT,
    ),
    VersionPermissionRule(
        VersionOperation.PORTABLE_V2_INSPECTION,
        SchemaVersionBand.EXACT_V2,
        VersionPermission.READ_ONLY,
        (VerifierRequirement.PORTABLE_V2_SUBSET,),
    ),
    VersionPermissionRule(
        VersionOperation.PORTABLE_V2_INSPECTION,
        SchemaVersionBand.EXACT_V3,
        VersionPermission.READ_ONLY,
        (VerifierRequirement.PORTABLE_V2_SUBSET,),
    ),
    VersionPermissionRule(
        VersionOperation.PORTABLE_V2_INSPECTION,
        SchemaVersionBand.FUTURE,
        VersionPermission.READ_ONLY,
        (VerifierRequirement.PORTABLE_V2_SUBSET,),
    ),
    VersionPermissionRule(
        VersionOperation.SEARCH_WRITE,
        SchemaVersionBand.BELOW_V2,
        VersionPermission.MIGRATE_SEARCH_TO_V2_THEN_REEVALUATE,
        (VerifierRequirement.EXISTING_SEARCH_MIGRATION,),
    ),
    VersionPermissionRule(
        VersionOperation.SEARCH_WRITE,
        SchemaVersionBand.EXACT_V2,
        VersionPermission.WRITE,
        (VerifierRequirement.EXACT_V2_SEARCH_WRITER,),
    ),
    VersionPermissionRule(
        VersionOperation.SEARCH_WRITE,
        SchemaVersionBand.EXACT_V3,
        VersionPermission.WRITE,
        (
            VerifierRequirement.EXACT_V2_SEARCH_WRITER,
            VerifierRequirement.FULL_FAMILY_AUTHORITY,
            VerifierRequirement.EXPECTED_AUTHORITY_INSTANCE_ID,
        ),
    ),
    VersionPermissionRule(
        VersionOperation.SEARCH_WRITE,
        SchemaVersionBand.FUTURE,
        VersionPermission.REJECT,
    ),
    VersionPermissionRule(
        VersionOperation.FAMILY_ACTIVATE,
        SchemaVersionBand.BELOW_V2,
        VersionPermission.REJECT,
    ),
    VersionPermissionRule(
        VersionOperation.FAMILY_ACTIVATE,
        SchemaVersionBand.EXACT_V2,
        VersionPermission.EXPLICIT_OFFLINE_ACTIVATION,
        (
            VerifierRequirement.EXACT_V2_ACTIVATION_SOURCE,
            VerifierRequirement.OFFLINE_DEPLOYMENT_ACTION,
        ),
    ),
    VersionPermissionRule(
        VersionOperation.FAMILY_ACTIVATE,
        SchemaVersionBand.EXACT_V3,
        VersionPermission.REJECT,
    ),
    VersionPermissionRule(
        VersionOperation.FAMILY_ACTIVATE,
        SchemaVersionBand.FUTURE,
        VersionPermission.REJECT,
    ),
    VersionPermissionRule(
        VersionOperation.FAMILY_VERIFY,
        SchemaVersionBand.BELOW_V2,
        VersionPermission.REJECT,
    ),
    VersionPermissionRule(
        VersionOperation.FAMILY_VERIFY,
        SchemaVersionBand.EXACT_V2,
        VersionPermission.REJECT,
    ),
    VersionPermissionRule(
        VersionOperation.FAMILY_VERIFY,
        SchemaVersionBand.EXACT_V3,
        VersionPermission.VERIFY_ONLY,
        (
            VerifierRequirement.FULL_FAMILY_AUTHORITY,
            VerifierRequirement.EXPECTED_AUTHORITY_INSTANCE_ID,
        ),
    ),
    VersionPermissionRule(
        VersionOperation.FAMILY_VERIFY,
        SchemaVersionBand.FUTURE,
        VersionPermission.REJECT,
    ),
    VersionPermissionRule(
        VersionOperation.FAMILY_WRITE,
        SchemaVersionBand.BELOW_V2,
        VersionPermission.REJECT,
    ),
    VersionPermissionRule(
        VersionOperation.FAMILY_WRITE,
        SchemaVersionBand.EXACT_V2,
        VersionPermission.REJECT,
    ),
    VersionPermissionRule(
        VersionOperation.FAMILY_WRITE,
        SchemaVersionBand.EXACT_V3,
        VersionPermission.WRITE,
        (
            VerifierRequirement.FULL_FAMILY_AUTHORITY,
            VerifierRequirement.EXPECTED_AUTHORITY_INSTANCE_ID,
        ),
    ),
    VersionPermissionRule(
        VersionOperation.FAMILY_WRITE,
        SchemaVersionBand.FUTURE,
        VersionPermission.REJECT,
    ),
)


@_dataclass(frozen=True, slots=True)
class VersionPermissionContract:
    phase: str = "PHASE_A"
    exact_search_version: int = 2
    exact_family_version: int = 3
    future_min_version: int = 4
    lifecycle_schema_exposed: bool = False
    rules: tuple[VersionPermissionRule, ...] = _PHASE_A_VERSION_RULES

    def __post_init__(self) -> None:
        if self.phase != "PHASE_A":
            raise ValueError("version-permission phase is fixed at PHASE_A")
        if type(self.exact_search_version) is not int or self.exact_search_version != 2:
            raise ValueError("exact search schema version is 2")
        if type(self.exact_family_version) is not int or self.exact_family_version != 3:
            raise ValueError("exact family schema version is 3")
        if type(self.future_min_version) is not int or self.future_min_version != 4:
            raise ValueError("future schema versions begin at 4")
        _ensure_bool(self.lifecycle_schema_exposed, "lifecycle_schema_exposed")
        if self.lifecycle_schema_exposed:
            raise ValueError("Phase A exposes no lifecycle schema")
        rules = _ensure_tuple(self.rules, "version permission rules", nonempty=True)
        _ensure_exact_items(rules, VersionPermissionRule, "version permission rules")
        actual_keys = tuple((rule.operation, rule.version_band) for rule in rules)
        expected_keys = tuple((operation, band) for operation in VersionOperation for band in SchemaVersionBand)
        if len(set(actual_keys)) != len(actual_keys):
            raise ValueError("version permission rules must not overlap")
        if set(actual_keys) != set(expected_keys):
            raise ValueError("version permission matrix must cover every operation and band")


PHASE_A_VERSION_PERMISSION_CONTRACT = VersionPermissionContract()


__all__ = [
    "ActionPolicySemanticRecipeSpec",
    "AuxiliarySemanticRecipeKind",
    "AuxiliarySemanticRecipeSpec",
    "ActivationContract",
    "ActivationEntrypointPolicy",
    "ActivationSourceCheck",
    "ActivationStep",
    "AggregateExpected",
    "ArtifactGraphContractSpec",
    "ArtifactGraphDerivationKind",
    "ArtifactGraphLocatorSourceKind",
    "ArtifactGraphLocatorSpec",
    "AuthorityTableSpec",
    "AuthorityTableExtensionSpec",
    "CanonicalEncoding",
    "CanonicalContentHashGoldenVectorSpec",
    "CanonicalContentHashSemanticRecipeSpec",
    "CanonicalContentSourceKind",
    "CanonicalJsonArrayProjection",
    "CanonicalJsonFailurePolicy",
    "CanonicalJsonFieldValue",
    "CanonicalJsonPayloadHashExpression",
    "CanonicalJsonScalarType",
    "ChainPredecessorExpression",
    "ChainGenesisRule",
    "ChainHeadRule",
    "ChainSpec",
    "CheckConstraintSpec",
    "CheckExpression",
    "Collation",
    "ColumnDefault",
    "ColumnSpec",
    "ColumnValue",
    "ComparisonExpression",
    "ComparisonOperator",
    "ComponentDependencySpec",
    "ContentAddressedArtifactGraphDerivationSpec",
    "ContractDerivationKind",
    "ContractFieldCoverageSpec",
    "ContractFieldDerivationSpec",
    "CoverageKind",
    "DefaultKind",
    "DependencyKind",
    "DdlExecutionPolicy",
    "ExactV2ActivationSourceContract",
    "ExternalAuthorityContinuitySpec",
    "ExternalAuthorityClaimProfileSpec",
    "ExternalAuthorityReferenceKind",
    "ExternalContinuityBranchSelectorSpec",
    "ExternalContinuityChronologyBranchSpec",
    "ExternalContinuityProviderInputSourceKind",
    "ExternalContinuityProviderInputSpec",
    "ExternalContinuityReadBindingSpec",
    "ExternalContinuityRowInputKind",
    "ExternalContinuityRowInputSpec",
    "ExternalContinuitySemanticKind",
    "ExternalContinuitySemanticProfileSpec",
    "ExternalContinuityStatusBranchKind",
    "ExternalContinuityTimeComparisonSpec",
    "ExternalFieldBindingSpec",
    "ExternalProviderClaimOperator",
    "ExternalProviderCapabilityRequirementSpec",
    "ExternalProviderFieldEqualitySpec",
    "ExternalProviderResolutionStage",
    "ExternalProviderSemanticClaimSpec",
    "FAMILY_AUTHORITY_GENESIS_CONTRACT",
    "FAMILY_AUTHORITY_GENESIS_TABLE_SPEC",
    "FAMILY_SCHEMA_INSERT_STATE_MACHINE_LOWERING",
    "FAMILY_SCHEMA_UNSUPPORTED_PREDICATE_NODES",
    "FAMILY_SCHEMA_RELATED_LOOKUP_LOWERING",
    "FAMILY_SCHEMA_RENDERER_GRAMMAR",
    "FAMILY_SCHEMA_RENDERER_NULL_LOWERING",
    "FAMILY_SCHEMA_RENDERER_SEMANTIC_FUNCTION_ABIS",
    "FAMILY_SCHEMA_SQLITE_RUNTIME_PROFILE",
    "FAMILY_SCHEMA_V3_ACTIVATION_CONTRACT",
    "FamilyAuthorityGenesisContract",
    "FamilySchemaActivationInputs",
    "ForeignKeyAction",
    "ForeignKeyDeferrability",
    "ForeignKeySpec",
    "IndexColumnSpec",
    "IndexSpec",
    "IdentityDerivationGoldenVectorSpec",
    "IdentityDerivationPayloadShape",
    "IdentityDerivationSemanticRecipeSpec",
    "IdentityDigestRepresentation",
    "IdentityIssuance",
    "IdentityPredecessorSourceKind",
    "IdentityPredecessorSourceSpec",
    "ImplementationEvidenceBinding",
    "ImplementationEvidenceCatalog",
    "ImplementationEvidenceHashMethod",
    "ImplementationEvidencePolicy",
    "ImplementationEvidenceRequirementCatalog",
    "ImplementationEvidenceRequirementSpec",
    "ImplementationTargetKind",
    "InsertStateMachineSpec",
    "InsertStateMachineLoweringContract",
    "InsertStateMachinePredicateComposition",
    "InsertStateMachineSqlLowering",
    "InsertStateTransition",
    "InsertionPosition",
    "LiteralValue",
    "LogicalExpression",
    "LogicalOperator",
    "LookupBinding",
    "MembershipExpression",
    "NotExpression",
    "NullExpression",
    "NullOperator",
    "OrdinalOffsetValue",
    "OriginBindingLifetime",
    "OrderedColumnInsertionSpec",
    "OrderedFieldInsertionSpec",
    "OriginProjectionContract",
    "OriginRowOrder",
    "OriginTableOrder",
    "PHASE_A_VERSION_PERMISSION_CONTRACT",
    "PredicateExpression",
    "PrefixPredecessorCardinality",
    "PrefixStreamExtensionExpression",
    "QualifiedColumnValue",
    "RelatedCanonicalJsonArrayContainsExpression",
    "RelatedCanonicalJsonArrayLengthValueSpec",
    "RelatedCanonicalJsonArrayValueSpec",
    "RelationAggregateIncludeValueSpec",
    "RelationExistsExpression",
    "RelationAggregateExpression",
    "RelationAggregateKind",
    "RelationAggregateOrderSpec",
    "RelationArgExtremumExpression",
    "RelationExtremum",
    "RelationJoinAggregateExpression",
    "RelationJoinAggregateOrderSpec",
    "RelationJoinCardinality",
    "RelationJoinEqualitySpec",
    "RelationJoinSpec",
    "RelationChainPrefixExpression",
    "RelationChainPrefixProof",
    "RelationLatestPerDomainCardinality",
    "RelationLatestPerDomainPolicyExpression",
    "RelationMetricSpec",
    "RelationPolicyAggregateExpression",
    "RelationSetEqualityExpression",
    "RelatedLookupCardinality",
    "RelatedLookupKeyRequirement",
    "RelatedLookupLoweringContract",
    "RelatedLookupSqlLowering",
    "RelatedValueLookupSpec",
    "RendererAbiValueKind",
    "RendererAggregateInputOrdering",
    "RendererFunctionFailurePolicy",
    "RendererGrammarContract",
    "RendererIdentifierQuoting",
    "RendererKeywordCase",
    "RendererLineEnding",
    "RendererObjectOrder",
    "RendererNullLowering",
    "RendererNullLoweringContract",
    "RendererSemanticFunctionAbi",
    "RendererSemanticFunctionKind",
    "RendererSemanticOperation",
    "RendererStatementTermination",
    "RendererWhitespace",
    "RequiredTableExtensionSpec",
    "RequiredContractSurfaceSpec",
    "RowHashAlgorithm",
    "RowHashSpec",
    "RowSource",
    "ScalarExpression",
    "SchemaComponentSpec",
    "SchemaVersionBand",
    "SortDirection",
    "SqlType",
    "SqliteAffinityPolicy",
    "SqliteAuthorizerDeniedOperation",
    "SqliteAuthorizerTransactionPolicy",
    "SqliteNativeExtensionPolicy",
    "SqliteRuntimeCapability",
    "SqliteRuntimeProfile",
    "SqliteTableMode",
    "SqliteTrustedSchemaProfile",
    "SqliteTrustTransitionStep",
    "SqliteValueTag",
    "StructuralVerificationSourceKind",
    "StructuralVerificationOperation",
    "STRUCTURAL_VERIFICATION_OPERATION_BY_SOURCE",
    "StructuralVerificationSourceSpec",
    "TriggerEffect",
    "TriggerEvent",
    "TriggerPolicyKind",
    "TriggerPolicySpec",
    "TriggerTiming",
    "VerificationRuleKind",
    "VerificationRuleSpec",
    "VerifierRequirement",
    "VersionOperation",
    "VersionPermission",
    "VersionPermissionContract",
    "VersionPermissionRule",
    "validate_authority_instance_id",
]
