# Barra-lite Diagnostics Implementation Plan

> **⚠ SUPERSEDED — already landed** as `c48e210` (2026-07-02, `services/portfolio/barra_lite.py`),
> an independent rewrite (dict-keyed `zscore`, percentile winsorize, silent-zero on missing data —
> all different from this plan's prescribed `Sequence`-keyed/σ-winsorize/raise-on-missing design).
> **Do NOT execute this plan's embedded source code** — doing so would overwrite the shipped
> implementation, which `tools/rebuild_snapshots.py` already depends on, with an incompatible
> signature. Kept for historical/design-motivation reference only.
>
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only Barra-lite diagnostic layer that turns existing QUANTOS dry-run/research books into structured factor loadings and risk reports without changing weights, gates, or trading capability.

**Architecture:** Add a pure, deterministic `services/portfolio/barra_lite.py` core that converts caller-supplied descriptors and return streams into the existing `FactorRiskModel` contract plus a human-readable report. Add a gate-outside `tools/barra_lite_diagnostics.py` driver that reuses existing carry/crowd_contra/risk-parity research artifacts and writes `data/cache/barra_lite_snapshot.json`. Keep all trading, live, broker, and promotion paths untouched.

**Tech Stack:** Python stdlib only in `services/`; existing `quantos.contracts.factor.FactorRiskModel`; existing `services.portfolio.factor_risk.risk_decomposition`; pytest; existing governance scanner.

---

## File Structure

- Create `services/portfolio/barra_lite.py`
  - Pure functions only.
  - No IO, no network, no RNG, no wall clock.
  - Owns descriptor validation, z-scoring, correlation exposure, specific variance, `FactorRiskModel` construction, and report formatting.

- Create `tests/portfolio/test_barra_lite.py`
  - TDD coverage for deterministic math, fail-closed validation, and factor-risk identity.

- Create `tests/governance/test_barra_lite_boundary.py`
  - AST/source boundary checks for the new service module.

- Create `tools/barra_lite_diagnostics.py`
  - Gate-outside driver.
  - Reads existing local caches/panels.
  - Builds strategy-level descriptors for carry/crowd_contra/risk-parity books.
  - Writes `data/cache/barra_lite_snapshot.json`.

- Modify `docs/PROJECT_STATE.md`
  - Add one short state entry after true run evidence exists.

- Modify `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`
  - Mark Rank #2 as landed only after implementation and validation pass.

Do not modify:

- `services/execution/dry_run_pipeline.py`
- `services/risk/pre_trade/**`
- `services/trading/**`
- `services/research/research_runner/**`
- any live/broker/readiness config

---

### Task 1: Pure Barra-lite Core Tests

**Files:**
- Create: `tests/portfolio/test_barra_lite.py`

- [ ] **Step 1: Write failing tests for math helpers and fail-closed behavior**

Create `tests/portfolio/test_barra_lite.py` with:

```python
import pytest

from quantos.contracts.factor import FactorRiskModel
from services.portfolio.barra_lite import (
    barra_lite_report,
    build_barra_lite_model,
    corr_exposure,
    winsorize,
    zscore,
)


def test_zscore_preserves_none_and_standardizes_valid_values():
    out = zscore([1.0, 2.0, None, 3.0])
    assert out[2] is None
    valid = [x for x in out if x is not None]
    assert round(sum(valid), 12) == 0.0
    assert valid[0] < valid[1] < valid[2]


def test_zscore_constant_values_return_zero_for_valid_values():
    assert zscore([2.0, None, 2.0]) == [0.0, None, 0.0]


def test_winsorize_caps_extreme_values_and_preserves_none():
    out = winsorize([-5.0, 0.0, 5.0, None], limit=2.0)
    assert out == [-2.0, 0.0, 2.0, None]


def test_corr_exposure_returns_none_on_short_overlap():
    assert corr_exposure([0.1, None, 0.2], [0.1, 0.2, None], min_overlap=2) is None


def test_corr_exposure_detects_positive_and_negative_relationships():
    pos = corr_exposure([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0], min_overlap=4)
    neg = corr_exposure([1.0, 2.0, 3.0, 4.0], [8.0, 6.0, 4.0, 2.0], min_overlap=4)
    assert pos == pytest.approx(1.0)
    assert neg == pytest.approx(-1.0)


def test_build_barra_lite_model_outputs_existing_factor_contract():
    alpha_returns = {
        "carry": (0.01, 0.02, -0.01, 0.03, 0.00),
        "crowd_contra": (0.00, -0.01, 0.02, 0.01, 0.03),
    }
    descriptors = {
        "market_beta": {"carry": 0.2, "crowd_contra": -0.1},
        "liquidity": {"carry": -0.4, "crowd_contra": 0.3},
        "crowding": {"carry": 0.1, "crowd_contra": 1.0},
    }
    model = build_barra_lite_model(alpha_returns, descriptors, min_overlap=4)
    assert isinstance(model, FactorRiskModel)
    assert model.factor_ids == ("market_beta", "liquidity", "crowding")
    assert set(model.loadings) == {"carry", "crowd_contra"}
    assert all(v >= 0.0 for v in model.specific_var.values())


def test_build_barra_lite_model_rejects_missing_descriptor_alpha():
    alpha_returns = {"carry": (0.01, 0.02, 0.03), "crowd_contra": (0.02, 0.01, 0.00)}
    descriptors = {"market_beta": {"carry": 0.2}}
    with pytest.raises(ValueError, match="missing loading"):
        build_barra_lite_model(alpha_returns, descriptors, min_overlap=2)


def test_build_barra_lite_model_rejects_non_finite_descriptor():
    alpha_returns = {"carry": (0.01, 0.02, 0.03)}
    descriptors = {"market_beta": {"carry": float("nan")}}
    with pytest.raises(ValueError, match="non-finite"):
        build_barra_lite_model(alpha_returns, descriptors, min_overlap=2)


def test_build_barra_lite_model_rejects_non_psd_covariance():
    alpha_returns = {"carry": (0.01, 0.02, 0.03), "crowd_contra": (0.02, 0.01, 0.00)}
    descriptors = {
        "market_beta": {"carry": 0.2, "crowd_contra": -0.1},
        "liquidity": {"carry": -0.4, "crowd_contra": 0.3},
    }
    bad_cov = ((1.0, 2.0), (2.0, 1.0))
    with pytest.raises(ValueError, match="positive semi-definite"):
        build_barra_lite_model(alpha_returns, descriptors, factor_cov=bad_cov, min_overlap=2)


def test_barra_lite_report_contains_decomposition_and_top_risks():
    alpha_returns = {
        "carry": (0.01, 0.02, -0.01, 0.03, 0.00),
        "crowd_contra": (0.00, -0.01, 0.02, 0.01, 0.03),
    }
    descriptors = {
        "market_beta": {"carry": 0.2, "crowd_contra": -0.1},
        "liquidity": {"carry": -0.4, "crowd_contra": 0.3},
        "crowding": {"carry": 0.1, "crowd_contra": 1.0},
    }
    model = build_barra_lite_model(alpha_returns, descriptors, min_overlap=4)
    report = barra_lite_report(model, ["carry", "crowd_contra"], [0.5, 0.5])
    assert report["total_var"] == pytest.approx(report["factor_var"] + report["specific_var"])
    assert report["exposures"]["crowding"] == pytest.approx(0.55)
    assert report["top_risks"][0]["factor"] in {"market_beta", "liquidity", "crowding"}


def test_barra_lite_model_is_deterministic():
    alpha_returns = {
        "carry": (0.01, 0.02, -0.01, 0.03, 0.00),
        "crowd_contra": (0.00, -0.01, 0.02, 0.01, 0.03),
    }
    descriptors = {
        "market_beta": {"carry": 0.2, "crowd_contra": -0.1},
        "liquidity": {"carry": -0.4, "crowd_contra": 0.3},
        "crowding": {"carry": 0.1, "crowd_contra": 1.0},
    }
    a = build_barra_lite_model(alpha_returns, descriptors, min_overlap=4).model_dump()
    b = build_barra_lite_model(alpha_returns, descriptors, min_overlap=4).model_dump()
    assert a == b
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
rtk proxy pytest tests/portfolio/test_barra_lite.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'services.portfolio.barra_lite'
```

- [ ] **Step 3: Commit failing tests**

```bash
rtk proxy git add tests/portfolio/test_barra_lite.py
rtk proxy git commit -m "test(portfolio): Barra-lite 诊断核心行为测试"
```

---

### Task 2: Pure Barra-lite Core Implementation

**Files:**
- Create: `services/portfolio/barra_lite.py`
- Test: `tests/portfolio/test_barra_lite.py`

- [ ] **Step 1: Implement the pure core**

Create `services/portfolio/barra_lite.py` with:

```python
"""Barra-lite structured risk diagnostics.

Pure-Python, deterministic, no IO/network/RNG/wall-clock. This module does not
change portfolio weights, risk gates, dry-run execution, or alpha lifecycle
state. It only converts caller-supplied descriptors and alpha returns into the
existing FactorRiskModel contract plus a readable decomposition report.
"""
from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence

from quantos.contracts.factor import FactorRiskModel
from services.portfolio.factor_risk import risk_decomposition


Number = Optional[float]


def _finite(v: float) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(float(v))


def _valid(values: Sequence[Number]) -> list[float]:
    return [float(v) for v in values if v is not None and _finite(float(v))]


def winsorize(values: Sequence[Number], limit: float = 3.0) -> list[Number]:
    """Clip finite values to [-limit, limit], preserving None."""
    if not _finite(limit) or limit <= 0:
        raise ValueError("limit must be finite and > 0")
    out: list[Number] = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        fv = float(v)
        if not math.isfinite(fv):
            raise ValueError("winsorize received non-finite value")
        out.append(max(-limit, min(limit, fv)))
    return out


def zscore(values: Sequence[Number]) -> list[Number]:
    """Population z-score over finite values, preserving None.

    A constant valid series maps to 0 for valid entries. Missing values remain
    missing so callers cannot silently treat unknown exposure as neutral.
    """
    vals = _valid(values)
    if not vals:
        return [None for _ in values]
    mu = statistics.fmean(vals)
    sd = statistics.pstdev(vals)
    out: list[Number] = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        fv = float(v)
        if not math.isfinite(fv):
            raise ValueError("zscore received non-finite value")
        out.append(0.0 if sd == 0 else (fv - mu) / sd)
    return out


def _aligned_pairs(a: Sequence[Number], b: Sequence[Number]) -> list[tuple[float, float]]:
    pairs = []
    for x, y in zip(a, b):
        if x is None or y is None:
            continue
        fx, fy = float(x), float(y)
        if not math.isfinite(fx) or not math.isfinite(fy):
            raise ValueError("correlation inputs must be finite or None")
        pairs.append((fx, fy))
    return pairs


def corr_exposure(target_returns: Sequence[Number], factor_returns: Sequence[Number],
                  min_overlap: int = 20) -> Optional[float]:
    """Correlation exposure over overlapping finite observations.

    Returns None when overlap is insufficient or either side has zero variance.
    None means unavailable, not zero exposure.
    """
    pairs = _aligned_pairs(target_returns, factor_returns)
    if len(pairs) < min_overlap:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    if sx == 0.0 or sy == 0.0:
        return None
    cov = statistics.fmean((x - statistics.fmean(xs)) * (y - statistics.fmean(ys)) for x, y in pairs)
    corr = cov / (sx * sy)
    return max(-1.0, min(1.0, corr))


def _sample_var(values: Sequence[float]) -> float:
    if len(values) < 2:
        raise ValueError("at least two returns required for variance")
    return statistics.pvariance(values)


def _default_factor_cov(factor_ids: Sequence[str]) -> tuple[tuple[float, ...], ...]:
    k = len(factor_ids)
    return tuple(tuple(1.0 if i == j else 0.0 for j in range(k)) for i in range(k))


def _validate_factor_cov(factor_cov: Sequence[Sequence[float]], k: int) -> tuple[tuple[float, ...], ...]:
    cov = tuple(tuple(float(x) for x in row) for row in factor_cov)
    if len(cov) != k or any(len(row) != k for row in cov):
        raise ValueError("factor_cov must be k x k")
    return cov


def _residual_specific_var(ret: Sequence[float], loading_row: Sequence[float]) -> float:
    """Conservative specific variance estimate.

    This diagnostic layer does not fit a full regression model. Use realized
    return variance scaled down by bounded structured loading strength, leaving
    a strictly non-negative residual variance for the existing risk contract.
    """
    total = _sample_var(ret)
    strength = min(0.95, sum(abs(x) for x in loading_row) / max(len(loading_row), 1))
    return max(total * (1.0 - strength), 0.0)


def build_barra_lite_model(
    alpha_returns: dict[str, Sequence[float]],
    descriptors: dict[str, dict[str, float]],
    *,
    factor_cov: Optional[Sequence[Sequence[float]]] = None,
    min_overlap: int = 20,
) -> FactorRiskModel:
    """Build a FactorRiskModel from structured descriptors.

    All alpha ids in alpha_returns must have a finite loading for every factor.
    Missing/unknown exposure must be handled by the caller before this function;
    this function fails closed instead of filling 0.
    """
    if not alpha_returns:
        raise ValueError("alpha_returns must be non-empty")
    if not descriptors:
        raise ValueError("descriptors must be non-empty")
    factor_ids = tuple(sorted(descriptors))
    alpha_ids = tuple(sorted(alpha_returns))
    if min_overlap < 2:
        raise ValueError("min_overlap must be >= 2")

    loadings: dict[str, tuple[float, ...]] = {}
    specific_var: dict[str, float] = {}
    for aid in alpha_ids:
        ret = tuple(float(x) for x in alpha_returns[aid])
        if len(ret) < min_overlap:
            raise ValueError(f"alpha {aid} has fewer than min_overlap returns")
        if any(not math.isfinite(x) for x in ret):
            raise ValueError(f"alpha {aid} returns contain non-finite value")
        row = []
        for fid in factor_ids:
            if aid not in descriptors[fid]:
                raise ValueError(f"missing loading for alpha {aid} factor {fid}")
            value = float(descriptors[fid][aid])
            if not math.isfinite(value):
                raise ValueError(f"non-finite loading for alpha {aid} factor {fid}")
            row.append(value)
        loadings[aid] = tuple(row)
        specific_var[aid] = _residual_specific_var(ret, row)

    cov = _default_factor_cov(factor_ids) if factor_cov is None else _validate_factor_cov(factor_cov, len(factor_ids))
    return FactorRiskModel(
        factor_ids=factor_ids,
        loadings=loadings,
        factor_cov=cov,
        specific_var=specific_var,
    )


def barra_lite_report(model: FactorRiskModel, alpha_ids: Sequence[str], weights: Sequence[float]) -> dict:
    """Human-readable structured risk report for a weighting."""
    ids = list(alpha_ids)
    w = [float(x) for x in weights]
    if len(ids) != len(w):
        raise ValueError("alpha_ids and weights length mismatch")
    if any(not math.isfinite(x) for x in w):
        raise ValueError("weights must be finite")
    dec = risk_decomposition(w, model, ids)
    top = sorted(
        (
            {"factor": fid, "variance": var, "abs_variance": abs(var), "exposure": dec["exposures"][fid]}
            for fid, var in dec["by_factor"].items()
        ),
        key=lambda item: item["abs_variance"],
        reverse=True,
    )
    return {
        "alpha_ids": ids,
        "weights": {ids[i]: w[i] for i in range(len(ids))},
        "total_var": dec["total_var"],
        "factor_var": dec["factor_var"],
        "specific_var": dec["specific_var"],
        "by_factor": dec["by_factor"],
        "exposures": dec["exposures"],
        "top_risks": top,
    }
```

- [ ] **Step 2: Run focused tests**

```bash
rtk proxy pytest tests/portfolio/test_barra_lite.py -q
```

Expected:

```text
11 passed
```

- [ ] **Step 3: Run existing factor-risk and construction tests**

```bash
rtk proxy pytest tests/portfolio/test_factor_risk.py tests/portfolio/test_construction.py -q
```

Expected:

```text
all selected tests passed
```

- [ ] **Step 4: Commit core implementation**

```bash
rtk proxy git add services/portfolio/barra_lite.py tests/portfolio/test_barra_lite.py
rtk proxy git commit -m "feat(portfolio): Barra-lite 结构化风险诊断核心"
```

---

### Task 3: Governance Boundary Tests

**Files:**
- Create: `tests/governance/test_barra_lite_boundary.py`

- [ ] **Step 1: Write governance boundary tests**

Create `tests/governance/test_barra_lite_boundary.py` with:

```python
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "services" / "portfolio" / "barra_lite.py"


def _source() -> str:
    return MODULE.read_text(encoding="utf-8")


def test_barra_lite_service_has_no_network_or_broker_imports():
    tree = ast.parse(_source())
    forbidden_roots = {
        "requests", "httpx", "socket", "urllib", "aiohttp",
        "ccxt", "binance", "coinbase", "kraken", "alpaca",
        "ibapi", "ib_insync", "quickfix", "vnpy", "ctp", "pyctp",
    }
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                seen.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            seen.add(node.module.split(".")[0])
    assert not (seen & forbidden_roots)


def test_barra_lite_service_has_no_order_or_credential_surface():
    text = _source().lower()
    forbidden = [
        "submit_order", "place_order", "create_order", "cancel_order",
        "withdraw", "transfer", "api_key", "secret_key", "private_key",
        "live_trading_enabled=true", "real_broker_enabled=true",
        "allow_order_submission=true",
    ]
    assert [token for token in forbidden if token in text] == []
```

- [ ] **Step 2: Run boundary tests**

```bash
rtk proxy pytest tests/governance/test_barra_lite_boundary.py -q
```

Expected:

```text
2 passed
```

- [ ] **Step 3: Commit governance tests**

```bash
rtk proxy git add tests/governance/test_barra_lite_boundary.py
rtk proxy git commit -m "test(governance): 钉死 Barra-lite 诊断边界"
```

---

### Task 4: Offline Diagnostic Driver

**Files:**
- Create: `tools/barra_lite_diagnostics.py`
- Test indirectly through smoke run and snapshot shape check.

- [ ] **Step 1: Create the offline driver**

Create `tools/barra_lite_diagnostics.py` with:

```python
"""Offline Barra-lite diagnostics for existing QUANTOS research books.

Gate-outside tool: reads local caches/panels and writes a diagnostic snapshot.
No network, no broker, no credentials, no orders. The output is diagnostic only
and never changes portfolio weights, dry-run execution, promotion, or gates.
"""
from __future__ import annotations

import json
import math
import os
import statistics

from services.portfolio.barra_lite import (
    barra_lite_report,
    build_barra_lite_model,
    corr_exposure,
)
from services.research import alpha_expression as ax
from tools.capacity_research import _carry_panel
from tools.crypto_carry_strategy import COMPOSITE as CARRY_COMPOSITE, QUANTILE as CARRY_Q, _legs as carry_legs
from tools.crypto_oi_research import QUANTILE as OI_Q, _legs as oi_legs
from tools.crypto_oi_verify import CROWD_CONTRA
from tools.dry_run_combo_multi import _add_global_ls
from tools.scenario_tail_research import _book_series


OUT = os.path.join("data", "cache", "barra_lite_snapshot.json")


def _ann_var(series):
    if len(series) < 2:
        return None
    return statistics.pvariance(series) * 52


def _safe_corr(a, b):
    return corr_exposure(a, b, min_overlap=min(20, len(a), len(b)))


def _market_proxy(close):
    out = []
    for row in close:
        vals = [x for x in row if x is not None and x > 0]
        out.append(statistics.fmean(vals) if vals else None)
    ret = []
    for i in range(len(out) - 1):
        if out[i] and out[i + 1]:
            ret.append(out[i + 1] / out[i] - 1.0)
        else:
            ret.append(None)
    return ret


def _series_for_books():
    panel = _add_global_ls(_carry_panel())
    close = panel.matrices["close"]
    funding = panel.matrices["funding"]
    ev = panel.event_times

    carry_sig = ax.evaluate(CARRY_COMPOSITE, panel)
    crowd_sig = ax.evaluate(CROWD_CONTRA, panel)

    carry_gross, carry_ts, _ = _book_series(
        panel.instruments, close, funding, ev, carry_sig, carry_legs, CARRY_Q
    )
    crowd_gross, crowd_ts, _ = _book_series(
        panel.instruments, close, funding, ev, crowd_sig, oi_legs, OI_Q
    )

    n = min(len(carry_gross), len(crowd_gross))
    carry = list(carry_gross[-n:])
    crowd = list(crowd_gross[-n:])
    market = _market_proxy(close)[-n:]

    return {
        "alpha_returns": {
            "carry": tuple(carry),
            "crowd_contra": tuple(crowd),
        },
        "market": market,
        "timestamps": list(carry_ts[-n:]),
    }


def _descriptor_map(alpha_returns, market):
    carry = list(alpha_returns["carry"])
    crowd = list(alpha_returns["crowd_contra"])
    carry_var = _ann_var(carry) or 0.0
    crowd_var = _ann_var(crowd) or 0.0
    max_var = max(carry_var, crowd_var, 1e-12)

    market_carry = _safe_corr(carry, market)
    market_crowd = _safe_corr(crowd, market)
    if market_carry is None or market_crowd is None:
        raise ValueError("market_beta exposure unavailable")

    return {
        "market_beta": {
            "carry": market_carry,
            "crowd_contra": market_crowd,
        },
        "volatility": {
            "carry": carry_var / max_var,
            "crowd_contra": crowd_var / max_var,
        },
        "carry": {
            "carry": 1.0,
            "crowd_contra": 0.0,
        },
        "crowding": {
            "carry": 0.0,
            "crowd_contra": 1.0,
        },
        "liquidity": {
            # Conservative first-pass proxy: carry uses broader/lower-liquidity
            # universe; crowd_contra is concentrated in major OI names.
            "carry": -0.5,
            "crowd_contra": 0.5,
        },
    }


def _round_nested(obj):
    if isinstance(obj, float):
        if not math.isfinite(obj):
            return None
        return round(obj, 10)
    if isinstance(obj, dict):
        return {k: _round_nested(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_nested(v) for v in obj]
    if isinstance(obj, tuple):
        return [_round_nested(v) for v in obj]
    return obj


def main() -> None:
    series = _series_for_books()
    alpha_returns = series["alpha_returns"]
    descriptors = _descriptor_map(alpha_returns, series["market"])
    model = build_barra_lite_model(alpha_returns, descriptors, min_overlap=20)

    reports = {
        "carry": barra_lite_report(model, ["carry"], [1.0]),
        "crowd_contra": barra_lite_report(model, ["crowd_contra"], [1.0]),
        "equal_weight": barra_lite_report(model, ["carry", "crowd_contra"], [0.5, 0.5]),
    }
    snapshot = {
        "provenance": "BARRA_LITE_DIAGNOSTIC_OFFLINE",
        "readiness": "L2_DRY_RUN_ONLY",
        "honesty_note": "diagnostic only; no trading, no promotion, no weight change",
        "n_periods": len(next(iter(alpha_returns.values()))),
        "factors": list(model.factor_ids),
        "loadings": {k: list(v) for k, v in model.loadings.items()},
        "specific_var": dict(model.specific_var),
        "factor_cov": [list(row) for row in model.factor_cov],
        "descriptors": descriptors,
        "portfolio_reports": reports,
        "unavailable": {
            "sector": "UNKNOWN: no reliable crypto sector mapping in first version",
            "macro": "not used in core model; macro_regime remains retrospective diagnostics",
        },
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(_round_nested(snapshot), fh, ensure_ascii=False, indent=2)
    print(f"=== Barra-lite diagnostics wrote {OUT} ===", flush=True)
    print(f"  factors: {', '.join(model.factor_ids)}", flush=True)
    print(f"  periods: {snapshot['n_periods']}", flush=True)
    for name, rpt in reports.items():
        print(f"  {name}: total_var={rpt['total_var']:.8f} factor_var={rpt['factor_var']:.8f}", flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the driver**

```bash
rtk proxy python tools/barra_lite_diagnostics.py
```

Expected:

```text
=== Barra-lite diagnostics wrote data/cache/barra_lite_snapshot.json ===
factors: carry, crowding, liquidity, market_beta, volatility
periods: a positive integer printed by the driver
```

- [ ] **Step 3: Verify snapshot shape**

Run:

```bash
rtk proxy python -c "import json; p='data/cache/barra_lite_snapshot.json'; j=json.load(open(p,encoding='utf-8')); assert j['provenance']=='BARRA_LITE_DIAGNOSTIC_OFFLINE'; assert j['readiness']=='L2_DRY_RUN_ONLY'; assert 'portfolio_reports' in j; assert 'crowd_contra' in j['portfolio_reports']; print('snapshot ok')"
```

Expected:

```text
snapshot ok
```

- [ ] **Step 4: Run focused tests again**

```bash
rtk proxy pytest tests/portfolio/test_barra_lite.py tests/governance/test_barra_lite_boundary.py -q
```

Expected:

```text
all selected tests passed
```

- [ ] **Step 5: Commit driver**

```bash
rtk proxy git add tools/barra_lite_diagnostics.py
rtk proxy git commit -m "research(portfolio): Barra-lite 离线结构化风险诊断快照"
```

---

### Task 5: Documentation and Roadmap Update

**Files:**
- Modify: `docs/PROJECT_STATE.md`
- Modify: `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`

- [ ] **Step 1: Add state entry to PROJECT_STATE**

In `docs/PROJECT_STATE.md`, add a short entry near the current risk/portfolio findings:

```markdown
- **Barra-lite 结构化风险诊断(2026-07-01,`services/portfolio/barra_lite.py` + `tools/barra_lite_diagnostics.py`)**:
  在不改变任何组合权重/干跑流水线/风险门的前提下,把 carry 与 crowd_contra 的风险暴露结构化为
  `FactorRiskModel` 可消费的载荷、因子协方差、特异风险,并落 `barra_lite_snapshot.json`。第一版只做诊断:
  market_beta/volatility/liquidity/carry/crowding;sector 因无可靠 crypto mapping 诚实标 `UNKNOWN`;
  macro regime 仍是回顾性诊断,不做择时。**非门禁、非晋级、非下单。**
```

- [ ] **Step 2: Mark Rank #2 in roadmap**

In `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`, update Rank #2 from:

```markdown
| 2 | Barra-lite 结构化因子库(crypto 板块+风格+宏观) | 4 | P1 / M | `fundamental_store`(已填 FRED)、聚类 | 现在组合风险只有样本协方差,不透明;因子载荷解锁归因+压力测试+拥挤 |
```

to:

```markdown
| **2 ✓** | **Barra-lite 结构化风险诊断**(已做:market_beta/volatility/liquidity/carry/crowding,只读快照;sector/macro 诚实标注边界) | 4 | P1 / M | `FactorRiskModel`、`factor_risk.py`、carry/crowd 本地面板 | **已完成第一版诊断层**:不改权重/不接门/不晋级,把 carry/crowd_contra 暴露转成结构化载荷和 `barra_lite_snapshot.json`;sector 因无可靠映射标 UNKNOWN,macro 仍回顾性。后续 Rank #3 才考虑拥挤门接组合构建。 |
```

- [ ] **Step 3: Commit docs**

```bash
rtk proxy git add docs/PROJECT_STATE.md docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md
rtk proxy git commit -m "docs: Barra-lite 结构化风险诊断落地记入活文档"
```

---

### Task 6: Full Verification and Final Review

**Files:**
- No new files unless fixes are needed.

- [ ] **Step 1: Run targeted verification**

```bash
rtk proxy pytest tests/portfolio/test_barra_lite.py tests/governance/test_barra_lite_boundary.py tests/portfolio/test_factor_risk.py tests/portfolio/test_construction.py -q
```

Expected:

```text
all selected tests passed
```

- [ ] **Step 2: Run governance scanner**

```bash
rtk proxy python apps/research-console/scripts/run_paper_to_live_governance_check.py
```

Expected:

```text
SCANNER PASSED: No forbidden implementation items found.
```

- [ ] **Step 3: Run full fail-closed gate**

Use escalated execution if local sandbox cannot delete pytest temp directories:

```bash
rtk proxy python scripts/validate.py
```

Expected:

```text
VALIDATE PASSED (fail-closed gate green).
```

- [ ] **Step 4: Review git status**

```bash
rtk proxy git status --short --branch
```

Expected:

```text
## master...origin/master [ahead 4 or higher]
?? AGENTS.md
?? C...scratchpadresearch_queries.txt
?? tools/yahoo_equity_fetch.py
```

The three pre-existing untracked files must remain uncommitted unless the user separately approves them.

- [ ] **Step 5: Request code review**

Use `superpowers:requesting-code-review` after implementation completes:

```bash
rtk proxy git rev-parse HEAD~4
rtk proxy git rev-parse HEAD
```

Dispatch review with the actual SHAs printed by the two commands:

```text
DESCRIPTION: Implemented read-only Barra-lite structured risk diagnostics for carry/crowd_contra.
PLAN_OR_REQUIREMENTS: docs/superpowers/plans/2026-07-01-barra-lite-diagnostics.md and docs/superpowers/specs/2026-07-01-barra-lite-diagnostics-design.md.
BASE_SHA: paste the earlier SHA printed by git rev-parse HEAD~4
HEAD_SHA: paste the current SHA printed by git rev-parse HEAD
```

- [ ] **Step 6: Push when Git transport works**

```bash
rtk proxy git push origin master
```

Expected:

```text
master -> master
```

If `git push` still fails while `gh api rate_limit` works, report Git transport as blocked and do not use GitHub API to synthesize commits unless the user explicitly authorizes that workaround.

---

## Self-Review

**Spec coverage:**  
Spec §1 goal maps to Tasks 1-4. Spec §2 non-goals map to Task 3 governance tests and Task 6 status review. Spec §3 core/tool architecture maps to Tasks 2 and 4. Spec §4 data flow maps to Task 4. Spec §5 error handling maps to Task 1 fail-closed tests and Task 2 implementation. Spec §6 tests maps to Tasks 1 and 3. Spec §7 future extensions remain explicitly out of scope.

**Placeholder scan:**  
No deferred-detail markers are present. All code steps include exact code. Commands include expected results. Review handoff tells the implementer to paste actual SHAs printed by commands after commits exist.

**Type consistency:**  
`build_barra_lite_model(alpha_returns, descriptors, factor_cov=None, min_overlap=20)` is defined in Task 2 and used consistently in tests and driver. `barra_lite_report(model, alpha_ids, weights)` is defined in Task 2 and used consistently. Snapshot keys match the design spec. No task changes dry-run, broker, live, risk gate, or research runner code.
