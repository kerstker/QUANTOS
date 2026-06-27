"""A Type + Dimension checker for the cross-sectional alpha DSL: catch malformed trees STATICALLY
(known head, correct arity, scalar-slot types, matrix-slot positions) instead of as a runtime
IndexError inside evaluate(). Since every panel matrix is (T x N) and every operator is shape-
preserving, the dimension check reduces to operand-POSITION validation (matrix-vs-scalar), which
this performs. The signature table is derived from the engine registries, so it can never drift
from the operators evaluate() actually dispatches."""
import services.research.alpha_expression as ax
from services.research import dsl_checker as dc
from services.research import cs_genome as g


def test_valid_expressions_pass():
    good = [
        ("field", "close"),
        ("const", 1.5),
        ("cs_rank", ("ts_mean", ("field", "close"), 5)),
        ("cs_winsorize", ("field", "volume")),
        ("cs_residualize", ("field", "close"), ("field", "volume")),
        ("cs_zscore", ("ts_beta", ("field", "close"), ("field", "volume"), 7)),
        ("cs_scale", ("signed_power", ("field", "close"), 0.5)),
        ("cs_rank", ("add", ("field", "close"), ("neg", ("field", "volume")))),
        ("cs_rank", ("sqrt", ("ts_cov", ("field", "close"), ("field", "volume"), 5))),
    ]
    for e in good:
        r = dc.check_expression(e)
        assert r.ok, (ax.serialize(e), r.errors)


def test_unknown_operator_rejected():
    r = dc.check_expression(("cs_rank", ("ts_lead", ("field", "close"), 1)))
    assert not r.ok and any("unknown operator" in m and "ts_lead" in m for m in r.errors)


def test_wrong_arity_rejected():
    assert not dc.check_expression(("cs_rank",)).ok                       # missing child
    assert not dc.check_expression(("cs_rank", ("field", "close"), ("field", "volume"))).ok  # extra
    assert not dc.check_expression(("ts_mean", ("field", "close"))).ok    # missing window
    assert not dc.check_expression(("cs_residualize", ("field", "close"))).ok  # missing 2nd operand


def test_bad_scalar_types_rejected():
    assert not dc.check_expression(("ts_mean", ("field", "close"), "5")).ok      # window not int
    assert not dc.check_expression(("ts_mean", ("field", "close"), 0)).ok        # window < 1
    assert not dc.check_expression(("ts_mean", ("field", "close"), True)).ok     # bool is not a window
    assert not dc.check_expression(("field", 123)).ok                            # field name not str
    assert not dc.check_expression(("const", "x")).ok                            # const not a number
    assert not dc.check_expression(("signed_power", ("field", "close"), "p")).ok  # exponent not number


def test_matrix_slot_given_scalar_rejected():
    # operand in a matrix position must be a sub-expression, not a bare scalar
    r = dc.check_expression(("add", ("field", "close"), 5))
    assert not r.ok and any("must be an expression" in m for m in r.errors)


def test_error_path_points_at_offending_node():
    r = dc.check_expression(("cs_rank", ("ts_mean", ("field", "close"), 0)))
    assert not r.ok
    assert any("root.1.2" in m for m in r.errors)            # the window arg, two levels down


def test_validate_expression_raises_on_invalid_and_passes_valid():
    assert dc.validate_expression(("cs_rank", ("field", "close"))) is None
    raised = False
    try:
        dc.validate_expression(("cs_rank",))
    except ValueError as e:
        raised = True
        assert "invalid alpha expression" in str(e)
    assert raised


def test_genome_products_all_typecheck():
    # every machine-generated genome alpha must pass the static checker (engine + genome agree)
    fields, windows = ["close", "volume", "funding"], [3, 5, 7, 14]
    for s in g.seed_alphas(fields, windows):
        assert dc.check_expression(s).ok, ax.serialize(s)
        for step in range(4):
            assert dc.check_expression(g.mutate(s, fields, windows, step)).ok


def test_no_rng_or_wallclock_import():
    import inspect
    src = inspect.getsource(dc)
    assert "import random" not in src and "import time" not in src and "Random" not in src
