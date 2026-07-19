"""Canonical SQL rendering and fail-closed sealing for family schema v3.

The renderer is pure and deterministic.  The public seal is deliberately
unavailable until this module owns reviewed implementations for every internal
UDF and verifier target.  Nothing here opens a database, executes SQL, migrates
a store, activates an authority, or registers runtime functions.
"""

from __future__ import annotations

import ast
import builtins
import datetime
import dis
import functools
import hashlib
import importlib
import importlib.util
import inspect
import math
import re
import struct
import sys
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from types import CodeType, FunctionType, ModuleType
from typing import Iterator

from quantos.contracts.canonical import (
    canonical_json_bytes,
    load_canonical_json_bytes,
)

from . import family_schema_spec as fs
from . import family_schema_semantic_functions as _semantic_functions
from .family_schema_composition import (
    EXACT_V3_VERIFIER_COMPOSITION_BINDING_KEY,
    FamilySchemaComposition,
    auxiliary_semantic_recipe_binding_key,
    compose_family_schema_v3,
    external_claim_profile_binding_key,
)


class FamilySchemaRenderError(ValueError):
    """The closed schema composition cannot be lowered canonically."""


class FamilySchemaUnsealedError(FamilySchemaRenderError):
    """The exact implementation registry is incomplete, so no seal exists."""


@dataclass(frozen=True, slots=True)
class _ImplementationCallableBinding:
    """One implementation owned by the private canonical production registry."""

    target_kind: fs.ImplementationTargetKind
    target_id: str
    implementation_id: str
    implementation: object

    def __post_init__(self) -> None:
        if type(self.target_kind) is not fs.ImplementationTargetKind:
            raise TypeError("implementation target kind must be closed")
        if self.target_kind is fs.ImplementationTargetKind.EXTERNAL_PROVIDER_TRUST_ROOT:
            raise ValueError("external provider trust roots cannot bind local implementations")
        for value, label in (
            (self.target_id, "implementation target ID"),
            (self.implementation_id, "implementation ID"),
        ):
            if type(value) is not str or not value:
                raise ValueError(f"{label} must be non-empty text")
        if type(self.implementation) not in (FunctionType, type):
            raise TypeError("implementation evidence requires an exact Python function or class")
        if getattr(self.implementation, "__name__", None) == "<lambda>":
            raise TypeError("implementation evidence rejects anonymous Python functions")


@dataclass(frozen=True, slots=True)
class _CanonicalBindingDigest:
    canonical_bytes_sha256: str
    canonical_byte_length: int

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.canonical_bytes_sha256):
            raise ValueError("canonical binding digest must be lowercase SHA-256")
        if type(self.canonical_byte_length) is not int or self.canonical_byte_length < 1:
            raise ValueError("canonical binding byte length must be positive")


@dataclass(frozen=True, slots=True, init=False)
class FamilySchemaManifest:
    """Factory-only exact-v3 seal; no caller-supplied sealed boolean exists."""

    schema_version: int
    source_schema_version: int
    renderer_version: str
    component_order: tuple[str, ...]
    rendered_objects: tuple[RenderedSchemaObject, ...]
    implementation_evidence: fs.ImplementationEvidenceCatalog
    composition_sha256: str
    statement_catalog_sha256: str
    implementation_evidence_sha256: str
    verifier_mapping_sha256: str
    manifest_bytes: bytes
    manifest_sha256: str
    _seal_token: object = field(repr=False, compare=False)

    def __init__(self) -> None:
        raise TypeError("FamilySchemaManifest is created only by seal_family_schema_v3")


@dataclass(frozen=True, slots=True)
class RenderedSchemaObject:
    ordinal: int
    object_type: str
    object_name: str
    table_name: str
    sqlite_schema_sql: str
    statement_sql: str
    statement_bytes: bytes
    statement_sha256: str

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 1:
            raise ValueError("rendered object ordinal must be positive")
        if self.object_type not in {"table", "index", "trigger"}:
            raise ValueError("rendered object type is not closed")
        for value, label in (
            (self.object_name, "rendered object name"),
            (self.table_name, "rendered table name"),
            (self.sqlite_schema_sql, "sqlite_schema SQL"),
            (self.statement_sql, "execution SQL"),
            (self.statement_sha256, "statement hash"),
        ):
            if type(value) is not str or not value:
                raise ValueError(f"{label} must be non-empty text")
        if type(self.statement_bytes) is not bytes:
            raise TypeError("rendered statement bytes must be bytes")
        if self.statement_sql != self.sqlite_schema_sql + ";\n":
            raise ValueError("rendered statement termination is not canonical")
        if self.statement_bytes != self.statement_sql.encode("utf-8"):
            raise ValueError("rendered statement bytes are not exact UTF-8")
        if hashlib.sha256(self.statement_bytes).hexdigest() != self.statement_sha256:
            raise ValueError("rendered statement hash mismatch")


@dataclass(frozen=True, slots=True)
class _RenderContext:
    current_alias: str | None = None
    related_alias: str | None = None


_SCALAR_NODE_TYPES = (
    fs.ColumnValue,
    fs.LiteralValue,
    fs.QualifiedColumnValue,
    fs.CanonicalJsonFieldValue,
    fs.OrdinalOffsetValue,
    fs.RelatedCanonicalJsonArrayValueSpec,
    fs.RelatedCanonicalJsonArrayLengthValueSpec,
)

_PREDICATE_NODE_TYPES = (
    fs.ComparisonExpression,
    fs.NullExpression,
    fs.MembershipExpression,
    fs.LogicalExpression,
    fs.NotExpression,
    fs.CanonicalJsonPayloadHashExpression,
    fs.RelatedCanonicalJsonArrayContainsExpression,
    fs.RelationExistsExpression,
    fs.RelationAggregateExpression,
    fs.RelationJoinAggregateExpression,
    fs.RelationArgExtremumExpression,
    fs.RelationPolicyAggregateExpression,
    fs.RelationLatestPerDomainPolicyExpression,
    fs.PrefixStreamExtensionExpression,
    fs.ChainPredecessorExpression,
    fs.RelationChainPrefixExpression,
    fs.RelationSetEqualityExpression,
)

_RENDERABLE_PREDICATE_NODE_TYPES = tuple(
    item for item in _PREDICATE_NODE_TYPES if item.__name__ not in fs.FAMILY_SCHEMA_UNSUPPORTED_PREDICATE_NODES
)

_RELATED_NODE_TYPES = (
    fs.LookupBinding,
    fs.RelatedValueLookupSpec,
    fs.RelationAggregateOrderSpec,
    fs.RelationJoinAggregateOrderSpec,
    fs.RelationJoinEqualitySpec,
    fs.RelationJoinSpec,
    fs.RelationMetricSpec,
)

_SCHEMA_NODE_TYPES = (
    fs.AuthorityTableSpec,
    fs.CheckConstraintSpec,
    fs.ColumnSpec,
    fs.ForeignKeySpec,
    fs.IndexColumnSpec,
    fs.IndexSpec,
    fs.InsertStateMachineSpec,
    fs.InsertStateTransition,
    fs.TriggerPolicySpec,
)

SUPPORTED_SCALAR_NODE_TYPES = tuple(item.__name__ for item in _SCALAR_NODE_TYPES)
SUPPORTED_PREDICATE_NODE_TYPES = tuple(item.__name__ for item in _RENDERABLE_PREDICATE_NODE_TYPES)
SUPPORTED_RELATED_NODE_TYPES = tuple(item.__name__ for item in _RELATED_NODE_TYPES)
SUPPORTED_SCHEMA_NODE_TYPES = tuple(item.__name__ for item in _SCHEMA_NODE_TYPES)
UNSUPPORTED_PREDICATE_NODE_TYPES = fs.FAMILY_SCHEMA_UNSUPPORTED_PREDICATE_NODES


_COMPARISON_SQL = {
    fs.ComparisonOperator.EQ: "=",
    fs.ComparisonOperator.NE: "<>",
    fs.ComparisonOperator.LT: "<",
    fs.ComparisonOperator.LE: "<=",
    fs.ComparisonOperator.GT: ">",
    fs.ComparisonOperator.GE: ">=",
}
_FOREIGN_KEY_ACTION_SQL = {
    fs.ForeignKeyAction.NO_ACTION: "NO ACTION",
    fs.ForeignKeyAction.RESTRICT: "RESTRICT",
    fs.ForeignKeyAction.CASCADE: "CASCADE",
    fs.ForeignKeyAction.SET_NULL: "SET NULL",
    fs.ForeignKeyAction.SET_DEFAULT: "SET DEFAULT",
}
_DEFERRABILITY_SQL = {
    fs.ForeignKeyDeferrability.NOT_DEFERRABLE: "NOT DEFERRABLE",
    fs.ForeignKeyDeferrability.DEFERRABLE_INITIALLY_IMMEDIATE: ("DEFERRABLE INITIALLY IMMEDIATE"),
    fs.ForeignKeyDeferrability.DEFERRABLE_INITIALLY_DEFERRED: ("DEFERRABLE INITIALLY DEFERRED"),
}


def _quote(identifier: str) -> str:
    if type(identifier) is not str or not identifier:
        raise FamilySchemaRenderError("identifier must be non-empty text")
    if '"' in identifier or "\x00" in identifier:
        raise FamilySchemaRenderError("identifier is outside the closed grammar")
    return f'"{identifier}"'


def _text_literal(value: str) -> str:
    if type(value) is not str or "\x00" in value:
        raise FamilySchemaRenderError("TEXT literal is outside the closed grammar")
    return "'" + value.replace("'", "''") + "'"


def _literal(value: object) -> str:
    if value is None:
        return "NULL"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise FamilySchemaRenderError("REAL literal must be finite")
        if value == 0.0:
            return "0.0"
        return format(value, ".17g")
    if type(value) is str:
        return _text_literal(value)
    if type(value) is bytes:
        return "X'" + value.hex().upper() + "'"
    raise FamilySchemaRenderError(f"unknown literal type: {type(value).__name__}")


def _canonical_text_bytes_sql(payload: bytes) -> str:
    if type(payload) is not bytes:
        raise FamilySchemaRenderError("canonical text payload must be bytes")
    return "CAST(X'" + payload.hex().upper() + "' AS TEXT)"


def _predecessor_offset_sql(value_sql: str, offset: int) -> str:
    if type(offset) is not int or offset != -1:
        raise FamilySchemaRenderError("the closed ordinal-offset lowering permits predecessor offset -1 only")
    return f"({value_sql} - 1)"


def _canonical_manifest_value(value: object) -> object:
    """Convert closed Python values to a type-preserving canonical JSON tree."""

    if isinstance(value, Enum):
        return {
            "enum_type": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": value.value,
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "dataclass_type": f"{type(value).__module__}.{type(value).__qualname__}",
            "fields": {item.name: _canonical_manifest_value(getattr(value, item.name)) for item in fields(value)},
        }
    if type(value) in (type(None), bool, int, float, str):
        return value
    if type(value) is bytes:
        return {"bytes_hex": value.hex()}
    if type(value) in (tuple, list):
        return [_canonical_manifest_value(item) for item in value]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise FamilySchemaRenderError("canonical manifest mappings require text keys")
        return {key: _canonical_manifest_value(item) for key, item in value.items()}
    if type(value) in (set, frozenset):
        converted = tuple(_canonical_manifest_value(item) for item in value)
        return sorted(
            converted,
            key=lambda item: canonical_json_bytes(item),
        )
    if value is Ellipsis:
        return {"python_singleton": "Ellipsis"}
    raise FamilySchemaRenderError(f"unsupported canonical manifest value: {type(value).__name__}")


@dataclass(slots=True)
class _DescriptorSnapshot:
    """Ephemeral per-bind memo for immutable descriptor fragments."""

    module_artifacts: dict[tuple[ModuleType, int], dict[str, object]] = field(default_factory=dict)
    normalized_sources: dict[object, str] = field(default_factory=dict)
    code_descriptors: dict[CodeType, dict[str, object]] = field(default_factory=dict)
    nested_code_objects: dict[CodeType, tuple[CodeType, ...]] = field(default_factory=dict)
    instructions: dict[CodeType, tuple[dis.Instruction, ...]] = field(default_factory=dict)
    loaded_attribute_paths: dict[
        tuple[CodeType, str],
        tuple[tuple[str, ...], ...],
    ] = field(default_factory=dict)
    dynamic_getattr_access: dict[tuple[CodeType, str], bool] = field(default_factory=dict)


_ACTIVE_DESCRIPTOR_SNAPSHOT: ContextVar[_DescriptorSnapshot | None] = ContextVar(
    "family_schema_descriptor_snapshot",
    default=None,
)


@contextmanager
def _descriptor_snapshot() -> Iterator[_DescriptorSnapshot]:
    current = _ACTIVE_DESCRIPTOR_SNAPSHOT.get()
    if current is not None:
        yield current
        return
    snapshot = _DescriptorSnapshot()
    token = _ACTIVE_DESCRIPTOR_SNAPSHOT.set(snapshot)
    try:
        yield snapshot
    finally:
        _ACTIVE_DESCRIPTOR_SNAPSHOT.reset(token)


def _code_descriptor(code: CodeType) -> dict[str, object]:
    snapshot = _ACTIVE_DESCRIPTOR_SNAPSHOT.get()
    if snapshot is not None and code in snapshot.code_descriptors:
        return snapshot.code_descriptors[code]
    constants: list[object] = []
    for value in code.co_consts:
        constants.append(
            {"code": _code_descriptor(value)} if type(value) is CodeType else _canonical_manifest_value(value)
        )
    descriptor: dict[str, object] = {
        "argcount": code.co_argcount,
        "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount,
        "nlocals": code.co_nlocals,
        "flags": code.co_flags,
        "bytecode_hex": code.co_code.hex(),
        "constants": constants,
        "names": list(code.co_names),
        "varnames": list(code.co_varnames),
        "freevars": list(code.co_freevars),
        "cellvars": list(code.co_cellvars),
    }
    if snapshot is not None:
        snapshot.code_descriptors[code] = descriptor
    return descriptor


def _normalized_source(value: object) -> str:
    snapshot = _ACTIVE_DESCRIPTOR_SNAPSHOT.get()
    if snapshot is not None and value in snapshot.normalized_sources:
        return snapshot.normalized_sources[value]
    try:
        source = inspect.getsource(value)
    except (OSError, TypeError) as exc:
        raise FamilySchemaRenderError("implementation source closure is unavailable") from exc
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    if snapshot is not None:
        snapshot.normalized_sources[value] = normalized
    return normalized


def _python_runtime_descriptor() -> dict[str, object]:
    return {
        "implementation": sys.implementation.name,
        "version": list(sys.version_info[:3]),
        "cache_tag": sys.implementation.cache_tag,
    }


def _module_version(module: ModuleType) -> object:
    value = getattr(module, "__version__", None)
    if type(value) in (type(None), bool, int, float, str, tuple, list):
        return _canonical_manifest_value(value)
    return None


def _require_loaded_module(module_name: object, error: str) -> ModuleType:
    """Resolve an implementation owner without executing a dynamic import."""

    if type(module_name) is not str or not module_name:
        raise FamilySchemaRenderError(error)
    module = sys.modules.get(module_name)
    if type(module) is not ModuleType:
        raise FamilySchemaRenderError(error)
    return module


def _module_artifact_descriptor(
    module: ModuleType,
    dependency_depth: int = 2,
) -> dict[str, object]:
    snapshot = _ACTIVE_DESCRIPTOR_SNAPSHOT.get()
    cache_key = (module, dependency_depth)
    if snapshot is not None and cache_key in snapshot.module_artifacts:
        return snapshot.module_artifacts[cache_key]
    descriptor = _uncached_module_artifact_descriptor(
        module,
        dependency_depth,
    )
    if snapshot is not None:
        snapshot.module_artifacts[cache_key] = descriptor
    return descriptor


class _ModuleScopeImportVisitor(ast.NodeVisitor):
    """Collect imports that can bind module globals, excluding local scopes."""

    def __init__(self, tree: ast.Module) -> None:
        self._direct_statement_ids = {id(statement) for statement in tree.body}
        self.imports: list[tuple[str, str, str | None, int]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            binding = alias.asname or alias.name.split(".", maxsplit=1)[0]
            self.imports.append(("IMPORT", alias.name, binding, 0))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module_name = node.module or ""
        for alias in node.names:
            binding = None if alias.name == "*" else (alias.asname or alias.name)
            kind = (
                "DIRECT_WILDCARD"
                if binding is None and id(node) in self._direct_statement_ids
                else "FROM"
            )
            self.imports.append((kind, module_name, binding, node.level))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        del node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        del node

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        del node

    def visit_Lambda(self, node: ast.Lambda) -> None:
        del node


def _resolved_import_name(
    module: ModuleType,
    imported_name: str,
    level: int,
) -> str:
    if level == 0:
        return imported_name
    package = getattr(module, "__package__", None)
    if type(package) is not str or not package:
        raise FamilySchemaRenderError(
            "relative source import has no package identity"
        )
    try:
        return importlib.util.resolve_name("." * level + imported_name, package)
    except (ImportError, ValueError) as exc:
        raise FamilySchemaRenderError(
            "relative source import could not be resolved"
        ) from exc


def _static_import_bindings(
    module: ModuleType,
    normalized_source: str,
) -> tuple[tuple[str, ...], tuple[ModuleType, ...]]:
    try:
        tree = ast.parse(normalized_source)
    except SyntaxError as exc:
        raise FamilySchemaRenderError(
            "module implementation source is not valid Python"
        ) from exc
    visitor = _ModuleScopeImportVisitor(tree)
    visitor.visit(tree)
    module_globals = vars(module)
    binding_names: set[str] = set()
    dependency_modules: dict[str, ModuleType] = {}
    for kind, imported_name, binding_name, level in visitor.imports:
        if binding_name is None:
            if kind != "DIRECT_WILDCARD":
                continue
            resolved = _resolved_import_name(module, imported_name, level)
            wildcard_module = sys.modules.get(resolved)
            if type(wildcard_module) is not ModuleType:
                raise FamilySchemaRenderError(
                    "unconditional wildcard source import is not loaded"
                )
            dependency_modules[wildcard_module.__name__] = wildcard_module
            exported = getattr(wildcard_module, "__all__", None)
            export_names = (
                tuple(exported)
                if type(exported) in (tuple, list)
                and all(type(name) is str for name in exported)
                else tuple(
                    name
                    for name in vars(wildcard_module)
                    if not name.startswith("_")
                )
            )
            for name in export_names:
                if name in module_globals and module_globals[name] is getattr(
                    wildcard_module,
                    name,
                    object(),
                ):
                    binding_names.add(name)
            continue
        if binding_name not in module_globals:
            continue
        binding_names.add(binding_name)
        bound = module_globals[binding_name]
        if type(bound) is ModuleType:
            dependency_modules[bound.__name__] = bound
            continue
        defining_name = getattr(bound, "__module__", None)
        if type(defining_name) is str and defining_name != "builtins":
            defining_module = sys.modules.get(defining_name)
            if type(defining_module) is ModuleType:
                dependency_modules[defining_module.__name__] = defining_module
    return (
        tuple(sorted(binding_names)),
        tuple(dependency_modules[name] for name in sorted(dependency_modules)),
    )


def _uncached_module_artifact_descriptor(
    module: ModuleType,
    dependency_depth: int,
) -> dict[str, object]:
    spec = getattr(module, "__spec__", None)
    if spec is None or type(getattr(spec, "name", None)) is not str:
        raise FamilySchemaRenderError("module implementation artifact has no import specification")
    loader = getattr(spec, "loader", None)
    loader_type = None if loader is None else f"{type(loader).__module__}.{type(loader).__qualname__}"
    source: str | None = None
    get_source = getattr(loader, "get_source", None)
    if callable(get_source):
        try:
            source = get_source(spec.name)
        except Exception as exc:
            raise FamilySchemaRenderError("module implementation source could not be read") from exc
    if source is not None:
        if type(source) is not str:
            raise FamilySchemaRenderError("module implementation source must be text")
        normalized = source.replace("\r\n", "\n").replace("\r", "\n")
        descriptor: dict[str, object] = {
            "module_name": spec.name,
            "artifact_kind": "NORMALIZED_PYTHON_SOURCE",
            "loader_type": loader_type,
            "module_version": _module_version(module),
            "source_lf_utf8_hex": normalized.encode("utf-8").hex(),
            "source_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        }
        binding_names, static_dependency_modules = _static_import_bindings(
            module,
            normalized,
        )
        module_globals = vars(module)
        native_symbols: dict[str, dict[str, object]] = {}
        for name in binding_names:
            value = module_globals[name]
            if not inspect.isbuiltin(value):
                continue
            defining_name = getattr(value, "__module__", None)
            if type(defining_name) is not str or defining_name == "builtins":
                continue
            defining_module = sys.modules.get(defining_name)
            if type(defining_module) is not ModuleType:
                raise FamilySchemaRenderError(
                    "native module dependency owner is not already loaded"
                )
            native_symbols[name] = {
                "symbol": (f"{defining_name}.{getattr(value, '__qualname__', getattr(value, '__name__', name))}"),
                "artifact": _module_artifact_descriptor(
                    defining_module,
                    dependency_depth=0,
                ),
            }
        descriptor["native_symbols"] = [{"name": name, **native_symbols[name]} for name in sorted(native_symbols)]
        if dependency_depth > 0:
            dependency_modules = {
                value.__name__: value
                for value in static_dependency_modules
                if value is not module
            }
            descriptor["imported_modules"] = [
                _module_artifact_descriptor(
                    dependency_modules[name],
                    dependency_depth=dependency_depth - 1,
                )
                for name in sorted(dependency_modules)
            ]
        else:
            descriptor["imported_modules"] = []
        return descriptor
    origin = getattr(spec, "origin", None)
    if origin in ("built-in", "frozen"):
        return {
            "module_name": spec.name,
            "artifact_kind": str(origin).upper().replace("-", "_"),
            "loader_type": loader_type,
            "module_version": _module_version(module),
            "python_runtime": _python_runtime_descriptor(),
        }
    if type(origin) is str:
        try:
            binary = Path(origin).read_bytes()
        except OSError as exc:
            raise FamilySchemaRenderError("module implementation binary could not be read") from exc
        return {
            "module_name": spec.name,
            "artifact_kind": "NATIVE_OR_BYTECODE_BINARY",
            "loader_type": loader_type,
            "module_version": _module_version(module),
            "origin_suffixes": list(Path(origin).suffixes),
            "binary_size": len(binary),
            "binary_sha256": hashlib.sha256(binary).hexdigest(),
            "python_runtime": _python_runtime_descriptor(),
        }
    raise FamilySchemaRenderError("module implementation artifact is neither source, runtime, nor binary bound")


def _nested_code_objects(code: CodeType) -> tuple[CodeType, ...]:
    snapshot = _ACTIVE_DESCRIPTOR_SNAPSHOT.get()
    if snapshot is not None and code in snapshot.nested_code_objects:
        return snapshot.nested_code_objects[code]
    nested = tuple(
        child for value in code.co_consts if type(value) is CodeType for child in _nested_code_objects(value)
    )
    result = (code, *nested)
    if snapshot is not None:
        snapshot.nested_code_objects[code] = result
    return result


def _code_instructions(code: CodeType) -> tuple[dis.Instruction, ...]:
    snapshot = _ACTIVE_DESCRIPTOR_SNAPSHOT.get()
    if snapshot is not None and code in snapshot.instructions:
        return snapshot.instructions[code]
    instructions = tuple(dis.get_instructions(code))
    if snapshot is not None:
        snapshot.instructions[code] = instructions
    return instructions


def _loaded_attribute_paths(
    code: CodeType,
    name: str,
) -> tuple[tuple[str, ...], ...]:
    snapshot = _ACTIVE_DESCRIPTOR_SNAPSHOT.get()
    cache_key = (code, name)
    if snapshot is not None and cache_key in snapshot.loaded_attribute_paths:
        return snapshot.loaded_attribute_paths[cache_key]
    paths: list[tuple[str, ...]] = []
    load_ops = {"LOAD_GLOBAL", "LOAD_NAME", "LOAD_DEREF"}
    for nested_code in _nested_code_objects(code):
        instructions = _code_instructions(nested_code)
        for ordinal, instruction in enumerate(instructions):
            if instruction.opname not in load_ops or instruction.argval != name:
                continue
            attributes: list[str] = []
            cursor = ordinal + 1
            while cursor < len(instructions) and instructions[cursor].opname in {
                "LOAD_ATTR",
                "LOAD_METHOD",
            }:
                attributes.append(str(instructions[cursor].argval))
                cursor += 1
            if attributes:
                paths.append(tuple(attributes))
                continue
            if (
                ordinal > 0
                and instructions[ordinal - 1].opname in load_ops
                and instructions[ordinal - 1].argval == "getattr"
                and cursor < len(instructions)
                and instructions[cursor].opname == "LOAD_CONST"
                and type(instructions[cursor].argval) is str
            ):
                paths.append((instructions[cursor].argval,))
    result = tuple(dict.fromkeys(paths))
    if snapshot is not None:
        snapshot.loaded_attribute_paths[cache_key] = result
    return result


def _loaded_module_attributes(code: CodeType, name: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(path[0] for path in _loaded_attribute_paths(code, name)))


def _has_dynamic_getattr_access(code: CodeType, name: str) -> bool:
    snapshot = _ACTIVE_DESCRIPTOR_SNAPSHOT.get()
    cache_key = (code, name)
    if snapshot is not None and cache_key in snapshot.dynamic_getattr_access:
        return snapshot.dynamic_getattr_access[cache_key]
    load_ops = {"LOAD_GLOBAL", "LOAD_NAME", "LOAD_DEREF"}
    for nested_code in _nested_code_objects(code):
        instructions = _code_instructions(nested_code)
        for ordinal, instruction in enumerate(instructions):
            if (
                instruction.opname not in load_ops
                or instruction.argval != name
                or ordinal == 0
                or instructions[ordinal - 1].opname not in load_ops
                or instructions[ordinal - 1].argval != "getattr"
            ):
                continue
            following = instructions[ordinal + 1] if ordinal + 1 < len(instructions) else None
            if following is None or following.opname != "LOAD_CONST" or type(following.argval) is not str:
                if snapshot is not None:
                    snapshot.dynamic_getattr_access[cache_key] = True
                return True
    if snapshot is not None:
        snapshot.dynamic_getattr_access[cache_key] = False
    return False


_RuntimeDependencyRefs = dict[int, tuple[object, str]]


def _module_dependency_descriptor(
    module: ModuleType,
    attribute_paths: tuple[tuple[str, ...], ...],
    active: set[str],
    object_refs: _RuntimeDependencyRefs,
) -> dict[str, object]:
    if not attribute_paths:
        raise FamilySchemaRenderError("module implementation dependency is not attribute-resolved")
    return {
        "module_artifact": _module_artifact_descriptor(module),
        "material_member_paths": [
            {
                "path": list(path),
                "implementation": _attribute_path_descriptor(
                    module,
                    path,
                    active,
                    object_refs,
                ),
            }
            for path in attribute_paths
        ],
    }


def _claim_runtime_dependency_ref(
    value: object,
    object_refs: _RuntimeDependencyRefs,
) -> tuple[str, bool]:
    identity = id(value)
    existing = object_refs.get(identity)
    if existing is not None:
        if existing[0] is not value:
            raise FamilySchemaRenderError("runtime dependency identity was reused during one snapshot")
        return existing[1], True
    reference = f"RUNTIME_DEPENDENCY_{len(object_refs) + 1:04d}"
    object_refs[identity] = (value, reference)
    return reference, False


def _native_module_singleton_descriptor(
    value: object,
    active: set[str],
    object_refs: _RuntimeDependencyRefs,
    attributes: tuple[str, ...],
    reference: str,
) -> dict[str, object] | None:
    """Close an explicitly immutable native module singleton.

    Opaque extension objects are accepted only when their exact native type
    implements the standard singleton reduction/copy protocol, all three
    protocol methods return the same object, and the reduction name resolves
    back to that exact object in the defining module.  The extension binary,
    Python runtime, native method owners, canonical export, and aliases are all
    material.  Any other opaque object remains unsupported.
    """

    value_type = type(value)
    protocol_names = ("__reduce__", "__copy__", "__deepcopy__")
    for name in protocol_names:
        if name not in vars(value_type) or not inspect.ismethoddescriptor(vars(value_type)[name]):
            return None

    defining_name = getattr(value_type, "__module__", None)
    if type(defining_name) is not str or defining_name == "builtins":
        return None
    defining_module = sys.modules.get(defining_name)
    if type(defining_module) is not ModuleType:
        return None
    module_artifact = _module_artifact_descriptor(defining_module)
    if module_artifact["artifact_kind"] != "NATIVE_OR_BYTECODE_BINARY":
        return None

    try:
        reduction_name = value.__reduce__()
        copy_value = value.__copy__()
        deepcopy_value = value.__deepcopy__({})
    except Exception:
        return None
    if (
        type(reduction_name) is not str
        or not reduction_name.isidentifier()
        or copy_value is not value
        or deepcopy_value is not value
    ):
        return None
    if vars(defining_module).get(reduction_name) is not value:
        raise FamilySchemaRenderError(
            "native module singleton reduction export does not resolve to the exact dependency"
        )
    export_aliases = tuple(
        sorted(
            name
            for name, candidate in vars(defining_module).items()
            if type(name) is str and name.isidentifier() and candidate is value
        )
    )
    if reduction_name not in export_aliases:
        raise FamilySchemaRenderError("native module singleton export identity is inconsistent")
    return {
        "runtime_dependency_id": reference,
        "native_module_singleton": {
            "export_name": reduction_name,
            "reduction_name": reduction_name,
            "export_aliases": list(export_aliases),
            "module_artifact": module_artifact,
            "runtime_object_type": _type_dependency_descriptor(
                value_type,
                active,
                (*attributes, *protocol_names),
                object_refs,
            ),
            "copy_returns_self": True,
            "deepcopy_returns_self": True,
        },
    }


def _native_type_member_descriptor(value: object) -> dict[str, object] | None:
    if not (
        inspect.ismethoddescriptor(value) or inspect.isgetsetdescriptor(value) or inspect.ismemberdescriptor(value)
    ):
        return None
    owner = getattr(value, "__objclass__", None)
    name = getattr(value, "__name__", None)
    if not isinstance(owner, type) or type(name) is not str or not name:
        raise FamilySchemaRenderError("native runtime attribute has no exact owner")
    try:
        owner_member = inspect.getattr_static(owner, name)
    except AttributeError as exc:
        raise FamilySchemaRenderError("native runtime attribute is absent from its exact owner") from exc
    if owner_member is not value:
        raise FamilySchemaRenderError("native runtime attribute identity does not match its exact owner")
    return {
        "descriptor_kind": "NATIVE_TYPE_MEMBER",
        "identity": f"{owner.__module__}.{owner.__qualname__}.{name}",
        "owner_module_artifact": _module_artifact_descriptor(
            _require_loaded_module(
                owner.__module__,
                "native runtime attribute owner is not already loaded",
            )
        ),
        "python_runtime": _python_runtime_descriptor(),
    }


def _runtime_dependency_value(
    value: object,
    active: set[str],
    object_refs: _RuntimeDependencyRefs | None = None,
    attributes: tuple[str, ...] = (),
) -> object:
    object_refs = {} if object_refs is None else object_refs
    if isinstance(value, Enum):
        return {
            "enum_type": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": _runtime_dependency_value(value.value, active, object_refs),
        }
    if is_dataclass(value) and not isinstance(value, type):
        reference, seen = _claim_runtime_dependency_ref(value, object_refs)
        if seen:
            return {"runtime_dependency_ref": reference}
        return {
            "runtime_dependency_id": reference,
            "dataclass_type": f"{type(value).__module__}.{type(value).__qualname__}",
            "type_implementation": _type_dependency_descriptor(
                type(value),
                active,
                attributes,
                object_refs,
            ),
            "fields": {
                item.name: _runtime_dependency_value(
                    getattr(value, item.name),
                    active,
                    object_refs,
                )
                for item in fields(value)
            },
        }
    if type(value) in (type(None), bool, int, str):
        return value
    if type(value) is float:
        return {"float64_be_hex": struct.pack(">d", value).hex()}
    if type(value) is bytes:
        return {"bytes_hex": value.hex()}
    if type(value) is datetime.datetime:
        reference, seen = _claim_runtime_dependency_ref(value, object_refs)
        if seen:
            return {"runtime_dependency_ref": reference}
        return {
            "runtime_dependency_id": reference,
            "datetime": {
                "year": value.year,
                "month": value.month,
                "day": value.day,
                "hour": value.hour,
                "minute": value.minute,
                "second": value.second,
                "microsecond": value.microsecond,
                "fold": value.fold,
                "tzinfo": _runtime_dependency_value(
                    value.tzinfo,
                    active,
                    object_refs,
                ),
            },
        }
    if type(value) is datetime.date:
        reference, seen = _claim_runtime_dependency_ref(value, object_refs)
        if seen:
            return {"runtime_dependency_ref": reference}
        return {
            "runtime_dependency_id": reference,
            "date": {"year": value.year, "month": value.month, "day": value.day},
        }
    if type(value) is datetime.time:
        reference, seen = _claim_runtime_dependency_ref(value, object_refs)
        if seen:
            return {"runtime_dependency_ref": reference}
        return {
            "runtime_dependency_id": reference,
            "time": {
                "hour": value.hour,
                "minute": value.minute,
                "second": value.second,
                "microsecond": value.microsecond,
                "fold": value.fold,
                "tzinfo": _runtime_dependency_value(
                    value.tzinfo,
                    active,
                    object_refs,
                ),
            },
        }
    if type(value) is datetime.timedelta:
        reference, seen = _claim_runtime_dependency_ref(value, object_refs)
        if seen:
            return {"runtime_dependency_ref": reference}
        return {
            "runtime_dependency_id": reference,
            "timedelta": {
                "days": value.days,
                "seconds": value.seconds,
                "microseconds": value.microseconds,
            },
        }
    if type(value) is datetime.timezone:
        reference, seen = _claim_runtime_dependency_ref(value, object_refs)
        if seen:
            return {"runtime_dependency_ref": reference}
        return {
            "runtime_dependency_id": reference,
            "timezone": {
                "offset": _runtime_dependency_value(
                    value.utcoffset(None),
                    active,
                    object_refs,
                ),
                "name": value.tzname(None),
            },
        }
    if type(value) is functools.partial:
        reference, seen = _claim_runtime_dependency_ref(value, object_refs)
        if seen:
            return {"runtime_dependency_ref": reference}
        return {
            "runtime_dependency_id": reference,
            "partial": {
                "function": _runtime_dependency_value(
                    value.func,
                    active,
                    object_refs,
                ),
                "args": _runtime_dependency_value(
                    value.args,
                    active,
                    object_refs,
                ),
                "keywords": _runtime_dependency_value(
                    value.keywords or {},
                    active,
                    object_refs,
                ),
            },
        }
    if type(value) in (tuple, list):
        reference, seen = _claim_runtime_dependency_ref(value, object_refs)
        if seen:
            return {"runtime_dependency_ref": reference}
        return {
            "runtime_dependency_id": reference,
            "sequence_type": type(value).__name__,
            "items": [_runtime_dependency_value(item, active, object_refs) for item in value],
        }
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise FamilySchemaRenderError("runtime dependency mappings require text keys")
        reference, seen = _claim_runtime_dependency_ref(value, object_refs)
        if seen:
            return {"runtime_dependency_ref": reference}
        return {
            "runtime_dependency_id": reference,
            "mapping": {key: _runtime_dependency_value(value[key], active, object_refs) for key in sorted(value)},
        }
    if type(value) in (set, frozenset):
        reference, seen = _claim_runtime_dependency_ref(value, object_refs)
        if seen:
            return {"runtime_dependency_ref": reference}
        try:
            ordered = tuple(
                sorted(
                    value,
                    key=lambda item: canonical_json_bytes(_canonical_manifest_value(item)),
                )
            )
        except (FamilySchemaRenderError, TypeError) as exc:
            raise FamilySchemaRenderError("runtime dependency sets require canonically orderable values") from exc
        return {
            "runtime_dependency_id": reference,
            "set_type": type(value).__name__,
            "items": [_runtime_dependency_value(item, active, object_refs) for item in ordered],
        }
    if value is Ellipsis:
        return {"python_singleton": "Ellipsis"}
    native_type_member = _native_type_member_descriptor(value)
    if native_type_member is not None:
        return native_type_member
    if inspect.ismethod(value):
        return {
            "bound_method": _function_descriptor(
                value.__func__,
                active,
                object_refs,
            ),
            "receiver": _runtime_dependency_value(
                value.__self__,
                active,
                object_refs,
            ),
        }
    if (
        type(value) in (FunctionType, ModuleType)
        or isinstance(value, type)
        or inspect.isbuiltin(value)
        or isinstance(value, re.Pattern)
    ):
        return _global_dependency_descriptor(
            value,
            active,
            object_refs=object_refs,
        )

    value_type = type(value)
    type_identity = f"{value_type.__module__}.{value_type.__qualname__}"
    reference, seen = _claim_runtime_dependency_ref(value, object_refs)
    if seen:
        return {"runtime_dependency_ref": reference}
    if value_type.__module__ == "_json" and value_type.__qualname__ == "Scanner":
        return {
            "runtime_dependency_id": reference,
            "runtime_object_type": _type_dependency_descriptor(
                value_type,
                active,
                object_refs=object_refs,
            ),
            "scanner_state": {
                name: _runtime_dependency_value(
                    getattr(value, name),
                    active,
                    object_refs,
                )
                for name in (
                    "object_hook",
                    "object_pairs_hook",
                    "parse_constant",
                    "parse_float",
                    "parse_int",
                    "strict",
                )
            },
        }
    try:
        state = vars(value)
    except TypeError:
        state = None
    if state is None:
        native_singleton = _native_module_singleton_descriptor(
            value,
            active,
            object_refs,
            attributes,
            reference,
        )
        if native_singleton is not None:
            return native_singleton
        raise FamilySchemaRenderError(f"unsupported opaque runtime dependency: {type_identity}")
    return {
        "runtime_dependency_id": reference,
        "runtime_object_type": _type_dependency_descriptor(
            value_type,
            active,
            attributes,
            object_refs,
        ),
        "state_kind": "PYTHON_DICT_STATE",
        "state": {name: _runtime_dependency_value(item, active, object_refs) for name, item in sorted(state.items())},
    }


def _global_dependency_descriptor(
    value: object,
    active: set[str],
    attributes: tuple[str, ...] = (),
    object_refs: _RuntimeDependencyRefs | None = None,
) -> object:
    object_refs = {} if object_refs is None else object_refs
    if type(value) is FunctionType:
        return _implementation_descriptor(value, active, object_refs)
    if isinstance(value, type):
        return _type_dependency_descriptor(
            value,
            active,
            attributes,
            object_refs,
        )
    if type(value) is ModuleType:
        raise FamilySchemaRenderError("module implementation dependency must be resolved from bytecode attributes")
    if inspect.isbuiltin(value):
        module_name = getattr(value, "__module__", None)
        symbol = getattr(
            value,
            "__qualname__",
            getattr(value, "__name__", "unknown"),
        )
        descriptor: dict[str, object] = {
            "native_symbol": (f"{module_name}.{symbol}" if module_name else symbol),
            "python_runtime": _python_runtime_descriptor(),
        }
        if module_name not in (None, "builtins"):
            descriptor["module_artifact"] = _module_artifact_descriptor(
                _require_loaded_module(
                    module_name,
                    "native implementation dependency owner is not already loaded",
                )
            )
        receiver = getattr(value, "__self__", None)
        if receiver is not None and type(receiver) not in (ModuleType, type):
            descriptor["bound_receiver"] = _runtime_dependency_value(
                receiver,
                active,
                object_refs,
            )
        return descriptor
    if isinstance(value, re.Pattern):
        return {
            "regex_pattern": value.pattern,
            "regex_flags": value.flags,
            "regex_runtime": _python_runtime_descriptor(),
            "regex_module": _module_artifact_descriptor(re),
        }
    return _runtime_dependency_value(
        value,
        active,
        object_refs,
        attributes,
    )


def _type_dependency_descriptor(
    value: type,
    active: set[str],
    attributes: tuple[str, ...] = (),
    object_refs: _RuntimeDependencyRefs | None = None,
) -> dict[str, object]:
    object_refs = {} if object_refs is None else object_refs
    defining_name = getattr(value, "__module__", None)
    if type(defining_name) is not str:
        raise FamilySchemaRenderError("type implementation dependency has no defining module")
    defining_module = _require_loaded_module(
        defining_name,
        "type implementation dependency owner is not already loaded",
    )
    try:
        source = _normalized_source(value)
    except FamilySchemaRenderError:
        source = None
    identity = f"{defining_name}.{value.__qualname__}"
    if identity in active:
        return {"implementation_ref": identity}
    nested_active = {*active, identity}
    explicit_material_names = bool(attributes)
    material_names = attributes
    if not explicit_material_names:
        material_names = tuple(
            name
            for name in ("__new__", "__init__", "__call__")
            if name in vars(value) and type(vars(value)[name]) in (FunctionType, staticmethod, classmethod, property)
        )
    material_members: list[dict[str, object]] = []
    for member_name in material_names:
        owner = next(
            (candidate for candidate in (*value.__mro__, *type(value).__mro__) if member_name in vars(candidate)),
            None,
        )
        if owner is None:
            raise FamilySchemaRenderError(f"type implementation member is absent: {identity}.{member_name}")
        raw_member = vars(owner)[member_name]
        try:
            callable_descriptor = _class_member_descriptor(
                raw_member,
                nested_active,
                object_refs,
            )
        except FamilySchemaRenderError:
            if explicit_material_names:
                raise
            continue
        if callable_descriptor is not None:
            implementation: object = callable_descriptor
        elif inspect.ismethoddescriptor(raw_member) or inspect.isbuiltin(raw_member):
            implementation = {
                "descriptor_kind": "NATIVE_TYPE_MEMBER",
                "identity": (f"{owner.__module__}.{owner.__qualname__}.{member_name}"),
                "owner_module_artifact": _module_artifact_descriptor(
                    _require_loaded_module(
                        owner.__module__,
                        "native type member owner is not already loaded",
                    )
                ),
                "python_runtime": _python_runtime_descriptor(),
            }
        else:
            implementation = {
                "descriptor_kind": "CLASS_ATTRIBUTE",
                "value": _class_attribute_descriptor(
                    raw_member,
                    nested_active,
                    object_refs,
                ),
            }
        material_members.append(
            {
                "name": member_name,
                "owner": f"{owner.__module__}.{owner.__qualname__}",
                "implementation": implementation,
            }
        )
    return {
        "type_identity": identity,
        "bases": [f"{base.__module__}.{base.__qualname__}" for base in value.__bases__],
        "source_lf_utf8_hex": (None if source is None else source.encode("utf-8").hex()),
        "module_artifact": _module_artifact_descriptor(defining_module),
        "material_members": material_members,
    }


def _builtin_dependency_descriptor(name: str) -> dict[str, object]:
    return {
        "builtin_ref": f"builtins.{name}",
        "python_runtime": _python_runtime_descriptor(),
    }


def _function_descriptor(
    function: FunctionType,
    active: set[str],
    object_refs: _RuntimeDependencyRefs | None = None,
) -> dict[str, object]:
    object_refs = {} if object_refs is None else object_refs
    identity = f"{function.__module__}.{function.__qualname__}"
    if identity in active:
        return {"implementation_ref": identity}
    nested_active = {*active, identity}
    globals_payload: list[dict[str, object]] = []
    referenced_names = {name for code in _nested_code_objects(function.__code__) for name in code.co_names}
    for name in sorted(referenced_names):
        if name in function.__globals__:
            dependency = function.__globals__[name]
            globals_payload.append(
                {
                    "name": name,
                    "value": _loaded_dependency_descriptor(
                        dependency,
                        function.__code__,
                        name,
                        nested_active,
                        object_refs,
                    ),
                }
            )
        elif hasattr(builtins, name):
            globals_payload.append(
                {
                    "name": name,
                    "value": _builtin_dependency_descriptor(name),
                }
            )
    closure_payload = []
    if function.__closure__ is not None:
        for name, cell in zip(
            function.__code__.co_freevars,
            function.__closure__,
            strict=True,
        ):
            try:
                cell_value = cell.cell_contents
            except ValueError as exc:
                raise FamilySchemaRenderError("implementation closure contains an empty cell") from exc
            closure_payload.append(
                {
                    "name": name,
                    "value": _loaded_dependency_descriptor(
                        cell_value,
                        function.__code__,
                        name,
                        nested_active,
                        object_refs,
                    ),
                }
            )
    return {
        "implementation_kind": "PYTHON_FUNCTION",
        "identity": identity,
        "source_lf_utf8_hex": _normalized_source(function).encode("utf-8").hex(),
        "code": _code_descriptor(function.__code__),
        "defaults": _runtime_dependency_value(
            function.__defaults__,
            nested_active,
            object_refs,
        ),
        "keyword_defaults": _runtime_dependency_value(
            function.__kwdefaults__ or {},
            nested_active,
            object_refs,
        ),
        "closure": closure_payload,
        "globals": globals_payload,
    }


def _class_member_descriptor(
    member: object,
    active: set[str],
    object_refs: _RuntimeDependencyRefs,
) -> dict[str, object] | None:
    if type(member) is FunctionType:
        return {
            "descriptor_kind": "INSTANCE_METHOD",
            "callable": _function_descriptor(member, active, object_refs),
        }
    if type(member) is staticmethod:
        return {
            "descriptor_kind": "STATIC_METHOD",
            "callable": _function_descriptor(member.__func__, active, object_refs),
        }
    if type(member) is classmethod:
        return {
            "descriptor_kind": "CLASS_METHOD",
            "callable": _function_descriptor(member.__func__, active, object_refs),
        }
    if type(member) is property:
        return {
            "descriptor_kind": "PROPERTY",
            "getter": (None if member.fget is None else _function_descriptor(member.fget, active, object_refs)),
            "setter": (None if member.fset is None else _function_descriptor(member.fset, active, object_refs)),
            "deleter": (None if member.fdel is None else _function_descriptor(member.fdel, active, object_refs)),
        }
    return None


def _class_attribute_descriptor(
    value: object,
    active: set[str],
    object_refs: _RuntimeDependencyRefs,
) -> object:
    if type(value) is ModuleType:
        return {"module_artifact": _module_artifact_descriptor(value)}
    return _global_dependency_descriptor(
        value,
        active,
        object_refs=object_refs,
    )


def _attribute_path_descriptor(
    root: object,
    path: tuple[str, ...],
    active: set[str],
    object_refs: _RuntimeDependencyRefs,
) -> object:
    if not path:
        raise FamilySchemaRenderError("runtime dependency attribute path is empty")
    current = root
    for ordinal, attribute in enumerate(path):
        try:
            current = inspect.getattr_static(current, attribute)
        except AttributeError as exc:
            raise FamilySchemaRenderError(
                f"runtime dependency attribute is absent: {'.'.join(path[: ordinal + 1])}"
            ) from exc
        if ordinal < len(path) - 1:
            if type(current) in (staticmethod, classmethod):
                current = current.__func__
            elif type(current) is property or inspect.ismethoddescriptor(current):
                raise FamilySchemaRenderError("runtime dependency attribute path crosses an executable descriptor")
    callable_descriptor = _class_member_descriptor(
        current,
        active,
        object_refs,
    )
    if callable_descriptor is not None:
        return callable_descriptor
    native_type_member = _native_type_member_descriptor(current)
    if native_type_member is not None:
        return native_type_member
    return _runtime_dependency_value(current, active, object_refs)


def _loaded_dependency_descriptor(
    dependency: object,
    code: CodeType,
    name: str,
    active: set[str],
    object_refs: _RuntimeDependencyRefs,
) -> object:
    if _has_dynamic_getattr_access(code, name):
        raise FamilySchemaRenderError(f"dynamic getattr cannot close runtime dependency: {name}")
    attribute_paths = _loaded_attribute_paths(code, name)
    if type(dependency) is ModuleType:
        return _module_dependency_descriptor(
            dependency,
            attribute_paths,
            active,
            object_refs,
        )
    root = _global_dependency_descriptor(
        dependency,
        active,
        _loaded_module_attributes(code, name),
        object_refs,
    )
    if not attribute_paths:
        return root
    return {
        "root_dependency": root,
        "material_attribute_paths": [
            {
                "path": list(path),
                "implementation": _attribute_path_descriptor(
                    dependency,
                    path,
                    active,
                    object_refs,
                ),
            }
            for path in attribute_paths
        ],
    }


def _class_descriptor(
    implementation_class: type,
    active: set[str],
    object_refs: _RuntimeDependencyRefs | None = None,
) -> dict[str, object]:
    object_refs = {} if object_refs is None else object_refs
    identity = f"{implementation_class.__module__}.{implementation_class.__qualname__}"
    if identity in active:
        return {"implementation_ref": identity}
    nested_active = {*active, identity}
    members = tuple(
        (
            name,
            member,
            _class_member_descriptor(member, nested_active, object_refs),
        )
        for name, member in vars(implementation_class).items()
        if name
        not in {
            "__annotations__",
            "__dict__",
            "__doc__",
            "__module__",
            "__qualname__",
            "__weakref__",
        }
    )
    return {
        "implementation_kind": "PYTHON_CLASS",
        "identity": identity,
        "source_lf_utf8_hex": (_normalized_source(implementation_class).encode("utf-8").hex()),
        "bases": [
            (
                _builtin_dependency_descriptor(base.__qualname__)
                if base.__module__ == "builtins"
                else _class_descriptor(base, nested_active, object_refs)
            )
            for base in implementation_class.__bases__
        ],
        "methods": [
            {
                "name": name,
                "descriptor": descriptor,
            }
            for name, _, descriptor in sorted(members)
            if descriptor is not None
        ],
        "class_attributes": [
            {
                "name": name,
                "value": _class_attribute_descriptor(
                    member,
                    nested_active,
                    object_refs,
                ),
            }
            for name, member, descriptor in sorted(members)
            if descriptor is None
        ],
    }


def _implementation_descriptor(
    implementation: object,
    active: set[str] | None = None,
    object_refs: _RuntimeDependencyRefs | None = None,
) -> dict[str, object]:
    if _ACTIVE_DESCRIPTOR_SNAPSHOT.get() is None:
        with _descriptor_snapshot():
            return _implementation_descriptor(
                implementation,
                active,
                object_refs,
            )
    active = set() if active is None else active
    object_refs = {} if object_refs is None else object_refs
    if type(implementation) is FunctionType:
        return _function_descriptor(implementation, active, object_refs)
    if type(implementation) is type:
        return _class_descriptor(implementation, active, object_refs)
    raise FamilySchemaRenderError("implementation evidence requires a Python function or aggregate class")


def _register_binding_value(
    bindings: dict[str, object],
    key: str,
    value: object,
) -> None:
    if key in bindings:
        raise FamilySchemaRenderError(f"duplicate canonical implementation binding key: {key}")
    bindings[key] = value


def _canonical_implementation_binding_map(
    composition: FamilySchemaComposition,
) -> dict[str, object]:
    bindings: dict[str, object] = {}
    for abi in composition.renderer_grammar.semantic_function_abis:
        _register_binding_value(
            bindings,
            f"SEMANTIC_ABI::{abi.operation.value}::{abi.sqlite_name}::{abi.semantic_contract_id}",
            abi,
        )
    _register_binding_value(
        bindings,
        EXACT_V3_VERIFIER_COMPOSITION_BINDING_KEY,
        composition,
    )
    genesis = composition.tables[0]
    for rule in genesis.verification_rules:
        _register_binding_value(
            bindings,
            f"FAMILY_AUTHORITY_GENESIS_V1:TABLE:{genesis.table_name}:{rule.rule_id}",
            rule,
        )
    for component in composition.components:
        for recipe in component.auxiliary_semantic_recipes:
            _register_binding_value(
                bindings,
                auxiliary_semantic_recipe_binding_key(
                    component.component_id,
                    recipe.recipe_id,
                ),
                recipe,
            )
        for table in component.tables:
            for rule in table.verification_rules:
                _register_binding_value(
                    bindings,
                    f"{component.component_id}:TABLE:{table.table_name}:{rule.rule_id}",
                    rule,
                )
        for extension in component.extensions:
            for rule in extension.verification_rules:
                _register_binding_value(
                    bindings,
                    f"{component.component_id}:EXTENSION:{extension.target_table}:{rule.rule_id}",
                    rule,
                )
            for continuity in extension.external_continuity_rules:
                _register_binding_value(
                    bindings,
                    f"{component.component_id}:CONTINUITY:{extension.target_table}:{continuity.verifier_code}",
                    continuity,
                )
        for graph in component.artifact_graph_derivations:
            _register_binding_value(
                bindings,
                f"{component.component_id}:GRAPH:{graph.derivation_id}:{graph.verifier_code}",
                graph,
            )
        for profile in component.external_authority_claim_profiles:
            _register_binding_value(
                bindings,
                external_claim_profile_binding_key(
                    profile.provider_contract_type,
                    profile.provider_capability,
                ),
                profile,
            )
    for requirement in composition.external_provider_requirements:
        _register_binding_value(
            bindings,
            f"EXTERNAL_PROVIDER_CONTRACT::{requirement.provider_contract_type}::{requirement.provider_capability}",
            requirement,
        )
    declared_keys = {
        key
        for requirement in composition.implementation_evidence_requirements.entries
        for key in requirement.canonical_binding_keys
    }
    if set(bindings) != declared_keys:
        raise FamilySchemaRenderError("canonical implementation binding map does not exactly close requirements")
    return bindings


def _implementation_evidence_sha256(
    requirement: fs.ImplementationEvidenceRequirementSpec,
    bound_specs: tuple[tuple[str, object], ...],
    descriptor: dict[str, object],
) -> str:
    canonical_bound_specs = []
    for key, value in bound_specs:
        digest = value if type(value) is _CanonicalBindingDigest else _canonical_binding_digest(value)
        canonical_bound_specs.append(
            {
                "binding_key": key,
                "canonical_bytes_sha256": digest.canonical_bytes_sha256,
                "canonical_byte_length": digest.canonical_byte_length,
            }
        )
    payload = {
        "hash_method": requirement.hash_method.value,
        "hash_method_version": requirement.hash_method_version,
        "target_kind": requirement.target_kind.value,
        "target_id": requirement.target_id,
        "implementation_id": requirement.implementation_id,
        "implementation_source_closure": _deduplicated_descriptor_graph(descriptor),
        "canonical_bound_specs": canonical_bound_specs,
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _deduplicated_descriptor_graph(value: object) -> object:
    """Encode repeated descriptor containers once without omitting material.

    Per-snapshot module/source/code memoization intentionally creates a DAG.
    Canonical JSON has no native reference form, so naively serializing that
    DAG repeats large, identical source and binary artifacts.  This two-pass
    representation keeps the first full value and replaces only subsequent
    occurrences of the exact same container object with a stable traversal
    reference.  Equal-but-distinct containers remain distinct.
    """

    occurrence_counts: dict[int, int] = {}
    expanded: set[int] = set()

    def count(item: object) -> None:
        if type(item) not in (dict, list, tuple):
            return
        identity = id(item)
        occurrence_counts[identity] = occurrence_counts.get(identity, 0) + 1
        if identity in expanded:
            return
        expanded.add(identity)
        if type(item) is dict:
            for key in sorted(item):
                count(item[key])
            return
        for child in item:
            count(child)

    count(value)
    repeated = {identity for identity, occurrences in occurrence_counts.items() if occurrences > 1}
    references: dict[int, str] = {}

    def encode(item: object) -> object:
        if type(item) not in (dict, list, tuple):
            return item
        identity = id(item)
        reference = references.get(identity)
        if reference is not None:
            return {"shared_descriptor_node_ref": reference}
        repeated_item = identity in repeated
        if repeated_item:
            reference = f"SHARED_DESCRIPTOR_NODE_{len(references) + 1:04d}"
            references[identity] = reference
        if type(item) is dict:
            encoded: object = {key: encode(item[key]) for key in sorted(item)}
            value_kind = "MAPPING"
        else:
            encoded = [encode(child) for child in item]
            value_kind = "SEQUENCE"
        if not repeated_item:
            return encoded
        return {
            "shared_descriptor_node_id": reference,
            "value_kind": value_kind,
            "value": encoded,
        }

    return encode(value)


def _canonical_binding_digest(value: object) -> _CanonicalBindingDigest:
    exact_bytes = canonical_json_bytes(_canonical_manifest_value(value))
    return _CanonicalBindingDigest(
        canonical_bytes_sha256=hashlib.sha256(exact_bytes).hexdigest(),
        canonical_byte_length=len(exact_bytes),
    )


def _validate_implementation_shape(
    requirement: fs.ImplementationEvidenceRequirementSpec,
    implementation: object,
    composition: FamilySchemaComposition,
) -> None:
    if requirement.target_kind is fs.ImplementationTargetKind.SQLITE_SEMANTIC_FUNCTION:
        abi = next(
            (
                item
                for item in composition.renderer_grammar.semantic_function_abis
                if item.operation is requirement.semantic_operation
            ),
            None,
        )
        if abi is None:
            raise FamilySchemaRenderError("semantic implementation has no canonical ABI")
        if abi.kind is fs.RendererSemanticFunctionKind.SCALAR:
            if type(implementation) is not FunctionType:
                raise FamilySchemaRenderError("scalar semantic implementation must be a Python function")
            return
        if type(implementation) is not type or any(
            type(vars(implementation).get(name)) is not FunctionType for name in ("step", "finalize")
        ):
            raise FamilySchemaRenderError("aggregate semantic implementation must define step and finalize")
        return
    if type(implementation) is not FunctionType:
        raise FamilySchemaRenderError("named and structural implementations must be Python functions")


def _bind_implementation_evidence(
    registry: tuple[_ImplementationCallableBinding, ...],
    composition: FamilySchemaComposition,
) -> fs.ImplementationEvidenceCatalog:
    if type(registry) is not tuple or any(type(item) is not _ImplementationCallableBinding for item in registry):
        raise TypeError("implementation registry requires exact _ImplementationCallableBinding values")
    required = tuple(
        item
        for item in composition.implementation_evidence_requirements.entries
        if item.evidence_policy is fs.ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256
    )
    expected = tuple((item.target_kind, item.target_id, item.implementation_id) for item in required)
    actual = tuple((item.target_kind, item.target_id, item.implementation_id) for item in registry)
    if actual != expected:
        raise FamilySchemaRenderError("implementation callable bindings must exactly cover internal requirements")
    canonical_bindings = _canonical_implementation_binding_map(composition)
    canonical_binding_digests = {key: _canonical_binding_digest(value) for key, value in canonical_bindings.items()}
    descriptor_cache: dict[object, dict[str, object]] = {}
    evidence: list[fs.ImplementationEvidenceBinding] = []
    with _descriptor_snapshot():
        for requirement, binding in zip(required, registry, strict=True):
            _validate_implementation_shape(
                requirement,
                binding.implementation,
                composition,
            )
            descriptor = descriptor_cache.get(binding.implementation)
            if descriptor is None:
                descriptor = _implementation_descriptor(binding.implementation)
                descriptor_cache[binding.implementation] = descriptor
            bound_specs = tuple((key, canonical_binding_digests[key]) for key in requirement.canonical_binding_keys)
            evidence.append(
                fs.ImplementationEvidenceBinding(
                    target_kind=requirement.target_kind,
                    target_id=requirement.target_id,
                    implementation_id=binding.implementation_id,
                    implementation_sha256=_implementation_evidence_sha256(
                        requirement,
                        bound_specs,
                        descriptor,
                    ),
                    hash_method=requirement.hash_method,
                    hash_method_version=requirement.hash_method_version,
                )
            )
    return fs.ImplementationEvidenceCatalog(
        requirements=composition.implementation_evidence_requirements,
        bindings=tuple(evidence),
    )


_CANONICAL_IMPLEMENTATION_REGISTRY: tuple[
    _ImplementationCallableBinding,
    ...,
] = tuple(_semantic_functions.build_canonical_semantic_callable_bindings())
del _semantic_functions


def _bind_canonical_production_implementation_registry(
    registry: tuple[_ImplementationCallableBinding, ...],
    composition: FamilySchemaComposition,
) -> fs.ImplementationEvidenceCatalog:
    required = tuple(
        item
        for item in composition.implementation_evidence_requirements.entries
        if item.evidence_policy is fs.ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256
    )
    expected_keys = {(item.target_kind, item.target_id, item.implementation_id) for item in required}
    actual_keys = {
        (item.target_kind, item.target_id, item.implementation_id)
        for item in registry
        if type(item) is _ImplementationCallableBinding
    }
    missing = len(expected_keys - actual_keys)
    if type(registry) is not tuple or len(registry) != len(required) or missing:
        raise FamilySchemaUnsealedError(
            "UNSEALED: canonical production implementation registry is incomplete; "
            f"missing {missing} of {len(required)} internal implementations"
        )
    try:
        return _bind_implementation_evidence(registry, composition)
    except (FamilySchemaRenderError, TypeError, ValueError) as exc:
        raise FamilySchemaUnsealedError("UNSEALED: canonical production implementation registry is invalid") from exc


def _bind_implementation_evidence_for_test(
    registry: tuple[_ImplementationCallableBinding, ...],
    composition: FamilySchemaComposition | None = None,
) -> fs.ImplementationEvidenceCatalog:
    canonical = compose_family_schema_v3()
    if composition is None:
        composition = canonical
    if type(composition) is not FamilySchemaComposition:
        raise TypeError("composition must be FamilySchemaComposition")
    if composition != canonical:
        raise FamilySchemaRenderError("composition is not the canonical exact-v3 value")
    return _bind_implementation_evidence(registry, composition)


class _SqlRenderer:
    def __init__(self, grammar: fs.RendererGrammarContract) -> None:
        if type(grammar) is not fs.RendererGrammarContract:
            raise TypeError("renderer grammar must be RendererGrammarContract")
        if grammar != fs.FAMILY_SCHEMA_RENDERER_GRAMMAR:
            raise FamilySchemaRenderError("renderer grammar is not canonical")
        self._grammar = grammar
        self._alias_ordinal = 0
        self._function_names = {item.operation: item.sqlite_name for item in grammar.semantic_function_abis}
        if tuple(self._function_names) != tuple(fs.RendererSemanticOperation):
            raise FamilySchemaRenderError("semantic function ABI is incomplete")

    def _alias(self) -> str:
        self._alias_ordinal += 1
        return f"q{self._alias_ordinal}"

    def _function(
        self,
        operation: fs.RendererSemanticOperation,
        arguments: tuple[str, ...],
    ) -> str:
        name = self._function_names.get(operation)
        abi = next(
            (item for item in self._grammar.semantic_function_abis if item.operation is operation),
            None,
        )
        if name is None or abi is None or len(arguments) != abi.arity:
            raise FamilySchemaRenderError(f"semantic function ABI mismatch: {operation.value}")
        return f"{_quote(name)}(" + ", ".join(arguments) + ")"

    def _column(self, value: fs.ColumnValue, context: _RenderContext) -> str:
        if value.source is fs.RowSource.NEW:
            return f"NEW.{_quote(value.column)}"
        if value.source is fs.RowSource.OLD:
            return f"OLD.{_quote(value.column)}"
        if value.source is fs.RowSource.RELATED:
            if context.related_alias is None:
                raise FamilySchemaRenderError("RELATED column has no closed relation alias")
            return f"{_quote(context.related_alias)}.{_quote(value.column)}"
        if context.current_alias is None:
            return _quote(value.column)
        return f"{_quote(context.current_alias)}.{_quote(value.column)}"

    def _scalar(self, value: object, context: _RenderContext) -> str:
        node_type = type(value)
        if node_type is fs.ColumnValue:
            return self._column(value, context)
        if node_type is fs.LiteralValue:
            return _literal(value.value)
        if node_type is fs.QualifiedColumnValue:
            return f"{_quote(value.relation_alias)}.{_quote(value.column)}"
        if node_type is fs.CanonicalJsonFieldValue:
            path = "$." + ".".join(value.path)
            return self._function(
                fs.RendererSemanticOperation.CANONICAL_JSON_FIELD_VALUE,
                (
                    self._scalar(value.document, context),
                    _text_literal(path),
                    _text_literal(value.scalar_type.value),
                ),
            )
        if node_type is fs.OrdinalOffsetValue:
            return _predecessor_offset_sql(
                self._scalar(value.value, context),
                value.offset,
            )
        if node_type is fs.RelatedCanonicalJsonArrayValueSpec:
            return self._related_json_array_value(value, context)
        if node_type is fs.RelatedCanonicalJsonArrayLengthValueSpec:
            return self._related_json_array_length(value, context)
        raise FamilySchemaRenderError(f"unknown scalar node: {node_type.__name__}")

    def _binding_sql(
        self,
        alias: str,
        bindings: tuple[fs.LookupBinding, ...],
        context: _RenderContext,
    ) -> tuple[str, ...]:
        return tuple(
            f"{_quote(alias)}.{_quote(binding.related_column)} = {self._scalar(binding.value, context)}"
            for binding in bindings
        )

    @staticmethod
    def _and(parts: tuple[str, ...]) -> str:
        if not parts:
            return "1"
        if len(parts) == 1:
            return parts[0]
        return "(" + " AND ".join(parts) + ")"

    @staticmethod
    def _or(parts: tuple[str, ...]) -> str:
        if not parts:
            return "0"
        if len(parts) == 1:
            return parts[0]
        return "(" + " OR ".join(parts) + ")"

    def _where(
        self,
        alias: str,
        bindings: tuple[fs.LookupBinding, ...],
        predicate: object | None,
        context: _RenderContext,
    ) -> str:
        parts = list(self._binding_sql(alias, bindings, context))
        if predicate is not None:
            parts.append(
                self._predicate(
                    predicate,
                    _RenderContext(
                        current_alias=context.current_alias,
                        related_alias=alias,
                    ),
                )
            )
        return self._and(tuple(parts))

    def _exact_lookup(
        self,
        table: str,
        bindings: tuple[fs.LookupBinding, ...],
        column: str,
        context: _RenderContext,
    ) -> str:
        alias = self._alias()
        where = self._where(alias, bindings, None, context)
        relation = f"{_quote(table)} AS {_quote(alias)}"
        return (
            "(CASE WHEN (SELECT COUNT(*) FROM "
            + relation
            + " WHERE "
            + where
            + ") = 1 THEN (SELECT "
            + f"{_quote(alias)}.{_quote(column)} FROM {relation} WHERE {where}) "
            + "ELSE NULL END)"
        )

    def _related_lookup(
        self,
        value: fs.RelatedValueLookupSpec,
        context: _RenderContext,
    ) -> str:
        return self._exact_lookup(
            value.table,
            value.bindings,
            value.value,
            context,
        )

    def _related_json_array_value(
        self,
        value: fs.RelatedCanonicalJsonArrayValueSpec,
        context: _RenderContext,
    ) -> str:
        alias = self._alias()
        where = self._where(alias, value.bindings, None, context)
        relation = f"{_quote(value.table)} AS {_quote(alias)}"
        ordinal = (
            "NULL"
            if value.ordinal is None
            else _predecessor_offset_sql(
                self._scalar(value.ordinal, context),
                value.ordinal_offset,
            )
        )
        projected = self._function(
            fs.RendererSemanticOperation.RELATED_CANONICAL_JSON_ARRAY_VALUE,
            (
                f"{_quote(alias)}.{_quote(value.column)}",
                _text_literal("$"),
                _text_literal(value.element_type.value),
                _text_literal(value.projection.value),
                ordinal,
            ),
        )
        return (
            f"(CASE WHEN (SELECT COUNT(*) FROM {relation} WHERE {where}) = 1 "
            f"THEN (SELECT {projected} FROM {relation} WHERE {where}) ELSE NULL END)"
        )

    def _related_json_array_length(
        self,
        value: fs.RelatedCanonicalJsonArrayLengthValueSpec,
        context: _RenderContext,
    ) -> str:
        alias = self._alias()
        where = self._where(alias, value.bindings, None, context)
        relation = f"{_quote(value.table)} AS {_quote(alias)}"
        length = self._function(
            fs.RendererSemanticOperation.RELATED_CANONICAL_JSON_ARRAY_LENGTH,
            (
                f"{_quote(alias)}.{_quote(value.column)}",
                _text_literal("$"),
                _text_literal(value.element_type.value),
            ),
        )
        return (
            f"(CASE WHEN (SELECT COUNT(*) FROM {relation} WHERE {where}) = 1 "
            f"THEN (SELECT {length} FROM {relation} WHERE {where}) ELSE NULL END)"
        )

    def _related_json_contains(
        self,
        value: fs.RelatedCanonicalJsonArrayContainsExpression,
        context: _RenderContext,
    ) -> str:
        alias = self._alias()
        where = self._where(alias, value.bindings, None, context)
        relation = f"{_quote(value.table)} AS {_quote(alias)}"
        contains = self._function(
            fs.RendererSemanticOperation.RELATED_CANONICAL_JSON_ARRAY_CONTAINS,
            (
                f"{_quote(alias)}.{_quote(value.column)}",
                _text_literal("$"),
                _text_literal(value.element_type.value),
                self._scalar(value.element, context),
            ),
        )
        result = (
            f"(CASE WHEN (SELECT COUNT(*) FROM {relation} WHERE {where}) = 1 "
            f"THEN (SELECT {contains} FROM {relation} WHERE {where}) ELSE NULL END)"
        )
        return f"(NOT {result})" if value.negated else result

    def _expected(self, value: object, context: _RenderContext) -> str:
        if type(value) is fs.RelatedValueLookupSpec:
            return self._related_lookup(value, context)
        return self._scalar(value, context)

    def _predicate(self, value: object, context: _RenderContext) -> str:
        node_type = type(value)
        if node_type.__name__ in self._grammar.unsupported_predicate_nodes:
            raise FamilySchemaRenderError(f"predicate node disabled by grammar: {node_type.__name__}")
        if node_type is fs.ComparisonExpression:
            return (
                "("
                + self._scalar(value.left, context)
                + " "
                + _COMPARISON_SQL[value.operator]
                + " "
                + self._scalar(value.right, context)
                + ")"
            )
        if node_type is fs.NullExpression:
            operator = "IS NULL" if value.operator is fs.NullOperator.IS_NULL else "IS NOT NULL"
            return f"({self._scalar(value.value, context)} {operator})"
        if node_type is fs.MembershipExpression:
            operator = "NOT IN" if value.negated else "IN"
            literals = ", ".join(_literal(item.value) for item in value.values)
            return f"({self._scalar(value.value, context)} {operator} ({literals}))"
        if node_type is fs.LogicalExpression:
            rendered = tuple(self._predicate(item, context) for item in value.operands)
            return self._and(rendered) if value.operator is fs.LogicalOperator.AND else self._or(rendered)
        if node_type is fs.NotExpression:
            return f"(NOT {self._predicate(value.operand, context)})"
        if node_type is fs.CanonicalJsonPayloadHashExpression:
            digest = self._function(
                fs.RendererSemanticOperation.CANONICAL_JSON_PAYLOAD_HASH,
                (
                    self._scalar(value.document, context),
                    _canonical_text_bytes_sql(canonical_json_bytes([list(path) for path in value.excluded_paths])),
                ),
            )
            expected = self._expected(value.expected_hash, context)
            return f"({digest} {_COMPARISON_SQL[value.operator]} {expected})"
        if node_type is fs.RelatedCanonicalJsonArrayContainsExpression:
            return self._related_json_contains(value, context)
        if node_type is fs.RelationExistsExpression:
            return self._relation_exists(value, context)
        if node_type is fs.RelationAggregateExpression:
            return self._relation_aggregate(value, context)
        if node_type is fs.RelationJoinAggregateExpression:
            return self._relation_join_aggregate(value, context)
        if node_type is fs.RelationArgExtremumExpression:
            return self._relation_arg_extremum(value, context)
        if node_type is fs.RelationLatestPerDomainPolicyExpression:
            return self._relation_latest_policy(value, context)
        if node_type is fs.PrefixStreamExtensionExpression:
            return self._prefix_stream(value, context)
        if node_type is fs.ChainPredecessorExpression:
            return self._chain_predecessor(value, context)
        if node_type is fs.RelationChainPrefixExpression:
            return self._chain_prefix(value, context)
        if node_type is fs.RelationSetEqualityExpression:
            return self._relation_set_equality(value, context)
        raise FamilySchemaRenderError(f"unknown predicate node: {node_type.__name__}")

    def _relation_exists(
        self,
        value: fs.RelationExistsExpression,
        context: _RenderContext,
    ) -> str:
        alias = self._alias()
        where = self._where(alias, value.bindings, value.predicate, context)
        expression = f"EXISTS (SELECT 1 FROM {_quote(value.table)} AS {_quote(alias)} WHERE {where})"
        return f"(NOT {expression})" if value.negated else f"({expression})"

    def _aggregate_sql(
        self,
        *,
        aggregate: fs.RelationAggregateKind,
        value_sql: str | None,
        from_sql: str,
        where_sql: str,
        order_sql: str,
    ) -> str:
        if aggregate is fs.RelationAggregateKind.COUNT_ROWS:
            return f"(SELECT COUNT(*) FROM {from_sql} WHERE {where_sql})"
        if aggregate is fs.RelationAggregateKind.COUNT_DISTINCT:
            if value_sql is None:
                raise FamilySchemaRenderError("COUNT_DISTINCT has no value")
            return f"(SELECT COUNT(DISTINCT {value_sql}) FROM {from_sql} WHERE {where_sql})"
        if aggregate is not fs.RelationAggregateKind.CANONICAL_ORDERED_VALUE_HASH:
            raise FamilySchemaRenderError("unknown relation aggregate")
        if value_sql is None or not order_sql:
            raise FamilySchemaRenderError("ordered hash lowering is incomplete")
        ordered_alias = self._alias()
        function = self._function(
            fs.RendererSemanticOperation.CANONICAL_ORDERED_VALUE_HASH,
            (f"{_quote(ordered_alias)}.{_quote('value')}",),
        )
        return (
            f"(SELECT {function} FROM (SELECT {value_sql} AS {_quote('value')} "
            f"FROM {from_sql} WHERE {where_sql} ORDER BY {order_sql}) "
            f"AS {_quote(ordered_alias)})"
        )

    def _relation_aggregate(
        self,
        value: fs.RelationAggregateExpression,
        context: _RenderContext,
    ) -> str:
        alias = self._alias()
        relation_context = _RenderContext(
            current_alias=context.current_alias,
            related_alias=alias,
        )
        where = self._where(alias, value.bindings, value.predicate, context)
        value_sql = None if value.value is None else f"{_quote(alias)}.{_quote(value.value)}"
        order = ", ".join(
            f"{_quote(alias)}.{_quote(item.column)} COLLATE {_quote(item.collation.value)} {item.direction.value}"
            for item in value.order
        )
        aggregate = self._aggregate_sql(
            aggregate=value.aggregate,
            value_sql=value_sql,
            from_sql=f"{_quote(value.table)} AS {_quote(alias)}",
            where_sql=where,
            order_sql=order,
        )
        del relation_context
        return f"({aggregate} {_COMPARISON_SQL[value.operator]} {self._expected(value.expected, context)})"

    def _join_from(
        self,
        anchor_table: str,
        anchor_alias: str,
        joins: tuple[fs.RelationJoinSpec, ...],
        context: _RenderContext,
    ) -> str:
        result = f"{_quote(anchor_table)} AS {_quote(anchor_alias)}"
        for join in joins:
            on_parts = [
                f"{_quote(item.left_alias)}.{_quote(item.left_column)} = "
                f"{_quote(item.right_alias)}.{_quote(item.right_column)}"
                for item in join.on
            ]
            if join.predicate is not None:
                on_parts.append(self._predicate(join.predicate, context))
            result += f" INNER JOIN {_quote(join.table)} AS {_quote(join.alias)} ON " + self._and(tuple(on_parts))
        return result

    def _join_completeness(
        self,
        anchor_table: str,
        anchor_alias: str,
        bindings: tuple[fs.LookupBinding, ...],
        joins: tuple[fs.RelationJoinSpec, ...],
        context: _RenderContext,
        joined_from: str,
        joined_where: str,
    ) -> str:
        anchor_where = self._where(anchor_alias, bindings, None, context)
        anchor_count = f"(SELECT COUNT(*) FROM {_quote(anchor_table)} AS {_quote(anchor_alias)} WHERE {anchor_where})"
        joined_count = f"(SELECT COUNT(*) FROM {joined_from} WHERE {joined_where})"
        return f"({anchor_count} = {joined_count})"

    def _relation_join_aggregate(
        self,
        value: fs.RelationJoinAggregateExpression,
        context: _RenderContext,
    ) -> str:
        joined_from = self._join_from(
            value.anchor_table,
            value.anchor_alias,
            value.joins,
            context,
        )
        where = self._where(value.anchor_alias, value.bindings, None, context)
        completeness = self._join_completeness(
            value.anchor_table,
            value.anchor_alias,
            value.bindings,
            value.joins,
            context,
            joined_from,
            where,
        )
        value_sql = None if value.value is None else self._scalar(value.value, context)
        order = ", ".join(
            f"{_quote(item.relation_alias)}.{_quote(item.column)} "
            f"COLLATE {_quote(item.collation.value)} {item.direction.value}"
            for item in value.order
        )
        aggregate = self._aggregate_sql(
            aggregate=value.aggregate,
            value_sql=value_sql,
            from_sql=joined_from,
            where_sql=where,
            order_sql=order,
        )
        comparison = f"({aggregate} {_COMPARISON_SQL[value.operator]} {self._expected(value.expected, context)})"
        return f"(CASE WHEN {completeness} THEN {comparison} ELSE NULL END)"

    def _relation_arg_extremum(
        self,
        value: fs.RelationArgExtremumExpression,
        context: _RenderContext,
    ) -> str:
        joined_from = self._join_from(
            value.anchor_table,
            value.anchor_alias,
            value.joins,
            context,
        )
        where = self._where(value.anchor_alias, value.bindings, None, context)
        completeness = self._join_completeness(
            value.anchor_table,
            value.anchor_alias,
            value.bindings,
            value.joins,
            context,
            joined_from,
            where,
        )
        metric = self._function(
            fs.RendererSemanticOperation.MEAN_NET_RETURN_DIFFERENTIAL,
            tuple(self._scalar(item, context) for item in value.metric.inputs),
        )
        order = [f"{metric} DESC"]
        order.extend(
            f"{_quote(item.relation_alias)}.{_quote(item.column)} "
            f"COLLATE {_quote(item.collation.value)} {item.direction.value}"
            for item in value.tie_break
        )
        selected = tuple(
            f"(SELECT {self._scalar(result, context)} FROM {joined_from} "
            f"WHERE {where} ORDER BY {', '.join(order)} LIMIT 1)"
            for result in value.result
        )
        comparisons = tuple(
            f"({actual} {_COMPARISON_SQL[value.operator]} {self._scalar(expected, context)})"
            for actual, expected in zip(selected, value.expected, strict=True)
        )
        combined = self._or(comparisons) if value.operator is fs.ComparisonOperator.NE else self._and(comparisons)
        return f"(CASE WHEN {completeness} THEN {combined} ELSE NULL END)"

    def _latest_selected_sql(
        self,
        value: fs.RelationLatestPerDomainPolicyExpression,
        context: _RenderContext,
    ) -> tuple[str, str]:
        row_alias = self._alias()
        max_alias = self._alias()
        base = list(self._binding_sql(row_alias, value.bindings, context))
        max_bindings = tuple(
            f"{_quote(max_alias)}.{_quote(binding.related_column)} = {self._scalar(binding.value, context)}"
            for binding in value.bindings
        )
        max_bindings = (
            *max_bindings,
            f"{_quote(max_alias)}.{_quote(value.domain_column)} = {_quote(row_alias)}.{_quote(value.domain_column)}",
        )
        base.append(
            f"{_quote(row_alias)}.{_quote(value.latest_ordinal_column)} = "
            f"(SELECT MAX({_quote(max_alias)}.{_quote(value.latest_ordinal_column)}) "
            f"FROM {_quote(value.table)} AS {_quote(max_alias)} "
            f"WHERE {self._and(max_bindings)})"
        )
        columns = ", ".join(f"{_quote(row_alias)}.{_quote(column)} AS {_quote(column)}" for column in value.inputs)
        return (
            f"SELECT {columns} FROM {_quote(value.table)} AS {_quote(row_alias)} WHERE {self._and(tuple(base))}",
            row_alias,
        )

    def _relation_latest_policy(
        self,
        value: fs.RelationLatestPerDomainPolicyExpression,
        context: _RenderContext,
    ) -> str:
        selected_sql, _ = self._latest_selected_sql(value, context)
        selected_alias = self._alias()
        declared = self._scalar(value.declared_domain, context)
        domain_length = self._function(
            fs.RendererSemanticOperation.RELATED_CANONICAL_JSON_ARRAY_LENGTH,
            (
                declared,
                _text_literal("$"),
                _text_literal(value.declared_domain.element_type.value),
            ),
        )
        selected_relation = f"({selected_sql}) AS {_quote(selected_alias)}"
        selected_count = f"(SELECT COUNT(*) FROM {selected_relation})"
        distinct_count = (
            f"(SELECT COUNT(DISTINCT {_quote(selected_alias)}.{_quote(value.domain_column)}) FROM {selected_relation})"
        )
        contains = self._function(
            fs.RendererSemanticOperation.RELATED_CANONICAL_JSON_ARRAY_CONTAINS,
            (
                declared,
                _text_literal("$"),
                _text_literal(value.declared_domain.element_type.value),
                f"{_quote(selected_alias)}.{_quote(value.domain_column)}",
            ),
        )
        all_declared = f"NOT EXISTS (SELECT 1 FROM {selected_relation} WHERE COALESCE({contains}, 0) <> 1)"
        cardinality = self._and(
            (
                f"{selected_count} = {domain_length}",
                f"{distinct_count} = {selected_count}",
                all_declared,
            )
        )
        ordered_alias = self._alias()
        ordered_relation = (
            f"(SELECT * FROM ({selected_sql}) AS {_quote(selected_alias)} "
            f"ORDER BY {_quote(selected_alias)}.{_quote(value.order[0].column)} "
            f"COLLATE {_quote(value.order[0].collation.value)} "
            f"{value.order[0].direction.value}) AS {_quote(ordered_alias)}"
        )
        policy = self._function(
            fs.RendererSemanticOperation.LATEST_PER_DOMAIN_POLICY,
            tuple(f"{_quote(ordered_alias)}.{_quote(column)}" for column in value.inputs),
        )
        policy_result = f"(SELECT {policy} FROM {ordered_relation})"
        verdict = self._function(
            fs.RendererSemanticOperation.CANONICAL_JSON_FIELD_VALUE,
            (
                policy_result,
                _text_literal("$.diagnostic_verdict"),
                _text_literal(fs.CanonicalJsonScalarType.TEXT.value),
            ),
        )
        ordered_hash = self._function(
            fs.RendererSemanticOperation.CANONICAL_JSON_FIELD_VALUE,
            (
                policy_result,
                _text_literal("$.ordered_latest_grid_assessment_hashes_hash"),
                _text_literal(fs.CanonicalJsonScalarType.TEXT.value),
            ),
        )
        matches = self._and(
            (
                f"({verdict} = {self._scalar(value.expected_verdict, context)})",
                f"({ordered_hash} = {self._scalar(value.expected_ordered_record_hashes_hash, context)})",
            )
        )
        result = f"(NOT {matches})" if value.negated else matches
        return f"(CASE WHEN {cardinality} THEN {result} ELSE NULL END)"

    def _prefix_stream(
        self,
        value: fs.PrefixStreamExtensionExpression,
        context: _RenderContext,
    ) -> str:
        previous_alias = self._alias()
        previous_where = self._where(
            previous_alias,
            value.previous_bindings,
            None,
            context,
        )
        previous_relation = f"{_quote(value.previous_table)} AS {_quote(previous_alias)}"
        previous_count = f"(SELECT COUNT(*) FROM {previous_relation} WHERE {previous_where})"
        ordinal = self._scalar(value.current_ordinal, context)
        is_first = f"({ordinal} = {value.first_ordinal})"
        predecessor_cardinality = f"(CASE WHEN {is_first} THEN ({previous_count} = 0) ELSE ({previous_count} = 1) END)"
        genesis = self._scalar(value.genesis_hash, context)

        def previous_or_genesis(column: str, genesis_value: str) -> str:
            return (
                f"(CASE WHEN {is_first} THEN {genesis_value} ELSE "
                f"(SELECT {_quote(previous_alias)}.{_quote(column)} "
                f"FROM {previous_relation} WHERE {previous_where}) END)"
            )

        declaration_alias = self._alias()
        declaration_where = self._where(
            declaration_alias,
            value.declaration_bindings,
            None,
            context,
        )
        declaration_relation = f"{_quote(value.declaration_table)} AS {_quote(declaration_alias)}"
        declaration_count = f"(SELECT COUNT(*) FROM {declaration_relation} WHERE {declaration_where})"

        def declaration_column(column: str) -> str:
            return (
                f"(SELECT {_quote(declaration_alias)}.{_quote(column)} "
                f"FROM {declaration_relation} WHERE {declaration_where})"
            )

        arguments = (
            self._scalar(value.current_prefix_hash, context),
            self._scalar(value.current_replication_count, context),
            self._scalar(value.current_period_count, context),
            previous_or_genesis(value.previous_prefix_hash_column, genesis),
            previous_or_genesis(value.previous_replication_count_column, "0"),
            previous_or_genesis(value.previous_period_count_column, "0"),
            self._scalar(value.block_length, context),
            ordinal,
            genesis,
            *(declaration_column(column) for column in value.algorithm_columns),
        )
        valid = self._and(
            (
                predecessor_cardinality,
                f"({declaration_count} = 1)",
                f"({self._function(fs.RendererSemanticOperation.PREFIX_STREAM_EXTENSION, arguments)} = 1)",
            )
        )
        return f"(NOT {valid})" if value.negated else valid

    def _chain_predecessor(
        self,
        value: fs.ChainPredecessorExpression,
        context: _RenderContext,
    ) -> str:
        alias = self._alias()
        partition = self._binding_sql(alias, value.partition_bindings, context)
        ordinal = self._scalar(value.current_ordinal, context)
        previous_hash = self._scalar(value.current_previous_hash, context)
        genesis = self._scalar(value.genesis_hash, context)
        relation = f"{_quote(value.table)} AS {_quote(alias)}"
        partition_count = f"(SELECT COUNT(*) FROM {relation} WHERE {self._and(partition)})"
        predecessor_where = self._and(
            (
                *partition,
                f"{_quote(alias)}.{_quote(value.ordinal_column)} = ({ordinal} - 1)",
                f"{_quote(alias)}.{_quote(value.record_hash_column)} = {previous_hash}",
            )
        )
        predecessor_count = f"(SELECT COUNT(*) FROM {relation} WHERE {predecessor_where})"
        valid = self._or(
            (
                self._and(
                    (
                        f"({ordinal} = 1)",
                        f"({previous_hash} = {genesis})",
                        f"({partition_count} = 0)",
                    )
                ),
                self._and(
                    (
                        f"({ordinal} > 1)",
                        f"({predecessor_count} = 1)",
                    )
                ),
            )
        )
        return f"(NOT {valid})" if value.negated else valid

    def _chain_prefix(
        self,
        value: fs.RelationChainPrefixExpression,
        context: _RenderContext,
    ) -> str:
        alias = self._alias()
        partition = self._binding_sql(alias, value.partition_bindings, context)
        relation = f"{_quote(value.table)} AS {_quote(alias)}"
        ancestor = self._related_lookup(value.ancestor_hash, context)
        head = self._scalar(value.head_hash, context)
        ancestor_ordinal = (
            f"(SELECT {_quote(alias)}.{_quote(value.ordinal_column)} FROM {relation} "
            f"WHERE {self._and((*partition, f'{_quote(alias)}.{_quote(value.record_hash_column)} = {ancestor}'))})"
        )
        head_ordinal = (
            f"(SELECT {_quote(alias)}.{_quote(value.ordinal_column)} FROM {relation} "
            f"WHERE {self._and((*partition, f'{_quote(alias)}.{_quote(value.record_hash_column)} = {head}'))})"
        )
        max_ordinal = (
            f"(SELECT MAX({_quote(alias)}.{_quote(value.ordinal_column)}) FROM {relation} WHERE {self._and(partition)})"
        )
        segment_count = (
            f"(SELECT COUNT(*) FROM {relation} WHERE "
            f"{self._and((*partition, f'{_quote(alias)}.{_quote(value.ordinal_column)} BETWEEN {ancestor_ordinal} AND {head_ordinal}'))})"
        )
        row_alias = self._alias()
        pred_alias = self._alias()
        broken = (
            f"EXISTS (SELECT 1 FROM {_quote(value.table)} AS {_quote(row_alias)} "
            f"WHERE {self._and(tuple(self._binding_sql(row_alias, value.partition_bindings, context)) + (f'{_quote(row_alias)}.{_quote(value.ordinal_column)} > {ancestor_ordinal}', f'{_quote(row_alias)}.{_quote(value.ordinal_column)} <= {head_ordinal}'))} "
            f"AND NOT EXISTS (SELECT 1 FROM {_quote(value.table)} AS {_quote(pred_alias)} "
            f"WHERE {self._and(tuple(self._binding_sql(pred_alias, value.partition_bindings, context)) + (f'{_quote(pred_alias)}.{_quote(value.ordinal_column)} = {_quote(row_alias)}.{_quote(value.ordinal_column)} - 1', f'{_quote(pred_alias)}.{_quote(value.record_hash_column)} = {_quote(row_alias)}.{_quote(value.previous_hash_column)}'))}))"
        )
        ancestor_count = (
            f"(SELECT COUNT(*) FROM {relation} WHERE "
            f"{self._and((*partition, f'{_quote(alias)}.{_quote(value.record_hash_column)} = {ancestor}'))})"
        )
        head_count = (
            f"(SELECT COUNT(*) FROM {relation} WHERE "
            f"{self._and((*partition, f'{_quote(alias)}.{_quote(value.record_hash_column)} = {head}'))})"
        )
        valid = self._and(
            (
                f"({ancestor_count} = 1)",
                f"({head_count} = 1)",
                f"({head_ordinal} = {max_ordinal})",
                f"({segment_count} = ({head_ordinal} - {ancestor_ordinal} + 1))",
                f"(NOT {broken})",
            )
        )
        return f"(NOT {valid})" if value.negated else valid

    def _ordered_set_hash(
        self,
        select_sql: str,
        order: tuple[fs.RelationAggregateOrderSpec, ...],
    ) -> str:
        source_alias = self._alias()
        distinct_alias = self._alias()
        ordered = ", ".join(
            f"{_quote(source_alias)}.{_quote('value')} COLLATE {_quote(item.collation.value)} {item.direction.value}"
            for item in order
        )
        function = self._function(
            fs.RendererSemanticOperation.CANONICAL_ORDERED_VALUE_HASH,
            (f"{_quote(distinct_alias)}.{_quote('value')}",),
        )
        return (
            f"(SELECT {function} FROM (SELECT DISTINCT "
            f"{_quote(source_alias)}.{_quote('value')} AS {_quote('value')} "
            f"FROM ({select_sql}) AS {_quote(source_alias)} ORDER BY {ordered}) "
            f"AS {_quote(distinct_alias)})"
        )

    def _relation_set_equality(
        self,
        value: fs.RelationSetEqualityExpression,
        context: _RenderContext,
    ) -> str:
        left_alias = self._alias()
        left_where = self._where(
            left_alias,
            value.left_bindings,
            value.left_predicate,
            context,
        )
        left_select = (
            f"SELECT {_quote(left_alias)}.{_quote(value.left_value)} AS {_quote('value')} "
            f"FROM {_quote(value.left_table)} AS {_quote(left_alias)} "
            f"WHERE {left_where}"
        )
        for included in value.left_include_values:
            left_select += f" UNION ALL SELECT {self._scalar(included, context)} AS {_quote('value')}"
        right_alias = self._alias()
        right_where = self._where(
            right_alias,
            value.right_bindings,
            value.right_predicate,
            context,
        )
        right_select = (
            f"SELECT {_quote(right_alias)}.{_quote(value.right_value)} AS {_quote('value')} "
            f"FROM {_quote(value.right_table)} AS {_quote(right_alias)} "
            f"WHERE {right_where}"
        )
        comparison = (
            f"({self._ordered_set_hash(left_select, value.order)} = "
            f"{self._ordered_set_hash(right_select, value.order)})"
        )
        return f"(NOT {comparison})" if value.negated else comparison

    def _state_machine_violation(
        self,
        table_name: str,
        machine: fs.InsertStateMachineSpec,
    ) -> str:
        if machine.sequence_column is None:
            raise FamilySchemaRenderError("state-machine lowering requires a sequence column")
        alias = self._alias()
        identity = tuple(
            f"{_quote(alias)}.{_quote(column)} = NEW.{_quote(column)}" for column in machine.identity_columns
        )
        relation = f"{_quote(table_name)} AS {_quote(alias)}"
        existing_count = f"(SELECT COUNT(*) FROM {relation} WHERE {self._and(identity)})"
        initial = self._and(
            (
                f"(NEW.{_quote(machine.sequence_column)} = 0)",
                f"(NEW.{_quote(machine.state_column)} IN ("
                + ", ".join(_text_literal(item) for item in machine.initial_states)
                + "))",
                f"({existing_count} = 0)",
            )
        )
        transitions = self._or(
            tuple(
                self._and(
                    (
                        f"{_quote(alias)}.{_quote(machine.state_column)} = {_text_literal(item.previous_state)}",
                        f"NEW.{_quote(machine.state_column)} = {_text_literal(item.next_state)}",
                    )
                )
                for item in machine.transitions
            )
        )
        predecessor_where = self._and(
            (
                *identity,
                f"{_quote(alias)}.{_quote(machine.sequence_column)} = NEW.{_quote(machine.sequence_column)} - 1",
                transitions,
            )
        )
        transition = self._and(
            (
                f"(NEW.{_quote(machine.sequence_column)} > 0)",
                f"((SELECT COUNT(*) FROM {relation} WHERE {predecessor_where}) = 1)",
            )
        )
        return f"(NOT {self._or((initial, transition))})"

    def table(self, table: fs.AuthorityTableSpec) -> str:
        if type(table) is not fs.AuthorityTableSpec:
            raise FamilySchemaRenderError("unknown table AST node")
        parts: list[str] = []
        for column in table.columns:
            if type(column) is not fs.ColumnSpec:
                raise FamilySchemaRenderError("unknown column AST node")
            definition = f"{_quote(column.name)} {column.sql_type.value}"
            if not column.nullable:
                definition += " NOT NULL"
            if column.default is not None:
                if type(column.default) is not fs.ColumnDefault:
                    raise FamilySchemaRenderError("unknown default AST node")
                definition += " DEFAULT " + _literal(column.default.value)
            parts.append(definition)
        parts.append("PRIMARY KEY (" + ", ".join(_quote(column) for column in table.primary_key_columns) + ")")
        for foreign_key in table.foreign_keys:
            if type(foreign_key) is not fs.ForeignKeySpec:
                raise FamilySchemaRenderError("unknown foreign-key AST node")
            parts.append(
                "FOREIGN KEY ("
                + ", ".join(_quote(column) for column in foreign_key.columns)
                + f") REFERENCES {_quote(foreign_key.referenced_table)} ("
                + ", ".join(_quote(column) for column in foreign_key.referenced_columns)
                + ") ON UPDATE "
                + _FOREIGN_KEY_ACTION_SQL[foreign_key.on_update]
                + " ON DELETE "
                + _FOREIGN_KEY_ACTION_SQL[foreign_key.on_delete]
                + " "
                + _DEFERRABILITY_SQL[foreign_key.deferrability]
            )
        for check in table.checks:
            if type(check) is not fs.CheckConstraintSpec:
                raise FamilySchemaRenderError("unknown check AST node")
            predicate = self._predicate(check.expression, _RenderContext())
            parts.append(f"CONSTRAINT {_quote(check.name)} CHECK (COALESCE({predicate}, 0))")
        return f"CREATE TABLE {_quote(table.table_name)} (" + ", ".join(parts) + ") STRICT"

    def index(self, table_name: str, index: fs.IndexSpec) -> str:
        if type(index) is not fs.IndexSpec:
            raise FamilySchemaRenderError("unknown index AST node")
        columns = ", ".join(
            f"{_quote(item.column)} COLLATE {_quote(item.collation.value)} {item.direction.value}"
            for item in index.columns
        )
        sql = (
            "CREATE "
            + ("UNIQUE " if index.unique else "")
            + f"INDEX {_quote(index.name)} ON {_quote(table_name)} ({columns})"
        )
        if index.predicate is not None:
            sql += " WHERE " + self._predicate(index.predicate, _RenderContext())
        return sql

    def trigger(self, table_name: str, trigger: fs.TriggerPolicySpec) -> str:
        if type(trigger) is not fs.TriggerPolicySpec:
            raise FamilySchemaRenderError("unknown trigger AST node")
        sql = (
            f"CREATE TRIGGER {_quote(trigger.name)} {trigger.timing.value} "
            f"{trigger.event.value} ON {_quote(table_name)}"
        )
        if trigger.kind is fs.TriggerPolicyKind.INSERT_GUARD:
            if trigger.violation_when is None:
                raise FamilySchemaRenderError("insert guard has no predicate")
            violations = [f"COALESCE({self._predicate(trigger.violation_when, _RenderContext())}, 1)"]
            if trigger.state_machine is not None:
                violations.append(f"COALESCE({self._state_machine_violation(table_name, trigger.state_machine)}, 1)")
            sql += " WHEN " + self._or(tuple(violations))
        elif trigger.kind not in (
            fs.TriggerPolicyKind.NO_UPDATE,
            fs.TriggerPolicyKind.NO_DELETE,
        ):
            raise FamilySchemaRenderError("unknown trigger policy kind")
        sql += " BEGIN SELECT RAISE(ABORT, " + _text_literal(trigger.error_code) + "); END"
        return sql


# Load only after ``_SqlRenderer`` exists: structural replay reuses this exact
# closed renderer and therefore cannot participate in the module's earlier
# semantic/named-registry bootstrap without a circular import.
from . import family_schema_named_verifiers_c4_c5 as _named_verifiers_c4_c5  # noqa: E402
from . import family_schema_named_verifiers_c6_c9 as _named_verifiers_c6_c9  # noqa: E402
from . import family_schema_named_verifiers_c7 as _named_verifiers_c7  # noqa: E402
from . import family_schema_named_verifiers_c8 as _named_verifiers_c8  # noqa: E402
from . import family_schema_structural_verifiers as _structural_verifiers  # noqa: E402

_named_verifier_modules = (
    (
        _named_verifiers_c4_c5,
        "GENESIS_C4_C5_NAMED_VERIFIER_IMPLEMENTATIONS",
    ),
    (
        _named_verifiers_c7,
        "C7_NAMED_VERIFIER_IMPLEMENTATIONS",
    ),
    (
        _named_verifiers_c8,
        "C8_NAMED_VERIFIER_IMPLEMENTATIONS",
    ),
    (
        _named_verifiers_c6_c9,
        "C6_C9_NAMED_VERIFIER_IMPLEMENTATIONS",
    ),
)

_canonical_implementation_candidates = list(_CANONICAL_IMPLEMENTATION_REGISTRY)
for _named_module, _implementation_attribute in _named_verifier_modules:
    for _item in getattr(_named_module, _implementation_attribute):
        if not getattr(_item, "production_ready", True):
            continue
        _canonical_implementation_candidates.append(
            _ImplementationCallableBinding(
                target_kind=fs.ImplementationTargetKind.NAMED_VERIFIER,
                target_id=_item.implementation_requirement_id,
                implementation_id=_item.implementation_id,
                implementation=_item.implementation,
            )
        )
_canonical_implementation_candidates.extend(
    _ImplementationCallableBinding(
        target_kind=fs.ImplementationTargetKind.STRUCTURAL_HANDLER,
        target_id=f"STRUCTURAL_HANDLER::{item.operation.value}",
        implementation_id=item.implementation_id,
        implementation=item.implementation,
    )
    for item in (_structural_verifiers.FAMILY_SCHEMA_STRUCTURAL_VERIFIER_IMPLEMENTATIONS)
)

_canonical_implementation_by_key: dict[
    tuple[fs.ImplementationTargetKind, str, str],
    _ImplementationCallableBinding,
] = {}
for _binding in _canonical_implementation_candidates:
    _binding_key = (
        _binding.target_kind,
        _binding.target_id,
        _binding.implementation_id,
    )
    if _binding_key in _canonical_implementation_by_key:
        raise FamilySchemaRenderError("canonical production implementation registry contains duplicate bindings")
    _canonical_implementation_by_key[_binding_key] = _binding

_canonical_composition = compose_family_schema_v3()
_canonical_required_keys = tuple(
    (item.target_kind, item.target_id, item.implementation_id)
    for item in _canonical_composition.implementation_evidence_requirements.entries
    if item.evidence_policy is fs.ImplementationEvidencePolicy.REQUIRE_ACTUAL_SHA256
)
_unexpected_implementation_keys = set(_canonical_implementation_by_key).difference(_canonical_required_keys)
if _unexpected_implementation_keys:
    raise FamilySchemaRenderError("canonical production implementation registry contains unexpected bindings")
_CANONICAL_IMPLEMENTATION_REGISTRY = tuple(
    _canonical_implementation_by_key[key] for key in _canonical_required_keys if key in _canonical_implementation_by_key
)

del _binding
del _binding_key
del _canonical_composition
del _canonical_implementation_by_key
del _canonical_implementation_candidates
del _canonical_required_keys
del _implementation_attribute
del _item
del _named_module
del _named_verifier_modules
del _structural_verifiers
del _unexpected_implementation_keys


def _rendered_object(
    ordinal: int,
    object_type: str,
    object_name: str,
    table_name: str,
    sqlite_schema_sql: str,
) -> RenderedSchemaObject:
    if ";" in sqlite_schema_sql:
        # Trigger bodies contain one required statement terminator.
        if object_type != "trigger" or sqlite_schema_sql.count(";") != 1:
            raise FamilySchemaRenderError("unexpected embedded SQL terminator")
    if "IF NOT EXISTS" in sqlite_schema_sql:
        raise FamilySchemaRenderError("IF NOT EXISTS is forbidden")
    if "\r" in sqlite_schema_sql or "\n" in sqlite_schema_sql:
        raise FamilySchemaRenderError("canonical sqlite_schema SQL must be one line")
    if sqlite_schema_sql.startswith(("BEGIN ", "COMMIT", "ROLLBACK")):
        raise FamilySchemaRenderError("transaction-control SQL is forbidden")
    statement_sql = sqlite_schema_sql + ";\n"
    statement_bytes = statement_sql.encode("utf-8")
    return RenderedSchemaObject(
        ordinal=ordinal,
        object_type=object_type,
        object_name=object_name,
        table_name=table_name,
        sqlite_schema_sql=sqlite_schema_sql,
        statement_sql=statement_sql,
        statement_bytes=statement_bytes,
        statement_sha256=hashlib.sha256(statement_bytes).hexdigest(),
    )


def render_family_schema_v3(
    composition: FamilySchemaComposition | None = None,
) -> tuple[RenderedSchemaObject, ...]:
    canonical = compose_family_schema_v3()
    if composition is None:
        composition = canonical
    if type(composition) is not FamilySchemaComposition:
        raise TypeError("composition must be FamilySchemaComposition")
    if composition != canonical:
        raise FamilySchemaRenderError("composition is not the canonical exact-v3 value")
    if composition.renderer_grammar != fs.FAMILY_SCHEMA_RENDERER_GRAMMAR:
        raise FamilySchemaRenderError("composition renderer grammar is not canonical")

    rendered: list[RenderedSchemaObject] = []
    object_names: set[str] = set()
    for table in composition.tables:
        table_sql = _SqlRenderer(composition.renderer_grammar).table(table)
        rendered.append(
            _rendered_object(
                len(rendered) + 1,
                "table",
                table.table_name,
                table.table_name,
                table_sql,
            )
        )
        for index in table.indexes:
            index_sql = _SqlRenderer(composition.renderer_grammar).index(
                table.table_name,
                index,
            )
            rendered.append(
                _rendered_object(
                    len(rendered) + 1,
                    "index",
                    index.name,
                    table.table_name,
                    index_sql,
                )
            )
        for trigger in table.triggers:
            trigger_sql = _SqlRenderer(composition.renderer_grammar).trigger(
                table.table_name,
                trigger,
            )
            rendered.append(
                _rendered_object(
                    len(rendered) + 1,
                    "trigger",
                    trigger.name,
                    table.table_name,
                    trigger_sql,
                )
            )

    for item in rendered:
        if item.object_name in object_names:
            raise FamilySchemaRenderError(f"duplicate SQLite object name: {item.object_name}")
        object_names.add(item.object_name)
    return tuple(rendered)


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(_canonical_manifest_value(value))).hexdigest()


def _statement_catalog_payload(
    rendered: tuple[RenderedSchemaObject, ...],
) -> list[dict[str, object]]:
    return [
        {
            "ordinal": item.ordinal,
            "object_type": item.object_type,
            "object_name": item.object_name,
            "table_name": item.table_name,
            "sqlite_schema_sql": item.sqlite_schema_sql,
            "statement_utf8_hex": item.statement_bytes.hex(),
            "statement_sha256": item.statement_sha256,
        }
        for item in rendered
    ]


def _implementation_evidence_payload(
    evidence: fs.ImplementationEvidenceCatalog,
) -> dict[str, object]:
    return {
        "catalog_version": evidence.catalog_version,
        "requirements_catalog_version": evidence.requirements.catalog_version,
        "requirements": [_canonical_manifest_value(item) for item in evidence.requirements.entries],
        "bindings": [
            {
                "target_kind": item.target_kind.value,
                "target_id": item.target_id,
                "implementation_id": item.implementation_id,
                "implementation_sha256": item.implementation_sha256,
                "hash_method": item.hash_method.value,
                "hash_method_version": item.hash_method_version,
            }
            for item in evidence.bindings
        ],
    }


def _read_verifier_mapping_payload() -> dict[str, object]:
    try:
        from .family_schema_verifier_registry import (
            FAMILY_SCHEMA_STRUCTURAL_VERIFICATION_PLANS,
            FAMILY_SCHEMA_VERIFIER_BINDINGS,
            FAMILY_SCHEMA_VERIFIER_REGISTRY,
        )
    except Exception as exc:
        raise FamilySchemaUnsealedError("UNSEALED: canonical verifier mapping registry is unavailable") from exc
    return {
        "structural_plans": [_canonical_manifest_value(item) for item in FAMILY_SCHEMA_STRUCTURAL_VERIFICATION_PLANS],
        "named_bindings": [_canonical_manifest_value(item) for item in FAMILY_SCHEMA_VERIFIER_BINDINGS],
        "registry": [_canonical_manifest_value(item) for item in FAMILY_SCHEMA_VERIFIER_REGISTRY],
    }


def _build_verifier_mapping_reader() -> tuple[
    bytes,
    str,
    Callable[[], dict[str, object]],
]:
    """Capture the reviewed mapping in immutable closure-owned canonical bytes."""

    read_mapping_payload = _read_verifier_mapping_payload
    reviewed_bytes = canonical_json_bytes(read_mapping_payload())
    reviewed_sha256 = hashlib.sha256(reviewed_bytes).hexdigest()

    def verifier_mapping_payload() -> dict[str, object]:
        current = read_mapping_payload()
        if canonical_json_bytes(current) != reviewed_bytes:
            raise FamilySchemaUnsealedError("UNSEALED: canonical verifier mapping registry was mutated")
        return current

    return reviewed_bytes, reviewed_sha256, verifier_mapping_payload


(
    _CANONICAL_VERIFIER_MAPPING_BYTES,
    _CANONICAL_VERIFIER_MAPPING_SHA256,
    _verifier_mapping_payload,
) = _build_verifier_mapping_reader()
del _build_verifier_mapping_reader


def _family_schema_manifest_body(
    composition: FamilySchemaComposition,
    rendered: tuple[RenderedSchemaObject, ...],
    evidence: fs.ImplementationEvidenceCatalog,
) -> dict[str, object]:
    composition_payload = _canonical_manifest_value(composition)
    statements = _statement_catalog_payload(rendered)
    evidence_payload = _implementation_evidence_payload(evidence)
    verifier_mappings = _verifier_mapping_payload()
    traceability = {
        "resolved_contract_surfaces": [
            _canonical_manifest_value(item) for item in composition.resolved_contract_surfaces
        ],
        "coverage": [_canonical_manifest_value(item) for item in composition.coverage],
        "contract_derivations": [_canonical_manifest_value(item) for item in composition.contract_derivations],
        "artifact_graph_derivations": [
            _canonical_manifest_value(item) for item in composition.artifact_graph_derivations
        ],
    }
    renderer = {
        "version": composition.renderer_grammar.renderer_version,
        "grammar": _canonical_manifest_value(composition.renderer_grammar),
        "sqlite_runtime": _canonical_manifest_value(composition.renderer_grammar.sqlite_runtime),
        "semantic_function_abis": [
            _canonical_manifest_value(item) for item in composition.renderer_grammar.semantic_function_abis
        ],
        "null_lowering": _canonical_manifest_value(composition.renderer_grammar.null_lowering),
        "related_lookup_lowering": _canonical_manifest_value(composition.renderer_grammar.related_lookup_lowering),
        "state_machine_lowering": _canonical_manifest_value(composition.renderer_grammar.state_machine_lowering),
    }
    payload: dict[str, object] = {
        "manifest_version": "QUANTOS_FAMILY_SCHEMA_MANIFEST_V3",
        "schema_version": composition.schema_version,
        "source_schema_version": composition.source_schema_version,
        "component_order": list(composition.component_order),
        "composition": composition_payload,
        "renderer": renderer,
        "statement_catalog": statements,
        "implementation_evidence": evidence_payload,
        "external_provider_trust_roots": [
            _canonical_manifest_value(item)
            for item in composition.implementation_evidence_requirements.entries
            if item.evidence_policy is fs.ImplementationEvidencePolicy.EXCLUDED_DEPLOYMENT_TRUST_ROOT
        ],
        "external_authority_claim_profiles": [
            _canonical_manifest_value(item) for item in composition.external_authority_claim_profiles
        ],
        "auxiliary_semantic_recipes": [
            _canonical_manifest_value(item) for item in composition.auxiliary_semantic_recipes
        ],
        "traceability": traceability,
        "verifier_mappings": verifier_mappings,
    }
    payload.update(
        {
            "composition_sha256": _canonical_sha256(composition_payload),
            "statement_catalog_sha256": _canonical_sha256(statements),
            "implementation_evidence_sha256": _canonical_sha256(evidence_payload),
            "verifier_mapping_sha256": _canonical_sha256(verifier_mappings),
        }
    )
    return payload


def _validate_sealed_manifest_integrity(manifest: FamilySchemaManifest) -> None:
    try:
        manifest_bytes = manifest.manifest_bytes
        if type(manifest_bytes) is not bytes:
            raise ValueError("manifest bytes must be exact bytes")
        payload = load_canonical_json_bytes(manifest_bytes)
        if type(payload) is not dict or canonical_json_bytes(payload) != manifest_bytes:
            raise ValueError("manifest bytes are not exact canonical JSON")
        if (
            payload.get("state") != "SEALED"
            or payload.get("manifest_version") != "QUANTOS_FAMILY_SCHEMA_MANIFEST_V3"
            or payload.get("schema_version") != manifest.schema_version
            or payload.get("source_schema_version") != manifest.source_schema_version
            or payload.get("component_order") != list(manifest.component_order)
        ):
            raise ValueError("manifest identity fields do not match")
        renderer = payload.get("renderer")
        if type(renderer) is not dict or renderer.get("version") != (manifest.renderer_version):
            raise ValueError("manifest renderer identity does not match")
        if hashlib.sha256(manifest_bytes).hexdigest() != manifest.manifest_sha256:
            raise ValueError("manifest byte hash does not match")
        if payload.get("statement_catalog") != _statement_catalog_payload(manifest.rendered_objects):
            raise ValueError("manifest statement catalog does not match")
        if payload.get("implementation_evidence") != (
            _implementation_evidence_payload(manifest.implementation_evidence)
        ):
            raise ValueError("manifest implementation evidence does not match")
        for payload_key, digest_key, field_value in (
            ("composition", "composition_sha256", manifest.composition_sha256),
            (
                "statement_catalog",
                "statement_catalog_sha256",
                manifest.statement_catalog_sha256,
            ),
            (
                "implementation_evidence",
                "implementation_evidence_sha256",
                manifest.implementation_evidence_sha256,
            ),
            (
                "verifier_mappings",
                "verifier_mapping_sha256",
                manifest.verifier_mapping_sha256,
            ),
        ):
            if payload.get(digest_key) != field_value or _canonical_sha256(payload[payload_key]) != field_value:
                raise ValueError(f"manifest {payload_key} hash does not match")
    except FamilySchemaUnsealedError:
        raise
    except Exception as exc:
        raise FamilySchemaUnsealedError("UNSEALED: manifest integrity verification failed") from exc


def _build_manifest_authority() -> tuple[
    property,
    Callable[[], FamilySchemaManifest],
]:
    """Create one closure-owned token and the sole zero-argument seal path."""

    seal_token = object()
    reviewed_registry = _CANONICAL_IMPLEMENTATION_REGISTRY
    reviewed_verifier_mapping_bytes = _CANONICAL_VERIFIER_MAPPING_BYTES
    verifier_mapping_payload = _verifier_mapping_payload
    integrity_validator = _validate_sealed_manifest_integrity

    def state(manifest: FamilySchemaManifest) -> str:
        if getattr(manifest, "_seal_token", None) is not seal_token:
            raise FamilySchemaUnsealedError("UNSEALED: manifest was not produced by the canonical seal path")
        integrity_validator(manifest)
        return "SEALED"

    def seal_family_schema_v3() -> FamilySchemaManifest:
        """Seal exact v3, or fail ``UNSEALED`` until all implementations exist."""

        reviewed_live_mappings = verifier_mapping_payload()
        composition = compose_family_schema_v3()
        evidence = _bind_canonical_production_implementation_registry(
            reviewed_registry,
            composition,
        )
        rendered = render_family_schema_v3(composition)
        body = _family_schema_manifest_body(composition, rendered, evidence)
        if (
            canonical_json_bytes(body["verifier_mappings"]) != reviewed_verifier_mapping_bytes
            or canonical_json_bytes(reviewed_live_mappings) != reviewed_verifier_mapping_bytes
        ):
            raise FamilySchemaUnsealedError("UNSEALED: verifier mapping differs from the reviewed snapshot")
        payload = {"state": "SEALED", **body}
        manifest_bytes = canonical_json_bytes(payload)
        if load_canonical_json_bytes(manifest_bytes) != payload:
            raise FamilySchemaUnsealedError("UNSEALED: canonical manifest bytes do not round-trip exactly")

        manifest = object.__new__(FamilySchemaManifest)
        values: tuple[tuple[str, object], ...] = (
            ("schema_version", composition.schema_version),
            ("source_schema_version", composition.source_schema_version),
            ("renderer_version", composition.renderer_grammar.renderer_version),
            ("component_order", composition.component_order),
            ("rendered_objects", rendered),
            ("implementation_evidence", evidence),
            ("composition_sha256", payload["composition_sha256"]),
            ("statement_catalog_sha256", payload["statement_catalog_sha256"]),
            (
                "implementation_evidence_sha256",
                payload["implementation_evidence_sha256"],
            ),
            ("verifier_mapping_sha256", payload["verifier_mapping_sha256"]),
            ("manifest_bytes", manifest_bytes),
            ("manifest_sha256", hashlib.sha256(manifest_bytes).hexdigest()),
            ("_seal_token", seal_token),
        )
        for name, value in values:
            object.__setattr__(manifest, name, value)
        integrity_validator(manifest)
        return manifest

    return property(state), seal_family_schema_v3


FamilySchemaManifest.state, seal_family_schema_v3 = _build_manifest_authority()
del _build_manifest_authority


def _render_predicate_for_test(value: object) -> str:
    """Narrow test seam proving unknown closed-AST dispatch fails."""

    return _SqlRenderer(fs.FAMILY_SCHEMA_RENDERER_GRAMMAR)._predicate(
        value,
        _RenderContext(),
    )


__all__ = [
    "FamilySchemaManifest",
    "FamilySchemaRenderError",
    "FamilySchemaUnsealedError",
    "RenderedSchemaObject",
    "SUPPORTED_PREDICATE_NODE_TYPES",
    "SUPPORTED_RELATED_NODE_TYPES",
    "SUPPORTED_SCALAR_NODE_TYPES",
    "SUPPORTED_SCHEMA_NODE_TYPES",
    "UNSUPPORTED_PREDICATE_NODE_TYPES",
    "render_family_schema_v3",
    "seal_family_schema_v3",
]
