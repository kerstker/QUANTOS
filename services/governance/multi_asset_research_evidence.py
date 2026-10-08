"""Independent, fail-closed verification of retained multi-asset research.

This verifier intentionally does not import ``services.research``,
``services.alpha``, or ``tools``.  The evidence generator exercises those
production research primitives; this module independently replays the fixed FX
factor contract so the implementation does not certify itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path
import statistics
import time
from typing import Iterable, Mapping, Sequence

from quantos.analytics.rolling_statistics import rolling_population_deviation
from quantos.contracts.hashing import source_file_sha256, stable_hash
from quantos.contracts.multi_asset import RETAINED_FX_FACTOR_SPECS, RETAINED_FX_KEYS
from services.governance.artifact_admission import load_json_without_duplicate_keys
from services.governance.model_risk_dataset import DatasetBundleError, verify_pit_bundle


EVIDENCE_SCHEMA_VERSION = 1
EVIDENCE_ID = "quantos-multi-asset-research-fx-v1"
DEFAULT_EVIDENCE_REF = "evidence/multi_asset_research_baseline.json"
DEFAULT_BUNDLE_REF = "evidence/datasets/fx_pit_tradable_v1.jsonl.gz"
DEFAULT_MANIFEST_REF = "evidence/datasets/fx_pit_tradable_v1.manifest.json"

MULTI_ASSET_IMPLEMENTATION_PATHS = (
    "quantos/analytics/rolling_statistics.py",
    "quantos/contracts/multi_asset.py",
    "tools/fx_pit_bundle.py",
    "tools/ic_panel_registry.py",
    "tools/ic_panel_research.py",
    "services/research/ic_panel.py",
    "services/research/signals.py",
    "services/governance/model_risk_dataset.py",
    "services/governance/multi_asset_research_evidence.py",
    "scripts/generate_multi_asset_research_evidence.py",
)
MULTI_ASSET_VERIFICATION_PATHS = (
    "tests/research/test_rolling_statistics.py",
    "tests/research/test_fx_pit_bundle.py",
    "tests/research/test_ic_panel.py",
    "tests/research/test_ic_panel_registry.py",
    "tests/governance/test_multi_asset_research_evidence.py",
    "tests/governance/test_multi_asset_research_boundary.py",
)

_SCOPE = {
    "existing_primary_asset_class": "CRYPTO",
    "retained_non_crypto_asset_class": "FX",
    "research_mode": "CROSS_SECTIONAL_FACTOR_DIAGNOSTIC",
    "shared_engine_ref": "services/research/ic_panel.py",
}
_LIMITATIONS = [
    "Frankfurter/ECB reference rates are end-of-day reference observations, not executable bid/ask quotes",
    "the retained universe is a fixed liquid-currency research universe, not historical venue membership",
    "volume, market impact, borrow, and venue microstructure are unavailable in this bundle",
    "IC diagnostics are screening evidence only and do not authorize promotion, deployment, or trading",
]
_SAFETY = {
    "allow_ai_direct_trading": False,
    "allow_order_submission": False,
    "can_promote_or_trade": False,
    "readiness": "L2_DRY_RUN_ONLY",
    "real_broker_enabled": False,
}
_CONCLUSION = {
    "capability_verdict": "REPLAYABLE_MULTI_ASSET_RESEARCH",
    "research_verdict": "DIAGNOSTIC_ONLY_NO_PROMOTION",
    "statement": (
        "The retained FX replay proves a clean-checkout non-crypto factor-research "
        "path; its statistics do not establish a deployable or profitable alpha."
    ),
}


class MultiAssetResearchEvidenceError(ValueError):
    """The retained research artifact cannot support institutional evidence."""


@dataclass(frozen=True)
class _IndependentPanel:
    instruments: tuple[str, ...]
    event_times: tuple[int, ...]
    close: tuple[tuple[float | None, ...], ...]
    row_count: int


def _repo_ref(path: str | Path, root: Path) -> str:
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise MultiAssetResearchEvidenceError("evidence path escapes repository")
    return resolved.relative_to(root).as_posix()


def _hash_paths(root: Path, paths: Iterable[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for ref in paths:
        if not isinstance(ref, str) or not ref or Path(ref).is_absolute() or ".." in Path(ref).parts:
            raise MultiAssetResearchEvidenceError("governed source paths must be repository-relative")
        path = root / ref
        resolved = path.resolve()
        if (resolved != root and root not in resolved.parents) or not resolved.is_file():
            raise MultiAssetResearchEvidenceError(f"governed source is missing: {ref}")
        result[ref] = source_file_sha256(resolved)
    return result


def _decode_row(row: Mapping[str, object]) -> tuple[str, int, int, float]:
    key = row.get("key")
    event_time = row.get("event_time")
    knowledge_time = row.get("knowledge_time")
    payload_json = row.get("payload_json")
    if (
        not isinstance(key, str)
        or type(event_time) is not int
        or type(knowledge_time) is not int
        or not isinstance(payload_json, str)
    ):
        raise MultiAssetResearchEvidenceError("FX PIT replay row shape is invalid")
    if knowledge_time > event_time:
        raise MultiAssetResearchEvidenceError("FX PIT replay contains future-known data")
    try:
        payload = load_json_without_duplicate_keys(payload_json)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MultiAssetResearchEvidenceError("FX PIT replay payload is invalid") from exc
    if not isinstance(payload, dict):
        raise MultiAssetResearchEvidenceError("FX PIT replay payload must be an object")
    if (
        payload.get("instrument_id") != key
        or payload.get("event_time") != event_time
        or payload.get("knowledge_time") != knowledge_time
    ):
        raise MultiAssetResearchEvidenceError("FX PIT replay payload identity mismatch")
    close = payload.get("close")
    if (
        isinstance(close, bool)
        or not isinstance(close, (int, float))
        or not math.isfinite(float(close))
        or float(close) <= 0.0
    ):
        raise MultiAssetResearchEvidenceError("FX PIT replay close is invalid")
    return key, event_time, knowledge_time, float(close)


def _load_independent_panel(bundle: Path, manifest: Mapping[str, object]) -> _IndependentPanel:
    expected_keys = tuple(manifest.get("keys", ()))
    if expected_keys != RETAINED_FX_KEYS:
        raise MultiAssetResearchEvidenceError("retained FX universe does not match its fixed contract")
    cells: dict[tuple[str, int], tuple[int, float]] = {}
    observed: set[str] = set()
    event_times: set[int] = set()
    row_count = 0
    try:
        with gzip.open(bundle, "rt", encoding="utf-8", newline="") as handle:
            for line in handle:
                row = load_json_without_duplicate_keys(line)
                if not isinstance(row, dict):
                    raise MultiAssetResearchEvidenceError("FX PIT replay row must be an object")
                key, event_time, knowledge_time, close = _decode_row(row)
                if key not in RETAINED_FX_KEYS:
                    raise MultiAssetResearchEvidenceError(f"unexpected retained FX key: {key}")
                cell = (key, event_time)
                previous = cells.get(cell)
                if previous is not None and knowledge_time <= previous[0]:
                    raise MultiAssetResearchEvidenceError("FX PIT revisions are not monotonic")
                cells[cell] = (knowledge_time, close)
                observed.add(key)
                event_times.add(event_time)
                row_count += 1
    except (OSError, EOFError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, MultiAssetResearchEvidenceError):
            raise
        raise MultiAssetResearchEvidenceError(f"FX PIT replay cannot be decoded: {exc}") from exc
    if observed != set(RETAINED_FX_KEYS):
        raise MultiAssetResearchEvidenceError("FX PIT replay does not cover the fixed universe")
    if row_count != manifest.get("row_count"):
        raise MultiAssetResearchEvidenceError("FX PIT replay row count mismatch")
    ordered_times = tuple(sorted(event_times))
    close = tuple(
        tuple(cells.get((key, event_time), (0, None))[1] for key in RETAINED_FX_KEYS) for event_time in ordered_times
    )
    return _IndependentPanel(RETAINED_FX_KEYS, ordered_times, close, row_count)


def _signal(panel: _IndependentPanel, signal_kind: str, window: int) -> list[list[float | None]]:
    output: list[list[float | None]] = [[None for _ in panel.instruments] for _ in panel.event_times]
    if signal_kind == "negative_volatility":
        for column in range(len(panel.instruments)):
            received = [(row, close) for row in range(len(panel.event_times))
                if (close := panel.close[row][column]) is not None]
            returns = (current[1] / previous[1] - 1.0
                for previous, current in zip(received, received[1:]))
            deviations = rolling_population_deviation(returns, window)
            for (row, _), value in zip(received[1:], deviations):
                if value is not None:
                    output[row][column] = -value
        return output
    for column in range(len(panel.instruments)):
        history: list[float] = []
        for row in range(len(panel.event_times)):
            close = panel.close[row][column]
            if close is None:
                continue
            history.append(close)
            value: float | None = None
            if signal_kind == "momentum" and len(history) >= window:
                value = history[-1] / history[-window] - 1.0
            output[row][column] = value
    return output


def _rank(values: Sequence[float]) -> dict[float, int]:
    return {value: index for index, value in enumerate(sorted(values))}


def _one_ic(
    signal_row: Sequence[float | None],
    price_start: Sequence[float | None],
    price_end: Sequence[float | None],
) -> float | None:
    pairs: list[tuple[float, float]] = []
    for signal, start, end in zip(signal_row, price_start, price_end):
        if signal is None or start is None or end is None or start <= 0.0:
            continue
        pairs.append((signal, end / start - 1.0))
    if len(pairs) < 4:
        return None
    signal_rank = _rank([pair[0] for pair in pairs])
    return_rank = _rank([pair[1] for pair in pairs])
    left = [signal_rank[pair[0]] for pair in pairs]
    right = [return_rank[pair[1]] for pair in pairs]
    mean_left = statistics.fmean(left)
    mean_right = statistics.fmean(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    denominator_left = sum((value - mean_left) ** 2 for value in left) ** 0.5
    denominator_right = sum((value - mean_right) ** 2 for value in right) ** 0.5
    if denominator_left <= 0.0 or denominator_right <= 0.0:
        return None
    return numerator / (denominator_left * denominator_right)


def _ic_series(
    signal: Sequence[Sequence[float | None]],
    price: Sequence[Sequence[float | None]],
    horizon: int,
) -> tuple[list[float | None], list[int]]:
    starts = list(range(len(signal) - 1)) if horizon == 1 else list(range(0, len(signal) - horizon, horizon))
    return (
        [_one_ic(signal[start], price[start], price[start + horizon]) for start in starts],
        starts,
    )


def _aggregate(ics: Sequence[float | None], event_times: Sequence[int]) -> dict[str, object]:
    valid = [(index, value) for index, value in enumerate(ics) if value is not None]
    count = len(valid)
    if count < 10:
        return {
            "mean_ic": None,
            "ic_std": None,
            "icir": None,
            "hit_rate": None,
            "t_stat": None,
            "n_periods": count,
            "per_year": {},
        }
    values = [value for _, value in valid]
    mean = statistics.fmean(values)
    standard_deviation = statistics.pstdev(values)
    per_year_values: dict[int, list[float]] = {}
    for index, value in valid:
        year = time.gmtime(event_times[index] / 1000).tm_year
        per_year_values.setdefault(year, []).append(value)
    per_year = {
        str(year): round(statistics.fmean(year_values), 4) if len(year_values) >= 10 else None
        for year, year_values in per_year_values.items()
    }
    return {
        "mean_ic": round(mean, 4),
        "ic_std": round(standard_deviation, 4),
        "icir": round(mean / standard_deviation, 4) if standard_deviation > 0 else None,
        "hit_rate": round(
            sum(1 for value in values if (value > 0) == (mean > 0)) / count,
            4,
        ),
        "t_stat": round(mean / (standard_deviation / math.sqrt(count)), 4) if standard_deviation > 0 else None,
        "n_periods": count,
        "per_year": per_year,
    }


def _independent_factor_results(panel: _IndependentPanel) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for spec in RETAINED_FX_FACTOR_SPECS:
        signal = _signal(panel, spec.signal_kind, spec.window)
        by_period: dict[str, dict[str, object]] = {}
        for horizon in spec.holding_periods:
            series, starts = _ic_series(signal, panel.close, horizon)
            by_period[str(horizon)] = _aggregate(
                series,
                [panel.event_times[index] for index in starts],
            )
        results.append(
            {
                "name": spec.factor_id,
                "universe": "fx_tradable",
                "thesis": spec.thesis,
                "n_instruments": len(panel.instruments),
                **by_period["1"],
                "holding_periods": list(spec.holding_periods),
                "by_period": by_period,
            }
        )
    return results


def _iso_date(event_time_ms: int) -> str:
    return datetime.fromtimestamp(event_time_ms / 1000, tz=timezone.utc).date().isoformat()


def assemble_multi_asset_research_evidence(
    *,
    repo_root: str | Path,
    bundle_ref: str | Path,
    manifest_ref: str | Path,
    manifest: Mapping[str, object],
    event_time_count: int,
    factor_results: list[dict[str, object]],
    implementation_paths: Sequence[str] = MULTI_ASSET_IMPLEMENTATION_PATHS,
    verification_paths: Sequence[str] = MULTI_ASSET_VERIFICATION_PATHS,
) -> dict[str, object]:
    """Assemble canonical metadata around independently reproducible results."""

    root = Path(repo_root).resolve()
    minimum = manifest.get("min_event_time")
    maximum = manifest.get("max_event_time")
    if type(minimum) is not int or type(maximum) is not int:
        raise MultiAssetResearchEvidenceError("FX manifest time range is invalid")
    document: dict[str, object] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "evidence_id": EVIDENCE_ID,
        "as_of_event_time": maximum,
        "scope": dict(_SCOPE),
        "dataset": {
            "asset_class": "FX",
            "bundle_ref": _repo_ref(bundle_ref, root),
            "bundle_sha256": manifest.get("bundle_sha256"),
            "event_time_count": event_time_count,
            "event_time_unit": "unix_milliseconds",
            "instrument_count": len(RETAINED_FX_KEYS),
            "instruments": list(RETAINED_FX_KEYS),
            "knowledge_time_rule": "knowledge_time <= event_time",
            "manifest_ref": _repo_ref(manifest_ref, root),
            "manifest_sha256": manifest.get("manifest_sha256"),
            "max_event_time": maximum,
            "max_event_date": _iso_date(maximum),
            "min_event_time": minimum,
            "min_event_date": _iso_date(minimum),
            "price_field": "close",
            "row_count": manifest.get("row_count"),
            "source": "Frankfurter/ECB daily reference rates",
            "source_db_ref": manifest.get("source_db_ref"),
            "source_db_sha256": manifest.get("source_db_sha256"),
        },
        "factor_contract": [spec.as_dict() for spec in RETAINED_FX_FACTOR_SPECS],
        "factor_results": factor_results,
        "implementation_sha256": _hash_paths(root, implementation_paths),
        "verification_sha256": _hash_paths(root, verification_paths),
        "replay": {
            "command": "python scripts/generate_multi_asset_research_evidence.py --check",
            "deterministic": True,
            "network_required": False,
            "result_sha256": stable_hash(factor_results),
            "source_database_required": False,
        },
        "limitations": list(_LIMITATIONS),
        "conclusion": dict(_CONCLUSION),
        "safety": dict(_SAFETY),
    }
    document["evidence_sha256"] = stable_hash(document)
    return document


def verify_multi_asset_research_evidence(
    evidence_path: str | Path = DEFAULT_EVIDENCE_REF,
    *,
    repo_root: str | Path = Path.cwd(),
    bundle_path: str | Path = DEFAULT_BUNDLE_REF,
    manifest_path: str | Path = DEFAULT_MANIFEST_REF,
    implementation_paths: Sequence[str] = MULTI_ASSET_IMPLEMENTATION_PATHS,
    verification_paths: Sequence[str] = MULTI_ASSET_VERIFICATION_PATHS,
) -> dict:
    """Verify hashes and independently replay the complete retained FX result."""

    root = Path(repo_root).resolve()
    if tuple(implementation_paths) != MULTI_ASSET_IMPLEMENTATION_PATHS:
        raise MultiAssetResearchEvidenceError("implementation path contract was weakened or reordered")
    if tuple(verification_paths) != MULTI_ASSET_VERIFICATION_PATHS:
        raise MultiAssetResearchEvidenceError("verification path contract was weakened or reordered")
    if _repo_ref(bundle_path, root) != DEFAULT_BUNDLE_REF:
        raise MultiAssetResearchEvidenceError("retained FX bundle contract was changed")
    if _repo_ref(manifest_path, root) != DEFAULT_MANIFEST_REF:
        raise MultiAssetResearchEvidenceError("retained FX manifest contract was changed")
    evidence = Path(evidence_path)
    if not evidence.is_absolute():
        evidence = root / evidence
    if not evidence.is_file():
        raise MultiAssetResearchEvidenceError("multi-asset research evidence is missing")
    try:
        document = load_json_without_duplicate_keys(evidence.read_bytes())
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MultiAssetResearchEvidenceError(f"multi-asset evidence JSON is invalid: {exc}") from exc
    if not isinstance(document, dict):
        raise MultiAssetResearchEvidenceError("multi-asset evidence must be an object")
    claimed_digest = document.get("evidence_sha256")
    unsigned = dict(document)
    unsigned.pop("evidence_sha256", None)
    if claimed_digest != stable_hash(unsigned):
        raise MultiAssetResearchEvidenceError("multi-asset evidence self-digest mismatch")

    try:
        manifest = verify_pit_bundle(
            bundle_path,
            manifest_path,
            repo_root=root,
        )
    except DatasetBundleError as exc:
        raise MultiAssetResearchEvidenceError(f"retained FX bundle verification failed: {exc}") from exc
    bundle = Path(bundle_path)
    if not bundle.is_absolute():
        bundle = root / bundle
    panel = _load_independent_panel(bundle, manifest)
    independently_replayed = _independent_factor_results(panel)
    expected = assemble_multi_asset_research_evidence(
        repo_root=root,
        bundle_ref=bundle_path,
        manifest_ref=manifest_path,
        manifest=manifest,
        event_time_count=len(panel.event_times),
        factor_results=independently_replayed,
        implementation_paths=implementation_paths,
        verification_paths=verification_paths,
    )

    if document.get("implementation_sha256") != expected["implementation_sha256"]:
        raise MultiAssetResearchEvidenceError("implementation digest mismatch")
    if document.get("verification_sha256") != expected["verification_sha256"]:
        raise MultiAssetResearchEvidenceError("verification digest mismatch")
    if document.get("factor_results") != independently_replayed:
        raise MultiAssetResearchEvidenceError("independent replay result mismatch")
    if document.get("dataset") != expected["dataset"]:
        raise MultiAssetResearchEvidenceError("retained dataset evidence mismatch")
    if document != expected:
        raise MultiAssetResearchEvidenceError("multi-asset evidence metadata mismatch")
    return document


__all__ = [
    "DEFAULT_BUNDLE_REF",
    "DEFAULT_EVIDENCE_REF",
    "DEFAULT_MANIFEST_REF",
    "MULTI_ASSET_IMPLEMENTATION_PATHS",
    "MULTI_ASSET_VERIFICATION_PATHS",
    "MultiAssetResearchEvidenceError",
    "assemble_multi_asset_research_evidence",
    "verify_multi_asset_research_evidence",
]
