"""Factor Garden materializer (727 §6.2 FactorObservation / F3, 2026-07-27).

Evaluates a garden factor definition over a PIT FieldPanel and writes the resulting
(T x N) matrix through to the schema-v2 :class:`SqliteFeatureValueStore` — one row per
observation carrying computed_at_ms / dataset_version / materializer_version /
quality_verdict, so "which version of the data produced which value, computed when, by
which materializer" is answerable from the store itself.

knowledge_time ruling (the honest kt semantics — read before changing):
``FieldPanel`` carries NO per-cell knowledge_time; its construction contract
(``build_field_panel``: ``latest_as_of(inst, t, t)`` used only when the latest knowable
bar's event_time == t, else None) is "cell (t, i) is knowable at t". ``evaluate`` only
composes time-series ops (look back over rows <= t) and cross-sectional ops (same row t),
so the factor value at (t, i) is computable entirely from data knowable at t — it becomes
knowable at t itself. Hence kt := event_time, the same kt=et convention the repo already
uses for EOD data stores (FRED / Deribit). This is derived from the panel's PIT contract,
not invented; the materializer inherits that contract exactly like every other
``evaluate`` consumer — a caller-built contaminated panel contaminates kt=et too.

Boundaries:
- quality_verdict is a CALLER/GLOBAL marker written to every row of the batch — per-cell
  leakage/quality detection is a later slice (F4), not fabricated here.
- None cells (missing inputs / warmup) are NOT materialized — no observation exists
  (mirrors the materialize_rolling_feature warmup precedent). NaN/Inf cells are not
  materialized either, and ARE counted (skipped_non_finite).
- Idempotent by construction: the store's PRIMARY KEY + INSERT OR IGNORE makes a rerun
  of the same inputs insert 0 rows.
- Deterministic core: no wall clock (computed_at_ms is caller-supplied int ms), no RNG,
  no network; the store is injected, the module holds no IO of its own.
"""
from __future__ import annotations

import math
from typing import Sequence

from quantos.contracts.feature import FeatureDefinition, FeatureValue
from services.features.value_store import SqliteFeatureValueStore
from services.research.alpha_expression import FieldPanel, deserialize, evaluate

MATERIALIZER_VERSION = "1.0.0"


def _require_text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def materialize_factor(
    defn: FeatureDefinition,
    panel: FieldPanel,
    store: SqliteFeatureValueStore,
    *,
    computed_at_ms: int,
    dataset_version: str,
    factor_version: str = "1.0.0",
    quality_verdict: str = "OK",
) -> dict:
    """Evaluate `defn` over `panel` and append the observations to `store` (schema v2).

    Returns {factor_id, factor_version, written, skipped_existing, skipped_non_finite,
    instruments, periods, dataset_version, materializer_version}.
    """
    if not isinstance(defn, FeatureDefinition):
        raise TypeError("defn must be a FeatureDefinition")
    if not isinstance(panel, FieldPanel):
        raise TypeError("panel must be a FieldPanel")
    if not isinstance(store, SqliteFeatureValueStore):
        raise TypeError("store must be a SqliteFeatureValueStore")
    if isinstance(computed_at_ms, bool) or not isinstance(computed_at_ms, int) \
            or computed_at_ms < 0:
        raise ValueError("computed_at_ms must be a caller-supplied non-negative int ms")
    dataset_version = _require_text(dataset_version, "dataset_version")
    factor_version = _require_text(factor_version, "factor_version")
    quality_verdict = _require_text(quality_verdict, "quality_verdict")

    matrix = evaluate(deserialize(defn.formula), panel)
    values = []
    skipped_non_finite = 0
    for ti, t in enumerate(panel.event_times):
        for ii, inst in enumerate(panel.instruments):
            v = matrix[ti][ii]
            if v is None:
                continue                    # no observation (missing/warmup) — not materialized
            if not math.isfinite(v):
                skipped_non_finite += 1     # NaN/Inf never reaches the store
                continue
            values.append(FeatureValue(
                feature_id=defn.name,
                instrument_id=inst,
                event_time=int(t),
                knowledge_time=int(t),       # kt := et — see the module docstring ruling
                value=float(v),
            ))
    written = store.add_many(
        values,
        version=factor_version,
        computed_at_ms=computed_at_ms,
        dataset_version=dataset_version,
        materializer_version=MATERIALIZER_VERSION,
        quality_verdict=quality_verdict,
    )
    return {
        "factor_id": defn.name,
        "factor_version": factor_version,
        "written": written,
        "skipped_existing": len(values) - written,
        "skipped_non_finite": skipped_non_finite,
        "instruments": len(panel.instruments),
        "periods": len(panel.event_times),
        "dataset_version": dataset_version,
        "materializer_version": MATERIALIZER_VERSION,
    }


def materialize_many(
    defns: Sequence[FeatureDefinition],
    panel: FieldPanel,
    store: SqliteFeatureValueStore,
    **kwargs,
) -> dict:
    """Materialize several factors over one panel; a single factor's failure is isolated
    (recorded as {"factor_id", "error"} in the result, same isolation precedent as
    services/alpha/maintenance) and never drags down the others."""
    results = {}
    for defn in defns:
        name = defn.name if isinstance(defn, FeatureDefinition) else repr(defn)
        try:
            results[name] = materialize_factor(defn, panel, store, **kwargs)
        except Exception as exc:                     # per-factor isolation — deliberate
            results[name] = {"factor_id": name, "error": f"{type(exc).__name__}: {exc}"}
    return results
