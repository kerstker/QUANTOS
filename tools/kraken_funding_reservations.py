"""Private local budget reservations and replayed observed-settlement releases.

All accepted debit claims remain held until the settlement bridge replays their
complete disposition. Timestamps, process exit and retries never release
claims. A local reservation is not proof of venue costs or trading permission.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
from decimal import Context, Decimal, localcontext
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from services.data.kraken_account import canonical, number
from services.data.kraken_public import require
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.kraken_funding_review import build
from tools.kraken_inventory_report import load, sealed
from tools.kraken_public_capture import atomic_json, digest


@contextmanager
def _locked(root, *, create=False):
    root = Path(root).absolute()
    require(root.resolve() == root, 'KRAKEN_RESERVATION_SYMLINK')
    if create: root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and
            stat.S_IMODE(info.st_mode) & 0o077 == 0, 'KRAKEN_RESERVATION_PRIVATE_ROOT')
    lock = root/'funding-reservations.lock'
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        db = root/'funding-reservations.sqlite'
        marker = root/'funding-reservations-head.json'
        for path in (lock, db, marker, Path(str(db)+'-journal'), Path(str(db)+'-wal'), Path(str(db)+'-shm')):
            require(not path.is_symlink(), 'KRAKEN_RESERVATION_SYMLINK')
            if path.exists():
                info = path.stat()
                require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and
                        stat.S_IMODE(info.st_mode) & 0o077 == 0, 'KRAKEN_RESERVATION_PRIVATE_FILE')
        require(not marker.exists() or db.is_file(), 'KRAKEN_RESERVATION_DATABASE_MISSING')
        yield db, marker


def _write_marker(path, metadata, records):
    atomic_json(path, {'metadata_hash': digest(metadata), 'generation': len(records),
                       'head_hash': records[-1]['result_hash'] if records else None})


def _append(conn, record):
    conn.execute('INSERT INTO reservations VALUES (?,?)',
                 (record['generation'], json.dumps(record, sort_keys=True, allow_nan=False)))


def _budget(account, requirements, drafts, held, as_of_ms):
    # Validate the original claim before adding trusted local ledger totals.
    build(account, requirements, drafts, as_of_ms=as_of_ms)
    effective = deepcopy(requirements)
    with localcontext(Context(prec=100)):
        effective['pending_debits'] = {
            asset: canonical(number(requirements['pending_debits'].get(asset, '0')) + held.get(asset, Decimal(0)))
            for asset in sorted(set(requirements['pending_debits']) | set(held))}
    return build(account, effective, drafts, as_of_ms=as_of_ms)


def _record(metadata, records, batch_id, account, requirements, drafts, budget, as_of_ms):
    value = {'schema': 'KRAKEN_LOCAL_RESERVATION_V1', 'generation': len(records)+1,
             'previous_hash': records[-1]['result_hash'] if records else None,
             'metadata_hash': digest(metadata), 'batch_id': batch_id, 'as_of_ms': as_of_ms,
             'account': account, 'requirements': requirements, 'drafts': drafts,
             'budget_review': budget, 'status': 'LOCAL_BUDGET_RESERVED',
             'reservation_created': True, 'submission_enabled': False, 'authoritative': False,
             'scope': 'LOCAL_CALLER_DEBIT_CLAIMS_ONLY'}
    value['result_hash'] = digest(value)
    return value


SETTLED='KRAKEN_LOCAL_RESERVATION_SETTLEMENT_V1'


def _floor(records):
    for record in reversed(records):
        if record['schema']==SETTLED:
            account=record['account']
            return {'result_hash':account['result_hash'],'observed_at_ms':account['observed_at_ms']}
    return None


def require_settled_account(account, floor):
    if floor is None:return
    reads=account.get('reads')
    first=reads[0].get('requested_at_ms') if isinstance(reads,list) and reads and isinstance(reads[0],dict) else None
    observed=account.get('observed_at_ms')
    require(account.get('result_hash')==floor['result_hash'] or
            (type(observed) is int and type(first) is int and observed>=floor['observed_at_ms'] and first>=floor['observed_at_ms']),
            'KRAKEN_RESERVATION_PRE_SETTLEMENT_ACCOUNT')


def _settled_record(root, metadata, records, reservation, proof, as_of_ms):
    from tools.kraken_reserved_settlement import prove
    result=prove(root,reservation,proof,as_of_ms=as_of_ms)
    require(type(as_of_ms) is int and as_of_ms>=records[-1]['as_of_ms'] and
            result['account']['observed_at_ms']>=records[-1]['account']['observed_at_ms'],
            'KRAKEN_RESERVATION_TIME_REGRESSION')
    value={'schema':SETTLED,'generation':len(records)+1,'previous_hash':records[-1]['result_hash'],
           'metadata_hash':digest(metadata),'batch_id':reservation['batch_id'],
           'reservation_hash':reservation['result_hash'],'as_of_ms':as_of_ms,'proof':proof,**result,
           'status':'LOCAL_RESERVATION_SETTLED','submission_enabled':False,'authoritative':False,
           'scope':'OBSERVED_LOCAL_CLAIM_SETTLEMENT_ONLY'}
    value['result_hash']=digest(value)
    return value


def _read(conn, marker, account_ref):
    require(marker.is_file(), 'KRAKEN_RESERVATION_MARKER_MISSING')
    metadata = decode(conn.execute('SELECT document FROM metadata WHERE id=1').fetchone()[0])
    require(set(metadata) == {'schema', 'account_ref_sha256', 'cash_reserves'} and
            metadata['schema'] == 'KRAKEN_LOCAL_RESERVATION_STORE_V1' and
            metadata['account_ref_sha256'] == account_ref, 'KRAKEN_RESERVATION_ACCOUNT_BINDING')
    binding = load(marker)
    require(binding.get('metadata_hash') == digest(metadata), 'KRAKEN_RESERVATION_METADATA')
    records = []; held = {}; order_ids = set(); batch_ids = set(); hashes = [None]; released=set()
    for generation, document in conn.execute('SELECT generation,document FROM reservations ORDER BY generation'):
        record = sealed(decode(document))
        if record['schema']==SETTLED:
            batch=record['batch_id']
            require(batch in batch_ids and batch not in released,'KRAKEN_RESERVATION_RELEASE_REUSED')
            original=next(r for r in records if r['batch_id']==batch and r['schema']!=SETTLED)
            require(generation==len(records)+1 and record==_settled_record(marker.parent,metadata,records,
                    original,record['proof'],record['as_of_ms']),'KRAKEN_RESERVATION_SETTLEMENT_REPLAY')
            with localcontext(Context(prec=100)):
                for asset,row in original['budget_review']['assets'].items():
                    debit=Decimal(row['new_debits'])
                    if debit:
                        require(held.get(asset,Decimal(0))>=debit,'KRAKEN_RESERVATION_RELEASE_BALANCE')
                        held[asset]-=debit
                        if held[asset]==0:del held[asset]
            released.add(batch);records.append(record);hashes.append(record['result_hash']);continue
        account = record['account']; requirements = record['requirements']; drafts = record['drafts']
        require_settled_account(account,_floor(records))
        batch_id = record['batch_id']
        require(isinstance(batch_id, str) and re.fullmatch('[a-f0-9]{32}', batch_id) and
                batch_id not in batch_ids, 'KRAKEN_RESERVATION_BATCH_REUSED')
        require(account['account_ref_sha256'] == account_ref and
                requirements['cash_reserves'] == metadata['cash_reserves'], 'KRAKEN_RESERVATION_POLICY')
        ids = {o['client_order_id'] for o in requirements['orders']}
        require(not ids & order_ids, 'KRAKEN_RESERVATION_ORDER_REUSED')
        if records:
            previous = records[-1]
            require(record['as_of_ms'] >= previous['as_of_ms'] and
                    account['observed_at_ms'] >= previous['account']['observed_at_ms'],
                    'KRAKEN_RESERVATION_TIME_REGRESSION')
        budget = _budget(account, requirements, drafts, held, record['as_of_ms'])
        require(budget['status'] == 'CONDITIONAL_BUDGET_FIT' and generation == len(records)+1 and
                record == _record(metadata, records, batch_id, account, requirements, drafts, budget, record['as_of_ms']),
                'KRAKEN_RESERVATION_REPLAY')
        with localcontext(Context(prec=100)):
            for asset, row in budget['assets'].items():
                debit = Decimal(row['new_debits'])
                if debit: held[asset] = held.get(asset, Decimal(0)) + debit
        records.append(record); hashes.append(record['result_hash'])
        order_ids.update(ids); batch_ids.add(batch_id)
    generation = binding.get('generation')
    require(type(generation) is int and 0 <= generation <= len(records) and
            len(records)-generation <= 1 and binding.get('head_hash') == hashes[generation],
            'KRAKEN_RESERVATION_HEAD_MISMATCH')
    return metadata, records, held, order_ids, generation != len(records)


def reserve(root, account, requirements, drafts, *, batch_id, as_of_ms):
    account, requirements, drafts = deepcopy((account, requirements, drafts))
    require(isinstance(batch_id, str) and re.fullmatch('[a-f0-9]{32}', batch_id), 'KRAKEN_RESERVATION_BATCH_ID')
    # Content/shape validation happens before creating any store. A blocked but
    # well-formed budget may initialise an empty store, never an accepted claim.
    build(account, requirements, drafts, as_of_ms=as_of_ms)
    metadata = {'schema': 'KRAKEN_LOCAL_RESERVATION_STORE_V1',
                'account_ref_sha256': account['account_ref_sha256'], 'cash_reserves': requirements['cash_reserves']}
    try:
        with _locked(root, create=True) as (db, marker):
            fresh = not db.exists()
            if fresh:
                fd = os.open(db, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600); os.close(fd)
            with closing(sqlite3.connect(db, timeout=30)) as conn:
                conn.execute('PRAGMA synchronous=FULL')
                if fresh:
                    with conn:
                        conn.execute('CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL)')
                        conn.execute('CREATE TABLE reservations (generation INTEGER PRIMARY KEY, document TEXT NOT NULL)')
                        conn.execute('INSERT INTO metadata VALUES (1,?)', (json.dumps(metadata, sort_keys=True),))
                    _write_marker(marker, metadata, [])
                with conn:
                    conn.execute('BEGIN IMMEDIATE')
                    actual, records, held, ids, lagging = _read(conn, marker, account['account_ref_sha256'])
                    require(actual == metadata, 'KRAKEN_RESERVATION_POLICY_CHANGED')
                    if lagging: _write_marker(marker, metadata, records)
                    for record in records:
                        if record['batch_id'] == batch_id:
                            require(record['account'] == account and record['requirements'] == requirements and
                                    record['drafts'] == drafts, 'KRAKEN_RESERVATION_RETRY_CHANGED')
                            return next((r for r in records if r['schema']==SETTLED and r['batch_id']==batch_id),record)
                    require(not ids & {o['client_order_id'] for o in requirements['orders']}, 'KRAKEN_RESERVATION_ORDER_REUSED')
                    require_settled_account(account,_floor(records))
                    if records:
                        require(as_of_ms >= records[-1]['as_of_ms'] and
                                account['observed_at_ms'] >= records[-1]['account']['observed_at_ms'],
                                'KRAKEN_RESERVATION_TIME_REGRESSION')
                    budget = _budget(account, requirements, drafts, held, as_of_ms)
                    if budget['status'] != 'CONDITIONAL_BUDGET_FIT': return budget
                    record = _record(metadata, records, batch_id, account, requirements, drafts, budget, as_of_ms)
                    _append(conn, record)
                # Database commit precedes publication. A one-record lag is
                # replayed and repaired under the same lock after interruption.
                _write_marker(marker, metadata, [*records, record])
                return record
    except (sqlite3.Error, KeyError, TypeError, IndexError) as exc:
        raise ValueError('KRAKEN_RESERVATION_STORE_INVALID') from exc


def status(root, account_ref):
    try:
        with _locked(root) as (db, marker):
            require(db.is_file(), 'KRAKEN_RESERVATION_DATABASE_MISSING')
            with closing(sqlite3.connect(db.as_uri()+'?mode=rw', uri=True)) as conn:
                metadata, records, held, _, lagging = _read(conn, marker, account_ref)
                if lagging: _write_marker(marker, metadata, records)
            return {'schema': 'KRAKEN_LOCAL_RESERVATION_STATUS_V1', 'account_ref_sha256': account_ref,
                    'generation': len(records), 'head_hash': records[-1]['result_hash'] if records else None,
                    'reserved_debits': {asset: canonical(value) for asset, value in sorted(held.items())},
                    'settled_account_floor':_floor(records),
                    'pending_batch_ids':sorted({r['batch_id'] for r in records if r['schema']!=SETTLED}-
                                               {r['batch_id'] for r in records if r['schema']==SETTLED}),
                    'submission_enabled': False, 'authoritative': False}
    except (sqlite3.Error, KeyError, TypeError, IndexError) as exc:
        raise ValueError('KRAKEN_RESERVATION_STORE_INVALID') from exc


def review_orders(root, *, batch_id, market_captures, as_of_ms):
    """Read-only identity review against the verified local batch and raw archives.

    Matching is necessary but insufficient for releasing any reservation. Source
    snapshots have local integrity, not an independently signed issuer identity.
    """
    from services.data.kraken_order_identity import match
    from services.data.kraken_order_query import assess
    from tools.kraken_public_capture import validate
    from tools.kraken_settlement_review import archive
    require(type(as_of_ms) is int and as_of_ms>0,'KRAKEN_RESERVED_IDENTITY_CLOCK')
    require(isinstance(batch_id,str) and re.fullmatch('[a-f0-9]{32}',batch_id),'KRAKEN_RESERVATION_BATCH_ID')
    require(isinstance(market_captures,list) and 1<=len(market_captures)<=32,'KRAKEN_RESERVED_IDENTITY_MARKETS')
    with _locked(root) as (db,marker):
        require(db.is_file(),'KRAKEN_RESERVATION_DATABASE_MISSING')
        recovery_path=Path(root)/'order-recovery.json'
        require(not recovery_path.is_symlink(),'KRAKEN_RESERVATION_SYMLINK')
        recovery=sealed(load(recovery_path));ref=recovery['account_ref_sha256']
        require(recovery.get('schema')=='KRAKEN_ORDER_RECOVERY_V1' and recovery['status']=='ORDERS_OBSERVED' and
                type(recovery.get('observed_at_ms')) is int and 0<=as_of_ms-recovery['observed_at_ms']<=120000,
                'KRAKEN_RESERVED_IDENTITY_RECOVERY')
        try:
            with closing(sqlite3.connect(db.as_uri()+'?mode=rw',uri=True)) as conn:
                _,records,_,_,_=_read(conn,marker,ref)
        except (sqlite3.Error,KeyError,TypeError,IndexError) as exc:
            raise ValueError('KRAKEN_RESERVATION_STORE_INVALID') from exc
        records=[r for r in records if r['batch_id']==batch_id and r['schema']!=SETTLED]
        require(len(records)==1,'KRAKEN_RESERVED_IDENTITY_BATCH_MISSING')
        record=records[0]
        evidence=archive(Path(root).absolute(),'order-queries',recovery['query_hash'])
        observed=assess(evidence['read'],as_of_ms=evidence['read']['received_at_ms'])
        require(observed==evidence['report'] and observed['account_ref_sha256']==ref and
                observed['observed_at_ms']<=recovery['observed_at_ms'] and
                sorted(o['order_id'] for o in observed['orders'])==recovery['order_ids'],
                'KRAKEN_RESERVED_IDENTITY_QUERY_REPLAY')
        markets=[validate(raw) for raw in market_captures]
        require(all(raw['status']=='CAPTURED' and 0<=as_of_ms-raw['completed_at_ms']<=120000 for raw in markets),
                'KRAKEN_RESERVED_IDENTITY_MARKET_TIME')
        result=match(evidence['read'],record['drafts'],[raw['instrument'] for raw in markets],
                     account_ref=ref,not_before_ms=record['as_of_ms'],as_of_ms=as_of_ms)
        result.update(reservation_hash=record['result_hash'],batch_id=batch_id,query_hash=recovery['query_hash'],
                      recovery_hash=recovery['result_hash'],market_hashes=[raw['result_hash'] for raw in markets])
        result['result_hash']=digest(result)
        return result


def settle(root, *, batch_id, cash, recovery, market_captures, as_of_ms):
    """Release only local claims after replaying their entire observed settlement."""
    require(isinstance(batch_id,str) and re.fullmatch('[a-f0-9]{32}',batch_id),'KRAKEN_RESERVATION_BATCH_ID')
    proof=deepcopy({'cash':cash,'recovery':recovery,'market_captures':market_captures})
    sealed(cash);sealed(recovery)
    try:
        with _locked(root) as (db,marker):
            require(db.is_file(),'KRAKEN_RESERVATION_DATABASE_MISSING')
            with closing(sqlite3.connect(db.as_uri()+'?mode=rw',uri=True)) as conn:
                conn.execute('PRAGMA synchronous=FULL')
                with conn:
                    conn.execute('BEGIN IMMEDIATE')
                    metadata,records,_,_,lagging=_read(conn,marker,cash['account_ref_sha256'])
                    if lagging:_write_marker(marker,metadata,records)
                    prior=[r for r in records if r['batch_id']==batch_id]
                    require(bool(prior),'KRAKEN_RESERVED_SETTLEMENT_BATCH_MISSING')
                    if prior[-1]['schema']==SETTLED:
                        require(prior[-1]['proof']==proof,'KRAKEN_RESERVED_SETTLEMENT_RETRY_CHANGED')
                        return prior[-1]
                    record=_settled_record(marker.parent,metadata,records,prior[0],proof,as_of_ms)
                    _append(conn,record)
                _write_marker(marker,metadata,[*records,record])
                return record
    except (sqlite3.Error,KeyError,TypeError,IndexError) as exc:
        raise ValueError('KRAKEN_RESERVATION_STORE_INVALID') from exc
