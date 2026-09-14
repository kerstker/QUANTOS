"""Read-only progress of one finite Demo execution and cash comparison."""
from decimal import Decimal
import re
from services.research import demo_display_store
from services.research.demo_oms_status import _hash, _time

SCHEMA='DEMO_FUNCTIONAL_STATUS_V1'
ACTIVE=('OPENING_CASH_CAPTURE','OPEN_IOC','RECEIPT_RECOVERY','REDUCE_ONLY_IOC','CASH_RECONCILIATION')
TERMINAL=('DEMO_CYCLE_COMPLETED','RECOVERY_REQUIRED','NO_TRADING_EVIDENCE',
    'CASH_RECONCILIATION_REQUIRED','REDUCTION_INCOMPLETE','CYCLE_NOT_COMPLETED','REDUCTION_AUTHORIZATION_REQUIRED','EVIDENCE_REPAIR_REQUIRED')


def validate(raw):
    if type(raw) is not dict or set(raw)!=set(('schema environment scope phase observed_at_ms source_commit '
        'cycle_sha256 orders_attempted final_quantity fill_count cash_status cash_reconciled economic_authority submission_enabled').split()):
        raise ValueError('FUNCTIONAL_STATUS_FIELDS')
    if (raw['schema']!=SCHEMA or raw['environment']!='BINANCE_USDM_DEMO' or raw['scope']!='TECHNICAL_VALIDATION_ONLY' or
        raw['phase'] not in ACTIVE+TERMINAL or raw['economic_authority'] is not False or raw['submission_enabled'] is not False):
        raise ValueError('FUNCTIONAL_STATUS_SCOPE')
    _time(raw['observed_at_ms']);_hash(raw['cycle_sha256'])
    if type(raw['source_commit']) is not str or not re.fullmatch('[a-f0-9]{40}',raw['source_commit']):raise ValueError('FUNCTIONAL_STATUS_SOURCE')
    for key,maximum in (('orders_attempted',3),('fill_count',10000)):
        if raw[key] is not None and (type(raw[key]) is not int or not 0<=raw[key]<=maximum):raise ValueError('FUNCTIONAL_STATUS_COUNT')
    quantity=raw['final_quantity']
    if quantity is not None and (type(quantity) is not str or not re.fullmatch(r'\d{1,20}(\.\d{1,18})?',quantity)):
        raise ValueError('FUNCTIONAL_STATUS_QUANTITY')
    if raw['cash_status'] not in (None,'MATCHED_REPORTED_INPUTS','UNRESOLVED','DIFFERENCE_FOUND'):raise ValueError('FUNCTIONAL_STATUS_CASH')
    completed=raw['phase']=='DEMO_CYCLE_COMPLETED'
    if raw['cash_reconciled'] is not completed:raise ValueError('FUNCTIONAL_STATUS_COMPLETION')
    if completed and (raw['orders_attempted'] is None or raw['orders_attempted']<2 or
        raw['fill_count'] is None or raw['fill_count']<2 or quantity is None or Decimal(quantity)!=0 or
        raw['cash_status']!='MATCHED_REPORTED_INPUTS'):raise ValueError('FUNCTIONAL_STATUS_COMPLETION_EVIDENCE')
    return raw


def build(*,phase,source_commit,cycle_sha256,observed_at_ms,result=None):
    result=result or {}
    return validate({'schema':SCHEMA,'environment':'BINANCE_USDM_DEMO','scope':'TECHNICAL_VALIDATION_ONLY',
        'phase':phase,'observed_at_ms':observed_at_ms,'source_commit':source_commit,'cycle_sha256':cycle_sha256,
        **{k:result.get(k) for k in ('orders_attempted','final_quantity','fill_count','cash_status')},
        'cash_reconciled':result.get('cash_reconciled',False),'economic_authority':False,'submission_enabled':False})


def save(directory,raw):return demo_display_store.save(directory,raw,validator=validate)
def load_latest(directory):return demo_display_store.load_latest(directory,validator=validate)
