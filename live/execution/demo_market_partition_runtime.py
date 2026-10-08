"""Consume protected market cuts inside the original execution boundaries.

An installed cut routes the original V2 replay; it is never an order grant or
economic authority. The entrypoints still verify their accepted immutable
source, private role, independent grant, current stop and actual risk state.
"""
from decimal import Decimal
from pathlib import Path
import os
import re
import stat

from live.execution import demo_authority
from live.execution.demo_batch_risk import _digest
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.validation.demo_submission import validate_registry,registry_sha256
from services.execution.perpetual_ledger_v2 import ledger_decimal_context

EXECUTOR_RECORD=Path('/etc/quantos/demo-submission/market-partition.json')
ISSUER_RECORD=Path('/etc/quantos/demo-issuer/market-partition.json')
ACTIVATION=Path('/etc/quantos/demo-market-partition-activation.json')
_CORE={'reservations','recovery','oms','wire'}


class MarketPartitionRejected(ValueError):
    pass


def _require(condition,reason):
    if not condition:raise MarketPartitionRejected(reason)


def executing_source_commit():
    """Bind the cut to the actual immutable path already checked by its entry."""
    root=Path(__file__).resolve().parents[2]
    _require(root.parent==Path('/opt/quantos/releases')
        and re.fullmatch('[a-f0-9]{40}',root.name), 'PARTITION_EXECUTING_SOURCE')
    for path in reversed((root,*root.parents)):
        demo_authority._require_protected(path.lstat(),directory=True)
    return root.name


def _role_identity(role,registry):
    _require(role in ('executor','issuer') and os.geteuid()==registry[role+'_uid'],
        'PARTITION_PRIVATE_ROLE_REQUIRED')


def _read_record(path,*,gid,public=False):
    def custody(info):
        _require(info.st_gid==gid and stat.S_IMODE(info.st_mode)==(0o644 if public else 0o640),
            'PARTITION_RECORD_READERS')
    return demo_authority._read_root_owned_json(path,file_check=custody)


def _exists(path):
    try:path.lstat();return True
    except FileNotFoundError:return False
    except OSError:raise MarketPartitionRejected('PARTITION_RECORD_UNAVAILABLE') from None


def _hash(value):
    _require(type(value) is str and re.fullmatch('[a-f0-9]{64}',value), 'PARTITION_RECORD_DIGEST')


def _load(*,role,registry=None,as_of_ms):
    try:
        _require(role in ('executor','issuer') and type(as_of_ms) is int and as_of_ms>0,'PARTITION_RUNTIME_ARGUMENTS')
        path=EXECUTOR_RECORD if role=='executor' else ISSUER_RECORD
        if not _exists(path):
            _require(not _exists(ACTIVATION),'PARTITION_LOCAL_MIRROR_MISSING')
            if role=='issuer':
                _require(not _exists(path.parent/'market-partition-intent.json'), 'PARTITION_INSTALL_INCOMPLETE')
            return None
        _require(_exists(ACTIVATION),'PARTITION_INSTALL_INCOMPLETE')
        registry=demo_authority.load_registry_for_executor() if registry is None else registry
        validate_registry(registry);_role_identity(role,registry)
        record=load_json_without_duplicate_keys(_read_record(path,gid=registry[role+'_uid']))
        activation=load_json_without_duplicate_keys(_read_record(ACTIVATION,gid=0,public=True))
        return validate_installed_records(record,activation,registry=registry,
            source_commit=executing_source_commit(),as_of_ms=as_of_ms)
    except MarketPartitionRejected:raise
    except Exception:raise MarketPartitionRejected('PARTITION_INSTALLED_RECORD_UNAVAILABLE') from None


def validate_installed_records(record, activation, *, registry, source_commit, as_of_ms):
    """Original partition content gate, without fixed-path reads or role powers.

    The caller independently proves the record/activation custody and original
    source identity. Historical validation does not grant runtime permission.
    """
    from live.execution.demo_market_partition import _candidate
    try:
        _require(type(as_of_ms) is int and as_of_ms > 0, 'PARTITION_RUNTIME_ARGUMENTS')
        validate_registry(registry)
        fields=set('schema source_commit candidate candidate_sha256 legacy_registry operator_approval '
            'installed_at_ms revalidation_sha256 installed submission_enabled economic_authority'.split())
        _require(type(record) is dict and set(record)==fields
            and record['schema']=='DEMO_INSTALLED_MARKET_PARTITION_V1'
            and record['installed'] is True and record['submission_enabled'] is False
            and record['economic_authority'] is False,'PARTITION_INSTALLED_RECORD_FIELDS')
        _require(record['source_commit']==source_commit,'PARTITION_INSTALLED_SOURCE_CHANGED')
        cut=_candidate(record['candidate'],as_of_ms)
        _require(cut['source_commit']==record['source_commit']
            and record['candidate_sha256']==_digest(record['candidate'])
            and type(record['installed_at_ms']) is int
            and cut['recorded_at_ms']<=record['installed_at_ms']<=as_of_ms,'PARTITION_INSTALL_BINDING')
        _hash(record['revalidation_sha256'])
        old=validate_registry(record['legacy_registry'])
        _require(registry_sha256(old)==cut['registry_sha256'] and old['keys']
            and all(key['valid_until_ms']<=cut['recorded_at_ms'] for key in old['keys'])
            and {k:v for k,v in registry.items() if k!='keys'}=={k:v for k,v in old.items() if k!='keys'}
            and registry['keys'][:len(old['keys'])]==old['keys'], 'PARTITION_LEGACY_REGISTRY_CHANGED')
        _require(all(key['valid_from_ms']>=record['installed_at_ms'] for key in registry['keys'][len(old['keys']):]),
            'PARTITION_NEW_KEY_PRECEDES_INSTALLATION')
        approval=record['operator_approval']
        _require(type(approval) is dict and set(approval)=={'schema','candidate_sha256','operator_id','approved_at_ms','purpose'}
            and approval['schema']=='DEMO_PARTITION_LOCAL_OPERATOR_APPROVAL_V1'
            and approval['purpose']=='DEMO_MARKET_PARTITION_INSTALL_ONLY'
            and approval['candidate_sha256']==record['candidate_sha256']
            and type(approval['operator_id']) is str and re.fullmatch('[a-zA-Z0-9_-]{1,64}',approval['operator_id'])
            and type(approval['approved_at_ms']) is int
            and cut['recorded_at_ms']<=approval['approved_at_ms']<=record['installed_at_ms'], 'PARTITION_OPERATOR_BINDING')
        _require(type(activation) is dict and set(activation)==set('schema source_commit installed_record_sha256 '
            'candidate_sha256 ready_result_sha256 activated_at_ms installed submission_enabled economic_authority'.split())
            and activation['schema']=='DEMO_MARKET_PARTITION_ACTIVATION_V1'
            and activation['source_commit']==record['source_commit']
            and activation['installed_record_sha256']==_digest(record)
            and activation['candidate_sha256']==record['candidate_sha256']
            and type(activation['activated_at_ms']) is int
            and record['installed_at_ms']<=activation['activated_at_ms']<=as_of_ms
            and activation['installed'] is True and activation['submission_enabled'] is False
            and activation['economic_authority'] is False,'PARTITION_ACTIVATION_BINDING')
        _hash(activation['ready_result_sha256'])
        return record,registry
    except MarketPartitionRejected:raise
    except Exception:raise MarketPartitionRejected('PARTITION_INSTALLED_RECORD_UNAVAILABLE') from None


def _descriptor(record):
    return dict(schema='DEMO_MARKET_PARTITION_RUNTIME_BINDING_V1',source_commit=record['source_commit'],
        installed_record_sha256=_digest(record),candidate_sha256=record['candidate_sha256'],
        binding_sha256=record['candidate']['binding_sha256'])


def _core(state):
    _require(type(state) is dict and _CORE<=set(state)
        and set(state)<=_CORE|{'position_context','pilot_control','market_partition'},'PARTITION_PROOF_STATE_FIELDS')
    return {key:state[key] for key in _CORE}


def _new_key(record,registry,at,policy=None):
    keys=registry['keys'][len(record['legacy_registry']['keys']):]
    active=[key for key in keys if key['status']=='ACTIVE' and key['valid_from_ms']<=at<key['valid_until_ms']]
    _require(active,'PARTITION_NEW_GOLD_AUTHORITY_REQUIRED')
    if policy is not None:
        _require(policy['executor_release_commit']==record['source_commit']
            and any((key['authority_id'],key['key_id'])==(policy['authority_id'],policy['key_id']) for key in active),
            'PARTITION_SELECTED_KEY_NOT_NEW_AUTHORITY')


def _contexts(state,record,at):
    from live.execution.demo_market_partition import rebuild_contexts
    from live.execution.demo_position_context import _load_context_unpartitioned
    return rebuild_contexts(state=_core(state),candidate=record['candidate'],as_of_ms=at,
        replayer=_load_context_unpartitioned)


@ledger_decimal_context()
def _inventory(origin,record,at,position):
    from services.research.demo_account_baseline import build_baseline
    from services.research.demo_execution_origin import account_capture,assess
    from live.execution.demo_position_context import validate_origin
    try:
        cut=record['candidate']['binding'];report=assess(origin,as_of_ms=at)
        _require(origin['symbol']==cut['target_symbol']
            and origin['account_ref_sha256']==cut['account_ref_sha256'],'PARTITION_CURRENT_ORIGIN_SCOPE')
        baseline=build_baseline(account_capture(origin),as_of_ms=at)
        _require(baseline['inventory_status']=='STABLE_OBSERVATIONS'
            and all(row['symbol']==cut['target_symbol'] for row in baseline['positions']),
            'PARTITION_UNOWNED_ACCOUNT_POSITION')
        if position is None:
            _require(report['status']=='OBSERVED_FLAT_QUOTE' and report['wallet']==cut['gold_initial_wallet'],
                'PARTITION_FIRST_GOLD_WALLET_CHANGED')
        else:validate_origin(origin,position,as_of_ms=at)
        fields=('asset','observed_wallet','observed_cross_wallet','observed_available',
            'observed_margin_balance','observed_unrealized','account_identity_residual')
        inventory=[]
        for row in baseline['assets']:
            fact={key:row[key] for key in fields}
            if row['asset']==cut['target_collateral']:
                for key in fields[1:]:fact[key]=cut['gold_initial_wallet'] if key not in ('observed_unrealized','account_identity_residual') else '0'
            else:
                wallet=Decimal(row['observed_wallet'])
                _require(wallet>=0 and all(Decimal(row[key])==wallet for key in
                    ('observed_cross_wallet','observed_available','observed_margin_balance'))
                    and Decimal(row['observed_unrealized'])==Decimal(row['account_identity_residual'])==0,
                    'PARTITION_OTHER_COLLATERAL_NOT_FREE_AND_FLAT')
            inventory.append(fact)
        _require(_digest(sorted(inventory,key=lambda row:row['asset']))==record['candidate']['review']['account_inventory_sha256'],
            'PARTITION_ORIGINAL_INVENTORY_CHANGED')
    except MarketPartitionRejected:raise
    except Exception:raise MarketPartitionRejected('PARTITION_ACCOUNT_OBSERVATION_UNAVAILABLE') from None


def installed_context(*,state,symbol,account_ref,as_of_ms):
    loaded=_load(role='executor',as_of_ms=as_of_ms)
    if loaded is None:return False,None
    record,_=loaded;cut=record['candidate']['binding']
    _require(account_ref==cut['account_ref_sha256'] and symbol in (cut['legacy_symbol'],cut['target_symbol']),
        'PARTITION_CONTEXT_SCOPE')
    contexts=_contexts(state,record,as_of_ms)
    return True,contexts['legacy'] if symbol==cut['legacy_symbol'] else contexts['target']


_FIRST_GOLD_REVIEW_FIELDS=set('schema status source_commit account_ref_sha256 registry_sha256 '
    'partition_binding original_candidate original_cut_at_ms reviewed_at_ms valid_until_ms origin_sha256 '
    'legacy_state_sha256 legacy_context_sha256 legacy_risk observed_usdt_wallet sealed_usdt_wallet '
    'gold_position_context partition_installed gold_enrollment_installed collateral_conversion '
    'approval_granted source_verified submission_enabled economic_authority'.split())


def validate_first_gold_review(report,*,origin,registry,source_commit,as_of_ms):
    """Check a diagnostic response from the existing source/cycle-bound pipe."""
    from services.research.demo_execution_origin import assess
    from live.execution.demo_market_partition import _candidate
    validate_registry(registry)
    _require(type(as_of_ms) is int and as_of_ms>0,'PARTITION_FIRST_REVIEW_TIME')
    _require(type(report) is dict and set(report)==_FIRST_GOLD_REVIEW_FIELDS,
        'PARTITION_FIRST_REVIEW_FIELDS')
    _require(report['schema']=='DEMO_FIRST_GOLD_ENROLLMENT_REVIEW_V1'
        and report['status']=='FIRST_GOLD_ENROLLMENT_REVIEW_REQUIRED'
        and report['source_commit']==source_commit and re.fullmatch('[a-f0-9]{40}',source_commit)
        and origin['symbol']=='XAUUSDT'
        and report['account_ref_sha256']==origin['account_ref_sha256']
        and report['registry_sha256']==registry_sha256(registry)
        and report['origin_sha256']==_digest(origin),'PARTITION_FIRST_REVIEW_BINDING')
    binding=report['partition_binding']
    _require(type(binding) is dict and set(binding)==set('schema source_commit installed_record_sha256 '
        'candidate_sha256 binding_sha256'.split())
        and binding['schema']=='DEMO_MARKET_PARTITION_RUNTIME_BINDING_V1'
        and binding['source_commit']==source_commit,'PARTITION_FIRST_REVIEW_DESCRIPTOR')
    for key in ('installed_record_sha256','candidate_sha256','binding_sha256'):_hash(binding[key])
    for key in ('account_ref_sha256','registry_sha256','origin_sha256','legacy_state_sha256','legacy_context_sha256'):_hash(report[key])
    original=report['original_candidate'];sealed=_candidate(original,as_of_ms)
    _require(binding['candidate_sha256']==_digest(original)
        and binding['binding_sha256']==original['binding_sha256']
        and sealed['source_commit']==source_commit
        and sealed['account_ref_sha256']==report['account_ref_sha256']
        and sealed['registry_sha256']==report['registry_sha256']
        and sealed['recorded_at_ms']==report['original_cut_at_ms']
        and sealed['legacy_state_sha256']==report['legacy_state_sha256']
        and sealed['legacy_context_sha256']==report['legacy_context_sha256']
        and sealed['gold_initial_wallet']==report['sealed_usdt_wallet'],
        'PARTITION_FIRST_REVIEW_ORIGINAL_BINDING')
    cut,reviewed,deadline=(report[key] for key in ('original_cut_at_ms','reviewed_at_ms','valid_until_ms'))
    _require(all(type(value) is int and value>0 for value in (cut,reviewed,deadline))
        and cut<=reviewed<=as_of_ms<=deadline<=reviewed+30000
        and origin['observed_at_ms']//86400000==reviewed//86400000==as_of_ms//86400000,
        'PARTITION_FIRST_REVIEW_EXPIRED')
    observed=assess(origin,as_of_ms=as_of_ms)
    _require(observed['status']=='OBSERVED_FLAT_QUOTE'
        and report['observed_usdt_wallet']==report['sealed_usdt_wallet']==observed['wallet'],
        'PARTITION_FIRST_REVIEW_WALLET')
    risk=report['legacy_risk'];old=original['review']['legacy_book']
    risk_fields=set('canonical_wallet initial_equity initial_day_pnl initial_plan_turnover '
        'peak_equity_floor capacity_consumed day_id cash_flow_policy quantity'.split())
    risk_fields|={key for key in ('day_checkpoint_sha256','day_open_equity') if key in old}
    _require(type(risk) is dict and set(risk)==risk_fields
        and all(risk[key]==old[key] for key in risk_fields),'PARTITION_FIRST_REVIEW_LEGACY_RISK')
    _require(risk['quantity']=='0'
        and report['gold_position_context'] is None and report['partition_installed'] is True
        and report['collateral_conversion']=='NOT_PERFORMED'
        and all(report[key] is False for key in ('gold_enrollment_installed','approval_granted',
            'source_verified','submission_enabled','economic_authority')),'PARTITION_FIRST_REVIEW_NOT_AUTHORITY')
    return report


@ledger_decimal_context()
def first_gold_enrollment_review(*,state,origin,as_of_ms):
    """Review the first target registration without requiring or creating a key."""
    loaded=_load(role='executor',as_of_ms=as_of_ms)
    _require(loaded is not None,'PARTITION_FIRST_REVIEW_REQUIRES_INSTALLED_CUT')
    record,registry=loaded;cut=record['candidate']['binding']
    _require(registry==record['legacy_registry'],'PARTITION_GOLD_ALREADY_REGISTERED')
    contexts=_contexts(state,record,as_of_ms)
    _require(contexts['target'] is None and contexts['full_state_sha256']==cut['legacy_state_sha256'],
        'PARTITION_FIRST_REVIEW_NEW_EXECUTION')
    _inventory(origin,record,as_of_ms,None)
    legacy=contexts['legacy']
    risk={key:legacy[key] for key in ('canonical_wallet','initial_equity','initial_day_pnl',
        'initial_plan_turnover','peak_equity_floor','capacity_consumed','day_id','cash_flow_policy','quantity')}
    for key in ('day_checkpoint_sha256','day_open_equity'):
        if key in legacy:risk[key]=legacy[key]
    data={row['kind']:row['data'] for row in origin['reads']}
    deadline=min(origin['observed_at_ms']+30000,data['book']['time']+30000,
        data['mark']['time']+30000,data['mark']['nextFundingTime']-1)
    report=dict(schema='DEMO_FIRST_GOLD_ENROLLMENT_REVIEW_V1',status='FIRST_GOLD_ENROLLMENT_REVIEW_REQUIRED',
        source_commit=record['source_commit'],account_ref_sha256=cut['account_ref_sha256'],
        registry_sha256=registry_sha256(registry),partition_binding=_descriptor(record),
        original_candidate=record['candidate'],
        original_cut_at_ms=cut['recorded_at_ms'],reviewed_at_ms=as_of_ms,valid_until_ms=deadline,
        origin_sha256=_digest(origin),legacy_state_sha256=contexts['full_state_sha256'],
        legacy_context_sha256=_digest(legacy),legacy_risk=risk,observed_usdt_wallet=cut['gold_initial_wallet'],
        sealed_usdt_wallet=cut['gold_initial_wallet'],gold_position_context=None,
        partition_installed=True,gold_enrollment_installed=False,collateral_conversion='NOT_PERFORMED',
        approval_granted=False,source_verified=False,submission_enabled=False,economic_authority=False)
    validate_first_gold_review(report,origin=origin,registry=registry,
        source_commit=record['source_commit'],as_of_ms=as_of_ms)
    return report


_FIRST_ACTIVATION_FIELDS=set('schema status source_commit account_ref_sha256 legacy_registry_sha256 '
    'expected_registry_sha256 observed_registry_sha256 registry_state partition_binding original_cut_at_ms '
    'legacy_state_sha256 legacy_context_sha256 legacy_risk reviewed_at_ms valid_until_ms origin_sha256 '
    'observed_usdt_wallet sealed_usdt_wallet gold_position_context collateral_conversion '
    'configuration_activation_verified approval_granted source_verified submission_enabled economic_authority'.split())


def first_gold_activation_review(*,state,origin,expected_registry,as_of_ms):
    """Read-only staging proof, before or after the exact public key publication.

    This does not claim that the profile, issuer files or activation pointer
    are installed. The human-controlled installer separately checks those.
    """
    validate_registry(expected_registry)
    loaded=_load(role='executor',as_of_ms=as_of_ms)
    _require(loaded is not None,'PARTITION_FIRST_ACTIVATION_REQUIRES_INSTALLED_CUT')
    record,current=loaded;old=record['legacy_registry'];cut=record['candidate']['binding']
    _require(len(expected_registry['keys'])==len(old['keys'])+1
        and _digest(expected_registry['keys'][:-1])==_digest(old['keys'])
        and _digest({k:v for k,v in expected_registry.items() if k!='keys'})
            ==_digest({k:v for k,v in old.items() if k!='keys'}),'PARTITION_FIRST_ACTIVATION_REGISTRY_EXTENSION')
    _require(expected_registry['keys'][-1]['valid_from_ms']>=record['installed_at_ms'],
        'PARTITION_FIRST_ACTIVATION_KEY_PRECEDES_INSTALLATION')
    _new_key(record,expected_registry,as_of_ms)
    observed=registry_sha256(current);legacy=registry_sha256(old);expected=registry_sha256(expected_registry)
    _require(observed in (legacy,expected),'PARTITION_FIRST_ACTIVATION_REGISTRY_CHANGED')
    contexts=_contexts(state,record,as_of_ms)
    _require(contexts['target'] is None and contexts['full_state_sha256']==cut['legacy_state_sha256'],
        'PARTITION_FIRST_ACTIVATION_NEW_EXECUTION')
    _inventory(origin,record,as_of_ms,None)
    context=contexts['legacy']
    risk={key:context[key] for key in ('canonical_wallet','initial_equity','initial_day_pnl',
        'initial_plan_turnover','peak_equity_floor','capacity_consumed','day_id','cash_flow_policy','quantity')}
    for key in ('day_checkpoint_sha256','day_open_equity'):
        if key in context:risk[key]=context[key]
    data={row['kind']:row['data'] for row in origin['reads']}
    deadline=min(origin['observed_at_ms']+30000,data['book']['time']+30000,
        data['mark']['time']+30000,data['mark']['nextFundingTime']-1)
    return dict(schema='DEMO_FIRST_GOLD_ACTIVATION_REVIEW_V1',status='FIRST_GOLD_ACTIVATION_REVIEWED',
        source_commit=record['source_commit'],account_ref_sha256=cut['account_ref_sha256'],
        legacy_registry_sha256=legacy,expected_registry_sha256=expected,observed_registry_sha256=observed,
        registry_state='LEGACY_PREIMAGE' if observed==legacy else 'EXACT_STAGED_REGISTRY',
        partition_binding=_descriptor(record),original_cut_at_ms=cut['recorded_at_ms'],
        legacy_state_sha256=contexts['full_state_sha256'],legacy_context_sha256=_digest(context),legacy_risk=risk,
        reviewed_at_ms=as_of_ms,valid_until_ms=deadline,origin_sha256=_digest(origin),
        observed_usdt_wallet=cut['gold_initial_wallet'],sealed_usdt_wallet=cut['gold_initial_wallet'],
        gold_position_context=None,collateral_conversion='NOT_PERFORMED',
        configuration_activation_verified=False,approval_granted=False,source_verified=False,
        submission_enabled=False,economic_authority=False)


def validate_first_gold_activation_review(report,*,origin,legacy_registry,expected_registry,
        observed_registry,original_review,source_commit,as_of_ms):
    from services.research.demo_execution_origin import assess
    validate_registry(legacy_registry);validate_registry(expected_registry);validate_registry(observed_registry)
    _require(type(report) is dict and set(report)==_FIRST_ACTIVATION_FIELDS,'PARTITION_FIRST_ACTIVATION_FIELDS')
    _require(type(original_review) is dict and set(original_review)==_FIRST_GOLD_REVIEW_FIELDS,
        'PARTITION_FIRST_ACTIVATION_ORIGINAL_REVIEW')
    _require(report['schema']=='DEMO_FIRST_GOLD_ACTIVATION_REVIEW_V1'
        and report['status']=='FIRST_GOLD_ACTIVATION_REVIEWED'
        and report['source_commit']==source_commit==original_review['source_commit']
        and report['account_ref_sha256']==origin['account_ref_sha256']==original_review['account_ref_sha256']
        and report['origin_sha256']==_digest(origin),'PARTITION_FIRST_ACTIVATION_BINDING')
    legacy=registry_sha256(legacy_registry);expected=registry_sha256(expected_registry);observed=registry_sha256(observed_registry)
    _require(report['legacy_registry_sha256']==legacy==original_review['registry_sha256']
        and report['expected_registry_sha256']==expected and report['observed_registry_sha256']==observed
        and observed in (legacy,expected)
        and report['registry_state']==('LEGACY_PREIMAGE' if observed==legacy else 'EXACT_STAGED_REGISTRY'),
        'PARTITION_FIRST_ACTIVATION_REGISTRY_BINDING')
    _require(len(expected_registry['keys'])==len(legacy_registry['keys'])+1
        and _digest(expected_registry['keys'][:-1])==_digest(legacy_registry['keys'])
        and _digest({k:v for k,v in expected_registry.items() if k!='keys'})
            ==_digest({k:v for k,v in legacy_registry.items() if k!='keys'}),
        'PARTITION_FIRST_ACTIVATION_EXPECTED_EXTENSION')
    for key in ('partition_binding','original_cut_at_ms','legacy_state_sha256','sealed_usdt_wallet'):
        _require(_digest(report[key])==_digest(original_review[key]),'PARTITION_FIRST_ACTIVATION_ORIGINAL_CUT_CHANGED')
    _hash(report['legacy_context_sha256'])
    risk=report['legacy_risk'];old=original_review['legacy_risk']
    keys=set('canonical_wallet initial_equity initial_day_pnl initial_plan_turnover peak_equity_floor '
        'capacity_consumed day_id cash_flow_policy quantity'.split())
    _require(type(risk) is dict and keys<=set(risk)<=keys|{'day_checkpoint_sha256','day_open_equity'},
        'PARTITION_FIRST_ACTIVATION_RISK_FIELDS')
    for key in keys-{'day_id'}:
        _require(_digest(risk[key])==_digest(old[key]),'PARTITION_FIRST_ACTIVATION_LEGACY_RISK_CHANGED')
    if risk['day_id']==old['day_id']:
        _require(report['legacy_context_sha256']==original_review['legacy_context_sha256']
            and set(risk)==set(old) and all(_digest(risk[key])==_digest(old[key])
                for key in ('day_checkpoint_sha256','day_open_equity') if key in old),
            'PARTITION_FIRST_ACTIVATION_SAME_DAY_CONTEXT_CHANGED')
    reviewed,deadline=report['reviewed_at_ms'],report['valid_until_ms']
    _require(all(type(value) is int and value>0 for value in (as_of_ms,reviewed,deadline))
        and original_review['reviewed_at_ms']<=reviewed<=as_of_ms<=deadline<=reviewed+30000
        and origin['observed_at_ms']//86400000==reviewed//86400000==as_of_ms//86400000
        and type(risk['day_id']) is int and risk['day_id']==as_of_ms//86400000,
        'PARTITION_FIRST_ACTIVATION_EXPIRED')
    assessment=assess(origin,as_of_ms=as_of_ms)
    _require(assessment['status']=='OBSERVED_FLAT_QUOTE'
        and report['observed_usdt_wallet']==report['sealed_usdt_wallet']==assessment['wallet'],
        'PARTITION_FIRST_ACTIVATION_WALLET')
    _require(report['gold_position_context'] is None and report['collateral_conversion']=='NOT_PERFORMED'
        and all(report[key] is False for key in ('configuration_activation_verified','approval_granted',
            'source_verified','submission_enabled','economic_authority')),'PARTITION_FIRST_ACTIVATION_NO_AUTHORITY')
    return report


def context_reservations(*,state,context,as_of_ms):
    """Pair canonical sources after validating any installed market subset.

    The caller has just rebuilt the original context. A shorter source list is
    admitted only by rereading the protected cut and replaying the entire book.
    This does not permit filtering an unpartitioned account's history.
    """
    try:
        reservations=state['reservations'];sources=context['sources']
        _require(type(reservations) is list and type(sources) is list
            and 1<=len(sources)<=len(reservations)<=32,'PARTITION_CONTEXT_SOURCE_BUDGET')
        if len(sources)!=len(reservations):
            used,original=installed_context(state=state,symbol=context['symbol'],
                account_ref=context['account_ref_sha256'],as_of_ms=as_of_ms)
            _require(used and original==context,'PARTITION_CONTEXT_SUBSET_NOT_VERIFIED')
        by_hash={row['record_hash']:(index,row) for index,row in enumerate(reservations)}
        identities=[source['reservation_sha256'] for source in sources]
        _require(len(by_hash)==len(reservations) and len(set(identities))==len(identities)
            and all(identity in by_hash for identity in identities),'PARTITION_CONTEXT_SOURCE_IDENTITY')
        indices=[by_hash[identity][0] for identity in identities]
        _require(indices==sorted(indices),'PARTITION_CONTEXT_SOURCE_ORDER')
        return [(by_hash[source['reservation_sha256']][1],source) for source in sources]
    except MarketPartitionRejected:raise
    except Exception:raise MarketPartitionRejected('PARTITION_CONTEXT_SOURCES_UNAVAILABLE') from None


def resolve_context(*,state,origin,as_of_ms):
    from live.execution import demo_position_context as position
    from services.research.demo_execution_origin import assess
    loaded=_load(role='executor',as_of_ms=as_of_ms)
    if loaded is None:
        report=assess(origin,as_of_ms=as_of_ms)
        context=position._load_context_unpartitioned(state,symbol=origin['symbol'],
            account_ref=origin['account_ref_sha256'],as_of_ms=as_of_ms) if state['reservations'] or report['status']!='OBSERVED_FLAT_QUOTE' else None
        return context,None
    record,registry=loaded;_new_key(record,registry,as_of_ms)
    context=_contexts(state,record,as_of_ms)['target']
    _inventory(origin,record,as_of_ms,context)
    return context,_descriptor(record)


_BUDGET_REVIEW_FIELDS=set('schema status source_commit account_ref_sha256 registry_sha256 '
    'partition_binding original_candidate legacy_registry original_cut_at_ms reviewed_at_ms valid_until_ms '
    'origin_sha256 full_state_sha256 legacy_context_sha256 legacy_risk observed_usdt_wallet sealed_usdt_wallet '
    'gold_position_context gold_context_sha256 gold_risk partition_installed registry_has_post_cut_keys '
    'collateral_conversion approval_granted source_verified authorization_current_verified '
    'budget_enrolled submission_enabled economic_authority'.split())


def _budget_risk(context):
    fields=set('canonical_wallet initial_equity initial_day_pnl initial_plan_turnover peak_equity_floor '
        'capacity_consumed day_id cash_flow_policy quantity'.split())
    fields|={key for key in ('day_checkpoint_sha256','day_open_equity') if key in context}
    return {key:context[key] for key in fields}


def validate_gold_budget_review(report,*,origin,registry,source_commit,as_of_ms):
    """Validate an original private-worker response, never an execution grant."""
    from live.execution.demo_market_partition import _candidate
    from live.execution.demo_position_context import validate_origin
    from services.research.demo_execution_origin import assess
    try:
        validate_registry(registry)
        _require(type(as_of_ms) is int and as_of_ms>0,'PARTITION_BUDGET_REVIEW_TIME')
        _require(type(report) is dict and set(report)==_BUDGET_REVIEW_FIELDS,'PARTITION_BUDGET_REVIEW_FIELDS')
        _require(report['schema']=='DEMO_GOLD_BUDGET_SCOPE_REVIEW_V1'
            and report['status']=='GOLD_BUDGET_SCOPE_REVIEW_REQUIRED'
            and type(source_commit) is str and re.fullmatch('[a-f0-9]{40}',source_commit)
            and report['source_commit']==source_commit
            and origin['symbol']=='XAUUSDT' and report['account_ref_sha256']==origin['account_ref_sha256']
            and report['registry_sha256']==registry_sha256(registry)
            and report['origin_sha256']==_digest(origin),'PARTITION_BUDGET_REVIEW_BINDING')
        old=validate_registry(report['legacy_registry']);original=report['original_candidate']
        cut=_candidate(original,as_of_ms);binding=report['partition_binding']
        _require(type(binding) is dict and set(binding)==set('schema source_commit installed_record_sha256 '
            'candidate_sha256 binding_sha256'.split())
            and binding['schema']=='DEMO_MARKET_PARTITION_RUNTIME_BINDING_V1'
            and binding['source_commit']==source_commit and binding['candidate_sha256']==_digest(original)
            and binding['binding_sha256']==original['binding_sha256']
            and cut['source_commit']==source_commit and cut['account_ref_sha256']==report['account_ref_sha256']
            and cut['recorded_at_ms']==report['original_cut_at_ms']
            and registry_sha256(old)==cut['registry_sha256']
            and len(registry['keys'])>len(old['keys']) and registry['keys'][:len(old['keys'])]==old['keys']
            and {k:v for k,v in registry.items() if k!='keys'}=={k:v for k,v in old.items() if k!='keys'},
            'PARTITION_BUDGET_REVIEW_ORIGINAL_BINDING')
        for key in ('installed_record_sha256','candidate_sha256','binding_sha256'):_hash(binding[key])
        for key in ('account_ref_sha256','registry_sha256','origin_sha256','full_state_sha256',
                'legacy_context_sha256','gold_context_sha256'):_hash(report[key])
        risk=_budget_risk(original['review']['legacy_book'])
        _require(_digest(report['legacy_risk'])==_digest(risk) and risk['quantity']=='0'
            and report['legacy_context_sha256']==cut['legacy_context_sha256']
            and report['sealed_usdt_wallet']==cut['gold_initial_wallet'],'PARTITION_BUDGET_REVIEW_LEGACY_RISK')
        start,deadline=report['reviewed_at_ms'],report['valid_until_ms']
        _require(type(start) is int and type(deadline) is int
            and cut['recorded_at_ms']<=start<=as_of_ms<=deadline<=start+30000
            and origin['observed_at_ms']//86400000==start//86400000==as_of_ms//86400000,
            'PARTITION_BUDGET_REVIEW_EXPIRED')
        observed=assess(origin,as_of_ms=as_of_ms)
        context=report['gold_position_context']
        _require(observed['status']=='OBSERVED_FLAT_QUOTE' and report['observed_usdt_wallet']==observed['wallet']
            and report['gold_context_sha256']==_digest(context),'PARTITION_BUDGET_REVIEW_GOLD_BINDING')
        if context is None:
            _require(report['gold_risk'] is None and observed['wallet']==cut['gold_initial_wallet']
                and report['full_state_sha256']==cut['legacy_state_sha256'],'PARTITION_BUDGET_REVIEW_EMPTY_TARGET')
        else:
            validate_origin(origin,context,as_of_ms=as_of_ms)
            _require(context['quantity']=='0' and _digest(report['gold_risk'])==_digest(_budget_risk(context)),
                'PARTITION_BUDGET_REVIEW_GOLD_RISK')
        _require(report['partition_installed'] is True and report['registry_has_post_cut_keys'] is True
            and report['collateral_conversion']=='NOT_PERFORMED'
            and all(report[key] is False for key in ('approval_granted','source_verified',
                'authorization_current_verified','budget_enrolled','submission_enabled','economic_authority')),
            'PARTITION_BUDGET_REVIEW_NOT_AUTHORITY')
        return report
    except MarketPartitionRejected:raise
    except Exception:raise MarketPartitionRejected('PARTITION_BUDGET_REVIEW_INVALID') from None


@ledger_decimal_context()
def gold_budget_scope_review(*,state,origin,as_of_ms):
    """Read the full native book after Gold registration, including expired keys.

    Only the diagnostic path omits _new_key; execution consumers retain it.
    No allowance, pilot, account baseline or ledger entry is written here.
    """
    loaded=_load(role='executor',as_of_ms=as_of_ms)
    _require(loaded is not None,'PARTITION_BUDGET_REVIEW_REQUIRES_INSTALLED_CUT')
    record,registry=loaded;cut=record['candidate']['binding']
    _require(len(registry['keys'])>len(record['legacy_registry']['keys']),
        'PARTITION_BUDGET_REVIEW_REQUIRES_GOLD_REGISTRATION')
    contexts=_contexts(state,record,as_of_ms);context=contexts['target']
    _require(context is None or context['quantity']=='0','PARTITION_BUDGET_REVIEW_REQUIRES_FLAT_TARGET')
    _inventory(origin,record,as_of_ms,context)
    data={row['kind']:row['data'] for row in origin['reads']}
    from services.research.demo_execution_origin import assess
    deadline=min(origin['observed_at_ms']+30000,data['book']['time']+30000,
        data['mark']['time']+30000,data['mark']['nextFundingTime']-1)
    report=dict(schema='DEMO_GOLD_BUDGET_SCOPE_REVIEW_V1',status='GOLD_BUDGET_SCOPE_REVIEW_REQUIRED',
        source_commit=record['source_commit'],account_ref_sha256=cut['account_ref_sha256'],
        registry_sha256=registry_sha256(registry),partition_binding=_descriptor(record),
        original_candidate=record['candidate'],legacy_registry=record['legacy_registry'],
        original_cut_at_ms=cut['recorded_at_ms'],reviewed_at_ms=as_of_ms,valid_until_ms=deadline,
        origin_sha256=_digest(origin),full_state_sha256=contexts['full_state_sha256'],
        legacy_context_sha256=_digest(contexts['legacy']),legacy_risk=_budget_risk(contexts['legacy']),
        observed_usdt_wallet=assess(origin,as_of_ms=as_of_ms)['wallet'],sealed_usdt_wallet=cut['gold_initial_wallet'],
        gold_position_context=context,gold_context_sha256=_digest(context),
        gold_risk=None if context is None else _budget_risk(context),partition_installed=True,
        registry_has_post_cut_keys=True,collateral_conversion='NOT_PERFORMED',approval_granted=False,
        source_verified=False,authorization_current_verified=False,budget_enrolled=False,
        submission_enabled=False,economic_authority=False)
    return validate_gold_budget_review(report,origin=origin,registry=registry,
        source_commit=record['source_commit'],as_of_ms=as_of_ms)


def check_proposal(proof,*,role='executor',registry=None,policy=None,as_of_ms):
    """Reread the local protected mirror and common final activation record."""
    from live.execution.demo_market_partition import partition_state
    loaded=_load(role=role,registry=registry,as_of_ms=as_of_ms)
    if loaded is None:
        _require('market_partition' not in proof['state'],'PARTITION_LOCAL_BINDING_REQUIRED')
        _require(all(order['symbol']==proof['origin']['symbol'] for row in proof['state']['reservations']
            for order in row['payload']['orders']),'PARTITION_MIXED_HISTORY_REQUIRES_BINDING')
        return
    record,registry=loaded;_new_key(record,registry,as_of_ms,policy)
    _require(proof['state'].get('market_partition')==_descriptor(record),'PARTITION_PROPOSAL_BINDING_CHANGED')
    view=partition_state(state=_core(proof['state']),candidate=record['candidate'],as_of_ms=as_of_ms)
    context=proof['state'].get('position_context')
    if role=='executor':
        _require(_contexts(proof['state'],record,as_of_ms)['target']==context,
            'PARTITION_CANONICAL_CONTEXT_CHANGED')
    elif view['target']['reservations']:
        _require(type(context) is dict and context.get('schema')=='DEMO_CANONICAL_POSITION_CONTEXT_V1'
            and context.get('symbol')=='XAUUSDT' and context.get('account_ref_sha256')==record['candidate']['binding']['account_ref_sha256']
            and context.get('initial_equity')==record['candidate']['binding']['gold_initial_wallet']
            and [row['reservation_sha256'] for row in context.get('sources',[])]==[row['record_hash'] for row in view['target']['reservations']],
            'PARTITION_ISSUER_TARGET_CONTEXT_BINDING')
    else:_require(context is None,'PARTITION_FIRST_TARGET_CONTEXT_MUST_BE_ABSENT')
    _inventory(proof['origin'],record,as_of_ms,context)
