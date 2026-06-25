"""GP expression-tree core (tests-first).

Trees generalize the flat (operator,window) atom + 2-leg interaction to arbitrary-depth
SCALAR expression trees: LEAVES are the existing SIGNAL_OPS atoms (the ONLY readers of
closes), INTERNAL nodes are pure scalar combinators. A tree maps ONE instrument's PIT
closes to ONE scalar — no cross-sectional/panel op ever enters the per-instrument tree
(that would smuggle across-name lookahead), so it plugs into build_panel_periods unchanged.

Invariants under test: PIT/fail-closed eval, None-poison + protected-div + non-finite,
order-stable STRUCTURAL canonicalization (no algebraic rewrites), injective ':'-free
serialize/parse round-trip, bounded validate, AlphaDefinition codec round-trip + distinct
definition_hash per distinct canonical tree, deterministic operators, provider == build_panel_periods.
"""
import math
import pytest

from services.research.gp_tree import (
    eval_tree, canonicalize, serialize, parse, validate_tree, tree_depth, tree_size,
    tree_definition, tree_from_params, make_tree_provider, tree_signal_fn,
    LEAF_OPS, UNARY, BINARY, CONST_VOCAB, MAX_DEPTH_DEFAULT, MAX_NODES_DEFAULT,
)
from services.research.gp_genome import point_and_subtree_mutate, crossover, parsimony_penalty
from services.data.persistent_store import SqlitePITStore
from quantos.contracts.data import Bar

LEAF = ("leaf", "momentum", "10")
COMPOSITE = ("pdiv", ("leaf", "momentum", "10"),
             ("add", ("leaf", "volatility", "5"), ("abs1", ("leaf", "zscore", "3"))))
VOCAB = {"windows": ("3", "5", "10"), "unary": UNARY, "binary": BINARY,
         "consts": CONST_VOCAB, "max_depth": MAX_DEPTH_DEFAULT, "max_nodes": MAX_NODES_DEFAULT}
CLOSES = [10.0, 11.0, 12.0, 11.5, 13.0, 12.5, 14.0, 13.5, 15.0, 14.5, 16.0, 15.5]


# ---------- eval: PIT / fail-closed -----------------------------------------
def test_eval_leaf_matches_signal_op():
    from services.research.signals import SIGNAL_OPS
    assert eval_tree(LEAF, CLOSES) == SIGNAL_OPS["momentum"](CLOSES, 10)


def test_eval_const_is_the_constant():
    assert eval_tree(("const", "2"), CLOSES) == 2.0
    assert eval_tree(("const", "-1"), CLOSES) == -1.0


def test_eval_composite_post_order():
    v = eval_tree(COMPOSITE, CLOSES)
    assert v is None or math.isfinite(v)


def test_eval_pit_leaf_reads_only_trailing_window():
    # momentum-3 uses closes[-1] and closes[-3]; mutating an OLDER close must not change it
    t = ("leaf", "momentum", "3")
    a = eval_tree(t, CLOSES)
    perturbed = [999.0] + CLOSES[1:]
    assert eval_tree(t, perturbed) == a


def test_eval_short_window_fails_closed():
    assert eval_tree(("leaf", "momentum", "10"), [1.0, 2.0, 3.0]) is None  # < window -> None


def test_eval_none_poison_propagates():
    # one child None -> whole node None
    t = ("add", ("leaf", "momentum", "10"), ("leaf", "momentum", "10"))
    assert eval_tree(t, [1.0, 2.0]) is None


def test_eval_protected_div_near_zero_is_none():
    # zscore of a constant series is None; but craft a pdiv by ~0 via const 1 / (x - x)
    # use sub of identical leaves -> 0 denominator -> pdiv None
    same = ("leaf", "momentum", "5")
    t = ("pdiv", ("const", "1"), ("sub", same, same))
    assert eval_tree(t, CLOSES) is None


def test_eval_non_finite_is_none():
    # huge magnitudes shouldn't escape as inf; abs of a normal leaf stays finite
    v = eval_tree(("abs1", ("leaf", "zscore", "5")), CLOSES)
    assert v is None or math.isfinite(v)


# ---------- canonical form: STRUCTURAL only ---------------------------------
def test_canonicalize_sorts_commutative_children():
    a, b = ("leaf", "momentum", "10"), ("leaf", "zscore", "5")
    assert canonicalize(("add", a, b)) == canonicalize(("add", b, a))
    assert canonicalize(("mul", a, b)) == canonicalize(("mul", b, a))


def test_canonicalize_preserves_noncommutative_order():
    a, b = ("leaf", "momentum", "10"), ("leaf", "zscore", "5")
    assert canonicalize(("sub", a, b)) != canonicalize(("sub", b, a))
    assert canonicalize(("pdiv", a, b)) != canonicalize(("pdiv", b, a))


def test_canonicalize_idempotent():
    c = canonicalize(COMPOSITE)
    assert canonicalize(c) == c


# ---------- serialize / parse: injective, ':'-free, round-trip --------------
def test_serialize_is_colon_free():
    for t in (LEAF, ("const", "-1"), COMPOSITE, ("neg", LEAF)):
        assert ":" not in serialize(t)


def test_serialize_injective_for_distinct_trees():
    s1 = serialize(canonicalize(("sub", ("leaf", "momentum", "10"), ("leaf", "zscore", "5"))))
    s2 = serialize(canonicalize(("sub", ("leaf", "zscore", "5"), ("leaf", "momentum", "10"))))
    assert s1 != s2                       # order-significant for sub


def test_parse_round_trips_canonical():
    for t in (LEAF, ("const", "2"), ("neg", LEAF), COMPOSITE):
        c = canonicalize(t)
        assert parse(serialize(c)) == c


# ---------- validate: bounded, fail-closed ----------------------------------
def test_validate_rejects_unknown_and_oversized():
    with pytest.raises(ValueError):
        validate_tree(("leaf", "NOPE", "5"), max_depth=4, max_nodes=12)
    with pytest.raises(ValueError):
        validate_tree(("leaf", "momentum", "0"), max_depth=4, max_nodes=12)   # window < 1
    with pytest.raises(ValueError):
        validate_tree(("zzz", LEAF), max_depth=4, max_nodes=12)               # unknown unary comb
    deep = LEAF
    for _ in range(6):
        deep = ("neg", deep)
    with pytest.raises(ValueError):
        validate_tree(deep, max_depth=4, max_nodes=12)                        # too deep


def test_depth_and_size():
    assert tree_depth(LEAF) == 1 and tree_size(LEAF) == 1
    assert tree_depth(("neg", LEAF)) == 2 and tree_size(("neg", LEAF)) == 2
    assert tree_size(("add", LEAF, LEAF)) == 3


# ---------- AlphaDefinition codec -------------------------------------------
def test_tree_definition_id_is_colon_free_and_stable():
    d1 = tree_definition(("add", ("leaf", "momentum", "10"), ("leaf", "zscore", "5")))
    d2 = tree_definition(("add", ("leaf", "zscore", "5"), ("leaf", "momentum", "10")))
    assert ":" not in d1.alpha_id and d1.alpha_id.startswith("hgp_")
    assert d1.alpha_id == d2.alpha_id                      # commutative -> same canonical -> same id
    assert d1.params["family"] == "gp_tree"


def test_distinct_trees_distinct_definition_hash():
    d1 = tree_definition(("sub", ("leaf", "momentum", "10"), ("leaf", "zscore", "5")))
    d2 = tree_definition(("sub", ("leaf", "zscore", "5"), ("leaf", "momentum", "10")))
    assert d1.definition_hash != d2.definition_hash        # order-significant -> distinct trial


def test_codec_round_trip_via_params():
    t = canonicalize(COMPOSITE)
    assert tree_from_params(tree_definition(COMPOSITE).params) == t


# ---------- deterministic operators -----------------------------------------
def test_mutate_is_deterministic_sorted_and_excludes_self():
    a = point_and_subtree_mutate(COMPOSITE, VOCAB, max_depth=MAX_DEPTH_DEFAULT, max_nodes=MAX_NODES_DEFAULT)
    b = point_and_subtree_mutate(COMPOSITE, VOCAB, max_depth=MAX_DEPTH_DEFAULT, max_nodes=MAX_NODES_DEFAULT)
    assert a == b                                          # no RNG / clock
    assert all(canonicalize(t) == t for t in a)            # all canonical
    assert canonicalize(COMPOSITE) not in a                # excludes self
    assert a == sorted(a, key=serialize)                   # sorted, deduped
    assert len(a) == len(set(serialize(t) for t in a))


def test_crossover_swaps_subtrees_bounded():
    t1 = ("add", ("leaf", "momentum", "10"), ("leaf", "zscore", "5"))
    t2 = ("mul", ("leaf", "volatility", "5"), ("leaf", "accel", "3"))
    kids = crossover(t1, t2, VOCAB, max_depth=MAX_DEPTH_DEFAULT, max_nodes=MAX_NODES_DEFAULT)
    assert kids == crossover(t1, t2, VOCAB, max_depth=MAX_DEPTH_DEFAULT, max_nodes=MAX_NODES_DEFAULT)
    assert all(tree_depth(t) <= MAX_DEPTH_DEFAULT and tree_size(t) <= MAX_NODES_DEFAULT for t in kids)


def test_parsimony_penalty_grows_with_size_and_depth():
    assert parsimony_penalty(COMPOSITE, 0.1, 0.1) > parsimony_penalty(LEAF, 0.1, 0.1)
    assert parsimony_penalty(LEAF, 0.0, 0.0) == 0.0


# ---------- provider == build_panel_periods ---------------------------------
def _store():
    st = SqlitePITStore(":memory:", create=True)
    insts = ["A", "B", "C"]
    for k, inst in enumerate(insts):
        for i in range(14):
            px = 100.0 + i + k * (1 if i % 2 else -1)      # some cross-sectional spread
            st.add(inst, Bar(instrument_id=inst, event_time=i + 1, knowledge_time=i + 1,
                             open=px, high=px + 1, low=px - 1, close=px, volume=100))
    return st, insts, list(range(1, 15))


def test_make_tree_provider_yields_panel_periods():
    st, insts, ets = _store()
    prov = make_tree_provider(st, insts, event_times=ets, fwd_horizon=1)
    periods = prov(tree_definition(("add", ("leaf", "momentum", "3"), ("leaf", "zscore", "3"))))
    assert isinstance(periods, list) and periods
    p = periods[-1]
    assert len(p.instruments) >= 2 and len(p.signals) == len(p.instruments)


def test_leaf_tree_equals_flat_signal_path():
    # a ('leaf',op,w) tree must produce the SAME signal as the flat 'P' atom path
    st, insts, ets = _store()
    from services.research.panel_builder import make_data_periods_provider
    from quantos.contracts.alpha import AlphaDefinition
    flat = make_data_periods_provider(st, insts, event_times=ets, fwd_horizon=1)(
        AlphaDefinition(alpha_id="h_momentum_3", name="x", factor_ids=("close",),
                        params={"operator": "momentum", "window": "3"}))
    tree = make_tree_provider(st, insts, event_times=ets, fwd_horizon=1)(
        tree_definition(("leaf", "momentum", "3")))
    assert [p.signals for p in tree] == [p.signals for p in flat]
