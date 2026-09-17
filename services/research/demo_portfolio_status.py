"""Small read-only portfolio cycle projection; never an execution permission."""
import re
import hashlib
import json
from decimal import Decimal

from services.research import demo_display_store
from services.research.demo_oms_status import _hash, _time

SCHEMA='DEMO_PORTFOLIO_STATUS_V1'
STATUSES=('NO_CHANGE','QUERY_ONLY','TASK_READY','EXECUTION_ATTEMPT_RECORDED','EXECUTION_NOT_COMPLETED',
    'CASH_RECONCILIATION_REQUIRED','CASH_RECONCILED','INPUTS_UNRESOLVED','CONTROLLER_RESULT_NOT_RETAINED',
    'ALREADY_FLAT','CLOSE_RECOVERY_REQUIRED','CLOSING_REQUIRED','DECISION_BUDGET_RESERVED',
    'EXPIRED_QUERY_ONLY','CYCLE_DEADLINE_REACHED')
FIELDS=('schema environment scope source_commit observed_at_ms status phase allocation_sha256 model_hash capital '
    'policy_valid_until_ms task_sha256 close_index final_quantity fill_count cash_status cash_report_sha256 '
    'wallet_residual cash_reconciled economic_authority submission_enabled').split()


def validate(raw):
    if type(raw) is not dict or set(raw)!=set(FIELDS):raise ValueError('PORTFOLIO_STATUS_FIELDS')
    if (raw['schema']!=SCHEMA or raw['environment']!='BINANCE_USDM_DEMO' or
        raw['scope']!='TECHNICAL_VALIDATION_ONLY' or raw['status'] not in STATUSES or
        raw['economic_authority'] is not False or raw['submission_enabled'] is not False):
        raise ValueError('PORTFOLIO_STATUS_SCOPE')
    _time(raw['observed_at_ms'])
    if type(raw['source_commit']) is not str or not re.fullmatch('[a-f0-9]{40}',raw['source_commit']):
        raise ValueError('PORTFOLIO_STATUS_SOURCE')
    if type(raw['phase']) is not str or not re.fullmatch('[A-Z_]{1,64}',raw['phase']):raise ValueError('PORTFOLIO_STATUS_PHASE')
    for name in ('allocation_sha256','model_hash','task_sha256','cash_report_sha256'):
        if raw[name] is not None:_hash(raw[name])
    if raw['policy_valid_until_ms'] is not None:_time(raw['policy_valid_until_ms'])
    for name in ('capital','final_quantity','wallet_residual'):
        value=raw[name]
        if value is not None and (type(value) is not str or not re.fullmatch(r'-?\d{1,20}(\.\d{1,18})?',value)):
            raise ValueError('PORTFOLIO_STATUS_DECIMAL')
    if raw['capital'] is not None and Decimal(raw['capital'])<=0:raise ValueError('PORTFOLIO_STATUS_CAPITAL')
    for name,maximum in (('close_index',2),('fill_count',10000)):
        if raw[name] is not None and (type(raw[name]) is not int or not 0<=raw[name]<=maximum):raise ValueError('PORTFOLIO_STATUS_COUNT')
    if raw['close_index']==0:raise ValueError('PORTFOLIO_STATUS_CLOSE_INDEX')
    if raw['cash_status'] not in (None,'MATCHED_REPORTED_INPUTS'):raise ValueError('PORTFOLIO_STATUS_CASH')
    complete=raw['status']=='CASH_RECONCILED'
    if raw['cash_reconciled'] is not complete:raise ValueError('PORTFOLIO_STATUS_COMPLETION')
    if complete and (any(raw[k] is None for k in ('allocation_sha256','model_hash','capital','policy_valid_until_ms',
        'cash_report_sha256','fill_count')) or raw['fill_count']<2 or raw['cash_status']!='MATCHED_REPORTED_INPUTS' or
        raw['final_quantity']!='0' or raw['wallet_residual']!='0'):
        raise ValueError('PORTFOLIO_STATUS_CASH_EVIDENCE')
    return raw


def build(result,*,config=None,policy=None):
    allocation=(config or {}).get('allocation')
    cash=(result.get('reconciliation') or {}).get('cash') or {}
    if cash and cash.get('economic_authority') is not False:raise ValueError('PORTFOLIO_STATUS_CASH_AUTHORITY')
    return validate({'schema':SCHEMA,'environment':'BINANCE_USDM_DEMO','scope':'TECHNICAL_VALIDATION_ONLY',
        'source_commit':result['source_commit'],'observed_at_ms':result['observed_at_ms'],
        'status':result['status'],'phase':result.get('phase','CYCLE'),
        'allocation_sha256':None if allocation is None else hashlib.sha256(json.dumps(allocation,
            sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest(),
        'model_hash':None if allocation is None else allocation['model_hash'],
        'capital':None if allocation is None else allocation['capital'],
        'policy_valid_until_ms':(policy or {}).get('valid_until_ms'),'task_sha256':result.get('task_sha256'),
        'close_index':result.get('close_index'),'final_quantity':cash.get('final_quantity'),
        'fill_count':cash.get('fill_count'),'cash_status':cash.get('status'),'cash_report_sha256':cash.get('display_report_sha256'),
        'wallet_residual':cash.get('wallet_residual'),'cash_reconciled':result.get('cash_reconciled',False),
        'economic_authority':False,'submission_enabled':False})


def save(directory,raw):return demo_display_store.save(directory,raw,validator=validate)
def load_latest(directory):return demo_display_store.load_latest(directory,validator=validate)
