"""Exact-v2 inherited catalog contract used by the Pass-M composer."""

from __future__ import annotations

from services.research.experiment_registry.family_schema_v2_catalog import (
    EXACT_V2_INHERITED_SCHEMA_CATALOG,
)
from services.research.experiment_registry.store import SqliteExperimentStore


def test_inherited_catalog_matches_a_fresh_exact_v2_store() -> None:
    store = SqliteExperimentStore(":memory:", create=True)
    try:
        connection = store._conn
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        actual_objects = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT type,name,tbl_name FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        )
        assert tuple(
            (item.object_type, item.name, item.table_name)
            for item in EXACT_V2_INHERITED_SCHEMA_CATALOG.objects
        ) == actual_objects

        actual_tables: list[
            tuple[
                str,
                tuple[tuple[str, str, bool, int], ...],
                tuple[
                    tuple[
                        tuple[str, ...],
                        tuple[str, ...],
                        tuple[str, ...],
                    ],
                    ...,
                ],
            ]
        ] = []
        for table in EXACT_V2_INHERITED_SCHEMA_CATALOG.tables:
            column_rows = tuple(
                tuple(row)
                for row in connection.execute(f"PRAGMA table_info({table.table_name})")
            )
            columns = tuple(
                (str(row[1]), str(row[2]), bool(row[3]), int(row[5]))
                for row in column_rows
            )
            primary_key = tuple(
                name
                for _, name in sorted(
                    (int(row[5]), str(row[1]))
                    for row in column_rows
                    if int(row[5]) > 0
                )
            )
            unique_keys = (
                {
                    (
                        primary_key,
                        ("ASC",) * len(primary_key),
                        ("BINARY",) * len(primary_key),
                    )
                }
                if primary_key
                else set()
            )
            for index_row in connection.execute(
                f"PRAGMA index_list({table.table_name})"
            ):
                if not bool(index_row[2]) or bool(index_row[4]):
                    continue
                key_rows = tuple(
                    row
                    for row in connection.execute(
                        f"PRAGMA index_xinfo({index_row[1]})"
                    )
                    if bool(row[5]) and int(row[1]) >= 0
                )
                unique_keys.add(
                    (
                        tuple(str(row[2]) for row in key_rows),
                        tuple("DESC" if bool(row[3]) else "ASC" for row in key_rows),
                        tuple(str(row[4] or "BINARY").upper() for row in key_rows),
                    )
                )
            actual_tables.append(
                (
                    table.table_name,
                    columns,
                    tuple(sorted(unique_keys)),
                )
            )

        assert tuple(
            (
                table.table_name,
                tuple(
                    (
                        column.name,
                        column.sql_type.value,
                        column.not_null,
                        column.primary_key_ordinal,
                    )
                    for column in table.columns
                ),
                tuple(
                    (
                        key.columns,
                        tuple(item.value for item in key.directions),
                        tuple(item.value for item in key.collations),
                    )
                    for key in table.unique_keys
                ),
            )
            for table in EXACT_V2_INHERITED_SCHEMA_CATALOG.tables
        ) == tuple(actual_tables)

        assert tuple(
            (item.object_type, item.name, item.table_name, item.sql)
            for item in EXACT_V2_INHERITED_SCHEMA_CATALOG.sqlite_schema_entries
        ) == tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_schema "
                "ORDER BY type,name"
            )
        )
        for table in EXACT_V2_INHERITED_SCHEMA_CATALOG.tables:
            actual_indexes = []
            for index_row in connection.execute(
                f'PRAGMA index_list("{table.table_name}")'
            ):
                key_rows = tuple(
                    row
                    for row in connection.execute(
                        f'PRAGMA index_xinfo("{index_row[1]}")'
                    )
                    if bool(row[5])
                )
                actual_indexes.append(
                    (
                        str(index_row[1]),
                        bool(index_row[2]),
                        str(index_row[3]),
                        bool(index_row[4]),
                        tuple(str(row[2]) for row in key_rows),
                        tuple(bool(row[3]) for row in key_rows),
                        tuple(str(row[4] or "BINARY").upper() for row in key_rows),
                    )
                )
            assert tuple(sorted(actual_indexes)) == tuple(
                sorted(
                    (
                        item.name,
                        item.unique,
                        item.origin,
                        item.partial,
                        item.columns,
                        item.descending,
                        item.collations,
                    )
                    for item in EXACT_V2_INHERITED_SCHEMA_CATALOG.indexes
                    if item.table_name == table.table_name
                )
            )
            assert tuple(
                connection.execute(f'PRAGMA foreign_key_list("{table.table_name}")')
            ) == ()
    finally:
        store.close()


def test_inherited_catalog_is_closed_and_deployment_neutral() -> None:
    catalog = EXACT_V2_INHERITED_SCHEMA_CATALOG
    assert catalog.catalog_id == "EXACT_V2_FAMILY_SCHEMA_INHERITED_CATALOG_V1"
    assert catalog.schema_version == 2
    assert len(catalog.tables) == 13
    assert len(catalog.objects) == 39
    assert len(catalog.sqlite_schema_entries) == 60
    assert len(catalog.indexes) == 24
    assert catalog.owned_sqlite_sequence_tables == (
        "evaluation_audit_dispatch",
        "evaluation_audit_outbox",
        "experiment_event_dispatch",
        "experiment_event_outbox",
        "search_attempt_events",
        "search_attempt_heartbeats",
        "search_attempt_recoveries",
        "search_candidates",
        "search_evaluations",
    )
    assert not hasattr(catalog, "database_path")
    assert not hasattr(catalog, "connection")


def test_inherited_catalog_freezes_the_exact_logical_origin_projection() -> None:
    catalog = EXACT_V2_INHERITED_SCHEMA_CATALOG
    projection = catalog.origin_projection

    assert projection.projection_id == "QUANTOS_EXACT_V2_LOGICAL_ORIGIN_V1"
    assert projection.projection_version == 1
    assert projection.domain_separator == b"QUANTOS_EXACT_V2_LOGICAL_ORIGIN_V1"
    assert projection.digest_algorithm == "SHA256"
    assert projection.schema_field_order == ("type", "name", "tbl_name", "sql")
    assert projection.schema_entry_order == tuple(
        (entry.object_type, entry.name) for entry in catalog.sqlite_schema_entries
    )
    assert projection.table_order == tuple(
        table.table_name for table in catalog.tables
    )
    assert {
        item.table_name: item.columns for item in projection.primary_key_order
    } == {
        table.table_name: tuple(
            column.name
            for column in sorted(
                table.columns,
                key=lambda item: item.primary_key_ordinal or len(table.columns) + 1,
            )
            if column.primary_key_ordinal
        )
        for table in catalog.tables
    }
    assert projection.sequence_order == catalog.owned_sqlite_sequence_tables
    assert projection.chain_head_order == (
        "evaluation_audit",
        "evaluation_audit_dispatch",
        "experiment_event_dispatch",
        "experiment_event_outbox",
        "search_candidates",
        "search_evaluations",
        "search_events",
        "search_heartbeats",
        "search_recoveries",
    )
    assert tuple(
        (item.sqlite_storage_class, item.tag, item.payload_encoding)
        for item in projection.type_tags
    ) == (
        ("NULL", b"N", "EMPTY"),
        ("INTEGER", b"I", "SIGNED_INT64_BIG_ENDIAN"),
        ("REAL", b"R", "IEEE754_BINARY64_BIG_ENDIAN"),
        ("TEXT", b"T", "UTF8"),
        ("BLOB", b"B", "RAW_BYTES"),
    )
    assert projection.integer_encoding == "SIGNED_INT64_BIG_ENDIAN"
    assert projection.real_encoding == "IEEE754_BINARY64_BIG_ENDIAN"
    assert projection.text_encoding == "UTF8"
    assert projection.blob_encoding == "RAW_BYTES"
    assert (
        projection.frame_encoding
        == "LABEL_ASCII_U32_BIG_ENDIAN_VALUE_U64_BIG_ENDIAN"
    )
