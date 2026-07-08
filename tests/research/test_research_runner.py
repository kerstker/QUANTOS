"""research_runner: the ONLY mutating surface — a guarded async trigger that runs WHITELISTED research
kinds and writes results to the run_registry. It can never run anything but research (no order/broker/
credential path); unknown kinds and unknown params are rejected fail-closed."""
import pytest

from services.research.run_registry import contract
from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.registry import RunRegistry
from services.research.research_runner.run_kinds import RUN_KINDS, RunSpec, validate_params
from services.research.research_runner.runner import JobQueue, _git_sha


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


# --- 2026-07-07 审计 P2: _git_sha() 在 git worktree 布局下 .git 是文件(非目录),裸 os.path.join
# 直接抛异常被吞掉静默返回 None -> 经 research_runner 产生的每条 run 都丢失 code_version ---

def test_git_sha_reads_plain_repo_git_dir(tmp_path, monkeypatch):
    """Baseline: a normal clone where .git IS a directory must keep working unchanged."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    refs_dir = git_dir / "refs" / "heads"
    refs_dir.mkdir(parents=True)
    (refs_dir / "main").write_text("abc123def4567890deadbeef\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    assert _git_sha() == "abc123def456"


def test_git_sha_follows_worktree_gitdir_file(tmp_path, monkeypatch):
    """A `git worktree add` checkout on a BRANCH (the default/typical case) — faithfully
    reproducing real git's on-disk layout, not a simplified stand-in:

      - .git is a FILE containing 'gitdir: <private per-worktree gitdir path>'.
      - The private gitdir has HEAD (a symbolic ref) + a `commondir` file, and its own
        refs/ directory exists but is EMPTY (verified against a real `git worktree add`
        checkout: `refs/heads/<branch>` is NOT under the private gitdir).
      - The actual branch ref lives in the COMMON gitdir, reached by resolving `commondir`
        (a relative path, e.g. '../..') against the private gitdir.

    _git_sha() must follow gitdir -> commondir -> common gitdir's refs/heads/<branch>,
    not silently return None."""
    main_git = tmp_path / "main_repo" / ".git"
    real_gitdir = main_git / "worktrees" / "wt1"
    real_gitdir.mkdir(parents=True)
    (real_gitdir / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
    (real_gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    (real_gitdir / "refs").mkdir()          # present but EMPTY, as in a real worktree

    common_refs_dir = main_git / "refs" / "heads"
    common_refs_dir.mkdir(parents=True)
    (common_refs_dir / "feature").write_text("deadbeefcafe0000feed\n", encoding="utf-8")

    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir()
    (worktree_dir / ".git").write_text(f"gitdir: {real_gitdir}\n", encoding="utf-8")

    monkeypatch.chdir(worktree_dir)
    assert _git_sha() == "deadbeefcafe"


def test_git_sha_worktree_falls_back_to_packed_refs_in_common_gitdir(tmp_path, monkeypatch):
    """If the branch ref has been packed (no loose refs/heads/<branch> file at all, only an
    entry in packed-refs), resolution must still work via the common gitdir."""
    main_git = tmp_path / "main_repo" / ".git"
    real_gitdir = main_git / "worktrees" / "wt1"
    real_gitdir.mkdir(parents=True)
    (real_gitdir / "HEAD").write_text("ref: refs/heads/packed-branch\n", encoding="utf-8")
    (real_gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    (real_gitdir / "refs").mkdir()

    main_git.mkdir(parents=True, exist_ok=True)
    (main_git / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        "cafefeed0000deadbeef1234 refs/heads/packed-branch\n",
        encoding="utf-8",
    )

    worktree_dir = tmp_path / "worktree3"
    worktree_dir.mkdir()
    (worktree_dir / ".git").write_text(f"gitdir: {real_gitdir}\n", encoding="utf-8")

    monkeypatch.chdir(worktree_dir)
    assert _git_sha() == "cafefeed0000"


def test_git_sha_returns_none_when_worktree_gitdir_target_missing(tmp_path, monkeypatch):
    """Double failure: the .git file resolves to a gitdir that itself has no readable HEAD.
    Must degrade gracefully to None, not raise (submit()/​_run() must never crash on this)."""
    worktree_dir = tmp_path / "worktree2"
    worktree_dir.mkdir()
    missing_gitdir = tmp_path / "does_not_exist"
    (worktree_dir / ".git").write_text(f"gitdir: {missing_gitdir}\n", encoding="utf-8")

    monkeypatch.chdir(worktree_dir)
    assert _git_sha() is None


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
