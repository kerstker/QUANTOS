"""Operator-controlled monitoring with bounded reconnects and local checkpoints.

Credentials remain in the calling human process. This is not a resident service.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
import uuid

from services.research.gold_account_journal import AccountJournal, reconcile_trades, normalize_event, MAX_EVENT_BYTES
from services.research.gold_account_history import reconcile_inventory
from services.research.gold_monitor_view import activity_view
from services.research.gold_trade_observation import load_document, _require
from tools.gold_account_capture import collect
from tools.gold_account_observe import open_stream, sources
from tools.gold_account_history import collect_history, signed_reader, HistoryRequestFailure, HistoryContractFailure
from tools.gold_trade_observation import _write_private, _encode

ROOT=Path(__file__).resolve().parents[1]
EXTRA_SOURCES=('tools/gold_account_monitor.py','tools/gold_account_history.py','services/research/gold_account_history.py','services/research/gold_monitor_view.py')


def hashes():
    return sources() | {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in EXTRA_SOURCES}


def rejected_event_diagnostic(raw):
    """Only fixed enums and a digest; never persist rejected account payloads."""
    result={'schema':'GOLD_REJECTED_EVENT_V1','event_type':'UNKNOWN',
            'reason':'INVALID_EVENT','response_sha256':None,'message_shape':'UNPARSED','event_name_sha256':None}
    if not isinstance(raw,str) or len(raw)>MAX_EVENT_BYTES:
        result['reason']='INVALID_EVENT_SIZE'
        return result
    try:
        encoded=raw.encode()
        if len(encoded)>MAX_EVENT_BYTES:
            result['reason']='INVALID_EVENT_SIZE'
            return result
        result['response_sha256']=hashlib.sha256(encoded).hexdigest()
        document=load_document(encoded)
        if not isinstance(document, dict):
            result['message_shape'] = 'NON_OBJECT'
        elif 'e' in document:
            result['message_shape'] = 'EVENT_OBJECT'
        elif 'data' in document:
            result['message_shape'] = 'WRAPPED_DATA'
        elif 'result' in document:
            result['message_shape'] = 'RESULT_OBJECT'
        elif 'code' in document:
            result['message_shape'] = 'ERROR_OBJECT'
        else:
            result['message_shape'] = 'OTHER_OBJECT'
        kind=document.get('e') if isinstance(document,dict) else None
        if isinstance(kind, str):
            result['event_name_sha256'] = hashlib.sha256(kind.encode()).hexdigest()
        if isinstance(kind,str) and kind in (
            'listenKeyExpired','ACCOUNT_UPDATE','ORDER_TRADE_UPDATE','TRADE_LITE',
            'MARGIN_CALL','CONDITIONAL_ORDER_TRIGGER_REJECT','ALGO_UPDATE','ACCOUNT_CONFIG_UPDATE'):
            result['event_type']=kind
        normalize_event(raw)
    except Exception as exc:
        if isinstance(exc,ValueError) and str(exc) in (
            'UNSUPPORTED_EVENT','INVALID_EVENT_SIZE','INVALID_EVENT','INVALID_ORDER_EVENT',
            'INVALID_TRADE_EVENT','INVALID_TRADE_LITE','INVALID_ALGO_EVENT',
            'INVALID_ACTUAL_ORDER_ID','INVALID_LEVERAGE','EMPTY_CONFIG_EVENT','INVALID_ENUM'):
            result['reason']=str(exc)
    return result


def atomic_json(path,value):
    path=Path(path)
    _require(path.parent.resolve()==path.parent.absolute() and not path.is_symlink(),'LINKED_OUTPUT')
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    try:
        _write_private(temporary,_encode(value));os.replace(temporary,path)
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if temporary.exists():temporary.unlink()


def recover(path,*,api_key,api_secret,initial,journal,clock_ms,capture=collect,read=None):
    path.mkdir(mode=0o700)
    end_dir=path/'snapshot';end_dir.mkdir(mode=0o700)
    final_report=capture(end_dir,api_key=api_key,api_secret=api_secret)
    _require(final_report['status'] in ('STABLE_OBSERVATIONS','CHANGED_DURING_CAPTURE'),'FINAL_SNAPSHOT_FAILED')
    final=load_document((end_dir/'capture.json').read_bytes())
    def account(c):return next(r for r in c['reads'] if r['kind']=='account_after')
    b,e=account(initial),account(final)
    # Pad both boundaries for preflight's maximum allowed local/server offset.
    start=max(0,b['started_at_ms']-2000);end=e['received_at_ms']+2001
    # Wait for the upper bound and a small publication delay; repeated reads are
    # evidence of stability only, never a claim of exchange completeness.
    if end>clock_ms():time.sleep((end-clock_ms())/1000+1)
    pages=[]
    def save_page(proof):
        pages.append(proof);_write_private(path/f'page-{len(pages):04d}.json',_encode(proof))
    history=collect_history(read or signed_reader(api_key,api_secret,clock_ms=clock_ms),
        account_ref=journal.account_ref,start_ms=start,end_ms=end,exported_ms=max(end,clock_ms()),on_page=save_page,
        known_orders=next(r['data'] for r in initial['reads'] if r['kind']=='orders_after'),
        known_algos=next(r['data'] for r in initial['reads'] if r['kind']=='algos_after'))
    _write_private(path/'history.json',_encode(history))
    latest=history['passes'][-1];fills=latest['fills'];income=latest['income']
    stream_check=reconcile_trades(journal,fills)
    inventory=reconcile_inventory(b['data'],e['data'],fills,income)
    times=[r['time_ms'] for r in fills['fills']]+[r['time'] for r in income]
    boundary=any(t<=b['received_at_ms']+2000 or t>=e['started_at_ms']-2000 for t in times)
    status=('BOUNDARY_ACTIVITY' if boundary else 'CHANGED_DURING_READ' if history['status']!='STABLE_WINDOW_READS'
            else 'MISMATCH' if 'MISMATCH' in (inventory['wallet_status'],inventory['position_status'],inventory['cash_fill_status'],stream_check['status'])
            else 'OTHER_MARKETS_UNCHECKED' if inventory['position_status']=='OTHER_MARKETS_UNCHECKED'
            else 'INCOMPLETE' if any(inventory[k]!='MATCHING_OBSERVED_TOTALS' for k in ('wallet_status','position_status','cash_fill_status'))
            else 'SNAPSHOT_CHANGED' if final_report['status']!='STABLE_OBSERVATIONS'
            else 'WINDOW_CHECKED')
    # Persist complete facts independently of whether the comparison matched.
    result={'schema':'GOLD_MONITOR_RECOVERY_V1','status':status,'start_ms':start,'end_ms':end,
        'stream':stream_check,'inventory':inventory,'history_complete':False,
        'order_lifecycle_complete':False,'recovered_fills':len(fills['fills']),
        'income_records':len(income),'ordinary_order_records':len(latest['orders']),
        'conditional_order_records':len(latest['algos']),'snapshot_status':final_report['status']}
    _write_private(path/'reconciliation.json',_encode(result))
    return result


def monitor(destination,*,api_key,api_secret,seconds=300,resume=False,capture=None,
            connect=None,history=None,clock_ms=None,monotonic=None,pause=None,progress=None):
    root=Path(destination).absolute()
    _require(type(seconds) is int and 1<=seconds<=86400,'INVALID_DURATION')
    _require(root.resolve()==root and root.is_dir(),'PRIVATE_DIRECTORY_REQUIRED')
    clock_ms=clock_ms or (lambda:time.time_ns()//1_000_000);monotonic=monotonic or time.monotonic
    pause=pause or time.sleep;progress=progress or (lambda v:None)
    capture=capture or collect;connect=connect or open_stream;history=history or recover
    source=hashes()
    if resume:
        _require((root/'identity.json').resolve()==root/'identity.json','LINKED_IDENTITY')
        _require((root/'identity.json').stat().st_mode & 0o777==0o600,'PRIVATE_IDENTITY_REQUIRED')
        identity=load_document((root/'identity.json').read_bytes())
        expected=hmac.new(api_key.encode(),identity['salt'].encode(),hashlib.sha256).hexdigest()
        _require(identity['schema']=='GOLD_MONITOR_IDENTITY_V1' and hmac.compare_digest(expected,identity['key_binding']),'ACCOUNT_BINDING_MISMATCH')
        _require((root/'journal.db').is_file(),'JOURNAL_MISSING')
    else:
        _require(not any(root.iterdir()),'EMPTY_PRIVATE_DIRECTORY_REQUIRED');os.chmod(root,0o700)
        salt=uuid.uuid4().hex
        identity={'schema':'GOLD_MONITOR_IDENTITY_V1','salt':salt,
            'account_ref':hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            'key_binding':hmac.new(api_key.encode(),salt.encode(),hashlib.sha256).hexdigest(),
            'binding_kind':'SAME_API_KEY_NOT_INDEPENDENT_ACCOUNT_AUTHORITY'}
        _write_private(root/'identity.json',_encode(identity))
    # Acquire writer lock before reserving a run or calling any private API.
    with AccountJournal(root/'journal.db',account_ref=identity['account_ref']) as journal:
        runs=sorted(root.glob('run-[0-9][0-9][0-9][0-9]'))
        _require(len(runs)<9999,'RUN_BUDGET')
        run=root/f'run-{len(runs)+1:04d}';run.mkdir(mode=0o700)
        _write_private(run/'sources.json',_encode(source))
        journal.start(at_ms=clock_ms(),source_sha256=hashlib.sha256(_encode(source)).hexdigest())
        base=run/'baseline';base.mkdir(mode=0o700)
        progress({'status':'PREFLIGHT','submission_enabled':False})
        preflight=capture(base,api_key=api_key,api_secret=api_secret)
        if preflight['status']!='STABLE_OBSERVATIONS':
            journal.lifecycle('STOPPED',at_ms=clock_ms())
            result=journal.summary(as_of_ms=clock_ms())|{'reason':'PREFLIGHT_INCOMPLETE',
                'preflight_reason':preflight.get('reason') if preflight['status']!='CHANGED_DURING_CAPTURE' else 'CHANGED_DURING_CAPTURE',
                'preflight_request_error':preflight.get('request_error')}
            atomic_json(root/'account-monitor.json',{'schema':'GOLD_MONITOR_VIEW_V1','as_of_ms':clock_ms(),'summary':result,'recovery':None})
            _write_private(run/'report.json',_encode(result));seal_run(run,journal,result,at_ms=clock_ms());return result
        initial_path=root/'run-0001/baseline/capture.json'
        # Preserve failed starts but take the first successful baseline as the
        # initial observation. Never interpret an incomplete capture as empty.
        if not (root/'recovery-anchor.json').exists():
            for candidate in sorted(root.glob('run-[0-9][0-9][0-9][0-9]/baseline/capture.json')):
                _require(candidate.resolve()==candidate,'LINKED_BASELINE')
                value=load_document(candidate.read_bytes())
                if any(r['kind']=='account_after' for r in value['reads']):
                    initial_path=candidate;break
        if (root/'recovery-anchor.json').exists():
            anchor_path=root/'recovery-anchor.json'
            _require(anchor_path.resolve()==anchor_path,'LINKED_ANCHOR')
            anchor=load_document(anchor_path.read_bytes());relative=Path(anchor['snapshot'])
            _require(not relative.is_absolute() and '..' not in relative.parts,'INVALID_ANCHOR_PATH')
            initial_path=root/relative
            _require(initial_path.resolve()==initial_path and
                hashlib.sha256(initial_path.read_bytes()).hexdigest()==anchor['sha256'],'ANCHOR_HASH_MISMATCH')
        _require(initial_path.resolve()==initial_path,'LINKED_BASELINE')
        initial=load_document(initial_path.read_bytes())
        _require(any(r['kind']=='account_after' for r in initial['reads']),'INITIAL_SNAPSHOT_MISSING')
        journal.gap('BASELINE_TO_STREAM',at_ms=clock_ms())
        view_baseline=load_document((base/'capture.json').read_bytes())
        deadline=monotonic()+seconds;reconnects=0;failures=0;checkpoint=0;reason='DURATION_COMPLETE';last_recovery=None;latest_fills=None
        last_publish=-1e30
        def publish(force=False):
            nonlocal last_publish
            if force or monotonic()-last_publish>=20:
                result=journal.summary(as_of_ms=clock_ms())
                atomic_json(root/'account-monitor.json',{'schema':'GOLD_MONITOR_VIEW_V1','as_of_ms':clock_ms(),
                    'summary':result,'recovery':last_recovery,'activity':activity_view(view_baseline,journal.rows(),rest_fills=latest_fills)})
                progress({'status':result['status'],'events':result['event_count'],'gaps':result['gap_count'],
                          'reconnects':reconnects,'submission_enabled':False})
                last_publish=monotonic()
        def checkpoint_history():
            nonlocal checkpoint,last_recovery,view_baseline,initial,latest_fills
            checkpoint+=1
            progress({'status':'HISTORY_CHECK','checkpoint':checkpoint,'submission_enabled':False})
            try:
                last_recovery=history(run/f'checkpoint-{checkpoint:04d}',api_key=api_key,api_secret=api_secret,
                    initial=initial,journal=journal,clock_ms=clock_ms,capture=capture)
                history_file=run/f'checkpoint-{checkpoint:04d}/history.json'
                if history_file.exists():latest_fills=load_document(history_file.read_bytes())['passes'][-1]['fills']
                saved=run/f'checkpoint-{checkpoint:04d}/snapshot/capture.json'
                if saved.exists():
                    view_baseline=load_document(saved.read_bytes())
                    if last_recovery['status']=='WINDOW_CHECKED':
                        initial=view_baseline
                        atomic_json(root/'recovery-anchor.json',{'snapshot':str(saved.relative_to(root)),
                            'sha256':hashlib.sha256(saved.read_bytes()).hexdigest()})
            except Exception as exc:
                journal.gap('HISTORY_FAILED',at_ms=clock_ms())
                safe={'HISTORY_REQUEST_FAILED','HISTORY_REQUEST_REJECTED','HISTORY_CONTRACT_FAILED','HISTORY_TIME_BUDGET','PAGE_BUDGET',
                      'SATURATED_MILLISECOND','REPEATED_PAGE','FINAL_SNAPSHOT_FAILED','INVALID_HISTORY_SCOPE'}
                last_recovery={'status':'INCOMPLETE','history_complete':False,
                    'reason':str(exc) if type(exc) in (ValueError,HistoryRequestFailure,HistoryContractFailure) and str(exc) in safe else 'HISTORY_FAILED'}
                if type(exc) is HistoryRequestFailure:last_recovery['request_error']=exc.diagnostic
                if type(exc) is HistoryContractFailure:last_recovery['contract_error']=exc.diagnostic
                _write_private(run/f'history-error-{checkpoint:04d}.json',_encode(last_recovery))
            publish(True)
        try:
            while monotonic()<deadline:
                _require(hashes()==source,'SOURCE_CHANGED')
                try:
                    with connect(api_key) as connection:
                        connected=monotonic();journal.lifecycle('CONNECTED',at_ms=clock_ms());publish(True)
                        while monotonic()<min(deadline,connected+(60 if checkpoint==0 else 300)):
                            try:raw=connection.recv(timeout=min(20,max(.001,min(deadline,connected+(60 if checkpoint==0 else 300))-monotonic())))
                            except TimeoutError:
                                if monotonic()>=min(deadline,connected+(60 if checkpoint==0 else 300)):break
                                if not connection.ping().wait(timeout=min(5,max(.001,deadline-monotonic()))):raise ValueError('PONG_TIMEOUT')
                                journal.lifecycle('HEARTBEAT',at_ms=clock_ms());failures=0;publish();continue
                            if not journal.message(raw,at_ms=clock_ms()):
                                reason='EVENT_UNSUPPORTED_OR_INVALID'
                                atomic_json(run/'event-error.json',rejected_event_diagnostic(raw))
                                break
                            failures=0;publish()
                            if load_document(raw.encode())['e']=='listenKeyExpired':raise ValueError('LEASE_EXPIRED')
                        if reason!='DURATION_COMPLETE':break
                    if monotonic()<deadline:
                        journal.gap('ROTATION',at_ms=clock_ms());checkpoint_history()
                        # Revalidate permissions/configuration before each next lease.
                        check=run/f'preflight-{checkpoint:04d}';check.mkdir(mode=0o700)
                        check_report=capture(check,api_key=api_key,api_secret=api_secret)
                        if check_report['status']!='STABLE_OBSERVATIONS':
                            reason='PREFLIGHT_INCOMPLETE';break
                except Exception:
                    journal.lifecycle('DISCONNECTED',at_ms=clock_ms());failures+=1;publish(True)
                    if failures>=5:reason='RECONNECT_EXHAUSTED';break
                    if monotonic()>=deadline:reason='STREAM_FAILED';break
                    reconnects+=1;pause(min(2**failures,30,max(0,deadline-monotonic())))
            if reason=='DURATION_COMPLETE':
                checkpoint_history()
                if last_recovery and last_recovery['status']=='INCOMPLETE':reason='HISTORY_FAILED'
        except KeyboardInterrupt:
            reason='OPERATOR_STOPPED';journal.gap('INTERRUPTED',at_ms=clock_ms())
        except Exception:
            reason='MONITOR_FAILED';journal.gap('SOURCE_CHANGED' if hashes()!=source else 'DISCONNECTED',at_ms=clock_ms())
        journal.lifecycle('STOPPED',at_ms=clock_ms());publish(True)
        result=journal.summary(as_of_ms=clock_ms())|{'reason':reason,'reconnects':reconnects,
            'history_status':last_recovery['status'] if last_recovery else 'NOT_CHECKED',
            'history_reason':last_recovery.get('reason') if last_recovery else None,
            'history_request_error':last_recovery.get('request_error') if last_recovery else None}
        if reason=='PREFLIGHT_INCOMPLETE':
            result['preflight_reason']=check_report.get('reason')
            result['preflight_request_error']=check_report.get('request_error')
        _write_private(run/'report.json',_encode(result))
        seal_run(run,journal,result,at_ms=clock_ms())
        return result


def seal_run(run,journal,result,*,at_ms):
    files={}
    for path in sorted(run.rglob('*.json')):
        _require(path.resolve()==path,'LINKED_ARCHIVE_MEMBER')
        if path.name=='monitor-manifest.json':continue
        files[str(path.relative_to(run))]=hashlib.sha256(path.read_bytes()).hexdigest()
    row=journal.db.execute('SELECT seq,digest FROM records ORDER BY seq DESC LIMIT 1').fetchone()
    _write_private(run/'monitor-manifest.json',_encode({'schema':'GOLD_MONITOR_ARCHIVE_V1','files':files,
        'journal_seq':row[0],'journal_digest':row[1],'as_of_ms':at_ms,
        'summary':journal.summary(as_of_ms=at_ms),'source_authenticated':False}))


def verify_monitor(directory):
    root=Path(directory).absolute();_require(root.resolve()==root,'LINKED_MONITOR')
    def read(path):
        _require(path.resolve()==path and path.stat().st_size<=8_000_000,'INVALID_ARCHIVE_MEMBER')
        return path.read_bytes()
    identity=load_document(read(root/'identity.json'))
    runs=sorted(root.glob('run-[0-9][0-9][0-9][0-9]'));_require(bool(runs),'NO_RUNS')
    with AccountJournal(root/'journal.db',account_ref=identity['account_ref'],read_only=True) as journal:
        rows=journal.rows();verified=0
        for run in runs:
            manifest=load_document(read(run/'monitor-manifest.json'))
            _require(manifest['schema']=='GOLD_MONITOR_ARCHIVE_V1' and manifest['source_authenticated'] is False,'INVALID_MANIFEST')
            actual={str(p.relative_to(run)) for p in run.rglob('*.json') if p.name!='monitor-manifest.json'}
            _require(actual==set(manifest['files']),'ARCHIVE_MEMBERS_CHANGED')
            for name,digest in manifest['files'].items():
                relative=Path(name)
                _require(not relative.is_absolute() and '..' not in relative.parts,'INVALID_ARCHIVE_PATH')
                _require(hashlib.sha256(read(run/relative)).hexdigest()==digest,'ARCHIVE_HASH_MISMATCH')
            source=load_document(read(run/'sources.json'))
            _require(source==hashes(),'SOURCE_VERSION_MISMATCH')
            seq=manifest['journal_seq'];_require(type(seq) is int and 0<seq<=len(rows),'INVALID_JOURNAL_PREFIX')
            stored=journal.db.execute('SELECT digest FROM records WHERE seq=?',(seq,)).fetchone()
            _require(stored[0]==manifest['journal_digest'],'JOURNAL_PREFIX_MISMATCH')
            # Current run must bind the actual journal summary, not a cached report.
            if seq==len(rows):_require(journal.summary(as_of_ms=manifest['as_of_ms'])==manifest['summary'],'SUMMARY_MISMATCH')
            report=load_document(read(run/'report.json'))
            _require(all(report[k]==v for k,v in manifest['summary'].items()),'REPORT_MISMATCH')
            verified+=1
    return {'status':'LOCAL_ARCHIVE_VERIFIED','runs':verified,'records':len(rows),
            'source_authenticated':False,'history_complete':False,'submission_enabled':False}
