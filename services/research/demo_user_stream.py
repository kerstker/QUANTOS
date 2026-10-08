"""Append-only private input facts; no order dispatch, cash ledger or authority.

Account updates are partial observations. A connected socket has no exchange
sequence watermark and cannot prove complete history. Raw recognized messages
are retained only after every field passes the whitelist; lease tokens are not.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from quantos.contracts.binance_demo_account import _decimal, _symbol, _text, _bool, _int, _side
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.binance_order_receipts import REQUIRED_ORDER, OPTIONAL_ORDER, expiry_reason

SCHEMA = 'DEMO_USER_STREAM_V1'
MAX_MESSAGE_BYTES = 131072
KINDS = {'SESSION_START','CONNECTED','HEARTBEAT','LEASE_RENEWED','MESSAGE','GAP','STOPPED'}
GAP_REASONS = {'UNCLEAN_RESTART','RESTART_WINDOW','CONNECT_FAILED','STREAM_DISCONNECTED',
               'LEASE_EXPIRED','KEEPALIVE_FAILED','MESSAGE_REJECTED','OUT_OF_ORDER_EVENT',
               'CLOCK_REGRESSION','RECONNECT_BUDGET_EXHAUSTED'}
STOP_REASONS = {'BUDGET_EXHAUSTED','SIGNAL','AUTHENTICATION_REJECTED','RATE_LIMITED','FAILED'}


class StreamValidationError(ValueError):
    """Fixed error code and a schema-owned path; never carries input values."""
    def __init__(self, code, location):
        super().__init__(code)
        self.code, self.location = code, location


def _parse(value, parser, location):
    try:
        return parser(value)
    except (ValueError, KeyError, TypeError, ArithmeticError, RecursionError, UnicodeError):
        raise StreamValidationError('STREAM_VALUE_INVALID', location) from None


DDL = (
    'CREATE TABLE stream_meta(singleton INTEGER PRIMARY KEY CHECK(singleton=1),body TEXT NOT NULL,hash TEXT NOT NULL)',
    'CREATE TABLE stream_records(sequence INTEGER PRIMARY KEY,kind TEXT NOT NULL,at_ms INTEGER NOT NULL,body TEXT NOT NULL,prev_hash TEXT NOT NULL,record_hash TEXT NOT NULL UNIQUE,response_hash TEXT)',
    'CREATE INDEX stream_record_kinds ON stream_records(kind,sequence)',
    'CREATE INDEX stream_response_hashes ON stream_records(response_hash)',
    *(f"CREATE TRIGGER {table}_no_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'append only'); END"
      for table in ('stream_meta','stream_records') for op in ('UPDATE','DELETE')),
)


def _hash(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value):
        raise ValueError('STREAM_HASH_REQUIRED')
    return value


def _time(value):
    if type(value) is not int or not 0 < value <= 2**53-1:raise ValueError('STREAM_TIME_INVALID')
    return value


def _fields(value,required,optional=frozenset(), *, location='event'):
    if not isinstance(value,dict) or not set(required)<=value.keys()<=set(required)|set(optional):
        raise StreamValidationError('STREAM_FIELDS_INVALID', location)


def _enum(value,allowed):
    if not isinstance(value,str) or value not in allowed:raise ValueError('STREAM_ENUM_INVALID')
    return value


def _rows(value,required,optional,identity,parsers, *, location='event'):
    if not isinstance(value,list) or len(value)>2000:
        raise StreamValidationError('STREAM_ROWS_INVALID', location)
    seen=set()
    for row in value:
        _fields(row,required,optional,location=location+'[]')
        # Field names are schema-owned only after _fields has accepted the row.
        for key,v in row.items():_parse(v,parsers[key],location+'[].'+key)
        key=tuple(row[k] for k in identity)
        if key in seen:raise StreamValidationError('STREAM_DUPLICATE_IDENTITY',location)
        seen.add(key)


def _validate_payload(p):
    if not isinstance(p,dict):raise ValueError('STREAM_OBJECT_REQUIRED')
    kind=p.get('e')
    if kind=='ACCOUNT_UPDATE':
        _fields(p,{'e','E','T','a'},location='ACCOUNT_UPDATE');a=p['a']
        _fields(a,{'m','B'},{'P','S'},location='ACCOUNT_UPDATE.a')
        _parse(a['m'],lambda v:_enum(v,{'DEPOSIT','WITHDRAW','ORDER','FUNDING_FEE','WITHDRAW_REJECT','ADJUSTMENT',
              'INSURANCE_CLEAR','ADMIN_DEPOSIT','ADMIN_WITHDRAW','MARGIN_TRANSFER','MARGIN_TYPE_CHANGE',
              'ASSET_TRANSFER','OPTIONS_PREMIUM_FEE','OPTIONS_SETTLE_PROFIT','AUTO_EXCHANGE',
              'COIN_SWAP_DEPOSIT','COIN_SWAP_WITHDRAW'}),'ACCOUNT_UPDATE.a.m')
        if 'S' in a:_parse(a['S'],_symbol,'ACCOUNT_UPDATE.a.S')
        _rows(a['B'],{'a','wb','cw','bc'},set(),('a',),{'a':_text,'wb':_decimal,'cw':_decimal,'bc':_decimal},
              location='ACCOUNT_UPDATE.a.B')
        # Crossed funding updates can omit P entirely. Preserve absence: it is
        # a partial observation, not a statement that the account has no positions.
        if 'P' in a:
            _rows(a['P'],{'s','pa','ep','cr','up','mt','iw','ps'},{'bep'},('s','ps'),
                  {'s':_symbol,'ps':_side,'mt':lambda v:_enum(v,{'isolated','cross'}),
                   **dict.fromkeys(('pa','ep','bep','cr','up','iw'),_decimal)},location='ACCOUNT_UPDATE.a.P')
    elif kind=='ORDER_TRADE_UPDATE':
        _fields(p,{'e','E','T','o'},location='ORDER_TRADE_UPDATE');o=p['o']
        _fields(o,REQUIRED_ORDER,OPTIONAL_ORDER|{'er'},location='ORDER_TRADE_UPDATE.o')
        parsers={'s':_symbol,'c':lambda v:_text(v,r'[.A-Z:/a-z0-9_-]{1,36}'),'S':lambda v:_enum(v,{'BUY','SELL'}),
                 'ps':_side,**dict.fromkeys(('i','t','T','si','ss','gtd'),_int),'er':expiry_reason,
                 **dict.fromkeys(('q','p','l','z','L','ap','sp','n','b','a','AP','cr','rp'),_decimal),
                 **dict.fromkeys(('m','R','cp','pP'),_bool),'N':lambda v:None if v is None else _text(v),
                 **dict.fromkeys(('o','f','x','X','wt','ot','V','pm'),_text)}
        for key,value in o.items():_parse(value,parsers[key],'ORDER_TRADE_UPDATE.o.'+key)
    elif kind=='ACCOUNT_CONFIG_UPDATE':
        _fields(p,{'e','E','T'},{'ac','ai'})
        if not ({'ac','ai'} & p.keys()):raise ValueError('STREAM_CONFIG_MISSING')
        if 'ac' in p:
            _fields(p['ac'],{'s','l'});_symbol(p['ac']['s']);_int(p['ac']['l'])
            if not 1<=p['ac']['l']<=1000:raise ValueError('STREAM_LEVERAGE_INVALID')
        if 'ai' in p:_fields(p['ai'],{'j'});_bool(p['ai']['j'])
    elif kind=='MARGIN_CALL':
        _fields(p,{'e','E','p'},{'cw'})
        if 'cw' in p:_decimal(p['cw'])
        _rows(p['p'],{'s','ps','pa','mt','iw','mp','up','mm'},set(),('s','ps'),
              {'s':_symbol,'ps':_side,'mt':lambda v:_enum(v,{'ISOLATED','CROSSED','isolated','cross'}),
               **dict.fromkeys(('pa','iw','mp','up','mm'),_decimal)})
    elif kind=='TRADE_LITE':
        _fields(p,{'e','E','T','s','q','p','m','c','S','L','l','t','i'})
        _symbol(p['s']);_text(p['c'],r'[.A-Z:/a-z0-9_-]{1,36}');_enum(p['S'],{'BUY','SELL'});_bool(p['m'])
        for k in ('q','p','L','l'):_decimal(p[k])
        for k in ('t','i'):_int(p[k])
    elif kind=='listenKeyExpired':
        _fields(p,{'e','E'})
    else:raise ValueError('STREAM_EVENT_UNSUPPORTED')
    _parse(p['E'],_time,kind+'.E')
    if 'T' in p:_parse(p['T'],_time,kind+'.T')


def normalize_message(raw):
    try:
        if not isinstance(raw,str) or len(raw.encode())>MAX_MESSAGE_BYTES:raise ValueError('STREAM_MESSAGE_SIZE')
        p=load_json_without_duplicate_keys(raw)
        if isinstance(p,dict) and p.get('e')=='listenKeyExpired':
            _fields(p,{'e','E'},{'listenKey'})
            # The original event can contain a credential-equivalent lease.
            p={k:p[k] for k in ('e','E')};kept=None
        else:kept=raw
        _validate_payload(p)
        return {'event_kind':p['e'],'event_ms':p['E'],'transaction_ms':p.get('T'),
                'payload':p,'raw_utf8':kept,'response_sha256':hashlib.sha256(raw.encode()).hexdigest()}
    except (KeyError,TypeError,ArithmeticError,RecursionError,UnicodeError) as exc:
        raise ValueError('STREAM_MESSAGE_INVALID') from exc


def _encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def _digest(value):return hashlib.sha256(_encoded(value).encode()).hexdigest()


def _validate_record(body):
    _fields(body,{'kind','at_ms','data'});_enum(body['kind'],KINDS);_time(body['at_ms']);d=body['data']
    if body['kind']=='MESSAGE':
        _fields(d,{'message'});m=d['message']
        _fields(m,{'event_kind','event_ms','transaction_ms','payload','raw_utf8','response_sha256'})
        if m['raw_utf8'] is None:
            _validate_payload(m['payload']);_hash(m['response_sha256'])
            if (m['event_kind']!='listenKeyExpired' or m['payload']['e']!=m['event_kind']
                    or m['event_ms']!=m['payload']['E'] or m['transaction_ms'] is not None):
                raise ValueError('STREAM_REDACTED_MESSAGE_INVALID')
        elif normalize_message(m['raw_utf8'])!=m:raise ValueError('STREAM_MESSAGE_CHANGED')
    elif body['kind']=='SESSION_START':_fields(d,{'source_sha256'});_hash(d['source_sha256'])
    elif body['kind']=='GAP':
        _fields(d,{'reason'},{'response_sha256'});_enum(d['reason'],GAP_REASONS)
        if 'response_sha256' in d:_hash(d['response_sha256'])
    elif body['kind']=='STOPPED':_fields(d,{'reason'});_enum(d['reason'],STOP_REASONS)
    else:_fields(d,set())


class UserStreamJournal:
    def __init__(self,path,*,account_ref_sha256=None,read_only=False):
        original=Path(path)
        if original.is_symlink():raise ValueError('STREAM_JOURNAL_SYMLINK')
        self.path=original.resolve();self.read_only=read_only
        if not self.path.exists() and (read_only or account_ref_sha256 is None):raise ValueError('STREAM_JOURNAL_MISSING')
        if account_ref_sha256 is not None:_hash(account_ref_sha256)
        new=not self.path.exists()
        if new:self.path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
        self.db=sqlite3.connect(self.path.as_uri()+('?mode=ro' if read_only else '?mode=rwc'),uri=True,timeout=5)
        try:
            if new:
                self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
                with self.db:
                    for sql in DDL:self.db.execute(sql)
                    meta={'schema':SCHEMA,'account_ref_sha256':account_ref_sha256}
                    self.db.execute('INSERT INTO stream_meta VALUES(1,?,?)',(_encoded(meta),_digest(meta)))
            # Validate the entire declared schema before any write or WAL change.
            # A removed/modified trigger is damage, not an implicit migration.
            actual={row[0] for row in self.db.execute('SELECT sql FROM sqlite_master WHERE sql IS NOT NULL')}
            if actual!={sql.rstrip(';') for sql in DDL}:raise ValueError('STREAM_SCHEMA_MISMATCH')
            row=self.db.execute('SELECT body,hash FROM stream_meta WHERE singleton=1').fetchone()
            if row is None:raise ValueError('STREAM_MANIFEST_MISSING')
            self.meta=load_json_without_duplicate_keys(row[0]);self.genesis=row[1]
            _fields(self.meta,{'schema','account_ref_sha256'});_hash(self.meta['account_ref_sha256'])
            if (self.meta['schema']!=SCHEMA or _digest(self.meta)!=self.genesis
                    or account_ref_sha256 is not None and self.meta['account_ref_sha256']!=account_ref_sha256):
                raise ValueError('STREAM_MANIFEST_MISMATCH')
            if not read_only:
                self.verify()
                self.db.execute('PRAGMA synchronous=FULL')
        except BaseException:self.db.close();raise

    def __enter__(self):return self
    def __exit__(self,*_):self.db.close()

    @contextmanager
    def _snapshot(self):
        self.db.execute('BEGIN')
        try:yield
        finally:self.db.rollback()

    def _checked_rows(self,rows,previous,start):
        result=[]
        for seq,kind,at,raw,prev,sha,response in rows:
            body=load_json_without_duplicate_keys(raw);_validate_record(body)
            expected_response=body['data']['message']['response_sha256'] if kind=='MESSAGE' else None
            if (seq!=start or prev!=previous or body['kind']!=kind or body['at_ms']!=at or expected_response!=response
                    or _digest({'sequence':seq,'prev_hash':prev,'body':body})!=sha):
                raise ValueError('STREAM_CHAIN_INVALID')
            result.append((seq,sha,body));previous=sha;start+=1
        return result

    def verify(self):
        with self._snapshot():
            return self._verify_visible()

    def _verify_visible(self):
        previous=self.genesis;count=0
        for row in self.db.execute('SELECT * FROM stream_records ORDER BY sequence'):
            result=self._checked_rows([row],previous,count+1);previous=result[0][1];count+=1
        return {'records':count,'head_sha256':previous,'scope':'LOCAL_HASH_CHAIN_ONLY'}

    def record(self,kind,*,at_ms,**data):
        if self.read_only:raise ValueError('STREAM_READ_ONLY')
        body={'kind':kind,'at_ms':at_ms,'data':data};_validate_record(body)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT sequence,record_hash FROM stream_records ORDER BY sequence DESC LIMIT 1').fetchone()
            seq,previous=(row[0]+1,row[1]) if row else (1,self.genesis)
            if seq>1_000_000:raise ValueError('STREAM_RECORD_BUDGET_EXHAUSTED')
            sha=_digest({'sequence':seq,'prev_hash':previous,'body':body})
            self.db.execute('INSERT INTO stream_records VALUES(?,?,?,?,?,?,?)',(seq,kind,at_ms,_encoded(body),previous,sha,
                data['message']['response_sha256'] if kind=='MESSAGE' else None))
        return sha

    def start_session(self,*,source_sha256,at_ms):
        row=self.db.execute('SELECT kind FROM stream_records ORDER BY sequence DESC LIMIT 1').fetchone()
        if row:self.record('GAP',at_ms=at_ms,reason='RESTART_WINDOW' if row[0]=='STOPPED' else 'UNCLEAN_RESTART')
        self.record('SESSION_START',at_ms=at_ms,source_sha256=source_sha256)

    def snapshot(self,*,as_of_ms=None,verify_full=False):
        if as_of_ms is not None:_time(as_of_ms)
        with self._snapshot():
            if verify_full:self._verify_visible()
            raw=self.db.execute('SELECT * FROM stream_records ORDER BY sequence DESC LIMIT 100').fetchall()[::-1]
            if not raw:raise ValueError('STREAM_SESSION_MISSING')
            anchor=self.genesis if raw[0][0]==1 else self.db.execute('SELECT record_hash FROM stream_records WHERE sequence=?',(raw[0][0]-1,)).fetchone()[0]
            rows=self._checked_rows(raw,anchor,raw[0][0])
            counts=dict(self.db.execute('SELECT kind,count(*) FROM stream_records GROUP BY kind'))
            unique=self.db.execute('SELECT count(DISTINCT response_hash) FROM stream_records WHERE kind=?',('MESSAGE',)).fetchone()[0]
            last_gap=self.db.execute("SELECT body FROM stream_records WHERE kind='GAP' ORDER BY sequence DESC LIMIT 1").fetchone()
            lifecycle=self.db.execute("SELECT kind,at_ms FROM stream_records WHERE kind IN ('SESSION_START','CONNECTED','HEARTBEAT','GAP','STOPPED') ORDER BY sequence DESC LIMIT 1").fetchone()
            source=self.db.execute("SELECT body FROM stream_records WHERE kind='SESSION_START' ORDER BY sequence DESC LIMIT 1").fetchone()
            if source is None or lifecycle is None:raise ValueError('STREAM_SESSION_MISSING')
            events=dict(self.db.execute("SELECT json_extract(body,'$.data.message.event_kind'),count(*) FROM stream_records WHERE kind='MESSAGE' GROUP BY 1"))
            last_at=rows[-1][2]['at_ms']
            # Sample after the read transaction's visible head, not before it.
            if as_of_ms is None:as_of_ms=time.time_ns()//1_000_000
            if last_at>as_of_ms:raise ValueError('STREAM_FUTURE_OBSERVATION')
            state=('STALE' if as_of_ms-last_at>120000 else 'STOPPED' if lifecycle[0]=='STOPPED' else
                   'DISCONNECTED' if lifecycle[0]=='GAP' else 'CONNECTING' if lifecycle[0]=='SESSION_START' else 'CONNECTED')
            recent=[]
            for seq,sha,b in rows:
                m=b['data'].get('message');p=m['payload'] if m else {}
                order=p.get('o',{})
                recent.append({'sequence':seq,'kind':b['kind'],'at_ms':b['at_ms'],'record_hash':sha,
                    'event_kind':m['event_kind'] if m else None,'event_ms':m['event_ms'] if m else None,
                    'transaction_ms':m['transaction_ms'] if m else None,'reason':b['data'].get('reason'),
                    'symbol':order.get('s') or p.get('s') or p.get('a',{}).get('S'),
                    'order_id':str(order['i']) if 'i' in order else None,'order_status':order.get('X'),
                    'last_quantity':order.get('l'),'commission':order.get('n'),'commission_asset':order.get('N'),
                    'account_reason':p.get('a',{}).get('m')})
            return {'schema':SCHEMA,'environment':'BINANCE_USDM_DEMO','account_ref_sha256':self.meta['account_ref_sha256'],
                'source_sha256':json.loads(source[0])['data']['source_sha256'],'observed_at_ms':last_at,'state':state,
                'records':sum(counts.values()),'head_sha256':rows[-1][1],'sessions':counts.get('SESSION_START',0),
                'connections':counts.get('CONNECTED',0),'heartbeats':counts.get('HEARTBEAT',0),
                'messages':counts.get('MESSAGE',0),'unique_messages':unique,'gaps':counts.get('GAP',0),
                'event_counts':events,
                'last_gap':({'at_ms':json.loads(last_gap[0])['at_ms'],**json.loads(last_gap[0])['data']}) if last_gap else None,
                'recent':recent[-50:],'integrity_scope':'FULL_LOCAL_HASH_CHAIN' if verify_full else 'RECENT_100_RECORDS_AND_MANIFEST',
                'coverage_verified':False,'cash_reconciliation':'UNKNOWN','submission_enabled':False,'authoritative':False}

    def export_order_messages(self,*,start_ms,end_ms):
        _time(start_ms);_time(end_ms)
        if not start_ms<end_ms or end_ms-start_ms>150000:raise ValueError('STREAM_EXPORT_WINDOW_INVALID')
        with self._snapshot():
            # Export only rows from the exact read view whose chain was checked.
            integrity=self._verify_visible()
            result=[];gaps=[];size=0
            preceding=self.db.execute("SELECT body FROM stream_records WHERE kind='GAP' AND at_ms<? ORDER BY sequence DESC LIMIT 1",(start_ms,)).fetchone()
            for index,(kind,at,raw) in enumerate(self.db.execute('SELECT kind,at_ms,body FROM stream_records WHERE at_ms BETWEEN ? AND ? ORDER BY sequence',(start_ms,end_ms))):
                if index>=10000:raise ValueError('STREAM_EXPORT_RECORD_LIMIT')
                body=json.loads(raw)
                if kind=='GAP':gaps.append({'at_ms':at,**body['data']})
                if kind=='MESSAGE' and body['data']['message']['event_kind']=='ORDER_TRADE_UPDATE':
                    m=body['data']['message'];size+=len(m['raw_utf8'].encode())
                    if size>2_000_000:raise ValueError('STREAM_EXPORT_BYTE_LIMIT')
                    result.append({'received_at_ms':at,'raw_utf8':m['raw_utf8'],'response_sha256':m['response_sha256']})
            p=json.loads(preceding[0]) if preceding else None
            return {'messages':result,'gaps':gaps,'preceding_gap':({'at_ms':p['at_ms'],**p['data']}) if p else None,
                    'coverage_verified':False,'source_verified':False,'integrity':integrity,
                    'account_ref_sha256':self.meta['account_ref_sha256']}
