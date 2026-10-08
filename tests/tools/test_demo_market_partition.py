"""The protected worker prepares a candidate from retained fixed-path evidence."""
import hashlib
import json
import pytest

from tools import demo_functional_worker as worker
from tests.live.test_demo_market_partition import book as book, NOW
from services.research.demo_execution_origin import save
from contextlib import nullcontext
from copy import deepcopy
from tools import demo_successor_setup as operator


def inputs(book,monkeypatch):
    state, _, values, stores, _=book
    root=stores['dispatch'].path.parent
    paths={symbol:save(root/'execution-origins'/symbol,values[name])
           for symbol,name in [('BTCUSDC','legacy_origin'),('XAUUSDT','gold_origin')]}
    monkeypatch.setattr(worker,'_open',lambda stack,path:stores)
    monkeypatch.setattr(worker,'_check_path',lambda *a,**k:None)
    monkeypatch.setattr(worker,'now_ms',lambda:values['now_ms'])
    payload=dict(market_partition_origins={symbol:path.stem for symbol,path in paths.items()})
    return root,payload,paths


def test_original_worker_rebuilds_full_prefix_and_retains_actual_stop_bytes(book,monkeypatch):
    root,payload,_=inputs(book,monkeypatch);state,_,values,stores,events=book
    before=events.row_count();stop=(root/'kill-switch.sqlite3').read_bytes()
    result=worker.prepare_market_partition(root,values['legacy_origin']['account_ref_sha256'],'c'*40,payload)
    assert result['status']=='PARTITION_CANDIDATE'
    candidate=result['candidate']
    assert candidate['binding']['legacy_state_sha256']==worker.demo_operator_setup.digest(state)
    assert candidate['binding']['kill_switch_sha256']==hashlib.sha256(stop).hexdigest()
    assert (root/'kill-switch.sqlite3').read_bytes()==stop and events.row_count()==before
    assert not candidate['runtime_authorization'] and not candidate['approval_granted']
    assert payload['market_partition_origins'].keys()=={'BTCUSDC','XAUUSDT'}


@pytest.mark.parametrize('damage',['extra','path','missing','digest','wallet','stop_wal','account'])
def test_worker_cannot_substitute_materials_or_caller_selected_equity(book,monkeypatch,damage):
    root,payload,paths=inputs(book,monkeypatch);_,_,values,_,_=book
    account=values['legacy_origin']['account_ref_sha256']
    if damage=='extra':payload['initial_equity']='9999'
    elif damage=='path':payload['market_partition_origins']['BTCUSDC']='/tmp/caller.json'
    elif damage=='missing':paths['XAUUSDT'].unlink()
    elif damage=='digest':paths['BTCUSDC'].write_bytes(b'{}')
    elif damage=='wallet':payload['gold_initial_wallet']='9999'
    elif damage=='stop_wal':(root/'kill-switch.sqlite3-wal').write_bytes(b'not quiescent')
    else:account='e'*64
    with pytest.raises(ValueError):worker.prepare_market_partition(root,account,'c'*40,payload)


def test_operator_prints_the_original_executor_candidate_without_installing(book,tmp_path,monkeypatch,capsys):
    from tools import demo_functional_cycle
    state,context,values,_,_=book;root,payload,paths=inputs(book,monkeypatch)
    result=worker.prepare_market_partition(root,values['legacy_origin']['account_ref_sha256'],'c'*40,payload)
    monkeypatch.setattr(operator,'verify_source',lambda _:None)
    monkeypatch.setattr(operator,'SETUP',tmp_path/'uninstalled')
    monkeypatch.setattr(operator.os,'geteuid',lambda:0)
    monkeypatch.setattr(operator,'cycle_lock',lambda _:nullcontext())
    monkeypatch.setattr(operator.installer,'now_ms',lambda:values['now_ms'])
    spec=operator.setup.propose(origin=values['legacy_origin'],registry=values['registry'],
        position_context=context,now_ms=values['now_ms'],source_commit='c'*40)
    records={'spec.json':spec,'result.json':dict(spec_sha256=operator.setup.digest(spec),source_commit='c'*40)}
    monkeypatch.setattr(operator,'read',lambda path:deepcopy(records[path.name]))
    captures=[]
    def runtime_inputs(source,*,continuation,symbol):
        assert source=='c'*40 and continuation is True;captures.append(symbol)
        return values['registry'],values['legacy_origin' if symbol=='BTCUSDC' else 'gold_origin']
    monkeypatch.setattr(operator.setup,'_runtime_inputs',runtime_inputs)
    monkeypatch.setattr(operator,'context_for',lambda *a,**k:pytest.fail('worker owns full replay'))
    calls=[]
    class Runtime:
        def __init__(self,source,registry,cycle):
            assert source=='c'*40 and registry==values['registry']
        def invoke(self,operation,*,payload,timeout):
            assert operation=='context' and timeout==60
            assert payload['market_partition_origins']=={symbol:path.stem for symbol,path in paths.items()}
            calls.append(operation);return result
        def close(self):calls.append('close')
    monkeypatch.setattr(demo_functional_cycle,'Runtime',Runtime)
    monkeypatch.setattr(operator.setup,'operator_confirmation',lambda *_:pytest.fail('no enrollment'))
    monkeypatch.setattr(operator,'install_successor',lambda *a,**k:pytest.fail('no installation'))
    assert operator.main(['--source-commit','c'*40,'--review-market-transition','XAUUSDT',
                         '--prepare-market-partition'])==0
    printed=json.loads(capsys.readouterr().out)
    assert printed['status']=='UNINSTALLED_MARKET_PARTITION_CANDIDATE'
    assert printed['candidate']==result['candidate'] and not printed['orders_submitted']
    assert captures==['BTCUSDC','BTCUSDC','XAUUSDT'] and calls==['context','close']
    assert not operator.SETUP.exists()


def test_partition_preparation_requires_explicit_transition_review_before_protected_reads(monkeypatch,capsys):
    monkeypatch.setattr(operator,'verify_source',lambda _:pytest.fail('no protected access'))
    assert operator.main(['--source-commit','c'*40,'--prepare-market-partition'])==3
    assert json.loads(capsys.readouterr().out)['phase']=='PREFLIGHT'


@pytest.mark.parametrize('mode',['candidate','wrong_cycle','caller_wallet'])
def test_worker_context_entrypoint_only_prepares_retained_candidate(book,monkeypatch,capsys,mode):
    root,payload,_=inputs(book,monkeypatch);_,_,values,stores,events=book
    area=root/'runtime-view';area.mkdir()
    monkeypatch.setattr(worker,'verify_source',lambda _:None)
    monkeypatch.setattr(worker,'require_custody',lambda:root)
    monkeypatch.setattr(worker,'fixed_account',lambda:({},values['legacy_origin']['account_ref_sha256']))
    monkeypatch.setattr(worker,'private_pipe',lambda _:None)
    monkeypatch.setattr(worker.sys.stdin,'fileno',lambda:0)
    monkeypatch.setattr(worker.sys.stdout,'fileno',lambda:1)
    if mode=='caller_wallet':payload['initial_equity']='9999'
    monkeypatch.setattr(worker,'read_frame',lambda *a,**k:json.dumps(payload).encode())
    monkeypatch.setattr(worker,'area_for',lambda *a:area)
    monkeypatch.setattr(worker,'cycle_symbol',lambda *a:'XAUUSDT' if mode=='wrong_cycle' else 'BTCUSDC')
    before=worker._state(stores['dispatch'],stores['recovery'],stores['oms']);count=events.row_count()
    code=worker.main(['--source-commit','c'*40,'--cycle-sha256','e'*64,'--operation','context'])
    result=json.loads(capsys.readouterr().out)
    assert code==(0 if mode=='candidate' else 3)
    assert result['status']==('PARTITION_CANDIDATE' if mode=='candidate' else 'UNRESOLVED')
    if mode=='candidate':
        files=list((area/'results').glob('*.json'));assert len(files)==1
        assert json.loads(files[0].read_bytes())==result
        assert result['candidate']['approval_granted'] is False
    assert worker._state(stores['dispatch'],stores['recovery'],stores['oms'])==before
    assert events.row_count()==count


def test_original_worker_revalidates_retained_cut_on_new_day(book,monkeypatch):
    from tests.live.test_demo_market_partition_revalidation import fresh_observations
    root,payload,_=inputs(book,monkeypatch);state,context,values,_,events=book
    original=worker.prepare_market_partition(root,values['legacy_origin']['account_ref_sha256'],'c'*40,payload)['candidate']
    at=values['now_ms']+86400000;fresh=fresh_observations(values,at)
    payload['market_partition_origins']={symbol:save(root/'execution-origins'/symbol,fresh[name]).stem
        for symbol,name in (('BTCUSDC','legacy_origin'),('XAUUSDT','gold_origin'))}
    payload['retained_market_partition']=original
    monkeypatch.setattr(worker,'now_ms',lambda:at)
    before=deepcopy(state),events.row_count(),(root/'kill-switch.sqlite3').read_bytes()
    result=worker.prepare_market_partition(root,values['legacy_origin']['account_ref_sha256'],'c'*40,payload)
    assert result['status']=='PARTITION_REVALIDATED'
    assert result['revalidation']['legacy_context_sha256']==worker.demo_operator_setup.digest(context)
    assert result['revalidation']['original_cut_at_ms']==original['binding']['recorded_at_ms']
    assert before==(state,events.row_count(),(root/'kill-switch.sqlite3').read_bytes())


def test_operator_and_private_worker_revalidation_use_actual_local_pipes_and_original_books(book,tmp_path,monkeypatch):
    import subprocess
    import sys
    from pathlib import Path
    from tools import demo_functional_cycle
    from tests.live.test_demo_market_partition_revalidation import fresh_observations
    root,payload,_=inputs(book,monkeypatch);state,context,values,stores,events=book
    original=worker.prepare_market_partition(root,values['legacy_origin']['account_ref_sha256'],'c'*40,payload)['candidate']
    at=values['now_ms']+86400000;fresh=fresh_observations(values,at)
    for symbol,name in (('BTCUSDC','legacy_origin'),('XAUUSDT','gold_origin')):
        save(root/'execution-origins'/symbol,fresh[name])
    config=dict(root=str(root),registry=values['registry'],account=values['legacy_origin']['account_ref_sha256'],at=at,
        source_root=str(Path(worker.__file__).resolve().parents[1]),paths=dict(dispatch=str(stores['dispatch'].path),
        recovery=stores['recovery'].db_path,oms=stores['oms']._conn.execute('PRAGMA database_list').fetchone()[2]))
    cfg=tmp_path/'local-worker-config.json';cfg.write_text(json.dumps(config))
    script=tmp_path/'local-worker.py'
    script.write_text('''import sys,json
from pathlib import Path
cfg=json.loads(Path(sys.argv[1]).read_text());sys.path.insert(0,cfg['source_root'])
from tools import demo_functional_worker as w
from live.execution import demo_position_context as p
from live.execution.demo_dispatch_store import DemoDispatchStore
from live.execution.order_recovery import SqliteOrderRecoveryStore
from live.execution.position_ledger import LiveLedger
root=Path(cfg['root'])
# Identity, accepted source and custody are virtual; pipes, original stores,
# native receipts, hash binding and V2 replay are real isolated implementations.
w.verify_source=lambda value:None
w.private_pipe=lambda fd:None
w.require_custody=lambda:root
w.fixed_account=lambda:({},cfg['account'])
w._check_path=lambda *a,**k:None
w.now_ms=lambda:cfg['at']
w.demo_authority.load_registry_for_executor=lambda:cfg['registry']
def area(ignored,cycle):
 path=root/'local-worker-view'/cycle
 path.mkdir(parents=True,mode=0o700,exist_ok=True)
 return path
w.area_for=area
p._ROOT=root
p._custody=lambda *a,**k:None
def books(stack,ignored):
 values={}
 for name,cls in [('dispatch',DemoDispatchStore),('recovery',SqliteOrderRecoveryStore),('oms',LiveLedger)]:
  obj=cls(cfg['paths'][name]);stack.callback(obj.close);values[name]=obj
 return values
w._open=books
original=w.prepare_market_partition
def diagnose(*args,**kwargs):
 try:return original(*args,**kwargs)
 except Exception:
  import traceback
  traceback.print_exc(file=sys.stderr)
  raise
w.prepare_market_partition=diagnose
raise SystemExit(w.main(sys.argv[2:]))
''')
    processes=[]
    class LocalRuntime(demo_functional_cycle.Runtime):
        def start(self,operation,payload):
            process=subprocess.Popen([sys.executable,str(script),str(cfg),'--source-commit',self.source_commit,
                '--cycle-sha256',self.cycle,'--operation',operation],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,bufsize=0,cwd=config['source_root'])
            processes.append(process)
            demo_functional_cycle.supervisor._send(process.stdin,operator.setup.canonical(payload),
                demo_functional_cycle.time.monotonic()+10)
            process.stdin.close()
            return process
        def close(self):
            for process in processes:
                if process.poll() is None:process.kill()
                process.wait(timeout=5)
                (tmp_path/'local-worker.stderr').write_bytes(process.stderr.read())
    monkeypatch.setattr(demo_functional_cycle,'Runtime',LocalRuntime)
    monkeypatch.setattr(operator.setup,'_runtime_inputs',lambda source,**kw:
        (values['registry'],fresh['legacy_origin' if kw['symbol']=='BTCUSDC' else 'gold_origin']))
    monkeypatch.setattr(operator.installer,'now_ms',lambda:at)
    before=deepcopy(state),events.row_count()
    result=operator.market_partition_revalidation('c'*40,values['registry'],'e'*64,original)
    assert result['candidate_sha256']==operator.setup.digest(original)
    assert result['legacy_context_sha256']==operator.setup.digest(context)
    assert result['observed_day_id']==at//86400000 and result['submission_enabled'] is False
    assert before==(state,events.row_count())
    assert processes and all(process.returncode==0 for process in processes)
