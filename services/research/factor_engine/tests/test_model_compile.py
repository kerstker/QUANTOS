"""Security + correctness tests for FactorDefinition.compile().

The compiler must behave as a *restricted, parsed* expression evaluator:
  1. Only whitelisted past-only primitives and the ``data`` input are allowed.
  2. Forward / future / look-ahead references (leakage) are rejected.
  3. Arbitrary attribute / dunder access and unknown names are rejected.
  4. Errors are clear and are a subclass of ``ValueError`` (back-compat).

These tests do NOT enable any trading; they only exercise the research-only
expression compiler.
"""
import math

import pytest

from services.research.factor_engine.domain.model import (
    FactorDefinition,
    UnsafeExpressionError,
)


def _compile(expression):
    return FactorDefinition(expression=expression).compile()


# --------------------------------------------------------------------------- #
# Safe, past-only expressions still compile and evaluate correctly
# --------------------------------------------------------------------------- #

def test_safe_arithmetic_expression_evaluates():
    fn = _compile("sqrt(abs(data)) + 2")
    assert fn(16.0) == pytest.approx(math.sqrt(16.0) + 2)


def test_safe_field_subscript_evaluates():
    fn = _compile("data['close'] * 2 - data['open']")
    assert fn({"close": 5.0, "open": 1.0}) == pytest.approx(9.0)


def test_safe_whitelisted_functions_evaluate():
    fn = _compile("max(min(data, 10), 0)")
    assert fn(42.0) == pytest.approx(10.0)
    assert fn(-3.0) == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# (3) Malicious expressions: dunder / attribute / unknown-name access rejected
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "expression",
    [
        "data.__class__",
        "data.__class__.__mro__",
        "().__class__.__bases__[0].__subclasses__()",
        "data.__class__.__init__.__globals__",
    ],
)
def test_rejects_dunder_and_attribute_access(expression):
    with pytest.raises(UnsafeExpressionError):
        _compile(expression)


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os')",
        "__import__('os').system('echo pwned')",
        "eval('1+1')",
        "exec('x=1')",
        "open('/etc/passwd')",
        "os.system('echo pwned')",
    ],
)
def test_rejects_imports_and_unknown_callables(expression):
    with pytest.raises(UnsafeExpressionError):
        _compile(expression)


def test_rejection_is_value_error_subclass():
    # Back-compat: callers catching ValueError keep working.
    assert issubclass(UnsafeExpressionError, ValueError)
    with pytest.raises(ValueError):
        _compile("data.__class__")


def test_error_message_is_clear_for_attribute_access():
    with pytest.raises(UnsafeExpressionError) as exc:
        _compile("data.__class__")
    msg = str(exc.value).lower()
    assert "attribute" in msg or "not allowed" in msg


# --------------------------------------------------------------------------- #
# (2) Leakage / forward-reference guard
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "expression",
    [
        "data['forward_return']",
        "data['future_close']",
        "future(data)",
        "lead(data, 1)",
        "ts_future_mean(data)",
        "lookahead(data)",
    ],
)
def test_rejects_forward_looking_references(expression):
    with pytest.raises(UnsafeExpressionError):
        _compile(expression)


def test_rejects_negative_subscript_as_lookahead():
    with pytest.raises(UnsafeExpressionError):
        _compile("data[-1]")


def test_forward_reference_error_mentions_lookahead():
    with pytest.raises(UnsafeExpressionError) as exc:
        _compile("data['forward_return']")
    msg = str(exc.value).lower()
    assert "forward" in msg or "look" in msg or "leak" in msg


# --------------------------------------------------------------------------- #
# Malformed expressions raise a clear ValueError too
# --------------------------------------------------------------------------- #

def test_syntax_error_raises_value_error():
    with pytest.raises(ValueError):
        _compile("data +")
