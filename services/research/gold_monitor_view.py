"""Bounded, identifier-redacted monitor view; local observations only."""
from services.research.gold_trade_observation import _require, _time, _amount, assess
from services.research.gold_account_snapshot import _side, _text
from services.research.gold_account_journal import _facts

COUNTS=('records','event_count','duplicate_events','unique_trades','trade_conflicts','out_of_order_events','gap_count')
STATUSES=('EMPTY','CONNECTING','CONNECTED','DISCONNECTED','STOPPED','DATA_CONFLICT','STALE')
RECOVERY=('INCOMPLETE','BOUNDARY_ACTIVITY','CHANGED_DURING_READ','MISMATCH','OTHER_MARKETS_UNCHECKED','WINDOW_CHECKED','SNAPSHOT_CHANGED')


def activity_view(baseline,rows,*,rest_fills=None):
    positions={};cutoff=baseline['completed_at_ms']
    for row in baseline['reads']:
        if row['kind']=='positions':
            for p in row['data']:
                if p['symbol']=='XAUUSDT':positions[p['positionSide']]={'side':p['positionSide'],
                    'quantity':p['positionAmt'],'entry_price':p['entryPrice'],'observed_at_ms':cutoff,'source':'SNAPSHOT'}
    for row in rows:
        if row['kind']!='EVENT' or row['at_ms']<cutoff:continue
        event=row['data']['payload']
        if event['e']=='ACCOUNT_UPDATE':
            for p in event['a'].get('P',[]):
                if p['s']=='XAUUSDT':positions[p['ps']]={'side':p['ps'],'quantity':p['pa'],
                    'entry_price':p['ep'],'observed_at_ms':row['at_ms'],'source':'PARTIAL_STREAM_UPDATE'}
    facts=_facts(rows)
    if facts['conflicts'] or facts['out_of_order']:positions={}
    combined={k[1]:v for k,v in facts['trades'].items() if k[0]=='XAUUSDT'}
    conflict=False
    if rest_fills is not None:
        assess(rest_fills)
        for row in rest_fills['fills']:
            old=combined.get(row['trade_id'])
            if old is not None:
                for k,v in old.items():
                    if k in ('price','quantity','commission','reported_realized_pnl'):
                        if v is None or _amount(v)!=_amount(row[k]):conflict=True
                    elif v!=row[k]:conflict=True
            combined[row['trade_id']]=row
    trades=[] if conflict or facts['conflicts'] or facts['out_of_order'] else sorted(combined.values(),key=lambda r:r['time_ms'])[-20:]
    return {'positions':list(positions.values()),'position_complete':False,
        'recent_trades':[{k:r[k] for k in ('time_ms','side','position_side','price','quantity','commission','commission_asset')} for r in trades]}


def validate_view(value,*,now_ms):
    _require(isinstance(value,dict) and value['schema']=='GOLD_MONITOR_VIEW_V1','INVALID_MONITOR_VIEW')
    at=_time(value['as_of_ms']);_require(at<=_time(now_ms),'FUTURE_MONITOR_VIEW')
    s=value['summary']
    _require(s['schema']=='GOLD_ACCOUNT_JOURNAL_SUMMARY_V1' and s['status'] in STATUSES and
        all(s[k] is False for k in ('source_authenticated','history_complete','submission_enabled')) and s['net_pnl'] is None,
        'INVALID_MONITOR_SUMMARY')
    result={k:_time(s[k]) for k in COUNTS}
    _require(s['reconciliation'] in ('UNKNOWN','REQUIRED'),'INVALID_RECONCILIATION')
    result.update(status='STALE' if now_ms-at>120000 else s['status'],observed_at_ms=at,
        age_ms=now_ms-at,reconciliation=s['reconciliation'],recovery_status='NOT_CHECKED',
        positions=[],recent_trades=[],position_complete=False,history_complete=False,source_authenticated=False)
    if value.get('recovery') is not None:
        recovery=value['recovery'];_require(recovery['status'] in RECOVERY and recovery['history_complete'] is False,'INVALID_RECOVERY')
        result['recovery_status']=recovery['status']
        for k in ('recovered_fills','income_records','ordinary_order_records','conditional_order_records'):
            result[k]=_time(recovery[k]) if k in recovery else None
    activity=value.get('activity')
    if activity:
        _require(activity['position_complete'] is False and len(activity['positions'])<=3 and len(activity['recent_trades'])<=20,'INVALID_ACTIVITY')
        for p in activity['positions']:
            _side(p['side']);_amount(p['quantity']);_amount(p['entry_price']);_time(p['observed_at_ms'])
            _require(p['observed_at_ms']<=at and p['source'] in ('SNAPSHOT','PARTIAL_STREAM_UPDATE'),'INVALID_POSITION_OBSERVATION')
            result['positions'].append({k:p[k] for k in ('side','quantity','entry_price','observed_at_ms','source')})
        for t in activity['recent_trades']:
            _time(t['time_ms']);_side(t['position_side'])
            _require(t['side'] in ('BUY','SELL'),'INVALID_TRADE_SIDE')
            for k in ('price','quantity'):_amount(t[k],positive=True)
            if t['commission'] is not None:_amount(t['commission'])
            if t['commission_asset'] is not None:_text(t['commission_asset'])
            result['recent_trades'].append({k:t[k] for k in ('time_ms','side','position_side','price','quantity','commission','commission_asset')})
    return result
