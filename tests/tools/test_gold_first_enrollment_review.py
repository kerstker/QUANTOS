"""Original operator/worker exchange can review Gold before any new key exists."""
from contextlib import nullcontext
from copy import deepcopy
import json

import pytest

from services.research.demo_execution_origin import save
from tests.live.test_demo_market_partition import book as book, NOW
from tests.live.test_demo_market_partition_runtime import installed as installed
from tests.live.test_gold_first_enrollment_review import first as first
from tools import demo_functional_worker as worker, demo_successor_setup as operator


@pytest.fixture
def bound(first, monkeypatch):
    root = first['stores']['dispatch'].path.parent
    path = save(root/'execution-origins'/'XAUUSDT', first['origin'])
    monkeypatch.setattr(worker, '_open', lambda stack, path: first['stores'])
    monkeypatch.setattr(worker, '_check_path', lambda *a, **k: None)
    monkeypatch.setattr(worker, 'now_ms', lambda: NOW + 140000)
    payload = {'first_gold_enrollment_origin_sha256': path.stem}
    return first, root, payload, path


def test_protected_worker_reviews_without_new_key_or_target_ledger(bound):
    value, root, payload, _ = bound
    before = deepcopy(value['state']), value['events'].row_count(), (root/'kill-switch.sqlite3').read_bytes()
    result = worker.review_first_gold(root, value['origin']['account_ref_sha256'], 'c'*40, payload)
    assert result['status'] == 'FIRST_GOLD_ENROLLMENT_REVIEW'
    assert result['review']['gold_position_context'] is None
    assert result['review']['legacy_risk']['canonical_wallet'] == '4999.6801'
    assert before == (value['state'], value['events'].row_count(), (root/'kill-switch.sqlite3').read_bytes())


@pytest.mark.parametrize('damage', ['path', 'extra', 'account', 'source', 'stop'])
def test_worker_review_rejects_substituted_input_and_native_stop(bound, damage):
    value, root, payload, path = bound
    account = value['origin']['account_ref_sha256'];source = 'c'*40
    if damage == 'path': payload['first_gold_enrollment_origin_sha256'] = str(path)
    elif damage == 'extra': payload['initial_equity'] = '99999'
    elif damage == 'account': account = 'f'*64
    elif damage == 'source': source = 'd'*40
    else: (root/'kill-switch.sqlite3').write_bytes(b'isolated invalid stop')
    before = deepcopy(value['state']), value['events'].row_count()
    with pytest.raises(ValueError): worker.review_first_gold(root, account, source, payload)
    assert before == (value['state'], value['events'].row_count())


@pytest.fixture
def exchange(bound, monkeypatch):
    from tools import demo_functional_cycle
    value, root, payload, _ = bound
    monkeypatch.setattr(operator.setup, '_runtime_inputs', lambda *a, **k:
        (deepcopy(value['registry']), deepcopy(value['origin'])))
    monkeypatch.setattr(operator.installer, 'now_ms', lambda: NOW + 140000)
    calls = []
    class LocalRuntime:
        def __init__(self, source, registry, cycle): calls.append(('open', source, cycle))
        def invoke(self, operation, *, payload, timeout):
            assert operation == 'context' and timeout == 60
            return worker.review_first_gold(root, value['origin']['account_ref_sha256'], 'c'*40, payload)
        def close(self): calls.append(('close',))
    monkeypatch.setattr(demo_functional_cycle, 'Runtime', LocalRuntime)
    return value, calls


def test_operator_consumes_actual_review_and_closes_original_channel(exchange):
    value, calls = exchange
    report = operator.first_gold_review_for('c'*40, value['registry'], 'e'*64)
    assert report['gold_enrollment_installed'] is False and calls[-1] == ('close',)


def test_first_review_uses_real_subprocess_pipe_sqlite_and_native_replay(bound, tmp_path, monkeypatch):
    from pathlib import Path
    import subprocess
    import sys
    from tools import demo_functional_cycle
    value, root, _, _ = bound
    config = dict(root=str(root), registry=value['registry'], account=value['origin']['account_ref_sha256'],
        at=NOW+140000, source_root=str(Path(worker.__file__).resolve().parents[1]),
        executor=str(value['executor']), issuer=str(value['issuer']), activation=str(value['activation']),
        dispatch=str(value['stores']['dispatch'].path), recovery=value['stores']['recovery'].db_path,
        oms=value['stores']['oms']._conn.execute('PRAGMA database_list').fetchone()[2])
    cfg=tmp_path/'pipe-config.json';cfg.write_text(json.dumps(config));script=tmp_path/'local-worker.py'
    script.write_text('''import sys,json
from pathlib import Path
cfg=json.loads(Path(sys.argv[1]).read_text());sys.path.insert(0,cfg['source_root'])
from tools import demo_functional_worker as w
from live.execution import demo_position_context as p,demo_market_partition_runtime as r
from live.execution.demo_dispatch_store import DemoDispatchStore
from live.execution.order_recovery import SqliteOrderRecoveryStore
from live.execution.position_ledger import LiveLedger
root=Path(cfg['root'])
# Identity/source/custody are fixture substitutes; transport and stores are real.
w.verify_source=lambda value:None
w.private_pipe=lambda fd:None
w.require_custody=lambda:root
w.fixed_account=lambda:({},cfg['account'])
w._check_path=lambda *a,**k:None
w.now_ms=lambda:cfg['at']
w.demo_authority.load_registry_for_executor=lambda:cfg['registry']
def area(ignored,cycle):
 path=root/'local-review-view'/cycle;path.mkdir(parents=True,mode=0o700,exist_ok=True);return path
w.area_for=area
w._open=lambda stack,ignored:{'dispatch':DemoDispatchStore(cfg['dispatch']),
 'recovery':SqliteOrderRecoveryStore(cfg['recovery']), 'oms':LiveLedger(cfg['oms'])}
p._ROOT=root;p._custody=lambda *a,**k:None
r.EXECUTOR_RECORD=Path(cfg['executor']);r.ISSUER_RECORD=Path(cfg['issuer']);r.ACTIVATION=Path(cfg['activation'])
r._read_record=lambda path,**kw:path.read_bytes()
r.executing_source_commit=lambda:'c'*40;r._role_identity=lambda *a:None
raise SystemExit(w.main(sys.argv[2:]))
''')
    processes=[]
    class LocalRuntime(demo_functional_cycle.Runtime):
        def start(self, operation, payload):
            process=subprocess.Popen([sys.executable,str(script),str(cfg),'--source-commit',self.source_commit,
                '--cycle-sha256',self.cycle,'--operation',operation],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,bufsize=0,cwd=config['source_root'])
            processes.append(process)
            demo_functional_cycle.supervisor._send(process.stdin,operator.setup.canonical(payload),
                demo_functional_cycle.time.monotonic()+10)
            process.stdin.close();return process
        def close(self):
            for process in processes:
                if process.poll() is None:process.kill()
                process.wait(timeout=5);(tmp_path/'pipe.stderr').write_bytes(process.stderr.read())
    monkeypatch.setattr(demo_functional_cycle,'Runtime',LocalRuntime)
    monkeypatch.setattr(operator.setup,'_runtime_inputs',lambda *a,**k:
        (deepcopy(value['registry']),deepcopy(value['origin'])))
    monkeypatch.setattr(operator.installer,'now_ms',lambda:NOW+140000)
    before=deepcopy(value['state']),value['events'].row_count()
    report=operator.first_gold_review_for('c'*40,value['registry'],'e'*64)
    assert report['gold_position_context'] is None and report['legacy_risk']['canonical_wallet']=='4999.6801'
    assert before==(value['state'],value['events'].row_count())
    assert len(processes)==1 and processes[0].returncode==0


@pytest.mark.parametrize('damage', ['source', 'registry', 'origin', 'wallet', 'expired', 'authority',
    'legacy_risk', 'candidate', 'descriptor'])
def test_operator_never_accepts_unbound_or_authoritative_review(exchange, monkeypatch, damage):
    from tools import demo_functional_cycle
    value, _ = exchange;original = demo_functional_cycle.Runtime.invoke
    def changed(self, *args, **kwargs):
        result = original(self, *args, **kwargs);report = result['review']
        if damage == 'source': report['source_commit'] = 'd'*40
        elif damage == 'registry': report['registry_sha256'] = 'f'*64
        elif damage == 'origin': report['origin_sha256'] = 'f'*64
        elif damage == 'wallet': report['sealed_usdt_wallet'] = '99999'
        elif damage == 'expired': report['valid_until_ms'] = NOW + 139999
        elif damage == 'legacy_risk': report['legacy_risk']['canonical_wallet'] = '99999'
        elif damage == 'candidate': report['original_candidate']['binding']['gold_initial_wallet'] = '99999'
        elif damage == 'descriptor': report['partition_binding']['candidate_sha256'] = 'f'*64
        else: report['approval_granted'] = True
        return result
    monkeypatch.setattr(demo_functional_cycle.Runtime, 'invoke', changed)
    with pytest.raises(ValueError): operator.first_gold_review_for('c'*40, value['registry'], 'e'*64)


def test_operator_cli_can_review_from_original_btc_setup_without_enrollment(
        exchange, book, tmp_path, monkeypatch, capsys):
    value, _ = exchange;_, context, values, _, _ = book
    setup = tmp_path/'operator-setup'
    monkeypatch.setattr(operator, 'SETUP', setup)
    monkeypatch.setattr(operator, 'verify_source', lambda _: None)
    monkeypatch.setattr(operator.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(operator, 'cycle_lock', lambda _: nullcontext())
    spec = operator.setup.propose(origin=values['legacy_origin'], registry=value['registry'],
        position_context=context, now_ms=values['now_ms'], source_commit='c'*40)
    records = {'spec.json': spec, 'result.json': {'spec_sha256': operator.setup.digest(spec),
        'source_commit': 'c'*40}, operator.installer.REGISTRY.name: value['registry']}
    monkeypatch.setattr(operator, 'read', lambda path: deepcopy(records[path.name]))
    monkeypatch.setattr(operator.setup, 'operator_confirmation', lambda *a: pytest.fail('no enrollment confirmation'))
    assert operator.main(['--source-commit', 'c'*40, '--review-first-gold-enrollment']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'FIRST_GOLD_ENROLLMENT_REVIEW_REQUIRED'
    assert result['orders_submitted'] is False and result['services_started'] is False
    assert not setup.exists()


@pytest.mark.parametrize('extra', [['--enroll'], ['--run-cycle'], ['--symbol', 'XAUUSDT'],
    ['--review-market-transition', 'XAUUSDT'], ['--portfolio-config', '/isolated.json']])
def test_review_option_cannot_be_combined_with_enrollment_or_execution(monkeypatch, extra):
    monkeypatch.setattr(operator, 'verify_source', lambda _: pytest.fail('reject before source or capture'))
    assert operator.main(['--source-commit', 'c'*40, '--review-first-gold-enrollment', *extra]) == 3
