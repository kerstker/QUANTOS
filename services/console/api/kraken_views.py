"""Read-only Kraken observation status; never reads credentials or calls a venue."""
import hashlib
import json
import os
from pathlib import Path
import re
import time

from fastapi import APIRouter
from quantos.runtime_paths import runtime_state_root
from services.console.api.common import with_snapshot_metadata
from services.console.api.state_artifact import read_json_artifact, VALID, MISSING

router = APIRouter()


def _session_directory():
    return Path(os.environ.get('QUANTOS_KRAKEN_SESSION_ROOT',
        str(Path(runtime_state_root()) / 'data' / 'kraken' / 'account'))).absolute()


def _sealed(root, name):
    status, value = read_json_artifact(root / name, trusted_root=root)
    if status != VALID or not isinstance(value, dict):
        raise ValueError('MISSING_OR_INVALID')
    expected = hashlib.sha256(json.dumps({k:v for k,v in value.items() if k != 'result_hash'},
        sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    if value.get('result_hash') != expected or value.get('submission_enabled') is not False or value.get('authoritative') is not False:
        raise ValueError('INVALID_BINDING')
    return value


def _status(value):
    if not isinstance(value, str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,79}', value):
        raise ValueError('INVALID_STATUS')
    return value


@router.get('/api/v1/console/kraken-portfolio')
def kraken_portfolio():
    output = dict(source='BACKEND_COMPUTED_SNAPSHOT', read_only=True, api_version='0.1.0',
        data_provenance='KRAKEN_READ_ONLY_SESSION', snapshot_status='NO_SNAPSHOT',
        submission_enabled=False, authoritative=False, observed_at_ms=None,
        session_status=None, portfolio_status=None, stages=[], blockers=[])
    root = _session_directory()
    try:
        if root.resolve() != root.absolute():
            raise ValueError('SYMLINK_ROOT')
        state, _ = read_json_artifact(root/'portfolio-session-status.json', trusted_root=root)
        if state == MISSING:
            return with_snapshot_metadata(output)
        session = _sealed(root, 'portfolio-session-status.json')
        at = session.get('as_of_ms')
        now = time.time_ns() // 1_000_000
        if session.get('schema') != 'KRAKEN_PORTFOLIO_SESSION_V1' or type(at) is not int or not 0 < at <= now:
            raise ValueError('INVALID_SESSION')
        status = session.get('status')
        if status not in ('REFRESH_IN_PROGRESS', 'FAILED', 'OBSERVATION_COMPLETE'):
            raise ValueError('INVALID_SESSION_STATUS')
        output.update(snapshot_status='STALE' if now-at > 120000 else 'REAL',
                      observed_at_ms=at, session_status=status, snapshot_id=session['result_hash'])
        if status == 'OBSERVATION_COMPLETE':
            report = _sealed(root, 'portfolio/status.json')
            account = _sealed(root, 'latest.json')
            if (report.get('schema') != 'KRAKEN_REVIEW_CYCLE_V1' or
                account.get('schema') != 'KRAKEN_ACCOUNT_OBSERVATION_V1' or
                report['result_hash'] != session.get('portfolio_hash') or
                account['result_hash'] != session.get('account_hash') or
                account.get('account_ref_sha256') != session.get('account_ref_sha256') or
                report.get('status') != session.get('portfolio_status') or
                type(report.get('completed_at_ms')) is not int or
                not 0 <= at-report['completed_at_ms'] <= 120000):
                raise ValueError('DIFFERENT_CYCLE')
            output['portfolio_status'] = _status(report['status'])
            output['stages'] = [{'id':name, 'status':_status(report[field])} for name, field in (
                ('market', 'market_status'), ('orders', 'order_recovery_status'),
                ('cash', 'cash_reconciliation_status'), ('fees', 'fee_observation_status'),
                ('portfolio', 'status'))]
            blockers = report.get('blockers')
            if not isinstance(blockers, list) or len(blockers) > 64:
                raise ValueError('INVALID_BLOCKERS')
            output['blockers'] = [_status(value) for value in blockers]
        if _sealed(root, 'portfolio-session-status.json') != session:
            raise ValueError('CONCURRENT_REFRESH')
    except (OSError, ValueError, TypeError, KeyError):
        output.update(snapshot_status='DATA_BLOCKED', session_status=None, portfolio_status=None,
                      stages=[], blockers=[], observed_at_ms=None)
        output.pop('snapshot_id', None)
    return with_snapshot_metadata(output)
