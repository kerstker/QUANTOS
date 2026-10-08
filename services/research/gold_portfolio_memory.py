"""Bounded prior shared-account diagnostics; never economic approval or labels."""
from copy import deepcopy
import json
import re
from services.execution.backtest_authority import exact_identity_hash
from services.research.gold_bar_diagnostics import _number, validate_target_diagnostics

ACCOUNT = ('net_equity_change','fees','funding','max_observed_drawdown','reconciliation_residual')
COUNTS = ('order_count','filled_order_count','open_position_count')
REVIEWS = ('INDEPENDENT_AUTHORITY_UNCONFIGURED','REJECTED','VERIFIED_SIGNATURE_ENGINEERING_ONLY')


def _hash(value):
    return exact_identity_hash(value,name='gold_portfolio_memory')


def _digest(value):
    return type(value) is str and re.fullmatch('[a-f0-9]{64}',value) is not None


def validate_memory(value):
    fields={'schema','usage','as_of_ms','next_attempt','records','economic_eligible','result_hash'}
    if (type(value) is not dict or set(value)!=fields
            or value['schema']!='GOLD_BAR_PORTFOLIO_MEMORY_V1'
            or value['usage']!='PRIOR_TECHNICAL_DIAGNOSTICS_NOT_PERFORMANCE_LABELS'
            or value['economic_eligible'] is not False
            or type(value['as_of_ms']) is not int or not 0<=value['as_of_ms']<=2**63-1
            or type(value['next_attempt']) is not int or not 1<=value['next_attempt']<=1000
            or type(value['records']) is not list or not 1<=len(value['records'])<=7
            or value['result_hash']!=_hash({k:v for k,v in value.items() if k!='result_hash'})):
        raise ValueError('INVALID_GOLD_PORTFOLIO_MEMORY')
    previous=0
    for row in value['records']:
        if (type(row) is not dict or set(row)!={'attempt','result_hash','known_at_ms','review_status','portfolio'}
                or type(row['attempt']) is not int or not previous<row['attempt']<value['next_attempt']
                or not _digest(row['result_hash']) or row['review_status'] not in REVIEWS
                or row['known_at_ms'] is not None and (type(row['known_at_ms']) is not int
                    or not 0<=row['known_at_ms']<=value['as_of_ms'])):
            raise ValueError('INVALID_GOLD_PORTFOLIO_MEMORY_CLOCK_OR_IDENTITY')
        previous=row['attempt']
        portfolio=row['portfolio']
        if portfolio is None:
            continue
        if (type(portfolio) is not dict
                or set(portfolio)-{'target_diagnostics'}!={'trial_id','result_hash','account','loss_status',*COUNTS}
                or not _digest(portfolio['trial_id']) or not _digest(portfolio['result_hash'])
                or portfolio['loss_status'] not in (None,'ACTIVE','STOPPED_FLAT','STOPPING_UNRESOLVED')
                or any(type(portfolio[k]) is not int or not 0<=portfolio[k]<=1000000 for k in COUNTS)
                or portfolio['filled_order_count']>portfolio['order_count']
                or type(portfolio['account']) is not dict or set(portfolio['account'])!=set(ACCOUNT)):
            raise ValueError('INVALID_GOLD_PORTFOLIO_MEMORY_ACCOUNT')
        if 'target_diagnostics' in portfolio:
            validate_target_diagnostics(portfolio['target_diagnostics'])
        for number in portfolio['account'].values():
            if number is not None:
                _number(number)
        account=portfolio['account']
        if (account['reconciliation_residual'] is not None and _number(account['reconciliation_residual'])!=0
                or account['max_observed_drawdown'] is not None and _number(account['max_observed_drawdown'])<0):
            raise ValueError('UNRECONCILED_GOLD_PORTFOLIO_MEMORY')
    if len(json.dumps(value,allow_nan=False).encode())>32768:
        raise ValueError('GOLD_PORTFOLIO_MEMORY_BUDGET')
    return deepcopy(value)


def build_memory(completed, *, next_attempt, as_of_ms):
    if not any('portfolio' in row['report'] for row in completed):
        return None
    rows=[]
    for row in completed:
        original=row['report'].get('portfolio')
        portfolio=None
        if original is not None:
            portfolio=dict(trial_id=original['trial_id'],result_hash=original['result_hash'],
                account={k:(original.get('account') or {}).get(k) for k in ACCOUNT},
                loss_status=(original.get('loss_control') or {}).get('status'),
                **{k:original[k] for k in COUNTS})
            if 'target_diagnostics' in original:
                portfolio['target_diagnostics'] = validate_target_diagnostics(original['target_diagnostics'])
        rows.append(dict(attempt=row['attempt'],result_hash=row['result_hash'],
            known_at_ms=row.get('reviewed_at_ms'),review_status=row['review']['status'],portfolio=portfolio))
    document=dict(schema='GOLD_BAR_PORTFOLIO_MEMORY_V1',
        usage='PRIOR_TECHNICAL_DIAGNOSTICS_NOT_PERFORMANCE_LABELS',as_of_ms=as_of_ms,
        next_attempt=next_attempt,records=rows,economic_eligible=False)
    document['result_hash']=_hash(document)
    return validate_memory(document)


def continue_memory(prior, completed, *, next_attempt, as_of_ms):
    """Retain imported diagnostics alongside new independent experiment records."""
    if prior is None:
        return build_memory(completed, next_attempt=next_attempt, as_of_ms=as_of_ms)
    old = validate_memory(prior)
    if old['as_of_ms'] > as_of_ms or old['next_attempt'] > next_attempt:
        raise ValueError('IMPORTED_MEMORY_CLOCK_OR_ATTEMPT')
    if any(row['portfolio'] is None or row['portfolio']['loss_status'] != 'ACTIVE'
           for row in old['records']):
        raise ValueError('IMPORTED_MEMORY_LOSS_STOP_OR_UNKNOWN')
    current = build_memory(completed, next_attempt=next_attempt, as_of_ms=as_of_ms)
    if completed and current is None:
        raise ValueError('IMPORTED_MEMORY_PORTFOLIO_REQUIRED')
    document = {k: v for k, v in old.items() if k != 'result_hash'}
    document.update(next_attempt=next_attempt, as_of_ms=as_of_ms,
                    records=old['records'] + (current['records'] if current else []))
    document['result_hash'] = _hash(document)
    return validate_memory(document)
