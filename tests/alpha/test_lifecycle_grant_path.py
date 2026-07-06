"""lifecycle.advance(grant=) —— 多方批准驱动 →APPROVED 的新加法路径(战线①,2026-07-06)。

钉死:非 CONSUMED / subject 不匹配 / executor != APPROVED 步 actor → GuardRejected;
非人 actor 持合法 grant 仍被结构锁拒(UnauthorizedPromotion);证据门一寸不松(缺 paper
evidence / eval trial count 缺失或过期 → 拒);合法三方 CONSUMED grant + 完整证据 → APPROVED,
且转换记录 grant.request_id(评审 #2 可追溯);旧 ApprovalRecord 路径零回归(原测试文件原样跑)。"""
import pytest

from quantos.contracts.alpha import (
    Actor, ActorKind, AlphaDefinition, AlphaEvaluation, UnauthorizedPromotion,
)
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.alpha.guards import parse_promotion_subject, promotion_subject
from services.alpha.lifecycle import AlphaLifecycleManager, GuardRejected
from services.governance.approval import approve, consume, open_request, review

S = AlphaState
ALICE = Actor(actor_id="alice", kind=ActorKind.HUMAN)
BOB = Actor(actor_id="bob", kind=ActorKind.HUMAN)
CAROL = Actor(actor_id="carol", kind=ActorKind.HUMAN)
AGENT = Actor(actor_id="research-agent", kind=ActorKind.AGENT)
GOOD_PAPER = {"backtest_sharpe": 1.1, "paper_sharpe": 1.0}


def _eval(alpha_id, trial_count):
    return AlphaEvaluation(alpha_id=alpha_id, verdict=ValidationVerdict.PASS, deflated_sharpe=0.8,
                           pbo=0.1, catalog_trial_count=trial_count, marginal_ir=0.5)


def _paper_mgr():
    cat = AlphaCatalog()
    cat.register(AlphaDefinition(alpha_id="a1", name="A", factor_ids=("f1",)))
    mgr = AlphaLifecycleManager(cat)
    cc = cat.global_trial_count()
    mgr.advance("a1", S.TESTING, AGENT, timestamp=1)
    mgr.advance("a1", S.VALIDATED, AGENT, timestamp=2, evaluation=_eval("a1", cc))
    mgr.advance("a1", S.PAPER_TRADING, AGENT, timestamp=3, evaluation=_eval("a1", cc))
    return mgr, cat


def _grant(alpha_id="a1", *, consumed=True, executor=CAROL, request_id="req-1"):
    subject = promotion_subject(alpha_id, S.APPROVED)
    req = open_request(request_id, subject, ALICE, at=10)
    req = review(req, BOB, at=11)
    req = approve(req, CAROL, at=12)
    if consumed:
        req = consume(req, subject, executor, at=13)
    return req


# --- subject 格式 ------------------------------------------------------------

def test_promotion_subject_roundtrip():
    subject = promotion_subject("a:b:1", S.APPROVED)
    assert parse_promotion_subject(subject) == ("a:b:1", S.APPROVED)
    with pytest.raises(ValueError):
        parse_promotion_subject("not-a-promotion-subject")
    with pytest.raises(ValueError):
        parse_promotion_subject("alpha-promotion:a1:NOT_A_STATE")


# --- grant 路径的拒绝面 --------------------------------------------------------

def test_non_consumed_grant_rejected():
    mgr, _ = _paper_mgr()
    grant = _grant(consumed=False)          # 停在 APPROVED,未 consume
    with pytest.raises(GuardRejected) as e:
        mgr.advance("a1", S.APPROVED, CAROL, timestamp=20, grant=grant,
                    paper_evidence=GOOD_PAPER, eval_trial_count=1)
    assert "MULTI_PARTY_GRANT_INVALID" in e.value.reasons


def test_subject_mismatch_rejected():
    mgr, _ = _paper_mgr()
    grant = _grant(alpha_id="OTHER")        # 给别的 alpha 的批准花不到 a1 上
    with pytest.raises(GuardRejected) as e:
        mgr.advance("a1", S.APPROVED, CAROL, timestamp=20, grant=grant,
                    paper_evidence=GOOD_PAPER, eval_trial_count=1)
    assert "MULTI_PARTY_GRANT_INVALID" in e.value.reasons


def test_executor_must_be_grant_approver():
    mgr, _ = _paper_mgr()
    grant = _grant()                        # APPROVED 步是 carol
    with pytest.raises(GuardRejected) as e:  # bob 不能代花 carol 的批准
        mgr.advance("a1", S.APPROVED, BOB, timestamp=20, grant=grant,
                    paper_evidence=GOOD_PAPER, eval_trial_count=1)
    assert "APPROVER_IDENTITY_MISMATCH" in e.value.reasons


def test_grant_path_nonhuman_actor_still_blocked():
    """AGENT 持合法 grant 调 advance → 结构锁先于 guard(UnauthorizedPromotion)。"""
    mgr, _ = _paper_mgr()
    grant = _grant()
    with pytest.raises(UnauthorizedPromotion):
        mgr.advance("a1", S.APPROVED, AGENT, timestamp=20, grant=grant,
                    paper_evidence=GOOD_PAPER, eval_trial_count=1)


def test_grant_path_does_not_relax_paper_evidence():
    mgr, _ = _paper_mgr()
    grant = _grant()
    with pytest.raises(GuardRejected) as e:  # 缺 paper evidence
        mgr.advance("a1", S.APPROVED, CAROL, timestamp=20, grant=grant,
                    paper_evidence=None, eval_trial_count=1)
    assert "NO_PAPER_EVIDENCE" in e.value.reasons
    with pytest.raises(GuardRejected) as e:  # 不完整(backtest_sharpe 缺)—— 证据必须来自真实 artifact,不代填
        mgr.advance("a1", S.APPROVED, CAROL, timestamp=21, grant=grant,
                    paper_evidence={"backtest_sharpe": None, "paper_sharpe": 1.0},
                    eval_trial_count=1)
    assert "PAPER_EVIDENCE_INCOMPLETE" in e.value.reasons


def test_grant_path_requires_eval_trial_count():
    """评审 #1:持久化世界里新建 manager 的 _eval_trial_count 恒空——grant 路径必须显式
    喂来自 evaluation artifact 的 trial count,取不到 → fail-closed 拒绝,绝不静默跳过。"""
    mgr, _ = _paper_mgr()
    fresh = AlphaLifecycleManager(mgr.catalog)   # 新进程模拟:进程内 eval 计数为空
    grant = _grant()
    with pytest.raises(GuardRejected) as e:
        fresh.advance("a1", S.APPROVED, CAROL, timestamp=20, grant=grant,
                      paper_evidence=GOOD_PAPER)
    assert "NO_EVALUATION_TRIAL_COUNT" in e.value.reasons


def test_grant_path_rejects_stale_trial_count():
    """catalog 长大后,旧评估的 alpha 走 grant 路径必须被拒(评审 #1 显式测试)。"""
    mgr, cat = _paper_mgr()
    for k in range(5):
        cat.register_trial(f"c{k}")              # catalog 从 1 长到 6
    grant = _grant()
    with pytest.raises(GuardRejected) as e:
        mgr.advance("a1", S.APPROVED, CAROL, timestamp=20, grant=grant,
                    paper_evidence=GOOD_PAPER, eval_trial_count=1)
    assert "TRIAL_COUNT_UNDERSTATED_AT_APPROVAL" in e.value.reasons


# --- 放行路径 + 可追溯性 -------------------------------------------------------

def test_valid_grant_advances_and_records_request_id():
    mgr, cat = _paper_mgr()
    grant = _grant()
    tr = mgr.advance("a1", S.APPROVED, CAROL, timestamp=20, grant=grant,
                     paper_evidence=GOOD_PAPER, eval_trial_count=cat.global_trial_count())
    assert cat.state("a1") == S.APPROVED
    # 评审 #2:APPROVED 转换在转换链上与三方批准可追溯地连着
    assert tr.approval_id == grant.request_id == "req-1"
    assert mgr.audit_chain_valid()


def test_legacy_approval_record_path_unchanged():
    """不传 grant 的旧 ApprovalRecord 路径一字不动(零回归,原测试文件另行全量跑)。"""
    from quantos.contracts.governance import ApprovalRecord
    mgr, cat = _paper_mgr()
    ok = ApprovalRecord(approval_id="z", subject_id="a1", approver="alice", approved=True, timestamp=13)
    tr = mgr.advance("a1", S.APPROVED, ALICE, timestamp=13, approval=ok, paper_evidence=GOOD_PAPER)
    assert cat.state("a1") == S.APPROVED and tr.approval_id == "z"
