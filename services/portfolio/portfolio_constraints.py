"""Unified, fail-closed portfolio constraint results.

The portfolio package already contains several useful pure diagnostics:
``firm_aggregation``, ``firm_gate`` and ``crowding_gate``.  They predate a
single composition contract, however, and callers had to interpret different
return shapes (and the optimizer's ``InfeasibleConstraints`` exception).

This module adds that contract without importing any execution or broker
surface.  Each check is deterministic and receives all evidence from its
caller.  ``PortfolioConstraintInputs`` and ``enforce_portfolio_constraints``
are the runtime adapter used by portfolio optimizers and constructors; missing
evidence/configuration is ``PENDING``; malformed evidence or a breached
configured limit is ``BLOCK``.  A warning is retained for the crowding
diagnostic because a warning is not the same thing as an approval.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from .crowding_gate import evaluate_crowding_gate
from .firm_aggregation import aggregate_firm_books


ConstraintStatus = Literal["PASS", "WARN", "BLOCK", "PENDING"]
_STATUS_ORDER: dict[str, int] = {"PASS": 0, "WARN": 1, "PENDING": 2, "BLOCK": 3}
_EPS = 1e-12
_PROVENANCE = "REAL_PORTFOLIO_CONSTRAINTS"


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


@dataclass(frozen=True)
class ConstraintCheck:
    """One named constraint evaluation.

    ``metrics`` intentionally permits strings for identifiers such as the
    capacity bottleneck.  It is still a plain dictionary so the result can be
    persisted by a caller without importing a serialization framework.
    """

    name: str
    status: ConstraintStatus
    reasons: tuple[str, ...] = ()
    metrics: dict[str, object] = field(default_factory=dict)
    provenance: str = _PROVENANCE

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class PortfolioConstraintResult:
    """Composed result for all requested portfolio constraints."""

    status: ConstraintStatus
    checks: tuple[ConstraintCheck, ...]
    reasons: tuple[str, ...] = ()
    metrics: dict[str, object] = field(default_factory=dict)
    provenance: str = _PROVENANCE

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "checks": [check.as_dict() for check in self.checks],
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class PortfolioConstraintInputs:
    """Evidence/configuration carried into a portfolio runtime gate.

    The existing evaluator remains a pure function.  This value object is the
    small adapter used by optimizers and constructors: weights are supplied by
    the runtime after optimization, while all independent evidence and limits
    are supplied by the caller.  An explicitly supplied object is a request to
    enforce the checks; an empty object therefore evaluates to ``PENDING`` and
    cannot accidentally approve a plan.
    """

    scenarios: Sequence[Sequence[float]] | None = None
    scenario_symbols: Sequence[str] | None = None
    tail_budget: float | None = None
    tail_alpha: float = 0.95
    book_positions: Mapping[str, Mapping[str, float]] | None = None
    book_notionals: Mapping[str, float] | None = None
    max_firm_gross: float | None = None
    max_firm_net: float | None = None
    max_symbol_weight: float | None = None
    position_notional_by_symbol: Mapping[str, float] | None = None
    capacity_by_symbol: Mapping[str, float] | None = None
    max_capacity_usage: float | None = None
    crowding_by_symbol: Mapping[str, float | None] | None = None
    crowding_threshold: float | None = None
    crowding_warn_threshold: float = 0.45
    crowding_min_coverage: float = 0.80
    crowding_max_name_weight: float = 0.25
    crowding_average_warn_only: bool = False

    def evaluate(self, weights: Mapping[str, float]) -> "PortfolioConstraintResult":
        """Evaluate this request against the runtime-produced ``weights``.

        ``weights`` is passed only to checks that need it.  This distinction is
        important: a caller asking only for firm exposure should not implicitly
        request a tail check merely because an optimizer produced weights.
        """
        kwargs: dict[str, object] = {}
        if self.scenarios is not None or self.tail_budget is not None:
            kwargs.update(
                weights=weights,
                scenarios=self.scenarios,
                scenario_symbols=self.scenario_symbols,
                tail_budget=self.tail_budget,
                tail_alpha=self.tail_alpha,
            )
        if (
            self.book_positions is not None
            or self.book_notionals is not None
            or self.max_firm_gross is not None
            or self.max_firm_net is not None
            or self.max_symbol_weight is not None
        ):
            kwargs.update(
                book_positions=self.book_positions,
                book_notionals=self.book_notionals,
                max_firm_gross=self.max_firm_gross,
                max_firm_net=self.max_firm_net,
                max_symbol_weight=self.max_symbol_weight,
            )
        if (
            self.position_notional_by_symbol is not None
            or self.capacity_by_symbol is not None
            or self.max_capacity_usage is not None
        ):
            kwargs.update(
                position_notional_by_symbol=self.position_notional_by_symbol,
                capacity_by_symbol=self.capacity_by_symbol,
                max_capacity_usage=self.max_capacity_usage,
            )
        if self.crowding_by_symbol is not None or self.crowding_threshold is not None:
            kwargs.update(
                weights=weights,
                crowding_by_symbol=self.crowding_by_symbol,
                crowding_threshold=self.crowding_threshold,
                crowding_warn_threshold=self.crowding_warn_threshold,
                crowding_min_coverage=self.crowding_min_coverage,
                crowding_max_name_weight=self.crowding_max_name_weight,
                crowding_average_warn_only=self.crowding_average_warn_only,
            )
        return evaluate_portfolio_constraints(**kwargs)


class PortfolioConstraintViolation(ValueError):
    """Raised when an explicitly requested portfolio gate is not PASS."""

    def __init__(self, result: "PortfolioConstraintResult") -> None:
        self.result = result
        reasons = ",".join(result.reasons) or "no_reason"
        super().__init__(f"portfolio constraints {result.status}: {reasons}")


def enforce_portfolio_constraints(
    weights: Mapping[str, float],
    inputs: PortfolioConstraintInputs | None,
) -> PortfolioConstraintResult | None:
    """Fail closed at a runtime portfolio boundary.

    No argument preserves legacy research callers.  Once inputs are supplied,
    only a complete ``PASS`` is accepted; ``WARN`` is deliberately not treated
    as approval because the unified result contract says warning is not a gate
    grant.
    """
    if inputs is None:
        return None
    result = inputs.evaluate(weights)
    if result.status != "PASS":
        raise PortfolioConstraintViolation(result)
    return result


def _pending(name: str, reason: str, *, metrics: Mapping[str, object] | None = None,
             provenance: str = _PROVENANCE) -> ConstraintCheck:
    return ConstraintCheck(name, "PENDING", (reason,), dict(metrics or {}), provenance)


def _blocked(name: str, reason: str, *, metrics: Mapping[str, object] | None = None,
             provenance: str = _PROVENANCE) -> ConstraintCheck:
    return ConstraintCheck(name, "BLOCK", (reason,), dict(metrics or {}), provenance)


def _passed(name: str, *, reasons: tuple[str, ...] = (),
            metrics: Mapping[str, object] | None = None,
            provenance: str = _PROVENANCE) -> ConstraintCheck:
    return ConstraintCheck(name, "PASS", reasons, dict(metrics or {}), provenance)


def evaluate_tail_budget(
    weights: Mapping[str, float] | None,
    scenarios: Sequence[Sequence[float]] | None,
    *,
    scenario_symbols: Sequence[str] | None = None,
    max_cvar_loss: float | None,
    alpha: float = 0.95,
) -> ConstraintCheck:
    """Evaluate an empirical CVaR loss budget over caller-supplied scenarios.

    ``scenarios`` contains return rows and ``weights`` are the corresponding
    portfolio weights.  The check uses the worst ``ceil((1-alpha) * T)``
    portfolio losses, with at least one scenario.  It requires at least two
    observations so a one-row fixture cannot masquerade as tail evidence.
    """

    name = "tail_budget"
    if max_cvar_loss is None or weights is None or scenarios is None:
        missing = "tail_budget_not_configured" if max_cvar_loss is None else "tail_evidence_missing"
        return _pending(name, missing)
    if not _finite(max_cvar_loss) or float(max_cvar_loss) < 0.0:
        return _blocked(name, "invalid_tail_budget")
    if not _finite(alpha) or not 0.0 < float(alpha) < 1.0:
        return _blocked(name, "invalid_tail_alpha")
    if not isinstance(weights, Mapping) or not weights:
        return _blocked(name, "empty_or_invalid_weights")

    if scenario_symbols is None:
        return _pending(name, "scenario_symbols_missing")
    if isinstance(scenario_symbols, (str, bytes)) or not isinstance(
        scenario_symbols, Sequence
    ):
        return _blocked(name, "invalid_scenario_symbols")
    symbols = list(scenario_symbols)
    if (
        len(symbols) != len(weights)
        or len(set(symbols)) != len(symbols)
        or any(not isinstance(symbol, str) or not symbol for symbol in symbols)
        or set(symbols) != set(weights)
    ):
        return _blocked(name, "scenario_symbol_mismatch")
    for symbol in symbols:
        if not _finite(weights[symbol]):
            return _blocked(name, f"non_finite_weight:{symbol}")

    if isinstance(scenarios, (str, bytes)) or not isinstance(scenarios, Sequence):
        return _blocked(name, "invalid_scenarios")
    if len(scenarios) < 2:
        return _blocked(name, "insufficient_scenarios")

    losses: list[float] = []
    for row_idx, row in enumerate(scenarios):
        if isinstance(row, (str, bytes)) or not isinstance(row, Sequence):
            return _blocked(name, f"invalid_scenario_row:{row_idx}")
        if len(row) != len(symbols):
            return _blocked(name, f"scenario_width_mismatch:{row_idx}")
        for col_idx, value in enumerate(row):
            if not _finite(value):
                return _blocked(name, f"non_finite_scenario:{row_idx}:{col_idx}")
        losses.append(-sum(float(row[i]) * float(weights[symbols[i]]) for i in range(len(symbols))))

    ordered = sorted(losses, reverse=True)
    tail_count = max(1, math.ceil((1.0 - float(alpha)) * len(ordered)))
    tail = ordered[:tail_count]
    cvar_loss = max(0.0, sum(tail) / tail_count)
    var_loss = max(0.0, tail[-1])
    metrics = {
        "cvar_loss": cvar_loss,
        "var_loss": var_loss,
        "tail_budget": float(max_cvar_loss),
        "alpha": float(alpha),
        "tail_count": float(tail_count),
        "n_scenarios": float(len(ordered)),
    }
    if cvar_loss > float(max_cvar_loss) + _EPS:
        return _blocked(
            name,
            f"tail_budget_exceeded:{cvar_loss:.10f}>{float(max_cvar_loss):.10f}",
            metrics=metrics,
        )
    return _passed(name, reasons=("tail_budget_ok",), metrics=metrics)


def evaluate_firm_constraints(
    book_positions: Mapping[str, Mapping[str, float]] | None,
    book_notionals: Mapping[str, float] | None = None,
    *,
    max_gross: float | None = None,
    max_net: float | None = None,
    max_symbol_weight: float | None = None,
) -> ConstraintCheck:
    """Aggregate books and enforce firm gross/net/symbol concentration limits."""

    name = "firm_exposure"
    limits = {
        "max_gross": max_gross,
        "max_net": max_net,
        "max_symbol_weight": max_symbol_weight,
    }
    if not any(value is not None for value in limits.values()):
        return _pending(name, "firm_limits_not_configured")
    for key, value in limits.items():
        if value is None:
            continue
        if not _finite(value) or float(value) < 0.0:
            return _blocked(name, f"invalid_{key}")
        if key == "max_symbol_weight" and float(value) > 1.0:
            return _blocked(name, "invalid_max_symbol_weight")
    if book_positions is None:
        return _pending(name, "firm_evidence_missing")
    if not isinstance(book_positions, Mapping) or not book_positions:
        return _blocked(name, "empty_or_invalid_book_positions")
    if book_notionals is not None:
        if not isinstance(book_notionals, Mapping):
            return _blocked(name, "invalid_book_notionals")
        missing_notionals = sorted(set(book_positions) - set(book_notionals))
        if missing_notionals:
            return _pending(name, f"firm_notional_evidence_missing:{missing_notionals[0]}")

    view = aggregate_firm_books(book_positions, book_notionals)
    if view.get("status") != "AGGREGATED":
        reasons = view.get("reasons", ("firm_aggregation_failed",))
        reason = str(tuple(reasons)[0]) if reasons else "firm_aggregation_failed"
        return _blocked(name, reason, provenance="REAL_FIRM_AGGREGATION")

    firm_gross = view.get("firm_gross")
    firm_net = view.get("firm_net")
    if not _finite(firm_gross) or not _finite(firm_net):
        return _blocked(name, "non_finite_firm_aggregate", provenance="REAL_FIRM_AGGREGATION")
    firm_gross = float(firm_gross)
    firm_net = float(firm_net)
    top_positions = view.get("top_firm_positions", ())
    max_symbol = 0.0
    if firm_gross > _EPS:
        try:
            max_symbol = max(
                (abs(float(item["firm_position"])) / firm_gross for item in top_positions),
                default=0.0,
            )
        except (KeyError, TypeError, ValueError):
            return _blocked(name, "invalid_firm_symbol_aggregate", provenance="REAL_FIRM_AGGREGATION")

    metrics = {
        "firm_gross": firm_gross,
        "firm_net": firm_net,
        "max_symbol_weight": max_symbol,
        "n_books": float(view.get("n_books", 0)),
        "n_overlaps": float(view.get("n_overlaps", 0)),
    }
    reasons: list[str] = []
    if max_gross is not None and firm_gross > float(max_gross) + _EPS:
        reasons.append(f"firm_gross_exceeded:{firm_gross:.10f}>{float(max_gross):.10f}")
    if max_net is not None and abs(firm_net) > float(max_net) + _EPS:
        reasons.append(f"firm_net_exceeded:{firm_net:.10f}>{float(max_net):.10f}")
    if max_symbol_weight is not None and max_symbol > float(max_symbol_weight) + _EPS:
        reasons.append(
            f"symbol_weight_exceeded:{max_symbol:.10f}>{float(max_symbol_weight):.10f}"
        )
    if reasons:
        return ConstraintCheck(
            name=name,
            status="BLOCK",
            reasons=tuple(reasons),
            metrics=metrics,
            provenance="REAL_FIRM_AGGREGATION",
        )
    return _passed(
        name,
        reasons=("firm_limits_ok",),
        metrics=metrics,
        provenance="REAL_FIRM_AGGREGATION",
    )


def evaluate_capacity_constraint(
    position_notional_by_symbol: Mapping[str, float] | None,
    capacity_by_symbol: Mapping[str, float] | None,
    *,
    max_usage: float | None,
) -> ConstraintCheck:
    """Enforce ``abs(position_notional) / capacity`` for every held symbol."""

    name = "capacity"
    if max_usage is None:
        return _pending(name, "capacity_limit_not_configured")
    if not _finite(max_usage) or float(max_usage) < 0.0:
        return _blocked(name, "invalid_max_capacity_usage")
    if position_notional_by_symbol is None or capacity_by_symbol is None:
        return _pending(name, "capacity_evidence_missing")
    if not isinstance(position_notional_by_symbol, Mapping) or not position_notional_by_symbol:
        return _blocked(name, "empty_or_invalid_positions")
    if not isinstance(capacity_by_symbol, Mapping) or not capacity_by_symbol:
        return _blocked(name, "empty_or_invalid_capacity")

    usage_by_symbol: dict[str, float] = {}
    for symbol in sorted(position_notional_by_symbol):
        position = position_notional_by_symbol[symbol]
        if not _finite(position):
            return _blocked(name, f"non_finite_position:{symbol}")
        if symbol not in capacity_by_symbol:
            return _blocked(name, f"missing_capacity:{symbol}")
        capacity = capacity_by_symbol[symbol]
        if not _finite(capacity) or float(capacity) <= 0.0:
            return _blocked(name, f"invalid_capacity:{symbol}")
        usage_by_symbol[symbol] = abs(float(position)) / float(capacity)

    bottleneck = max(usage_by_symbol, key=lambda symbol: (usage_by_symbol[symbol], symbol))
    max_observed = usage_by_symbol[bottleneck]
    metrics = {
        "max_usage": max_observed,
        "capacity_usage": max_observed,
        "bottleneck_symbol": bottleneck,
        "max_capacity_usage": float(max_usage),
        "n_symbols": float(len(usage_by_symbol)),
    }
    if max_observed > float(max_usage) + _EPS:
        return _blocked(
            name,
            f"capacity_usage_exceeded:{max_observed:.10f}>{float(max_usage):.10f}",
            metrics=metrics,
        )
    return _passed(name, reasons=("capacity_usage_ok",), metrics=metrics)


def evaluate_crowding_constraint(
    weights: Mapping[str, float] | None,
    crowding_by_symbol: Mapping[str, float | None] | None,
    *,
    threshold: float | None,
    warn_threshold: float = 0.45,
    min_coverage: float = 0.80,
    max_name_weight: float = 0.25,
    average_warn_only: bool = False,
) -> ConstraintCheck:
    """Adapt the existing crowding gate to the unified result contract."""

    name = "crowding"
    if threshold is None:
        return _pending(name, "crowding_limit_not_configured")
    if weights is None or crowding_by_symbol is None:
        return _pending(name, "crowding_evidence_missing")
    try:
        decision = evaluate_crowding_gate(
            weights,
            crowding_by_symbol,
            threshold=float(threshold),
            warn_threshold=float(warn_threshold),
            min_coverage=float(min_coverage),
            max_name_weight=float(max_name_weight),
            average_warn_only=bool(average_warn_only),
        )
    except (TypeError, ValueError, OverflowError) as exc:
        return _blocked(name, f"invalid_crowding_input:{type(exc).__name__}")
    metrics: dict[str, object] = {
        "threshold": decision.threshold,
        "score": decision.score,
        "n_exposures": float(len(decision.exposures)),
    }
    reasons = tuple(decision.reasons)
    if decision.status == "PASS":
        return _passed(name, reasons=reasons or ("crowding_ok",), metrics=metrics,
                       provenance="REAL_CROWDING_GATE")
    if decision.status == "WARN":
        return ConstraintCheck(name, "WARN", reasons or ("crowding_warn",), metrics,
                               "REAL_CROWDING_GATE")
    return ConstraintCheck(name, "BLOCK", reasons or ("crowding_blocked",), metrics,
                           "REAL_CROWDING_GATE")


def _compose(checks: Sequence[ConstraintCheck]) -> PortfolioConstraintResult:
    ordered_checks = tuple(checks)
    if not ordered_checks:
        return PortfolioConstraintResult(
            status="PENDING",
            checks=(),
            reasons=("no_constraints_configured",),
            metrics={},
        )
    status = max((check.status for check in ordered_checks), key=lambda value: _STATUS_ORDER[value])
    reasons = tuple(
        f"{check.name}:{reason}"
        for check in ordered_checks
        for reason in check.reasons
    )
    metrics: dict[str, object] = {}
    for check in ordered_checks:
        for key, value in check.metrics.items():
            metrics[f"{check.name}.{key}"] = value
    metrics["n_checks"] = float(len(ordered_checks))
    return PortfolioConstraintResult(status, ordered_checks, reasons, metrics)


def evaluate_portfolio_constraints(
    *,
    weights: Mapping[str, float] | None = None,
    scenarios: Sequence[Sequence[float]] | None = None,
    scenario_symbols: Sequence[str] | None = None,
    tail_budget: float | None = None,
    tail_alpha: float = 0.95,
    book_positions: Mapping[str, Mapping[str, float]] | None = None,
    book_notionals: Mapping[str, float] | None = None,
    max_firm_gross: float | None = None,
    max_firm_net: float | None = None,
    max_symbol_weight: float | None = None,
    position_notional_by_symbol: Mapping[str, float] | None = None,
    capacity_by_symbol: Mapping[str, float] | None = None,
    max_capacity_usage: float | None = None,
    crowding_by_symbol: Mapping[str, float | None] | None = None,
    crowding_threshold: float | None = None,
    crowding_warn_threshold: float = 0.45,
    crowding_min_coverage: float = 0.80,
    crowding_max_name_weight: float = 0.25,
    crowding_average_warn_only: bool = False,
) -> PortfolioConstraintResult:
    """Evaluate every constraint for which the caller supplied evidence/config.

    A shared ``weights`` map may feed both tail and crowding checks; it does
    not itself request either check.  A check is requested only when at least
    one of its configuration/evidence arguments is present, making a bare
    call return an explicit ``PENDING`` result instead of silently passing.
    """

    checks: list[ConstraintCheck] = []
    # ``weights`` is shared by tail and crowding checks.  It is not, by
    # itself, evidence/configuration for a tail budget; otherwise a
    # crowding-only runtime gate would accidentally become PENDING on an
    # unconfigured tail check.
    if scenarios is not None or tail_budget is not None:
        checks.append(
            evaluate_tail_budget(
                weights,
                scenarios,
                scenario_symbols=scenario_symbols,
                max_cvar_loss=tail_budget,
                alpha=tail_alpha,
            )
        )
    if (
        book_positions is not None
        or book_notionals is not None
        or max_firm_gross is not None
        or max_firm_net is not None
        or max_symbol_weight is not None
    ):
        checks.append(
            evaluate_firm_constraints(
                book_positions,
                book_notionals,
                max_gross=max_firm_gross,
                max_net=max_firm_net,
                max_symbol_weight=max_symbol_weight,
            )
        )
    if (
        position_notional_by_symbol is not None
        or capacity_by_symbol is not None
        or max_capacity_usage is not None
    ):
        checks.append(
            evaluate_capacity_constraint(
                position_notional_by_symbol,
                capacity_by_symbol,
                max_usage=max_capacity_usage,
            )
        )
    if crowding_by_symbol is not None or crowding_threshold is not None:
        checks.append(
            evaluate_crowding_constraint(
                weights,
                crowding_by_symbol,
                threshold=crowding_threshold,
                warn_threshold=crowding_warn_threshold,
                min_coverage=crowding_min_coverage,
                max_name_weight=crowding_max_name_weight,
                average_warn_only=crowding_average_warn_only,
            )
        )
    return _compose(checks)


__all__ = [
    "ConstraintCheck",
    "ConstraintStatus",
    "PortfolioConstraintResult",
    "PortfolioConstraintInputs",
    "PortfolioConstraintViolation",
    "evaluate_capacity_constraint",
    "evaluate_crowding_constraint",
    "evaluate_firm_constraints",
    "evaluate_portfolio_constraints",
    "evaluate_tail_budget",
    "enforce_portfolio_constraints",
]


