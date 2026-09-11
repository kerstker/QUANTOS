import json

import pytest
from fastapi.testclient import TestClient

from services.console.main import app
from services.console.api import binance_demo_views as views
from tests.tools.test_binance_demo_readiness import NOW, transport
from tools.binance_demo_readiness import collect


def test_missing_receipt_is_unknown_and_get_creates_no_files(tmp_path, monkeypatch):
    path = tmp_path / "absent.json"
    monkeypatch.setattr(views, "_receipt_path", lambda: path)
    body = TestClient(app).get("/api/v1/console/binance-demo").json()
    assert body["snapshot_status"] == "NO_SNAPSHOT"
    assert body["public"] is None and body["account"] is None
    assert body["submission_enabled"] is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(("age", "status"), [(0, "REAL"), (120001, "STALE"), (-1, "DATA_BLOCKED")])
def test_receipt_freshness_does_not_grant_submission(tmp_path, age, status):
    path = tmp_path / "receipt.json"
    receipt = collect(get=transport([]), clock_ms=lambda: NOW)
    path.write_text(receipt.model_dump_json())
    before = path.read_bytes()
    body = views.build_snapshot(path=path, as_of_ms=NOW + age)
    assert body["snapshot_status"] == status
    assert body["submission_enabled"] is False and body["authoritative"] is False
    assert body["execution_reason"] == "LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE"
    assert path.read_bytes() == before


@pytest.mark.parametrize("mutate", [lambda b: b.update(submission_enabled=True),
                                    lambda b: b.update(rest_base="https://fapi.binance.com"),
                                    lambda b: b.update(api_key="do-not-leak"),
                                    lambda b: b["public"].update(symbols=[]),
                                    lambda b: b["account"].update(status="READY")])
def test_malformed_or_misleading_receipt_is_rejected(tmp_path, mutate):
    path = tmp_path / "receipt.json"
    body = collect(get=transport([]), clock_ms=lambda: NOW).model_dump()
    mutate(body)
    path.write_text(json.dumps(body))
    result = views.build_snapshot(path=path, as_of_ms=NOW)
    assert result["snapshot_status"] == "CORRUPT_OR_UNREADABLE"
    assert result["public"] is None and result["account"] is None
    assert "do-not-leak" not in json.dumps(result)


def test_saved_failure_keeps_successful_public_diagnostics_but_no_account_readiness(tmp_path):
    path = tmp_path / "receipt.json"
    receipt = collect(get=transport([]), clock_ms=lambda: NOW, account_read=True, environ={})
    path.write_text(receipt.model_dump_json())
    body = views.build_snapshot(path=path, as_of_ms=NOW)
    assert body["snapshot_status"] == "DATA_BLOCKED"
    assert body["public"]["status"] == "READY"
    assert body["account"]["reason"] == "CREDENTIALS_MISSING"
    assert body["submission_enabled"] is False
