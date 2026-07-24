"""research_runner: the ONLY mutating surface — a guarded async trigger that runs WHITELISTED research
kinds and writes results to the run_registry. It can never run anything but research (no order/broker/
credential path); unknown kinds and unknown params are rejected fail-closed."""
from contextlib import contextmanager
from dataclasses import replace
import json

import pytest

from services.research.run_registry import contract
from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.registry import RunRegistry
from services.research_runner.run_kinds import (
    DIAGNOSTIC_ONLY,
    RUN_KINDS,
    SELECTION_BEARING,
    RunPurpose,
    RunSpec,
    validate_params,
)
from services.research_runner.runner import (
    JobQueue,
    ProvenanceError,
    _git_sha,
    validate_run_provenance,
)


def _factory_panel():
    from services.research.alpha_expression import FieldPanel

    return FieldPanel(
        ["A", "B"],
        [1, 2, 3, 4],
        {
            "close": [[100.0, 101.0]] * 4,
            "funding": [[0.001, -0.001]] * 4,
            "global_ls": [[1.1, 0.9]] * 4,
        },
    )


def _patch_factory_tool(monkeypatch, module, captured):
    marker = object()

    @contextmanager
    def registry_context():
        yield marker

    def fake_factory(*_args, **kwargs):
        captured.append(kwargs)
        return {
            "n_evaluated": 0,
            "best": [],
            "scored_by": "orthogonal_incremental_ir",
        }

    monkeypatch.setattr(module, "build_panel", lambda **_kwargs: (_factory_panel(), ["A", "B"]))
    monkeypatch.setattr(module, "economic_return_series", lambda *_args, **_kwargs: [0.01] * 4)
    monkeypatch.setattr(module, "sharpe", lambda *_args, **_kwargs: 1.0)
    monkeypatch.setattr(
        module,
        "walk_forward_selection_oos",
        lambda *_args, **_kwargs: {"n_folds": 0, "mean_oos_sharpe": None, "folds": []},
    )
    monkeypatch.setattr(module, "production_experiment_registry", registry_context)
    monkeypatch.setattr(module, "run_alpha_factory", fake_factory)
    return marker


def _assert_persistent_factory_call(call, marker):
    assert call["search_registry"] is marker
    assert call["search_id"].startswith("search-")
    assert call["search_kind"]
    assert call["search_source"].startswith("tools.crypto_factory_")
    assert call["dataset_version"]
    assert isinstance(call["timestamp"], int)


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


def test_runner_kinds_have_explicit_research_use_policies():
    selection_bearing = {
        kind
        for kind, spec in RUN_KINDS.items()
        if spec.provenance_policy.purpose is RunPurpose.SELECTION_BEARING
    }
    diagnostic_only = {
        kind
        for kind, spec in RUN_KINDS.items()
        if spec.provenance_policy.purpose is RunPurpose.DIAGNOSTIC_ONLY
    }
    assert selection_bearing == {"factory_orthogonal", "factory_loop"}
    assert diagnostic_only == set(RUN_KINDS) - selection_bearing


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


def test_factory_orthogonal_runner_kind_injects_durable_trial_provenance(
    tmp_path, monkeypatch
):
    import tools.crypto_factory_orthogonal as tool

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "cache").mkdir(parents=True)
    captured = []
    marker = _patch_factory_tool(monkeypatch, tool, captured)

    result = RUN_KINDS["factory_orthogonal"].runner(
        {"generations": 1, "population": 4}
    )

    assert result["containment"].startswith("research")
    assert len(captured) == 1
    _assert_persistent_factory_call(captured[0], marker)


def test_factory_loop_runner_kind_injects_provenance_for_every_round(
    tmp_path, monkeypatch
):
    import tools.crypto_factory_loop as tool

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "cache").mkdir(parents=True)
    captured = []
    marker = _patch_factory_tool(monkeypatch, tool, captured)

    result = RUN_KINDS["factory_loop"].runner({})

    assert result["containment"].startswith("research")
    assert len(captured) == tool.DRY_LIMIT
    for call in captured:
        _assert_persistent_factory_call(call, marker)
    assert len({call["search_id"] for call in captured}) == len(captured)


def test_job_runs_and_writes_registry(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))
    _register_stub("_stub", RunSpec(
        entrypoint="services.research._x:noop", param_schema={"n": int},
        adapter=lambda out: {"title": "stub", "params": {}, "metrics": {"v": out}, "series": {},
                             "verdict": "DONE", "caveats": [],
                             "provenance": "services/research/_x.py"},
        title="stub", provenance_policy=DIAGNOSTIC_ONLY,
        runner=lambda params: params["n"] * 2))
    try:
        q = JobQueue(reg, max_workers=1)
        rid = q.submit("_stub", {"n": 21}, now=1000)
        q.join()
        got = reg.get(rid)
        assert got.status == "done" and got.metrics["v"] == 42 and got.kind == "_stub"
        provenance = validate_run_provenance(got, RUN_KINDS["_stub"])
        assert provenance["purpose"] == "DIAGNOSTIC_ONLY"
        assert provenance["selection_eligible"] is False
        assert provenance["ledger_binding_status"] == "NOT_APPLICABLE"
        assert provenance["result_authority"] == "NON_AUTHORITATIVE_RUN_REGISTRY_MIRROR"
        assert provenance["result_sha256"]
    finally:
        _unregister_stub("_stub")


def test_paper_cycle_runner_uses_fixed_profile_and_adapts_health(
    tmp_path, monkeypatch
):
    import tools.paper_realtime as paper_tool

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "cache").mkdir(parents=True)
    captured = {}

    def fake_main(argv):
        captured["argv"] = list(argv)
        output = argv[argv.index("--output") + 1]
        health_output = argv[argv.index("--health-output") + 1]
        (tmp_path / output).write_text(json.dumps({
            "verdict": {"cycle_completed": True},
            "persistence": {"new_snapshots": 3},
            "alphas": {"carry_oi_majors": {"net_pnl": 1.5}},
        }), encoding="utf-8")
        (tmp_path / health_output).write_text(json.dumps({
            "verdict": {"paper_ready": True}, "blockers": [],
        }), encoding="utf-8")
        return 0

    monkeypatch.setattr(paper_tool, "main", fake_main)
    result = RUN_KINDS["paper_cycle"].runner({"small_10k": True})

    assert result["profile"] == "small_10k"
    assert "--refresh-paper" in captured["argv"]
    assert captured["argv"][captured["argv"].index("--aum") + 1] == "10000"
    assert result["report"]["persistence"]["new_snapshots"] == 3
    adapted = RUN_KINDS["paper_cycle"].adapter(result)
    assert adapted["verdict"] == "PAPER_CYCLE_OK"
    assert adapted["provenance"] == "tools/paper_realtime.py"


def test_same_second_submissions_are_distinct_invocations(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "same-second.db")))
    _register_stub("_same_second", RunSpec(
        entrypoint="services.research._x:noop",
        param_schema={},
        adapter=lambda _out: {
            "title": "same-second",
            "metrics": {},
            "series": {},
            "verdict": "DONE",
            "caveats": [],
            "provenance": "services/research/_x.py",
        },
        title="same-second",
        provenance_policy=DIAGNOSTIC_ONLY,
        runner=lambda _params: None,
    ))
    try:
        queue = JobQueue(reg, max_workers=2)
        first = queue.submit("_same_second", {}, now=1000, operator="alice")
        second = queue.submit("_same_second", {}, now=1000, operator="bob")
        queue.join()
        assert first != second
        assert {reg.get(first).operator, reg.get(second).operator} == {"alice", "bob"}
        assert reg.get(first).invocation_id != reg.get(second).invocation_id
    finally:
        _unregister_stub("_same_second")


def test_duplicate_invocation_id_is_idempotent_across_seconds(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "duplicate-invocation.db")))
    calls = []
    _register_stub("_duplicate_invocation", RunSpec(
        entrypoint="services.research._x:noop",
        param_schema={},
        adapter=lambda _out: {
            "title": "duplicate",
            "metrics": {},
            "series": {},
            "verdict": "DONE",
            "caveats": [],
            "provenance": "services/research/_x.py",
        },
        title="duplicate",
        provenance_policy=DIAGNOSTIC_ONLY,
        runner=lambda _params: calls.append("ran"),
    ))
    try:
        queue = JobQueue(reg, max_workers=1)
        run_id = queue.submit(
            "_duplicate_invocation", {}, now=1000, invocation_id="fixed"
        )
        replayed_run_id = queue.submit(
            "_duplicate_invocation", {}, now=1001, invocation_id="fixed"
        )
        queue.join()
        assert calls == ["ran"]
        assert replayed_run_id == run_id
        assert reg.get(run_id).invocation_id == "fixed"
    finally:
        _unregister_stub("_duplicate_invocation")


@pytest.mark.parametrize(
    ("second_kind", "second_params", "second_operator"),
    [
        ("_invocation_a", {"value": 2}, "alice"),
        ("_invocation_b", {"value": 1}, "alice"),
        ("_invocation_a", {"value": 1}, "bob"),
    ],
)
def test_invocation_reuse_with_different_request_fails_closed(
    tmp_path, second_kind, second_params, second_operator
):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "invocation-conflict.db")))
    calls = []

    def spec(label):
        return RunSpec(
            entrypoint="services.research._x:noop",
            param_schema={"value": int},
            adapter=lambda _out: {
                "title": label,
                "metrics": {},
                "series": {},
                "verdict": "DONE",
                "caveats": [],
                "provenance": "services/research/_x.py",
            },
            title=label,
            provenance_policy=DIAGNOSTIC_ONLY,
            runner=lambda _params: calls.append(label),
        )

    _register_stub("_invocation_a", spec("a"))
    _register_stub("_invocation_b", spec("b"))
    try:
        queue = JobQueue(reg, max_workers=1)
        queue.submit(
            "_invocation_a",
            {"value": 1},
            now=1000,
            operator="alice",
            invocation_id="fixed-conflict",
        )
        with pytest.raises(contract.InvocationConflictError, match="different kind, params, or operator"):
            queue.submit(
                second_kind,
                second_params,
                now=1001,
                operator=second_operator,
                invocation_id="fixed-conflict",
            )
        queue.join()
        assert calls == ["a"]
        assert len(reg.list()) == 1
    finally:
        _unregister_stub("_invocation_a")
        _unregister_stub("_invocation_b")


def test_selection_bearing_result_names_authority_but_stays_unbound_and_ineligible(
    tmp_path, monkeypatch
):
    import services.research_runner.runner as runner_module

    reg = RunRegistry(ResearchRunStore(str(tmp_path / "selection.db")))
    monkeypatch.setattr(runner_module, "_git_sha", lambda: "abc123def456")
    _register_stub("_selection", RunSpec(
        entrypoint="services.research._x:select",
        param_schema={},
        adapter=lambda _out: {
            "title": "selection",
            "metrics": {"score": 1.5},
            "series": {},
            "verdict": "CANDIDATE",
            "caveats": ["not promoted"],
            "provenance": "services/research/_x.py",
        },
        title="selection",
        provenance_policy=SELECTION_BEARING,
        runner=lambda _params: {"candidate": "alpha-1"},
    ))
    try:
        q = JobQueue(reg, max_workers=1)
        rid = q.submit("_selection", {}, now=7)
        q.join()
        got = reg.get(rid)
        provenance = validate_run_provenance(got, RUN_KINDS["_selection"])
        assert provenance["purpose"] == "SELECTION_BEARING"
        assert provenance["selection_eligible"] is False
        assert provenance["evidence_authority"] == "EXPERIMENT_REGISTRY_GLOBAL_TRIAL_LEDGER"
        assert provenance["ledger_binding_status"] == "UNBOUND"
        assert provenance["result_authority"] == "NON_AUTHORITATIVE_RUN_REGISTRY_MIRROR"
        assert provenance["code_version"] == "abc123def456"
        assert provenance["code_attestation"] == "HEAD_ONLY_WORKTREE_UNATTESTED"
        assert provenance["result_sha256"]
        false_green = dict(provenance, selection_eligible=True)
        with pytest.raises(ProvenanceError, match="selection_eligible"):
            validate_run_provenance(
                replace(got, provenance=json.dumps(false_green)),
                RUN_KINDS["_selection"],
            )
    finally:
        _unregister_stub("_selection")


@pytest.mark.parametrize("code_version", [None, "not-a-commit"])
def test_selection_bearing_submit_fails_before_persistence_without_valid_code_version(
    tmp_path, monkeypatch, code_version
):
    import services.research_runner.runner as runner_module

    reg = RunRegistry(ResearchRunStore(str(tmp_path / "missing-code.db")))
    monkeypatch.setattr(runner_module, "_git_sha", lambda: code_version)
    _register_stub("_selection_no_code", RunSpec(
        entrypoint="services.research._x:select",
        param_schema={},
        adapter=lambda _out: {},
        title="selection",
        provenance_policy=SELECTION_BEARING,
        runner=lambda _params: {},
    ))
    try:
        q = JobQueue(reg, max_workers=1)
        with pytest.raises(ProvenanceError, match="code version"):
            q.submit("_selection_no_code", {}, now=7)
        assert reg.list() == []
    finally:
        _unregister_stub("_selection_no_code")


def test_adapter_cannot_override_declared_source(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "wrong-source.db")))
    _register_stub("_wrong_source", RunSpec(
        entrypoint="services.research._x:diagnose",
        param_schema={},
        adapter=lambda _out: {"provenance": "unknown.py"},
        title="diagnostic",
        provenance_policy=DIAGNOSTIC_ONLY,
        runner=lambda _params: {},
    ))
    try:
        q = JobQueue(reg, max_workers=1)
        rid = q.submit("_wrong_source", {}, now=8)
        q.join()
        got = reg.get(rid)
        assert got.status == "error"
        assert "does not match" in (got.error or "")
        assert json.loads(got.provenance)["selection_eligible"] is False
    finally:
        _unregister_stub("_wrong_source")


def test_persisted_result_tampering_breaks_provenance_validation(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "tamper.db")))
    _register_stub("_tamper", RunSpec(
        entrypoint="services.research._x:diagnose",
        param_schema={},
        adapter=lambda _out: {
            "metrics": {"score": 1.0},
            "provenance": "services/research/_x.py",
        },
        title="diagnostic",
        provenance_policy=DIAGNOSTIC_ONLY,
        runner=lambda _params: {},
    ))
    try:
        q = JobQueue(reg, max_workers=1)
        rid = q.submit("_tamper", {}, now=9)
        q.join()
        got = reg.get(rid)
        with pytest.raises(ProvenanceError, match="result digest"):
            validate_run_provenance(replace(got, metrics={"score": 99.0}), RUN_KINDS["_tamper"])
    finally:
        _unregister_stub("_tamper")


def test_job_error_sets_error_status(tmp_path):
    reg = RunRegistry(ResearchRunStore(str(tmp_path / "runs.db")))

    def _boom(_params):
        raise RuntimeError("kaboom")

    _register_stub("_boom", RunSpec(entrypoint="services.research._x:boom", param_schema={},
                                    adapter=lambda out: {}, title="boom",
                                    provenance_policy=DIAGNOSTIC_ONLY, runner=_boom))
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
                             "verdict": "D", "caveats": [],
                             "provenance": "services/research/_x.py"},
        title="t", provenance_policy=DIAGNOSTIC_ONLY, runner=_printer))
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
                             "verdict": "D", "caveats": [],
                             "provenance": "services/research/_x.py"},
        title="t", provenance_policy=DIAGNOSTIC_ONLY, runner=_slow))
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


def test_cancelled_queued_run_never_starts_or_gets_overwritten(tmp_path):
    import threading

    reg = RunRegistry(ResearchRunStore(str(tmp_path / "queued-cancel.db")))
    first_started = threading.Event()
    release_first = threading.Event()
    calls = []

    def _serial(_params):
        calls.append("started")
        if len(calls) == 1:
            first_started.set()
            release_first.wait(3)
        return 1

    _register_stub("_queued_cancel", RunSpec(
        entrypoint="services.research._x:serial",
        param_schema={},
        adapter=lambda _out: {
            "title": "serial",
            "metrics": {},
            "series": {},
            "verdict": "DONE",
            "caveats": [],
            "provenance": "services/research/_x.py",
        },
        title="serial",
        provenance_policy=DIAGNOSTIC_ONLY,
        runner=_serial,
    ))
    try:
        queue = JobQueue(reg, max_workers=1)
        first = queue.submit("_queued_cancel", {}, now=1)
        assert first_started.wait(3)
        second = queue.submit("_queued_cancel", {}, now=1)
        assert queue.cancel(second) is True
        release_first.set()
        queue.join()
        assert reg.get(first).status == "done"
        assert reg.get(second).status == "cancelled"
        assert calls == ["started"]
    finally:
        _unregister_stub("_queued_cancel")


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
                             "verdict": "D", "caveats": [],
                             "provenance": "services/research/_x.py"},
        title="t", provenance_policy=DIAGNOSTIC_ONLY, runner=lambda _p: 1))
    try:
        rid = q.submit("_done", {}, now=1)
        q.join()
        assert q.cancel(rid) is False                   # already done -> cannot cancel
    finally:
        _unregister_stub("_done")
