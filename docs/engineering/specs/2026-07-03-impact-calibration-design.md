# Impact Calibration Primitive Design

## Context

Roadmap Rank #6 asks for "冲击模型校准(文献默认 + 敏感性,RE-1 收尾)". QUANTOS already has:

- `services/research/realism/impact.py`: a pure Almgren-Chriss style impact model.
- `services/research/realism/capacity_curve.py`: capacity curves and participation-cap binding logic.
- `tools/capacity_research.py`: a driver with hard-coded impact coefficient ranges.

The missing piece is not a new impact equation. The missing piece is a reusable, explicit calibration primitive that names the literature/default assumptions, provides conservative/base/aggressive scenarios, and exposes a documented coefficient band for `capacity_band`.

## Decision

Add a pure module:

- `services/research/realism/impact_calibration.py`

It will define deterministic calibration scenarios that can construct `AlmgrenChrissImpact` instances and provide the coefficient range consumed by `capacity_band`.

## API

```python
ImpactCalibrationScenario(
    name: str,
    temp_coef: float,
    perm_coef: float,
    temp_exponent: float,
    provenance: str,
    note: str,
)

ImpactCalibrationSet(
    provenance: str,
    conservative: ImpactCalibrationScenario,
    base: ImpactCalibrationScenario,
    aggressive: ImpactCalibrationScenario,
    source_note: str,
)

default_crypto_impact_calibration() -> ImpactCalibrationSet
impact_band_from_calibration(calibration: ImpactCalibrationSet) -> tuple[float, float]
scenario_impact(scenario: ImpactCalibrationScenario) -> AlmgrenChrissImpact
```

## Calibration Semantics

The default crypto calibration is deliberately labeled `UNCALIBRATED_PRINCIPLED`. It is a literature/default sensitivity assumption, not a venue-specific fit.

Scenario ordering:

- `aggressive`: lower impact coefficient, higher capacity.
- `base`: middle estimate.
- `conservative`: higher impact coefficient, lower capacity.

The band returned by `impact_band_from_calibration` must be `(conservative.temp_coef, aggressive.temp_coef)` because existing `capacity_band` interprets `impact_coef_lo_hi` as `(conservative, aggressive)`.

## Validation

Fail closed with `ValueError` when:

- any coefficient is non-finite;
- `temp_coef <= 0` or `perm_coef < 0`;
- `temp_exponent <= 0` or `temp_exponent > 1`;
- scenario ordering is invalid.

## Boundaries

This primitive is research diagnostics only:

- no IO;
- no network;
- no wall clock;
- no randomness;
- no `tools` import;
- no `services.execution`, `services.trading`, pre-trade, broker, order, credential, lifecycle, or console integration;
- no change to `CostModel`, dry-run fills, paper trading, or live readiness.

## Tests

Tests must cover:

- default scenario names and provenance;
- coefficients are positive and ordered conservative > base > aggressive;
- generated `AlmgrenChrissImpact` preserves scenario values;
- coefficient band plugs into `capacity_band` and produces an honest range;
- invalid scenario ordering/values fail closed;
- governance scan rejects network/broker/execution/order/credential surface.
