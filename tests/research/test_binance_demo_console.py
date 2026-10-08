import json

import pytest


def test_cash_window_console_keeps_fixed_window_and_readonly_scope(tmp_path,monkeypatch):
    from services.research.demo_cash_window import save_capture
    from tests.research.test_demo_cash_window import fixture, NOW as at
    directory=tmp_path/'cash-window';save_capture(directory,fixture())
    before={p.name:p.read_bytes() for p in directory.iterdir()}
    monkeypatch.setattr(views,'_cash_window_directory',lambda:directory)
    monkeypatch.setattr(views.time,'time_ns',lambda:(at+120000)*1_000_000)
    result=TestClient(app).get('/api/v1/console/binance-demo').json()['cash_window']
    assert result['status']=='AVAILABLE'
    assert result['report']['status']=='MATCHED_REPORTED_WINDOW'
    assert result['report']['assets'][0]['residual']=='0'
    assert result['report']['ledger_reconciliation']=='NOT_EVALUATED'
    assert result['report']['source_verified'] is False
    assert views.build_cash_window_snapshot(directory=directory,as_of_ms=at)['status']=='DATA_BLOCKED'
    assert before=={p.name:p.read_bytes() for p in directory.iterdir()}
    (directory/'latest.json').write_text('{}')
    assert views.build_cash_window_snapshot(directory=directory,as_of_ms=at+120000)['status']=='CORRUPT_OR_UNREADABLE'


def test_user_stream_snapshot_is_read_only_and_does_not_claim_order_delivery(tmp_path,monkeypatch):
    from services.research.demo_user_stream import UserStreamJournal
    path=tmp_path/'stream.db'
    at=1789160000000
    with UserStreamJournal(path,account_ref_sha256='a'*64) as journal:
        journal.start_session(source_sha256='b'*64,at_ms=at)
        journal.record('CONNECTED',at_ms=at)
        journal.record('HEARTBEAT',at_ms=at)
    before=path.read_bytes()
    monkeypatch.setattr(views,'_user_stream_path',lambda:path)
    monkeypatch.setattr(views.time,'time_ns',lambda:at*1_000_000)
    report=TestClient(app).get('/api/v1/console/binance-demo').json()['user_stream']['report']
    assert report['state']=='CONNECTED' and report['messages']==0
    assert report['cash_reconciliation']=='UNKNOWN' and report['coverage_verified'] is False
    assert views.build_user_stream_snapshot(path=path,as_of_ms=at+120001)['report']['state']=='STALE'
    assert views.build_user_stream_snapshot(path=path,as_of_ms=at-1)=={'status':'DATA_BLOCKED','report':None}
    assert path.read_bytes()==before


def test_statement_projection_is_read_only_and_preserves_missing_cash_evidence(tmp_path):
    from services.console.api.binance_demo_views import build_statement_snapshot
    from tools.binance_demo_statement import collect, save_statement
    from tests.tools.test_binance_demo_statement import transport, NOW
    from tests.tools.test_binance_demo_private_check import credentials
    report=collect(credential_file=credentials(tmp_path),symbols=['BTCUSDT'],start_ms=NOW-1000,
        end_ms=NOW,operator_read=True,get=transport([]),clock=lambda:NOW)
    directory=tmp_path/'statements';save_statement(directory,report)
    before={p.name:p.read_bytes() for p in directory.iterdir()}
    r=build_statement_snapshot(directory=directory,as_of_ms=NOW)
    assert r['report']['trade_count']==1 and r['report']['income_count']==1
    assert r['report']['venue_cash_reconciliation']=='UNKNOWN' and r['report']['strategy_pnl'] is None
    assert build_statement_snapshot(directory=directory,as_of_ms=NOW-1)['status']=='DATA_BLOCKED'
    assert build_statement_snapshot(directory=directory,as_of_ms=NOW+120001)['status']=='STALE'
    assert before=={p.name:p.read_bytes() for p in directory.iterdir()}
from fastapi.testclient import TestClient

from services.console.main import app
from services.console.api import binance_demo_views as views
from tests.tools.test_binance_demo_readiness import NOW, transport
from tools.binance_demo_readiness import collect


def test_account_opening_capture_is_read_only_and_never_cash_reconciliation(tmp_path, monkeypatch):
    from services.research.demo_account_baseline import save_capture
    from tests.research.test_demo_account_baseline import captured, NOW as at
    directory = tmp_path / 'baseline'
    save_capture(directory, captured())
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    monkeypatch.setattr(views, '_account_capture_directory', lambda: directory)
    monkeypatch.setattr(views.time, 'time_ns', lambda: (at + 200) * 1_000_000)
    result = TestClient(app).get('/api/v1/console/binance-demo').json()['account_baseline']
    assert result['status'] == 'AVAILABLE'
    assert result['report']['positions'][0]['observed_entry_price'] == '100'
    assert result['report']['new_strategy_pnl'] is None
    assert result['report']['ledger_reconciliation'] == 'UNKNOWN'
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}
    assert views.build_account_baseline_snapshot(directory=directory, as_of_ms=at + 120001)['report']['inventory_status'] == 'STALE'
    assert views.build_account_baseline_snapshot(directory=directory, as_of_ms=at - 1) == {'status': 'DATA_BLOCKED', 'report': None}
    (directory / 'latest.json').write_text('{}')
    assert views.build_account_baseline_snapshot(directory=directory, as_of_ms=at + 200) == {'status': 'CORRUPT_OR_UNREADABLE', 'report': None}


def test_private_connection_report_is_independent_read_only_and_expires(tmp_path, monkeypatch):
    from tools.binance_demo_private_check import collect as private_collect
    from services.research.demo_private_checks import save_report
    directory = tmp_path/'checks'
    save_report(directory, private_collect(credential_file=tmp_path/'absent', clock_ms=lambda: NOW))
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    monkeypatch.setattr(views, '_private_check_directory', lambda: directory)
    monkeypatch.setattr(views.time, 'time_ns', lambda: (NOW+1)*1_000_000)
    body = TestClient(app).get('/api/v1/console/binance-demo').json()
    assert body['private_checks']['status'] == 'AVAILABLE'
    assert body['private_checks']['report']['submission_status'] == 'BLOCKED'
    assert body['ledger_reconciliation_status'] == 'NOT_CHECKED'
    assert views.build_private_check_snapshot(directory=directory, as_of_ms=NOW+120001)['status'] == 'STALE'
    future = views.build_private_check_snapshot(directory=directory, as_of_ms=NOW-1)
    assert future == {'status': 'DATA_BLOCKED', 'report': None}
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}


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
