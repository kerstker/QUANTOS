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


def test_factory_params_validate_typed():
    # factory search depth is tunable from the UI; typed + key-checked
    assert validate_params("factory_orthogonal", {"generations": 4, "population": 32}) == {"generations": 4, "population": 32}
    with pytest.raises(ValueError):
        validate_params("factory_orthogonal", {"generations": "deep"})   # wrong type
    with pytest.raises(ValueError):
        validate_params("factory_orthogonal", {"rounds": 3})             # unknown key


# --- 2026-07-06 安全审查 punch-list:generations/population 无上限,可提交一个跑到天荒地老的任务 ---

def test_factory_params_reject_unbounded_generations():
    with pytest.raises(ValueError):
        validate_params("factory_orthogonal", {"generations": 10 ** 6})


def test_factory_params_reject_unbounded_population():
    with pytest.raises(ValueError):
        validate_params("factory_orthogonal", {"population": 10 ** 6})


def test_factory_params_reject_non_positive_generations():
    with pytest.raises(ValueError):
        validate_params("factory_orthogonal", {"generations": 0})


def test_factory_params_reject_non_positive_population():
    with pytest.raises(ValueError):
        validate_params("factory_orthogonal", {"population": -1})


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


def test_live_log_captured_from_stdout_and_git_sha(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))

    def _printer(_p):
        print("[seed] hello")
        print("[gen 1] best 0.42")
        return 7

    _register_stub("_log", RunSpec(entrypoint="services.research._x:p", param_schema={},
        adapter=lambda out: {"title": "t", "params": {}, "metrics": {"v": out}, "series": {},
                             "verdict": "D", "caveats": [], "provenance": "s"},
        title="t", runner=_printer))
    try:
        q = JobQueue(reg, max_workers=1)
        rid = q.submit("_log", {}, now=1)
        q.join()
        got = reg.get(rid)
        assert got.status == "done"
        assert any("[gen 1] best 0.42" in line for line in got.log) and "[done]" in got.log
        assert got.code_version is not None            # git sha captured (auditable)
    finally:
        _unregister_stub("_log")


def test_soft_cancel_drops_result(tmp_path):
    import threading
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    started, release = threading.Event(), threading.Event()

    def _slow(_p):
        started.set()
        release.wait(3)
        return 1

    _register_stub("_slow", RunSpec(entrypoint="services.research._x:s", param_schema={},
        adapter=lambda out: {"title": "t", "params": {}, "metrics": {"v": out}, "series": {},
                             "verdict": "D", "caveats": [], "provenance": "s"},
        title="t", runner=_slow))
    try:
        q = JobQueue(reg, max_workers=1)
        rid = q.submit("_slow", {}, now=1)
        assert started.wait(3)                          # the job is running
        assert q.cancel(rid) is True
        release.set()
        q.join()
        assert reg.get(rid).status == "cancelled"       # result dropped, stays cancelled
    finally:
        _unregister_stub("_slow")


def test_cancel_terminal_run_returns_false(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    q = JobQueue(reg, max_workers=1)
    _register_stub("_done", RunSpec(entrypoint="services.research._x:d", param_schema={},
        adapter=lambda out: {"title": "t", "params": {}, "metrics": {}, "series": {},
                             "verdict": "D", "caveats": [], "provenance": "s"},
        title="t", runner=lambda _p: 1))
    try:
        rid = q.submit("_done", {}, now=1)
        q.join()
        assert q.cancel(rid) is False                   # already done -> cannot cancel
    finally:
        _unregister_stub("_done")
