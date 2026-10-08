"""ApprovalStore 防篡改:字段篡改 / 重排与跨请求拼接 / 尾部截断(复活已否决请求)/
同 index 重放 —— 全部 fail-closed 拒绝(load raise ApprovalStoreError)。战线①(2026-07-06)。"""
import sqlite3

import pytest

from quantos.contracts.alpha import Actor, ActorKind
from services.governance.approval import approve, open_request, reject, review
from services.governance.approval_store import ApprovalStore, ApprovalStoreConflict, ApprovalStoreError

A = Actor(actor_id="alice", kind=ActorKind.HUMAN)
B = Actor(actor_id="bob", kind=ActorKind.HUMAN)
C = Actor(actor_id="carol", kind=ActorKind.HUMAN)
SUBJECT = "alpha-promotion:a1:APPROVED"


def _db(tmp_path):
    return str(tmp_path / "approvals.db")


def _seed(tmp_path, request_id="r1", rejected_tail=False):
    store = ApprovalStore(_db(tmp_path))
    req = open_request(request_id, SUBJECT, A, at=1, note="please")
    req = review(req, B, at=2, note="ok")
    req = reject(req, B, at=3, note="no") if rejected_tail else approve(req, C, at=3, note="yes")
    store.save(req, kind="ALPHA_PROMOTION", now=3)
    return store


def _raw(tmp_path):
    return sqlite3.connect(_db(tmp_path))


def test_load_rejects_field_tamper(tmp_path):
    store = _seed(tmp_path)
    raw = _raw(tmp_path)
    raw.execute("UPDATE approval_steps SET actor_id='mallory' WHERE request_id='r1' AND step_index=2")
    raw.commit(); raw.close()
    with pytest.raises(ApprovalStoreError):
        store.load("r1")


def test_load_rejects_note_tamper(tmp_path):
    store = _seed(tmp_path)
    raw = _raw(tmp_path)
    raw.execute("UPDATE approval_steps SET note='forged' WHERE request_id='r1' AND step_index=0")
    raw.commit(); raw.close()
    with pytest.raises(ApprovalStoreError):
        store.load("r1")


def test_load_rejects_reorder(tmp_path):
    store = _seed(tmp_path)
    raw = _raw(tmp_path)
    # 交换 0/1 两步的 step_index(经由临时 index 绕过主键)
    raw.execute("UPDATE approval_steps SET step_index=99 WHERE request_id='r1' AND step_index=0")
    raw.execute("UPDATE approval_steps SET step_index=0 WHERE request_id='r1' AND step_index=1")
    raw.execute("UPDATE approval_steps SET step_index=1 WHERE request_id='r1' AND step_index=99")
    raw.commit(); raw.close()
    with pytest.raises(ApprovalStoreError):
        store.load("r1")


def test_load_rejects_cross_request_splice(tmp_path):
    store = _seed(tmp_path)
    # 第二个请求,拿它的步骤拼接进 r1
    req2 = open_request("r2", "alpha-promotion:a2:APPROVED", A, at=7)
    store.save(req2, now=7)
    raw = _raw(tmp_path)
    raw.execute(
        "INSERT INTO approval_steps (request_id, step_index, state, actor_id, timestamp, note,"
        " prev_hash, entry_hash) SELECT 'r1', 3, state, actor_id, timestamp, note, prev_hash,"
        " entry_hash FROM approval_steps WHERE request_id='r2' AND step_index=0")
    raw.commit(); raw.close()
    with pytest.raises(ApprovalStoreError):
        store.load("r1")


def test_load_rejects_tail_truncation_resurrects_rejected(tmp_path):
    """删掉尾部 REJECTED 步(不更新承诺列)= 把已否决请求"复活"回 REVIEWED —— 链头承诺
    (n_steps + head_hash)必须抓住它。"""
    store = _seed(tmp_path, rejected_tail=True)
    raw = _raw(tmp_path)
    raw.execute("DELETE FROM approval_steps WHERE request_id='r1' AND step_index=2")
    raw.commit(); raw.close()
    with pytest.raises(ApprovalStoreError):
        store.load("r1")


def test_load_rejects_commitment_tamper(tmp_path):
    """篡改承诺列本身(n_steps/head_hash 改成与被截断后的链一致)仍然要求逐条 entry_hash
    与 prev_hash 链自洽——单改承诺列而不改步骤会先在链校验处失败;这里改 n_steps 抓不一致。"""
    store = _seed(tmp_path)
    raw = _raw(tmp_path)
    raw.execute("UPDATE approval_requests SET n_steps=2 WHERE request_id='r1'")
    raw.commit(); raw.close()
    with pytest.raises(ApprovalStoreError):
        store.load("r1")


def test_replay_same_step_index_rejected(tmp_path):
    _seed(tmp_path)
    raw = _raw(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute(
            "INSERT INTO approval_steps (request_id, step_index, state, actor_id, timestamp, note,"
            " prev_hash, entry_hash) VALUES ('r1', 0, 'REQUESTED', 'mallory', 1, '', '', 'x')")
    raw.close()


def test_hole_in_step_indexes_rejected(tmp_path):
    store = _seed(tmp_path)
    raw = _raw(tmp_path)
    raw.execute("UPDATE approval_requests SET n_steps=2 WHERE request_id='r1'")
    raw.execute("DELETE FROM approval_steps WHERE request_id='r1' AND step_index=1")
    raw.commit(); raw.close()
    with pytest.raises(ApprovalStoreError):
        store.load("r1")
