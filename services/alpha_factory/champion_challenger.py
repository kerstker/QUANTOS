"""Champion / Challenger (727 §10, first slice) — freeze pins + comparison policy.

§10.1: a Champion running in Paper/Shadow/Micro-Live/Live must be FROZEN —
alpha_version, code_hash, config_hash, factor_versions, dataset/cost/risk
policy.「不得在线自动调参」is a machine-checkable property: `assert_frozen`
compares the frozen pins against the CURRENT environment and reports drift
codes. This slice is the comparison-evidence half of §10.2/§10.3; the
replacement flow itself (human approval, micro-live graduation) is downstream
governance and deliberately NOT here.

§10.3: never compare on total return alone. `compare_champion_challenger`
evaluates a fixed multi-metric panel (missing keys stay honestly None) and
applies a FIXED verdict policy — double-superiority on net_sharpe AND
max_drawdown with no guard-metric regression, or it is not CHALLENGER_BETTER.

Deterministic core: no I/O, no wall clock, no RNG.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from quantos.contracts.hashing import stable_hash


class ChampionChallengerError(ValueError):
    """A snapshot or comparison input fails fail-closed validation."""


#: The §10.3 comparison panel. Direction convention (documented, fixed):
#: max_drawdown / expected_shortfall are given as POSITIVE MAGNITUDES (loss
#: size), turnover/fees/correlation as plain levels — LOWER is better for the
#: lower-is-better set, higher for the rest.
HIGHER_IS_BETTER = frozenset(
    {"net_sharpe", "sortino", "capacity", "regime_stability", "reality_retention"}
)
LOWER_IS_BETTER = frozenset(
    {"max_drawdown", "expected_shortfall", "turnover", "fees", "correlation"}
)
COMPARISON_KEYS = tuple(sorted(HIGHER_IS_BETTER | LOWER_IS_BETTER))

#: Guard metrics: a regression on any of these (when present on both sides)
#: blocks CHALLENGER_BETTER even when the two key metrics say otherwise.
_GUARD_KEYS = ("sortino", "expected_shortfall", "regime_stability", "reality_retention")
_KEY_METRICS = ("net_sharpe", "max_drawdown")


def _text(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ChampionChallengerError(f"{field} must be a string")
    if value != value.strip():
        raise ChampionChallengerError(f"{field} must not have surrounding whitespace")
    if not value and not allow_empty:
        raise ChampionChallengerError(f"{field} must not be empty")
    return value


def _optional_number(value: object, field: str) -> Optional[float]:
    """None stays None (metric absent); NaN/Inf/non-number is fail-closed."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ChampionChallengerError(f"{field} must be a number or None")
    number = float(value)
    if not math.isfinite(number):
        raise ChampionChallengerError(f"{field} must be finite")
    return number


@dataclass(frozen=True)
class ChampionSnapshot:
    """The immutable freeze record of a running Champion (727 §10.1)."""

    alpha_id: str
    alpha_version: str
    code_hash: str
    config_hash: str
    factor_versions: tuple[str, ...]
    dataset_policy_hash: str
    cost_policy_hash: str
    risk_policy_hash: str
    frozen_at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "alpha_id", _text(self.alpha_id, "alpha_id"))
        object.__setattr__(
            self, "alpha_version", _text(self.alpha_version, "alpha_version")
        )
        # code_hash/config_hash MAY be "" — honest UNPINNED; assert_frozen then
        # has no opinion on that pin (documented there, never silently PASSed).
        object.__setattr__(
            self, "code_hash", _text(self.code_hash, "code_hash", allow_empty=True)
        )
        object.__setattr__(
            self,
            "config_hash",
            _text(self.config_hash, "config_hash", allow_empty=True),
        )
        if isinstance(self.factor_versions, str) or not isinstance(
            self.factor_versions, Sequence
        ):
            raise ChampionChallengerError(
                "factor_versions must be a sequence of strings"
            )
        object.__setattr__(
            self,
            "factor_versions",
            tuple(
                _text(v, "factor_versions[]") for v in self.factor_versions
            ),
        )
        object.__setattr__(
            self,
            "dataset_policy_hash",
            _text(self.dataset_policy_hash, "dataset_policy_hash", allow_empty=True),
        )
        object.__setattr__(
            self,
            "cost_policy_hash",
            _text(self.cost_policy_hash, "cost_policy_hash", allow_empty=True),
        )
        object.__setattr__(
            self,
            "risk_policy_hash",
            _text(self.risk_policy_hash, "risk_policy_hash", allow_empty=True),
        )
        if (
            isinstance(self.frozen_at_ms, bool)
            or not isinstance(self.frozen_at_ms, int)
            or self.frozen_at_ms < 0
        ):
            raise ChampionChallengerError(
                "frozen_at_ms must be a non-negative integer"
            )


def champion_snapshot_hash(snapshot: ChampionSnapshot) -> str:
    """Content hash over the freeze pins (frozen_at_ms is the provenance of
    the freezing act, not part of the pin set — same convention as the
    hypothesis/trial identity hashes)."""
    return stable_hash(
        {
            "alpha_id": snapshot.alpha_id,
            "alpha_version": snapshot.alpha_version,
            "code_hash": snapshot.code_hash,
            "config_hash": snapshot.config_hash,
            "cost_policy_hash": snapshot.cost_policy_hash,
            "dataset_policy_hash": snapshot.dataset_policy_hash,
            "factor_versions": list(snapshot.factor_versions),
            "risk_policy_hash": snapshot.risk_policy_hash,
        }
    )


def assert_frozen(
    snapshot: ChampionSnapshot, *, current_code_hash: str, current_config_hash: str
) -> tuple[str, ...]:
    """§10.1「不得在线自动调参」as a machine check: compare the frozen pins
    against the CURRENT environment. An UNPINNED ("") snapshot hash yields NO
    opinion on that pin — absence of a pin is a fact the caller must handle,
    never a silent PASS."""
    if not isinstance(snapshot, ChampionSnapshot):
        raise ChampionChallengerError("snapshot must be a ChampionSnapshot")
    violations: list[str] = []
    if snapshot.code_hash and snapshot.code_hash != current_code_hash:
        violations.append("CHAMPION_CODE_DRIFT")
    if snapshot.config_hash and snapshot.config_hash != current_config_hash:
        violations.append("CHAMPION_CONFIG_DRIFT")
    return tuple(violations)


def _marginal_ir(
    champion_metrics: Mapping[str, object], challenger_metrics: Mapping[str, object]
) -> Optional[float]:
    """Residual IR of the challenger orthogonalized on the champion:
    (ir_c - rho * ir_h) / sqrt(1 - rho^2). Computed ONLY when both IRs and a
    correlation are supplied; otherwise None (honest — never fabricated).
    |rho| >= 1 makes the residual undefined -> None."""
    ir_h = _optional_number(champion_metrics.get("ir"), "champion ir")
    ir_c = _optional_number(challenger_metrics.get("ir"), "challenger ir")
    rho = _optional_number(
        challenger_metrics.get("correlation", champion_metrics.get("correlation")),
        "correlation",
    )
    if ir_h is None or ir_c is None or rho is None:
        return None
    if abs(rho) >= 1.0:
        return None
    return (ir_c - rho * ir_h) / math.sqrt(1.0 - rho * rho)


def compare_champion_challenger(
    champion_metrics: Mapping[str, object],
    challenger_metrics: Mapping[str, object],
) -> dict:
    """§10.3 multi-metric comparison with a FIXED verdict policy.

    - Every panel key is reported for both sides; missing keys stay None
      (honest absence, never zero-filled).
    - CHALLENGER_BETTER requires BOTH key metrics (net_sharpe higher AND
      max_drawdown lower) AND no regression on any guard metric present on
      both sides (sortino / expected_shortfall / regime_stability /
      reality_retention).「不得只比较总收益」——单指标优势永远不够。
    - Missing key metrics, a mixed key-metric picture, or a guard regression
      -> INCONCLUSIVE (the fixed policy refuses to call it; a human looks).
    - Challenger worse-or-equal on BOTH key metrics -> CHAMPION_STANDS.
    """
    for name, metrics in (
        ("champion_metrics", champion_metrics),
        ("challenger_metrics", challenger_metrics),
    ):
        if not isinstance(metrics, Mapping):
            raise ChampionChallengerError(f"{name} must be a mapping")

    panel: dict[str, dict[str, object]] = {}
    for key in COMPARISON_KEYS:
        champion_value = _optional_number(
            champion_metrics.get(key), f"champion {key}"
        )
        challenger_value = _optional_number(
            challenger_metrics.get(key), f"challenger {key}"
        )
        panel[key] = {
            "champion": champion_value,
            "challenger": challenger_value,
            "direction": (
                "higher_is_better" if key in HIGHER_IS_BETTER else "lower_is_better"
            ),
        }

    marginal_ir = _marginal_ir(champion_metrics, challenger_metrics)

    key_values = {
        key: (panel[key]["champion"], panel[key]["challenger"])
        for key in _KEY_METRICS
    }
    if any(champion is None or challenger is None for champion, challenger in key_values.values()):
        verdict = "INCONCLUSIVE"
        reason = "key metrics missing on at least one side"
    else:
        sharpe_h, sharpe_c = key_values["net_sharpe"]
        dd_h, dd_c = key_values["max_drawdown"]
        double_superior = sharpe_c > sharpe_h and dd_c < dd_h
        guard_regressions = []
        for key in _GUARD_KEYS:
            champion_value = panel[key]["champion"]
            challenger_value = panel[key]["challenger"]
            if champion_value is None or challenger_value is None:
                continue
            if key in HIGHER_IS_BETTER and challenger_value < champion_value:
                guard_regressions.append(key)
            elif key in LOWER_IS_BETTER and challenger_value > champion_value:
                guard_regressions.append(key)
        if double_superior and not guard_regressions:
            verdict = "CHALLENGER_BETTER"
            reason = "superior on both key metrics with no guard regression"
        elif double_superior:
            verdict = "INCONCLUSIVE"
            reason = "key-metric superiority but guard regression: " + ",".join(
                guard_regressions
            )
        elif sharpe_c <= sharpe_h and dd_c >= dd_h:
            verdict = "CHAMPION_STANDS"
            reason = "challenger not superior on either key metric"
        else:
            verdict = "INCONCLUSIVE"
            reason = "mixed key-metric picture; single-metric wins never suffice"

    return {
        "metrics": panel,
        "marginal_ir": marginal_ir,
        "verdict": verdict,
        "reason": reason,
    }


__all__ = [
    "COMPARISON_KEYS",
    "ChampionChallengerError",
    "ChampionSnapshot",
    "assert_frozen",
    "champion_snapshot_hash",
    "compare_champion_challenger",
]
