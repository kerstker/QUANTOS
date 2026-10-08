"""Frozen prior technical outcomes, never validation scores or cash labels."""
from copy import deepcopy
import re


_SIGNAL_REASONS = frozenset(('TERMINAL_ZONE', 'ZONE_EXPIRED_OR_BREACHED',
    'LATER_WINDOW_CONFIRMED', 'CONFIRMATION_WAIT', 'CONFIRMATION_TIMEOUT',
    'SELL_PRESSURE_WITH_RECLAIM', 'CONDITIONS_NOT_MET'))


def validate_signal_diagnostics(value):
    if (type(value) is not dict or set(value) != {'evaluated_windows', 'reason_counts', 'final_state'}
            or type(value['evaluated_windows']) is not int or not 1 <= value['evaluated_windows'] <= 1000
            or value['final_state'] not in ('WATCHING', 'CONFIRMING', 'SIGNALLED', 'EXPIRED')
            or type(value['reason_counts']) is not dict or not value['reason_counts']
            or any(k not in _SIGNAL_REASONS or type(v) is not int or v <= 0
                   for k, v in value['reason_counts'].items())
            or sum(value['reason_counts'].values()) != value['evaluated_windows']):
        raise ValueError('invalid gold signal diagnostics')
    return deepcopy(value)


def signal_diagnostics(report):
    """Only completed signal observations; no prices, return scores or free text."""
    replay = report['signal_replay']
    if replay['status'] != 'RESEARCH_SIGNALS_ONLY':
        raise ValueError('incomplete gold signal diagnostics')
    records = replay['records']
    if type(records) is not list or not 1 <= len(records) <= 1000:
        raise ValueError('invalid gold signal records')
    counts = {}
    for record in records:
        reason = record['decision']['reason']
        if reason not in _SIGNAL_REASONS:
            raise ValueError('invalid gold signal reason')
        counts[reason] = counts.get(reason, 0) + 1
    return validate_signal_diagnostics({'evaluated_windows': len(records),
        'reason_counts': counts, 'final_state': records[-1]['decision']['state']})


def validate_memory(value):
    from services.research.ai_factory_store import digest
    fields={'schema','usage','as_of_ms','records','economic_eligible','result_hash'}
    if (type(value) is not dict or set(value)!=fields or value['schema'] not in ('GOLD_RESEARCH_MEMORY_V1','GOLD_RESEARCH_MEMORY_V2','GOLD_RESEARCH_MEMORY_V3')
            or value['usage']!='TECHNICAL_DIAGNOSTIC_NOTES' or value['economic_eligible'] is not False
            or type(value['as_of_ms']) is not int or not 0<=value['as_of_ms']<=8640000000000000
            or value['result_hash']!=digest({k:v for k,v in value.items() if k!='result_hash'})):
        raise ValueError('invalid gold research memory')
    rows=value['records']
    if type(rows) is not list or not 1<=len(rows)<=6:raise ValueError('invalid gold memory size')
    seen=set()
    for row in rows:
        failed=type(row) is dict and row.get('outcome')=='RESEARCH_FAILED' and value['schema'] in ('GOLD_RESEARCH_MEMORY_V2','GOLD_RESEARCH_MEMORY_V3')
        hashes=('input_sha256','config_sha256','trial_receipts_sha256') if failed else ('trial_id','input_sha256','report_sha256')
        row_fields={'attempt','candidate_id','outcome','known_at_ms',*hashes}
        if type(row) is dict and 'signal_diagnostics' in row and not failed and value['schema']=='GOLD_RESEARCH_MEMORY_V3':
            validate_signal_diagnostics(row['signal_diagnostics'])
            row_fields.add('signal_diagnostics')
        if (type(row) is not dict or set(row)!=row_fields
                or type(row['attempt']) is not int or not 1<=row['attempt']<=1000 or row['attempt'] in seen
                or not (failed and row['candidate_id'] is None or type(row['candidate_id']) is int and 0<=row['candidate_id']<6)
                or type(row['known_at_ms']) is not int or not 0<=row['known_at_ms']<=value['as_of_ms']
                or not failed and row['outcome'] not in ('NO_SIGNAL','OPEN_AT_END','FLAT_TERMINAL')
                or any(type(row[k]) is not str or re.fullmatch('[a-f0-9]{64}',row[k]) is None
                       for k in hashes)):
            raise ValueError('invalid gold memory record')
        seen.add(row['attempt'])
    if rows!=sorted(rows,key=lambda r:(r['known_at_ms'],r['attempt'])):raise ValueError('unordered gold memory')
    return deepcopy(value)


def campaign_memory(store,campaign_id,*,now,feedback_attempt=None,include_current=True):
    """Prior experiments, including the execution source verified by the store."""
    from services.research.ai_factory_store import digest
    batch=store.c.execute('SELECT root_id,ordinal FROM collection_program_batches WHERE campaign_id=?',(campaign_id,)).fetchone()
    prior=store.c.execute('''
        SELECT r.attempt,r.finished_ms,r.state FROM campaign_rounds r
        LEFT JOIN collection_program_batches b ON b.campaign_id=r.campaign_id
        WHERE r.state IN ('COMPLETED','FAILED') AND
            (r.campaign_id=? OR (b.root_id=? AND b.ordinal<?) OR r.attempt=?)
        ORDER BY r.finished_ms DESC,r.attempt DESC LIMIT 6
    ''',(campaign_id if include_current else -1,batch['root_id'] if batch else None,batch['ordinal'] if batch else None,feedback_attempt)).fetchall()
    rows=[]
    for round_ in prior:
        session=store.get(round_['attempt']);summary=session['result']
        if session['state']!=round_['state']:continue
        if session['state']=='COMPLETED' and not summary:continue
        known=max(session['updated'],round_['finished_ms'])
        if known>now:raise ValueError('future gold research memory')
        if session['state']=='FAILED':
            receipts=store.trials(session['attempt'])
            if (len(receipts)!=1 or set(receipts[0])!={'candidate_id','status'} or receipts[0]['status']!='FAILED'):
                raise ValueError('invalid failed gold memory receipt')
            rows.append({'attempt':session['attempt'],'candidate_id':receipts[0]['candidate_id'],
                'input_sha256':session['config']['input_sha256'],'config_sha256':session['config_hash'],
                'trial_receipts_sha256':digest(receipts),'outcome':'RESEARCH_FAILED','known_at_ms':known})
            continue
        rows.append({'attempt':session['attempt'],'candidate_id':summary['candidate_id'],
                     'trial_id':summary['trial_id'],'input_sha256':session['config']['input_sha256'],
                     'report_sha256':summary['report_sha256'],'outcome':summary['outcome'],'known_at_ms':known})
        if 'signal_diagnostics' in summary:
            rows[-1]['signal_diagnostics']=validate_signal_diagnostics(summary['signal_diagnostics'])
    if not rows:return None
    schema='GOLD_RESEARCH_MEMORY_V2' if any(r['outcome']=='RESEARCH_FAILED' for r in rows) else 'GOLD_RESEARCH_MEMORY_V1'
    if any('signal_diagnostics' in r for r in rows):schema='GOLD_RESEARCH_MEMORY_V3'
    value={'schema':schema,'usage':'TECHNICAL_DIAGNOSTIC_NOTES','as_of_ms':now,
           'records':sorted(rows,key=lambda r:(r['known_at_ms'],r['attempt'])),'economic_eligible':False}
    return validate_memory({**value,'result_hash':digest(value)})
