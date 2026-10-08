"""Versioned Decimal arithmetic shared by the ledger and offline verifiers.

No execution or account state is imported. The reviewed V2 implementation is
shared rather than duplicated; callers retain the original arithmetic policy.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, ROUND_HALF_EVEN, localcontext
from functools import wraps
from typing import Callable, Iterator, ParamSpec, TypeVar

ZERO = Decimal("0")
DECIMAL_CONTEXT_VERSION = "quantos.linear-perpetual-decimal-context.v1"
_DECIMAL_CONTEXT_PRECISION = 50
_DECIMAL_CONTEXT_EMIN = -999_999
_DECIMAL_CONTEXT_EMAX = 999_999
_P = ParamSpec("_P")
_R = TypeVar("_R")


def _new_decimal_context() -> Context:
    """Construct, rather than share, the reviewed arithmetic context."""
    return Context(
        prec=_DECIMAL_CONTEXT_PRECISION,
        rounding=ROUND_HALF_EVEN,
        Emin=_DECIMAL_CONTEXT_EMIN,
        Emax=_DECIMAL_CONTEXT_EMAX,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


@contextmanager
def ledger_decimal_context() -> Iterator[None]:
    """Install the versioned ledger arithmetic context without exposing it.

    A fresh private ``Context`` is used for every entry, so neither caller
    mutation nor process-global decimal settings can alter a V2 calculation.
    The caller's context is restored unchanged when the scope exits.
    """
    with localcontext(_new_decimal_context()):
        yield


def _reviewed_decimal(function: Callable[_P, _R]) -> Callable[_P, _R]:
    @wraps(function)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with ledger_decimal_context():
            return function(*args, **kwargs)

    return wrapped


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{field} must be Decimal, int, or canonical decimal string")
    try:
        result = value if isinstance(value, Decimal) else Decimal(value)  # type: ignore[arg-type]
    except Exception as exc:
        raise TypeError(f"{field} is not a decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


@_reviewed_decimal
def canonical_decimal(value: Decimal) -> str:
    """Return one non-exponent, trailing-zero-free Decimal representation."""
    normalized = _decimal(value, "value")
    if normalized == ZERO:
        return "0"
    rendered = format(normalized, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


__all__ = ["DECIMAL_CONTEXT_VERSION", "canonical_decimal", "ledger_decimal_context"]
