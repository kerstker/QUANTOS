"""Rank #19 — the offline rebuild orchestrator's plan is well-formed and deterministic, and it
skips honestly when an input cache is absent (never fabricates a snapshot)."""
from tools.rebuild_snapshots import plan, run


def test_plan_is_ordered_and_well_formed():
    p = plan()
    assert len(p) >= 10
    for step in p:
        assert set(step) == {"label", "module", "input_db", "input_present"}
        assert step["module"].startswith("tools.")
        assert isinstance(step["input_present"], bool)
    labels = [s["label"] for s in p]
    assert len(labels) == len(set(labels))                   # unique labels
    # dependency order: a capacity/dry-run generator comes before the digest-consuming diagnostics
    assert labels.index("capacity") < labels.index("ic_panel")


def test_plan_is_deterministic():
    assert plan() == plan()


def test_list_mode_never_executes_and_reports_skips_for_absent_inputs():
    # execute=False must not import/run any generator; it only lists (absent inputs -> skipped)
    result = run(execute=False)
    assert result["ran"] == [] and result["failed"] == []
    # every generator whose input DB is absent is reported skipped (honest, not faked)
    absent = [s["label"] for s in plan() if not s["input_present"]]
    assert set(absent) <= set(result["skipped"])
