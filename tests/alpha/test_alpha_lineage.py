"""Lineage construction primitive: build a child AlphaAsset from its parents, recording the mutation
operator and the performance delta (child metric - best parent metric). This is the reusable piece the
evolutionary / cs_genome search calls when it breeds a child, so genealogy is written into the registry
(parent->child + op + delta) instead of being ephemeral. Deterministic."""
from quantos.contracts.alpha import AlphaEvaluation
from quantos.contracts.alpha_asset import AlphaAsset
from quantos.contracts.research import ValidationVerdict
from services.alpha.lineage import make_child_asset


def _asset(aid, ns):
    return AlphaAsset(alpha_id=aid, generator="cs_genome",
                      evaluation=AlphaEvaluation(alpha_id=aid, verdict=ValidationVerdict.PASS, net_sharpe=ns))


def test_child_records_parents_op_and_perf_delta():
    p1, p2 = _asset("p1", 1.0), _asset("p2", 1.5)
    child_ev = AlphaEvaluation(alpha_id="c", verdict=ValidationVerdict.PASS, net_sharpe=1.8)
    c = make_child_asset(alpha_id="c", expression="(cs_rank (field funding))", parents=[p1, p2],
                         mutation_op="crossover", evaluation=child_ev)
    assert c.lineage.parent_ids == ("p1", "p2") and c.lineage.mutation_op == "crossover"
    assert abs(c.lineage.perf_delta - (1.8 - 1.5)) < 1e-9       # improvement over the BEST parent
    assert c.generator == "cs_genome"                           # inherited from parent when unset
    assert c.evaluation is child_ev


def test_no_parents_or_no_eval_gives_none_delta():
    c = make_child_asset(alpha_id="seed", expression="(field close)", parents=[], mutation_op="seed")
    assert c.lineage.parent_ids == () and c.lineage.perf_delta is None
    p = _asset("p", 1.0)
    c2 = make_child_asset(alpha_id="c2", expression="x", parents=[p], mutation_op="mutate")  # no child eval
    assert c2.lineage.perf_delta is None and c2.lineage.parent_ids == ("p",)


def test_metadata_overrides_and_generator_explicit():
    p = _asset("p", 1.0)
    c = make_child_asset(alpha_id="c", expression="x", parents=[p], mutation_op="mutate",
                         generator="gp", tags=("bred",), version=3)
    assert c.generator == "gp" and c.tags == ("bred",) and c.version == 3


def test_explicit_parent_ids_override_records_full_genealogy():
    # 育种时父代 asset 常不在本次 best 作用域(拿不到 AlphaAsset 对象);parent_ids override 记全谱系,
    # perf_delta 仍从 in-scope parents 评估算(拿不到的父不参与 delta,fail-safe 契约既有)
    p1 = _asset("p1", 1.0)
    child_ev = AlphaEvaluation(alpha_id="c", verdict=ValidationVerdict.PASS, net_sharpe=1.5)
    c = make_child_asset(alpha_id="c", expression="x", parents=[p1], parent_ids=("p1", "p2_out"),
                         mutation_op="crossover", evaluation=child_ev)
    assert c.lineage.parent_ids == ("p1", "p2_out")            # 全谱系(含不在 scope 的父)
    assert abs(c.lineage.perf_delta - (1.5 - 1.0)) < 1e-9      # perf_delta 仍从 in-scope p1 算


def test_default_parent_ids_unchanged_from_parents():
    p = _asset("p", 1.0)
    c = make_child_asset(alpha_id="c", expression="x", parents=[p], mutation_op="mutate")
    assert c.lineage.parent_ids == ("p",)                     # parent_ids=None -> 从 parents 派生(旧行为)
