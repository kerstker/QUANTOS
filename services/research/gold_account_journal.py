"""Local mainnet observation journal. Never an execution or accounting ledger.

Partial account updates are preserved as partial facts. Gaps cannot be cleared
by reconnecting, and matching REST trades cannot prove a complete cash history.
"""
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from services.research.gold_account_snapshot import _text, _bool, _side, _decimal, _rows
from services.research.gold_trade_observation import _time, _require, load_document, assess

MAX_EVENT_BYTES=131072
SCHEMA='GOLD_ACCOUNT_JOURNAL_V1'
DDL=(
    'CREATE TABLE meta (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)',
    'CREATE TABLE records (seq INTEGER PRIMARY KEY, at_ms INTEGER NOT NULL, body TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL)',
    "CREATE TRIGGER records_no_update BEFORE UPDATE ON records BEGIN SELECT RAISE(ABORT,'append only'); END",
    "CREATE TRIGGER records_no_delete BEFORE DELETE ON records BEGIN SELECT RAISE(ABORT,'append only'); END",
    "CREATE TRIGGER meta_no_update BEFORE UPDATE ON meta BEGIN SELECT RAISE(ABORT,'immutable'); END",
    "CREATE TRIGGER meta_no_delete BEFORE DELETE ON meta BEGIN SELECT RAISE(ABORT,'immutable'); END",
)


def _encode(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def _hash(value):
    return hashlib.sha256(_encode(value).encode()).hexdigest()


def _sha(value):
    _require(isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value),'INVALID_DIGEST')
    return value


def normalize_event(raw):
    _require(isinstance(raw,str) and len(raw.encode())<=MAX_EVENT_BYTES,'INVALID_EVENT_SIZE')
    p=load_document(raw.encode())
    _require(isinstance(p,dict),'INVALID_EVENT')
    try:
        kind=p['e'];event={'e':kind,'E':_time(p['E'])}
        if kind=='listenKeyExpired':
            pass  # The lease itself must never be written.
        elif kind=='ACCOUNT_UPDATE':
            event['T']=_time(p['T']);a=p['a']
            update={'m':_text(a['m']), 'B':_rows(a['B'],{'a':_text,
                **dict.fromkeys(('wb','cw','bc'),_decimal)},('a',))}
            if 'S' in a:update['S']=_text(a['S'])
            if 'P' in a:
                update['P']=_rows(a['P'],{'s':_text,'ps':_side,'mt':lambda v:v if v in ('cross','isolated') else _fail(),
                    **dict.fromkeys(('pa','ep','cr','up','iw'),_decimal)},('s','ps'))
            event['a']=update
        elif kind=='ORDER_TRADE_UPDATE':
            event['T']=_time(p['T']);o=p['o']
            parsers={'s':_text,'i':_time,'t':lambda v:-1 if type(v) is int and v==-1 else _time(v),'T':_time,'S':_text,'ps':_side,
                'x':_text,'X':_text,'o':_text,'m':_bool,'R':_bool,
                **dict.fromkeys(('q','p','l','z','L'),_decimal)}
            order={k:parse(o[k]) for k,parse in parsers.items()}
            for name in ('n','rp','sp','ap','AP','cr'):
                if name in o:order[name]=_decimal(o[name])
            for name in ('cp','pP'):
                if name in o:order[name]=_bool(o[name])
            for name in ('f','wt','ot','V','pm'):
                if name in o:order[name]=_text(o[name])
            if 'N' in o:order['N']=None if o['N'] is None else _text(o['N'])
            _require(order['i']>0 and order['S'] in ('BUY','SELL') and
                order['X'] in ('NEW','PARTIALLY_FILLED','FILLED','CANCELED','EXPIRED','EXPIRED_IN_MATCH') and
                order['x'] in ('NEW','CANCELED','CALCULATED','EXPIRED','TRADE','AMENDMENT') and
                0<=Decimal(order['l'])<=Decimal(order['z'])<=Decimal(order['q']) and
                Decimal(order['p'])>=0 and Decimal(order['L'])>=0,'INVALID_ORDER_EVENT')
            if order['x']=='TRADE':
                _require(order['t']>0 and Decimal(order['l'])>0 and Decimal(order['L'])>0,'INVALID_TRADE_EVENT')
            event['o']=order
        elif kind=='TRADE_LITE':
            for name,parse in {'T':_time,'s':_text,'S':_text,'m':_bool,'t':_time,'i':_time,
                               **dict.fromkeys(('q','p','l','L'),_decimal)}.items():event[name]=parse(p[name])
            _require(event['S'] in ('BUY','SELL') and event['i']>0 and event['t']>0 and
                     Decimal(event['l'])>0 and Decimal(event['L'])>0,'INVALID_TRADE_LITE')
        elif kind=='MARGIN_CALL':
            if 'cw' in p:event['cw']=_decimal(p['cw'])
            event['p']=_rows(p['p'],{'s':_text,'ps':_side,
                'mt':lambda v:v if v in ('cross','isolated','CROSSED','ISOLATED') else _fail(),
                **dict.fromkeys(('pa','iw','mp','up','mm'),_decimal)},('s','ps'))
        elif kind=='CONDITIONAL_ORDER_TRIGGER_REJECT':
            event['T']=_time(p['T']);o=p['or']
            event['or']={'s':_text(o['s']),'i':_time(o['i'])}
            # Free-form exchange reasons are deliberately not persisted.
        elif kind=='ALGO_UPDATE':
            event['T']=_time(p['T']);o=p['o']
            a={k:parse(o[k]) for k,parse in {'aid':_time,'s':_text,'S':_text,'ps':_side,'X':_text}.items()}
            _require(a['aid']>0 and a['S'] in ('BUY','SELL') and a['X'] in
                ('NEW','CANCELED','TRIGGERING','TRIGGERED','FINISHED','REJECTED','EXPIRED'),'INVALID_ALGO_EVENT')
            for k in ('q','ap','aq','tp','p'):
                if k in o and o[k]!='':a[k]=_decimal(o[k])
            for k in ('at','o','f','act','V','wt','pm'):
                if k in o and o[k]!='':a[k]=_text(o[k])
            for k in ('cp','pP','R','ia'):
                if k in o:a[k]=_bool(o[k])
            for k in ('tt','gtd'):
                if k in o:a[k]=_time(o[k])
            if 'ai' in o:
                _require(isinstance(o['ai'],str) and (o['ai']=='' or re.fullmatch('[0-9]{1,20}',o['ai'])),'INVALID_ACTUAL_ORDER_ID')
                a['ai']=o['ai']
            event['o']=a
        elif kind=='ACCOUNT_CONFIG_UPDATE':
            event['T']=_time(p['T'])
            if 'ac' in p:
                event['ac']={'s':_text(p['ac']['s']),'l':_time(p['ac']['l'])}
                _require(1<=event['ac']['l']<=1000,'INVALID_LEVERAGE')
            if 'ai' in p:event['ai']={'j':_bool(p['ai']['j'])}
            _require('ac' in event or 'ai' in event,'EMPTY_CONFIG_EVENT')
        else:
            raise ValueError('UNSUPPORTED_EVENT')
        return {'payload':event,'response_sha256':hashlib.sha256(raw.encode()).hexdigest()}
    except (KeyError,TypeError,ArithmeticError):
        raise ValueError('INVALID_EVENT') from None


def _fail():
    raise ValueError('INVALID_ENUM')


def _validate_record(body):
    _require(isinstance(body,dict) and set(body)=={'kind','data'},'INVALID_RECORD')
    kind,data=body['kind'],body['data']
    _require(isinstance(data,dict),'INVALID_RECORD')
    if kind=='START':
        _require(set(data)=={'source_sha256'},'INVALID_START');_sha(data['source_sha256'])
    elif kind in ('CONNECTED','HEARTBEAT','STOPPED'):
        _require(not data,'INVALID_LIFECYCLE')
    elif kind=='GAP':
        _require(set(data) in ({'reason'},{'reason','response_sha256'}) and data['reason'] in
            ('RESTART','UNCLEAN_RESTART','DISCONNECTED','INVALID_EVENT','LEASE_EXPIRED',
             'BASELINE_TO_STREAM','INTERRUPTED','SOURCE_CHANGED','ROTATION','HISTORY_FAILED'), 'INVALID_GAP')
        if 'response_sha256' in data:_sha(data['response_sha256'])
    elif kind=='EVENT':
        _require(set(data)=={'payload','response_sha256'},'INVALID_EVENT_RECORD')
        _sha(data['response_sha256'])
        _require(normalize_event(_encode(data['payload']))['payload']==data['payload'],'NONCANONICAL_EVENT')
    else:raise ValueError('INVALID_RECORD_KIND')


class AccountJournal:
    def __init__(self,path,*,account_ref,read_only=False):
        _sha(account_ref)
        path=Path(path).absolute()
        _require(path.resolve()==path,'LINKED_JOURNAL')
        new=not path.exists()
        _require(not (read_only and new),'JOURNAL_MISSING')
        if new:
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        info=path.stat()
        _require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode)==0o600
                 and info.st_uid==os.getuid(),'PRIVATE_JOURNAL_REQUIRED')
        self.lock_fd=None
        if not read_only:
            self.lock_fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW)
            try:fcntl.flock(self.lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(self.lock_fd);self.lock_fd=None
                raise ValueError('JOURNAL_IN_USE') from None
        self.read_only=read_only
        self.account_ref=account_ref
        self.db=None
        try:
            self.db=sqlite3.connect(path.as_uri()+('?mode=ro' if read_only else '?mode=rw'),uri=True,timeout=5)
            if new:
                with self.db:
                    for sql in DDL:self.db.execute(sql)
                    self.db.execute('INSERT INTO meta VALUES(1,?)',(_encode({'schema':SCHEMA,
                        'environment':'BINANCE_USDM_MAINNET','account_ref':account_ref}),))
            actual={r[0] for r in self.db.execute('SELECT sql FROM sqlite_master WHERE sql IS NOT NULL')}
            _require(actual==set(DDL),'JOURNAL_SCHEMA_MISMATCH')
            meta=self.db.execute('SELECT body FROM meta WHERE id=1').fetchone()
            expected={'schema':SCHEMA,'environment':'BINANCE_USDM_MAINNET','account_ref':account_ref}
            _require(meta is not None and meta[0]==_encode(expected),'JOURNAL_IDENTITY_MISMATCH')
            self.genesis=_hash(expected)
            if not read_only:self.db.execute('PRAGMA synchronous=FULL')
            self.rows()
        except BaseException:self.close();raise

    def __enter__(self):return self
    def __exit__(self,*args):self.close()

    def close(self):
        if self.db is not None:self.db.close();self.db=None
        if self.lock_fd is not None:os.close(self.lock_fd);self.lock_fd=None

    def rows(self):
        result=[];previous=self.genesis;last_time=0
        # A single SELECT holds a consistent SQLite read snapshot.
        for seq,at,raw,parent,digest in self.db.execute('SELECT * FROM records ORDER BY seq'):
            body=load_document(raw.encode());_validate_record(body);_time(at)
            _require(seq==len(result)+1 and parent==previous and at>=last_time and
                digest==_hash({'seq':seq,'at_ms':at,'body':body,'previous':parent}), 'JOURNAL_CHAIN_INVALID')
            result.append({'seq':seq,'at_ms':at,**body});previous=digest;last_time=at
        return result

    def _append(self,kind,at_ms,data):
        _time(at_ms);body={'kind':kind,'data':data};_validate_record(body)
        row=self.db.execute('SELECT seq,at_ms,digest FROM records ORDER BY seq DESC LIMIT 1').fetchone()
        seq,parent=(row[0]+1,row[2]) if row else (1,self.genesis)
        _require(seq<=100000 and (row is None or at_ms>=row[1]),'JOURNAL_BUDGET_OR_CLOCK')
        digest=_hash({'seq':seq,'at_ms':at_ms,'body':body,'previous':parent})
        self.db.execute('INSERT INTO records VALUES(?,?,?,?,?)',(seq,at_ms,_encode(body),parent,digest))

    def _write(self,records):
        _require(not self.read_only,'JOURNAL_READ_ONLY')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            for kind,at,data in records:self._append(kind,at,data)

    def start(self,*,at_ms,source_sha256):
        _require(not self.read_only,'JOURNAL_READ_ONLY')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            last=self.db.execute('SELECT body FROM records ORDER BY seq DESC LIMIT 1').fetchone()
            if last:
                reason='RESTART' if load_document(last[0].encode())['kind']=='STOPPED' else 'UNCLEAN_RESTART'
                self._append('GAP',at_ms,{'reason':reason})
            self._append('START',at_ms,{'source_sha256':source_sha256})

    def gap(self,reason,*,at_ms):
        self._write([('GAP',at_ms,{'reason':reason})])

    def lifecycle(self,kind,*,at_ms):
        _require(kind in ('CONNECTED','HEARTBEAT','STOPPED','DISCONNECTED'),'INVALID_LIFECYCLE')
        self._write([('GAP',at_ms,{'reason':'DISCONNECTED'}) if kind=='DISCONNECTED' else (kind,at_ms,{})])

    def message(self,raw,*,at_ms):
        try:event=normalize_event(raw)
        except ValueError:
            data={'reason':'INVALID_EVENT'}
            if isinstance(raw,str):data['response_sha256']=hashlib.sha256(raw.encode()).hexdigest()
            self._write([('GAP',at_ms,data)])
            return False
        rows=[('EVENT',at_ms,event)]
        if event['payload']['e']=='listenKeyExpired':rows.append(('GAP',at_ms,{'reason':'LEASE_EXPIRED'}))
        self._write(rows)
        return True

    def summary(self,*,as_of_ms):
        _time(as_of_ms);rows=self.rows()
        _require(not rows or rows[-1]['at_ms']<=as_of_ms,'FUTURE_JOURNAL')
        facts=_facts(rows)
        last=next((r['kind'] for r in reversed(rows) if r['kind'] in
                   ('START','CONNECTED','HEARTBEAT','GAP','STOPPED')),None)
        gaps=sum(r['kind']=='GAP' for r in rows)
        status={'START':'CONNECTING','CONNECTED':'CONNECTED','HEARTBEAT':'CONNECTED',
                'GAP':'DISCONNECTED','STOPPED':'STOPPED',None:'EMPTY'}[last]
        if facts['conflicts'] or facts['out_of_order']:status='DATA_CONFLICT'
        if rows and as_of_ms-rows[-1]['at_ms']>120000:status='STALE'
        return {'schema':'GOLD_ACCOUNT_JOURNAL_SUMMARY_V1','status':status,'records':len(rows),
            'event_count':facts['count'],'duplicate_events':facts['duplicates'],
            'unique_trades':len(facts['trades']),'trade_conflicts':facts['conflicts'],
            'out_of_order_events':facts['out_of_order'],'gap_count':gaps,
            'reconciliation':'REQUIRED' if gaps or facts['conflicts'] or facts['out_of_order'] else 'UNKNOWN',
            'history_complete':False,'source_authenticated':False,'submission_enabled':False,'net_pnl':None}


def _facts(rows):
    seen=set();trades={};last={};duplicates=0;count=0;conflicts=0;out_of_order=0
    for row in rows:
        if row['kind']=='CONNECTED':last={}
        if row['kind']!='EVENT':continue
        count+=1;p=row['data']['payload'];digest=_hash(p)
        if digest in seen:duplicates+=1;continue
        seen.add(digest)
        prior=last.get(p['e'])
        current=(p['E'],p.get('T',p['E']))
        if prior and (current[0]<prior[0] or current[1]<prior[1]):out_of_order+=1
        last[p['e']]=tuple(max(a,b) for a,b in zip(prior,current)) if prior else current
        if p['e']!='ORDER_TRADE_UPDATE' or p['o']['x']!='TRADE':continue
        o=p['o'];key=(o['s'],str(o['t']))
        trade={'trade_id':str(o['t']),'order_id':str(o['i']),'time_ms':o['T'],
            'position_side':o['ps'],'side':o['S'],'price':o['L'],'quantity':o['l'],
            'commission':o.get('n'),'commission_asset':o.get('N'),'reported_realized_pnl':o.get('rp')}
        if key in trades and trades[key]!=trade:conflicts+=1
        else:trades[key]=trade
    return {'count':count,'duplicates':duplicates,'trades':trades,'conflicts':conflicts,'out_of_order':out_of_order}


def reconcile_trades(journal,document):
    report=assess(document)
    _require(report['account_ref']==journal.account_ref,'ACCOUNT_SCOPE_MISMATCH')
    facts=_facts(journal.rows())
    native={k[1]:v for k,v in facts['trades'].items() if k[0]=='XAUUSDT' and
            document['start_ms']<=v['time_ms']<document['end_ms']}
    rest={r['trade_id']:r for r in document['fills']}
    differences=0;matched=0
    amounts={'price','quantity','commission','reported_realized_pnl'}
    for key in native.keys() & rest.keys():
        a,b=native[key],rest[key]
        equal=all((a[k] is not None and Decimal(a[k])==Decimal(b[k])) if k in amounts
                  else a[k]==b[k] for k in a)
        if equal:matched+=1
        else:differences+=1
    missing=len(native.keys() ^ rest.keys())
    status=('MISMATCH' if differences or facts['conflicts'] else 'MISSING_TRADES' if missing
            else 'MATCHING_OBSERVED_TRADES' if matched else 'NO_OBSERVED_TRADES')
    return {'schema':'GOLD_STREAM_REST_TRADE_CHECK_V1','status':status,'matched':matched,
        'different':differences,'stream_only':len(native.keys()-rest.keys()),
        'rest_only':len(rest.keys()-native.keys()),'fills_facts_sha256':report['facts_sha256'],
        'cash_reconciled':False,'history_complete':False,'net_pnl':None}
