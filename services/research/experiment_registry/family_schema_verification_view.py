"""Read-only typed row snapshots for family-schema semantic verification.

This module deliberately knows nothing about the family-schema catalog or any
component's semantics.  It only freezes caller-supplied SQLite-shaped rows so
component verifiers cannot mutate or observe a changing row mapping.  The
future total verifier remains responsible for exact table and column coverage.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable


SQLiteScalar: TypeAlias = None | int | float | str | bytes
SQLiteRow: TypeAlias = Mapping[str, SQLiteScalar]

_SQLITE_INTEGER_MIN = -(1 << 63)
_SQLITE_INTEGER_MAX = (1 << 63) - 1


def _exact_identifier(value: object, context: str) -> str:
    if type(value) is not str or not value:
        raise TypeError(f"{context} must be a non-empty exact str")
    return value


def _freeze_scalar(value: object) -> SQLiteScalar:
    if value is None:
        return None
    if type(value) is int:
        if not _SQLITE_INTEGER_MIN <= value <= _SQLITE_INTEGER_MAX:
            raise ValueError("SQLite INTEGER scalar is outside signed 64-bit range")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("SQLite REAL scalar must be finite")
        return value
    if type(value) is str:
        return value
    if type(value) is bytes:
        return memoryview(value).tobytes()
    raise TypeError("SQLite scalar must be None, exact int/float/str, or bytes")


@dataclass(frozen=True, slots=True)
class FrozenVerificationRow(Mapping[str, SQLiteScalar]):
    """An immutable, insertion-ordered copy of one SQLite result row."""

    _items: tuple[tuple[str, SQLiteScalar], ...]

    def __post_init__(self) -> None:
        if type(self._items) is not tuple:
            raise TypeError("verification row items must be a tuple")
        frozen: list[tuple[str, SQLiteScalar]] = []
        names: set[str] = set()
        for item in self._items:
            if type(item) is not tuple or len(item) != 2:
                raise TypeError("verification row item must be an exact pair")
            name = _exact_identifier(item[0], "column name")
            if name in names:
                raise ValueError(f"duplicate column name: {name}")
            names.add(name)
            frozen.append((name, _freeze_scalar(item[1])))
        object.__setattr__(self, "_items", tuple(frozen))

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "FrozenVerificationRow":
        if not isinstance(row, Mapping):
            raise TypeError("verification row must be a Mapping")
        return cls(tuple(row.items()))

    def __getitem__(self, key: str) -> SQLiteScalar:
        if type(key) is not str:
            raise KeyError(key)
        for name, value in self._items:
            if name == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (name for name, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)


@runtime_checkable
class FamilySchemaVerificationView(Protocol):
    """Minimal read-only interface consumed by component semantic verifiers."""

    def rows(self, table_name: str, /) -> tuple[SQLiteRow, ...]: ...


@dataclass(frozen=True, slots=True)
class FrozenFamilySchemaVerificationView:
    """A table-indexed immutable row snapshot used by focused component tests."""

    _tables: tuple[tuple[str, tuple[FrozenVerificationRow, ...]], ...]

    def __post_init__(self) -> None:
        if type(self._tables) is not tuple:
            raise TypeError("verification tables must be a tuple")
        names: set[str] = set()
        frozen: list[tuple[str, tuple[FrozenVerificationRow, ...]]] = []
        for item in self._tables:
            if type(item) is not tuple or len(item) != 2:
                raise TypeError("verification table entry must be an exact pair")
            table_name = _exact_identifier(item[0], "table name")
            if table_name in names:
                raise ValueError(f"duplicate table input: {table_name}")
            names.add(table_name)
            rows = item[1]
            if type(rows) is not tuple or any(type(row) is not FrozenVerificationRow for row in rows):
                raise TypeError("verification table rows must be frozen row tuples")
            frozen.append((table_name, rows))
        object.__setattr__(self, "_tables", tuple(frozen))

    @classmethod
    def from_rows(
        cls,
        tables: Mapping[str, Iterable[Mapping[str, object]]] | Iterable[tuple[str, Iterable[Mapping[str, object]]]],
    ) -> "FrozenFamilySchemaVerificationView":
        source = tables.items() if isinstance(tables, Mapping) else tables
        frozen: list[tuple[str, tuple[FrozenVerificationRow, ...]]] = []
        for item in source:
            if type(item) is not tuple or len(item) != 2:
                raise TypeError("verification table input must contain exact pairs")
            table_name = _exact_identifier(item[0], "table name")
            try:
                rows = tuple(FrozenVerificationRow.from_mapping(row) for row in item[1])
            except TypeError:
                raise
            frozen.append((table_name, rows))
        return cls(tuple(frozen))

    def rows(self, table_name: str, /) -> tuple[SQLiteRow, ...]:
        name = _exact_identifier(table_name, "table name")
        for candidate, rows in self._tables:
            if candidate == name:
                return rows
        raise KeyError(name)


__all__ = [
    "FamilySchemaVerificationView",
    "FrozenFamilySchemaVerificationView",
    "FrozenVerificationRow",
    "SQLiteRow",
    "SQLiteScalar",
]
