"""Fresh-connection, lease-bound SQLite runtime for family authority writes.

The public entry point accepts a filesystem path, never a caller-owned
connection.  It implements the exact trust transition frozen by
``FAMILY_SCHEMA_SQLITE_RUNTIME_PROFILE`` and exposes only an append operation
through an opaque, revocable session.  The SQLite authorizer remains the
load-bearing second boundary if trusted callback code obtains the connection
through instrumentation or otherwise attempts to escape that API.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import TypeAlias

from . import family_schema_spec as fs
from .family_schema_semantic_functions import (
    FAMILY_SCHEMA_SQLITE_SEMANTIC_IMPLEMENTATIONS,
)
from .family_schema_v3_verifier import (
    FamilyAuthorityExternalProviders,
    FamilyAuthorityVerificationError,
    FamilyAuthorityVerificationEvidence,
    _canonical_composition,
    _register_semantic_functions,
    _reject_preexisting_quantos_functions,
    _verify_pretrust_family_authority,
    _verify_registered_family_authority,
    _verify_registered_semantic_functions,
)


class FamilyAuthorityRuntimeError(RuntimeError):
    """The controlled exact-v3 write runtime failed closed."""


@dataclass(frozen=True, slots=True)
class FamilyAuthorityRuntimeEvidence:
    """Connection-free evidence for one committed controlled transaction."""

    authority_instance_id: str
    genesis_record_hash: str
    manifest_sha256: str
    transition_steps: tuple[fs.SqliteTrustTransitionStep, ...]
    appended_row_counts: tuple[tuple[str, int], ...]
    pre_verification: FamilyAuthorityVerificationEvidence
    post_verification: FamilyAuthorityVerificationEvidence
    committed: bool

    def __post_init__(self) -> None:
        fs.validate_authority_instance_id(self.authority_instance_id)
        if self.transition_steps != tuple(fs.SqliteTrustTransitionStep):
            raise ValueError("runtime evidence requires the exact trust transition")
        if (
            type(self.appended_row_counts) is not tuple
            or not self.appended_row_counts
            or any(
                type(item) is not tuple
                or len(item) != 2
                or type(item[0]) is not str
                or not item[0]
                or type(item[1]) is not int
                or item[1] <= 0
                for item in self.appended_row_counts
            )
            or len({item[0] for item in self.appended_row_counts}) != len(self.appended_row_counts)
        ):
            raise ValueError("runtime evidence appended-row counts are invalid")
        if type(self.pre_verification) is not FamilyAuthorityVerificationEvidence:
            raise TypeError("runtime evidence requires exact pre-verification evidence")
        if type(self.post_verification) is not FamilyAuthorityVerificationEvidence:
            raise TypeError("runtime evidence requires exact post-verification evidence")
        if self.pre_verification.authority_instance_id != self.authority_instance_id:
            raise ValueError("runtime pre-verification identity does not match")
        if self.post_verification.authority_instance_id != self.authority_instance_id:
            raise ValueError("runtime post-verification identity does not match")
        if self.genesis_record_hash != self.pre_verification.genesis_record_hash or (
            self.genesis_record_hash != self.post_verification.genesis_record_hash
        ):
            raise ValueError("runtime evidence genesis hashes do not match")
        if self.manifest_sha256 != self.pre_verification.manifest_sha256 or (
            self.manifest_sha256 != self.post_verification.manifest_sha256
        ):
            raise ValueError("runtime evidence manifest hashes do not match")
        if self.committed is not True:
            raise ValueError("runtime evidence is returned only for a commit")


class _AuthorizerMode(StrEnum):
    RUNTIME = "RUNTIME"
    CALLBACK = "CALLBACK"
    TERMINAL = "TERMINAL"


_FROZEN_SAFE_BUILTIN_FUNCTIONS = frozenset({"coalesce", "count"})
_READ_ONLY_PRAGMAS = frozenset(
    {
        "data_version",
        "database_list",
        "foreign_key_check",
        "foreign_key_list",
        "foreign_keys",
        "function_list",
        "index_list",
        "index_xinfo",
        "integrity_check",
        "schema_version",
        "table_xinfo",
        "trusted_schema",
        "user_version",
    }
)
_SAFE_READ_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_RECURSIVE,
        sqlite3.SQLITE_SELECT,
    }
)
_OWNER_LOCK = threading.Lock()
_OWNER_TOKENS: dict[str, object] = {}
_SESSION_CONSTRUCTION_KEY = object()


class _AuthorizerGate:
    __slots__ = (
        "_allowed_family_insert_tables",
        "_allowed_functions",
        "_mode",
        "_terminal_action",
        "_terminal_consumed",
        "_trusted_schema_write",
        "_trusted_schema_write_consumed",
    )

    def __init__(
        self,
        allowed_family_insert_tables: frozenset[str],
        allowed_quantos_functions: frozenset[str],
    ) -> None:
        self._allowed_family_insert_tables = allowed_family_insert_tables
        self._allowed_functions = _FROZEN_SAFE_BUILTIN_FUNCTIONS | allowed_quantos_functions
        self._mode = _AuthorizerMode.RUNTIME
        self._terminal_action: str | None = None
        self._terminal_consumed = False
        self._trusted_schema_write: str | None = None
        self._trusted_schema_write_consumed = False

    def set_mode(self, mode: _AuthorizerMode) -> None:
        if type(mode) is not _AuthorizerMode:
            raise TypeError("authorizer mode must be exact")
        self._mode = mode

    def arm_trusted_schema_write(self, value: str) -> None:
        normalized = value.casefold()
        if normalized not in {"on", "off"} or self._trusted_schema_write is not None:
            raise FamilyAuthorityRuntimeError("trusted_schema transition cannot be armed")
        self._trusted_schema_write = normalized
        self._trusted_schema_write_consumed = False

    def require_trusted_schema_write_consumed(self) -> None:
        if not self._trusted_schema_write_consumed:
            raise FamilyAuthorityRuntimeError("trusted_schema transition was not authorizer-consumed")
        self._trusted_schema_write = None
        self._trusted_schema_write_consumed = False

    def arm_terminal(self, action: str) -> None:
        normalized = action.upper()
        if normalized not in {"COMMIT", "ROLLBACK"}:
            raise FamilyAuthorityRuntimeError("terminal transaction action cannot be armed")
        if self._terminal_action is not None and normalized != "ROLLBACK":
            raise FamilyAuthorityRuntimeError("terminal transaction action cannot be armed")
        self._mode = _AuthorizerMode.TERMINAL
        self._terminal_action = normalized
        self._terminal_consumed = False

    def require_terminal_consumed(self) -> None:
        if not self._terminal_consumed:
            raise FamilyAuthorityRuntimeError("terminal transaction action was not authorizer-consumed")

    def __call__(
        self,
        action: int,
        argument1: str | None,
        argument2: str | None,
        database: str | None,
        _source: str | None,
    ) -> int:
        if action in _SAFE_READ_ACTIONS:
            if action == sqlite3.SQLITE_READ and database not in {
                None,
                "main",
                "temp",
            }:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            function_name = (argument2 or argument1 or "").casefold()
            if function_name == "load_extension":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK if function_name in self._allowed_functions else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA:
            pragma_name = (argument1 or "").casefold()
            pragma_value = None if argument2 is None else argument2.casefold()
            if self._mode is not _AuthorizerMode.RUNTIME:
                return sqlite3.SQLITE_DENY
            if pragma_name == "trusted_schema" and pragma_value is not None:
                if self._trusted_schema_write == pragma_value and not self._trusted_schema_write_consumed:
                    self._trusted_schema_write_consumed = True
                    return sqlite3.SQLITE_OK
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK if pragma_name in _READ_ONLY_PRAGMAS else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_INSERT:
            return (
                sqlite3.SQLITE_OK
                if self._mode is _AuthorizerMode.CALLBACK
                and database == "main"
                and argument1 in self._allowed_family_insert_tables
                else sqlite3.SQLITE_DENY
            )
        if action == sqlite3.SQLITE_TRANSACTION:
            operation = (argument1 or "").upper()
            if (
                self._mode is _AuthorizerMode.TERMINAL
                and operation == self._terminal_action
                and not self._terminal_consumed
            ):
                self._terminal_consumed = True
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY


def _install_authorizer(
    connection: sqlite3.Connection,
    *,
    allowed_family_insert_tables: frozenset[str],
    allowed_quantos_functions: frozenset[str],
) -> _AuthorizerGate:
    gate = _AuthorizerGate(
        allowed_family_insert_tables,
        allowed_quantos_functions,
    )
    connection.set_authorizer(gate)
    return gate


class FamilyAuthorityWriteSession:
    """Opaque, revocable capability for exact-column family-table appends."""

    __slots__ = ("__active", "__append_row", "__counts", "__lease")

    def __init__(
        self,
        *,
        construction_key: object,
        lease: object,
        append_row: Callable[[object, str, dict[str, object]], None],
    ) -> None:
        if construction_key is not _SESSION_CONSTRUCTION_KEY:
            raise TypeError("family authority sessions are runtime-owned")
        self.__lease = lease
        self.__append_row = append_row
        self.__counts: dict[str, int] = {}
        self.__active = True

    def append(self, table_name: str, row: dict[str, object]) -> None:
        if not self.__active:
            raise FamilyAuthorityRuntimeError("family authority session lease is inactive")
        self.__append_row(self.__lease, table_name, row)
        self.__counts[table_name] = self.__counts.get(table_name, 0) + 1

    def _revoke(self, lease: object) -> tuple[tuple[str, int], ...]:
        if lease is not self.__lease:
            raise FamilyAuthorityRuntimeError("family authority session lease mismatch")
        self.__active = False
        return tuple(self.__counts.items())


FamilyAuthorityWriteCallback: TypeAlias = Callable[
    [FamilyAuthorityWriteSession],
    None,
]


def _canonical_database_path(database_path: str) -> str:
    if type(database_path) is not str or not database_path or "\x00" in database_path:
        raise TypeError("database_path must be non-empty exact text")
    if database_path == ":memory:" or database_path.casefold().startswith("file:"):
        raise FamilyAuthorityRuntimeError("family authority runtime requires a filesystem database path")
    path = Path(database_path)
    if not path.is_absolute():
        raise FamilyAuthorityRuntimeError("family authority runtime requires an absolute database path")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise FamilyAuthorityRuntimeError("family authority database path does not exist") from exc
    if not resolved.is_file():
        raise FamilyAuthorityRuntimeError("family authority database path must identify a regular file")
    return str(resolved)


def _owner_key(database_path: str) -> str:
    return os.path.normcase(os.path.realpath(database_path))


def _claim_owner(database_path: str) -> object:
    key = _owner_key(database_path)
    token = object()
    with _OWNER_LOCK:
        if key in _OWNER_TOKENS:
            raise FamilyAuthorityRuntimeError("family authority owner token is already held")
        _OWNER_TOKENS[key] = token
    return token


def _release_owner(database_path: str, token: object) -> None:
    key = _owner_key(database_path)
    with _OWNER_LOCK:
        if _OWNER_TOKENS.get(key) is not token:
            raise FamilyAuthorityRuntimeError("family authority owner token CAS failed")
        del _OWNER_TOKENS[key]


def _verify_main_and_empty_temp(connection: sqlite3.Connection) -> None:
    databases = tuple(str(row[1]) for row in connection.execute("PRAGMA database_list"))
    if databases not in (("main",), ("main", "temp")):
        raise FamilyAuthorityRuntimeError("family authority runtime found an unexpected SQLite namespace")
    if tuple(connection.execute("SELECT type,name,tbl_name,sql FROM temp.sqlite_schema ORDER BY type,name")):
        raise FamilyAuthorityRuntimeError("family authority runtime requires an empty temp namespace")


def _read_user_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    if row is None or len(row) != 1 or type(row[0]) is not int:
        raise FamilyAuthorityRuntimeError("family authority version read is invalid")
    return row[0]


def _set_trusted_schema(
    connection: sqlite3.Connection,
    value: str,
    *,
    gate: _AuthorizerGate | None,
) -> None:
    normalized = value.casefold()
    if normalized not in {"on", "off"}:
        raise FamilyAuthorityRuntimeError("trusted_schema value is invalid")
    if gate is not None:
        gate.set_mode(_AuthorizerMode.RUNTIME)
        gate.arm_trusted_schema_write(normalized)
    connection.execute(f"PRAGMA trusted_schema={normalized.upper()}")
    if gate is not None:
        gate.require_trusted_schema_write_consumed()
    row = connection.execute("PRAGMA trusted_schema").fetchone()
    expected = 1 if normalized == "on" else 0
    if row is None or tuple(row) != (expected,):
        raise FamilyAuthorityRuntimeError("trusted_schema transition did not reach its required state")


def _unregister_semantic_functions(connection: sqlite3.Connection) -> None:
    composition = _canonical_composition()
    abi_by_operation = {item.operation: item for item in composition.renderer_grammar.semantic_function_abis}
    for item in FAMILY_SCHEMA_SQLITE_SEMANTIC_IMPLEMENTATIONS:
        abi = abi_by_operation[item.operation]
        if item.kind is fs.RendererSemanticFunctionKind.SCALAR:
            connection.create_function(item.sqlite_name, abi.arity, None)
        else:
            connection.create_aggregate(item.sqlite_name, abi.arity, None)


def _append_contract() -> tuple[
    MappingProxyType[str, tuple[str, ...]],
    frozenset[str],
]:
    composition = _canonical_composition()
    columns = {
        table.table_name: tuple(column.name for column in table.columns)
        for table in composition.tables
        if table.table_name != "family_authority_genesis"
    }
    if len(columns) != 32:
        raise FamilyAuthorityRuntimeError("family authority append contract is not the exact sealed table set")
    return MappingProxyType(columns), frozenset(columns)


def _verify_append_accounting(
    pre_verification: FamilyAuthorityVerificationEvidence,
    post_verification: FamilyAuthorityVerificationEvidence,
    appended_row_counts: tuple[tuple[str, int], ...],
) -> None:
    if pre_verification.inherited_table_row_counts != post_verification.inherited_table_row_counts:
        raise FamilyAuthorityRuntimeError("family authority callback changed an inherited table")
    expected = dict(appended_row_counts)
    before = dict(pre_verification.family_table_row_counts)
    after = dict(post_verification.family_table_row_counts)
    if before.keys() != after.keys():
        raise FamilyAuthorityRuntimeError("family authority append accounting table set drifted")
    for table_name, before_count in before.items():
        if after[table_name] - before_count != expected.get(table_name, 0):
            raise FamilyAuthorityRuntimeError("family authority append accounting does not match the leased session")


def _open_fresh_connection(database_path: str) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(
            database_path,
            timeout=0.0,
            isolation_level=None,
            cached_statements=fs.FAMILY_SCHEMA_SQLITE_RUNTIME_PROFILE.connection_cached_statements,
            uri=False,
        )
    except sqlite3.Error as exc:
        raise FamilyAuthorityRuntimeError("family authority runtime could not open its fresh connection") from exc
    if type(connection) is not sqlite3.Connection:
        try:
            connection.close()
        finally:
            raise FamilyAuthorityRuntimeError("family authority runtime requires an exact sqlite3.Connection")
    return connection


def run_family_authority_transaction(
    database_path: str,
    *,
    expected_authority_instance_id: str,
    callback: FamilyAuthorityWriteCallback,
    external_providers: FamilyAuthorityExternalProviders | None = None,
) -> FamilyAuthorityRuntimeEvidence:
    """Execute one fail-closed append-only exact-v3 authority transaction."""

    canonical_path = _canonical_database_path(database_path)
    fs.validate_authority_instance_id(expected_authority_instance_id)
    if not callable(callback):
        raise TypeError("callback must be callable")
    if external_providers is not None and type(external_providers) is not (FamilyAuthorityExternalProviders):
        raise TypeError("external_providers must be FamilyAuthorityExternalProviders or None")
    if sqlite3.sqlite_version_info < (fs.FAMILY_SCHEMA_SQLITE_RUNTIME_PROFILE.minimum_sqlite_version):
        raise FamilyAuthorityRuntimeError("SQLite runtime version is below the floor")

    append_columns, append_tables = _append_contract()
    quantos_function_names = frozenset(
        item.sqlite_name.casefold() for item in FAMILY_SCHEMA_SQLITE_SEMANTIC_IMPLEMENTATIONS
    )
    transition_steps: list[fs.SqliteTrustTransitionStep] = []
    connection: sqlite3.Connection | None = None
    gate: _AuthorizerGate | None = None
    owner_token: object | None = None
    lease = object()
    session: FamilyAuthorityWriteSession | None = None
    functions_installed = False
    transaction_owned = False
    pre_verification: FamilyAuthorityVerificationEvidence | None = None
    post_verification: FamilyAuthorityVerificationEvidence | None = None
    appended_row_counts: tuple[tuple[str, int], ...] = ()

    def record(step: fs.SqliteTrustTransitionStep) -> None:
        expected = tuple(fs.SqliteTrustTransitionStep)[len(transition_steps)]
        if step is not expected:
            raise FamilyAuthorityRuntimeError("family authority trust transition order drifted")
        transition_steps.append(step)

    try:
        connection = _open_fresh_connection(canonical_path)
        record(fs.SqliteTrustTransitionStep.FRESH_NON_POOLED_CONNECTION)
        connection.enable_load_extension(False)
        record(fs.SqliteTrustTransitionStep.DISABLE_EXTENSION_LOADING)
        _set_trusted_schema(connection, "off", gate=None)
        record(fs.SqliteTrustTransitionStep.SET_TRUSTED_SCHEMA_OFF)
        connection.execute("PRAGMA foreign_keys=ON")
        if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
            raise FamilyAuthorityRuntimeError("family authority runtime requires foreign_keys=ON")
        _verify_main_and_empty_temp(connection)
        record(fs.SqliteTrustTransitionStep.VERIFY_MAIN_AND_EMPTY_TEMP)
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise FamilyAuthorityRuntimeError(
                "family authority runtime could not acquire the SQLite writer lock"
            ) from exc
        transaction_owned = True
        record(fs.SqliteTrustTransitionStep.BEGIN_IMMEDIATE)
        owner_token = _claim_owner(canonical_path)
        record(fs.SqliteTrustTransitionStep.CAS_EXCLUSIVE_OWNER_TOKEN)
        _reject_preexisting_quantos_functions(connection)
        try:
            _verify_pretrust_family_authority(
                connection,
                expected_authority_instance_id=expected_authority_instance_id,
            )
        except FamilyAuthorityVerificationError as exc:
            raise FamilyAuthorityRuntimeError(
                "family authority pre-trust version/catalog/genesis/identity verification failed"
            ) from exc
        record(fs.SqliteTrustTransitionStep.VERIFY_EXACT_SEALED_CATALOG)
        version_before_trust = _read_user_version(connection)
        if version_before_trust != 3:
            raise FamilyAuthorityRuntimeError("family authority pre-trust version must be exactly 3")
        record(fs.SqliteTrustTransitionStep.READ_SCHEMA_VERSION_BEFORE_TRUST)
        gate = _install_authorizer(
            connection,
            allowed_family_insert_tables=append_tables,
            allowed_quantos_functions=quantos_function_names,
        )
        record(fs.SqliteTrustTransitionStep.INSTALL_DENY_BY_DEFAULT_AUTHORIZER)
        _set_trusted_schema(connection, "on", gate=gate)
        record(fs.SqliteTrustTransitionStep.SET_TRUSTED_SCHEMA_ON_ONCE)
        version_after_trust = _read_user_version(connection)
        if version_after_trust != version_before_trust:
            raise FamilyAuthorityRuntimeError("family authority version changed across the trust transition")
        record(fs.SqliteTrustTransitionStep.READ_SCHEMA_VERSION_AFTER_TRUST)
        _register_semantic_functions(connection)
        functions_installed = True
        _verify_registered_semantic_functions(connection)
        record(fs.SqliteTrustTransitionStep.REGISTER_MANIFEST_BOUND_FUNCTIONS)
        try:
            pre_verification = _verify_registered_family_authority(
                connection,
                expected_authority_instance_id=expected_authority_instance_id,
                external_providers=external_providers,
            )
        except FamilyAuthorityVerificationError as exc:
            raise FamilyAuthorityRuntimeError("family authority pre-DML full verification failed") from exc

        def append_row(
            presented_lease: object,
            table_name: str,
            row: dict[str, object],
        ) -> None:
            if presented_lease is not lease:
                raise FamilyAuthorityRuntimeError("family authority session lease mismatch")
            if type(table_name) is not str:
                raise FamilyAuthorityRuntimeError("family authority append table must be exact text")
            if table_name == "family_authority_genesis":
                raise FamilyAuthorityRuntimeError("family authority genesis is never appendable at runtime")
            columns = append_columns.get(table_name)
            if columns is None:
                raise FamilyAuthorityRuntimeError("family authority append table is not sealed")
            if type(row) is not dict or tuple(row) != columns:
                raise FamilyAuthorityRuntimeError("family authority append requires the exact sealed column order")
            statement = (
                f'INSERT INTO "{table_name}" ('
                + ", ".join(f'"{column}"' for column in columns)
                + ") VALUES ("
                + ", ".join("?" for _ in columns)
                + ")"
            )
            try:
                connection.execute(statement, tuple(row[column] for column in columns))
            except sqlite3.Error as exc:
                raise FamilyAuthorityRuntimeError("family authority append was rejected") from exc

        session = FamilyAuthorityWriteSession(
            construction_key=_SESSION_CONSTRUCTION_KEY,
            lease=lease,
            append_row=append_row,
        )
        gate.set_mode(_AuthorizerMode.CALLBACK)
        callback_result = callback(session)
        appended_row_counts = session._revoke(lease)
        session = None
        gate.set_mode(_AuthorizerMode.RUNTIME)
        if callback_result is not None:
            raise FamilyAuthorityRuntimeError("family authority callback must return None")
        if not appended_row_counts:
            raise FamilyAuthorityRuntimeError("family authority transaction requires at least one append")
        record(fs.SqliteTrustTransitionStep.EXECUTE_CONTROLLED_DML)
        try:
            post_verification = _verify_registered_family_authority(
                connection,
                expected_authority_instance_id=expected_authority_instance_id,
                external_providers=external_providers,
            )
        except FamilyAuthorityVerificationError as exc:
            raise FamilyAuthorityRuntimeError("family authority post-DML full verification failed") from exc
        _verify_append_accounting(
            pre_verification,
            post_verification,
            appended_row_counts,
        )
        gate.arm_terminal("COMMIT")
        connection.commit()
        gate.require_terminal_consumed()
        transaction_owned = False
        record(fs.SqliteTrustTransitionStep.STORE_OWNED_TERMINAL_COMMIT_OR_ROLLBACK)
    except BaseException:
        if session is not None:
            try:
                session._revoke(lease)
            except BaseException:
                pass
        if connection is not None and transaction_owned:
            try:
                if gate is not None:
                    gate.arm_terminal("ROLLBACK")
                connection.rollback()
                if gate is not None:
                    gate.require_terminal_consumed()
            except BaseException as rollback_exc:
                try:
                    connection.close()
                except BaseException:
                    pass
                raise FamilyAuthorityRuntimeError(
                    "family authority rollback failed; connection closed"
                ) from rollback_exc
            finally:
                transaction_owned = False
        raise
    finally:
        cleanup_error: BaseException | None = None
        if connection is not None:
            if functions_installed:
                try:
                    _unregister_semantic_functions(connection)
                    record(fs.SqliteTrustTransitionStep.UNREGISTER_FUNCTIONS_IN_FINALLY)
                except BaseException as exc:
                    cleanup_error = exc
            if gate is not None:
                try:
                    _set_trusted_schema(connection, "off", gate=gate)
                    record(fs.SqliteTrustTransitionStep.SET_TRUSTED_SCHEMA_OFF_IN_FINALLY)
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
                try:
                    connection.set_authorizer(None)
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
            if owner_token is not None:
                try:
                    _release_owner(canonical_path, owner_token)
                    owner_token = None
                except BaseException as exc:
                    cleanup_error = cleanup_error or exc
            try:
                connection.close()
                if (
                    len(transition_steps) == len(tuple(fs.SqliteTrustTransitionStep)) - 1
                    and transition_steps[-1] is fs.SqliteTrustTransitionStep.SET_TRUSTED_SCHEMA_OFF_IN_FINALLY
                ):
                    record(fs.SqliteTrustTransitionStep.CLOSE_CONNECTION_IN_FINALLY)
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None and sys.exc_info()[0] is None:
            raise FamilyAuthorityRuntimeError("family authority runtime cleanup failed closed") from cleanup_error

    if pre_verification is None or post_verification is None:
        raise FamilyAuthorityRuntimeError("family authority runtime completed without verification evidence")
    return FamilyAuthorityRuntimeEvidence(
        authority_instance_id=expected_authority_instance_id,
        genesis_record_hash=pre_verification.genesis_record_hash,
        manifest_sha256=pre_verification.manifest_sha256,
        transition_steps=tuple(transition_steps),
        appended_row_counts=appended_row_counts,
        pre_verification=pre_verification,
        post_verification=post_verification,
        committed=True,
    )


__all__ = [
    "FamilyAuthorityRuntimeError",
    "FamilyAuthorityRuntimeEvidence",
    "FamilyAuthorityWriteCallback",
    "FamilyAuthorityWriteSession",
    "run_family_authority_transaction",
]
