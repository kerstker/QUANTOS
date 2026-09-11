import json

import pytest
from fastapi.testclient import TestClient

from services.console.main import app
from services.console.api import binance_demo_views as views
from tests.tools.test_binance_demo_readiness import NOW, transport
from tools.binance_demo_readiness import collect


def test_demo_console_rebuilds_saved_receipts_without_public_probe_or_writes(tmp_path, monkeypatch):
    from services.research.demo_execution_journal import DemoExecutionJournal
    from tests.research.test_demo_execution_journal import manifest
    from tests.research.test_binance_order_receipts import native_spec
    path = tmp_path / "journal.db"
    with DemoExecutionJournal(path, manifest=manifest()) as journal:
        journal.append(native_spec()["raw_observed"]["messages"])
    monkeypatch.setattr(views, "_execution_journal_path", lambda: path)
    monkeypatch.setattr(views, "_receipt_path", lambda: tmp_path / "absent.json")
    before = path.read_bytes()
    result = TestClient(app).get("/api/v1/console/binance-demo").json()
    assert result["snapshot_status"] == "NO_SNAPSHOT"
    assert result["execution"]["status"] == "AVAILABLE"
    assert result["execution"]["report"]["reconciliation"]["fee"] == "0.0198"
    assert result["ledger_reconciliation_status"] == "NOT_CHECKED"
    assert path.read_bytes() == before


def test_corrupt_execution_journal_is_not_silently_omitted(tmp_path):
    path = tmp_path / "journal.db"
    path.write_bytes(b"not sqlite")
    result = views.build_execution_snapshot(path=path, as_of_ms=1000)
    assert result == {"status":"CORRUPT_OR_UNREADABLE", "report":None}
    assert path.read_bytes() == b"not sqlite"


def test_saved_query_comparison_is_read_only_and_separate_from_execution_ready(tmp_path,monkeypatch):
    from tests.research.test_demo_order_recovery import fixture, capture
    from services.research.demo_order_recovery import build_query_plan, save_capture
    with fixture(tmp_path,partial=True) as journal:
        save_capture(tmp_path/'queries',capture(build_query_plan(journal)))
    monkeypatch.setattr(views,'_execution_journal_path',lambda:tmp_path/'journal.db')
    monkeypatch.setattr(views,'_order_query_directory',lambda:tmp_path/'queries')
    monkeypatch.setattr(views.time,'time_ns',lambda:200_000_000)
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    body=TestClient(app).get('/api/v1/console/binance-demo').json()
    assert body['order_recovery']['report']['orders'][0]['status']=='BACKFILL_REQUIRED'
    assert body['order_recovery']['report']['new_command_allowed'] is False
    assert body['order_stream_status']=='NOT_CHECKED'
    assert {str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}==before


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
