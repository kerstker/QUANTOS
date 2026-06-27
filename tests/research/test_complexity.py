"""Operator-weighted structural complexity for the alpha DSL — the parsimony signal the gp_tree
search lacked at the engine level. Used as Alpha-asset metadata and as a dedup/parsimony tie-break:
cheaper, shallower expressions are preferred when two alphas are otherwise equal. Deterministic."""
from services.research import complexity as cx


def test_leaf_costs():
    assert cx.complexity(("field", "close")) == 1.0
    assert cx.complexity(("const", 2.0)) == 0.5


def test_unary_wrap_adds_operator_cost():
    base = cx.complexity(("field", "close"))
    wrapped = cx.complexity(("cs_rank", ("field", "close")))
    assert wrapped > base                                  # wrapping costs more
    assert wrapped == cx._op_cost("cs_rank") + base


def test_pairwise_costs_more_than_elementwise():
    leaves = (("field", "close"), ("field", "volume"))
    cheap = cx.complexity(("add", *leaves))
    pricey = cx.complexity(("cs_residualize", *leaves))
    assert pricey > cheap                                  # a regression is structurally heavier than +


def test_complexity_is_monotonic_in_nesting():
    a = ("cs_rank", ("field", "close"))
    b = ("cs_rank", ("ts_mean", ("field", "close"), 5))
    c = ("cs_rank", ("ts_mean", ("sqrt", ("field", "close")), 5))
    assert cx.complexity(a) < cx.complexity(b) < cx.complexity(c)


def test_node_count_and_depth():
    e = ("cs_rank", ("add", ("field", "close"), ("ts_mean", ("field", "volume"), 5)))
    assert cx.node_count(e) == 5        # cs_rank, add, field, ts_mean, field
    assert cx.depth(e) == 4             # cs_rank -> add -> ts_mean -> field
    assert cx.depth(("field", "close")) == 1


def test_deterministic():
    e = ("cs_zscore", ("ts_beta", ("field", "close"), ("field", "volume"), 7))
    assert [cx.complexity(e) for _ in range(5)] == [cx.complexity(e)] * 5


def test_unknown_operator_fails_closed():
    # review fix: an unknown head means the cost table drifted from the engine -> raise, don't silently
    # return a fallback cost (fail-closed, matching evaluate() and dsl_checker()).
    raised = False
    try:
        cx.complexity(("ts_lead", ("field", "close"), 1))
    except ValueError:
        raised = True
    assert raised
