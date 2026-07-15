"""Promotion API end-to-end tests with externally verified identities.

Core JWT signature and mTLS binding are tested separately. These tests inject
frozen identities at the FastAPI dependency boundary and prove role enforcement,
three-subject separation, immutable actor IDs, and one-way execution semantics.
"""

from pathlib import Path

import pytest
from fastapi import Header, HTTPException
from fastapi.testclient import TestClient

from quantos.contracts.alpha import Actor, ActorKind, AlphaDefinition, AlphaEvaluation
from quantos.contracts.identity import (
    GovernanceRole,
    StrongIdentityAssurance,
    VerifiedIdentity,
)
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.alpha.lifecycle import AlphaLifecycleManager
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.paper_trading.store import PaperTrackStore
from services.governance.identity_authorization import (
    IdentityAuthorizationGrant,
    IdentityAuthorizationRegistry,
    load_identity_authorization_registry,
)
from services.governance.identity_authorization_store import (
    IdentityAuthorizationStore,
)

import services.promotion_api.main as m

S = AlphaState
AGENT = Actor(actor_id="research-agent", kind=ActorKind.AGENT)
ISSUER = "https://identity.example.test"
ALL_ROLES = tuple(GovernanceRole)


def _eval_report(alpha_id, trial_count, net_sharpe=1.2):
    return AlphaEvaluation(
        alpha_id=alpha_id,
        verdict=ValidationVerdict.PASS,
        deflated_sharpe=0.8,
        pbo=0.1,
        catalog_trial_count=trial_count,
        marginal_ir=0.5,
        net_sharpe=net_sharpe,
        reproducibility_hash="r1",
    )


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


def _verified_identity(subject: str, roles: tuple[GovernanceRole, ...]) -> VerifiedIdentity:
    return VerifiedIdentity(
        issuer=ISSUER,
        subject=subject,
        audience="quantos-governance",
        roles=roles,
        assurance=StrongIdentityAssurance.OIDC_MTLS_MFA,
        acr="urn:quantos:aal2",
        amr=("pwd", "mfa"),
        issued_at=10,
        not_before=10,
        authentication_time=9,
        expires_at=2_000_000_000,
        token_id=f"token-{subject}",
        certificate_sha256="a" * 64,
    )


def _identity_dependency(authorization: str = Header(default=None, alias="Authorization")):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "strong identity rejected")
    encoded = authorization.removeprefix("Bearer ")
    subject, separator, role_text = encoded.partition("::")
    if not separator:
        raise HTTPException(401, "strong identity rejected")
    try:
        roles = tuple(GovernanceRole(value) for value in role_text.split(",") if value)
    except ValueError as exc:
        raise HTTPException(401, "strong identity rejected") from exc
    return _verified_identity(subject, roles)


def _seed_identity_authorization(tmp_path: Path) -> tuple[Path, Path]:
    grants = {
        subject: IdentityAuthorizationGrant(
            issuer=ISSUER,
            subject=subject,
            principal_kind="HUMAN",
            enabled=subject != "revoked",
            revoked=subject == "revoked",
            roles=ALL_ROLES,
            tokens_valid_after_ms=0,
            access_review_expires_at_ms=2_000_000_000_000,
        )
        for subject in ("alice", "bob", "carol", "reader", "requester", "revoked")
    }
    workload = IdentityAuthorizationGrant(
        issuer=ISSUER,
        subject="research-agent",
        principal_kind="WORKLOAD",
        enabled=True,
        revoked=False,
        roles=(GovernanceRole.PROMOTION_READER,),
        tokens_valid_after_ms=0,
        access_review_expires_at_ms=2_000_000_000_000,
    )
    grants["research-agent"] = workload
    registry = IdentityAuthorizationRegistry(
        generation=0,
        principals={grant.actor_id: grant for grant in grants.values()},
    )
    registry_path = tmp_path / "identity_authorization_registry.json"
    registry_path.write_text(registry.model_dump_json(indent=2), encoding="utf-8")
    store_path = tmp_path / "identity_authorization.db"
    store = IdentityAuthorizationStore(str(store_path), create=True)
    store.append_registry_snapshot(
        registry_path.read_bytes(),
        recorded_at_ms=1,
        expected_head=None,
    )
    store.close()
    return registry_path, store_path


@pytest.fixture()
def client(tmp_path, monkeypatch):
    _seed(tmp_path)
    authorization_registry, authorization_store = _seed_identity_authorization(tmp_path)
    monkeypatch.setattr(m, "_APPROVALS_DB", str(tmp_path / "approvals.db"))
    monkeypatch.setattr(m, "_EXP_DB", str(tmp_path / "experiments_reg.db"))
    monkeypatch.setattr(m, "_PAPER_DB", str(tmp_path / "paper_track.db"))
    monkeypatch.setattr(m, "_AUDIT_DB", str(tmp_path / "gov_audit.db"))
    monkeypatch.setattr(m, "_EVENT_DB", str(tmp_path / "domain_events.db"))
    monkeypatch.setattr(
        m,
        "_EVENT_PROJECTION_DB",
        str(tmp_path / "domain_event_projection.db"),
    )
    monkeypatch.setattr(
        m,
        "_IDENTITY_AUTHORIZATION_REGISTRY",
        str(authorization_registry),
    )
    monkeypatch.setattr(
        m,
        "_IDENTITY_AUTHORIZATION_DB",
        str(authorization_store),
    )
    m.app.dependency_overrides[m.require_governance_identity] = _identity_dependency
    try:
        yield TestClient(m.app)
    finally:
        m.app.dependency_overrides.clear()


def _h(operator, roles=ALL_ROLES):
    role_text = ",".join(role.value for role in roles)
    return {"Authorization": f"Bearer {operator}::{role_text}"}


def _open(client, operator="alice", alpha_id="a1"):
    r = client.post(
        "/api/v1/governance/promotion/requests", json={"alpha_id": alpha_id, "note": "please"}, headers=_h(operator)
    )
    assert r.status_code == 200, r.text
    return r.json()["request_id"]


def test_health_is_static_and_open(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["live_disallowed"] is True


def test_legacy_self_asserted_operator_without_bearer_is_401(client):
    r = client.post("/api/v1/governance/promotion/requests", json={"alpha_id": "a1"}, headers={"X-Operator": "alice"})
    assert r.status_code == 401


def test_reader_cannot_open_and_requester_cannot_approve(client):
    r = client.post(
        "/api/v1/governance/promotion/requests",
        json={"alpha_id": "a1"},
        headers=_h("reader", (GovernanceRole.PROMOTION_READER,)),
    )
    assert r.status_code == 403
    rid = _open(client, "alice")
    client.post(
        f"/api/v1/governance/promotion/requests/{rid}/review",
        json={},
        headers=_h("bob"),
    )
    r = client.post(
        f"/api/v1/governance/promotion/requests/{rid}/approve",
        json={},
        headers=_h("requester", (GovernanceRole.PROMOTION_REQUESTER,)),
    )
    assert r.status_code == 403


def test_token_roles_cannot_self_enroll_an_unregistered_subject(client):
    response = client.get(
        "/api/v1/governance/promotion/requests",
        headers=_h("mallory"),
    )

    assert response.status_code == 403
    assert "PRINCIPAL_NOT_REGISTERED" in response.text
    store = IdentityAuthorizationStore(m._IDENTITY_AUTHORIZATION_DB, create=False)
    assert store.decisions()[-1].decision.reason_code == "PRINCIPAL_NOT_REGISTERED"
    assert store.decisions()[-1].decision.verdict == "DENY"
    store.close()


def test_revoked_human_and_workload_human_action_are_denied(client):
    revoked = client.get(
        "/api/v1/governance/promotion/requests",
        headers=_h("revoked", (GovernanceRole.PROMOTION_READER,)),
    )
    workload = client.post(
        "/api/v1/governance/promotion/requests",
        json={"alpha_id": "a1"},
        headers=_h(
            "research-agent",
            (GovernanceRole.PROMOTION_REQUESTER,),
        ),
    )

    assert revoked.status_code == 403
    assert "PRINCIPAL_REVOKED" in revoked.text
    assert workload.status_code == 403
    assert "WORKLOAD_HUMAN_ACTION_FORBIDDEN" in workload.text


def test_missing_authorization_authority_fails_closed(client):
    Path(m._IDENTITY_AUTHORIZATION_DB).unlink()

    response = client.get(
        "/api/v1/governance/promotion/requests",
        headers=_h("alice", (GovernanceRole.PROMOTION_READER,)),
    )

    assert response.status_code == 503
    assert "identity authorization unavailable" in response.text


def test_registry_revocation_after_evaluation_persists_deny_not_allow(
    client,
    monkeypatch,
):
    original_record = IdentityAuthorizationStore.record
    raced = False

    def revoke_before_record(self, decision, **kwargs):
        nonlocal raced
        if not raced and decision.verdict == "ALLOW":
            raced = True
            registry_path = Path(m._IDENTITY_AUTHORIZATION_REGISTRY)
            current_registry = load_identity_authorization_registry(registry_path.read_bytes())
            principals = dict(current_registry.principals)
            alice_id = f"{ISSUER}|alice"
            principals[alice_id] = principals[alice_id].model_copy(update={"enabled": False, "revoked": True})
            revoked_registry = current_registry.model_copy(
                update={
                    "generation": current_registry.generation + 1,
                    "principals": principals,
                }
            )
            registry_path.write_text(
                revoked_registry.model_dump_json(indent=2),
                encoding="utf-8",
            )
            writer = IdentityAuthorizationStore(
                m._IDENTITY_AUTHORIZATION_DB,
                create=False,
            )
            writer.append_registry_snapshot(
                registry_path.read_bytes(),
                recorded_at_ms=kwargs["recorded_at_ms"],
                expected_head=writer.registry_head(),
            )
            writer.close()
        return original_record(self, decision, **kwargs)

    monkeypatch.setattr(
        IdentityAuthorizationStore,
        "record",
        revoke_before_record,
    )

    response = client.get(
        "/api/v1/governance/promotion/requests",
        headers=_h("alice", (GovernanceRole.PROMOTION_READER,)),
    )

    assert response.status_code == 503
    store = IdentityAuthorizationStore(m._IDENTITY_AUTHORIZATION_DB, create=False)
    retained = store.decisions()
    assert len(retained) == 1
    assert retained[0].decision.verdict == "DENY"
    assert retained[0].decision.reason_code == ("REGISTRY_HEAD_CHANGED_DURING_AUTHORIZATION")
    assert store.registry_head().authority_event_seq < retained[0].authority_event_seq
    store.close()


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
    r = client.post(
        "/api/v1/governance/promotion/requests", json={"alpha_id": "ghost", "note": ""}, headers=_h("alice")
    )
    assert r.status_code == 409


def test_same_operator_cannot_fill_two_roles(client):
    rid = _open(client, "alice")
    r = client.post(
        f"/api/v1/governance/promotion/requests/{rid}/review", json={"note": "self-review"}, headers=_h("alice")
    )
    assert r.status_code == 409  # ApprovalViolation 原样透出


def test_three_distinct_operators_full_loop_and_execute(client):
    rid = _open(client, "alice")
    assert (
        client.post(
            f"/api/v1/governance/promotion/requests/{rid}/review", json={"note": "ok"}, headers=_h("bob")
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/governance/promotion/requests/{rid}/approve", json={"note": "yes"}, headers=_h("carol")
        ).status_code
        == 200
    )
    r = client.post(f"/api/v1/governance/promotion/requests/{rid}/execute", json={}, headers=_h("carol"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["alpha_id"] == "a1" and body["dst"] == "APPROVED"
    assert body["event_outbox_id"]
    assert body["event_delivery"]["ok"] is True
    assert body["event_delivery"]["dispatch"] == {"dispatched": 1, "pending": 0}
    # 详情端点显示完整步骤时间线(谁做的、何时)
    detail = client.get(f"/api/v1/governance/promotion/requests/{rid}", headers=_h("alice")).json()
    states = [s["state"] for s in detail["steps"]]
    actors = [s["actor_id"] for s in detail["steps"]]
    assert states == ["REQUESTED", "REVIEWED", "APPROVED", "CONSUMED"]
    assert actors == [
        f"{ISSUER}|alice",
        f"{ISSUER}|bob",
        f"{ISSUER}|carol",
        f"{ISSUER}|carol",
    ]


def test_execute_by_non_approver_is_409_and_grant_not_burned(client):
    rid = _open(client, "alice")
    client.post(f"/api/v1/governance/promotion/requests/{rid}/review", json={}, headers=_h("bob"))
    client.post(f"/api/v1/governance/promotion/requests/{rid}/approve", json={}, headers=_h("carol"))
    r = client.post(
        f"/api/v1/governance/promotion/requests/{rid}/execute", json={}, headers=_h("bob")
    )  # bob 不是批准人
    assert r.status_code == 409
    detail = client.get(f"/api/v1/governance/promotion/requests/{rid}", headers=_h("alice")).json()
    assert detail["state"] == "APPROVED"  # 未烧,carol 仍可执行


def test_reject_closes_request(client):
    rid = _open(client, "alice")
    r = client.post(f"/api/v1/governance/promotion/requests/{rid}/reject", json={"note": "no"}, headers=_h("bob"))
    assert r.status_code == 200
    detail = client.get(f"/api/v1/governance/promotion/requests/{rid}", headers=_h("alice")).json()
    assert detail["state"] == "REJECTED"
    # 已否决的请求不能继续走
    assert (
        client.post(f"/api/v1/governance/promotion/requests/{rid}/review", json={}, headers=_h("carol")).status_code
        == 409
    )


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
