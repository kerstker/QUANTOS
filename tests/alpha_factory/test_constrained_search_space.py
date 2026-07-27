"""Constrained search space (727 §8.2): violation codes + bounded enumeration."""

from __future__ import annotations

import pytest

from services.alpha_factory.constrained_search_space import (
    DEFAULT_ALLOWED_OPS,
    SearchSpaceConstraints,
    SearchSpaceError,
    check_expression,
    enumerate_space,
)
from services.research import alpha_expression as ax


def _constraints(**overrides):
    base = dict(allowed_fields=("close", "funding", "global_ls"), max_window=60)
    base.update(overrides)
    return SearchSpaceConstraints(**base)


def test_legal_expression_passes() -> None:
    expr = ("cs_rank", ("ts_mean", ("neg", ("field", "close")), 10))
    assert check_expression(expr, _constraints()) == ()


def test_field_not_allowed() -> None:
    expr = ("cs_rank", ("field", "volume"))
    assert check_expression(expr, _constraints()) == ("SPACE_FIELD_NOT_ALLOWED",)


def test_op_not_allowed() -> None:
    constraints = _constraints(
        allowed_ops=tuple(op for op in DEFAULT_ALLOWED_OPS if op != "ts_mean")
    )
    expr = ("cs_rank", ("ts_mean", ("field", "close"), 10))
    assert check_expression(expr, constraints) == ("SPACE_OP_NOT_ALLOWED",)


def test_too_deep() -> None:
    expr = ("field", "close")
    for _ in range(5):
        expr = ("neg", expr)
    expr = ("cs_rank", expr)  # depth 7 under an economic root
    assert check_expression(expr, _constraints()) == ("SPACE_TOO_DEEP",)
    assert check_expression(expr, _constraints(max_depth=7)) == ()


def test_window_out_of_range_both_sides() -> None:
    too_wide = ("cs_rank", ("ts_mean", ("field", "close"), 500))
    too_narrow = ("cs_rank", ("ts_mean", ("field", "close"), 1))
    assert check_expression(too_wide, _constraints()) == ("SPACE_WINDOW_OUT_OF_RANGE",)
    assert check_expression(too_narrow, _constraints()) == (
        "SPACE_WINDOW_OUT_OF_RANGE",
    )
    # boundary values are in-range
    edge = ("cs_rank", ("ts_mean", ("field", "close"), 60))
    assert check_expression(edge, _constraints()) == ()
    low = ("cs_rank", ("ts_mean", ("field", "close"), 2))
    assert check_expression(low, _constraints()) == ()


def test_root_not_economic() -> None:
    expr = ("ts_mean", ("field", "close"), 10)  # ts root, no cross-sectional meaning
    assert check_expression(expr, _constraints()) == ("SPACE_ROOT_NOT_ECONOMIC",)
    assert check_expression(expr, _constraints(require_economic_root=False)) == ()


def test_constraints_construction_is_fail_closed() -> None:
    with pytest.raises(SearchSpaceError, match="unknown DSL ops"):
        _constraints(allowed_ops=("not_an_op",))
    with pytest.raises(SearchSpaceError):
        _constraints(allowed_fields=())
    with pytest.raises(SearchSpaceError):
        _constraints(min_window=10, max_window=5)
    with pytest.raises(TypeError):
        SearchSpaceConstraints(allowed_fields=("close",))  # max_window required


def test_enumeration_is_deterministic_bounded_and_in_space() -> None:
    constraints = _constraints()
    first = list(enumerate_space(constraints, max_candidates=50))
    second = list(enumerate_space(constraints, max_candidates=50))
    assert first == second
    assert len(first) == 50  # hard truncation
    for expr in first:
        assert check_expression(expr, constraints) == ()
        assert expr[0] in {"cs_rank", "cs_demean", "cs_zscore", "cs_scale",
                           "cs_winsorize", "cs_residualize"}  # cs roots only
    identities = [ax.alpha_id(ax.canonicalize(e)) for e in first]
    assert len(set(identities)) == len(identities)


def test_enumeration_full_size_and_window_ladder() -> None:
    constraints = SearchSpaceConstraints(
        allowed_fields=("close",), max_window=8, min_window=2,
        allowed_ops=("ts_mean", "cs_rank"),
    )
    full = list(enumerate_space(constraints, max_candidates=1000))
    # ladder 2,4,8; shape (cs_rank (ts_mean field w)) only (economic root)
    assert len(full) == 3
    windows = sorted(expr[1][2] for expr in full)
    assert windows == [2, 4, 8]
    with pytest.raises(SearchSpaceError):
        list(enumerate_space(constraints, max_candidates=0))
