"""Compare observed wallets with reported window flows, without owning cash state.

Income is a single comparison input: fees are not charged here or applied to the
V2 ledger. Matching two REST sweeps does not establish exchange finality, signed
source authority, complete history, or strategy profitability.
"""
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from quantos.contracts.binance_demo_account import _decimal, _int, _rows, _text
from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.demo_statement import normalize as normalize_statement

SCHEMA = 'DEMO_CASH_WINDOW_V1'
MAX_BYTES = 8_000_000
MAX_PAGES = 5
GUARD_MS = 5000
KINDS = ('clock_before', 'wallet_before', 'wallet_after', 'clock_after', 'income')
REASONS = ('REQUEST_FAILED', 'RATE_LIMITED', 'AUTHENTICATION_REJECTED', 'RESPONSE_TOO_LARGE',
           'RESPONSE_INVALID', 'CLOCK_UNRELIABLE', 'CAPTURE_BUDGET_EXHAUSTED')


def _fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError('CASH_WINDOW_FIELDS')


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('CASH_WINDOW_DIGEST')


def normalize_wallet(raw):
    return _rows(raw, {'asset': _text, 'wallet': _decimal, 'updated_at_ms': _int}, ('asset',), max_rows=500)


def window_bounds(reads):
    by_kind = {r['kind']: r for r in reads if r['kind'] != 'income'}
    opening, closing = by_kind.get('wallet_before'), by_kind.get('wallet_after')
    if opening is None or closing is None:
        return None
    return {'query_start_ms': opening['requested_at_ms']-GUARD_MS,
            'query_end_ms': closing['received_at_ms']+GUARD_MS,
            'inner_start_ms': opening['received_at_ms']+GUARD_MS,
            'inner_end_ms': closing['requested_at_ms']-GUARD_MS}


def validate(raw):
    _fields(raw, 'schema status observed_at_ms completed_at_ms account_ref_sha256 source_sha256 observe_seconds reads failures')
    if raw['schema'] != SCHEMA or raw['status'] not in ('NOT_REQUESTED', 'CAPTURED', 'PARTIAL', 'FAILED'):
        raise ValueError('CASH_WINDOW_SCHEMA')
    start, end = _int(raw['observed_at_ms']), _int(raw['completed_at_ms'])
    seconds = _int(raw['observe_seconds'])
    if not 30 <= seconds <= 3600 or not GUARD_MS < start <= end <= start+seconds*1000+240000:
        raise ValueError('CASH_WINDOW_CLOCK')
    _sha(raw['source_sha256'])
    if raw['account_ref_sha256'] is not None:
        _sha(raw['account_ref_sha256'])
    reads, failures = raw['reads'], raw['failures']
    if not isinstance(reads, list) or len(reads) > 4+2*MAX_PAGES or not isinstance(failures, list) or len(failures) > 1:
        raise ValueError('CASH_WINDOW_BOUNDS')
    previous = start
    seen = set()
    offsets = []
    pages = {1: [], 2: []}
    for read in reads:
        _fields(read, 'kind requested_at_ms received_at_ms account_ref_sha256 response_sha256 sweep page data')
        kind = read['kind']
        if kind not in KINDS:
            raise ValueError('CASH_WINDOW_KIND')
        sent, received = _int(read['requested_at_ms']), _int(read['received_at_ms'])
        if not previous <= sent <= received <= min(end, sent+10000):
            raise ValueError('CASH_WINDOW_READ_CLOCK')
        previous = received
        _sha(read['response_sha256'])
        sweep, page = _int(read['sweep']), _int(read['page'])
        if kind == 'income':
            if sweep not in pages or page != len(pages[sweep])+1 or page > MAX_PAGES:
                raise ValueError('CASH_WINDOW_PAGE_ORDER')
            if seen != set(KINDS[:4]) or (sweep == 1 and pages[2]) or (sweep == 2 and not pages[1]):
                raise ValueError('CASH_WINDOW_READ_ORDER')
            if page > 1 and len(pages[sweep][-1]['data']) < 1000:
                raise ValueError('CASH_WINDOW_EXTRA_PAGE')
            if normalize_statement('income', read['data'], []) != read['data']:
                raise ValueError('CASH_WINDOW_FLOW_SHAPE')
            pages[sweep].append(read)
        else:
            if kind != KINDS[len(seen)] or sweep != 0 or page != 0:
                raise ValueError('CASH_WINDOW_READ_ORDER')
            seen.add(kind)
            if kind.startswith('clock'):
                _fields(read['data'], 'serverTime')
                server = _int(read['data']['serverTime'])
                offset = server-(sent+received)//2
                if received-sent > 5000 or abs(offset) > 2000:
                    raise ValueError('CASH_WINDOW_CLOCK_UNRELIABLE')
                offsets.append(offset)
            elif normalize_wallet(read['data']) != read['data']:
                raise ValueError('CASH_WINDOW_WALLET_SHAPE')
            elif any(a['updated_at_ms'] > received+GUARD_MS for a in read['data']):
                raise ValueError('CASH_WINDOW_WALLET_FROM_FUTURE')
        expected_ref = None if kind.startswith('clock') else raw['account_ref_sha256']
        if read['account_ref_sha256'] != expected_ref or (not kind.startswith('clock') and expected_ref is None):
            raise ValueError('CASH_WINDOW_ACCOUNT_IDENTITY')
    if len(offsets) == 2 and abs(offsets[0]-offsets[1]) > 500:
        raise ValueError('CASH_WINDOW_CLOCK_DRIFT')
    bounds = window_bounds(reads)
    if bounds:
        opening, closing = reads[1:3]
        if closing['requested_at_ms'] < opening['received_at_ms']+seconds*1000:
            raise ValueError('CASH_WINDOW_OBSERVATION_TOO_SHORT')
        for sweep in pages.values():
            for read in sweep:
                if read['requested_at_ms'] <= bounds['query_end_ms']+10000:
                    raise ValueError('CASH_WINDOW_QUERY_TOO_EARLY')
                if any(not bounds['query_start_ms'] <= r['time'] <= bounds['query_end_ms'] for r in read['data']):
                    raise ValueError('CASH_WINDOW_FLOW_OUTSIDE_QUERY')
        if pages[2] and pages[2][0]['requested_at_ms'] < pages[1][-1]['received_at_ms']+10000:
            raise ValueError('CASH_WINDOW_RECHECK_TOO_EARLY')
    for failure in failures:
        _fields(failure, 'kind sweep page requested_at_ms received_at_ms reason')
        if failure['kind'] not in (*KINDS, 'credentials', 'worker') or failure['reason'] not in REASONS:
            raise ValueError('CASH_WINDOW_FAILURE')
        if not 0 <= _int(failure['sweep']) <= 2 or not 0 <= _int(failure['page']) <= MAX_PAGES:
            raise ValueError('CASH_WINDOW_FAILURE_PAGE')
        if not previous <= _int(failure['requested_at_ms']) <= _int(failure['received_at_ms']) <= end:
            raise ValueError('CASH_WINDOW_FAILURE_CLOCK')
    if raw['status'] == 'CAPTURED' and (failures or not pages[1] or not pages[2]):
        raise ValueError('CASH_WINDOW_CAPTURE_INCOMPLETE')
    if raw['status'] == 'NOT_REQUESTED' and (reads or failures or raw['account_ref_sha256'] is not None):
        raise ValueError('CASH_WINDOW_DEFAULT_NOT_OFFLINE')
    if raw['status'] in ('FAILED', 'PARTIAL') and not failures:
        raise ValueError('CASH_WINDOW_FAILURE_MISSING')
    return raw


def _sweep(reads):
    unique, duplicates, conflict = {}, 0, False
    for read in reads:
        for row in read['data']:
            key = row['incomeType'], row['tranId']
            if key in unique:
                duplicates += 1
                if unique[key] != row:
                    conflict = True
            else:
                unique[key] = row
    return unique, duplicates, conflict


@ledger_decimal_context()
def assess(raw, *, as_of_ms=None):
    validate(raw)
    if as_of_ms is not None and raw['completed_at_ms'] > _int(as_of_ms):
        raise ValueError('CASH_WINDOW_FUTURE')
    reads = raw['reads']
    wallets = {r['kind']: {a['asset']: a for a in r['data']} for r in reads if r['kind'].startswith('wallet')}
    opening, closing = wallets.get('wallet_before', {}), wallets.get('wallet_after', {})
    groups = [[r for r in reads if r['kind']=='income' and r['sweep']==s] for s in (1, 2)]
    one, dup1, conflict1 = _sweep(groups[0])
    two, dup2, conflict2 = _sweep(groups[1])
    bounds = window_bounds(reads)
    issues = []
    if raw['status'] != 'CAPTURED':
        issues.append('CAPTURE_INCOMPLETE')
    if any(not g or len(g[-1]['data']) == 1000 for g in groups):
        issues.append('INCOME_PAGE_LIMIT' if all(groups) else 'INCOME_SWEEP_MISSING')
    if one != two:
        issues.append('INCOME_SWEEPS_DIFFER')
    if conflict1 or conflict2:
        issues.append('CONFLICTING_FLOW_IDENTITY')
    rows = sorted(two.values(), key=lambda r: (r['time'], r['incomeType'], r['tranId']))
    ambiguity = [r for r in rows if bounds and not bounds['inner_start_ms'] < r['time'] < bounds['inner_end_ms']]
    if ambiguity:
        issues.append('ENDPOINT_EVENT_AMBIGUITY')
    assets = []
    if not opening or not closing:
        issues.append('WALLET_OBSERVATION_MISSING')
    names = sorted(set(opening) | set(closing) | {r['asset'] for r in rows})
    if any(name not in opening or name not in closing for name in names):
        issues.append('WALLET_ASSET_MISSING')
    can_compare = not issues
    for name in names:
        before = Decimal(opening[name]['wallet']) if name in opening else None
        after = Decimal(closing[name]['wallet']) if name in closing else None
        by_type = {}
        for row in rows:
            if row['asset'] == name:
                by_type[row['incomeType']] = by_type.get(row['incomeType'], Decimal(0))+Decimal(row['income'])
        total = sum(by_type.values(), Decimal(0)) if all(groups) else None
        change = after-before if before is not None and after is not None else None
        residual = change-total if can_compare and change is not None and total is not None else None
        fmt = lambda v: canonical_decimal(v) if v is not None else None
        assets.append({'asset': name, 'opening_wallet': fmt(before), 'closing_wallet': fmt(after),
                       'wallet_change': fmt(change), 'reported_flow': fmt(total), 'residual': fmt(residual),
                       'transfers': fmt(by_type.get('TRANSFER', Decimal(0))) if all(groups) else None,
                       'commissions': fmt(by_type.get('COMMISSION', Decimal(0))) if all(groups) else None,
                       'funding': fmt(by_type.get('FUNDING_FEE', Decimal(0))) if all(groups) else None,
                       'realized_pnl': fmt(by_type.get('REALIZED_PNL', Decimal(0))) if all(groups) else None,
                       'breakdown': [{'type': k, 'amount': fmt(v)} for k, v in sorted(by_type.items())]})
    status = 'UNRESOLVED' if issues else ('DIFFERENCE_FOUND' if any(Decimal(a['residual']) != 0 for a in assets)
                                        else 'MATCHED_REPORTED_WINDOW')
    return {'schema': SCHEMA, 'environment': 'BINANCE_USDM_DEMO', 'status': status,
            'capture_status': raw['status'], 'observed_at_ms': raw['observed_at_ms'], 'completed_at_ms': raw['completed_at_ms'],
            'account_ref_sha256': raw['account_ref_sha256'], 'source_sha256': raw['source_sha256'],
            'observe_seconds': raw['observe_seconds'], 'bounds': bounds, 'read_count': len(reads),
            'income_count': len(rows), 'duplicate_rows': dup1+dup2, 'endpoint_ambiguous_rows': len(ambiguity),
            'sweeps_match': bool(all(groups) and one == two and not (conflict1 or conflict2)),
            'assets': assets, 'issues': sorted(set(issues)), 'failures': raw['failures'],
            'recent_flows': [{'asset': r['asset'], 'type': r['incomeType'], 'amount': r['income'],
                              'time_ms': r['time'], 'transaction_id': r['tranId'], 'symbol': r['symbol']}
                             for r in rows[-50:]],
            'ledger_reconciliation': 'NOT_EVALUATED', 'strategy_pnl': None, 'history_complete': False,
            'source_verified': False, 'submission_enabled': False, 'authoritative': False}


def save_capture(directory, capture):
    validate(capture)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = json.dumps(capture, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError('CASH_WINDOW_TOO_LARGE')
    digest = hashlib.sha256(raw).hexdigest()
    path = directory/(digest+'.json')
    if path.is_symlink():
        raise ValueError('CASH_WINDOW_SYMLINK')
    try:
        with path.open('xb') as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
        path.chmod(0o600)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ValueError('CASH_WINDOW_COLLISION') from None
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=directory, delete=False) as f:
            temporary = Path(f.name)
            json.dump({'sha256': digest}, f); f.flush(); os.fsync(f.fileno())
        os.replace(temporary, directory/'latest.json')
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_capture(directory):
    directory = Path(directory)
    pointer = directory/'latest.json'
    if pointer.is_symlink():
        raise ValueError('CASH_WINDOW_SYMLINK')
    with pointer.open('rb') as f:
        data = load_json_without_duplicate_keys(f.read(1025))
    _fields(data, 'sha256'); _sha(data['sha256'])
    path = directory/(data['sha256']+'.json')
    if path.is_symlink():
        raise ValueError('CASH_WINDOW_SYMLINK')
    with path.open('rb') as f:
        raw = f.read(MAX_BYTES+1)
    if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != data['sha256']:
        raise ValueError('CASH_WINDOW_INTEGRITY')
    return validate(load_json_without_duplicate_keys(raw))
