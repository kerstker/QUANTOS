#!/usr/bin/env python
"""Generate or verify the deterministic canonical event schema catalog."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Sequence


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from quantos.contracts.event_schemas import CANONICAL_EVENT_SCHEMAS  # noqa: E402
from quantos.contracts.events import (  # noqa: E402
    EVENT_TIMESTAMP_UNIT_MICROSECONDS,
)
from quantos.contracts.hashing import source_file_sha256  # noqa: E402


DEFAULT_OUTPUT = REPO / "evidence" / "event_schema_catalog.json"
IMPLEMENTATION_PATHS = (
    "quantos/contracts/events.py",
    "quantos/contracts/event_schemas.py",
    "services/alpha/events.py",
    "services/alpha/maintenance_events.py",
    "services/console/api/system_views.py",
    "services/data/admission_store.py",
    "services/data/events.py",
    "services/data/refresh_history.py",
    "services/deployment/events.py",
    "services/deployment/runtime.py",
    "services/deployment/store.py",
    "services/execution/events.py",
    "services/execution/dry_run_store.py",
    "services/monitoring/domain_event_runtime.py",
    "services/monitoring/event_health.py",
    "services/risk/pre_trade/events.py",
    "services/risk/pre_trade/kill_switch_store.py",
    "services/risk/research_risk/events.py",
    "scripts/generate_event_schema_catalog.py",
    "tools/data_quality_admission.py",
)


def _canonical_bytes(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    return source_file_sha256(path)


def build_event_schema_catalog(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    schemas = CANONICAL_EVENT_SCHEMAS.manifest()
    latest_consumer_versions = CANONICAL_EVENT_SCHEMAS.latest_consumer_versions()
    legacy_units = []
    definition_count = 0
    for event_type, entry in schemas.items():
        definitions = entry["definitions"]
        definition_count += len(definitions)
        latest = next(
            item for item in definitions if item["version"] == entry["latest_version"]
        )
        if latest["timestamp_unit"] != EVENT_TIMESTAMP_UNIT_MICROSECONDS:
            raise ValueError(f"latest event schema is not canonical: {event_type}")
        for definition in definitions:
            if definition["timestamp_unit"] != EVENT_TIMESTAMP_UNIT_MICROSECONDS:
                legacy_units.append(
                    {
                        "event_type": event_type,
                        "timestamp_unit": definition["timestamp_unit"],
                        "version": definition["version"],
                    }
                )

    implementation_sha256 = {}
    for relative in IMPLEMENTATION_PATHS:
        path = root / relative
        if not path.is_file():
            raise ValueError(f"event schema implementation missing: {relative}")
        implementation_sha256[relative] = _sha256_file(path)

    schema_bytes = json.dumps(
        schemas,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema_version": 1,
        "verdict": "PASS",
        "mode": "VERSIONED_CANONICAL_EVENT_SCHEMA_CATALOG",
        "canonical_latest_timestamp_unit": EVENT_TIMESTAMP_UNIT_MICROSECONDS,
        "latest_timestamp_units_all_canonical": True,
        "event_type_count": len(schemas),
        "schema_definition_count": definition_count,
        "legacy_timestamp_unit_versions": sorted(
            legacy_units,
            key=lambda item: (item["event_type"], item["version"]),
        ),
        "latest_consumer_versions": latest_consumer_versions,
        "schemas": schemas,
        "schemas_sha256": hashlib.sha256(schema_bytes).hexdigest(),
        "implementation_sha256": implementation_sha256,
    }


def write_event_schema_catalog(path: str | Path, document: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_bytes(_canonical_bytes(document))
    temporary.replace(destination)


def event_schema_catalog_is_current(
    repo_root: str | Path,
    path: str | Path,
) -> bool:
    destination = Path(path)
    if not destination.is_file():
        return False
    expected = _canonical_bytes(build_event_schema_catalog(repo_root))
    return destination.read_bytes() == expected


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(REPO))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.check:
            if not event_schema_catalog_is_current(args.repo_root, args.output):
                print("event schema catalog is missing or stale", file=sys.stderr)
                return 1
            document = build_event_schema_catalog(args.repo_root)
            print(
                "event schema catalog: current; "
                f"types={document['event_type_count']}; "
                f"definitions={document['schema_definition_count']}"
            )
            return 0
        document = build_event_schema_catalog(args.repo_root)
        write_event_schema_catalog(args.output, document)
        print(
            f"event schema catalog: {args.output}; "
            f"types={document['event_type_count']}; "
            f"definitions={document['schema_definition_count']}"
        )
        return 0
    except (OSError, ValueError) as exc:
        print(f"event schema catalog generation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
