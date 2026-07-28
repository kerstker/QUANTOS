"""Build deterministic PIT feature-distribution drift evidence.

This driver is deliberately offline and read-only.  It replays the retained,
versioned FX PIT bundle, evaluates three causal rolling features, and reduces
each date to a cross-sectional median before applying the existing KS
diagnostic.  The median is an explicit diagnostic contract; it is not a
promotion or risk gate and it never invents a missing instrument value.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.data.distribution_drift import (
    DistributionDriftPolicy,
    evaluate_feature_distribution_drift,
)
from services.research.alpha_expression import FieldPanel, evaluate
from tools.fx_pit_bundle import (
    DEFAULT_BUNDLE,
    DEFAULT_MANIFEST,
    load_fx_pit_panel,
)


REPO = Path(__file__).resolve().parents[1]
OUTPUT = REPO / "evidence" / "feature_distribution_drift_baseline.json"
PROVENANCE = "REAL_PIT_FEATURE_DISTRIBUTION_DRIFT_V1"
AGGREGATION = "CROSS_SECTIONAL_MEDIAN"
MIN_CROSS_SECTIONAL_NAMES = 3


# These are all causal expressions over the close field.  ``evaluate`` only
# looks backwards on the time axis and across names at the same timestamp.
FEATURE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "feature_id": "fx_momentum_20",
        "formula": "close / delay(close,20) - 1",
        "expression": (
            "sub",
            ("div", ("field", "close"), ("ts_delay", ("field", "close"), 20)),
            ("const", 1.0),
        ),
    },
    {
        "feature_id": "fx_volatility_20",
        "formula": "std(close / delay(close,1) - 1,20)",
        "expression": (
            "ts_std",
            (
                "sub",
                ("div", ("field", "close"), ("ts_delay", ("field", "close"), 1)),
                ("const", 1.0),
            ),
            20,
        ),
    },
    {
        "feature_id": "fx_distance_from_high_20",
        "formula": "close / rolling_max(close,20) - 1",
        "expression": (
            "sub",
            ("div", ("field", "close"), ("ts_max", ("field", "close"), 20)),
            ("const", 1.0),
        ),
    },
)


class FeatureDriftEvidenceError(ValueError):
    """The retained PIT feature evidence cannot be verified."""


def _cross_sectional_median(
    matrix: Sequence[Sequence[float | None]],
    event_times: Sequence[int],
    *,
    minimum_names: int = MIN_CROSS_SECTIONAL_NAMES,
) -> tuple[list[float], list[int]]:
    if len(matrix) != len(event_times):
        raise FeatureDriftEvidenceError("feature matrix and event-time lengths differ")
    if isinstance(minimum_names, bool) or not isinstance(minimum_names, int) or minimum_names < 2:
        raise FeatureDriftEvidenceError("minimum_names must be an integer >= 2")
    values: list[float] = []
    timestamps: list[int] = []
    for row, timestamp in zip(matrix, event_times):
        finite = [float(value) for value in row if value is not None and math.isfinite(float(value))]
        if len(finite) < minimum_names:
            continue
        values.append(float(statistics.median(finite)))
        timestamps.append(int(timestamp))
    return values, timestamps


def _feature_series(panel: FieldPanel) -> tuple[dict[str, list[float]], dict[str, dict[str, Any]]]:
    values: dict[str, list[float]] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for definition in FEATURE_DEFINITIONS:
        matrix = evaluate(definition["expression"], panel)
        series, timestamps = _cross_sectional_median(matrix, panel.event_times)
        values[definition["feature_id"]] = series
        metadata[definition["feature_id"]] = {
            "formula": definition["formula"],
            "aggregation": AGGREGATION,
            "minimum_cross_sectional_names": MIN_CROSS_SECTIONAL_NAMES,
            "observations": len(series),
            "first_event_time": timestamps[0] if timestamps else None,
            "last_event_time": timestamps[-1] if timestamps else None,
        }
    return values, metadata


def build_feature_drift_evidence(
    panel: FieldPanel,
    manifest: Mapping[str, Any],
    *,
    manifest_sha256: str,
    policy: DistributionDriftPolicy | None = None,
) -> dict[str, Any]:
    """Build a deterministic evidence document from a verified PIT panel."""
    values, metadata = _feature_series(panel)
    effective_policy = policy or DistributionDriftPolicy()
    assessment = evaluate_feature_distribution_drift(
        values,
        expected_features=tuple(item["feature_id"] for item in FEATURE_DEFINITIONS),
        policy=effective_policy,
    )
    return {
        "schema_version": 1,
        "provenance": PROVENANCE,
        "calibration": assessment.calibration,
        "load_bearing": False,
        "source": {
            "bundle_ref": str(manifest.get("bundle_ref", "")),
            "bundle_sha256": str(manifest.get("bundle_sha256", "")),
            "manifest_sha256": manifest_sha256,
            "knowledge_rule": "knowledge_time <= event_time",
        },
        "panel": {
            "instruments": list(panel.instruments),
            "instrument_count": len(panel.instruments),
            "event_time_count": len(panel.event_times),
            "first_event_time": panel.event_times[0] if panel.event_times else None,
            "last_event_time": panel.event_times[-1] if panel.event_times else None,
        },
        "policy": {
            "baseline_window": effective_policy.baseline_window,
            "recent_window": effective_policy.recent_window,
            "min_observations": effective_policy.min_observations,
            "warn_ks": effective_policy.warn_ks,
            "block_ks": effective_policy.block_ks,
        },
        "features": metadata,
        "assessment": assessment.as_dict(),
        "honesty_note": (
            "This is a read-only diagnostic rebuilt from the retained PIT FX bundle. "
            "It does not claim that a live feed or scheduled FeatureValue cache exists, "
            "and it does not gate promotion, sizing, or orders."
        ),
    }


def build_default_evidence() -> dict[str, Any]:
    panel, manifest = load_fx_pit_panel(DEFAULT_BUNDLE, DEFAULT_MANIFEST, repo_root=REPO)
    return build_feature_drift_evidence(
        panel,
        manifest,
        # Use the manifest's validated self-digest, not an ambiguous raw-file
        # digest, so the evidence binds to the same contract as the PIT loader.
        manifest_sha256=str(manifest.get("manifest_sha256", "")),
    )


def verify_feature_drift_evidence(path: str | Path = OUTPUT) -> dict[str, Any]:
    target = Path(path)
    try:
        actual = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FeatureDriftEvidenceError(f"feature drift evidence is unreadable: {exc}") from exc
    expected = build_default_evidence()
    if actual != expected:
        raise FeatureDriftEvidenceError("feature drift evidence does not match the verified PIT replay")
    return actual


def _write(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the tracked evidence without writing")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    target = args.output if args.output.is_absolute() else (REPO / args.output)
    if args.check:
        document = verify_feature_drift_evidence(target)
        print(
            f"feature drift evidence: {document['assessment']['status']} "
            f"({len(document['features'])} features, {document['panel']['instrument_count']} instruments)"
        )
        return 0
    document = build_default_evidence()
    _write(target, document)
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AGGREGATION",
    "FEATURE_DEFINITIONS",
    "FeatureDriftEvidenceError",
    "PROVENANCE",
    "build_default_evidence",
    "build_feature_drift_evidence",
    "main",
    "verify_feature_drift_evidence",
]
