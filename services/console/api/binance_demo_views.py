"""Project saved Demo observations without network, credentials or mutations."""
import hashlib
from pathlib import Path
import time

from fastapi import APIRouter

from quantos.contracts.binance_demo import DEMO_REST_BASE, DemoReceipt, MAX_AGE_MS, MAX_RESPONSE_BYTES
from quantos.runtime_paths import runtime_state_root
from services.validation.live_submission_authority import LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE
from services.research.demo_execution_journal import DemoExecutionJournal, replay_report
from services.research.demo_order_recovery import load_capture, compare_orders
from services.research.demo_private_checks import load_report
from services.research.demo_account_baseline import load_capture as load_account_capture, build_baseline
from services.research.demo_statement import load_statement, assess as assess_statement
from services.research.demo_user_stream import UserStreamJournal
from services.research.demo_cash_window import load_capture as load_cash_window, assess as assess_cash_window
from services.research.demo_execution_origin import load_latest as load_origin, assess as assess_origin
from services.research.demo_cash_projection import project_cash_window
from services.research.demo_oms_status import load_latest as load_oms_status
from services.research.demo_cancel_status import load_latest as load_cancel_status
from services.research.demo_resident_status import load_latest as load_resident_status
from services.research.demo_cash_chain_status import load_latest as load_cash_chain
from services.research.demo_executor_observation import load_latest as load_executor_observation
from services.research.demo_issuer_status import load_latest as load_issuer_status
from services.research import demo_market_inputs, demo_backup_status

router = APIRouter(tags=["binance-demo"])


def _backup_status_directory():
    return runtime_state_root() / 'data/cache/binance_demo_backup_status'


def build_backup_status_snapshot(*, directory: Path, as_of_ms: int):
    if not (directory/'latest.json').exists() and not (directory/'latest.json').is_symlink():
        return {'status': 'NO_SNAPSHOT', 'report': None}
    try:
        raw = demo_backup_status.load_latest(directory)
        if raw['observed_at_ms'] > as_of_ms:
            return {'status': 'DATA_BLOCKED', 'report': None}
        return {'status': 'STALE' if as_of_ms-raw['observed_at_ms'] > demo_backup_status.MAX_AGE_MS else 'AVAILABLE', 'report': raw}
    except Exception:
        return {'status': 'CORRUPT_OR_UNREADABLE', 'report': None}


def _market_inputs_directory():
    return runtime_state_root() / 'data/cache/binance_demo_market_inputs'


def build_market_inputs_snapshot(*,directory:Path,as_of_ms:int):
    if not (directory/'latest.json').exists() and not (directory/'latest.json').is_symlink():
        return {'status':'NO_SNAPSHOT','report':None}
    try:
        raw=demo_market_inputs.load_latest(directory)
        if raw['symbol']!=directory.name or raw['completed_at_ms']>as_of_ms:
            return {'status':'DATA_BLOCKED','report':None}
        status='STALE' if as_of_ms-raw['completed_at_ms']>demo_market_inputs.MAX_AGE_MS else 'AVAILABLE'
        return {'status':status,'report':demo_market_inputs.display(raw)}
    except Exception:return {'status':'CORRUPT_OR_UNREADABLE','report':None}


def _issuer_status_directory():
    return runtime_state_root() / 'data/cache/binance_demo_issuer_status'


def build_issuer_status_snapshot(*,directory:Path,as_of_ms:int):
    if not (directory/'latest.json').exists() and not (directory/'latest.json').is_symlink():
        return {'status':'NO_SNAPSHOT','report':None}
    try:
        raw=load_issuer_status(directory)
        if raw['observed_at_ms']>as_of_ms:return {'status':'DATA_BLOCKED','report':None}
        return {'status':'STALE' if as_of_ms-raw['observed_at_ms']>120000 else 'AVAILABLE','report':raw}
    except Exception:return {'status':'CORRUPT_OR_UNREADABLE','report':None}


def _executor_observation_directory():
    return runtime_state_root() / 'data/cache/binance_demo_executor_observation'


def build_executor_observation_snapshot(*,directory:Path,as_of_ms:int):
    if not (directory/'latest.json').exists() and not (directory/'latest.json').is_symlink():
        return {'status':'NO_SNAPSHOT','report':None}
    try:
        raw=load_executor_observation(directory)
        if raw['observed_at_ms']>as_of_ms:return {'status':'DATA_BLOCKED','report':None}
        return {'status':'STALE' if as_of_ms-raw['observed_at_ms']>120000 else 'AVAILABLE','report':raw}
    except Exception:return {'status':'CORRUPT_OR_UNREADABLE','report':None}


def _cash_chain_directory():
    return runtime_state_root() / 'data/cache/binance_demo_cash_chain'


def build_cash_chain_snapshot(*, directory: Path, as_of_ms: int):
    if not (directory/'latest.json').exists() and not (directory/'latest.json').is_symlink():
        return {'status':'NO_SNAPSHOT','report':None}
    try:
        raw=load_cash_chain(directory)
        if raw['observed_at_ms']>as_of_ms:return {'status':'DATA_BLOCKED','report':None}
        return {'status':'AVAILABLE','report':raw}
    except Exception:
        return {'status':'CORRUPT_OR_UNREADABLE','report':None}


def _resident_status_directory():
    return runtime_state_root() / 'data/cache/binance_demo_resident_status'


def build_resident_status_snapshot(*, directory: Path, as_of_ms: int):
    empty = {'report': None, 'last_result': None}
    if not (directory/'views/latest.json').exists() and not (directory/'views/latest.json').is_symlink():
        return {'status': 'NO_SNAPSHOT', **empty}
    try:
        report = load_resident_status(directory/'views')
        last = None
        if (directory/'outcomes/latest.json').exists() or (directory/'outcomes/latest.json').is_symlink():
            last = load_resident_status(directory/'outcomes')
        if report['observed_at_ms'] > as_of_ms or last is not None and last['observed_at_ms'] > as_of_ms:
            return {'status': 'DATA_BLOCKED', **empty}
        return {'status': 'AVAILABLE', 'report': report, 'last_result': last}
    except Exception:
        return {'status': 'CORRUPT_OR_UNREADABLE', **empty}


def _oms_status_directory():
    return runtime_state_root() / 'data/cache/binance_demo_oms_status'


def _cancel_status_directory():
    return runtime_state_root() / 'data/cache/binance_demo_cancel_status'


def build_cancel_status_snapshot(*,directory:Path,as_of_ms:int):
    if not (directory/'latest.json').exists() and not (directory/'latest.json').is_symlink():return {'status':'NO_SNAPSHOT','report':None}
    try:
        raw=load_cancel_status(directory)
        if raw['observed_at_ms']>as_of_ms:return {'status':'DATA_BLOCKED','report':None}
        return {'status':'AVAILABLE','report':raw}
    except Exception:return {'status':'CORRUPT_OR_UNREADABLE','report':None}


def build_oms_status_snapshot(*,directory:Path,as_of_ms:int):
    if not (directory/'latest.json').exists() and not (directory/'latest.json').is_symlink():return {'status':'NO_SNAPSHOT','report':None}
    try:
        raw=load_oms_status(directory)
        if raw['observed_at_ms']>as_of_ms:return {'status':'DATA_BLOCKED','report':None}
        return {'status':'AVAILABLE','report':raw}
    except Exception:return {'status':'CORRUPT_OR_UNREADABLE','report':None}


def _execution_origin_directory():
    return runtime_state_root() / 'data/cache/binance_demo_execution_origins'


def build_execution_origin_snapshot(*, directory: Path, as_of_ms: int):
    if not (directory/'latest.json').exists():return {'status':'NO_SNAPSHOT','report':None}
    try:
        raw=load_origin(directory)
        if raw['completed_at_ms']>as_of_ms:return {'status':'DATA_BLOCKED','report':None}
        return {'status':'AVAILABLE','report':assess_origin(raw,as_of_ms=as_of_ms)}
    except Exception:return {'status':'CORRUPT_OR_UNREADABLE','report':None}


def _cash_window_directory():
    return runtime_state_root() / 'data/cache/binance_demo_cash_windows'


def build_cash_window_snapshot(*, directory: Path, as_of_ms: int):
    if not (directory/'latest.json').exists():
        return {'status': 'NO_SNAPSHOT', 'report': None}
    try:
        raw = load_cash_window(directory)
        if raw['completed_at_ms'] > as_of_ms:
            return {'status': 'DATA_BLOCKED', 'report': None}
        return {'status': 'AVAILABLE', 'report': assess_cash_window(raw, as_of_ms=as_of_ms)}
    except Exception:
        return {'status': 'CORRUPT_OR_UNREADABLE', 'report': None}


def build_canonical_cash_snapshot(*, path: Path, directory: Path, as_of_ms: int):
    if not path.exists() or not (directory/'latest.json').exists():
        return {'status': 'INPUTS_MISSING', 'report': None}
    try:
        raw = load_cash_window(directory)
        with DemoExecutionJournal(path, read_only=True) as journal:
            records, _, _ = journal.read()
            if raw['completed_at_ms'] > as_of_ms or any(r['received_at_ms'] > as_of_ms for r in records):
                return {'status': 'DATA_BLOCKED', 'report': None}
            report = project_cash_window(journal, raw)
        if report['observed_at_ms']>as_of_ms:
            return {'status':'DATA_BLOCKED','report':None}
        return {'status': 'AVAILABLE', 'report': report}
    except Exception:
        return {'status': 'CORRUPT_OR_UNREADABLE', 'report': None}


def _user_stream_path():
    return runtime_state_root() / 'data/cache/binance_demo_user_stream.db'


def build_user_stream_snapshot(*, path: Path, as_of_ms=None):
    if not path.exists():return {'status':'NO_JOURNAL','report':None}
    try:
        with UserStreamJournal(path,read_only=True) as journal:
            report=journal.snapshot(as_of_ms=as_of_ms)
        return {'status':'AVAILABLE','report':report}
    except ValueError as exc:
        return {'status':'DATA_BLOCKED' if str(exc)=='STREAM_FUTURE_OBSERVATION' else 'CORRUPT_OR_UNREADABLE','report':None}
    except Exception:return {'status':'CORRUPT_OR_UNREADABLE','report':None}


def build_statement_snapshot(*, directory: Path, as_of_ms: int):
    if not (directory / 'latest.json').exists():
        return {'status': 'NO_SNAPSHOT', 'report': None}
    try:
        report = assess_statement(load_statement(directory))
        if report['completed_at_ms'] > as_of_ms:
            return {'status': 'DATA_BLOCKED', 'report': None}
        return {'status': 'STALE' if as_of_ms-report['observed_at_ms'] > MAX_AGE_MS else 'AVAILABLE', 'report': report}
    except Exception:
        return {'status': 'CORRUPT_OR_UNREADABLE', 'report': None}


def _receipt_path():
    return runtime_state_root() / "data/cache/binance_demo_readiness.json"


def _execution_journal_path():
    return runtime_state_root() / "data/cache/binance_demo_execution.db"


def _order_query_directory():
    return runtime_state_root() / "data/cache/binance_demo_order_queries"


def _private_check_directory():
    return runtime_state_root() / 'data/cache/binance_demo_private_checks'


def _account_capture_directory():
    return runtime_state_root() / 'data/cache/binance_demo_account_captures'


def build_account_baseline_snapshot(*, directory: Path, as_of_ms: int):
    if not (directory / 'latest.json').exists():
        return {'status': 'NO_SNAPSHOT', 'report': None}
    try:
        report = build_baseline(load_account_capture(directory), as_of_ms=as_of_ms)
        if report['inventory_status'] == 'DATA_BLOCKED':
            return {'status': 'DATA_BLOCKED', 'report': None}
        return {'status': 'AVAILABLE', 'report': report}
    except Exception:
        return {'status': 'CORRUPT_OR_UNREADABLE', 'report': None}


def build_private_check_snapshot(*, directory: Path, as_of_ms: int):
    if not (directory/'latest.json').exists():
        return {'status': 'NO_SNAPSHOT', 'report': None}
    try:
        report = load_report(directory)
        if report.completed_at_ms > as_of_ms:
            return {'status': 'DATA_BLOCKED', 'report': None}
        return {'status': 'STALE' if as_of_ms-report.observed_at_ms > MAX_AGE_MS else 'AVAILABLE',
                'report': report.model_dump()}
    except Exception:
        return {'status': 'CORRUPT_OR_UNREADABLE', 'report': None}


def build_order_recovery_snapshot(*, directory: Path, receipts, as_of_ms: int):
    if not (directory / 'latest.json').exists():
        return {'status':'NO_SNAPSHOT', 'report':None}
    try:
        report = compare_orders(receipts, load_capture(directory), as_of_ms=as_of_ms)
        return {'status':'AVAILABLE', 'report':report}
    except Exception:
        return {'status':'CORRUPT_OR_UNREADABLE', 'report':None}


def build_execution_snapshot(*, path: Path, as_of_ms: int):
    if not path.exists():
        return {"status": "NO_JOURNAL", "report": None}
    try:
        with DemoExecutionJournal(path, read_only=True) as journal:
            report = replay_report(journal)
        if (report["observed_at_ms"] is not None and report["observed_at_ms"] > as_of_ms or
                report.get('clock',{}).get('available_at_ms',0)>as_of_ms):
            return {"status": "DATA_BLOCKED", "report": None}
        return {"status": "AVAILABLE", "report": report}
    except Exception:
        # A malformed imported diagnostic must not expose raw receipt contents or
        # make the independent connectivity snapshot unavailable.
        return {"status": "CORRUPT_OR_UNREADABLE", "report": None}


def build_snapshot(*, path: Path, as_of_ms: int):
    status, reason, receipt, digest, age = "NO_SNAPSHOT", "RECEIPT_MISSING", None, None, None
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("receipt too large")
        candidate = DemoReceipt.model_validate_json(raw)
        if candidate.completed_at_ms > as_of_ms:
            status, reason = "DATA_BLOCKED", "RECEIPT_FROM_FUTURE"
        else:
            receipt = candidate
            digest = hashlib.sha256(raw).hexdigest()
            age = as_of_ms - receipt.observed_at_ms
            if age > MAX_AGE_MS:
                status, reason = "STALE", "REFRESH_OBSERVATION"
            elif receipt.public.status != "READY" or receipt.account.status in ("FAILED", "SKIPPED"):
                status, reason = "DATA_BLOCKED", "PROBE_FAILED"
            else:
                status, reason = "REAL", "OBSERVATION_AVAILABLE"
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, RecursionError):
        status, reason = "CORRUPT_OR_UNREADABLE", "RECEIPT_INVALID_OR_UNREADABLE"
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT", "read_only": True, "api_version": "0.1.0",
        "data_provenance": "DEMO_CONNECTIVITY_DIAGNOSTIC", "snapshot_id": digest,
        "snapshot_ids": [] if digest is None else [digest], "snapshot_status": status,
        "data_quality": {"status": status, "provenance": "DEMO_CONNECTIVITY_DIAGNOSTIC", "reason": reason},
        "environment": "BINANCE_USDM_DEMO", "rest_base": DEMO_REST_BASE,
        "observed_at_ms": receipt.observed_at_ms if receipt else None,
        "as_of_ms": as_of_ms, "age_ms": age, "max_age_ms": MAX_AGE_MS,
        "public": receipt.public.model_dump() if receipt else None,
        "account": receipt.account.model_dump() if receipt else None,
        "submission_enabled": False, "authoritative": False,
        "execution_reason": LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE,
        "order_stream_status": "NOT_CHECKED", "ledger_reconciliation_status": "NOT_CHECKED",
    }


@router.get("/api/v1/console/binance-demo")
def binance_demo_snapshot():
    # Read the continuous journal before sampling the enclosing response clock.
    user_stream=build_user_stream_snapshot(path=_user_stream_path())
    as_of_ms = time.time_ns() // 1_000_000
    execution = build_execution_snapshot(path=_execution_journal_path(), as_of_ms=as_of_ms)
    return build_snapshot(path=_receipt_path(), as_of_ms=as_of_ms) | {
        'backup_status':build_backup_status_snapshot(directory=_backup_status_directory(),as_of_ms=as_of_ms),
        'executor_observation':build_executor_observation_snapshot(directory=_executor_observation_directory(),as_of_ms=as_of_ms),
        'issuer_status':build_issuer_status_snapshot(directory=_issuer_status_directory(),as_of_ms=as_of_ms),
        'market_inputs':{symbol:build_market_inputs_snapshot(directory=_market_inputs_directory()/symbol,as_of_ms=as_of_ms)
                         for symbol in ('BTCUSDC','BTCUSDT')},
        'execution_origin': build_execution_origin_snapshot(directory=_execution_origin_directory(), as_of_ms=as_of_ms),
        'oms_status': build_oms_status_snapshot(directory=_oms_status_directory(),as_of_ms=as_of_ms),
        'cancellation_status': build_cancel_status_snapshot(directory=_cancel_status_directory(),as_of_ms=as_of_ms),
        'resident_status': build_resident_status_snapshot(directory=_resident_status_directory(),as_of_ms=as_of_ms),
        'cash_window': build_cash_window_snapshot(directory=_cash_window_directory(), as_of_ms=as_of_ms),
        'cash_chain': build_cash_chain_snapshot(directory=_cash_chain_directory(),as_of_ms=as_of_ms),
        'canonical_cash': build_canonical_cash_snapshot(path=_execution_journal_path(), directory=_cash_window_directory(), as_of_ms=as_of_ms),
        'user_stream':user_stream,
        'statement': build_statement_snapshot(directory=runtime_state_root() / 'data/cache/binance_demo_statements', as_of_ms=as_of_ms),
        'account_baseline': build_account_baseline_snapshot(directory=_account_capture_directory(), as_of_ms=as_of_ms),
        'private_checks': build_private_check_snapshot(directory=_private_check_directory(), as_of_ms=as_of_ms),
        "execution": execution,
        "order_recovery": build_order_recovery_snapshot(directory=_order_query_directory(), receipts=execution['report'], as_of_ms=as_of_ms),
    }
