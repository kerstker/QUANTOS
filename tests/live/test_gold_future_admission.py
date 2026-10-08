"""Future-template admission uses native observations and original issuer events.

The observation fixture creates no issuer policy, reservation or history. Tests
compose it with separately verified administrative continuity; no admission,
ENABLE or forward-replay verifier is replaced with a success stub.
"""

from copy import deepcopy
import hashlib
import json
import os
import shutil
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tests.live.test_gold_strategy_constraints import case as case
from tests.tools.test_gold_runtime_evidence import original_evidence as original_evidence
from tests.live.test_demo_issuer_group_continuity import (
    book as book, installed as installed, first as first, materials as materials,
    day_materials as day_materials, local as local, scope_records as scope_records,
    native_scope as native_scope, scope_admin_case as scope_admin_case,
)


@pytest.fixture
def scope_inputs(materials, tmp_path):
    """Bind actual public warmup bytes before preparing or signing the Scope."""
    from tests.tools.test_gold_budget_scope_preparation import scope_inputs as original
    from tests.live.test_demo_market_partition import NOW
    from tests.research.test_gold_bar_dataset import raw_rows, STEP
    from tools.demo_operator_setup import canonical

    parts = original.__wrapped__(materials, tmp_path)
    value, _, _, _, runtime = parts
    scope_at = NOW + 3741000
    warmup_end = scope_at // STEP * STEP
    rows = raw_rows()
    delta = warmup_end - rows[-1][6] - 1
    for row in rows:
        row[0] += delta
        row[6] += delta
    raw = canonical(rows)
    receipt = (json.dumps(dict(requested_at_ms=warmup_end,
        received_at_ms=warmup_end + 10, response_sha256=hashlib.sha256(raw).hexdigest()),
        sort_keys=True, allow_nan=False, indent=2) + '\n').encode()
    runtime['history_sha256'] = hashlib.sha256(receipt).hexdigest()
    root = value['stores']['dispatch'].path.parent
    area = root / 'strategy-bar-history' / runtime['history_sha256']
    area.parent.mkdir(mode=0o700)
    area.mkdir(mode=0o700)
    for name, body in (('response.json', raw), ('receipt.json', receipt)):
        path = area / name
        path.write_bytes(body)
        path.chmod(0o600)
    value['scope_warmup_end_ms'] = warmup_end
    return parts


def scope_observation_case(scope_admin_case, native_scope, monkeypatch, *, max_holding_ms=None):
    """Actual Scope, baseline, INIT/SIGN and FRESH prefix; no exact-policy seed."""
    from tests.live.test_demo_issuer_group_continuity import initialized_scope, signing_case, operation_for
    from tests.research.test_gold_bar_dataset import STEP

    f = scope_admin_case
    event = initialized_scope(f)
    signing = signing_case(f, event)
    # The newly confirmed SIGN intent must permit the original archived rule's
    # full holding horizon. Change it before subject/confirmation/signature.
    signing['documents']['delegation_intent']['max_holding_ms'] = (
        signing['documents']['definition']['rules']['max_holding_bars'] * STEP
        if max_holding_ms is None else max_holding_ms)
    operation = operation_for(signing, sign=True)
    signed = f['store'].sign_delegation(operation, documents=signing['documents'], registry=f['registry'],
        installed_template=f['template'], key=f['private'], now_ms=signing['now_ms'])
    return scope_observation_from_records(admin=f, native_scope=native_scope, event=event,
        signed=signed, signing=signing, monkeypatch=monkeypatch)


def scope_observation_from_records(*, admin, native_scope, event, signed, signing, monkeypatch, enable=None):
    """Observe after existing original INIT/SIGN; never sign or re-enroll here.

    Root delivery may supply actual REGISTER/ENABLE public-reader output. The
    default only constructs explicitly labeled pure ENABLE contract content.
    """
    from tests.live.test_demo_market_partition_runtime import shift
    from tests.research.test_gold_bar_dataset import STEP
    from live.execution.demo_pre_submit import _state
    from live.execution import demo_market_partition_runtime as partition
    from tools import gold_bar_observe as observer
    from live.exchange import demo_network
    from tools.gold_bar_fresh_inputs import FreshEpisode
    from tools.gold_bar_episode_tail import EpisodeTail
    from tools.gold_bar_demo_cycle import prepare
    from tools.gold_bar_pilot_capacity import original_context
    from tools.gold_pilot_retention_store import Retention
    from tools.gold_delegated_proof import freeze_prefix, _pilot_records
    from tools.gold_strategy_worker import original_pilot_window
    from live.execution import demo_gold_delegation_enable as enabling

    f = admin
    source_commit = f['template']['executor_release_commit']
    original_rows = deepcopy(f['store'].snapshot())
    assert original_rows[event['seq']-1] == event and original_rows[signed['seq']-1] == signed
    assert event['kind'] == 'GROUP_INITIALIZED' and signed['kind'] == 'DELEGATION_V2_SIGNED'
    assert event['seq'] < signed['seq']
    delegation = signed['payload']['artifact']
    assert delegation['continuity_event_sha256'] == event['record_hash']
    assert delegation['future_template'] == f['template']
    root = native_scope['local']['root']
    runtime = deepcopy(f['documents']['runtime'])
    warmup_end = native_scope['local']['value']['scope_warmup_end_ms']
    at = warmup_end + STEP + 20
    clock = [signing['now_ms'] + 2]
    if enable is not None:
        assert enable['delegation'] == delegation and enable['runtime'] == runtime
        assert enable['definition'] == f['documents']['definition'] and enable['continuity_event'] == event
        clock[0] = max(clock[0], enable['operator_confirmation']['approved_at_ms']+1)
        enabling.validate(enable, registry=f['registry'], source_commit=source_commit,
            account_ref_sha256=f['template']['account_ref_sha256'], now_ms=clock[0])
        for anchor in (enable['issuer_anchor'], enable['original_binding']['issuer_current']):
            enabling.verify_history_prefix(anchor, original_rows, policy=f['template'])
    assert clock[0] < warmup_end + STEP, 'fixture must register before its first future close'
    message = json.dumps(dict(stream='xauusdt@kline_15m', data=dict(e='kline', E=at, s='XAUUSDT',
        k=dict(t=warmup_end, T=warmup_end+STEP-1, s='XAUUSDT', i='15m', x=True,
            o='100', h='110', l='99', c='109'))))

    class Socket:
        consumed = False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def recv(self):
            assert not self.consumed, 'fixture first close must complete in one bounded receive'
            self.consumed = True
            clock[0] = at
            return message

    # Only the public market transport and its arrival clock are synthetic.
    monkeypatch.setattr(observer, 'connect', lambda *a, **kw: Socket())
    monkeypatch.setattr(demo_network, 'proxy_url', lambda: None)
    monkeypatch.setattr(demo_network, 'tls_context', lambda: object())
    monkeypatch.setattr(observer.time, 'time_ns', lambda: clock[0]*1000000)
    old_umask = os.umask(0o077)
    try:
        episode = FreshEpisode(root, runtime, source_commit=source_commit, uid=os.getuid())
    finally:
        os.umask(old_umask)
    at += 1
    clock[0] = at
    tail = EpisodeTail(root, bundle_sha256=_digest(runtime), engine=episode.engine,
        entry_decision_ms=episode.entry_decision_ms)
    engine, continuation, _ = tail.snapshot()
    source_origin = native_scope['local']['parts']['origin']
    origin = shift(source_origin, at-source_origin['completed_at_ms']-1)
    stores = native_scope['local']['value']['stores']
    original = original_context(root, runtime, uid=os.getuid(), account=origin['account_ref_sha256'])
    with Retention(root, original=original, base_runtime=runtime, uid=os.getuid(), role='executor') as retained:
        claim = retained.claim(bundle=runtime, now_ms=at)
        retained.put(claim, 'origin', origin)
        decision = prepare(engine=engine, entry_decision_ms=episode.entry_decision_ms, origin=origin,
            **{k:stores[k] for k in ('dispatch','recovery','oms')}, now_ms=at)
        assert decision['status'] == 'DECISION_REQUIRES_APPROVAL', decision
        decision.update(source_commit=source_commit, bundle_sha256=_digest(runtime),
            account_ref_sha256=origin['account_ref_sha256'], origin_sha256=_digest(origin),
            bar_continuation_sha256=continuation)
        retained.put(claim, 'decision', decision)
        result = dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1', status='DECISION_RETAINED',
            source_commit=source_commit, bundle_sha256=_digest(runtime), decision_sha256=_digest(decision),
            decision=decision, submission_enabled=False, economic_authority=False,
            original_pilot_window=original_pilot_window(root, decision=decision,
                source_commit=source_commit, account=origin['account_ref_sha256']))
        retained.put(claim, 'result', result)
        capacity = deepcopy(retained.binding)
    state = _state(*(stores[k] for k in ('dispatch','recovery','oms')))
    context, _ = partition.resolve_context(state=state, origin=origin, as_of_ms=at)
    pilot = _pilot_records(root, os.getuid())
    proof = dict(schema='DEMO_GOLD_DELEGATED_DECISION_PROOF_V1', source_commit=source_commit,
        runtime=runtime, result=result, origin=origin, claim=claim, capacity=capacity, pilot=pilot,
        execution=dict(state=state, context=context, exit_trigger=None, trigger_evidence=None),
        prefix=freeze_prefix(root=root, runtime=runtime, decision=decision, source_commit=source_commit, uid=os.getuid()),
        submission_enabled=False, economic_authority=False)
    value = SimpleNamespace(root=root, now_ms=at, proof=proof, definition=f['documents']['definition'],
        runtime=runtime, pilot=pilot, configuration=pilot['configuration'], request=pilot['request'],
        delegation=delegation, continuity_event=event, signed=signed, admin=f, signing=signing,
        registry=f['registry'], base_policy=f['template'], native=native_scope)
    value.enable = scope_enable_value(value) if enable is None else deepcopy(enable)
    proof.update(enable_sha256=_digest(value.enable), delegation_sha256=_digest(delegation))
    assert f['store'].snapshot() == original_rows
    return value


def scope_enable_value(f):
    """Actual signed ancestry, pure ENABLE content; no public-install claim."""
    from live.execution import demo_gold_delegation_enable as contract, demo_issuer_group_continuity as continuity
    docs = f.signing['documents']
    anchor = continuity.history_anchor(f.admin['store'].snapshot(), authorization=f.base_policy)
    # This pure-content fixture does not substitute an actual protected REGISTER
    # operation. C separately tests its complete registration and public reader.
    registration = dict(schema='ISOLATED_ENABLE_CONTENT_FIXTURE', delegation_sha256=_digest(f.delegation),
        continuity_event_sha256=f.continuity_event['record_hash'], consumer_enabled=False)
    candidate = dict(schema='GOLD_EXECUTION_DELEGATION_ENABLE_CANDIDATE_V2', delegation=f.delegation,
        continuity_event=f.continuity_event, runtime=f.runtime, definition=f.definition,
        registration_sha256=_digest(registration), registration_candidate_sha256=_digest(registration),
        issuer_anchor=anchor, original_binding=dict(registry_sha256=_digest(f.registry),
            policy_sha256=_digest(f.base_policy), profile_sha256=_digest(docs['profile']),
            runtime_sha256=_digest(f.runtime), definition_sha256=_digest(f.definition),
            original_setup_sha256=_digest(f.admin['documents']['ancestry']['result']),
            original_spec_sha256=_digest(f.admin['documents']['ancestry']['spec']),
            executor_ledger_state_hash=_digest(f.proof['execution']['state']),
            dispatcher_sha256=_digest(docs['dispatcher']), issuer_current=anchor),
        consumer_enabled=False, submission_enabled=False, economic_authority=False)
    approval = dict(schema='GOLD_EXECUTION_DELEGATION_ENABLE_APPROVAL_V1', purpose='ENABLE_BOUNDED_FUTURE_SIGNALS',
        candidate_sha256=_digest(candidate), operator_id='isolated-new-future-confirmation',
        approved_at_ms=f.signing['now_ms']+1, consumer_enabled=True, submission_enabled=False, economic_authority=False)
    value = dict(candidate, schema='DEMO_GOLD_DELEGATION_ENABLE_V2', consumer_enabled=True, operator_confirmation=approval)
    return contract.validate(value, registry=f.registry, source_commit=f.base_policy['executor_release_commit'],
        account_ref_sha256=f.base_policy['account_ref_sha256'], now_ms=f.now_ms)


def test_scope_signed_runtime_generates_native_fresh_proof_without_an_exact_grant(scope_admin_case, native_scope, monkeypatch):
    f = scope_observation_case(scope_admin_case, native_scope, monkeypatch)
    assert [row['kind'] for row in f.admin['store'].snapshot()] == ['GROUP_INITIALIZED', 'DELEGATION_V2_SIGNED']
    assert f.proof['runtime'] == f.signing['documents']['runtime']
    assert f.proof['result']['decision']['action'] == 'OPEN_LONG'


def test_first_scope_future_signal_admits_without_placeholder_or_first_exact_policy(scope_admin_case, native_scope, monkeypatch):
    from live.execution.demo_gold_delegated_admission import admit
    f = scope_observation_case(scope_admin_case, native_scope, monkeypatch)
    before = deepcopy(f.admin['store'].snapshot())
    checked = admit(delegation=f.delegation, enable=f.enable, base_policy=f.base_policy,
        registry=f.registry, proof=f.proof, now_ms=f.now_ms)
    assert checked['constraints'] == f.proof['result']['decision']['execution_constraints']
    assert checked['orders'] == f.proof['result']['decision']['draft']['orders']
    assert checked['effective_policy']['policy_id'] == 'gold-delegated-'+_digest(f.proof)
    assert 'execution_constraints_sha256' not in f.base_policy['bindings']
    assert f.admin['store'].snapshot() == before


def test_future_admission_rejects_changed_originals_and_never_renews_deadline(scope_admin_case, native_scope, monkeypatch):
    from live.execution.demo_gold_delegated_admission import admit
    f = scope_observation_case(scope_admin_case, native_scope, monkeypatch)
    args = dict(delegation=f.delegation, enable=f.enable, base_policy=f.base_policy,
        registry=f.registry, proof=f.proof, now_ms=f.now_ms)
    admitted = admit(**args)
    assert admit(**dict(args, now_ms=f.now_ms+1))['valid_until_ms'] == admitted['valid_until_ms']
    before = deepcopy(f.admin['store'].snapshot())
    for damage in ('raw_prefix', 'tail', 'signal', 'quantity', 'capacity', 'pilot', 'runtime',
            'source', 'state', 'origin', 'stopped', 'enable', 'event', 'key', 'expired', 'future'):
        changed = deepcopy(args)
        proof = changed['proof']
        decision = proof['result']['decision']
        if damage == 'raw_prefix':
            import base64
            name = 'journal/messages.jsonl'
            raw = base64.b64decode(proof['prefix']['files'][name])
            proof['prefix']['files'][name] = base64.b64encode(raw.replace(b'109', b'108')).decode()
            assert proof['prefix'] != f.proof['prefix']
        elif damage == 'tail':proof['prefix']['tail']['binding']['signal_sha256'] = '0'*64
        elif damage == 'signal':decision['bar_signal_sha256'] = '0'*64
        elif damage == 'quantity':decision['draft']['orders'][0]['quantity'] = '.003'
        elif damage == 'capacity':proof['capacity']['ends_at_ms'] += 1
        elif damage == 'pilot':proof['pilot']['configuration']['opening_origin_sha256'] = '0'*64
        elif damage == 'runtime':proof['runtime']['history_sha256'] = '0'*64
        elif damage == 'source':proof['source_commit'] = 'd'*40
        elif damage == 'state':proof['execution']['state']['reservations'] = []
        elif damage == 'origin':proof['origin']['account_ref_sha256'] = '0'*64
        elif damage == 'stopped':proof['pilot']['stop'] = {}
        elif damage == 'enable':proof['enable_sha256'] = '0'*64
        elif damage == 'event':changed['enable']['continuity_event']['record_hash'] = '0'*64
        elif damage == 'key':changed['registry']['keys'][-1]['revoked'] = True
        elif damage == 'expired':changed['now_ms'] = admitted['valid_until_ms']
        else:changed['now_ms'] -= 1
        proof['result']['decision_sha256'] = _digest(decision)
        with pytest.raises(ValueError):
            admit(**changed)
    assert f.admin['store'].snapshot() == before


def test_independently_signed_shorter_holding_permission_cannot_rewrite_archived_rule(scope_admin_case, native_scope, monkeypatch):
    from live.execution.demo_gold_delegated_admission import admit
    f = scope_observation_case(scope_admin_case, native_scope, monkeypatch, max_holding_ms=900000)
    with pytest.raises(ValueError, match='GOLD_DELEGATED_HOLDING_LIMIT'):
        admit(delegation=f.delegation, enable=f.enable, base_policy=f.base_policy,
            registry=f.registry, proof=f.proof, now_ms=f.now_ms)
    assert f.delegation['terms']['max_holding_ms'] == 900000
    assert f.definition['rules']['max_holding_bars'] == 96


def test_future_wire_resolves_original_exact_input_only(scope_admin_case, native_scope, monkeypatch):
    from live.execution.demo_delegated_inputs import resolve
    f = scope_observation_case(scope_admin_case, native_scope, monkeypatch)
    decision = f.proof['result']['decision']
    args = dict(policy=f.base_policy, orders=decision['draft']['orders'], constraints=decision['execution_constraints'],
        context=dict(registry=f.registry, enable=f.enable), now_ms=f.now_ms)
    checked = resolve(f.proof, **args)
    assert checked['proof_sha256'] == _digest(f.proof)
    different = deepcopy(args)
    different['orders'][0]['quantity'] = '.003'
    with pytest.raises(ValueError, match='DELEGATED_INPUTS_CHANGED'):
        resolve(f.proof, **different)


def test_observation_consumes_existing_signed_rows_and_supplied_enable_without_resigning(scope_admin_case, native_scope, monkeypatch):
    from tests.live.test_demo_issuer_group_continuity import initialized_scope, signing_case, operation_for
    from live.execution.demo_pre_submit import _state
    from live.execution.demo_gold_delegated_admission import admit
    admin = scope_admin_case
    event = initialized_scope(admin)
    signing = signing_case(admin, event)
    signing['documents']['delegation_intent']['max_holding_ms'] = (
        signing['documents']['definition']['rules']['max_holding_bars'] * 900000)
    signed = admin['store'].sign_delegation(operation_for(signing, sign=True),
        documents=signing['documents'], registry=admin['registry'], installed_template=admin['template'],
        key=admin['private'], now_ms=signing['now_ms'])
    state = _state(*(native_scope['local']['value']['stores'][k] for k in ('dispatch','recovery','oms')))
    content = SimpleNamespace(admin=admin, signing=signing, base_policy=admin['template'],
        registry=admin['registry'], delegation=signed['payload']['artifact'], continuity_event=event,
        runtime=admin['documents']['runtime'], definition=admin['documents']['definition'],
        proof=dict(execution=dict(state=state)), now_ms=signing['now_ms']+2)
    enable = scope_enable_value(content)
    before = deepcopy(admin['store'].snapshot())
    f = scope_observation_from_records(admin=admin, native_scope=native_scope, event=event,
        signed=signed, signing=signing, monkeypatch=monkeypatch, enable=enable)
    assert f.enable == enable and f.proof['enable_sha256'] == _digest(enable)
    assert admit(delegation=f.delegation, enable=enable, base_policy=f.base_policy,
        registry=f.registry, proof=f.proof, now_ms=f.now_ms)['orders']
    assert admin['store'].snapshot() == before


def observation_case(case, original_evidence, tmp_path, monkeypatch):
    """Native executor-only preparation, completed pilot and immutable prefix."""
    from live.execution import demo_authority, demo_position_context as position
    from live.execution import demo_market_partition_runtime as partition
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from live.execution.demo_pre_submit import _state
    from tests.live.test_demo_pre_submit import stores
    from tests.tools.test_gold_strategy_entry import origin_at
    from tests.tools.test_gold_pilot_capacity import write
    from tools.gold_bar_worker_inputs import load
    from tools.gold_bar_demo_cycle import prepare
    from tools.gold_bar_pilot_capacity import original_context
    from tools.gold_pilot_retention_store import Retention
    from tools.gold_delegated_proof import freeze_prefix
    from tools.gold_strategy_worker import original_pilot_window

    root = tmp_path / 'executor'
    shutil.copytree(original_evidence['root'], root)
    runtime = deepcopy(original_evidence['retained'])
    at = original_evidence['entry_ms'] + 1
    start = at - 100
    monkeypatch.setattr(position, '_ROOT', root)

    def custody(path, *, directory):
        from tools.demo_resident_recovery import _check_path
        path.relative_to(root)
        _check_path(path, os.getuid(), directory=directory)

    monkeypatch.setattr(position, '_custody', custody)
    monkeypatch.setattr(demo_authority, 'load_registry_for_executor', lambda: {'executor_uid': os.getuid()})
    monkeypatch.setattr(partition, 'EXECUTOR_RECORD', tmp_path / 'partition-executor.json')
    monkeypatch.setattr(partition, 'ISSUER_RECORD', tmp_path / 'partition-issuer.json')
    monkeypatch.setattr(partition, 'ACTIVATION', tmp_path / 'partition-activation.json')
    origin = origin_at(at)
    request = dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1', source_commit='c'*40,
        account_ref_sha256=origin['account_ref_sha256'], duration_seconds=86400,
        max_loss_fraction='0.05', submission_enabled=False)
    write(root / 'gold-pilot-request.json', request)
    native = stores(root)
    try:
        config = enrollment.enroll_requested(root=root, source_commit='c'*40, origin=origin_at(start),
            **dict(zip(('dispatch', 'recovery', 'oms'), native)), now_ms=start)
        engine, _ = load(root, runtime, source_commit='c'*40, uid=os.getuid())
        definition = engine.verified_definition()
        decision = prepare(engine=engine, entry_decision_ms=runtime['entry_decision_ms'], origin=origin,
            **dict(zip(('dispatch', 'recovery', 'oms'), native)), now_ms=at)
        execution = dict(state=_state(*native), context=None, exit_trigger=None, trigger_evidence=None)
    finally:
        for value in native:
            value.close()
    assert decision['status'] == 'DECISION_REQUIRES_APPROVAL'
    decision.update(source_commit='c'*40, bundle_sha256=_digest(runtime),
        account_ref_sha256=origin['account_ref_sha256'], origin_sha256=_digest(origin))
    original = original_context(root, runtime, uid=os.getuid(), account=origin['account_ref_sha256'])
    with Retention(root, original=original, base_runtime=runtime, uid=os.getuid(), role='executor') as retained:
        claim = retained.claim(bundle=runtime, now_ms=at)
        retained.put(claim, 'origin', origin)
        retained.put(claim, 'decision', decision)
        result = dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1', status='DECISION_RETAINED',
            source_commit='c'*40, bundle_sha256=_digest(runtime), decision_sha256=_digest(decision),
            decision=decision, submission_enabled=False, economic_authority=False,
            original_pilot_window=original_pilot_window(root, decision=decision,
                source_commit='c'*40, account=origin['account_ref_sha256']))
        retained.put(claim, 'result', result)
        capacity = deepcopy(retained.binding)
    pilot = dict(request=request, configuration=config,
        receipt=json.loads((root / 'gold-pilot/enrollment.json').read_bytes()),
        intent=json.loads((root / 'gold-pilot/enrollment-intent.json').read_bytes()), stop=None)
    proof = dict(schema='DEMO_GOLD_DELEGATED_DECISION_PROOF_V1', source_commit='c'*40,
        runtime=runtime, result=result, origin=origin, claim=claim, capacity=capacity,
        pilot=pilot, execution=execution,
        prefix=freeze_prefix(root=root, runtime=runtime, decision=decision,
            source_commit='c'*40, uid=os.getuid()), submission_enabled=False, economic_authority=False)
    return SimpleNamespace(root=root, now_ms=at, proof=proof, definition=definition,
        runtime=runtime, pilot=pilot, configuration=config, request=request)


def test_native_observation_preparation_does_not_seed_an_issuer_history(case, original_evidence, tmp_path, monkeypatch):
    f = observation_case(case, original_evidence, tmp_path, monkeypatch)
    assert case[-1].snapshot() == []
    assert f.proof['execution']['state']['reservations'] == []
    assert f.proof['result']['original_pilot_window']['configuration_sha256'] == _digest(f.configuration)
    # Authority fields are attached only after independent real administrative
    # signing and ENABLE. This raw observation is deliberately inadmissible.
    assert 'enable_sha256' not in f.proof and 'delegation_sha256' not in f.proof
