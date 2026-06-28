"""research_runner: the ONLY mutating surface — a guarded async trigger that runs WHITELISTED research
kinds and writes results to the run_registry. It can never run anything but research (no order/broker/
credential path); unknown kinds and unknown params are rejected fail-closed."""
import pytest

from services.research.run_registry import contract
from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.registry import RunRegistry
from services.research.research_runner.run_kinds import RUN_KINDS, RunSpec, validate_params
from services.research.research_runner.runner import JobQueue


def _register_stub(name, spec):
    """A test-only kind must be allowed in BOTH the runner whitelist and the registry whitelist."""
    RUN_KINDS[name] = spec
    contract.ALLOWED_KINDS.add(name)


def _unregister_stub(name):
    RUN_KINDS.pop(name, None)
    contract.ALLOWED_KINDS.discard(name)


def test_whitelist_is_research_only():
    assert set(RUN_KINDS) >= {"carry_strategy", "oi_research", "combo_book", "survivorship"}
    for spec in RUN_KINDS.values():
        root = spec.entrypoint.split(":")[0].split(".")[0]
        assert root in {"services", "tools"}
        assert "trading" not in spec.entrypoint and "order" not in spec.entrypoint


def test_validate_params_rejects_unknown_key():
    with pytest.raises(ValueError):
        validate_params("carry_strategy", {"evil": "rm -rf"})       # carry takes no params


def test_validate_params_rejects_wrong_type():
    # data_refresh takes {"intraday": bool}; a string must be rejected
    with pytest.raises(ValueError):
        validate_params("data_refresh", {"intraday": "yes"})


def test_unknown_kind_rejected():
    with pytest.raises(KeyError):
        validate_params("place_order", {})


def test_job_runs_and_writes_registry(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    _register_stub("_stub", RunSpec(
        entrypoint="services.research._x:noop", param_schema={"n": int},
        adapter=lambda out: {"title": "stub", "params": {}, "metrics": {"v": out}, "series": {},
                             "verdict": "DONE", "caveats": [], "provenance": "stub"},
        title="stub", runner=lambda params: params["n"] * 2))
    try:
        q = JobQueue(reg, max_workers=1)
        rid = q.submit("_stub", {"n": 21}, now=1000)
        q.join()
        got = reg.get(rid)
        assert got.status == "done" and got.metrics["v"] == 42 and got.kind == "_stub"
    finally:
        _unregister_stub("_stub")


def test_job_error_sets_error_status(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))

    def _boom(_params):
        raise RuntimeError("kaboom")

    _register_stub("_boom", RunSpec(entrypoint="services.research._x:boom", param_schema={},
                                    adapter=lambda out: {}, title="boom", runner=_boom))
    try:
        q = JobQueue(reg, max_workers=1)
        rid = q.submit("_boom", {}, now=1)
        q.join()
        got = reg.get(rid)
        assert got.status == "error" and "kaboom" in (got.error or "")
    finally:
        _unregister_stub("_boom")


def test_submit_unknown_kind_raises(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    q = JobQueue(reg, max_workers=1)
    with pytest.raises(KeyError):
        q.submit("place_order", {}, now=1)
