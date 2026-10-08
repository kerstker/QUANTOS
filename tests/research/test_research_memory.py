"""Phase 7 — Research Memory + auto Brief (no LLM required). The experiment registry stores immutable
EVALUATION artifacts; this adds the research-DECISION metadata the audit found missing (natural-language
hypothesis, code version, lineage, regime, next-action) and a DETERMINISTIC brief synthesizer over them:
what was tested, what worked / failed and why, which are redundant, and recommended next actions +
mutation suggestions. Pure-Python, deterministic — an LLM can later PROPOSE next actions, but the brief
itself needs no model."""
from quantos.contracts.alpha import AlphaEvaluation
from quantos.contracts.alpha_asset import AlphaAsset, AlphaLineage
from quantos.contracts.research import ValidationVerdict
from services.research.research_memory import ResearchMemory, ResearchNote
from services.research.research_brief import compose_brief


def _note(aid, verdict, dsr=0.7, gen="cs_genome", cluster="", reasons=(), data=("close",), nxt=""):
    return ResearchNote(alpha_id=aid, verdict=verdict, deflated_sharpe=dsr, generator=gen,
                        cluster_id=cluster, reasons=tuple(reasons), data_dependencies=tuple(data),
                        next_action=nxt, hypothesis=f"hyp for {aid}")


def test_memory_records_and_queries():
    m = ResearchMemory()
    m.record(_note("a", "PASS"))
    m.record(_note("b", "FAIL", reasons=("PBO_TOO_HIGH",)))
    assert {n.alpha_id for n in m.all()} == {"a", "b"}
    assert [n.alpha_id for n in m.by_verdict("PASS")] == ["a"]
    assert m.get("b").reasons == ("PBO_TOO_HIGH",)


def test_record_is_idempotent_latest_wins():
    m = ResearchMemory()
    m.record(_note("a", "FAIL"))
    m.record(_note("a", "PASS"))                       # re-record updates
    assert m.get("a").verdict == "PASS" and len(m.all()) == 1


def test_from_asset_pulls_provenance_and_lineage():
    ev = AlphaEvaluation(alpha_id="x", verdict=ValidationVerdict.PASS, deflated_sharpe=0.85, net_sharpe=1.3)
    asset = AlphaAsset(alpha_id="x", generator="gp", cluster_id="cl_1", data_dependencies=("funding",),
                       hypothesis="perp carry", evaluation=ev, regime_stability=0.7,
                       lineage=AlphaLineage(parent_ids=("p1",), mutation_op="crossover"))
    note = ResearchNote.from_asset(asset, code_version="abc123", next_action="scale funding factors")
    assert note.alpha_id == "x" and note.generator == "gp" and note.verdict == "PASS"
    assert note.deflated_sharpe == 0.85 and note.parent_ids == ("p1",) and note.mutation_op == "crossover"
    assert note.code_version == "abc123" and note.regime_stability == 0.7
    assert note.data_dependencies == ("funding",) and note.next_action == "scale funding factors"


def test_brief_partitions_worked_failed_and_reasons():
    notes = [
        _note("good1", "PASS", dsr=0.9, data=("funding",)),
        _note("good2", "PASS", dsr=0.8, data=("funding",)),
        _note("bad1", "FAIL", reasons=("PBO_TOO_HIGH", "REGIME_UNSTABLE")),
        _note("bad2", "FAIL", reasons=("PBO_TOO_HIGH",)),
        _note("rev", "REQUIRES_HUMAN_REVIEW"),
    ]
    b = compose_brief(notes)
    assert b["n_tested"] == 5
    assert b["worked"] == ["good1", "good2"] and b["failed"] == ["bad1", "bad2"]
    assert b["needs_review"] == ["rev"]
    assert b["failure_reasons"]["PBO_TOO_HIGH"] == 2          # histogram across failures
    assert b["top_performers"][0] == "good1"                  # highest DSR first
    # worked alphas all use funding -> the brief recommends expanding that data dependency
    assert any("funding" in a for a in b["next_actions"])


def test_brief_flags_all_failed_pivot():
    notes = [_note("a", "FAIL", reasons=("DEFLATED_SHARPE_BELOW_MIN",)),
             _note("b", "REJECTED", reasons=("LEAKAGE_DETECTED",))]
    b = compose_brief(notes)
    assert b["worked"] == []
    assert any("pivot" in a.lower() or "failed" in a.lower() for a in b["next_actions"])


def test_brief_redundant_clusters_and_mutation_suggestions():
    notes = [_note("a", "PASS", dsr=0.9, cluster="cl_0"), _note("b", "PASS", dsr=0.8, cluster="cl_0"),
             _note("c", "PASS", dsr=0.7, cluster="cl_1")]
    b = compose_brief(notes)
    assert b["redundant_clusters"].get("cl_0") == ["a", "b"]   # >1 member = redundant group
    assert "cl_1" not in b["redundant_clusters"]               # singleton not redundant
    assert any("a" in s for s in b["mutation_suggestions"])    # mutate the best performer


def test_brief_explicit_next_actions_surface_and_deterministic():
    notes = [_note("a", "PASS", nxt="try weekly rebalance"), _note("b", "PASS", nxt="try weekly rebalance")]
    b1 = compose_brief(notes)
    b2 = compose_brief(notes)
    assert b1 == b2                                            # deterministic
    assert "try weekly rebalance" in b1["next_actions"]        # explicit note action surfaced, deduped


def test_brief_surfaces_warn_verdict_partition_complete():
    # review P1: WARN is a real gate verdict (one soft failure) — it must not be silently dropped.
    notes = [_note("p", "PASS"), _note("w", "WARN"), _note("f", "FAIL"), _note("r", "REQUIRES_HUMAN_REVIEW")]
    b = compose_brief(notes)
    assert b["warned"] == ["w"]
    buckets = set(b["worked"]) | set(b["failed"]) | set(b["needs_review"]) | set(b["warned"])
    assert buckets == set(b["tested"])           # every tested alpha lands in exactly one bucket


def test_next_actions_dedup_across_quote_styles():
    # review P2: an explicit action differing from the heuristic only in quote style is merged
    n = ResearchNote(alpha_id="a", verdict="PASS", deflated_sharpe=0.9, generator="g1",
                     data_dependencies=("dep1",),
                     next_action='expand factors using the "dep1" data dependency (dominates the survivors)')
    acts = compose_brief([n])["next_actions"]
    norm = [a.replace('"', "'") for a in acts]
    assert len(norm) == len(set(norm))           # no quote-only duplicates remain


def test_empty_memory_and_no_rng():
    assert compose_brief([])["n_tested"] == 0
    import inspect
    import services.research.research_brief as rb
    import services.research.research_memory as rm
    for mod in (rb, rm):
        src = inspect.getsource(mod)
        assert "import random" not in src and "import time" not in src and "Random" not in src
