"""promotion_api 端到端(TestClient,injectable tmp 路径):三个不同 X-Operator 走完
request→review→approve,第三人 execute 成功;同人两角 409;非批准人 execute 409 且不烧;
无 X-Operator 400;candidates/requests 只读端点形状。战线①(2026-07-06)。"""
import pytest
from fastapi.testclient import TestClient

from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition, AlphaEvaluation
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.alpha.lifecycle import AlphaLifecycleManager
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.paper_trading.store import PaperTrackStore

import services.governance.promotion_api.main as m

S = AlphaState
AGENT = Actor(actor_id="research-agent", kind=ActorKind.AGENT)


def _eval_report(alpha_id, trial_count, net_sharpe=1.2):
    return AlphaEvaluation(alpha_id=alpha_id, verdict=ValidationVerdict.PASS, deflated_sharpe=0.8,
                           pbo=0.1, catalog_trial_count=trial_count, marginal_ir=0.5,
                           net_sharpe=net_sharpe, reproducibility_hash="r1")


def _seed(tmp_path):
    registry = ExperimentRegistry(SqliteExperimentStore(str(tmp_path / "experiments_reg.db"), create=True))
    cat = AlphaCatalog()
    cat.register(AlphaDefinition(alpha_id="a1", name="A", factor_ids=("f1",)))
    mgr = AlphaLifecycleManager(cat)
    cc = cat.global_trial_count()
    mgr.advance("a1", S.TESTING, AGENT, timestamp=1)
    mgr.advance("a1", S.VALIDATED, AGENT, timestamp=2, evaluation=_eval_report("a1", cc))
    mgr.advance("a1", S.PAPER_TRADING, AGENT, timestamp=3, evaluation=_eval_report("a1", cc))
    registry.save(cat, now=3)
    registry.record_evaluation(_eval_report("a1", cc), dataset_version="v1", recorded_ts=3)
    paper = PaperTrackStore(str(tmp_path / "paper_track.db"), create=True)
    paper.append_periods("a1", [(1, 0.01, None, "t"), (2, 0.02, None, "t"), (3, 0.01, None, "t")])
    paper.recompute_summary("a1", backtest_sharpe=1.2)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(m, "_APPROVALS_DB", str(tmp_path / "approvals.db"))
    monkeypatch.setattr(m, "_EXP_DB", str(tmp_path / "experiments_reg.db"))
    monkeypatch.setattr(m, "_PAPER_DB", str(tmp_path / "paper_track.db"))
    monkeypatch.setattr(m, "_AUDIT_DB", str(tmp_path / "gov_audit.db"))
    return TestClient(m.app)


def _h(operator):
    return {"X-Gov-Token": m._GOV_TOKEN, "X-Operator": operator}


def _open(client, operator="alice", alpha_id="a1"):
    r = client.post("/api/v1/governance/promotion/requests",
                    json={"alpha_id": alpha_id, "note": "please"}, headers=_h(operator))
    assert r.status_code == 200, r.text
    return r.json()["request_id"]


def test_health_is_static_and_open(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["live_disallowed"] is True


def test_missing_operator_is_400(client):
    r = client.post("/api/v1/governance/promotion/requests",
                    json={"alpha_id": "a1"}, headers={"X-Gov-Token": m._GOV_TOKEN})
    assert r.status_code == 400


def test_candidates_lists_paper_trading_alpha_with_evidence(client):
    r = client.get("/api/v1/governance/promotion/candidates", headers=_h("alice"))
    assert r.status_code == 200
    body = r.json()
    assert body["registry_present"] is True
    cands = {c["alpha_id"]: c for c in body["candidates"]}
    assert "a1" in cands
    c = cands["a1"]
    assert c["state"] == "PAPER_TRADING"
    assert c["evidence"]["backtest_sharpe"] == 1.2
    assert c["evidence"]["paper_sharpe"] is not None
    assert c["evidence"]["eval_trial_count"] == 1


def test_open_request_for_unknown_alpha_is_409(client):
    r = client.post("/api/v1/governance/promotion/requests",
                    json={"alpha_id": "ghost", "note": ""}, headers=_h("alice"))
    assert r.status_code == 409


def test_same_operator_cannot_fill_two_roles(client):
    rid = _open(client, "alice")
    r = client.post(f"/api/v1/governance/promotion/requests/{rid}/review",
                    json={"note": "self-review"}, headers=_h("alice"))
    assert r.status_code == 409           # ApprovalViolation 原样透出


def test_three_distinct_operators_full_loop_and_execute(client):
    rid = _open(client, "alice")
    assert client.post(f"/api/v1/governance/promotion/requests/{rid}/review",
                       json={"note": "ok"}, headers=_h("bob")).status_code == 200
    assert client.post(f"/api/v1/governance/promotion/requests/{rid}/approve",
                       json={"note": "yes"}, headers=_h("carol")).status_code == 200
    r = client.post(f"/api/v1/governance/promotion/requests/{rid}/execute",
                    json={}, headers=_h("carol"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["alpha_id"] == "a1" and body["dst"] == "APPROVED"
    # 详情端点显示完整步骤时间线(谁做的、何时)
    detail = client.get(f"/api/v1/governance/promotion/requests/{rid}", headers=_h("alice")).json()
    states = [s["state"] for s in detail["steps"]]
    actors = [s["actor_id"] for s in detail["steps"]]
    assert states == ["REQUESTED", "REVIEWED", "APPROVED", "CONSUMED"]
    assert actors == ["alice", "bob", "carol", "carol"]


def test_execute_by_non_approver_is_409_and_grant_not_burned(client):
    rid = _open(client, "alice")
    client.post(f"/api/v1/governance/promotion/requests/{rid}/review", json={}, headers=_h("bob"))
    client.post(f"/api/v1/governance/promotion/requests/{rid}/approve", json={}, headers=_h("carol"))
    r = client.post(f"/api/v1/governance/promotion/requests/{rid}/execute",
                    json={}, headers=_h("bob"))          # bob 不是批准人
    assert r.status_code == 409
    detail = client.get(f"/api/v1/governance/promotion/requests/{rid}", headers=_h("alice")).json()
    assert detail["state"] == "APPROVED"                 # 未烧,carol 仍可执行


def test_reject_closes_request(client):
    rid = _open(client, "alice")
    r = client.post(f"/api/v1/governance/promotion/requests/{rid}/reject",
                    json={"note": "no"}, headers=_h("bob"))
    assert r.status_code == 200
    detail = client.get(f"/api/v1/governance/promotion/requests/{rid}", headers=_h("alice")).json()
    assert detail["state"] == "REJECTED"
    # 已否决的请求不能继续走
    assert client.post(f"/api/v1/governance/promotion/requests/{rid}/review",
                       json={}, headers=_h("carol")).status_code == 409


def test_unknown_request_detail_is_404(client):
    r = client.get("/api/v1/governance/promotion/requests/nope", headers=_h("alice"))
    assert r.status_code == 404


def test_requests_list_shows_states(client):
    rid = _open(client, "alice")
    r = client.get("/api/v1/governance/promotion/requests", headers=_h("alice"))
    assert r.status_code == 200
    rows = {x["request_id"]: x for x in r.json()["requests"]}
    assert rows[rid]["state"] == "REQUESTED"
    assert rows[rid]["subject_id"].startswith("alpha-promotion:a1:")
