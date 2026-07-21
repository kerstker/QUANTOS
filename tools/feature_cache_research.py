"""Materialize and verify a deterministic PIT feature cache from the retained FX bundle.

The default run refreshes a local SQLite cache under ``data/cache`` and writes a small,
content-addressed evidence summary. ``--check`` never writes the cache: it rebuilds the
same values in memory and compares the tracked evidence. No network, live feed, broker,
or trading path is reachable from this driver.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantos.contracts.feature import (
    FeatureDefinition,
    FeatureRegistryItem,
    FeatureStatus,
    FeatureValue,
)
from services.features.registry_store import SqliteFeatureRegistryStore
from services.features.value_store import SqliteFeatureValueStore
from services.research.alpha_expression import FieldPanel, evaluate
from tools.feature_distribution_drift_research import FEATURE_DEFINITIONS
from tools.fx_pit_bundle import DEFAULT_BUNDLE, DEFAULT_MANIFEST, load_fx_pit_panel


REPO = Path(__file__).resolve().parents[1]
OUTPUT = REPO / "evidence" / "fx_feature_value_cache_baseline.json"
DEFAULT_STORE = REPO / "data" / "cache" / "fx_feature_store.db"
STORE_RELATIVE = "data/cache/fx_feature_store.db"
FEATURE_VERSION = "fx-pit-v1"
PROVENANCE = "REAL_PIT_FEATURE_VALUE_CACHE_V1"
CODE_VERSION = "feature_cache_v1"


class FeatureCacheEvidenceError(ValueError):
    """The retained feature-cache evidence cannot be verified."""


def _definition(feature: Mapping[str, Any]) -> FeatureDefinition:
    return FeatureDefinition(
        feature_id=str(feature["feature_id"]),
        name=str(feature["feature_id"]),
        formula=str(feature["formula"]),
        inputs=("fx_pit_tradable_v1.close",),
    )


def materialize_panel(
    panel: FieldPanel,
    registry: SqliteFeatureRegistryStore,
    values: SqliteFeatureValueStore,
) -> dict[str, dict[str, Any]]:
    """Write all finite expression values and return stable read-back summaries."""
    summaries: dict[str, dict[str, Any]] = {}
    for feature in FEATURE_DEFINITIONS:
        feature_id = str(feature["feature_id"])
        registry.register(
            FeatureRegistryItem(
                definition=_definition(feature),
                version=FEATURE_VERSION,
                owner="FX PIT research",
                status=FeatureStatus.VALIDATED,
            ),
            leakage_passed=True,
            code_version=CODE_VERSION,
        )
        matrix = evaluate(feature["expression"], panel)
        if len(matrix) != len(panel.event_times):
            raise FeatureCacheEvidenceError(f"{feature_id}: matrix/event-time length mismatch")
        rows: list[FeatureValue] = []
        for event_time, row in zip(panel.event_times, matrix):
            if len(row) != len(panel.instruments):
                raise FeatureCacheEvidenceError(f"{feature_id}: matrix row width mismatch")
            for instrument_id, raw in zip(panel.instruments, row):
                if raw is None:
                    continue
                try:
                    value = float(raw)
                except (TypeError, ValueError) as exc:
                    raise FeatureCacheEvidenceError(f"{feature_id}: non-numeric value") from exc
                if not math.isfinite(value):
                    continue
                # The source bundle is verified with knowledge_time <= event_time.  The
                # FeatureValue contract intentionally uses the conservative event boundary.
                rows.append(
                    FeatureValue(
                        feature_id=feature_id,
                        instrument_id=str(instrument_id),
                        event_time=int(event_time),
                        knowledge_time=int(event_time),
                        value=value,
                    )
                )
        values.add_many(rows, version=FEATURE_VERSION)
        summary = values.summary(feature_id, FEATURE_VERSION)
        summary["content_sha256"] = values.content_digest(feature_id, FEATURE_VERSION)
        summary["leakage_passed"] = True
        summary["code_version"] = CODE_VERSION
        summaries[feature_id] = summary
    return summaries


def _verify_readback(
    panel: FieldPanel,
    values: SqliteFeatureValueStore,
    summaries: Mapping[str, Mapping[str, Any]],
) -> None:
    for feature_id, summary in summaries.items():
        if int(summary["row_count"]) <= 0:
            raise FeatureCacheEvidenceError(f"{feature_id}: empty materialized cache")
        first_event = summary["min_event_time"]
        if first_event is None:
            raise FeatureCacheEvidenceError(f"{feature_id}: missing first event time")
        for instrument_id in panel.instruments:
            if values.as_of(feature_id, FEATURE_VERSION, instrument_id, int(first_event) - 1):
                raise FeatureCacheEvidenceError(f"{feature_id}: PIT read-back leaked before first event")


def build_cache_evidence(
    panel: FieldPanel,
    manifest: Mapping[str, Any],
    registry: SqliteFeatureRegistryStore,
    values: SqliteFeatureValueStore,
    *,
    store_path: str = STORE_RELATIVE,
) -> dict[str, Any]:
    summaries = materialize_panel(panel, registry, values)
    _verify_readback(panel, values, summaries)
    total_rows = sum(int(summary["row_count"]) for summary in summaries.values())
    return {
        "schema_version": 1,
        "provenance": PROVENANCE,
        "load_bearing": False,
        "source": {
            "bundle_ref": str(manifest.get("bundle_ref", "")),
            "bundle_sha256": str(manifest.get("bundle_sha256", "")),
            "manifest_sha256": str(manifest.get("manifest_sha256", "")),
            "knowledge_rule": "source knowledge_time <= event_time; materialized knowledge_time = event_time",
        },
        "cache": {
            "store_path": store_path,
            "store_mode": "SQLITE_BITEMPORAL_PIT",
            "feature_version": FEATURE_VERSION,
            "feature_count": len(summaries),
            "total_rows": total_rows,
            "instrument_count": len(panel.instruments),
            "event_time_count": len(panel.event_times),
            "readback_verified": True,
        },
        "features": summaries,
        "honesty_note": (
            "This cache is rebuilt from the retained FX PIT bundle only. It is an offline research cache, "
            "not a live or paper feed; no scheduled freshness/threshold alert is claimed, and it does not "
            "gate promotion, sizing, dry-run execution, or orders."
        ),
    }


def _memory_evidence(panel: FieldPanel, manifest: Mapping[str, Any]) -> dict[str, Any]:
    registry = SqliteFeatureRegistryStore(":memory:", create=True)
    values = SqliteFeatureValueStore(":memory:", create=True)
    try:
        return build_cache_evidence(panel, manifest, registry, values)
    finally:
        registry.close()
        values.close()


def build_default_evidence() -> dict[str, Any]:
    panel, manifest = load_fx_pit_panel(DEFAULT_BUNDLE, DEFAULT_MANIFEST, repo_root=REPO)
    return _memory_evidence(panel, manifest)


def _materialize_file(panel: FieldPanel, manifest: Mapping[str, Any], target: Path) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    registry = None
    values = None
    try:
        registry = SqliteFeatureRegistryStore(str(temporary), create=True)
        values = SqliteFeatureValueStore(str(temporary), create=True)
        document = build_cache_evidence(panel, manifest, registry, values)
        os.replace(temporary, target)
        return document
    finally:
        if registry is not None:
            registry.close()
        if values is not None:
            values.close()
        # A failed materialization must not leave a misleading partial cache beside the target.
        temporary.unlink(missing_ok=True)


def verify_cache_evidence(path: str | Path = OUTPUT) -> dict[str, Any]:
    target = Path(path)
    try:
        actual = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FeatureCacheEvidenceError(f"feature cache evidence is unreadable: {exc}") from exc
    expected = build_default_evidence()
    if actual != expected:
        raise FeatureCacheEvidenceError("feature cache evidence does not match the verified PIT replay")
    return actual


def _write(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify tracked evidence without writing")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    args = parser.parse_args(argv)
    output = args.output if args.output.is_absolute() else REPO / args.output
    if args.check:
        document = verify_cache_evidence(output)
        print(
            f"feature cache evidence: {document['cache']['total_rows']} rows, "
            f"{document['cache']['feature_count']} features, readback=PASS"
        )
        return 0

    panel, manifest = load_fx_pit_panel(DEFAULT_BUNDLE, DEFAULT_MANIFEST, repo_root=REPO)
    expected = _memory_evidence(panel, manifest)
    store = args.store if args.store.is_absolute() else REPO / args.store
    actual = _materialize_file(panel, manifest, store)
    if actual != expected:
        raise FeatureCacheEvidenceError("persistent cache differs from deterministic in-memory replay")
    _write(output, expected)
    print(f"wrote {output}; materialized {store}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CODE_VERSION",
    "DEFAULT_STORE",
    "FeatureCacheEvidenceError",
    "FEATURE_VERSION",
    "PROVENANCE",
    "build_cache_evidence",
    "build_default_evidence",
    "main",
    "materialize_panel",
    "verify_cache_evidence",
]
