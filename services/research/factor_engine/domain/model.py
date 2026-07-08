"""Factor Engine 鈥?Domain Model (S07)

Computes and evaluates quantitative factors with full reproducibility.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Any, Callable
from uuid import uuid4
import ast
import math
import operator as _operator


class UnsafeExpressionError(ValueError):
    """Raised when a factor expression is rejected by the restricted compiler.

    Subclasses ``ValueError`` so existing callers that catch ``ValueError``
    keep working.
    """


# --------------------------------------------------------------------------- #
# Restricted, parsed factor-expression evaluator
# --------------------------------------------------------------------------- #
#
# The factor ``expression`` string is attacker-controllable, so it is NEVER
# passed to ``eval``/``exec``. Instead it is parsed to an AST and evaluated by
# a default-deny interpreter that only permits a fixed grammar of past-only
# primitives. Anything not explicitly whitelisted is rejected.

# Whitelisted, side-effect-free, past-only primitives.
_ALLOWED_FUNCS: Dict[str, Callable] = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10, "sqrt": math.sqrt,
    "abs": abs, "max": max, "min": min, "sum": sum,
    "round": round, "pow": pow,
}

# Substrings in any identifier that imply use of future / forward information
# (look-ahead bias / leakage). Matched case-insensitively.
_FORWARD_TOKENS = (
    "future", "forward", "fwd", "lookahead", "look_ahead",
    "lead", "peek", "ahead", "next_bar", "nextbar",
)

_BINOP_FUNCS = {
    ast.Add: _operator.add, ast.Sub: _operator.sub, ast.Mult: _operator.mul,
    ast.Div: _operator.truediv, ast.FloorDiv: _operator.floordiv,
    ast.Mod: _operator.mod, ast.Pow: _operator.pow,
}
_UNARYOP_FUNCS = {
    ast.UAdd: _operator.pos, ast.USub: _operator.neg, ast.Not: _operator.not_,
}
_CMPOP_FUNCS = {
    ast.Eq: _operator.eq, ast.NotEq: _operator.ne, ast.Lt: _operator.lt,
    ast.LtE: _operator.le, ast.Gt: _operator.gt, ast.GtE: _operator.ge,
}
_BOOLOP_TYPES = (ast.And, ast.Or)


def _guard_identifier(name: str) -> None:
    """Reject dunder access and forward/look-ahead references in an identifier."""
    if "__" in name:
        raise UnsafeExpressionError(
            f"dunder access ('{name}') is not allowed in factor expressions"
        )
    lowered = name.lower()
    for token in _FORWARD_TOKENS:
        if token in lowered:
            raise UnsafeExpressionError(
                f"forward/look-ahead reference ('{name}') is not allowed: "
                f"factor expressions must be past-only to avoid leakage"
            )


def _validate_node(node: ast.AST) -> None:
    """Recursively reject any AST node outside the safe grammar (default-deny)."""
    if isinstance(node, ast.Expression):
        _validate_node(node.body)

    elif isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float, str, bool)):
            raise UnsafeExpressionError(
                f"constant of type {type(node.value).__name__} is not allowed"
            )
        if isinstance(node.value, str):
            _guard_identifier(node.value)

    elif isinstance(node, ast.Name):
        _guard_identifier(node.id)
        if node.id != "data" and node.id not in _ALLOWED_FUNCS:
            raise UnsafeExpressionError(
                f"unknown name '{node.id}' is not allowed; only 'data' and "
                f"{sorted(_ALLOWED_FUNCS)} are permitted"
            )

    elif isinstance(node, ast.BinOp):
        if type(node.op) not in _BINOP_FUNCS:
            raise UnsafeExpressionError(
                f"operator {type(node.op).__name__} is not allowed"
            )
        _validate_node(node.left)
        _validate_node(node.right)

    elif isinstance(node, ast.UnaryOp):
        if type(node.op) not in _UNARYOP_FUNCS:
            raise UnsafeExpressionError(
                f"unary operator {type(node.op).__name__} is not allowed"
            )
        _validate_node(node.operand)

    elif isinstance(node, ast.BoolOp):
        if not isinstance(node.op, _BOOLOP_TYPES):
            raise UnsafeExpressionError(
                f"boolean operator {type(node.op).__name__} is not allowed"
            )
        for value in node.values:
            _validate_node(value)

    elif isinstance(node, ast.Compare):
        for op in node.ops:
            if type(op) not in _CMPOP_FUNCS:
                raise UnsafeExpressionError(
                    f"comparison {type(op).__name__} is not allowed"
                )
        _validate_node(node.left)
        for comparator in node.comparators:
            _validate_node(comparator)

    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise UnsafeExpressionError(
                "only direct calls to whitelisted functions are allowed "
                "(calls via attribute access are forbidden)"
            )
        if node.keywords:
            raise UnsafeExpressionError("keyword arguments are not allowed")
        _validate_node(node.func)
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                raise UnsafeExpressionError("starred arguments are not allowed")
            _validate_node(arg)

    elif isinstance(node, ast.Subscript):
        _validate_node(node.value)
        _validate_subscript_key(node.slice)

    elif isinstance(node, (ast.List, ast.Tuple)):
        for element in node.elts:
            _validate_node(element)

    elif isinstance(node, ast.Attribute):
        raise UnsafeExpressionError(
            f"attribute access ('.{node.attr}') is not allowed in factor "
            f"expressions"
        )

    else:
        raise UnsafeExpressionError(
            f"expression element {type(node).__name__} is not allowed"
        )


def _validate_subscript_key(node: ast.AST) -> None:
    """Validate a subscript key and reject negative (look-ahead) indices."""
    if isinstance(node, ast.Slice):
        raise UnsafeExpressionError("slice ranges are not allowed in subscripts")
    # `data[-1]` parses as UnaryOp(USub, Constant(1)) -> a negative index, which
    # is a look-ahead into the most-recent / future bar relative to evaluation.
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) \
            and isinstance(node.operand, ast.Constant) \
            and isinstance(node.operand.value, (int, float)):
        raise UnsafeExpressionError(
            "negative subscript index is not allowed: it references "
            "look-ahead / future data (leakage)"
        )
    _validate_node(node)


def _eval_node(node: ast.AST, data: Any) -> Any:
    """Interpret a pre-validated AST node against ``data``."""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, data)

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        return data if node.id == "data" else _ALLOWED_FUNCS[node.id]

    if isinstance(node, ast.BinOp):
        return _BINOP_FUNCS[type(node.op)](
            _eval_node(node.left, data), _eval_node(node.right, data)
        )

    if isinstance(node, ast.UnaryOp):
        return _UNARYOP_FUNCS[type(node.op)](_eval_node(node.operand, data))

    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result: Any = True
            for value in node.values:
                result = _eval_node(value, data)
                if not result:
                    return result
            return result
        result = False
        for value in node.values:
            result = _eval_node(value, data)
            if result:
                return result
        return result

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, data)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, data)
            if not _CMPOP_FUNCS[type(op)](left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.Call):
        func = _eval_node(node.func, data)
        args = [_eval_node(arg, data) for arg in node.args]
        return func(*args)

    if isinstance(node, ast.Subscript):
        container = _eval_node(node.value, data)
        key = _eval_node(node.slice, data)
        if isinstance(key, int) and not isinstance(key, bool) and key < 0:
            raise UnsafeExpressionError(
                "negative subscript index is not allowed: it references "
                "look-ahead / future data (leakage)"
            )
        return container[key]

    if isinstance(node, ast.List):
        return [_eval_node(element, data) for element in node.elts]

    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(element, data) for element in node.elts)

    raise UnsafeExpressionError(
        f"expression element {type(node).__name__} is not allowed"
    )


class FactorCategory(str, Enum):
    MOMENTUM = "MOMENTUM"
    MEAN_REVERSION = "MEAN_REVERSION"
    VOLATILITY = "VOLATILITY"
    VALUE = "VALUE"
    QUALITY = "QUALITY"
    SIZE = "SIZE"
    GROWTH = "GROWTH"
    TECHNICAL = "TECHNICAL"
    CROSS_SECTIONAL = "CROSS_SECTIONAL"
    CUSTOM = "CUSTOM"


@dataclass
class FactorDefinition:
    factor_id: str = field(default_factory=lambda: str(uuid4()))
    factor_name: str = ""
    version: str = "1.0.0"
    category: FactorCategory = FactorCategory.CUSTOM
    expression: str = ""
    universe: str = ""
    frequency: str = "DAILY"
    parameters: Dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    created_by: str = "system"
    created_at: int = field(default_factory=lambda: int(datetime.now(timezone.utc).timestamp()))

    def compile(self) -> Callable[[Any], Any]:
        """Compile ``self.expression`` into a safe, past-only callable.

        The expression is parsed and validated against a strict whitelist
        (see :func:`_validate_node`); it is never passed to ``eval``/``exec``.
        Forward/look-ahead references and arbitrary attribute/dunder access are
        rejected with :class:`UnsafeExpressionError`. The returned callable
        takes the factor ``data`` input and returns the computed value.

        Research-only: compiling an expression performs NO trading.
        """
        if not self.expression or not self.expression.strip():
            raise ValueError("Cannot compile an empty factor expression")
        try:
            tree = ast.parse(self.expression, mode="eval")
        except SyntaxError as exc:
            raise ValueError(
                f"Failed to parse expression '{self.expression}': {exc}"
            ) from exc

        _validate_node(tree)

        def _factor(data: Any) -> Any:
            return _eval_node(tree, data)

        return _factor


@dataclass
class FactorEvaluation:
    evaluation_id: str = field(default_factory=lambda: str(uuid4()))
    factor_id: str = ""
    evaluation_date: str = ""  # YYYY-MM-DD
    universe: str = ""
    ic_mean: Optional[float] = None
    rank_ic: Optional[float] = None
    icir: Optional[float] = None
    ic_std: Optional[float] = None
    long_short_return: Optional[float] = None
    long_short_sharpe: Optional[float] = None
    turnover: Optional[float] = None
    max_drawdown: Optional[float] = None
    ic_decay_20d: Optional[float] = None
    half_life: Optional[int] = None
    capacity_estimate: Optional[float] = None
    crowding_score: Optional[float] = None
    evaluation_window: int = 252
    computation_time_ms: int = 0
    evaluated_at: int = field(default_factory=lambda: int(datetime.now(timezone.utc).timestamp()))


@dataclass(frozen=True)
class FactorEvaluated:
    factor_id: str
    factor_name: str
    ic_mean: Optional[float]
    rank_ic: Optional[float]
    icir: Optional[float]
    sharpe: Optional[float]
    turnover: Optional[float]
    event_id: str = field(default_factory=lambda: str(uuid4()))


def compute_ic(factor_values: List[float], forward_returns: List[float]) -> Dict[str, Optional[float]]:
    n = min(len(factor_values), len(forward_returns))
    if n < 10:
        return {"ic_mean": None, "rank_ic": None, "icir": None, "ic_std": None}

    import statistics
    # Pearson IC
    mean_f = sum(factor_values[:n]) / n
    mean_r = sum(forward_returns[:n]) / n
    cov = sum((factor_values[i] - mean_f) * (forward_returns[i] - mean_r) for i in range(n)) / n
    std_f = (sum((f - mean_f) ** 2 for f in factor_values[:n]) / n) ** 0.5
    std_r = (sum((r - mean_r) ** 2 for r in forward_returns[:n]) / n) ** 0.5

    ic = cov / (std_f * std_r) if std_f > 0 and std_r > 0 else None

    # Rank IC (Spearman)
    ranked_f = _rank(factor_values[:n])
    ranked_r = _rank(forward_returns[:n])
    mean_rf = sum(ranked_f) / n
    mean_rr = sum(ranked_r) / n
    cov_r = sum((ranked_f[i] - mean_rf) * (ranked_r[i] - mean_rr) for i in range(n)) / n
    std_rf = (sum((r - mean_rf) ** 2 for r in ranked_f) / n) ** 0.5
    std_rr = (sum((r - mean_rr) ** 2 for r in ranked_r) / n) ** 0.5
    rank_ic = cov_r / (std_rf * std_rr) if std_rf > 0 and std_rr > 0 else None

    ic_std = statistics.stdev([(factor_values[i] - mean_f) * (forward_returns[i] - mean_r) / (std_f * std_r)
                                for i in range(n)]) if ic is not None else None
    icir = ic / ic_std if ic is not None and ic_std is not None and ic_std > 0 else None

    return {"ic_mean": ic, "rank_ic": rank_ic, "icir": icir, "ic_std": ic_std}


def compute_sharpe(returns: List[float]) -> Optional[float]:
    if len(returns) < 2:
        return None
    import statistics
    mean = statistics.mean(returns)
    std = statistics.stdev(returns)
    return (mean / std) * math.sqrt(252) if std > 0 else None


def compute_max_drawdown(cumulative: List[float]) -> Optional[float]:
    if not cumulative:
        return None
    peak = cumulative[0]
    max_dd = 0.0
    for v in cumulative:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _rank(values: List[float]) -> List[float]:
    sorted_pairs = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    for rank, (idx, _) in enumerate(sorted_pairs):
        ranks[idx] = float(rank) / (len(values) - 1) if len(values) > 1 else 0.5
    return ranks
