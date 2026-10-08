"""First Scope future signal reaches the original private prepare/sign/send path.

The native Scope installation, pilot, issuer events, strategy replay, risk and
cash/position histories remain real. Operator/UID/source custody, public ENABLE
file loading and venue transport are explicit isolated fixtures. This is not a
claim of a protected public installation or a successful live venue request.
"""
from copy import deepcopy
from contextlib import closing
import json
import os
import stat
import sqlite3
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tests.live.test_gold_future_admission import (
    book as book, installed as installed, first as first, materials as materials,
    scope_inputs as scope_inputs, day_materials as day_materials, local as local,
    scope_records as scope_records, native_scope as native_scope,
    scope_admin_case as scope_admin_case, scope_observation_case, scope_observation_from_records,
)
from tests.tools.test_gold_delegated_request_prepare import call
from tests.tools.test_gold_execution_delegation_v2_install import (
    native_install as native_install, signed_native as signed_native,
    v2_installation as v2_installation,
)


def configure_delivery(f, tmp_path, monkeypatch, request, *, public_installed=False):
    from live.execution import demo_gold_delegation_enable as enabling
    from live.execution import demo_current_kill, demo_prepared_execution as executor
    from quantos.contracts.killswitch import KillSwitchState
    from tools import demo_request_prepare as preparer, demo_resident_recovery as recovery
    from tools import binance_demo_market_inputs as capture
    from tools.gold_strategy_request import task_from_draft
    from tests.tools.test_gold_scope_pilot import isolated_retention_custody
    from live.execution.demo_dispatch_store import DemoDispatchStore
    from live.execution.order_recovery import SqliteOrderRecoveryStore
    from live.execution.position_ledger import LiveLedger
    from live.execution.demo_pre_submit import _state

    f.clock = [f.now_ms]
    source_commit = f.base_policy['executor_release_commit']
    f.current_enable = deepcopy(f.enable)
    f.private = f.admin['private']
    # The shared historical fixture uses .db names. Populate the actual fixed
    # executor filenames with native SQLite backups, preserving every record.
    f.stores = {}
    for name, factory in (('dispatch', DemoDispatchStore),
            ('recovery', SqliteOrderRecoveryStore), ('oms', LiveLedger)):
        original = f.root/(name+'.db')
        target = f.root/(name+'.sqlite3')
        if not target.exists():
            assert original.is_file()
            with closing(sqlite3.connect(original.as_uri()+'?mode=ro', uri=True)) as source:
                with closing(sqlite3.connect(target)) as destination:
                    source.backup(destination)
            target.chmod(0o600)
        f.stores[name] = factory(str(target))
        request.addfinalizer(f.stores[name].close)
    assert _state(**f.stores) == f.proof['execution']['state']
    isolated_retention_custody(f.root, f.registry, tmp_path, monkeypatch)
    f.original_state = deepcopy(f.proof['execution']['state'])
    f.gets = []
    f.observation_errors = []
    native_retained_inputs = preparer.retained_delegated_inputs
    def retained_inputs(*args, **kwargs):
        try:
            return native_retained_inputs(*args, **kwargs)
        except Exception:
            import traceback
            f.observation_errors.append(traceback.format_exc())
            raise
    monkeypatch.setattr(preparer, 'retained_delegated_inputs', retained_inputs)
    f.initial_issuer_rows = deepcopy(f.admin['store'].snapshot())
    declared = deepcopy(f.signing['documents']['profile'])
    assert _digest(declared) == f.enable['original_binding']['profile_sha256']
    kill = KillSwitchState.model_validate_json(json.dumps(f.signing['documents']['dispatcher']['kill_switch']), strict=True)
    context = {'registry': f.registry, 'enable': f.enable}
    f.task = task_from_draft(f.proof['result']['decision']['draft'], profile=declared,
        issuer_policy=f.base_policy, kill_state=kill, source_commit=source_commit,
        now_ms=f.now_ms, delegated_admission=f.proof, delegation_context=context)

    uid = f.registry['executor_uid']
    virtual_os = SimpleNamespace(**{**vars(os), 'geteuid': lambda: uid,
        'getegid': lambda: uid, 'getgroups': lambda: [uid]})
    monkeypatch.setattr(preparer, 'os', virtual_os)
    monkeypatch.setattr(preparer, 'verify_source', lambda source: source == source_commit or pytest.fail('wrong source'))
    monkeypatch.setattr(preparer, 'fixed_account', lambda: (f.root, f.base_policy['account_ref_sha256']))
    monkeypatch.setattr(preparer, 'now_ms', lambda: f.clock[0])
    monkeypatch.setattr(executor, 'now_ms', lambda: f.clock[0])
    monkeypatch.setattr(executor, '_require_state_custody', lambda *a, **kw: None)
    monkeypatch.setattr(demo_current_kill, '_PATH', f.root/'kill-switch.sqlite3')

    native_check = recovery._check_path

    def check(path, owner, **kwargs):
        assert owner in (uid, os.getuid())
        path.relative_to(f.root)
        return native_check(path, os.getuid(), **kwargs)

    monkeypatch.setattr(recovery, '_check_path', check)

    def private(fd):
        info = os.fstat(fd)
        assert stat.S_ISFIFO(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077

    monkeypatch.setattr(preparer, 'private_pipe', private)

    def enabled(**kwargs):
        assert kwargs.pop('role') == 'executor'
        return deepcopy(enabling.validate(f.current_enable, **kwargs))

    if public_installed:
        # C's actual fixed-file reader remains in use; only this fixture's role
        # identity maps to the independently signed synthetic executor UID.
        monkeypatch.setattr(enabling, 'os', virtual_os)
    else:
        monkeypatch.setattr(enabling, 'load_enabled', enabled)
    monkeypatch.setattr(preparer.demo_executor_origin, 'run',
        lambda **kw: pytest.fail('retained first future signal recaptured account'))

    def market_capture(*, symbol, protected):
        assert symbol == 'XAUUSDT' and protected is True
        at = f.now_ms + 1
        end = at//60000*60000-1
        bars = []
        for index in range(61):
            start = end+1-(61-index)*60000
            price = str(4000+index)
            bars.append([start,price,price,price,price,'2',start+59999,'8000',3,'1','4000','0'])

        def get(kind, **kwargs):
            f.gets.append(kind)
            if kind.startswith('clock'):
                value = {'serverTime': at+(6 if kind == 'clock_after' else 0)}
            elif kind == 'ticker':
                value = dict(symbol=symbol,openTime=at-86400000,closeTime=at,
                    volume='100000',quoteVolume='400000000')
            else:
                value = bars
            return json.dumps(value).encode()

        ticks = iter(range(at, at+100))
        report = capture.collect(symbol=symbol, get=get, clock_ms=lambda: next(ticks))
        assert report['status'] == 'CAPTURED'
        f.clock[0] = at+10
        return report

    monkeypatch.setattr(capture, 'collect_bounded', market_capture)
    return f


@pytest.fixture
def future_delivery(scope_admin_case, native_scope, tmp_path, monkeypatch, request):
    f = scope_observation_case(scope_admin_case, native_scope, monkeypatch)
    return configure_delivery(f, tmp_path, monkeypatch, request)


@pytest.fixture
def installed_future_delivery(v2_installation, tmp_path, monkeypatch, request):
    return build_installed_future_delivery(v2_installation, tmp_path, monkeypatch, request)


def build_installed_future_delivery(installed_case, tmp_path, monkeypatch, request, *, original_issuer_prefix=()):
    """Consume actual installed events while retaining an exact prior prefix."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from live.execution.demo_issuer_store import IssuerStore
    from services.validation.demo_submission import _decode

    native = installed_case['case']
    source_commit = native['scope']['executor_release_commit']
    tool = installed_case['tool']
    registered = installed_case['install'].install_approved(**installed_case['args'])
    assert registered['consumer_enabled'] is False
    enabled_result = installed_case['enable'].enable_approved(source_commit=source_commit,
        delegation_sha256=installed_case['signed']['delegation_sha256'])
    assert enabled_result['consumer_enabled'] is True
    enable = installed_case['enable'].contract.load_enabled(registry=native['registry'],
        source_commit=source_commit, account_ref_sha256=native['scope']['account_ref_sha256'],
        now_ms=native['local']['clock'][0], role='controller')
    store = IssuerStore(native['issuer']/'issuance.sqlite3')
    request.addfinalizer(store.close)
    rows = store.snapshot()
    prefix = list(original_issuer_prefix)
    assert rows[:len(prefix)] == prefix
    grouped = [row for row in rows if row['proposal_sha256'] == installed_case['group']['group_operation_sha256']]
    signed = [row for row in rows if row['proposal_sha256'] == installed_case['signed']['sign_operation_sha256']]
    assert len(grouped) == len(signed) == 1
    assert grouped[0]['kind'] == 'GROUP_INITIALIZED' and signed[0]['kind'] == 'DELEGATION_V2_SIGNED'
    assert rows == prefix + [grouped[0], signed[0]]
    stage = tool.stage_for(installed_case['group']['group_operation_sha256'])
    group_documents = tool.read(stage/'candidate.json')['documents']
    signing_material = tool.public.load_operation(installed_case['signed']['sign_operation_sha256'],
        source_commit=source_commit, purpose='DELEGATION_V2_SIGN', registry=native['registry'], role='controller')
    # Only the independently generated isolated issuer fixture key is read.
    keydoc = json.loads((native['local']['cfg']/'signing-key.json').read_bytes())
    private = Ed25519PrivateKey.from_private_bytes(_decode(keydoc['private_key_base64url'], 32))
    admin = dict(store=store, template=group_documents['template'], registry=native['registry'],
        documents=group_documents, private=private)
    signing = dict(now_ms=signed[0]['at_ms'], documents=signing_material['documents'])
    f = scope_observation_from_records(admin=admin, native_scope=native, event=grouped[0],
        signed=signed[0], signing=signing, monkeypatch=monkeypatch, enable=enable)
    assert f.enable == enable and store.snapshot() == rows
    f.installation = installed_case
    return configure_delivery(f, tmp_path, monkeypatch, request, public_installed=True)


def send_first_future(f, monkeypatch):
    from live.execution import demo_issuer
    from live.execution.demo_execution_session import ExecutionSession, SessionRejected
    from live.exchange import binance_demo_exact as transport
    from tools.demo_task import same_declarations

    assert f.admin['store'].snapshot() == f.initial_issuer_rows
    assert [r['kind'] for r in f.initial_issuer_rows[-2:]] == ['GROUP_INITIALIZED', 'DELEGATION_V2_SIGNED']
    status, prepared = call(f)
    assert status == 0, json.dumps(prepared)+'\n'+'\n'.join(f.observation_errors)
    same_declarations(prepared['request'], f.task)
    assert prepared['request']['origin'] == f.proof['origin']
    assert f.gets == ['clock_before', 'ticker', 'klines', 'clock_after']
    posts = []
    monkeypatch.setattr(transport, '_credentials', lambda account:
        ('isolated-key','isolated-secret') if account == f.base_policy['account_ref_sha256']
        else pytest.fail('wrong account'))

    def post(body, headers):
        posts.append(body)
        return 200, json.dumps(dict(symbol='XAUUSDT', orderId=123,
            clientOrderId=f.task['orders'][0]['newClientOrderId'])).encode()

    monkeypatch.setattr(transport, '_http_once', post)
    session = ExecutionSession(**f.stores, evidence_directory=f.root/'future-delivery-evidence')
    try:
        handoff = session.propose(json.dumps(prepared['request']).encode(), now_ms=f.clock[0])
        grant = demo_issuer.issue_delegated_submission(handoff, policy=f.base_policy,
            registry=f.registry, public_material=f.current_enable, key=f.private,
            store=f.admin['store'], now_ms=f.clock[0], clock_ms=lambda: f.clock[0])
        result = session.submit(grant['proposal_sha256'], json.dumps(grant['envelope']).encode())
        assert result['execution']['status'] == 'WIRE_ATTEMPTS_RECORDED'
        assert result['cash_reconciled'] is False
        assert len(posts) == 1
        after = f.admin['store'].snapshot()
        assert after[:-2] == f.initial_issuer_rows
        assert [r['kind'] for r in after[-2:]] == ['RESERVED', 'SIGNED']
        assert after[-2]['payload']['policy']['budget_group'] == f.base_policy['budget_group']
        assert len(f.stores['dispatch'].snapshot()) == len(f.original_state['reservations'])+1
        assert f.stores['dispatch'].snapshot()[:-1] == f.original_state['reservations']
        with pytest.raises(SessionRejected):
            session.submit(grant['proposal_sha256'], json.dumps(grant['envelope']).encode())
        assert len(posts) == 1
        return result
    finally:
        session.close()


@pytest.mark.parametrize('delivery_fixture', ['future_delivery', 'installed_future_delivery'])
def test_first_scope_future_signal_prepares_signs_and_sends_without_an_exact_seed(delivery_fixture, request, monkeypatch):
    send_first_future(request.getfixturevalue(delivery_fixture), monkeypatch)


def test_installed_future_order_cancels_only_original_id_without_refund_or_oms_completion(installed_future_delivery, monkeypatch):
    from live.execution import demo_issuer, demo_cancellation
    from live.execution.demo_cancel_store import DemoCancelStore
    from live.execution.demo_cancellation_session import CancellationSession
    from live.execution.demo_execution_session import SessionRejected
    from live.execution.demo_issuer_group_continuity import history_anchor
    from live.execution.demo_wire_protocol import outcome_for_query
    from live.exchange import binance_demo_exact as transport
    from services.validation.demo_submission import GOLD_BATCH_SCHEMA
    from pathlib import Path
    from urllib.parse import parse_qs

    f = installed_future_delivery
    delivered = send_first_future(f, monkeypatch)
    batch = delivered['prepared']
    order = f.task['orders'][0]
    at = f.clock[0]+1
    observed = dict(symbol=order['symbol'], clientOrderId=order['newClientOrderId'], orderId=123,
        status='NEW', executedQty='0', time=at-1, updateTime=at,
        **{name: order[name] for name in ('side','price','positionSide','reduceOnly','timeInForce','type')},
        origQty=order['quantity'])
    query = outcome_for_query(order, 200, json.dumps(observed).encode(), schema=GOLD_BATCH_SCHEMA)
    assert query['status'] == 'FOUND'
    f.stores['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],
        client_order_id=order['newClientOrderId'], requested_at_ms=at, outcome=query, now_ms=at+1)
    f.clock[0] = at+2
    monkeypatch.setattr(demo_cancellation, 'now_ms', lambda: f.clock[0])
    monkeypatch.setattr(demo_cancellation, '_require_cancel_custody', lambda *a: None)
    monkeypatch.setattr(transport, '_http_once', lambda *_: pytest.fail('cancellation placed another order'))
    deletes = []

    def delete(body, headers):
        params = parse_qs(body.decode())
        assert params['origClientOrderId'] == [order['newClientOrderId']] and params['orderId'] == ['123']
        assert 'quantity' not in params and 'side' not in params
        deletes.append(params)
        return 200, json.dumps(dict(symbol=order['symbol'], clientOrderId=order['newClientOrderId'],
            orderId=123, status='CANCELED', executedQty='0')).encode()

    monkeypatch.setattr(transport, '_http_delete_once', delete)
    before_orders = f.stores['oms'].orders()
    before_recovery = f.stores['recovery'].latest_records()
    with DemoCancelStore(f.root/'cancellations.sqlite3', create=True) as cancel_store:
        session = CancellationSession(**f.stores, cancel_store=cancel_store,
            evidence_directory=Path(batch['proposal_path']).parent)
        try:
            request = dict(schema='DEMO_CANCELLATION_SESSION_INPUT_V1',
                reservation_sha256=batch['reservation_sha256'], client_order_ids=[order['newClientOrderId']])
            handoff = session.propose(json.dumps(request).encode(), now_ms=f.clock[0])
            grant = demo_issuer.issue_cancellation(handoff, policy=f.base_policy,
                registry=f.registry, key=f.private, store=f.admin['store'], now_ms=f.clock[0])
            result = session.submit(grant['proposal_sha256'], json.dumps(grant['envelope']).encode())
            assert result['status'] == 'ORIGINAL_ID_QUERY_RECOVERY_REQUIRED'
            assert result['cash_reconciled'] is False and len(deletes) == 1
            assert f.stores['oms'].orders() == before_orders
            assert f.stores['recovery'].latest_records() == before_recovery
            rows = f.admin['store'].snapshot()
            assert [row['kind'] for row in rows] == [
                'GROUP_INITIALIZED','DELEGATION_V2_SIGNED','RESERVED','SIGNED','RESERVED','SIGNED']
            assert rows[-2]['payload']['policy']['schema'] == 'DEMO_GOLD_TEMPLATE_CANCEL_VIEW_V1'
            assert rows[-2]['payload']['cancellation']['original_reserved_record_sha256'] == rows[2]['record_hash']
            anchor = history_anchor(rows, authorization=f.base_policy)
            assert anchor['group_consumed']['submission']['grants'] == 1
            assert anchor['group_consumed']['cancellation']['grants'] == 1
            with pytest.raises(SessionRejected):
                session.submit(grant['proposal_sha256'], json.dumps(grant['envelope']).encode())
            assert len(deletes) == 1
        finally:
            session.close()


def test_missing_installed_enable_after_signing_blocks_send_and_keeps_original_charge(installed_future_delivery, tmp_path, monkeypatch):
    from live.execution import demo_issuer
    from live.execution.demo_execution_session import ExecutionSession, SessionRejected
    from live.execution.demo_issuer_group_continuity import history_anchor
    from live.execution.demo_issuer_store import IssuerStore
    from live.exchange import binance_demo_exact as transport

    f = installed_future_delivery
    status, prepared = call(f)
    assert status == 0, prepared
    monkeypatch.setattr(transport, '_credentials', lambda *_: pytest.fail('disabled publication read credentials'))
    monkeypatch.setattr(transport, '_http_once', lambda *_: pytest.fail('disabled publication sent an order'))
    session = ExecutionSession(**f.stores, evidence_directory=f.root/'future-disabled-evidence')
    try:
        handoff = session.propose(json.dumps(prepared['request']).encode(), now_ms=f.clock[0])
        grant = demo_issuer.issue_delegated_submission(handoff, policy=f.base_policy,
            registry=f.registry, public_material=f.current_enable, key=f.private,
            store=f.admin['store'], now_ms=f.clock[0], clock_ms=lambda: f.clock[0])
        before = f.admin['store'].snapshot()
        anchor = history_anchor(before, authorization=f.base_policy)
        assert anchor['group_consumed']['submission']['grants'] == anchor['reserved'] == 1
        assert anchor['unsigned_reservations'] == 0
        wire_before = f.stores['dispatch'].wire_snapshot()
        complete = f.installation['enable'].contract.PUBLIC_ROOT/'complete.json'
        complete.relative_to(tmp_path)
        complete.unlink()  # Isolated interrupted/revoked public publication.
        with pytest.raises(SessionRejected, match='SESSION_STOPPED'):
            session.submit(grant['proposal_sha256'], json.dumps(grant['envelope']).encode())
        assert f.stores['dispatch'].wire_snapshot() == wire_before
        with closing(IssuerStore(f.admin['store'].path, read_only=True)) as reopened:
            assert reopened.snapshot() == before
            assert history_anchor(reopened.snapshot(), authorization=f.base_policy) == anchor
        with pytest.raises(SessionRejected, match='SESSION_NO_PENDING'):
            session.submit(grant['proposal_sha256'], json.dumps(grant['envelope']).encode())
    finally:
        session.close()
