"""Exact, offline diagnostics of retained Demo terms and account flows.

Reported income is compared with reported fills, never applied a second time to
the canonical cash ledger. A bounded REST window is not complete account history.
"""
from decimal import Decimal
import hashlib
import json
import re
from pathlib import Path

from quantos.contracts.binance_demo_account import _decimal, _symbol, _text, _int, _bool, _side
from services.execution.perpetual_ledger_v2 import ledger_decimal_context, canonical_decimal
from services.governance.json_integrity import load_json_without_duplicate_keys

SCHEMA = 'BINANCE_DEMO_STATEMENT_V1'
MAX_BYTES = 32_000_000


def amount(value):
    if type(value) is int:value=str(value)
    elif type(value) is Decimal:value=format(value,'f')
    return _decimal(value)


def identity(value):
    if isinstance(value, str) and value.isascii() and value.isdigit():
        value = int(value)
    return str(_int(value))


def notional_bound(value):
    # Venue metadata can use INT64_MAX as the terminal ceiling. Preserve the
    # literal ceiling; this does not increase any ledger or order risk budget.
    if type(value) is int:value=str(value)
    elif type(value) is Decimal:value=format(value,'f')
    if not isinstance(value,str) or not re.fullmatch(r'\d{1,19}(\.\d{1,24})?',value) or not 0<=Decimal(value)<=2**63-1:
        raise ValueError('bounded exact venue notional ceiling required')
    return value


def normalize(kind, raw, symbols):
    """Whitelist data fields. Ignore free text, keys and unexpected metadata."""
    if kind == 'time':
        return {'serverTime': _int(raw['serverTime'])}
    if kind == 'exchange':
        rows = []
        for r in raw['symbols']:
            if r['symbol'] not in symbols:
                continue
            filters=[]
            for f in r['filters']:
                names = {'LOT_SIZE': ('stepSize','minQty','maxQty'),
                    'MARKET_LOT_SIZE': ('stepSize','minQty','maxQty'),
                    'PRICE_FILTER': ('tickSize','minPrice','maxPrice'),
                    'MIN_NOTIONAL': ('notional',),
                    'PERCENT_PRICE': ('multiplierUp','multiplierDown','multiplierDecimal')}.get(f['filterType'])
                if names:
                    filters.append({'filterType':f['filterType'], **{k:amount(f[k]) for k in names}})
            rows.append({**{k:_symbol(r[k]) for k in ('symbol','contractType','quoteAsset','marginAsset','status')},
                         'liquidationFee':amount(r['liquidationFee']), 'filters':filters})
        return rows
    if kind == 'brackets':
        rows=raw if isinstance(raw,list) else [raw]
        return [{'symbol':_symbol(r['symbol']), 'notionalCoef':amount(r.get('notionalCoef',1)),
            'brackets':[{**{k:_int(b[k]) for k in ('bracket','initialLeverage')},
                **{k:notional_bound(b[k]) for k in ('notionalFloor','notionalCap')},
                **{k:amount(b[k]) for k in ('maintMarginRatio','cum')}}
                for b in r['brackets']]} for r in rows if r['symbol'] in symbols]
    if kind == 'commission':
        if raw['symbol'] not in symbols:raise ValueError('symbol mismatch')
        return {'symbol':_symbol(raw['symbol']), **{k:amount(raw[k]) for k in ('makerCommissionRate','takerCommissionRate')}}
    if not isinstance(raw,list) or len(raw)>1000:raise ValueError('bounded rows required')
    rows=[]
    for r in raw:
        if kind == 'trades':
            if r['symbol'] not in symbols:raise ValueError('symbol mismatch')
            row={**{k:identity(r[k]) for k in ('id','orderId')}, 'symbol':_symbol(r['symbol']),
                'side':_text(r['side']), 'positionSide':_side(r['positionSide']),
                **{k:amount(r[k]) for k in ('price','qty','quoteQty','realizedPnl','commission')},
                'commissionAsset':_text(r['commissionAsset']), 'time':_int(r['time']), 'maker':_bool(r['maker'])}
            if row['side'] not in ('BUY','SELL') or any(Decimal(row[k])<=0 for k in ('price','qty','quoteQty')):
                raise ValueError('invalid fill')
        elif kind == 'income':
            row={'symbol':_symbol(r['symbol']) if r['symbol'] else '', 'incomeType':_text(r['incomeType']),
                'income':amount(r['income']), 'asset':_text(r['asset']), 'time':_int(r['time']),
                'tranId':identity(r['tranId']), 'tradeId':identity(r['tradeId']) if r['tradeId'] else ''}
        else:raise ValueError('unknown kind')
        rows.append(row)
    return rows


def validate(report):
    keys=set('schema status observed_at_ms completed_at_ms start_ms end_ms symbols source_sha256 reads trades income gaps'.split())
    if set(report) not in (keys,keys|{'attempt_failures'}):
        raise ValueError('statement fields')
    if report['schema']!=SCHEMA or report['status'] not in ('NOT_REQUESTED','CAPTURED','PARTIAL','FAILED'):
        raise ValueError('statement schema')
    for k in ('observed_at_ms','completed_at_ms','start_ms','end_ms'):_int(report[k])
    if not 0<=report['start_ms']<report['end_ms']<=report['observed_at_ms']<=report['completed_at_ms']:
        raise ValueError('statement clock')
    symbols=report['symbols']
    if not isinstance(symbols,list) or not 1<=len(symbols)<=30 or len(set(symbols))!=len(symbols):
        raise ValueError('bounded distinct symbols')
    for s in symbols:_symbol(s)
    attempts=report.get('attempt_failures',[])
    if not isinstance(attempts,list) or len(attempts)>300:raise ValueError('bounded attempts')
    for row in attempts:
        if set(row)!=set('kind symbol requested_at_ms received_at_ms reason'.split()):raise ValueError('attempt fields')
        if row['kind'] not in ('time','exchange','brackets','commission','trades','income'):raise ValueError('attempt kind')
        if row['symbol'] and row['symbol'] not in symbols:raise ValueError('attempt symbol')
        if row['reason'] not in ('REQUEST_FAILED','RATE_LIMITED','AUTHENTICATION_REJECTED','RESPONSE_TOO_LARGE'):raise ValueError('attempt reason')
        if not report['observed_at_ms']<=_int(row['requested_at_ms'])<=_int(row['received_at_ms'])<=report['completed_at_ms']:raise ValueError('attempt clock')
    digest=report['source_sha256']
    if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('source digest')
    for k in ('trades','income','reads','gaps'):
        if not isinstance(report[k],list) or len(report[k])>100_000:raise ValueError('bounded statement')
    normalized={k:[] for k in ('trades','income')}
    observed_reads=set()
    for read in report['reads']:
        if set(read)!=set('kind symbol start_ms end_ms requested_at_ms received_at_ms response_sha256 data'.split()):raise ValueError('read fields')
        for k in ('requested_at_ms','received_at_ms'):_int(read[k])
        if not report['observed_at_ms']<=read['requested_at_ms']<=read['received_at_ms']<=report['completed_at_ms']:
            raise ValueError('read clock')
        digest=read['response_sha256']
        if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('digest')
        kind=read['kind']
        if kind not in ('time','exchange','brackets','commission','trades','income'):raise ValueError('read kind')
        if read['symbol'] and read['symbol'] not in symbols:raise ValueError('read symbol')
        read_key=(kind,read['symbol'],read['start_ms'],read['end_ms'])
        if read_key in observed_reads:raise ValueError('duplicate statement read')
        observed_reads.add(read_key)
        selected=[read['symbol']] if read['symbol'] else symbols
        if kind not in normalized:
            payload={'symbols':read['data']} if kind=='exchange' else read['data']
            if normalize(kind,payload,selected)!=read['data']:raise ValueError('normalized terms changed')
        if read['kind'] in normalized:
            rows=normalize(read['kind'],read['data'],symbols)
            if rows!=read['data']:raise ValueError('normalized rows changed')
            if not report['start_ms']<=read['start_ms']<=read['end_ms']<=report['end_ms']:raise ValueError('window')
            if any(not read['start_ms']<=r['time']<=read['end_ms'] for r in rows):raise ValueError('out of window')
            normalized[read['kind']].extend(rows)
    for kind,rows in normalized.items():
        keys=('symbol','id') if kind=='trades' else ('incomeType','tranId')
        unique={}
        for row in rows:
            key=tuple(row[k] for k in keys)
            if key in unique and unique[key]!=row:raise ValueError('conflicting flow identity')
            unique[key]=row
        expected=sorted(unique.values(),key=lambda r:(r['time'],tuple(str(r[k]) for k in keys)))
        if expected!=report[kind]:raise ValueError('derived flows mismatch')
    for gap in report['gaps']:
        if set(gap)!=set('kind symbol start_ms end_ms reason'.split()):raise ValueError('gap fields')
        if gap['kind'] not in ('credentials','time','exchange','brackets','commission','trades','income'):raise ValueError('gap kind')
        if gap['symbol'] and gap['symbol'] not in symbols:raise ValueError('gap symbol')
        if gap['reason'] not in ('PAGE_LIMIT_REACHED','REQUEST_FAILED','RATE_LIMITED','AUTHENTICATION_REJECTED',
            'RESPONSE_TOO_LARGE','RESPONSE_INVALID','CAPTURE_BUDGET_EXHAUSTED','CLOCK_UNRELIABLE'):raise ValueError('gap reason')
        if not report['start_ms']<=gap['start_ms']<=gap['end_ms']<=report['end_ms']:raise ValueError('gap window')
    if report['status']=='CAPTURED' and report['gaps']:raise ValueError('captured with gaps')
    if report['status']=='CAPTURED':
        expected={('time','',None,None),('exchange','',None,None)}
        expected.update((kind,s,None,None) for s in symbols for kind in ('brackets','commission'))
        for start in range(report['start_ms'],report['end_ms']+1,7*86_400_000):
            end=min(report['end_ms'],start+7*86_400_000-1)
            expected.update(('trades',s,start,end) for s in symbols)
            expected.add(('income','',start,end))
        if observed_reads!=expected:raise ValueError('incomplete captured request coverage')
        if any(r['kind'] in normalized and len(r['data'])>=1000 for r in report['reads']):raise ValueError('truncated captured response')
    return report


@ledger_decimal_context()
def assess(report):
    validate(report)
    totals={}
    def asset(name):
        return totals.setdefault(name,dict.fromkeys(('reported_commission','reported_realized_pnl','income_total','income_commission'),Decimal(0)))
    for r in report['trades']:
        asset(r['commissionAsset'])['reported_commission']+=Decimal(r['commission'])
        quote=next((s['marginAsset'] for read in report['reads'] if read['kind']=='exchange'
                    for s in read['data'] if s['symbol']==r['symbol']),None)
        if quote:asset(quote)['reported_realized_pnl']+=Decimal(r['realizedPnl'])
    breakdown={}
    for r in report['income']:
        a=asset(r['asset']);a['income_total']+=Decimal(r['income'])
        if r['incomeType']=='COMMISSION':a['income_commission']+=Decimal(r['income'])
        key=(r['asset'],r['incomeType'])
        breakdown[key]=breakdown.get(key,Decimal(0))+Decimal(r['income'])
    assets=[{'asset':name,**{k:canonical_decimal(v) for k,v in a.items()},
             'fee_crosscheck_residual':canonical_decimal(a['reported_commission']+a['income_commission'])}
            for name,a in sorted(totals.items())]
    return {'schema':SCHEMA,'status':report['status'],'observed_at_ms':report['observed_at_ms'],
        'completed_at_ms':report['completed_at_ms'],'start_ms':report['start_ms'],'end_ms':report['end_ms'],
        'symbols':report['symbols'],'read_count':len(report['reads']),'trade_count':len(report['trades']),
        'failed_attempt_count':len(report.get('attempt_failures',[])),
        'income_count':len(report['income']),'assets':assets,'gaps':report['gaps'],
        'unqueried_income_symbols':sorted({r['symbol'] for r in report['income'] if r['symbol']}-set(report['symbols'])),
        'income_breakdown':[{'asset':asset,'type':kind,'amount':canonical_decimal(value)} for (asset,kind),value in sorted(breakdown.items())],
        'terms':[{'symbol':s,'exchange_rules':any(r['kind']=='exchange' and any(x['symbol']==s for x in r['data']) for r in report['reads']),
            'margin_brackets':any(r['kind']=='brackets' and any(x['symbol']==s for x in r['data']) for r in report['reads']),
            'commission':next((r['data'] for r in report['reads'] if r['kind']=='commission' and r['symbol']==s),None)} for s in report['symbols']],
        'history_complete':False,'venue_cash_reconciliation':'UNKNOWN','strategy_pnl':None,'submission_enabled':False}


def save_statement(directory,report):
    # Publication is explicit; the read-only projection never calls this path.
    from quantos.local_artifacts import atomic_write_json

    validate(report)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    raw=json.dumps(report,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    if len(raw)>MAX_BYTES:raise ValueError('statement too large')
    digest=hashlib.sha256(raw).hexdigest();path=directory/(digest+'.json')
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('statement conflict')
    else:
        with path.open('xb') as f:f.write(raw)
        path.chmod(0o600)
    atomic_write_json(directory/'latest.json',{'sha256':digest})


def load_statement(directory):
    directory=Path(directory)
    with (directory/'latest.json').open('rb') as f:pointer=load_json_without_duplicate_keys(f.read(256))
    digest=pointer.get('sha256')
    if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('statement pointer')
    path=directory/(digest+'.json')
    if path.is_symlink():raise ValueError('statement symlink')
    with path.open('rb') as f:raw=f.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES or hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('statement integrity')
    return validate(load_json_without_duplicate_keys(raw))
