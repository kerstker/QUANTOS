"""Strict, unverified issuer budget display. Contains no grant or private input."""
from decimal import Decimal
from fractions import Fraction
import re

from services.execution.perpetual_ledger_v2 import ledger_decimal_context
from services.research import demo_display_store
from services.research.demo_oms_status import _hash,_time

SCHEMA='DEMO_ISSUER_STATUS_V1'
SCHEMA_V2='DEMO_ISSUER_STATUS_V2'
PHASES=('POLICY_INACTIVE','LIMIT_REACHED','PENDING_SIGNATURE','POLICY_OBSERVED')


def require(ok,reason):
    if not ok:raise ValueError(reason)


def exact(value,fields):
    require(type(value) is dict and set(value)==set(fields.split()),'ISSUER_DISPLAY_FIELDS')


def decimal(value):
    require(type(value) is str and re.fullmatch(r'\d{1,30}(\.\d{1,36})?',value) is not None,'ISSUER_DISPLAY_DECIMAL')
    return Decimal(value)


def phase(raw):
    future=raw.get('schema')==SCHEMA_V2
    if raw['key_status']!='ACTIVE' or not raw['valid_from_ms']<=raw['observed_at_ms']<raw['valid_until_ms']:
        return 'TEMPLATE_INACTIVE' if future else 'POLICY_INACTIVE'
    if any(b['reserved_grants']==b['max_grants'] or b['reserved_orders']==b['max_orders'] or
           b['remaining_notional'] is not None and decimal(b['remaining_notional'])==0 for b in raw['budgets']):
        return 'GROUP_LIMIT_REACHED' if future else 'LIMIT_REACHED'
    if any(b['reserved_grants']>b['signed_grants'] for b in raw['budgets']):return 'GROUP_PENDING_SIGNATURE' if future else 'PENDING_SIGNATURE'
    return 'TEMPLATE_OBSERVED' if future else 'POLICY_OBSERVED'


@ledger_decimal_context()
def validate(raw):
    future=type(raw) is dict and raw.get('schema')==SCHEMA_V2
    identity='template_id template_sha256 budget_basis group_id group_binding_sha256 continuity_event_sha256 exit_reserve opening_remaining ' if future else 'policy_id policy_sha256 '
    exact(raw,'schema environment scope phase observed_at_ms source_commit account_ref_sha256 '+identity+
        'symbol valid_from_ms valid_until_ms key_status key_material_status records last_record_at_ms journal_sha256 budgets '
        'source_verified submission_enabled cash_reconciled economic_authority')
    require(raw['schema'] in (SCHEMA,SCHEMA_V2) and raw['environment']=='BINANCE_USDM_DEMO' and raw['scope']=='TECHNICAL_VALIDATION_ONLY','ISSUER_DISPLAY_SCOPE')
    require(all(raw[k] is False for k in ('source_verified','submission_enabled','cash_reconciled','economic_authority')),'ISSUER_DISPLAY_AUTHORITY')
    require(type(raw['source_commit']) is str and re.fullmatch('[a-f0-9]{40}',raw['source_commit']) is not None,'ISSUER_DISPLAY_SOURCE')
    for name in ('account_ref_sha256','template_sha256' if future else 'policy_sha256'):_hash(raw[name])
    identifier=raw['template_id'] if future else raw['policy_id']
    require(type(identifier) is str and re.fullmatch('[A-Za-z0-9_.:-]{1,128}',identifier) is not None,'ISSUER_DISPLAY_POLICY_ID')
    require(raw['symbol'] in (('XAUUSDT',) if future else ('BTCUSDC','BTCUSDT')) and raw['key_status'] in ('ACTIVE','REVOKED','MISSING') and raw['key_material_status']=='NOT_CHECKED','ISSUER_DISPLAY_POLICY')
    for name in ('observed_at_ms','valid_from_ms','valid_until_ms'):_time(raw[name])
    require(raw['valid_from_ms']<raw['valid_until_ms'],'ISSUER_DISPLAY_WINDOW')
    require(type(raw['records']) is int and 0<=raw['records']<=20000,'ISSUER_DISPLAY_RECORDS')
    if raw['records']:
        _hash(raw['journal_sha256']);_time(raw['last_record_at_ms'])
        require(raw['last_record_at_ms']<=raw['observed_at_ms'],'ISSUER_DISPLAY_FUTURE')
    else:require(raw['journal_sha256'] is None and raw['last_record_at_ms'] is None,'ISSUER_DISPLAY_EMPTY')
    budgets=raw['budgets'];require(type(budgets) is list and len(budgets)==2,'ISSUER_DISPLAY_BUDGETS')
    for purpose,b in zip(('submission','cancellation'),budgets):
        exact(b,'purpose reserved_grants signed_grants reserved_orders max_grants max_orders reserved_notional max_notional remaining_notional')
        require(b['purpose']==purpose,'ISSUER_DISPLAY_PURPOSE')
        require(all(type(b[k]) is int for k in ('reserved_grants','signed_grants','reserved_orders','max_grants','max_orders')),'ISSUER_DISPLAY_COUNTS')
        require(0<=b['signed_grants']<=b['reserved_grants']<=b['max_grants']<=4096 and b['max_grants']>0 and
            b['reserved_grants']<=b['reserved_orders']<=min(b['max_orders'],b['reserved_grants']*20) and
            0<b['max_orders']<=50000,'ISSUER_DISPLAY_COUNT_RELATION')
        if purpose=='cancellation':require(all(b[k] is None for k in ('reserved_notional','max_notional','remaining_notional')),'ISSUER_DISPLAY_CANCEL_NOTIONAL')
        else:
            used,limit,remaining=(decimal(b[k]) for k in ('reserved_notional','max_notional','remaining_notional'))
            require(limit>0 and Fraction(used)+Fraction(remaining)==Fraction(limit) and (used>0)==(b['reserved_orders']>0),'ISSUER_DISPLAY_NOTIONAL')
    require(sum(b['reserved_grants']+b['signed_grants'] for b in budgets)<=raw['records'],'ISSUER_DISPLAY_HISTORY_COUNTS')
    if future:_group_metadata(raw)
    require(raw['phase']==phase(raw),'ISSUER_DISPLAY_PHASE')
    return raw


def _group_metadata(raw):
    require(raw['budget_basis']=='SHARED_GROUP','ISSUER_DISPLAY_BASIS')
    require(type(raw['group_id']) is str and re.fullmatch('[A-Za-z0-9_.:-]{1,128}',raw['group_id']) is not None,
        'ISSUER_DISPLAY_GROUP_ID')
    for name in ('group_binding_sha256','continuity_event_sha256'):_hash(raw[name])
    reserve=raw['exit_reserve'];left=raw['opening_remaining'];budget=raw['budgets'][0]
    exact(reserve,'grants orders notional');exact(left,'grants orders notional')
    for name in ('grants','orders'):
        require(type(reserve[name]) is int and type(left[name]) is int
            and 0<reserve[name]<budget['max_'+name]
            and left[name]==max(0,budget['max_'+name]-budget['reserved_'+name]-reserve[name]),
            'ISSUER_DISPLAY_OPENING_COUNTS')
    held,available,limit,used=(Fraction(decimal(value)) for value in
        (reserve['notional'],left['notional'],budget['max_notional'],budget['reserved_notional']))
    require(0<held<limit and available==max(Fraction(0),limit-used-held),'ISSUER_DISPLAY_OPENING_NOTIONAL')
    require(sum(b['reserved_grants']+b['signed_grants'] for b in raw['budgets'])<raw['records'],
        'ISSUER_DISPLAY_CONTINUITY_RECORD')


def save(directory,raw):return demo_display_store.save(directory,raw,validator=validate)


def load_latest(directory):return demo_display_store.load_latest(directory,validator=validate)
