"""Transactional observed-settlement checkpoints; no economic or order authority."""
from contextlib import closing
from decimal import Context, localcontext
import json
import os
from pathlib import Path
import sqlite3

from services.data.kraken_public import require
from services.data.kraken_account import number
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import kraken_inventory_report as inventory
from tools.kraken_public_capture import atomic_json, digest, validate as validate_capture
from tools.kraken_settlement_review import archive, build, replay_cash


def paths(root):
    root=Path(root).absolute()
    require(root.resolve()==root,'KRAKEN_CHECKPOINT_SYMLINK')
    db=root/'cash-checkpoints.sqlite';marker=root/'cash-checkpoints-required.json'
    for path in (db,marker,Path(str(db)+'-journal'),Path(str(db)+'-wal'),Path(str(db)+'-shm'),
                 root/'cash-baseline.json',root/'latest.json',root/'cash-reconciliation.json',root/'order-recovery.json'):
        require(not path.is_symlink(),'KRAKEN_CHECKPOINT_SYMLINK')
    require(not marker.exists() or db.is_file(),'KRAKEN_CHECKPOINT_DATABASE_MISSING')
    return root,db,marker


def initialise(root, opening, *, market_root):
    root,db,marker=paths(root);inventory.sealed(opening)
    require(opening.get('schema')=='KRAKEN_ACCOUNT_OBSERVATION_V1' and opening.get('quantity_basis')=='base' and
            opening.get('status')=='OBSERVED_STABLE' and not opening['issues'] and not opening['open_orders'],
            'KRAKEN_CHECKPOINT_INITIAL_ACCOUNT')
    market=Path(market_root).absolute()
    require(market.resolve()==market and market.is_dir(),'KRAKEN_CHECKPOINT_MARKET_ROOT')
    metadata={'schema':'KRAKEN_CHECKPOINT_STORE_V1','account_ref_sha256':opening['account_ref_sha256'],
              'initial_account_hash':opening['result_hash'],'market_root':str(market)}
    if not db.exists():
        fd=os.open(db,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600);os.close(fd)
    with closing(sqlite3.connect(db)) as conn,conn:
        conn.execute('PRAGMA synchronous=FULL')
        conn.execute('BEGIN IMMEDIATE')
        tables=conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if not tables:
            require(not marker.exists(),'KRAKEN_CHECKPOINT_DATABASE_INVALID')
            conn.execute('CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL)')
            conn.execute('CREATE TABLE checkpoints (generation INTEGER PRIMARY KEY, document TEXT NOT NULL)')
            conn.execute('INSERT INTO metadata VALUES (1,?)',(json.dumps(metadata,sort_keys=True),))
    with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as conn:
        row=conn.execute('SELECT document FROM metadata WHERE id=1').fetchone()
        require(row is not None and decode(row[0])==metadata,'KRAKEN_CHECKPOINT_CONFIG_CHANGED')
        count=conn.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0]
    binding={'metadata_hash':digest(metadata),'generation':0,'head_hash':None}
    if marker.exists():require(inventory.load(marker)['metadata_hash']==binding['metadata_hash'],'KRAKEN_CHECKPOINT_MARKER')
    else:
        require(count==0,'KRAKEN_CHECKPOINT_MARKER_MISSING')
        atomic_json(marker,binding)
    return load(root,opening['account_ref_sha256'])


def _settled(root, cash, recovery, specs, *, opening, as_of_ms):
    inventory.sealed(recovery)
    require(recovery.get('schema')=='KRAKEN_ORDER_RECOVERY_V1','KRAKEN_CHECKPOINT_RECOVERY_SCHEMA')
    bridge=replay_cash(root,cash)
    require(type(as_of_ms) is int and
            0<=as_of_ms-bridge['as_of_ms']<=120000 and
            0<=as_of_ms-recovery['observed_at_ms']<=120000,'KRAKEN_CHECKPOINT_STALE')
    require(bridge['account_ref_sha256']==opening['account_ref_sha256']==recovery['account_ref_sha256'] and
            bridge['opening_account_hash']==opening['result_hash'] and
            bridge['status']=='BALANCE_MOVEMENTS_MATCHED','KRAKEN_CHECKPOINT_OPENING')
    window=archive(root,'cash-windows',cash['evidence_hash'])
    next_opening=inventory.sealed(window['closing'])
    require(window['opening']==opening and not next_opening['open_orders'],'KRAKEN_CHECKPOINT_UNSETTLED_ACCOUNT')
    if recovery['order_ids']:
        review=build(root,cash,recovery,specs,as_of_ms=as_of_ms)
        require(review['next_review_allowed'] is True,'KRAKEN_CHECKPOINT_SETTLEMENT_UNRESOLVED')
    else:
        require(recovery['status']=='NO_TRACKED_ORDERS' and recovery['query_hash'] is None and
                recovery['trade_query_hash'] is None and not bridge['ledger_entries'],
                'KRAKEN_CHECKPOINT_UNLINKED_MOVEMENTS')
    return next_opening


def _load(root, account_ref):
    root,db,marker=paths(root)
    if not db.exists():return None
    require(marker.is_file(),'KRAKEN_CHECKPOINT_MARKER_MISSING')
    initial=inventory.sealed(inventory.load(root/'cash-baseline.json'))
    with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as conn:
        metadata=decode(conn.execute('SELECT document FROM metadata WHERE id=1').fetchone()[0])
        binding=inventory.load(marker)
        require(metadata['schema']=='KRAKEN_CHECKPOINT_STORE_V1' and
                metadata['account_ref_sha256']==account_ref==initial['account_ref_sha256'] and
                metadata['initial_account_hash']==initial['result_hash'] and
                binding['metadata_hash']==digest(metadata),'KRAKEN_CHECKPOINT_BINDING')
        records=conn.execute('SELECT generation,document FROM checkpoints ORDER BY generation').fetchall()
    opening=initial;retired=set();head=None;generation=0;heads=[None];ledger_entries={};applied=set()
    for index,raw in records:
        record=inventory.sealed(decode(raw))
        require(index==generation+1==record['generation'] and record['previous_hash']==head and
                record['schema']=='KRAKEN_SETTLEMENT_CHECKPOINT_V1' and
                record['account_ref_sha256']==account_ref,'KRAKEN_CHECKPOINT_CHAIN')
        window=archive(root,'cash-windows',record['cash_evidence_hash'])
        cash={**window['report'],'evidence_hash':record['cash_evidence_hash']}
        cash['result_hash']=digest(cash)
        require(cash['result_hash']==record['cash_result_hash'],'KRAKEN_CHECKPOINT_CASH_HASH')
        opening=_settled(root,cash,record['recovery'],record['specs'],opening=opening,as_of_ms=record['as_of_ms'])
        _remember_cash(cash,ledger_entries,applied)
        ids=set(record['recovery']['order_ids'])
        require(not ids&retired,'KRAKEN_CHECKPOINT_ORDER_RETIRED_TWICE')
        retired.update(ids);generation=index;head=record['result_hash']
        heads.append(head)
    # A fully committed SQLite append can precede its file marker by one step
    # after process interruption. Missing committed tails must not roll back.
    require(type(binding.get('generation')) is int and 0<=binding['generation']<=generation and
            generation-binding['generation']<=1 and binding.get('head_hash')==heads[binding['generation']],
            'KRAKEN_CHECKPOINT_HEAD_MISMATCH')
    return {'generation':generation,'head_hash':head,'opening':opening,'retired_ids':sorted(retired),
            'market_root':metadata['market_root'],'submission_enabled':False,'authoritative':False,
            '_ledger_entries':ledger_entries,'_applied_ledger_ids':sorted(applied)}


def _remember_cash(cash, seen, applied):
    with localcontext(Context(prec=160)):
        for row in cash['ledger_entries']:
            key=row['ledger_id']
            require(key not in seen or seen[key]==row,'KRAKEN_CHECKPOINT_LEDGER_MUTATED')
            seen[key]=row
            if cash['movement_start_exclusive_ms']<number(row['time_seconds'])*1000<cash['movement_end_exclusive_ms']:
                require(key not in applied,'KRAKEN_CHECKPOINT_LEDGER_APPLIED_TWICE')
                applied.add(key)


def load(root, account_ref):
    try:return _load(root,account_ref)
    except sqlite3.Error as exc:raise ValueError('KRAKEN_CHECKPOINT_DATABASE_INVALID') from exc


def recover(root):
    """Account-session startup only: let SQLite roll back a hot crash journal."""
    _,db,_=paths(root)
    if not db.exists():return
    try:
        with closing(sqlite3.connect(db.as_uri()+'?mode=rw',uri=True)) as conn:
            conn.execute('SELECT COUNT(*) FROM sqlite_master').fetchone()
    except sqlite3.Error as exc:raise ValueError('KRAKEN_CHECKPOINT_DATABASE_INVALID') from exc


def _pending(cash, error='CHECKPOINT_REFRESH_IN_PROGRESS'):
    value={k:v for k,v in cash.items() if k!='result_hash'}
    value.update(status='UNRESOLVED',failure={'error_type':error})
    value['result_hash']=digest(value)
    return value


def catalog_captures(market_root, *, as_of_ms):
    """Settlement needs fresh product identity, not a fresh trading candle."""
    root=Path(market_root).absolute()
    require(root.resolve()==root,'KRAKEN_CHECKPOINT_MARKET_ROOT')
    status=root/'status.json'
    require(status.resolve()==status.absolute(),'KRAKEN_CHECKPOINT_SYMLINK')
    observer=inventory.sealed(inventory.load(status))
    require(observer['schema'] in ('KRAKEN_PORTFOLIO_OBSERVER_V2','KRAKEN_PORTFOLIO_OBSERVER_V3') and
            type(as_of_ms) is int and type(observer['observed_at_ms']) is int and
            0<=as_of_ms-observer['observed_at_ms']<=120000,'KRAKEN_CHECKPOINT_CATALOG_STALE')
    captures=[]
    for item in observer['instruments']:
        require(item['status'] in ('CAPTURED','STALE'),'KRAKEN_CHECKPOINT_CATALOG_UNAVAILABLE')
        key=item['result_hash'];identity={k:item[k] for k in ('pair','asset_class')}
        require(isinstance(key,str) and len(key)==64 and all(c in '0123456789abcdef' for c in key),
                'KRAKEN_CHECKPOINT_CAPTURE_KEY')
        path=root/'instruments'/digest(identity)/'captures'/(key+'.json')
        require(path.resolve()==path.absolute(),'KRAKEN_CHECKPOINT_SYMLINK')
        raw=validate_capture(inventory.load(path))
        require(raw['status']=='CAPTURED' and raw['result_hash']==key and
                all(raw[k]==v for k,v in identity.items()) and raw['completed_at_ms']<=observer['observed_at_ms'] and
                type(raw['reads'][0]['received_at_ms']) is int and
                0<=as_of_ms-raw['reads'][0]['received_at_ms']<=120000,'KRAKEN_CHECKPOINT_CATALOG_BINDING')
        captures.append(raw)
    return captures


def catalog_specs(market_root, *, as_of_ms):
    return [raw['instrument'] for raw in catalog_captures(market_root,as_of_ms=as_of_ms)]


def advance(root, cash, recovery, specs, *, as_of_ms):
    root,db,marker=paths(root)
    state=load(root,cash['account_ref_sha256'])
    require(state is not None,'KRAKEN_CHECKPOINT_NOT_INITIALISED')
    next_opening=_settled(root,cash,recovery,specs,opening=state['opening'],as_of_ms=as_of_ms)
    _remember_cash(cash,state['_ledger_entries'],set(state['_applied_ledger_ids']))
    require(inventory.load(root/'latest.json')==next_opening and
            inventory.load(root/'cash-reconciliation.json') in (cash,_pending(cash)) and
            inventory.load(root/'order-recovery.json')==recovery,'KRAKEN_CHECKPOINT_SOURCE_CHANGED')
    require(not set(recovery['order_ids'])&set(state['retired_ids']),'KRAKEN_CHECKPOINT_ORDER_RETIRED_TWICE')
    record={'schema':'KRAKEN_SETTLEMENT_CHECKPOINT_V1','account_ref_sha256':cash['account_ref_sha256'],
        'generation':state['generation']+1,'previous_hash':state['head_hash'],'as_of_ms':as_of_ms,
        'cash_evidence_hash':cash['evidence_hash'],'cash_result_hash':cash['result_hash'],
        'recovery':recovery,'specs':specs,'submission_enabled':False,'authoritative':False}
    record['result_hash']=digest(record)
    binding=inventory.load(marker)
    atomic_json(marker,{**binding,'generation':state['generation'],'head_hash':state['head_hash']})
    with closing(sqlite3.connect(db)) as conn,conn:
        conn.execute('PRAGMA synchronous=FULL')
        conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT document FROM checkpoints ORDER BY generation DESC LIMIT 1').fetchone()
        require((decode(row[0])['result_hash'] if row else None)==state['head_hash'],'KRAKEN_CHECKPOINT_CONCURRENT_UPDATE')
        conn.execute('INSERT INTO checkpoints VALUES (?,?)',(record['generation'],json.dumps(record,sort_keys=True)))
    atomic_json(marker,{**binding,'generation':record['generation'],'head_hash':record['result_hash']})
    return record


def refresh(root, cash, recovery, *, market_root, as_of_ms):
    """Called under the account session lock; interruption keeps cash unresolved."""
    root=Path(root)
    require(inventory.load(root/'cash-reconciliation.json')==cash and
            inventory.load(root/'order-recovery.json')==recovery,'KRAKEN_CHECKPOINT_SOURCE_CHANGED')
    atomic_json(root/'cash-reconciliation.json',_pending(cash))
    try:
        captures=catalog_captures(market_root,as_of_ms=as_of_ms) if recovery['order_ids'] else []
        specs=[raw['instrument'] for raw in captures]
        from tools import kraken_funding_reservations as funding
        if any((root/name).exists() or (root/name).is_symlink() for name in
               ('funding-reservations.sqlite','funding-reservations-head.json')):
            claims=funding.status(root,cash['account_ref_sha256'])
            for batch_id in claims['pending_batch_ids']:
                funding.settle(root,batch_id=batch_id,cash=cash,recovery=recovery,
                               market_captures=captures,as_of_ms=as_of_ms)
        record=advance(root,cash,recovery,specs,as_of_ms=as_of_ms)
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as exc:
        atomic_json(root/'cash-reconciliation.json',_pending(cash,type(exc).__name__))
        return None
    atomic_json(root/'cash-reconciliation.json',cash)
    return record
