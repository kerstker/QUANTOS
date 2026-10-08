"""Explicit new Gold scope is bound to a sealed original book, never a rename."""
from copy import deepcopy
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.live.test_demo_market_partition import book as book, NOW
from tests.live.test_demo_market_partition_runtime import installed as installed, shift
from tests.live.test_gold_first_enrollment_review import first as first
from tests.tools.test_demo_operator_setup import stop_bytes
from tools import demo_operator_setup as setup


def test_reviewed_first_market_can_prepare_an_explicit_unsigned_scope(first):
    at = NOW+140000
    report = first['runtime'].first_gold_enrollment_review(state=first['state'],
        origin=first['origin'], as_of_ms=at)
    proposal = setup.propose(origin=first['origin'], registry=first['registry'], source_commit='c'*40,
        now_ms=at, symbol='XAUUSDT', first_gold_review=report)
    assert proposal['first_gold_enrollment'] == report
    assert setup.approval_purpose(proposal) == 'DEMO_FIRST_GOLD_FUNCTIONAL_TEST_ONLY'


@pytest.fixture
def materials(first, tmp_path):
    value = first
    at = NOW + 140000
    review = value['runtime'].first_gold_enrollment_review(state=value['state'],
        origin=value['origin'], as_of_ms=at)
    spec = setup.propose(origin=value['origin'], registry=value['registry'], source_commit='c'*40,
        now_ms=at, symbol='XAUUSDT', first_gold_review=review)
    approval = dict(schema='DEMO_OPERATOR_ENROLLMENT_V1', spec_sha256=setup.digest(spec),
        operator_id='isolated-operator', approved_at_ms=at,
        purpose='DEMO_FIRST_GOLD_FUNCTIONAL_TEST_ONLY')
    return value, review, spec, approval, stop_bytes(tmp_path), Ed25519PrivateKey.generate()


def assemble(parts, **changes):
    value, review, spec, approval, stop, private = parts
    args = dict(spec=spec, approval=approval, origin=value['origin'], registry=value['registry'],
        private_key=private, stop_image=stop, now_ms=NOW+140000, first_gold_review=review)
    args.update(changes)
    return setup.assemble(**args)


def test_new_scope_uses_original_assembler_and_preserves_old_registry_and_book(materials):
    value, review, spec, approval, stop, _ = materials
    before = deepcopy(value['state']), value['events'].row_count(), deepcopy(value['registry'])
    assert spec['first_gold_enrollment'] == review
    assert spec['symbol'] == 'XAUUSDT' and spec['window_seconds'] == 3600
    assert spec['orders_submitted'] is False and spec['economic_authority'] is False
    assert 'private_key' not in json.dumps(spec)
    manifest, files = assemble(materials)
    from tools.demo_operating_bundle import validate_materials
    result = validate_materials(manifest=manifest, files=files, existing_registry=value['registry'],
        source_commit='c'*40, account_ref=spec['account_ref_sha256'], now_ms=NOW+140000)
    assert result['registry']['keys'][:-1] == value['registry']['keys']
    assert result['registry']['keys'][-1]['valid_from_ms'] == approval['approved_at_ms']
    assert result['registry']['keys'][-1]['valid_until_ms'] == approval['approved_at_ms'] + 3600000
    assert result['policy']['schema'] == 'DEMO_GOLD_ISSUER_POLICY_V1'
    assert result['profile']['symbol'] == 'XAUUSDT'
    assert result['profile']['initial_peak_equity'] == '4200'
    assert result['profile']['initial_day_pnl'] == '0'
    assert review['gold_position_context'] is None
    assert review['legacy_risk']['canonical_wallet'] == '4999.6801'
    assert files['kill-switch.sqlite3'] == stop
    assert before == (value['state'], value['events'].row_count(), value['registry'])


def test_fresh_observation_can_change_time_without_changing_the_approved_cut(materials):
    value, review, _, _, _, _ = materials
    origin = shift(value['origin'], 1000)
    fresh = value['runtime'].first_gold_enrollment_review(state=value['state'], origin=origin,
        as_of_ms=NOW+141000)
    assert fresh['origin_sha256'] != review['origin_sha256']
    assemble(materials, origin=origin, now_ms=NOW+141000, first_gold_review=fresh)


@pytest.mark.parametrize('damage', ['missing_review', 'expired', 'old_purpose', 'early_approval',
    'legacy_loss', 'legacy_peak', 'legacy_capacity', 'wallet', 'source', 'partition',
    'candidate', 'authority', 'gold_context', 'active_old_key', 'position_context'])
def test_assembly_rejects_unreviewed_or_changed_scope_before_signing_materials(materials, damage):
    value, review, spec, approval, _, _ = materials
    changed = deepcopy(review);args = {'first_gold_review': changed}
    if damage == 'missing_review': args['first_gold_review'] = None
    elif damage == 'expired': args['now_ms'] = NOW+180000
    elif damage in ('old_purpose', 'early_approval'):
        altered = deepcopy(approval)
        if damage == 'old_purpose': altered['purpose'] = 'DEMO_GOLD_FUNCTIONAL_TEST_ONLY'
        else: altered['approved_at_ms'] -= 1
        args['approval'] = altered
    elif damage.startswith('legacy_'):
        key = {'legacy_loss': 'initial_day_pnl', 'legacy_peak': 'peak_equity_floor',
            'legacy_capacity': 'capacity_consumed'}[damage]
        changed['legacy_risk'][key] = {} if key == 'capacity_consumed' else '1'
    elif damage == 'wallet': changed['sealed_usdt_wallet'] = '4999.6801'
    elif damage == 'source': changed['source_commit'] = 'd'*40
    elif damage == 'partition': changed['partition_binding']['installed_record_sha256'] = 'e'*64
    elif damage == 'candidate': changed['original_candidate']['binding']['gold_initial_wallet'] = '4999.6801'
    elif damage == 'authority': changed['approval_granted'] = True
    elif damage == 'gold_context': changed['gold_position_context'] = {'quantity': '0'}
    elif damage == 'active_old_key':
        registry = deepcopy(value['registry']);registry['keys'][0]['valid_until_ms'] = NOW+180000
        args['registry'] = registry
    else: args['position_context'] = value['context']
    with pytest.raises(ValueError): assemble(materials, **args)


def test_ordinary_proposal_and_enrollment_still_reject_nonempty_registry_without_review(materials):
    value, _, spec, _, _, _ = materials
    with pytest.raises(ValueError, match='SETUP_FIRST_ENROLLMENT_ONLY'):
        setup.propose(origin=value['origin'], registry=value['registry'], source_commit='c'*40,
            now_ms=NOW+140000, symbol='XAUUSDT')
    unbound = deepcopy(spec);unbound.pop('first_gold_enrollment')
    approval = dict(schema='DEMO_OPERATOR_ENROLLMENT_V1', spec_sha256=setup.digest(unbound),
        operator_id='isolated-operator', approved_at_ms=NOW+140000,
        purpose='DEMO_GOLD_FUNCTIONAL_TEST_ONLY')
    with pytest.raises(ValueError): assemble(materials, spec=unbound, approval=approval)


def test_assembly_runs_original_risk_and_independent_issuer_without_dispatch(materials, tmp_path, monkeypatch):
    from live.execution import demo_authority, demo_request_inputs, demo_issuer
    from live.execution.demo_execution_session import ExecutionSession
    from live.execution.demo_issuer_store import IssuerStore
    from quantos.contracts.killswitch import KillSwitchState
    value, _, _, _, _, private = materials
    manifest, files = assemble(materials)
    task = json.loads(files[manifest['tasks'][0]+'.json'])
    policy = json.loads(files['policy.json']);registry = json.loads(files['registry.json'])
    value['registry'] = registry
    monkeypatch.setattr(demo_authority, 'load_registry_for_executor', lambda: deepcopy(registry))
    from tools import binance_demo_market_inputs
    from tests.tools.test_binance_demo_market_inputs import material
    at=NOW+139990;data=material();data['ticker'].update(symbol='XAUUSDT',
        openTime=at-86400000,closeTime=at)
    delta=at//60000*60000-1-data['klines'][-1][6]
    for row in data['klines']:
        row[0]+=delta;row[6]+=delta
    ticks=iter(range(at,at+100))
    def get(kind,**kwargs):
        body={'serverTime':at+(6 if kind=='clock_after' else 0)} if kind.startswith('clock') else data[kind]
        return json.dumps(body,separators=(',',':')).encode()
    market=binance_demo_market_inputs.collect(symbol='XAUUSDT',get=get,clock_ms=lambda:next(ticks))
    stores=value['stores']
    before = deepcopy(value['state']), value['events'].row_count()
    built=demo_request_inputs.build(orders=task['orders'], origin=value['origin'], market_inputs=market,
        profile=task['profile'], issuer_policy=policy, kill_state=KillSwitchState(),
        dispatch=stores['dispatch'], recovery=stores['recovery'], oms=stores['oms'], now_ms=NOW+140000)
    session=ExecutionSession(dispatch=stores['dispatch'], recovery=stores['recovery'], oms=stores['oms'],
        evidence_directory=tmp_path/'unsigned-proposals')
    handoff=session.propose(setup.canonical(built['request']), now_ms=NOW+140000)
    log=IssuerStore(tmp_path/'isolated-issuer.sqlite3', create=True)
    try:
        grant=demo_issuer.issue_submission(handoff, policy=policy, registry=registry, key=private,
            store=log, now_ms=NOW+140001)
    finally: log.close()
    from services.validation import demo_submission
    proof=demo_submission.verify(grant['envelope'],registry=registry,
        expected_context=handoff['proposal']['context'],orders=task['orders'],now_ms=NOW+140001)
    assert proof['authority_id']==registry['keys'][-1]['authority_id']
    assert grant['proposal_sha256'] == handoff['proposal_sha256']
    assert handoff['proposal']['state']['reservations'] == value['state']['reservations']
    assert 'position_context' not in handoff['proposal']['state']
    assert before == (value['state'], value['events'].row_count())


@pytest.mark.parametrize('damage', ['wrong_schema','wrong_source','changed_wallet',
    'changed_registry','permission','extra_field'])
def test_stored_unsigned_spec_cannot_change_its_review_binding(materials,damage):
    _, _, spec, _, _, _=materials
    spec=deepcopy(spec);review=spec['first_gold_enrollment']
    if damage=='wrong_schema':spec['schema']=setup.SCHEMA;spec['symbol']='BTCUSDC'
    elif damage=='wrong_source':review['source_commit']='d'*40
    elif damage=='changed_wallet':review['sealed_usdt_wallet']='5000'
    elif damage=='changed_registry':review['registry_sha256']='e'*64
    elif damage=='permission':review['submission_enabled']=True
    else:review['new_grant']=True
    if damage=='changed_wallet':
        approval=dict(schema='DEMO_OPERATOR_ENROLLMENT_V1',spec_sha256=setup.digest(spec),
            operator_id='isolated-operator',approved_at_ms=NOW+140000,
            purpose='DEMO_FIRST_GOLD_FUNCTIONAL_TEST_ONLY')
        with pytest.raises(ValueError):assemble(materials,spec=spec,approval=approval)
    else:
        with pytest.raises(ValueError):setup.spec_check(spec)
