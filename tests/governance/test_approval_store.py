"""ApprovalStore 基本语义:round-trip 保链、write-only-adds(append-only,绝不 clobber)、
并发主键竞态显式映射为 ApprovalStoreConflict、摘要查询。战线①(2026-07-06)。"""
import sqlite3

import pytest

from quantos.contracts.alpha import Actor, ActorKind
from services.governance.approval import (
    ApprovalState, approve, consume, open_request, reject, review, verify_multi_party_grant,
)
from services.governance.approval_store import (
    ApprovalStore, ApprovalStoreConflict, ApprovalStoreError,
)

A = Actor(actor_id="alice", kind=ActorKind.HUMAN)
B = Actor(actor_id="bob", kind=ActorKind.HUMAN)
C = Actor(actor_id="carol", kind=ActorKind.HUMAN)
SUBJECT = "alpha-promotion:a1:APPROVED"


def _approved_req(request_id="r1"):
    req = open_request(request_id, SUBJECT, A, at=1, note="please")
    req = review(req, B, at=2, note="looks fine")
    req = approve(req, C, at=3, note="approved")
    return req


@pytest.fixture()
def store(tmp_path):
    return ApprovalStore(str(tmp_path / "approvals.db"))


def test_store_roundtrip_preserves_chain(store):
    req = consume(_approved_req(), SUBJECT, C, at=4)
    store.save(req, kind="ALPHA_PROMOTION", now=4)
    loaded = store.load("r1")
    assert loaded.request_id == req.request_id
    assert loaded.subject_id == req.subject_id
    assert loaded.steps == req.steps
    assert loaded.state == ApprovalState.CONSUMED
    # 重载对象过 fail-closed 校验器仍放行
    assert verify_multi_party_grant(loaded, SUBJECT) is True


def test_store_save_is_append_only_superset(store):
    req3 = _approved_req()
    store.save(req3, kind="ALPHA_PROMOTION", now=3)
    # 追加第 4 步再 save:只插尾部,不重写前缀
    req4 = consume(req3, SUBJECT, C, at=4)
    store.save(req4, now=4)
    loaded = store.load("r1")
    assert len(loaded.steps) == 4
    assert loaded.steps[:3] == req3.steps


def test_store_save_rejects_diverged_prefix(store):
    req = _approved_req()
    store.save(req, kind="ALPHA_PROMOTION", now=3)
    # 一个"平行历史":同 request_id 但第 2 步不同(REJECTED 而非 REVIEWED 后 approve)
    alt = open_request("r1", SUBJECT, A, at=1, note="please")
    alt = reject(alt, B, at=9, note="no")
    with pytest.raises(ApprovalStoreConflict):
        store.save(alt, now=9)
    # 库内容不变(绝不 clobber)
    loaded = store.load("r1")
    assert loaded.steps == req.steps


def test_store_save_rejects_shorter_history(store):
    req = _approved_req()
    store.save(req, now=3)
    shorter = open_request("r1", SUBJECT, A, at=1, note="please")
    with pytest.raises(ApprovalStoreConflict):
        store.save(shorter, now=9)


def test_store_save_rejects_subject_mismatch(store):
    store.save(_approved_req(), now=3)
    other = open_request("r1", "alpha-promotion:OTHER:APPROVED", A, at=1)
    with pytest.raises(ApprovalStoreConflict):
        store.save(other, now=9)


def test_integrity_error_maps_to_conflict(store, tmp_path):
    """跨进程双写者竞态:两个写者都通过了前缀检查,后到者撞 (request_id, step_index)
    主键 —— sqlite IntegrityError 必须显式映射为 ApprovalStoreConflict(评审 #5)。"""
    req = _approved_req()
    store.save(req, now=3)
    # 模拟另一写者已插入第 4 步(绕过 store,直接写库,模拟第二进程赢得竞态)
    other = consume(req, SUBJECT, C, at=4)
    raw = sqlite3.connect(str(tmp_path / "approvals.db"))
    s = other.steps[-1]
    raw.execute(
        "INSERT INTO approval_steps (request_id, step_index, state, actor_id, timestamp, note,"
        " prev_hash, entry_hash) VALUES (?,?,?,?,?,?,?,?)",
        ("r1", 3, s.state.value, s.actor_id, s.timestamp, s.note, s.prev_hash, s.entry_hash))
    raw.commit()
    raw.close()
    # 本写者的前缀读是陈旧的(monkeypatch 读),save 一个自己的第 4 步 → 主键冲突 → Conflict
    mine = consume(req, SUBJECT, B, at=5)
    real_read = store._existing_steps
    store._existing_steps = lambda request_id: real_read(request_id)[:3]
    try:
        with pytest.raises(ApprovalStoreConflict):
            store.save(mine, now=5)
    finally:
        store._existing_steps = real_read


def test_load_unknown_request_raises(store):
    with pytest.raises(ApprovalStoreError):
        store.load("nope")


def test_list_requests_and_summary(store):
    store.save(_approved_req("r1"), kind="ALPHA_PROMOTION", now=3)
    r2 = open_request("r2", SUBJECT, A, at=5)
    store.save(r2, kind="ALPHA_PROMOTION", now=5)
    rows = store.list_requests(kind="ALPHA_PROMOTION")
    assert {r["request_id"] for r in rows} == {"r1", "r2"}
    by_id = {r["request_id"]: r for r in rows}
    assert by_id["r1"]["state"] == "APPROVED"
    assert by_id["r2"]["state"] == "REQUESTED"
    assert by_id["r1"]["n_steps"] == 3
    only_requested = store.list_requests(states=("REQUESTED",))
    assert [r["request_id"] for r in only_requested] == ["r2"]
    s = store.get_summary("r1")
    assert s is not None and s["subject_id"] == SUBJECT and s["kind"] == "ALPHA_PROMOTION"
    assert store.get_summary("missing") is None
