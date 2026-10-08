"""Native archive + framed worker + SQLite handoff, with explicit fixture identity."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tests.live.test_demo_market_partition import NOW, book as book
from tests.live.test_demo_market_partition_runtime import installed as installed
from tests.live.test_gold_first_enrollment_review import first as first
from tests.tools.test_gold_first_enrollment_materials import materials as materials
from tests.tools.test_gold_first_enrollment_review import bound as bound
from tests.tools.test_gold_budget_scope_preparation import scope_inputs as scope_inputs
from tools import gold_strategy_budget_enrollment as preparation
from tools import demo_functional_worker as worker, demo_functional_cycle

NativeRuntime = demo_functional_cycle.Runtime


@pytest.fixture
def operator_inputs(scope_inputs, bound, tmp_path, monkeypatch):
    from tools import gold_strategy_controller
    value, policy, profile, definition, runtime = scope_inputs
    _, root, _, _ = bound
    trial = root/'strategy-bar-trials'/runtime['trial_manifest_sha256']
    source = root/'strategy-bar-sources'/runtime['archive_source_commit']
    shutil.copytree(value['scope_archive'], trial)
    repo = Path(worker.__file__).resolve().parents[1]
    # Transfer self-contained objects instead of extending nested alternates.
    subprocess.run(['git', 'clone', '--no-local', '--quiet', str(repo), str(source)], check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    # Preserve the actual development overlay that generated the native archive.
    for name in ('services', 'quantos', 'tools'):
        shutil.copytree(repo/name, source/name, dirs_exist_ok=True)
    shutil.copy2(repo/'requirements-ci-linux-aarch64.lock', source/'requirements-ci-linux-aarch64.lock')
    # These owned temporary copies must satisfy the real file-mode checks.
    root.chmod(0o700)
    for area in (trial.parent, source.parent):
        area.chmod(0o700)
        for path in area.rglob('*'):
            path.chmod(0o700 if path.is_dir() else 0o600)
    monkeypatch.setattr(preparation.installer, 'EXECUTOR', root)
    native_load = preparation.load_definition
    # The fixture's virtual executor UID is mapped to its actual local file owner.
    monkeypatch.setattr(preparation, 'load_definition', lambda *a, **kw:
        native_load(*a, **(kw | {'uid': root.stat().st_uid})))
    monkeypatch.setattr(preparation.supervisor, 'controller_lease', lambda **kw: __import__('contextlib').nullcontext())
    monkeypatch.setattr(gold_strategy_controller, 'observation_configuration', lambda _: (deepcopy(value['registry']), deepcopy(policy)))
    original = {'source_commit':'c'*40, 'spec_sha256':'a'*64, 'status':'INSTALLED_PENDING_CYCLE'}
    spec = {'source_commit':'c'*40, 'symbol':'XAUUSDT'}
    original['spec_sha256'] = preparation.setup.digest(spec)
    stage = tmp_path/'virtual-installed-stage'
    monkeypatch.setattr(preparation.successor, 'active_setup', lambda: stage)
    monkeypatch.setattr(preparation.successor, 'read', lambda p: deepcopy(original if p.name == 'result.json' else spec))
    monkeypatch.setattr(preparation.setup, 'spec_check', lambda v: v)
    contents = {'strategy-runtime.json': json.dumps(runtime).encode(), 'profile.json': json.dumps(profile).encode()}
    monkeypatch.setattr(preparation.supervisor, '_read_root_owned_json', lambda p: contents[p.name])
    monkeypatch.setattr(preparation.setup, '_runtime_inputs', lambda *a, **kw:
        (deepcopy(value['registry']), deepcopy(value['origin'])))
    monkeypatch.setattr(preparation.installer, 'now_ms', lambda: NOW+140000)
    calls = []
    class LocalRuntime:
        def __init__(self, source_commit, registry, cycle):
            assert source_commit == 'c'*40 and registry == value['registry']
            assert cycle == preparation.setup.digest(original)
            calls.append(('open', cycle))
        def invoke(self, operation, *, payload, timeout):
            assert operation == 'context' and timeout == 60
            calls.append(('context', deepcopy(payload)))
            return worker.review_gold_budget_scope(root, value['origin']['account_ref_sha256'], 'c'*40, payload)
        def close(self): calls.append(('close',))
    monkeypatch.setattr(demo_functional_cycle, 'Runtime', LocalRuntime)
    return value, root, policy, contents, calls, original, LocalRuntime


def test_original_operator_reads_native_archive_and_worker_without_installing(operator_inputs):
    value, root, _, _, calls, _, _ = operator_inputs
    before = deepcopy(value['state']), value['events'].row_count(), (root/'kill-switch.sqlite3').read_bytes()
    request = preparation.prepare_original(source_commit='c'*40)
    assert request['original_cycle_review']['gold_position_context'] is None
    assert request['original_cycle_review']['legacy_risk']['canonical_wallet'] == '4999.6801'
    assert request['duration_seconds'] == 86400 and request['approval_granted'] is False
    assert calls[-1] == ('close',) and len(calls) == 3
    assert before == (value['state'], value['events'].row_count(), (root/'kill-switch.sqlite3').read_bytes())
    assert not (root/'gold-pilot').exists()


@pytest.mark.parametrize('damage', ['profile', 'runtime', 'policy', 'stop'])
def test_original_operator_rechecks_after_worker_and_closes_channel(operator_inputs, monkeypatch, damage):
    value, root, policy, contents, calls, _, local = operator_inputs
    def changed(self, *a, **kw):
        result = local.invoke(self, *a, **kw)
        if damage == 'profile': contents['profile.json'] += b' '
        elif damage == 'runtime': contents['strategy-runtime.json'] += b' '
        elif damage == 'policy': policy['max_orders'] += 1
        else: (root/'kill-switch.sqlite3').write_bytes(b'isolated damaged stop')
        return result
    class ChangedRuntime(local):
        invoke = changed
    if damage == 'stop':
        # Change during the protected native worker, so its post-read check must fail.
        original_stop = worker._partition_stop;count = [0]
        def stop(*a, **kw):
            count[0] += 1
            if count[0] == 2: (root/'kill-switch.sqlite3').write_bytes(b'isolated damaged stop')
            return original_stop(*a, **kw)
        monkeypatch.setattr(worker, '_partition_stop', stop)
    else: monkeypatch.setattr(demo_functional_cycle, 'Runtime', ChangedRuntime)
    with pytest.raises(ValueError): preparation.prepare_original(source_commit='c'*40)
    assert calls[-1] == ('close',)


def test_operator_uses_real_private_frame_and_original_sqlite_worker(operator_inputs, tmp_path, monkeypatch):
    value, root, _, _, _, original, _ = operator_inputs
    cfg = dict(root=str(root), registry=value['registry'], account=value['origin']['account_ref_sha256'],
        at=NOW+140000, source_root=str(Path(worker.__file__).resolve().parents[1]),
        executor=str(value['executor']), issuer=str(value['issuer']), activation=str(value['activation']),
        dispatch=str(value['stores']['dispatch'].path), recovery=value['stores']['recovery'].db_path,
        oms=value['stores']['oms']._conn.execute('PRAGMA database_list').fetchone()[2])
    config = tmp_path/'worker-fixture.json';config.write_text(json.dumps(cfg))
    script = tmp_path/'worker-fixture.py'
    script.write_text('''import json,sys
from pathlib import Path
cfg=json.loads(Path(sys.argv[1]).read_text());sys.path.insert(0,cfg['source_root'])
from tools import demo_functional_worker as w
from live.execution import demo_position_context as p,demo_market_partition_runtime as r
from live.execution.demo_dispatch_store import DemoDispatchStore
from live.execution.order_recovery import SqliteOrderRecoveryStore
from live.execution.position_ledger import LiveLedger
root=Path(cfg['root'])
# Virtual operator/source/custody only; native framing, SQLite and V2 are retained.
w.verify_source=lambda value:None;w.private_pipe=lambda fd:None
w.require_custody=lambda:root;w.fixed_account=lambda:({},cfg['account'])
w._check_path=lambda *a,**kw:None;w.now_ms=lambda:cfg['at']
w.demo_authority.load_registry_for_executor=lambda:cfg['registry']
def area(ignored,cycle):
 path=root/'fixture-budget-reviews'/cycle;path.mkdir(parents=True,exist_ok=True);return path
w.area_for=area
w._open=lambda stack,ignored:{'dispatch':DemoDispatchStore(cfg['dispatch']),
 'recovery':SqliteOrderRecoveryStore(cfg['recovery']), 'oms':LiveLedger(cfg['oms'])}
p._ROOT=root;p._custody=lambda *a,**kw:None
r.EXECUTOR_RECORD=Path(cfg['executor']);r.ISSUER_RECORD=Path(cfg['issuer']);r.ACTIVATION=Path(cfg['activation'])
r._read_record=lambda path,**kw:path.read_bytes();r.executing_source_commit=lambda:'c'*40
r._role_identity=lambda *a:None
raise SystemExit(w.main(sys.argv[2:]))
''')
    processes = []
    class PipeRuntime(NativeRuntime):
        def start(self, operation, payload):
            child = subprocess.Popen([sys.executable, str(script), str(config), '--source-commit', self.source_commit,
                '--cycle-sha256', self.cycle, '--operation', operation], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, bufsize=0, cwd=cfg['source_root'])
            processes.append(child)
            preparation.supervisor._send(child.stdin, preparation.setup.canonical(payload),
                demo_functional_cycle.time.monotonic()+10)
            child.stdin.close();return child
        def close(self):
            for child in processes:
                if child.poll() is None: child.kill()
                child.wait(timeout=5);(tmp_path/'worker.stderr').write_bytes(child.stderr.read())
    monkeypatch.setattr(demo_functional_cycle, 'Runtime', PipeRuntime)
    before = deepcopy(value['state']), value['events'].row_count()
    request = preparation.prepare_original(source_commit='c'*40)
    assert request['original_cycle_review']['observed_usdt_wallet'] == '4200'
    assert before == (value['state'], value['events'].row_count())
    assert len(processes) == 1 and processes[0].returncode == 0
    assert request['original_cycle_review']['full_state_sha256'] == preparation.setup.digest(value['state'])


@pytest.mark.parametrize('damage', ['manifest', 'sidecar', 'source'])
def test_fixed_archive_preparation_rejects_changed_inputs_before_worker(operator_inputs, damage):
    value, root, _, contents, calls, _, _ = operator_inputs
    runtime = json.loads(contents['strategy-runtime.json'])
    trial = root/'strategy-bar-trials'/runtime['trial_manifest_sha256']
    if damage == 'manifest': (trial/'manifest.json').write_text('{}')
    elif damage == 'sidecar': (trial/'trials.db-wal').write_bytes(b'isolated pending data')
    else:
        source = root/'strategy-bar-sources'/runtime['archive_source_commit']
        (source/'tools/gold_bar_cost_replay.py').write_text('# isolated changed semantics\n')
    with pytest.raises(ValueError): preparation.prepare_original(source_commit='c'*40)
    assert calls == []
