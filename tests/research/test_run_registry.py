"""run_registry: durable, fail-closed store of research-run records (research-ops console source of truth).

Pure stdlib sqlite3; deterministic (timestamps injected, no wall-clock); only whitelisted research kinds
may be stored (a non-research kind like an order is rejected at the registry boundary)."""
import pytest

from services.research.run_registry.contract import ResearchRun, make_run_id, VALID_STATUS
from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.registry import RunRegistry


def _run(**kw):
    base = dict(run_id="r1", kind="carry_strategy", title="t", params={"q": 0.3}, status="done",
                created_ts=1000, finished_ts=1010, metrics={"net_sharpe": 2.03},
                series={"per_year": [4.4, 2.8]}, verdict="DEPLOYABLE", caveats=["survivorship"],
                provenance="tool:carry", error=None)
    base.update(kw)
    return ResearchRun(**base)


def test_run_id_is_deterministic():
    a = make_run_id("carry_strategy", {"q": 0.3}, 1000)
    assert a == make_run_id("carry_strategy", {"q": 0.3}, 1000)
    assert a != make_run_id("carry_strategy", {"q": 0.3}, 1001)
    assert a.startswith("run_")


def test_store_round_trip(tmp_path):
    s = ResearchRunStore(str(tmp_path / "runs.db"))
    s.upsert(_run())
    got = s.get("r1")
    assert got.kind == "carry_strategy" and got.metrics["net_sharpe"] == 2.03
    assert got.series["per_year"] == [4.4, 2.8] and got.caveats == ["survivorship"]


def test_list_is_newest_first_and_filterable(tmp_path):
    s = ResearchRunStore(str(tmp_path / "runs.db"))
    s.upsert(_run(run_id="a", kind="carry_strategy", created_ts=1))
    s.upsert(_run(run_id="b", kind="oi_research", created_ts=2))
    assert [r.run_id for r in s.list()] == ["b", "a"]
    assert [r.run_id for r in s.list(kind="oi_research")] == ["b"]


def test_set_status(tmp_path):
    s = ResearchRunStore(str(tmp_path / "runs.db"))
    s.upsert(_run(status="queued", run_id="q1"))
    s.set_status("q1", "running")
    assert s.get("q1").status == "running"
    s.set_status("q1", "error", error="boom")
    assert s.get("q1").error == "boom" and s.get("q1").status == "error"


def test_empty_store_returns_none_and_empty(tmp_path):
    s = ResearchRunStore(str(tmp_path / "runs.db"))
    assert s.get("nope") is None
    assert s.list() == []


def test_registry_rejects_bad_status(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    with pytest.raises(ValueError):
        reg.upsert(_run(status="bogus"))


def test_registry_rejects_unknown_kind(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    with pytest.raises(ValueError):
        reg.upsert(_run(kind="place_order"))      # only whitelisted research kinds allowed


def test_valid_status_set():
    assert VALID_STATUS == {"queued", "running", "done", "error", "cancelled"}


def test_log_and_code_version_round_trip(tmp_path):
    s = ResearchRunStore(str(tmp_path / "runs.db"))
    s.upsert(_run(run_id="L1", log=["[seed] start", "[gen 1] best 0.5"], code_version="abc1234"))
    got = s.get("L1")
    assert got.log == ["[seed] start", "[gen 1] best 0.5"] and got.code_version == "abc1234"


def test_append_log_is_live(tmp_path):
    s = ResearchRunStore(str(tmp_path / "runs.db"))
    s.upsert(_run(run_id="P1", status="running", log=[]))
    s.append_log("P1", "[gen 1] done")
    s.append_log("P1", "[gen 2] done")
    assert s.get("P1").log == ["[gen 1] done", "[gen 2] done"]


def test_cancelled_status_is_valid(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    reg.upsert(_run(run_id="C1", status="queued"))
    reg.set_status("C1", "cancelled")
    assert reg.get("C1").status == "cancelled"
