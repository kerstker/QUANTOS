"""Seal market cuts and verify fresh observations through the original V2 book.

Candidates and revalidation reports remain diagnostics. Protected runtime
bindings, independent authorization and the original risk gates are separate.
"""
from decimal import Decimal
import json
import re

from live.execution.demo_batch_risk import _digest
from live.execution.demo_dispatch_store import _hash
from live.execution.demo_issuer_policy import require, amount
from services.execution.perpetual_ledger_v2 import ledger_decimal_context
from services.validation.demo_submission import validate_registry,registry_sha256

_STATE_FIELDS = {'reservations', 'recovery', 'oms', 'wire'}
_BINDING_FIELDS = set('schema environment source_commit account_ref_sha256 registry_sha256 '
    'kill_switch_sha256 recorded_at_ms legacy_symbol target_symbol legacy_collateral target_collateral '
    'legacy_state_sha256 legacy_context_sha256 legacy_reservation_count legacy_wire_count '
    'legacy_reservation_tail_sha256 review_sha256 gold_initial_wallet'.split())


def _copy(value):
    return json.loads(json.dumps(value, allow_nan=False))


def _time(value):
    require(type(value) is int and 0<value<=9007199254740991, 'PARTITION_TIME')
    return value


def _digest_field(value):
    require(type(value) is str and re.fullmatch('[a-f0-9]{64}',value), 'PARTITION_DIGEST')


def _state(state, as_of_ms):
    require(type(state) is dict and set(state)==_STATE_FIELDS
        and all(type(state[key]) is list for key in _STATE_FIELDS), 'PARTITION_STATE_FIELDS')
    require(1<=len(state['reservations'])<=32 and len(state['wire'])<=40000,
            'PARTITION_STATE_BUDGET')
    clients={};batches={};previous=''
    for sequence,row in enumerate(state['reservations'],1):
        require(row['seq']==sequence and row['prev_hash']==previous
            and row['record_hash']==_hash({k:v for k,v in row.items() if k!='record_hash'}),
            'PARTITION_RESERVATION_CHAIN')
        previous=row['record_hash'];batches[previous]=row
        body=row['payload'];account=row['account_ref_sha256']
        require(body['context']['account_ref_sha256']==account
            and type(body['reserved_at_ms']) is int
            and 0<=body['reserved_at_ms']<=as_of_ms and body['orders'], 'PARTITION_RESERVATION_BINDING')
        for order in body['orders']:
            client=order['newClientOrderId']
            require(client not in clients, 'PARTITION_CLIENT_REUSED')
            clients[client]=(row['record_hash'],order['symbol'],account)
    require(len(state['oms'])==len(state['recovery'])==len(clients), 'PARTITION_INTENT_COVERAGE')
    for name,field in (('oms','client_order_id'),('recovery','client_order_id')):
        ids=[row[field] for row in state[name]]
        require(len(set(ids))==len(ids) and set(ids)==set(clients), 'PARTITION_INTENT_IDENTITY')
    for row in state['oms']:
        reservation=batches[clients[row['client_order_id']][0]]
        require(row['status'] in ('FILLED','CANCELED','REJECTED','EXPIRED')
            and row['instrument_id']==clients[row['client_order_id']][1]
            and type(row['submitted_at']) is int
            and reservation['payload']['reserved_at_ms']<=row['submitted_at']<=as_of_ms,
            'PARTITION_OMS_UNRESOLVED')
    for row in state['recovery']:
        reservation=batches[clients[row['client_order_id']][0]]
        require(row['state']=='RESOLVED' and row['instrument_id']==clients[row['client_order_id']][1]
            and type(row['recorded_at_ms']) is int
            and reservation['payload']['reserved_at_ms']<=row['recorded_at_ms']<=as_of_ms,
                'PARTITION_RECOVERY_UNRESOLVED')
    previous=''
    for sequence,row in enumerate(state['wire'],1):
        require(row['seq']==sequence and row['prev_hash']==previous
            and row['record_hash']==_hash({k:v for k,v in row.items() if k!='record_hash'}),
            'PARTITION_WIRE_CHAIN')
        previous=row['record_hash']
        require(row['client_order_id'] in clients and row['reservation_sha256'] in batches
            and clients[row['client_order_id']][0]==row['reservation_sha256']
            and type(row['at_ms']) is int
            and batches[row['reservation_sha256']]['payload']['reserved_at_ms']<=row['at_ms']<=as_of_ms,
            'PARTITION_WIRE_BINDING')
    return clients


@ledger_decimal_context()
def prepare(*, state, legacy_origin, gold_origin, registry, source_commit, kill_switch_sha256, now_ms):
    """Original replay and dual-account review precede the immutable cut digest."""
    from live.execution.demo_position_context import _load_context_unpartitioned
    from live.execution.demo_market_transition_review import review
    _time(now_ms);_state(state, now_ms)
    require(type(source_commit) is str and re.fullmatch('[a-f0-9]{40}',source_commit), 'PARTITION_SOURCE')
    _digest_field(kill_switch_sha256)
    account=legacy_origin['account_ref_sha256']
    require(all(row['account_ref_sha256']==account and all(order['symbol']=='BTCUSDC'
        for order in row['payload']['orders']) for row in state['reservations']), 'PARTITION_LEGACY_SCOPE')
    context=_load_context_unpartitioned(state,symbol='BTCUSDC',account_ref=account,as_of_ms=now_ms)
    reviewed=review(legacy_origin=legacy_origin,gold_origin=gold_origin,legacy_context=context,
                    registry=registry,now_ms=now_ms)
    binding=dict(schema='DEMO_MARKET_PARTITION_BINDING_V1',environment='BINANCE_USDM_DEMO',
        source_commit=source_commit,account_ref_sha256=account,registry_sha256=reviewed['registry_sha256'],
        kill_switch_sha256=kill_switch_sha256,recorded_at_ms=now_ms,
        legacy_symbol='BTCUSDC',target_symbol='XAUUSDT',legacy_collateral='USDC',target_collateral='USDT',
        legacy_state_sha256=_digest(state),legacy_context_sha256=_digest(context),
        legacy_reservation_count=len(state['reservations']),legacy_wire_count=len(state['wire']),
        legacy_reservation_tail_sha256=state['reservations'][-1]['record_hash'],
        review_sha256=_digest(reviewed),gold_initial_wallet=reviewed['gold_observation']['wallet'])
    result=dict(schema='DEMO_MARKET_PARTITION_CANDIDATE_V1',
        status='PARTITION_CANDIDATE_REQUIRES_OPERATOR_INSTALLATION',binding=binding,binding_sha256=_digest(binding),
        review=reviewed,installed=False,approval_granted=False,runtime_authorization=False,
        source_verified=False,kill_switch_verified=False,submission_enabled=False,economic_authority=False)
    require(len(json.dumps(result,allow_nan=False).encode())<=65536, 'PARTITION_CANDIDATE_SIZE')
    return _copy(result)


def _candidate(candidate, as_of_ms):
    require(type(candidate) is dict and set(candidate)==set('schema status binding binding_sha256 review '
        'installed approval_granted runtime_authorization source_verified kill_switch_verified '
        'submission_enabled economic_authority'.split()), 'PARTITION_CANDIDATE_FIELDS')
    require(candidate['schema']=='DEMO_MARKET_PARTITION_CANDIDATE_V1'
        and candidate['status']=='PARTITION_CANDIDATE_REQUIRES_OPERATOR_INSTALLATION'
        and all(candidate[key] is False for key in ('installed','approval_granted','runtime_authorization',
            'source_verified','kill_switch_verified','submission_enabled','economic_authority')),
        'PARTITION_CANDIDATE_NOT_AUTHORITY')
    binding=candidate['binding'];reviewed=candidate['review']
    require(type(binding) is dict and set(binding)==_BINDING_FIELDS
        and binding['schema']=='DEMO_MARKET_PARTITION_BINDING_V1'
        and binding['environment']=='BINANCE_USDM_DEMO'
        and (binding['legacy_symbol'],binding['target_symbol'],binding['legacy_collateral'],binding['target_collateral'])
            ==('BTCUSDC','XAUUSDT','USDC','USDT'), 'PARTITION_BINDING_FIELDS')
    require(type(binding['source_commit']) is str and re.fullmatch('[a-f0-9]{40}',binding['source_commit']),
            'PARTITION_SOURCE')
    for key in ('account_ref_sha256','registry_sha256','kill_switch_sha256','legacy_state_sha256',
                'legacy_context_sha256','legacy_reservation_tail_sha256','review_sha256'):
        _digest_field(binding[key])
    require(_digest(binding)==candidate['binding_sha256'] and _digest(reviewed)==binding['review_sha256'],
            'PARTITION_CANDIDATE_CHANGED')
    require(_time(binding['recorded_at_ms'])<=as_of_ms and reviewed['reviewed_at_ms']==binding['recorded_at_ms']
        and reviewed['account_ref_sha256']==binding['account_ref_sha256']
        and reviewed['registry_sha256']==binding['registry_sha256']
        and reviewed['legacy_book']['context_sha256']==binding['legacy_context_sha256']
        and reviewed['legacy_book']['quantity']=='0'
        and reviewed['gold_observation']['wallet']==binding['gold_initial_wallet']
        and amount(binding['gold_initial_wallet'])>0, 'PARTITION_REVIEW_BINDING')
    require(type(binding['legacy_reservation_count']) is int and 1<=binding['legacy_reservation_count']<=32
        and type(binding['legacy_wire_count']) is int and 0<=binding['legacy_wire_count']<=40000,
        'PARTITION_PREFIX_COUNT')
    return binding


def partition_state(*, state, candidate, as_of_ms):
    """Derive two views only after checking every original prefix component."""
    try:
        _time(as_of_ms);binding=_candidate(candidate,as_of_ms);_state(state,as_of_ms)
        cut=binding['legacy_reservation_count'];wire_cut=binding['legacy_wire_count']
        require(len(state['reservations'])>=cut and len(state['wire'])>=wire_cut, 'PARTITION_PREFIX_MISSING')
        prefix=state['reservations'][:cut]
        clients={order['newClientOrderId'] for row in prefix for order in row['payload']['orders']}
        legacy=dict(reservations=prefix,wire=state['wire'][:wire_cut],
            recovery=[row for row in state['recovery'] if row['client_order_id'] in clients],
            oms=[row for row in state['oms'] if row['client_order_id'] in clients])
        require(_digest(legacy)==binding['legacy_state_sha256']
            and prefix[-1]['record_hash']==binding['legacy_reservation_tail_sha256'], 'PARTITION_PREFIX_CHANGED')
        target=dict(reservations=state['reservations'][cut:],wire=state['wire'][wire_cut:],
            recovery=[row for row in state['recovery'] if row['client_order_id'] not in clients],
            oms=[row for row in state['oms'] if row['client_order_id'] not in clients])
        target_hashes={row['record_hash'] for row in target['reservations']}
        require(all(row['account_ref_sha256']==binding['account_ref_sha256']
            and row['payload']['context']['account_ref_sha256']==binding['account_ref_sha256']
            and binding['recorded_at_ms']<row['payload']['reserved_at_ms']<=as_of_ms
            and all(order['symbol']==binding['target_symbol'] for order in row['payload']['orders'])
            for row in target['reservations']), 'PARTITION_TARGET_SCOPE')
        require(all(row['reservation_sha256'] in target_hashes for row in target['wire']),
                'PARTITION_LEGACY_WIRE_AFTER_CUT')
        return _copy(dict(schema='DEMO_MARKET_PARTITION_VIEW_V1',binding_sha256=candidate['binding_sha256'],
            full_state_sha256=_digest(state),legacy=legacy,target=target,
            installed=False,runtime_authorization=False,submission_enabled=False,economic_authority=False))
    except ValueError:
        raise
    except (KeyError,TypeError,IndexError):
        raise ValueError('PARTITION_INPUT_INVALID') from None


def rebuild_contexts(*, state, candidate, as_of_ms, replayer=None):
    """Reuse the same canonical replayer for each collateral; never sum them."""
    from live.execution.demo_position_context import _load_context_unpartitioned
    replay=_load_context_unpartitioned if replayer is None else replayer
    view=partition_state(state=state,candidate=candidate,as_of_ms=as_of_ms);binding=candidate['binding']
    legacy=replay(view['legacy'],symbol=binding['legacy_symbol'],
        account_ref=binding['account_ref_sha256'],as_of_ms=binding['recorded_at_ms'])
    require(_digest(legacy)==binding['legacy_context_sha256'], 'PARTITION_LEGACY_CONTEXT_CHANGED')
    target=None
    if view['target']['reservations']:
        target=replay(view['target'],symbol=binding['target_symbol'],
            account_ref=binding['account_ref_sha256'],as_of_ms=as_of_ms)
        require(target['symbol']==binding['target_symbol']
            and target['account_ref_sha256']==binding['account_ref_sha256']
            and Decimal(target['initial_equity'])==Decimal(binding['gold_initial_wallet']),
            'PARTITION_TARGET_BASELINE_CHANGED')
    return _copy(dict(schema='DEMO_MARKET_PARTITION_CONTEXTS_V1',
        status='TARGET_CONTEXT_REBUILT' if target is not None else 'NO_TARGET_EXECUTION_HISTORY',
        binding_sha256=candidate['binding_sha256'],full_state_sha256=view['full_state_sha256'],
        legacy=legacy,target=target,collateral_conversion='NOT_PERFORMED',
        installed=False,runtime_authorization=False,source_verified=False,
        submission_enabled=False,economic_authority=False))


@ledger_decimal_context()
def revalidate_pre_activation(*,state,candidate,legacy_origin,gold_origin,registry,
        source_commit,kill_switch_sha256,now_ms):
    """Check fresh flat observations against the original immutable cut.

    The legacy replay stays at the approved cut time, including its original
    daily loss and capacity facts. Current observations can belong to a later
    day; they never become a replacement candidate or initial cash ledger.
    """
    from live.execution.demo_market_transition_review import _inventory
    from services.research.demo_execution_origin import assess
    _time(now_ms);binding=_candidate(candidate,now_ms)
    validate_registry(registry)
    require(source_commit==binding['source_commit']
        and registry_sha256(registry)==binding['registry_sha256']
        and kill_switch_sha256==binding['kill_switch_sha256']
        and registry['keys'] and all(k['valid_until_ms']<=binding['recorded_at_ms'] for k in registry['keys']),
        'PARTITION_REVALIDATION_DEPLOYMENT_CHANGED')
    contexts=rebuild_contexts(state=state,candidate=candidate,as_of_ms=now_ms)
    require(contexts['target'] is None and contexts['full_state_sha256']==binding['legacy_state_sha256'],
        'PARTITION_REVALIDATION_NEW_EXECUTION')
    legacy=contexts['legacy'];account=binding['account_ref_sha256']
    require(legacy_origin['symbol']=='BTCUSDC' and gold_origin['symbol']=='XAUUSDT'
        and legacy_origin['account_ref_sha256']==gold_origin['account_ref_sha256']==account
        and legacy_origin['source_sha256']==gold_origin['source_sha256'],
        'PARTITION_REVALIDATION_CAPTURE_BINDING')
    require(binding['recorded_at_ms']<=legacy_origin['observed_at_ms']
        and legacy_origin['completed_at_ms']<=gold_origin['observed_at_ms']<=now_ms
        and legacy_origin['observed_at_ms']//86400000==gold_origin['observed_at_ms']//86400000==now_ms//86400000,
        'PARTITION_REVALIDATION_CAPTURE_SEQUENCE')
    old=assess(legacy_origin,as_of_ms=now_ms);gold=assess(gold_origin,as_of_ms=now_ms)
    require(old['status']==gold['status']=='OBSERVED_FLAT_QUOTE'
        and old['wallet']==legacy['canonical_wallet']
        and gold['wallet']==binding['gold_initial_wallet'], 'PARTITION_REVALIDATION_WALLET_CHANGED')
    old_inventory=_inventory(legacy_origin,now_ms);gold_inventory=_inventory(gold_origin,now_ms)
    require(old_inventory==gold_inventory
        and _digest(gold_inventory)==candidate['review']['account_inventory_sha256'],
        'PARTITION_REVALIDATION_INVENTORY_CHANGED')
    deadlines=[]
    for origin in (legacy_origin,gold_origin):
        data={row['kind']:row['data'] for row in origin['reads']}
        deadlines.extend((origin['observed_at_ms']+30000,data['book']['time']+30000,
            data['mark']['time']+30000,data['mark']['nextFundingTime']-1))
    return _copy(dict(schema='DEMO_PARTITION_PRE_ACTIVATION_REVALIDATION_V1',
        status='ORIGINAL_MARKET_PARTITION_REVALIDATED',source_commit=source_commit,
        candidate_sha256=_digest(candidate),binding_sha256=candidate['binding_sha256'],
        account_ref_sha256=account,original_cut_at_ms=binding['recorded_at_ms'],
        reviewed_at_ms=now_ms,observed_day_id=now_ms//86400000,valid_until_ms=min(deadlines),
        full_state_sha256=_digest(state),legacy_context_sha256=_digest(legacy),
        registry_sha256=registry_sha256(registry),kill_switch_sha256=kill_switch_sha256,
        legacy_origin_sha256=_digest(legacy_origin),gold_origin_sha256=_digest(gold_origin),
        account_inventory_sha256=_digest(gold_inventory),legacy_wallet=legacy['canonical_wallet'],
        gold_initial_wallet=binding['gold_initial_wallet'],original_cut_preserved=True,
        collateral_conversion='NOT_PERFORMED',installed=False,approval_granted=False,
        source_verified=False,kill_switch_verified=False,submission_enabled=False,economic_authority=False))


def validate_pre_activation_report(report,*,candidate,registry,source_commit,as_of_ms):
    """Validate the immutable cut and original observation deadline in a pipe report."""
    _time(as_of_ms);binding=_candidate(candidate,as_of_ms);validate_registry(registry)
    require(type(report) is dict and set(report)==set('schema status source_commit candidate_sha256 '
        'binding_sha256 account_ref_sha256 original_cut_at_ms reviewed_at_ms observed_day_id valid_until_ms '
        'full_state_sha256 legacy_context_sha256 registry_sha256 kill_switch_sha256 legacy_origin_sha256 '
        'gold_origin_sha256 account_inventory_sha256 legacy_wallet gold_initial_wallet original_cut_preserved '
        'collateral_conversion installed approval_granted source_verified kill_switch_verified '
        'submission_enabled economic_authority'.split()), 'PARTITION_REVALIDATION_REPORT_FIELDS')
    require(report['schema']=='DEMO_PARTITION_PRE_ACTIVATION_REVALIDATION_V1'
        and report['status']=='ORIGINAL_MARKET_PARTITION_REVALIDATED'
        and report['source_commit']==source_commit==binding['source_commit']
        and report['candidate_sha256']==_digest(candidate) and report['binding_sha256']==candidate['binding_sha256']
        and report['account_ref_sha256']==binding['account_ref_sha256']
        and report['original_cut_at_ms']==binding['recorded_at_ms']
        and report['full_state_sha256']==binding['legacy_state_sha256']
        and report['legacy_context_sha256']==binding['legacy_context_sha256']
        and report['registry_sha256']==binding['registry_sha256']==registry_sha256(registry)
        and report['kill_switch_sha256']==binding['kill_switch_sha256']
        and report['account_inventory_sha256']==candidate['review']['account_inventory_sha256']
        and report['legacy_wallet']==candidate['review']['legacy_book']['canonical_wallet']
        and report['gold_initial_wallet']==binding['gold_initial_wallet'], 'PARTITION_REVALIDATION_REPORT_BINDING')
    reviewed=_time(report['reviewed_at_ms']);deadline=_time(report['valid_until_ms'])
    require(binding['recorded_at_ms']<=reviewed<=as_of_ms<=deadline<=reviewed+30000
        and type(report['observed_day_id']) is int
        and report['observed_day_id']==reviewed//86400000==as_of_ms//86400000,
        'PARTITION_REVALIDATION_REPORT_EXPIRED')
    for key in ('legacy_origin_sha256','gold_origin_sha256'):_digest_field(report[key])
    require(report['original_cut_preserved'] is True and report['collateral_conversion']=='NOT_PERFORMED'
        and all(report[key] is False for key in ('installed','approval_granted','source_verified',
            'kill_switch_verified','submission_enabled','economic_authority')), 'PARTITION_REVALIDATION_REPORT_NOT_AUTHORITY')
    return report
