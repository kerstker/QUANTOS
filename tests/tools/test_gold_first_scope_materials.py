"""First Scope uses original native BTC evidence without a Gold functional key.

The partition publication/installed BTC metadata and custody are isolated test
inputs; real original ledger, signed proposal, cash replay, bar archive and
definition checks are retained. Full installation is a separate integration.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from live.execution.demo_batch_risk import _digest
from tests.live.test_demo_market_partition import book as book, NOW
from tests.live.test_demo_market_partition_revalidation import fresh_observations
from tools import demo_operator_setup as setup

# The archive must use the reviewed current bar semantics. The old BTC source
# remains separately pinned below and its original records are never rebased.
PARENT = '6c57f740556fbec656d349fb9d2c2e6d06cc7083'
OLD_SOURCE = '4673bc0a785a67b13d35402dd801e7984c69f363'


class FrozenFirstScopeArchive(tuple):
    """Preserve the two-value fixture while exposing real files to cold readers."""
    def __new__(cls, definition, runtime, *, source, archive):
        value=super().__new__(cls, (definition, runtime))
        value.source=source;value.archive=archive
        return value


@pytest.fixture(scope='session')
def first_scope_archive(tmp_path_factory):
    from tools import perpetual_replay_research as replay, gold_bar_experiment
    from tools.gold_bar_definition import define
    from tools.gold_bar_review import review_request
    from tests.tools.test_gold_bar_cost_replay import inputs
    folder = tmp_path_factory.mktemp('first-scope-original-archive')
    source = folder/'source'
    repository = Path(__file__).resolve().parents[2]
    subprocess.run(['git', 'clone', '--no-local', '--no-checkout', str(repository), str(source)],
        check=True, capture_output=True)
    subprocess.run(['git', '-C', str(source), 'checkout', '--detach', PARENT], check=True, capture_output=True)
    # Copy committed source into its own checkout before archiving. Concurrent
    # edits in the shared development checkout cannot alter this identity.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(replay, 'ROOT', source)
        identity = replay.source_identity()
        args = inputs();args.update(quantity=Decimal('.002'), release_identity=identity)
        archive = folder/'archive';gold_bar_experiment.archive(args, archive);review_request(archive)
        definition = define(archive, candidate='channel', source_directory=source)
    runtime = dict(schema='GOLD_BAR_FRESH_EPISODE_INPUT_V1', source_commit=PARENT,
        trial_manifest_sha256=hashlib.sha256((archive/'manifest.json').read_bytes()).hexdigest(),
        archive_source_commit=PARENT, history_sha256='e'*64, candidate='channel', seconds=300)
    return FrozenFirstScopeArchive(definition, runtime, source=source, archive=archive)


@pytest.fixture
def original_issuer(tmp_path, monkeypatch):
    from live.execution import demo_issuer, demo_authority
    from live.execution.demo_issuer_store import IssuerStore
    from tests.live import test_demo_prepared_execution as prepared
    from tests.live.test_demo_issuer import policy_for
    store = IssuerStore(tmp_path/'original-btc-issuer.sqlite3', create=True)

    def signed(private, envelope, proposal):
        registry = demo_authority.load_registry_for_executor()
        policy = policy_for(proposal, registry);policy['executor_release_commit'] = OLD_SOURCE
        handoff = dict(schema='DEMO_EXECUTION_SESSION_PROPOSAL_V1', proposal=proposal,
            proposal_sha256=_digest(proposal), proposal_path=str(tmp_path/'native-proposal.json'),
            status='WAITING_INDEPENDENT_GRANT', submission_enabled=False)
        grant = demo_issuer.issue_submission(handoff, policy=policy, registry=registry,
            key=private, store=store, now_ms=NOW)
        store.fixture_private = private
        return json.dumps(grant['envelope']).encode()

    monkeypatch.setattr(prepared, 'signed', signed)
    try:yield store
    finally:store.close()


@pytest.fixture
def first_scope_inputs(original_issuer, book, first_scope_archive, tmp_path, monkeypatch):
    from live.execution import demo_market_partition as partition, demo_market_partition_runtime as runtime
    from live.execution import demo_authority
    from tests.live.test_demo_issuer import policy_for
    from services.research.demo_market_inputs import VOLATILITY_POLICY
    from tools.binance_demo_market_inputs import source_hash
    from live.execution.demo_gold_first_scope_history import history_anchor
    state, context, values, stores, events = book
    registry = deepcopy(values['registry']);root = stores['dispatch'].path.parent
    stop = (root/'kill-switch.sqlite3').read_bytes();stop_sha = hashlib.sha256(stop).hexdigest()
    candidate = partition.prepare(state=state, legacy_origin=values['legacy_origin'], gold_origin=values['gold_origin'],
        registry=registry, source_commit=PARENT, kill_switch_sha256=stop_sha, now_ms=values['now_ms'])
    record = dict(schema='DEMO_INSTALLED_MARKET_PARTITION_V1', source_commit=PARENT,
        candidate=candidate, candidate_sha256=_digest(candidate), legacy_registry=deepcopy(registry),
        operator_approval=dict(schema='DEMO_PARTITION_LOCAL_OPERATOR_APPROVAL_V1', candidate_sha256=_digest(candidate),
            operator_id='isolated-operator', approved_at_ms=NOW+130001, purpose='DEMO_MARKET_PARTITION_INSTALL_ONLY'),
        installed_at_ms=NOW+130001, revalidation_sha256='e'*64, installed=True,
        submission_enabled=False, economic_authority=False)
    activation = dict(schema='DEMO_MARKET_PARTITION_ACTIVATION_V1', source_commit=PARENT,
        installed_record_sha256=_digest(record), candidate_sha256=_digest(candidate), ready_result_sha256='f'*64,
        activated_at_ms=NOW+130001, installed=True, submission_enabled=False, economic_authority=False)
    for name, value in (('EXECUTOR_RECORD', record), ('ISSUER_RECORD', record), ('ACTIVATION', activation)):
        path = tmp_path/(name+'.json');path.write_bytes(setup.canonical(value));monkeypatch.setattr(runtime, name, path)
    monkeypatch.setattr(runtime, '_read_record', lambda path, **kwargs:path.read_bytes())
    monkeypatch.setattr(runtime, '_role_identity', lambda *args:None)
    monkeypatch.setattr(runtime, 'executing_source_commit', lambda:PARENT)
    monkeypatch.setattr(demo_authority, 'load_registry_for_executor', lambda:deepcopy(registry))
    origin = fresh_observations(values, NOW+140000)['gold_origin']
    review = runtime.first_gold_enrollment_review(state=state, origin=origin, as_of_ms=NOW+140000)
    proofs = [json.loads(path.read_bytes()) for path in (root/'evidence').glob('*.json')]
    proof = next(p for p in proofs if p.get('schema')=='DEMO_PRE_SUBMISSION_PROPOSAL_V1')
    policy = policy_for(proof, registry);policy['executor_release_commit'] = OLD_SOURCE
    snap = proof['risk']['input_snapshot']
    profile = dict(schema='DEMO_REQUEST_PROFILE_V1', environment='BINANCE_USDM_DEMO', symbol='BTCUSDC',
        market_source_sha256=source_hash(), volatility_policy=VOLATILITY_POLICY,
        native_limits=deepcopy(snap['native_limits']), strategy=deepcopy(snap['strategy']),
        mandate=deepcopy(proof['mandate']), attestations=deepcopy(proof['validation']),
        initial_day_pnl='0', initial_peak_equity='5000', minimum_plan_turnover=0)
    spec = setup.propose(origin=values['legacy_origin'], registry=registry, source_commit=OLD_SOURCE,
        now_ms=values['now_ms'], position_context=context)
    result = dict(schema='DEMO_OPERATOR_SETUP_RESULT_V1', status='INSTALLED_PENDING_CYCLE',
        source_commit=OLD_SOURCE, spec_sha256=_digest(spec), enrollment_sha256='a'*64, task_sha256='b'*64,
        orders_submitted=False, economic_authority=False)
    definition, bar_runtime = first_scope_archive
    return dict(review=review, origin=origin, registry=registry, policy=policy, profile=profile,
        original_setup=dict(spec=spec, result=result), issuer_history=history_anchor(original_issuer.snapshot()),
        definition=deepcopy(definition), runtime=deepcopy(bar_runtime),
        source_commit=PARENT, stop_sha256=stop_sha, now_ms=NOW+140000), stop, state, events


def assembled(first_scope_inputs):
    from tools import gold_first_scope_materials as tool
    args, stop, _, _ = first_scope_inputs
    request = tool.prepare_first_scope(**args)
    approval = dict(schema='DEMO_GOLD_FIRST_SCOPE_OPERATOR_V1', purpose='FIRST_GOLD_SCOPE_ONLY',
        request_sha256=request['request_sha256'], original_cycle_sha256=_digest(args['original_setup']['result']),
        operator_id='isolated-operator', approved_at_ms=args['now_ms'], duration_seconds=86400,
        installation_allowance_seconds=300, execution_approval_granted=False, economic_authority=False)
    values = {k:v for k,v in args.items() if k not in ('stop_sha256', 'source_commit')}
    values.update(request=request, approval=approval, private_key=Ed25519PrivateKey.generate(), stop_image=stop)
    manifest, files = tool.assemble(**values)
    return values, manifest, files


def test_native_first_scope_compiler_needs_no_functional_gold_authority(first_scope_inputs, monkeypatch):
    from live.execution import demo_gold_budget_scope as scope
    from live.execution.demo_issuer_policy import validate_policy
    args, stop, state, events = first_scope_inputs
    before = deepcopy(args), deepcopy(state), events.row_count()
    monkeypatch.setattr(setup, 'assemble', lambda **kwargs:pytest.fail('functional assembler reached'))
    values, manifest, files = assembled(first_scope_inputs)
    docs = {name:json.loads(raw) for name,raw in files.items() if name.endswith('.json')}
    assert manifest['schema']==scope.BUNDLE_SCHEMA_V2 and manifest['tasks']==[] and len(files)==10
    assert docs['policy.json']['schema']==scope.SCHEMA_V2
    assert docs['registry.json']['keys'][:-1]==args['registry']['keys']
    assert docs['original-policy.json']['scope']['symbol']=='BTCUSDC'
    assert docs['request.json']['original_setup']==args['original_setup']
    assert docs['profile.json']['symbol']=='XAUUSDT' and docs['profile.json']['initial_peak_equity']=='4200'
    assert docs['request.json']['original_cycle_review']['legacy_risk']['canonical_wallet']=='4999.6801'
    assert sum(map(Decimal, docs['request.json']['original_cycle_review']['legacy_risk']['capacity_consumed'].values()))==Decimal('239.8')
    assert files['kill-switch.sqlite3']==stop
    with pytest.raises(ValueError):validate_policy(docs['policy.json'])
    assert before==(args, state, events.row_count())
    assert values['approval']['execution_approval_granted'] is False


def test_request_preserves_real_old_source_and_explicit_new_declarations(first_scope_inputs):
    from tools import gold_first_scope_materials as tool
    args, _, _, _ = first_scope_inputs
    value = tool.prepare_first_scope(**args)
    assert value['original_source_commit']==OLD_SOURCE!=value['source_commit']==PARENT
    assert value['original_cycle_sha256']==_digest(args['original_setup']['result'])
    assert value['target_declarations']['scope']['symbol']=='XAUUSDT'
    assert value['target_declarations']['scope']['max_quantity_per_order']=='0.01'
    assert value['target_declarations']['initial_peak_equity']=='4200'
    assert value['proposed_budget']==dict(max_grants=3,max_orders=3,max_total_notional='750',max_cancel_grants=3,max_cancel_orders=3)
    assert value['stop_image_sha256']==args['stop_sha256']
    assert 'execution_constraints_sha256' not in json.dumps(value)


@pytest.mark.parametrize('damage', ['old_source', 'old_account', 'old_spec', 'old_result', 'old_policy',
    'old_profile', 'legacy_loss', 'legacy_capacity', 'gold_wallet', 'gold_context', 'review_authority',
    'expired_review', 'stop', 'runtime_source', 'runtime_extra', 'definition_hash', 'definition_quantity',
    'definition_economic', 'issuer_records_bool', 'issuer_empty', 'issuer_extra'])
def test_first_scope_rejects_missing_or_changed_original_inputs(first_scope_inputs, damage):
    from tools import gold_first_scope_materials as tool
    from services.execution.backtest_authority import exact_identity_hash
    args = deepcopy(first_scope_inputs[0])
    if damage=='old_source':args['policy']['executor_release_commit']='e'*40
    elif damage=='old_account':args['policy']['account_ref_sha256']='e'*64
    elif damage=='old_spec':args['original_setup']['spec']['symbol']='XAUUSDT'
    elif damage=='old_result':args['original_setup']['result']['spec_sha256']='e'*64
    elif damage=='old_policy':args['policy']['schema']='DEMO_GOLD_ISSUER_POLICY_V1'
    elif damage=='old_profile':args['profile']['native_limits']['daily_loss_limit']=10000
    elif damage=='legacy_loss':args['review']['legacy_risk']['canonical_wallet']='5000'
    elif damage=='legacy_capacity':args['review']['legacy_risk']['capacity_consumed']={}
    elif damage=='gold_wallet':args['review']['sealed_usdt_wallet']='5000'
    elif damage=='gold_context':args['review']['gold_position_context']={'quantity':'0'}
    elif damage=='review_authority':args['review']['approval_granted']=True
    elif damage=='expired_review':args['now_ms']+=30001
    elif damage=='stop':args['stop_sha256']='e'*64
    elif damage=='runtime_source':args['runtime']['source_commit']='e'*40
    elif damage=='runtime_extra':args['runtime']['command']='unsafe'
    elif damage=='definition_hash':args['definition']['definition_hash']='e'*64
    elif damage=='definition_quantity':
        args['definition']['size_and_timing']['quantity']='1'
        args['definition']['definition_hash']=exact_identity_hash(
            {k:v for k,v in args['definition'].items() if k!='definition_hash'}, name='gold_bar_research_definition')
    elif damage=='definition_economic':args['definition']['economic_eligible']=True
    elif damage=='issuer_records_bool':args['issuer_history']['records']=True
    elif damage=='issuer_empty':args['issuer_history']['records']=0
    else:args['issuer_history']['replacement_ledger']=True
    with pytest.raises(ValueError):tool.prepare_first_scope(**args)


@pytest.mark.parametrize('damage', ['old_purpose', 'old_schema', 'wrong_cycle', 'auto_operator', 'early_operator',
    'expired_request', 'bool_duration', 'long_install', 'execution', 'budget', 'new_scope', 'issuer', 'old_setup',
    'new_origin', 'stop_image'])
def test_confirmation_and_compilation_never_rebase_or_expand_request(first_scope_inputs, damage):
    from tools import gold_first_scope_materials as tool
    values, _, _ = assembled(first_scope_inputs)
    values = {k:(v if k=='private_key' else deepcopy(v)) for k,v in values.items()}
    if damage=='old_purpose':values['approval']['purpose']='DEMO_GOLD_FIRST_DAY_FUNCTIONAL_PILOT_SCOPE_ONLY'
    elif damage=='old_schema':values['approval']['schema']='DEMO_GOLD_BUDGET_SCOPE_OPERATOR_V1'
    elif damage=='wrong_cycle':values['approval']['original_cycle_sha256']='e'*64
    elif damage=='auto_operator':values['approval']['operator_id']='AUTO'
    elif damage=='early_operator':values['approval']['approved_at_ms']-=1
    elif damage=='expired_request':values['now_ms']=values['request']['valid_until_ms']
    elif damage=='bool_duration':values['approval']['duration_seconds']=True
    elif damage=='long_install':values['approval']['installation_allowance_seconds']=3600
    elif damage=='execution':values['approval']['execution_approval_granted']=True
    elif damage=='budget':values['request']['proposed_budget']['max_grants']+=1
    elif damage=='new_scope':values['request']['target_declarations']['scope']['max_quantity_per_order']='1'
    elif damage=='issuer':values['issuer_history']['head_sha256']='e'*64
    elif damage=='old_setup':values['original_setup']['result']['enrollment_sha256']='e'*64
    elif damage=='new_origin':values['origin']['completed_at_ms']+=1
    else:values['stop_image']+=b'\x00'*4096
    with pytest.raises(ValueError):tool.assemble(**values)


def test_original_key_cannot_be_reused_for_new_scope(first_scope_inputs, original_issuer):
    from tools import gold_first_scope_materials as tool
    values, _, _ = assembled(first_scope_inputs)
    with pytest.raises(ValueError, match='GOLD_SCOPE_INDEPENDENT_KEY_REQUIRED'):
        tool.assemble(**(values | {'private_key':original_issuer.fixture_private}))


def test_all_old_keys_and_policy_must_have_expired_at_confirmation(first_scope_inputs):
    from tools import gold_first_scope_materials as tool
    # Directly inspect the pre-signing gate with a separate earlier key, not
    # merely a changed registry digest that would fail before reaching expiry.
    args = first_scope_inputs[0];at=args['now_ms']
    for target in ('earlier_key', 'policy'):
        registry=deepcopy(args['registry']);policy=deepcopy(args['policy'])
        if target=='earlier_key':registry['keys'].insert(0, dict(registry['keys'][0], valid_until_ms=at+1))
        else:policy['valid_until_ms']=at+1
        with pytest.raises(ValueError, match='FIRST_SCOPE_OLD_AUTHORITY_STILL_VALID'):
            tool._old_expired(registry, policy, at)


def test_original_bundle_reader_validates_real_v2_material_and_retained_window(first_scope_inputs):
    from tools import demo_operating_bundle as bundle
    values, manifest, files = assembled(first_scope_inputs)
    read=dict(manifest=manifest, files=files, existing_registry=values['registry'], source_commit=PARENT,
        account_ref=values['policy']['account_ref_sha256'], now_ms=values['now_ms']+90000)
    native=bundle.validate_materials(**read)
    assert native['scope_only'] is True and native['tasks']==[]
    assert native['policy']['scope_enrollment']['original_issuer_history']==values['issuer_history']
    assert native['policy']['scope_enrollment']['confirmed_at_ms']==values['approval']['approved_at_ms']
    assert native['profile']['initial_peak_equity']=='4200'


@pytest.mark.parametrize('damage', ['v1_manifest', 'v1_scope', 'task', 'extra_file', 'profile',
    'original_setup', 'issuer_anchor', 'private_key', 'stop_image'])
def test_full_bundle_rejects_cross_version_or_rehashed_substitution(first_scope_inputs, damage):
    from tools import demo_operating_bundle as bundle
    values, manifest, files = assembled(first_scope_inputs)
    manifest=deepcopy(manifest);files=dict(files)
    if damage=='v1_manifest':manifest['schema']='DEMO_GOLD_BUDGET_SCOPE_BUNDLE_V1'
    elif damage=='task':manifest['tasks']=['e'*64]
    elif damage=='extra_file':files['unapproved.json']=b'{}'
    elif damage=='stop_image':files['kill-switch.sqlite3']+=b'\x00'*4096
    else:
        name={'v1_scope':'policy.json','profile':'profile.json','original_setup':'request.json',
            'issuer_anchor':'request.json','private_key':'signing-key.json'}[damage]
        value=json.loads(files[name])
        if damage=='v1_scope':value['schema']='DEMO_GOLD_STRATEGY_BUDGET_SCOPE_V1'
        elif damage=='profile':value['initial_peak_equity']='5000'
        elif damage=='original_setup':value['original_setup']['result']['source_commit']=PARENT
        elif damage=='issuer_anchor':value['original_issuer_history']['history_sha256']='e'*64
        else:value['private_key_base64url']=setup.encoded(Ed25519PrivateKey.generate().private_bytes_raw())
        files[name]=bundle.canonical(value)
    manifest['files']={name:bundle.sha(raw) for name,raw in files.items()}
    with pytest.raises(ValueError):bundle.validate_materials(manifest=manifest, files=files,
        existing_registry=values['registry'], source_commit=PARENT,
        account_ref=values['policy']['account_ref_sha256'], now_ms=values['now_ms'])
