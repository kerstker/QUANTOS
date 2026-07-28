"""Pure Pass-M implementations for the nine closed SQLite semantic ABIs.

The functions in this module have no database, filesystem, clock, network, or
store access.  Every malformed or incompatible ABI input returns ``None`` so
the renderer's outer NULL-lowering can fail closed.  This module intentionally
publishes only a typed *partial* implementation-evidence catalog for the nine
semantic functions; it is not, and cannot be promoted into, a schema seal.
"""

from __future__ import annotations

import hashlib
import inspect
import math
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from types import FunctionType

from quantos.contracts import canonical as _canonical

from . import family_schema_spec as fs
from .family_schema_composition import (
    FamilySchemaComposition,
    compose_family_schema_v3,
)


_PATH_RE = re.compile(r"^\$(?:\.[a-z][a-z0-9_]*)*$")
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SQLITE_INTEGER_MIN = -(1 << 63)
_SQLITE_INTEGER_MAX = (1 << 63) - 1
_UINT64_MASK = (1 << 64) - 1

_TYPE_TEXT = "TEXT"
_TYPE_INTEGER = "INTEGER"
_TYPE_REAL = "REAL"
_TYPE_TAGS = (_TYPE_TEXT, _TYPE_INTEGER, _TYPE_REAL)
_PROJECTION_ELEMENT = "ELEMENT_AT_ORDINAL"
_PROJECTION_ARRAY = "CANONICAL_ARRAY"

_BOOTSTRAP_ALGORITHM = "QUANTOS_STATIONARY_BOOTSTRAP_V1"
_PRNG_ALGORITHM = "SPLITMIX64_V1"
_CANONICAL_JSON_VERSION = "QUANTOS_CANONICAL_JSON_V1"
_BLOCK_LENGTH_POLICY = "QUANTOS_BLOCK_LENGTH_GRID_V1"

_SUPPORTS = "SUPPORTS_SUPERIORITY"
_DOES_NOT_SUPPORT = "DOES_NOT_SUPPORT_SUPERIORITY"
_DISAGREEMENT = "METHOD_DISAGREEMENT"
_UNRESOLVED = "MONTE_CARLO_UNRESOLVED"


class _SemanticInputError(ValueError):
    """An ABI value is malformed or outside the closed semantic domain."""


def _nfc_text(value: object) -> str:
    if type(value) is not str or unicodedata.normalize("NFC", value) != value:
        raise _SemanticInputError("text must be exact NFC")
    return value


def _sqlite_integer(value: object) -> int:
    if (
        type(value) is not int
        or not _SQLITE_INTEGER_MIN <= value <= _SQLITE_INTEGER_MAX
    ):
        raise _SemanticInputError("value must be an exact SQLite INTEGER")
    return value


def _positive_integer(value: object) -> int:
    parsed = _sqlite_integer(value)
    if parsed <= 0:
        raise _SemanticInputError("value must be a positive SQLite INTEGER")
    return parsed


def _nonnegative_integer(value: object) -> int:
    parsed = _sqlite_integer(value)
    if parsed < 0:
        raise _SemanticInputError("value must be a non-negative SQLite INTEGER")
    return parsed


def _sha256_text(value: object) -> str:
    parsed = _nfc_text(value)
    if _SHA256_RE.fullmatch(parsed) is None:
        raise _SemanticInputError("value must be lowercase SHA-256 text")
    return parsed


def _canonical_document(value: object) -> object:
    text = _nfc_text(value)
    try:
        payload = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _SemanticInputError("canonical JSON must be UTF-8") from exc
    try:
        return _canonical.load_canonical_json_bytes(payload)
    except Exception as exc:
        raise _SemanticInputError("invalid canonical JSON") from exc


def _path_parts(value: object) -> tuple[str, ...]:
    path = _nfc_text(value)
    if _PATH_RE.fullmatch(path) is None:
        raise _SemanticInputError("path is outside the closed JSON-path grammar")
    if path == "$":
        return ()
    return tuple(path[2:].split("."))


def _resolve_path(document: object, path: tuple[str, ...]) -> object:
    current = document
    for item in path:
        if type(current) is not dict or item not in current:
            raise _SemanticInputError("canonical JSON path is missing")
        current = current[item]
    return current


def _canonical_scalar(value: object, type_tag: object) -> object:
    tag = _nfc_text(type_tag)
    if tag not in _TYPE_TAGS:
        raise _SemanticInputError("unknown canonical scalar type tag")
    if tag == _TYPE_TEXT:
        return _nfc_text(value)
    if tag == _TYPE_INTEGER:
        return _sqlite_integer(value)
    if type(value) is not str:
        raise _SemanticInputError("canonical REAL must be float64-hex text")
    try:
        parsed = _canonical.parse_float64_hex(value)
    except Exception as exc:
        raise _SemanticInputError("canonical REAL must be float64-hex text") from exc
    return 0.0 if parsed == 0.0 else parsed


def _sql_scalar_for_tag(value: object, type_tag: object) -> object:
    tag = _nfc_text(type_tag)
    if tag == _TYPE_TEXT:
        return _nfc_text(value)
    if tag == _TYPE_INTEGER:
        return _sqlite_integer(value)
    if tag != _TYPE_REAL or type(value) is not float or not math.isfinite(value):
        raise _SemanticInputError("SQL REAL must be an exact finite float")
    return 0.0 if value == 0.0 else value


def _canonical_array(
    document_text: object,
    path_text: object,
    type_tag: object,
) -> tuple[list[object], tuple[object, ...]]:
    document = _canonical_document(document_text)
    raw = _resolve_path(document, _path_parts(path_text))
    if type(raw) is not list:
        raise _SemanticInputError("canonical JSON value is not an array")
    converted = tuple(_canonical_scalar(item, type_tag) for item in raw)
    return raw, converted


def _has_duplicate_scalars(values: tuple[object, ...]) -> bool:
    for index, value in enumerate(values):
        if any(type(value) is type(previous) and value == previous for previous in values[:index]):
            return True
    return False


def quantos_canonical_json_field_v1(
    canonical_json_text: object,
    json_path_text: object,
    type_tag_text: object,
) -> object | None:
    """Extract one exact typed scalar from canonical JSON, else return NULL."""

    try:
        document = _canonical_document(canonical_json_text)
        value = _resolve_path(document, _path_parts(json_path_text))
        return _canonical_scalar(value, type_tag_text)
    except Exception:
        return None


def quantos_canonical_json_array_value_v1(
    canonical_json_text: object,
    json_path_text: object,
    type_tag_text: object,
    projection_tag_text: object,
    optional_integer: object,
) -> object | None:
    """Project an array element or the exact unique canonical domain array."""

    try:
        projection = _nfc_text(projection_tag_text)
        raw, converted = _canonical_array(
            canonical_json_text,
            json_path_text,
            type_tag_text,
        )
        if projection == _PROJECTION_ELEMENT:
            ordinal = _nonnegative_integer(optional_integer)
            if ordinal >= len(converted):
                raise _SemanticInputError("array ordinal is out of range")
            return converted[ordinal]
        if projection != _PROJECTION_ARRAY or optional_integer is not None:
            raise _SemanticInputError("invalid canonical array projection")
        if _has_duplicate_scalars(converted):
            raise _SemanticInputError("canonical domain arrays forbid duplicates")
        return _canonical.canonical_json_bytes(raw).decode("utf-8")
    except Exception:
        return None


def quantos_canonical_json_array_length_v1(
    canonical_json_text: object,
    json_path_text: object,
    type_tag_text: object,
) -> int | None:
    """Return the cardinality of one exact typed canonical array."""

    try:
        _, converted = _canonical_array(
            canonical_json_text,
            json_path_text,
            type_tag_text,
        )
        return len(converted)
    except Exception:
        return None


def quantos_canonical_json_array_contains_v1(
    canonical_json_text: object,
    json_path_text: object,
    type_tag_text: object,
    sql_scalar: object,
) -> int | None:
    """Return 1/0 for exact typed membership, or NULL on invalid input."""

    try:
        _, converted = _canonical_array(
            canonical_json_text,
            json_path_text,
            type_tag_text,
        )
        needle = _sql_scalar_for_tag(sql_scalar, type_tag_text)
        return int(any(type(needle) is type(item) and needle == item for item in converted))
    except Exception:
        return None


def _excluded_paths(value: object) -> tuple[tuple[str, ...], ...]:
    document = _canonical_document(value)
    if type(document) is not list:
        raise _SemanticInputError("excluded paths must be a canonical JSON array")
    paths: list[tuple[str, ...]] = []
    for raw_path in document:
        if type(raw_path) is not list or not raw_path:
            raise _SemanticInputError("each excluded path must be a non-empty array")
        path: list[str] = []
        for raw_item in raw_path:
            item = _nfc_text(raw_item)
            if _IDENTIFIER_RE.fullmatch(item) is None:
                raise _SemanticInputError("excluded paths require lowercase identifiers")
            path.append(item)
        paths.append(tuple(path))
    result = tuple(paths)
    if len(set(result)) != len(result):
        raise _SemanticInputError("excluded paths must be unique")
    for index, left in enumerate(result):
        for right in result[index + 1 :]:
            shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
            if longer[: len(shorter)] == shorter:
                raise _SemanticInputError("excluded paths must not overlap")
    return result


def _clone_canonical_tree(value: object) -> object:
    if type(value) is dict:
        return {key: _clone_canonical_tree(item) for key, item in value.items()}
    if type(value) is list:
        return [_clone_canonical_tree(item) for item in value]
    return value


def _path_parent(document: object, path: tuple[str, ...]) -> tuple[dict[str, object], str]:
    current = document
    for item in path[:-1]:
        if type(current) is not dict or item not in current:
            raise _SemanticInputError("excluded canonical JSON path is missing")
        current = current[item]
    if type(current) is not dict or path[-1] not in current:
        raise _SemanticInputError("excluded canonical JSON path is missing")
    return current, path[-1]


def quantos_canonical_json_sha256_v1(
    canonical_json_text: object,
    excluded_paths_json_text: object,
) -> str | None:
    """Hash exact canonical JSON after removing exact owned exclusions."""

    try:
        document = _canonical_document(canonical_json_text)
        paths = _excluded_paths(excluded_paths_json_text)
        clone = _clone_canonical_tree(document)
        for path in paths:
            _path_parent(clone, path)
        for path in paths:
            parent, key = _path_parent(clone, path)
            del parent[key]
        return _canonical.sha256_bytes(_canonical.canonical_json_bytes(clone))
    except Exception:
        return None


def _ordered_sql_scalar(value: object) -> object:
    if type(value) is int:
        return _sqlite_integer(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise _SemanticInputError("ordered REAL values must be finite")
        return 0.0 if value == 0.0 else value
    if type(value) is str:
        return _nfc_text(value)
    raise _SemanticInputError("ordered values require INTEGER, REAL, or TEXT")


class QuantosCanonicalOrderedValueSha256V1:
    """SQLite aggregate over an already BINARY-ASC ordered scalar stream."""

    def __init__(self) -> None:
        self._values: list[object] = []
        self._invalid = False

    def step(self, value: object) -> None:
        if self._invalid:
            return
        try:
            self._values.append(_ordered_sql_scalar(value))
        except Exception:
            self._invalid = True

    def finalize(self) -> str | None:
        if self._invalid or not self._values:
            return None
        try:
            return _canonical.sha256_bytes(
                _canonical.canonical_json_bytes(self._values)
            )
        except Exception:
            return None


def _net_return_vector(value: object) -> tuple[float, ...]:
    document = _canonical_document(value)
    if type(document) is not dict or "net_returns" not in document:
        raise _SemanticInputError("series payload requires net_returns")
    raw = document["net_returns"]
    if type(raw) is not list or not raw:
        raise _SemanticInputError("net_returns must be a non-empty array")
    return tuple(_canonical_scalar(item, _TYPE_REAL) for item in raw)  # type: ignore[return-value]


def quantos_mean_net_return_differential_v1(
    candidate_series_payload_json: object,
    benchmark_series_payload_json: object,
) -> float | None:
    """Compute the ordered sample mean of candidate minus benchmark net return."""

    try:
        candidate = _net_return_vector(candidate_series_payload_json)
        benchmark = _net_return_vector(benchmark_series_payload_json)
        if len(candidate) != len(benchmark):
            raise _SemanticInputError("candidate and benchmark vectors must align")
        total = 0.0
        for candidate_item, benchmark_item in zip(candidate, benchmark, strict=True):
            difference = candidate_item - benchmark_item
            if not math.isfinite(difference):
                raise _SemanticInputError("net-return differential must be finite")
            total += 0.0 if difference == 0.0 else difference
            if not math.isfinite(total):
                raise _SemanticInputError("net-return mean accumulation must be finite")
        result = total / len(candidate)
        if not math.isfinite(result):
            raise _SemanticInputError("net-return mean must be finite")
        return 0.0 if result == 0.0 else result
    except Exception:
        return None


class _SplitMix64:
    def __init__(self, seed: int) -> None:
        self._state = seed

    def next_u64(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & _UINT64_MASK
        value = self._state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _UINT64_MASK
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _UINT64_MASK
        return (value ^ (value >> 31)) & _UINT64_MASK

    def uniform_below(self, upper_bound: int) -> int:
        modulus = 1 << 64
        limit = modulus - (modulus % upper_bound)
        while True:
            value = self.next_u64()
            if value < limit:
                return value % upper_bound

    def restart_uniform(self) -> float:
        return (self.next_u64() >> 11) * (2.0**-53)


def _stationary_bootstrap_prefix_hashes(
    period_count: int,
    block_length: int,
    seed: int,
    replication_counts: tuple[int, ...],
) -> dict[int, str]:
    targets = set(replication_counts)
    terminal = max(targets)
    generator = _SplitMix64(seed)
    restart_probability = 1.0 / block_length
    stream_hash = hashlib.sha256()
    result: dict[int, str] = {}
    for replication in range(1, terminal + 1):
        index = generator.uniform_below(period_count)
        stream_hash.update(index.to_bytes(8, byteorder="little", signed=False))
        for _ in range(period_count - 1):
            if generator.restart_uniform() < restart_probability:
                index = generator.uniform_below(period_count)
            else:
                index = (index + 1) % period_count
            stream_hash.update(index.to_bytes(8, byteorder="little", signed=False))
        if replication in targets:
            result[replication] = stream_hash.hexdigest()
    return result


def quantos_prefix_stream_extension_v1(
    current_prefix_hash: object,
    current_replication_count: object,
    current_period_count: object,
    previous_or_genesis_prefix_hash: object,
    previous_or_genesis_replication_count: object,
    previous_or_genesis_period_count: object,
    block_length: object,
    current_ordinal: object,
    genesis_hash: object,
    bootstrap_seed: object,
    bootstrap_algorithm_version: object,
    prng_algorithm_version: object,
    canonical_json_version: object,
    block_length_policy_id: object,
) -> int | None:
    """Verify an exact deterministic stationary-bootstrap prefix extension."""

    try:
        current_hash = _sha256_text(current_prefix_hash)
        previous_hash = _sha256_text(previous_or_genesis_prefix_hash)
        genesis = _sha256_text(genesis_hash)
        current_count = _positive_integer(current_replication_count)
        period_count = _positive_integer(current_period_count)
        previous_count = _nonnegative_integer(previous_or_genesis_replication_count)
        previous_period = _nonnegative_integer(previous_or_genesis_period_count)
        block = _positive_integer(block_length)
        ordinal = _positive_integer(current_ordinal)
        # This ABI receives a SQLite INTEGER, hence the governed persisted
        # seed is necessarily limited to [0, 2**63 - 1].  SplitMix64 itself
        # remains a uint64 state machine after accepting that exact ABI value.
        seed = _nonnegative_integer(bootstrap_seed)
        algorithms = tuple(
            _nfc_text(item)
            for item in (
                bootstrap_algorithm_version,
                prng_algorithm_version,
                canonical_json_version,
                block_length_policy_id,
            )
        )
        if algorithms != (
            _BOOTSTRAP_ALGORITHM,
            _PRNG_ALGORITHM,
            _CANONICAL_JSON_VERSION,
            _BLOCK_LENGTH_POLICY,
        ):
            raise _SemanticInputError("unknown bootstrap algorithm tuple")
        if period_count < 20 or not 1 <= block <= period_count // 2:
            raise _SemanticInputError("period count or block length is outside the kernel domain")

        if ordinal == 1:
            if previous_count != 0 or previous_period != 0:
                raise _SemanticInputError("genesis starts from an empty prefix")
            expected = _stationary_bootstrap_prefix_hashes(
                period_count,
                block,
                seed,
                (current_count,),
            )
            return int(previous_hash == genesis and current_hash == expected[current_count])

        if (
            previous_count <= 0
            or previous_count >= current_count
            or previous_period != period_count
        ):
            raise _SemanticInputError("non-genesis prefixes must strictly extend their predecessor")
        expected = _stationary_bootstrap_prefix_hashes(
            period_count,
            block,
            seed,
            (previous_count, current_count),
        )
        return int(
            previous_hash == expected[previous_count]
            and current_hash == expected[current_count]
        )
    except Exception:
        return None


class QuantosAnyGridDisagreementMaxPV1:
    """Aggregate the ordered latest grid rows under the fixed family policy."""

    def __init__(self) -> None:
        self._rows: list[tuple[int, str, str]] = []
        self._record_hashes: set[str] = set()
        self._invalid = False

    def step(
        self,
        block_length: object,
        replication_count: object,
        reality_check_result_hash: object,
        spa_result_hash: object,
        grid_point_status: object,
        grid_point_verdict: object,
        record_hash: object,
    ) -> None:
        if self._invalid:
            return
        try:
            block = _positive_integer(block_length)
            replications = _positive_integer(replication_count)
            _sha256_text(reality_check_result_hash)
            _sha256_text(spa_result_hash)
            status = _nfc_text(grid_point_status)
            verdict = _nfc_text(grid_point_verdict)
            row_hash = _sha256_text(record_hash)
            if replications < 10_000:
                raise _SemanticInputError("family conclusions require publishable replications")
            if status == "RESOLVED":
                if verdict not in (_SUPPORTS, _DOES_NOT_SUPPORT, _DISAGREEMENT):
                    raise _SemanticInputError("resolved grid point has an invalid verdict")
            elif status == "AMBIGUOUS":
                if verdict != _UNRESOLVED:
                    raise _SemanticInputError("terminal ambiguous grid point must be unresolved")
            else:
                raise _SemanticInputError("invalid grid rows cannot produce an assessed conclusion")
            if self._rows and block <= self._rows[-1][0]:
                raise _SemanticInputError("latest grid rows must arrive in strict block order")
            if row_hash in self._record_hashes:
                raise _SemanticInputError("latest grid record hashes must be unique")
            self._rows.append((block, verdict, row_hash))
            self._record_hashes.add(row_hash)
        except Exception:
            self._invalid = True

    def finalize(self) -> str | None:
        if self._invalid or not self._rows:
            return None
        try:
            verdicts = tuple(row[1] for row in self._rows)
            if _UNRESOLVED in verdicts:
                conclusion = _UNRESOLVED
            elif _DISAGREEMENT in verdicts:
                conclusion = _DISAGREEMENT
            elif all(verdict == _SUPPORTS for verdict in verdicts):
                conclusion = _SUPPORTS
            elif all(verdict in (_SUPPORTS, _DOES_NOT_SUPPORT) for verdict in verdicts):
                conclusion = _DOES_NOT_SUPPORT
            else:
                return None
            ordered_hashes = [row[2] for row in self._rows]
            ordered_hashes_hash = _canonical.sha256_bytes(
                _canonical.canonical_json_bytes(ordered_hashes)
            )
            return _canonical.canonical_json_bytes(
                {
                    "diagnostic_verdict": conclusion,
                    "ordered_latest_grid_assessment_hashes_hash": ordered_hashes_hash,
                }
            ).decode("utf-8")
        except Exception:
            return None


@dataclass(frozen=True, slots=True)
class SemanticFunctionImplementation:
    """One typed callable ready for the canonical Pass-M binder."""

    operation: fs.RendererSemanticOperation
    sqlite_name: str
    kind: fs.RendererSemanticFunctionKind
    implementation: object

    def __post_init__(self) -> None:
        if type(self.operation) is not fs.RendererSemanticOperation:
            raise TypeError("semantic implementation operation must be closed")
        if type(self.sqlite_name) is not str or _IDENTIFIER_RE.fullmatch(self.sqlite_name) is None:
            raise ValueError("semantic implementation SQLite name must be an identifier")
        if type(self.kind) is not fs.RendererSemanticFunctionKind:
            raise TypeError("semantic implementation kind must be closed")
        abi = next(
            (
                item
                for item in fs.FAMILY_SCHEMA_RENDERER_SEMANTIC_FUNCTION_ABIS
                if item.operation is self.operation
            ),
            None,
        )
        if abi is None or abi.sqlite_name != self.sqlite_name or abi.kind is not self.kind:
            raise ValueError("semantic implementation identity must match the canonical ABI")
        if self.kind is fs.RendererSemanticFunctionKind.SCALAR:
            if type(self.implementation) is not FunctionType:
                raise TypeError("scalar semantic implementation must be a Python function")
            parameters = tuple(inspect.signature(self.implementation).parameters.values())
            if len(parameters) != abi.arity or any(
                parameter.kind
                is not inspect.Parameter.POSITIONAL_OR_KEYWORD
                or parameter.default is not inspect.Parameter.empty
                for parameter in parameters
            ):
                raise TypeError("scalar semantic implementation does not match ABI arity")
        elif type(self.implementation) is not type or any(
            type(vars(self.implementation).get(name)) is not FunctionType
            for name in ("step", "finalize")
        ):
            raise TypeError("aggregate semantic implementation requires step and finalize")
        else:
            step = tuple(
                inspect.signature(vars(self.implementation)["step"]).parameters.values()
            )
            finalize = tuple(
                inspect.signature(vars(self.implementation)["finalize"]).parameters.values()
            )
            if (
                len(step) != abi.arity + 1
                or len(finalize) != 1
                or any(
                    parameter.kind
                    is not inspect.Parameter.POSITIONAL_OR_KEYWORD
                    or parameter.default is not inspect.Parameter.empty
                    for parameter in (*step, *finalize)
                )
            ):
                raise TypeError("aggregate semantic implementation does not match ABI arity")


FAMILY_SCHEMA_SQLITE_SEMANTIC_IMPLEMENTATIONS = (
    SemanticFunctionImplementation(
        fs.RendererSemanticOperation.CANONICAL_JSON_FIELD_VALUE,
        "quantos_canonical_json_field_v1",
        fs.RendererSemanticFunctionKind.SCALAR,
        quantos_canonical_json_field_v1,
    ),
    SemanticFunctionImplementation(
        fs.RendererSemanticOperation.RELATED_CANONICAL_JSON_ARRAY_VALUE,
        "quantos_canonical_json_array_value_v1",
        fs.RendererSemanticFunctionKind.SCALAR,
        quantos_canonical_json_array_value_v1,
    ),
    SemanticFunctionImplementation(
        fs.RendererSemanticOperation.RELATED_CANONICAL_JSON_ARRAY_LENGTH,
        "quantos_canonical_json_array_length_v1",
        fs.RendererSemanticFunctionKind.SCALAR,
        quantos_canonical_json_array_length_v1,
    ),
    SemanticFunctionImplementation(
        fs.RendererSemanticOperation.RELATED_CANONICAL_JSON_ARRAY_CONTAINS,
        "quantos_canonical_json_array_contains_v1",
        fs.RendererSemanticFunctionKind.SCALAR,
        quantos_canonical_json_array_contains_v1,
    ),
    SemanticFunctionImplementation(
        fs.RendererSemanticOperation.CANONICAL_JSON_PAYLOAD_HASH,
        "quantos_canonical_json_sha256_v1",
        fs.RendererSemanticFunctionKind.SCALAR,
        quantos_canonical_json_sha256_v1,
    ),
    SemanticFunctionImplementation(
        fs.RendererSemanticOperation.CANONICAL_ORDERED_VALUE_HASH,
        "quantos_canonical_ordered_value_sha256_v1",
        fs.RendererSemanticFunctionKind.AGGREGATE,
        QuantosCanonicalOrderedValueSha256V1,
    ),
    SemanticFunctionImplementation(
        fs.RendererSemanticOperation.MEAN_NET_RETURN_DIFFERENTIAL,
        "quantos_mean_net_return_differential_v1",
        fs.RendererSemanticFunctionKind.SCALAR,
        quantos_mean_net_return_differential_v1,
    ),
    SemanticFunctionImplementation(
        fs.RendererSemanticOperation.PREFIX_STREAM_EXTENSION,
        "quantos_prefix_stream_extension_v1",
        fs.RendererSemanticFunctionKind.SCALAR,
        quantos_prefix_stream_extension_v1,
    ),
    SemanticFunctionImplementation(
        fs.RendererSemanticOperation.LATEST_PER_DOMAIN_POLICY,
        "quantos_any_grid_disagreement_max_p_v1",
        fs.RendererSemanticFunctionKind.AGGREGATE,
        QuantosAnyGridDisagreementMaxPV1,
    ),
)


def build_canonical_semantic_callable_bindings(
    composition: FamilySchemaComposition | None = None,
) -> tuple[object, ...]:
    """Return the nine rows in the canonical binder's native registry type.

    The renderer import is deliberately delayed: ``family_schema`` can import
    this module's pure registry after defining its binder types without creating
    a module-initialization cycle.
    """

    current = compose_family_schema_v3() if composition is None else composition
    if type(current) is not FamilySchemaComposition:
        raise TypeError("semantic callable bindings require the exact composition")
    from . import family_schema as canonical_binder

    requirements = tuple(
        item
        for item in current.implementation_evidence_requirements.entries
        if item.target_kind is fs.ImplementationTargetKind.SQLITE_SEMANTIC_FUNCTION
    )
    implementation_by_operation = {
        item.operation: item for item in FAMILY_SCHEMA_SQLITE_SEMANTIC_IMPLEMENTATIONS
    }
    if len(requirements) != len(fs.RendererSemanticOperation):
        raise ValueError("canonical semantic bindings require exactly nine requirements")
    rows: list[object] = []
    for requirement in requirements:
        registered = implementation_by_operation.get(requirement.semantic_operation)
        if (
            registered is None
            or requirement.sqlite_name != registered.sqlite_name
            or requirement.implementation_id
            != f"FAMILY_SCHEMA_SQLITE_SEMANTIC::{registered.sqlite_name}"
        ):
            raise ValueError("semantic registry does not match its implementation requirement")
        rows.append(
            canonical_binder._ImplementationCallableBinding(
                target_kind=requirement.target_kind,
                target_id=requirement.target_id,
                implementation_id=requirement.implementation_id,
                implementation=registered.implementation,
            )
        )
    return tuple(rows)


class PartialEvidenceCoverage(StrEnum):
    SQLITE_SEMANTIC_FUNCTIONS_ONLY = "SQLITE_SEMANTIC_FUNCTIONS_ONLY"


@dataclass(frozen=True, slots=True)
class PartialSemanticImplementationEvidenceCatalog:
    """Non-sealing evidence for exactly the nine SQLite semantic functions."""

    requirements: fs.ImplementationEvidenceRequirementCatalog
    bindings: tuple[fs.ImplementationEvidenceBinding, ...]
    coverage: PartialEvidenceCoverage = (
        PartialEvidenceCoverage.SQLITE_SEMANTIC_FUNCTIONS_ONLY
    )
    catalog_version: str = "QUANTOS_PARTIAL_SEMANTIC_IMPLEMENTATION_EVIDENCE_V1"

    def __post_init__(self) -> None:
        if type(self.requirements) is not fs.ImplementationEvidenceRequirementCatalog:
            raise TypeError("partial semantic evidence requires a typed requirement catalog")
        if self.catalog_version != "QUANTOS_PARTIAL_SEMANTIC_IMPLEMENTATION_EVIDENCE_V1":
            raise ValueError("unknown partial semantic evidence catalog version")
        if self.coverage is not PartialEvidenceCoverage.SQLITE_SEMANTIC_FUNCTIONS_ONLY:
            raise ValueError("partial evidence coverage must remain semantic-only")
        requirements = self.requirements.entries
        if len(requirements) != len(fs.RendererSemanticOperation) or any(
            item.target_kind is not fs.ImplementationTargetKind.SQLITE_SEMANTIC_FUNCTION
            for item in requirements
        ):
            raise ValueError("partial evidence must contain exactly the nine semantic requirements")
        if type(self.bindings) is not tuple or any(
            type(item) is not fs.ImplementationEvidenceBinding for item in self.bindings
        ):
            raise TypeError("partial evidence requires exact typed bindings")
        if len(self.bindings) != len(requirements):
            raise ValueError("partial semantic evidence requires one binding per semantic ABI")
        for requirement, binding in zip(requirements, self.bindings, strict=True):
            if (
                binding.target_kind is not requirement.target_kind
                or binding.target_id != requirement.target_id
                or binding.implementation_id != requirement.implementation_id
                or binding.hash_method is not requirement.hash_method
                or binding.hash_method_version != requirement.hash_method_version
            ):
                raise ValueError("partial semantic evidence does not match its requirement")

    @property
    def sealed(self) -> bool:
        return False

    @property
    def complete(self) -> bool:
        return False


def _semantic_implementation_evidence_sha256(
    requirement: fs.ImplementationEvidenceRequirementSpec,
    abi: fs.RendererSemanticFunctionAbi,
    implementation: object,
) -> str:
    """Use the canonical renderer binder's sole reviewed evidence algorithm."""

    # Delayed import keeps this private implementation module acyclic if the
    # renderer later imports the typed registry while building the full catalog.
    from . import family_schema as canonical_binder

    if (
        type(requirement) is not fs.ImplementationEvidenceRequirementSpec
        or type(abi) is not fs.RendererSemanticFunctionAbi
        or requirement.target_kind
        is not fs.ImplementationTargetKind.SQLITE_SEMANTIC_FUNCTION
        or requirement.semantic_operation is not abi.operation
        or requirement.sqlite_name != abi.sqlite_name
        or requirement.semantic_contract_id != abi.semantic_contract_id
    ):
        raise TypeError("semantic evidence requires one exact requirement/ABI pair")
    binding_key = (
        f"SEMANTIC_ABI::{abi.operation.value}::{abi.sqlite_name}::"
        f"{abi.semantic_contract_id}"
    )
    if requirement.canonical_binding_keys != (binding_key,):
        raise ValueError("semantic evidence requirement does not bind its exact ABI")
    descriptor = canonical_binder._implementation_descriptor(implementation)
    return canonical_binder._implementation_evidence_sha256(
        requirement,
        ((binding_key, abi),),
        descriptor,
    )


def build_partial_semantic_implementation_evidence(
    composition: FamilySchemaComposition | None = None,
) -> PartialSemanticImplementationEvidenceCatalog:
    """Bind real evidence for nine semantic callables without claiming a seal."""

    current = compose_family_schema_v3() if composition is None else composition
    if type(current) is not FamilySchemaComposition:
        raise TypeError("partial semantic evidence requires the exact composition")
    requirements = tuple(
        item
        for item in current.implementation_evidence_requirements.entries
        if item.target_kind is fs.ImplementationTargetKind.SQLITE_SEMANTIC_FUNCTION
    )
    abis = current.renderer_grammar.semantic_function_abis
    if len(requirements) != len(fs.RendererSemanticOperation) or len(abis) != len(requirements):
        raise ValueError("semantic evidence requires exactly the closed nine ABIs")
    implementation_by_operation = {
        item.operation: item for item in FAMILY_SCHEMA_SQLITE_SEMANTIC_IMPLEMENTATIONS
    }
    if len(implementation_by_operation) != len(requirements):
        raise ValueError("semantic implementation registry is incomplete or duplicated")

    bindings: list[fs.ImplementationEvidenceBinding] = []
    for requirement, abi in zip(requirements, abis, strict=True):
        registered = implementation_by_operation.get(requirement.semantic_operation)
        if (
            registered is None
            or registered.sqlite_name != abi.sqlite_name
            or registered.kind is not abi.kind
            or requirement.implementation_id
            != f"FAMILY_SCHEMA_SQLITE_SEMANTIC::{registered.sqlite_name}"
        ):
            raise ValueError("semantic callable registry does not match the canonical ABI")
        bindings.append(
            fs.ImplementationEvidenceBinding(
                target_kind=requirement.target_kind,
                target_id=requirement.target_id,
                implementation_id=requirement.implementation_id,
                implementation_sha256=_semantic_implementation_evidence_sha256(
                    requirement,
                    abi,
                    registered.implementation,
                ),
                hash_method=requirement.hash_method,
                hash_method_version=requirement.hash_method_version,
            )
        )
    return PartialSemanticImplementationEvidenceCatalog(
        requirements=fs.ImplementationEvidenceRequirementCatalog(entries=requirements),
        bindings=tuple(bindings),
    )


__all__ = [
    "FAMILY_SCHEMA_SQLITE_SEMANTIC_IMPLEMENTATIONS",
    "PartialEvidenceCoverage",
    "PartialSemanticImplementationEvidenceCatalog",
    "QuantosAnyGridDisagreementMaxPV1",
    "QuantosCanonicalOrderedValueSha256V1",
    "SemanticFunctionImplementation",
    "build_canonical_semantic_callable_bindings",
    "build_partial_semantic_implementation_evidence",
    "quantos_canonical_json_array_contains_v1",
    "quantos_canonical_json_array_length_v1",
    "quantos_canonical_json_array_value_v1",
    "quantos_canonical_json_field_v1",
    "quantos_canonical_json_sha256_v1",
    "quantos_mean_net_return_differential_v1",
    "quantos_prefix_stream_extension_v1",
]
