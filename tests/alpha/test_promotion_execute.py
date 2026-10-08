"""execute_promotion(战线①,2026-07-06):先烧 grant 再推进(fail-closed 方向)、
证据只来自真实 artifact(evaluation artifact + paper_track.db,绝不代填)、烧毁后全路径审计、
consume 前预检(评审 #4:错的执行人 / 非法转换不白烧)、experiments db 单飞锁(评审 #7)。"""
import json
import os
import threading
from dataclasses import replace

import pytest

import services.alpha.promotion as promotion_mod
from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition
from quantos.contracts.events import EVENT_ALPHA_PROMOTED
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.alpha.guards import promotion_subject
from services.alpha.lifecycle import AlphaLifecycleManager
from services.alpha.promotion import (
    PromotionPrecheckError, execute_promotion, gather_promotion_evidence,
)
from services.governance.approval import ApprovalState, approve, open_request, review
from services.governance.approval_store import ApprovalStore
from services.monitoring.audit_store import DurableAuditLog
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.paper_trading.store import PaperTrackStore
from services.risk.research_risk.dry_run_guard import FULL_RESEARCH_RISK_ENGINES
from tests.alpha.fixtures import bound_alpha_evaluation, seed_catalog_evidence
from tests.risk.full_risk_fixture import complete_engine_reports

S = AlphaState
AGENT = Actor(actor_id="research-agent", kind=ActorKind.AGENT)


def _full_risk_result():
    engines = {name: True for name in FULL_RESEARCH_RISK_ENGINES}
    gate_count = len(FULL_RESEARCH_RISK_ENGINES)
    return {
        "decision": "RISK_APPROVED",
        "status": "APPROVED",
        "provenance": "REAL_RESEARCH_RISK_FULL",
        "gates_run": gate_count,
        "gates_passed": gate_count,
        "engines_run": engines,
        "engines_skipped": {},
        "engines_pending": {},
        # Compute all nine reports over explicit synthetic history.
        "reports": complete_engine_reports(),
    }


def _test_authority_record():
    risk = _full_risk_result()
    return {
        "run_id": "signed-authority-run",
        "test_external_signature": "VALID",
        "detail": {
            "engine_attestations": {"risk_history_ledger_replayed": True},
            "research_risk": risk,
            "research_risk_history": [{"decision_id": "decision-1", "result": risk}],
        },
    }


def _eval_report(catalog, alpha_id, trial_count, net_sharpe=1.2, repro="r1"):
    return bound_alpha_evaluation(
        catalog,
        alpha_id,
        catalog_trial_count=trial_count,
        net_sharpe=net_sharpe,
        reproducibility_hash=repro,
    )


def _seed_registry(tmp_path, *, with_artifact=True):
    """experiments db:a1 在 PAPER_TRADING + (可选)一份真实 evaluation artifact。"""
    registry = ExperimentRegistry(SqliteExperimentStore(str(tmp_path / "experiments_reg.db"), create=True))
    cat = AlphaCatalog()
    cat.register(AlphaDefinition(alpha_id="a1", name="A", factor_ids=("f1",)))
    seed_catalog_evidence(cat, "a1")
    mgr = AlphaLifecycleManager(cat)
    cc = cat.global_trial_count()
    mgr.advance("a1", S.TESTING, AGENT, timestamp=1)
    mgr.advance("a1", S.VALIDATED, AGENT, timestamp=2, evaluation=_eval_report(cat, "a1", cc))
    mgr.advance("a1", S.PAPER_TRADING, AGENT, timestamp=3, evaluation=_eval_report(cat, "a1", cc))
    registry.save(cat, now=3)
    if with_artifact:
        registry.record_evaluation(_eval_report(cat, "a1", cc), dataset_version="v1", recorded_ts=3)
    return registry


def _seed_paper(tmp_path, alpha_id="a1"):
    store = PaperTrackStore(str(tmp_path / "paper_track.db"), create=True)
    store.append_periods(alpha_id, [(1, 0.01, None, "t"), (2, 0.02, None, "t"), (3, 0.01, None, "t")])
    store.recompute_summary(alpha_id, backtest_sharpe=1.2)
    return store


def _approved_request(store, alpha_id="a1", request_id="req-1"):
    subject = promotion_subject(alpha_id, S.APPROVED)
    req = open_request(request_id, subject, Actor(actor_id="alice", kind=ActorKind.HUMAN), at=10)
    req = review(req, Actor(actor_id="bob", kind=ActorKind.HUMAN), at=11)
    req = approve(req, Actor(actor_id="carol", kind=ActorKind.HUMAN), at=12)
    store.save(req, kind="ALPHA_PROMOTION", now=12)
    return req


@pytest.fixture()
def env(tmp_path, monkeypatch):
    environment = {
        "store": ApprovalStore(str(tmp_path / "approvals.db")),
        "registry": _seed_registry(tmp_path),
        "paper": _seed_paper(tmp_path),
        "audit": DurableAuditLog(str(tmp_path / "gov_audit.db")),
        "tmp": tmp_path,
    }
    original_gather = promotion_mod.gather_promotion_evidence

    def admitted_gather(*args, **kwargs):
        return replace(
            original_gather(*args, **kwargs),
            authoritative_backtest_present=True,
            authoritative_backtest_verified=True,
            full_research_risk_9_of_9=True,
            profitability_projection_verified=True,
            authoritative_backtest_run_id="controlled-test-authority",
            backtest_sharpe=1.2,
            paper_sharpe=1.1,
        )

    monkeypatch.setattr(promotion_mod, "gather_promotion_evidence", admitted_gather)
    return environment


def _execute(env, request_id="req-1", executor="carol", now=20, nonce="n1"):
    return execute_promotion(
        store=env["store"], registry=env["registry"], audit=env["audit"],
        request_id=request_id, executor_id=executor, now=now,
        paper_store=env["paper"], nonce=nonce)


# --- 证据采集:只来自真实 artifact ---------------------------------------------

def test_gather_evidence_from_real_artifacts(env):
    ev = gather_promotion_evidence(env["registry"], env["paper"], "a1")
    assert ev.backtest_sharpe is None
    assert ev.paper_sharpe is None
    assert ev.eval_trial_count == 2
    assert ev.eval_catalog_evidence_hash is not None
    assert ev.authoritative_backtest_present is False
    assert ev.authoritative_backtest_verified is False
    assert ev.full_research_risk_9_of_9 is False
    assert ev.profitability_projection_verified is False
    assert ev.promotion_authority_ready is False
    assert "NO_AUTHORITATIVE_BACKTEST_RECORD" in ev.notes
    assert "AUTHORITATIVE_PROFITABILITY_PROJECTION_REQUIRED" in ev.notes


def test_gather_explicit_authority_requires_verifier_and_full_9_of_9(monkeypatch):
    record = _test_authority_record()
    payload = {
        "net_sharpe": 1.2,
        "catalog_trial_count": 2,
        "authoritative_backtest_record": record,
    }

    class Registry:
        @staticmethod
        def list_evaluations(_alpha_id):
            return [{"run_id": "eval-1", "evaluation_json": json.dumps(payload)}]

        @staticmethod
        def verify_evaluation(_run_id):
            return True

    class Paper:
        @staticmethod
        def track(_alpha_id):
            return {"paper_sharpe": 1.1}

    unadmitted = gather_promotion_evidence(Registry(), Paper(), "a1")
    assert unadmitted.authoritative_backtest_present is True
    assert unadmitted.authoritative_backtest_verified is False
    assert unadmitted.full_research_risk_9_of_9 is True
    assert unadmitted.promotion_authority_ready is False
    assert "AUTHORITATIVE_BACKTEST_RECORD_NOT_ADMITTED" in unadmitted.notes

    monkeypatch.setattr(
        promotion_mod,
        "selection_authority_admitted",
        lambda *, requested, authoritative_backtest_record: (
            requested
            and authoritative_backtest_record.get("test_external_signature")
            == "VALID"
        ),
    )
    ev = gather_promotion_evidence(Registry(), Paper(), "a1")
    assert ev.backtest_sharpe is None
    assert ev.paper_sharpe is None
    assert ev.authoritative_backtest_present is True
    assert ev.authoritative_backtest_verified is True
    assert ev.full_research_risk_9_of_9 is True
    assert ev.profitability_projection_verified is False
    assert ev.promotion_authority_ready is False
    assert "AUTHORITATIVE_PROFITABILITY_PROJECTION_REQUIRED" in ev.notes
    assert "INVALIDATED_PERP_LEDGER_V1" in ev.notes


def test_full_risk_result_cannot_launder_unreplayed_history_into_promotion():
    record = _test_authority_record()
    record["detail"]["engine_attestations"][
        "risk_history_ledger_replayed"
    ] = False

    assert promotion_mod._has_full_research_risk_9_of_9(record) is False


def test_gather_evidence_fail_closed_when_missing(tmp_path):
    registry = _seed_registry(tmp_path, with_artifact=False)
    ev = gather_promotion_evidence(registry, None, "a1")
    assert ev.backtest_sharpe is None and ev.eval_trial_count is None
    assert "NO_EVALUATION_ARTIFACT" in ev.notes
    assert "NO_PAPER_TRACK_STORE" in ev.notes


def test_gather_evidence_rejects_tampered_artifact(env):
    # 直接改 sqlite 里的 evaluation_json(把 net_sharpe 吹大)→ verify_evaluation 抓到 → 证据作废
    conn = env["registry"]._store._conn
    row = conn.execute("SELECT run_id, evaluation_json FROM evaluations").fetchone()
    payload = json.loads(row["evaluation_json"])
    payload["net_sharpe"] = 99.0
    conn.execute("DROP TRIGGER evaluations_no_update")
    conn.execute("UPDATE evaluations SET evaluation_json=? WHERE run_id=?",
                 (json.dumps(payload, sort_keys=True, default=str), row["run_id"]))
    conn.commit()
    ev = gather_promotion_evidence(env["registry"], env["paper"], "a1")
    assert ev.backtest_sharpe is None
    assert "EVALUATION_ARTIFACT_INVALID" in ev.notes


# --- 预检(不烧 grant)---------------------------------------------------------

def test_wrong_executor_prechecked_without_burning(env):
    _approved_request(env["store"])
    with pytest.raises(PromotionPrecheckError):
        _execute(env, executor="bob")           # bob 不是 APPROVED 步的 carol
    assert env["store"].load("req-1").state == ApprovalState.APPROVED   # 未烧


# --- 进程内单飞锁(2026-07-06 对抗式复审 CONFIRMED 修复,front1-governance 高危发现)-------
#
# 此前 lock_path 默认 None 时,execute_promotion 对"同一进程内并发调用同一 request_id"没有
# 任何保护:两个调用者各自 load() 到同一份 APPROVED 请求、各自 consume() 出逐字节相同的
# CONSUMED 步骤,ApprovalStore.save() 的严格前缀检查对"完全相同的追加"是无声 no-op(不是
# 分叉,不 raise)——于是两次 advance()+registry.save()+PROMOTION_EXECUTED 审计各自独立发生,
# 一次性 grant 被实际执行两次。三个独立审查维度各自复现了这个反例并全票确认。

def test_concurrent_execute_promotion_same_request_id_is_serialized(env, monkeypatch):
    # ApprovalStore/DurableAuditLog 已是 check_same_thread=False(线程安全);
    # SqliteExperimentStore/PaperTrackStore 默认不是——镜像 promotion_api 每次 HTTP 请求都
    # 新建 registry/paper 对象的真实模式,每个线程自己 fresh 一份,指向同一份 tmp_path 文件。
    _approved_request(env["store"])
    tmp_path = env["tmp"]
    thread_a_inside = threading.Event()
    release_thread_a = threading.Event()
    original_consume = promotion_mod.consume

    def _slow_consume(*args, **kwargs):
        thread_a_inside.set()
        assert release_thread_a.wait(timeout=5), "test setup failure: never released"
        return original_consume(*args, **kwargs)

    monkeypatch.setattr(promotion_mod, "consume", _slow_consume)

    def _fresh_call(now, nonce):
        registry = ExperimentRegistry(SqliteExperimentStore(str(tmp_path / "experiments_reg.db")))
        paper = PaperTrackStore(str(tmp_path / "paper_track.db"))
        return execute_promotion(store=env["store"], registry=registry, audit=env["audit"],
                                 request_id="req-1", executor_id="carol", now=now,
                                 paper_store=paper, nonce=nonce)

    result_a = {}

    def _run_a():
        result_a["outcome"] = _fresh_call(now=20, nonce="nA")

    thread_a = threading.Thread(target=_run_a)
    thread_a.start()
    assert thread_a_inside.wait(timeout=5), "thread A never entered the locked critical section"

    # thread A is still holding the process-internal lock (paused inside consume()) — a second
    # concurrent call for the SAME request_id must be rejected, not silently double-executed.
    with pytest.raises(PromotionPrecheckError, match="PROMOTION_IN_PROGRESS"):
        _fresh_call(now=20, nonce="nB")

    release_thread_a.set()
    thread_a.join(timeout=5)
    assert not thread_a.is_alive()
    assert result_a["outcome"].ok is True

    executed_events = [e for e in env["audit"].events if e.action == "PROMOTION_EXECUTED"]
    assert len(executed_events) == 1, "grant must be executed exactly once, not twice"
    assert env["store"].load("req-1").state == ApprovalState.CONSUMED


def test_execute_promotion_different_request_ids_do_not_block_each_other(tmp_path, monkeypatch):
    # 按 request_id 分片的锁不能变成全局互斥——两个不相干的晋级请求必须能各自独立通过。
    exp_path = str(tmp_path / "exp.db")
    paper_path = str(tmp_path / "paper_track.db")
    registry = ExperimentRegistry(SqliteExperimentStore(exp_path, create=True))
    cat = AlphaCatalog()
    store = ApprovalStore(str(tmp_path / "approvals.db"))
    audit = DurableAuditLog(str(tmp_path / "gov_audit.db"))
    paper = PaperTrackStore(paper_path, create=True)
    for alpha_id in ("a1", "a2"):
        cat.register(AlphaDefinition(alpha_id=alpha_id, name=alpha_id, factor_ids=("f1",)))
        seed_catalog_evidence(cat, alpha_id)
        mgr = AlphaLifecycleManager(cat)
        cc = cat.global_trial_count()
        mgr.advance(alpha_id, S.TESTING, AGENT, timestamp=1)
        mgr.advance(alpha_id, S.VALIDATED, AGENT, timestamp=2, evaluation=_eval_report(cat, alpha_id, cc))
        mgr.advance(alpha_id, S.PAPER_TRADING, AGENT, timestamp=3, evaluation=_eval_report(cat, alpha_id, cc))
        registry.record_evaluation(_eval_report(cat, alpha_id, cc), dataset_version="v1", recorded_ts=3)
        paper.append_periods(alpha_id, [(1, 0.01, None, "t"), (2, 0.02, None, "t"), (3, 0.01, None, "t")])
        paper.recompute_summary(alpha_id, backtest_sharpe=1.2)
    registry.save(cat, now=3)
    original_gather = promotion_mod.gather_promotion_evidence

    def admitted_gather(*args, **kwargs):
        return replace(
            original_gather(*args, **kwargs),
            authoritative_backtest_present=True,
            authoritative_backtest_verified=True,
            full_research_risk_9_of_9=True,
            profitability_projection_verified=True,
            backtest_sharpe=1.2,
            paper_sharpe=1.1,
        )

    monkeypatch.setattr(promotion_mod, "gather_promotion_evidence", admitted_gather)

    for req_id, alpha_id in (("req-1", "a1"), ("req-2", "a2")):
        subject = promotion_subject(alpha_id, S.APPROVED)
        req = open_request(req_id, subject, Actor(actor_id="alice", kind=ActorKind.HUMAN), at=10)
        req = review(req, Actor(actor_id="bob", kind=ActorKind.HUMAN), at=11)
        req = approve(req, Actor(actor_id="carol", kind=ActorKind.HUMAN), at=12)
        store.save(req, kind="ALPHA_PROMOTION", now=12)

    results = {}

    def _run(req_id):
        # 每个线程自己 fresh 一份 registry/paper(SqliteExperimentStore/PaperTrackStore 默认
        # check_same_thread=True,不能跨线程共享同一个 Python 对象);store/audit 已线程安全。
        thread_registry = ExperimentRegistry(SqliteExperimentStore(exp_path))
        thread_paper = PaperTrackStore(paper_path)
        results[req_id] = execute_promotion(
            store=store, registry=thread_registry, audit=audit, request_id=req_id,
            executor_id="carol", now=20, paper_store=thread_paper, nonce=req_id)

    threads = [threading.Thread(target=_run, args=(r,)) for r in ("req-1", "req-2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert not any(t.is_alive() for t in threads)
    assert results["req-1"].ok is True and results["req-2"].ok is True


def test_not_yet_approved_request_prechecked(env):
    subject = promotion_subject("a1", S.APPROVED)
    req = open_request("req-2", subject, Actor(actor_id="alice", kind=ActorKind.HUMAN), at=10)
    env["store"].save(req, kind="ALPHA_PROMOTION", now=10)
    with pytest.raises(PromotionPrecheckError):
        _execute(env, request_id="req-2", executor="alice")
    assert env["store"].load("req-2").state == ApprovalState.REQUESTED


def test_unknown_alpha_prechecked_without_burning(env):
    subject = promotion_subject("ghost", S.APPROVED)
    req = open_request("req-3", subject, Actor(actor_id="alice", kind=ActorKind.HUMAN), at=10)
    req = review(req, Actor(actor_id="bob", kind=ActorKind.HUMAN), at=11)
    req = approve(req, Actor(actor_id="carol", kind=ActorKind.HUMAN), at=12)
    env["store"].save(req, kind="ALPHA_PROMOTION", now=12)
    with pytest.raises(PromotionPrecheckError):
        _execute(env, request_id="req-3")
    assert env["store"].load("req-3").state == ApprovalState.APPROVED   # 未烧


def test_single_flight_lock_prechecks_busy(env):
    _approved_request(env["store"])
    lock_path = str(env["tmp"] / "experiments_reg.db.promotion.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    try:
        with pytest.raises(PromotionPrecheckError):
            execute_promotion(store=env["store"], registry=env["registry"], audit=env["audit"],
                              request_id="req-1", executor_id="carol", now=20,
                              paper_store=env["paper"], lock_path=lock_path)
        assert env["store"].load("req-1").state == ApprovalState.APPROVED   # 未烧
    finally:
        os.remove(lock_path)


def test_execute_rejects_legacy_metrics_before_burning_grant(tmp_path):
    store = ApprovalStore(str(tmp_path / "approvals.db"))
    registry = _seed_registry(tmp_path)
    paper = _seed_paper(tmp_path)
    audit = DurableAuditLog(str(tmp_path / "gov_audit.db"))
    _approved_request(store)

    with pytest.raises(
        PromotionPrecheckError, match="AUTHORITATIVE_BACKTEST_RECORD_REQUIRED"
    ):
        execute_promotion(
            store=store,
            registry=registry,
            audit=audit,
            request_id="req-1",
            executor_id="carol",
            now=20,
            paper_store=paper,
        )

    assert store.load("req-1").state == ApprovalState.APPROVED
    fresh = AlphaCatalog()
    registry.load_into(fresh)
    assert fresh.state("a1") == S.PAPER_TRADING


def test_execute_rejects_unadmitted_authority_evidence_before_burning_grant(
    tmp_path, monkeypatch
):
    store = ApprovalStore(str(tmp_path / "approvals.db"))
    registry = _seed_registry(tmp_path)
    paper = _seed_paper(tmp_path)
    audit = DurableAuditLog(str(tmp_path / "gov_audit.db"))
    _approved_request(store)
    original_gather = promotion_mod.gather_promotion_evidence
    monkeypatch.setattr(
        promotion_mod,
        "gather_promotion_evidence",
        lambda *args, **kwargs: replace(
            original_gather(*args, **kwargs),
            authoritative_backtest_present=True,
            authoritative_backtest_verified=False,
            full_research_risk_9_of_9=True,
        ),
    )

    with pytest.raises(
        PromotionPrecheckError, match="AUTHORITATIVE_BACKTEST_RECORD_NOT_ADMITTED"
    ):
        execute_promotion(
            store=store,
            registry=registry,
            audit=audit,
            request_id="req-1",
            executor_id="carol",
            now=20,
            paper_store=paper,
        )

    assert store.load("req-1").state == ApprovalState.APPROVED


def test_execute_requires_full_9_of_9_even_when_authority_verifier_admits(
    tmp_path, monkeypatch
):
    store = ApprovalStore(str(tmp_path / "approvals.db"))
    registry = _seed_registry(tmp_path)
    paper = _seed_paper(tmp_path)
    audit = DurableAuditLog(str(tmp_path / "gov_audit.db"))
    _approved_request(store)
    original_gather = promotion_mod.gather_promotion_evidence
    monkeypatch.setattr(
        promotion_mod,
        "gather_promotion_evidence",
        lambda *args, **kwargs: replace(
            original_gather(*args, **kwargs),
            authoritative_backtest_present=True,
            authoritative_backtest_verified=True,
            full_research_risk_9_of_9=False,
        ),
    )

    with pytest.raises(
        PromotionPrecheckError, match="FULL_RESEARCH_RISK_9_OF_9_REQUIRED"
    ):
        execute_promotion(
            store=store,
            registry=registry,
            audit=audit,
            request_id="req-1",
            executor_id="carol",
            now=20,
            paper_store=paper,
        )

    assert store.load("req-1").state == ApprovalState.APPROVED


def test_execute_requires_bound_profitability_projection_before_burning_grant(
    tmp_path, monkeypatch
):
    store = ApprovalStore(str(tmp_path / "approvals.db"))
    registry = _seed_registry(tmp_path)
    paper = _seed_paper(tmp_path)
    audit = DurableAuditLog(str(tmp_path / "gov_audit.db"))
    _approved_request(store)
    original_gather = promotion_mod.gather_promotion_evidence
    monkeypatch.setattr(
        promotion_mod,
        "gather_promotion_evidence",
        lambda *args, **kwargs: replace(
            original_gather(*args, **kwargs),
            authoritative_backtest_present=True,
            authoritative_backtest_verified=True,
            full_research_risk_9_of_9=True,
            profitability_projection_verified=False,
        ),
    )

    with pytest.raises(
        PromotionPrecheckError,
        match="AUTHORITATIVE_PROFITABILITY_PROJECTION_REQUIRED",
    ):
        execute_promotion(
            store=store,
            registry=registry,
            audit=audit,
            request_id="req-1",
            executor_id="carol",
            now=20,
            paper_store=paper,
        )

    assert store.load("req-1").state == ApprovalState.APPROVED


# --- 烧毁语义 + 落盘 + 审计 -----------------------------------------------------

def test_execute_promotion_happy_path_persists_catalog(env):
    _approved_request(env["store"])
    outcome = _execute(env)
    assert outcome.ok and outcome.burned
    assert outcome.alpha_id == "a1" and outcome.dst == "APPROVED"
    # experiment_registry 落盘生效:全新 catalog 重载后状态是 APPROVED
    fresh = AlphaCatalog()
    env["registry"].load_into(fresh)
    assert fresh.state("a1") == S.APPROVED
    pending = env["registry"].pending_event_outbox()
    assert len(pending) == 1
    assert pending[0]["outbox_id"] == outcome.event_outbox_id
    assert pending[0]["event"].event_type == EVENT_ALPHA_PROMOTED
    assert pending[0]["event"].payload["alpha_id"] == "a1"
    # grant 已烧
    assert env["store"].load("req-1").state == ApprovalState.CONSUMED
    # 审计落链且链自洽
    actions = [e.action for e in env["audit"].events]
    assert "PROMOTION_EXECUTED" in actions
    assert env["audit"].chain_valid()


def test_execute_promotion_burns_grant_before_advance(env, tmp_path, monkeypatch):
    """guard 拒绝(paper 证据缺失)时:grant 已 CONSUMED(烧毁),状态未推进,审计落
    PROMOTION_EXECUTION_FAILED;再次 execute → 预检拒绝(一次性,须重走三方)。"""
    _approved_request(env["store"])
    env["paper"] = PaperTrackStore(str(tmp_path / "empty_paper.db"), create=True)   # 无 track 行
    admitted_gather = promotion_mod.gather_promotion_evidence
    monkeypatch.setattr(
        promotion_mod,
        "gather_promotion_evidence",
        lambda *args, **kwargs: replace(
            admitted_gather(*args, **kwargs), paper_sharpe=None
        ),
    )
    outcome = _execute(env)
    assert not outcome.ok and outcome.burned
    assert any("NO_PAPER" in r or "PAPER_EVIDENCE" in r for r in outcome.reasons)
    assert env["store"].load("req-1").state == ApprovalState.CONSUMED
    fresh = AlphaCatalog()
    env["registry"].load_into(fresh)
    assert fresh.state("a1") == S.PAPER_TRADING          # 未推进
    actions = [e.action for e in env["audit"].events]
    assert "PROMOTION_EXECUTION_FAILED" in actions
    assert env["audit"].chain_valid()
    with pytest.raises(PromotionPrecheckError):          # grant 一次性:烧毁后不可再花
        _execute(env, now=21, nonce="n2")


def test_execute_promotion_audits_failure_with_reasons(env, tmp_path, monkeypatch):
    _approved_request(env["store"])
    env["paper"] = PaperTrackStore(str(tmp_path / "empty_paper.db"), create=True)
    admitted_gather = promotion_mod.gather_promotion_evidence
    monkeypatch.setattr(
        promotion_mod,
        "gather_promotion_evidence",
        lambda *args, **kwargs: replace(
            admitted_gather(*args, **kwargs), paper_sharpe=None
        ),
    )
    outcome = _execute(env)
    assert outcome.audit_ok
    failed = [e for e in env["audit"].events if e.action == "PROMOTION_EXECUTION_FAILED"]
    assert failed and failed[0].metadata.get("reasons")


def test_audit_event_ids_survive_duplicate_base(env):
    """DurableAuditLog 重复 event_id 即抛(评审 #5)——同基串(同 request 同秒重试)必须
    自动加序号仍落链,绝不把审计路径打成异常。"""
    from services.alpha.promotion import _audit_event
    ok1 = _audit_event(env["audit"], request_id="req-x", alpha_id="a1", actor="carol",
                       action="PROMOTION_EXECUTION_FAILED", now=20, nonce="",
                       metadata={"reasons": ["R1"]})
    ok2 = _audit_event(env["audit"], request_id="req-x", alpha_id="a1", actor="carol",
                       action="PROMOTION_EXECUTION_FAILED", now=20, nonce="",
                       metadata={"reasons": ["R1"]})
    assert ok1 and ok2
    assert len(env["audit"].events) == 2
    assert env["audit"].chain_valid()


def test_stale_eval_trial_count_rejected_end_to_end(env):
    """catalog 长大后(新 trial 落库),旧 artifact 的 trial count 过期 → guard 拒(评审 #1)。"""
    _approved_request(env["store"])
    # 往 experiments db 再灌 5 个 trial(模拟研究继续跑)
    env["registry"]._store.add_trials([f"extra{k}" for k in range(5)], first_seen_ts=15)
    outcome = _execute(env)
    assert not outcome.ok and outcome.burned
    assert "TRIAL_COUNT_UNDERSTATED_AT_APPROVAL" in outcome.reasons
