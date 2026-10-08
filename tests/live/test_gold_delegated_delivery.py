"""Retained private preparation reaches the original issuer and send gate.

Custody/source acceptance and venue transport are isolated by the original
fixtures. Preparation, replay, risk, reservation and signing use native code.
"""

from contextlib import closing
import json

import pytest

from live.execution import demo_issuer, demo_prepared_execution
from live.execution.demo_execution_session import ExecutionSession, SessionRejected
from live.execution.demo_issuer_store import IssuerStore
from live.exchange import binance_demo_exact
from tests.tools.test_gold_delegated_request_prepare import (
    call,
    case as case,
    original_evidence as original_evidence,
    prepared_case as prepared_case,
)
from tools.demo_task import same_declarations


def test_native_retained_preparer_to_independent_signature_and_single_send(prepared_case, monkeypatch):
    f = prepared_case
    monkeypatch.setattr(demo_prepared_execution, "now_ms", lambda: f.clock[0])
    monkeypatch.setattr(demo_prepared_execution, "_require_state_custody", lambda *a, **kw: None)
    credentials, posts = [], []

    def isolated_credentials(reference):
        assert reference == f.base_policy["account_ref_sha256"]
        credentials.append(reference)
        return "isolated-key", "isolated-secret"

    def isolated_post(body, headers):
        assert len(f.stores["dispatch"].wire_snapshot()) == 1
        assert len(f.stores["recovery"].pending()) == 1
        posts.append(body)
        return 200, json.dumps({
            "symbol": "XAUUSDT",
            "orderId": 123,
            "clientOrderId": f.task["orders"][0]["newClientOrderId"],
        }).encode()

    monkeypatch.setattr(binance_demo_exact, "_credentials", isolated_credentials)
    monkeypatch.setattr(binance_demo_exact, "_http_once", isolated_post)
    status, prepared = call(f)
    assert status == 0, prepared
    assert f.captures == []
    same_declarations(prepared["request"], f.task)
    with closing(IssuerStore(f.ledger)) as issuer:
        session = ExecutionSession(**f.stores, evidence_directory=f.root / "delivery-evidence")
        try:
            handoff = session.propose(json.dumps(prepared["request"]).encode(), now_ms=f.clock[0])
            grant = demo_issuer.issue_delegated_submission(
                handoff, policy=f.base_policy, registry=f.registry, public_material=f.current_enable,
                key=f.private, store=issuer, now_ms=f.clock[0], clock_ms=lambda: f.clock[0],
            )
            result = session.submit(grant["proposal_sha256"], json.dumps(grant["envelope"]).encode())
            assert result["execution"]["status"] == "WIRE_ATTEMPTS_RECORDED"
            assert result["cash_reconciled"] is False
            assert [row["kind"] for row in issuer.snapshot()] == ["RESERVED", "RESERVED", "SIGNED"]
            assert len(credentials) == len(posts) == 1
            with pytest.raises(SessionRejected):
                session.submit(grant["proposal_sha256"], json.dumps(grant["envelope"]).encode())
            assert len(posts) == 1
        finally:
            session.close()
