"""Preserve the entire settled prefix before deriving a target-market view."""
from copy import deepcopy
from decimal import Decimal
import pytest

from live.execution.demo_batch_risk import _digest
from live.execution.demo_dispatch_store import _hash
from tests.live.test_demo_day_checkpoint import recovered_context
from tests.live.test_demo_reported_recovery import close
from tests.live.test_demo_market_transition_review import materials, NOW


@pytest.fixture
def book(tmp_path, monkeypatch):
    (tmp_path/'book').mkdir();(tmp_path/'observations').mkdir()
    _, stores, events, _, state, context = recovered_context(tmp_path/'book', monkeypatch)
    values = materials(tmp_path/'observations')
    def shift(value):
        if isinstance(value, dict): return {k:shift(v) for k,v in value.items()}
        if isinstance(value, list): return [shift(v) for v in value]
        return value+60000 if type(value) is int and value>=NOW else value
    for name in ('legacy_origin','gold_origin'):
        values[name]=shift(values[name]);values[name]['account_ref_sha256']=context['account_ref_sha256']
        for row in values[name]['reads']:
            if row['account_ref_sha256'] is not None:row['account_ref_sha256']=context['account_ref_sha256']
    values.update(now_ms=NOW+130000)
    from live.execution.demo_authority import load_registry_for_executor
    values['registry']=load_registry_for_executor()
    try:yield state, context, values, stores, events
    finally:events.close();close(stores)


def candidate(book):
    from live.execution.demo_market_partition import prepare
    state, _, values, _, _ = book
    return prepare(state=state, legacy_origin=values['legacy_origin'], gold_origin=values['gold_origin'],
        registry=values['registry'], source_commit='c'*40, kill_switch_sha256='d'*64,
        now_ms=values['now_ms'])


def split(state, value, at=NOW+140000):
    from live.execution.demo_market_partition import partition_state
    return partition_state(state=state,candidate=value,as_of_ms=at)


def append_target(state):
    """Projection fixture only; never a signing or dispatch operation."""
    result=deepcopy(state);old=result['reservations'][-1]
    row=deepcopy(old);row.update(seq=old['seq']+1,prev_hash=old['record_hash'],approval_id='target-fixture')
    row['payload']['reserved_at_ms']=NOW+135000
    row['payload']['orders'][0].update(symbol='XAUUSDT',newClientOrderId='QDtargetfixture')
    row['payload']['context']['account_ref_sha256']=old['account_ref_sha256']
    row['record_hash']=_hash({k:v for k,v in row.items() if k!='record_hash'})
    result['reservations'].append(row)
    recovery=deepcopy(result['recovery'][0]);recovery.update(client_order_id='QDtargetfixture',instrument_id='XAUUSDT',recorded_at_ms=NOW+136000)
    result['recovery'].append(recovery)
    oms=deepcopy(result['oms'][0]);oms.update(client_order_id='QDtargetfixture',instrument_id='XAUUSDT',submitted_at=NOW+135000)
    result['oms'].append(oms)
    return result


def test_seal_retains_full_real_prefix_and_separate_gold_amount_without_authority(book):
    state, context, _, stores, events=book;before=deepcopy(state);rows=events.row_count()
    value=candidate(book);result=split(state,value)
    assert value['status']=='PARTITION_CANDIDATE_REQUIRES_OPERATOR_INSTALLATION'
    assert value['binding']['legacy_state_sha256']==_digest(state)
    assert value['binding']['legacy_context_sha256']==_digest(context)
    assert value['binding']['gold_initial_wallet']=='4200'
    assert result['legacy']==state and result['target']==dict(reservations=[],recovery=[],oms=[],wire=[])
    assert result['binding_sha256']==value['binding_sha256']
    assert result['runtime_authorization'] is False and result['installed'] is False
    from live.execution.demo_pre_submit import _state
    assert state==before==_state(stores['dispatch'],stores['recovery'],stores['oms'])
    assert events.row_count()==rows


def test_existing_oldbook_loss_survives_target_projection(book):
    state, _, _, _, _=book;value=candidate(book);mixed=append_target(state);before=deepcopy(mixed)
    result=split(mixed,value)
    assert result['legacy']==state and len(result['target']['reservations'])==1
    assert result['target']['oms'][0]['instrument_id']=='XAUUSDT'
    assert mixed==before
    from live.execution.demo_position_context import load_context
    original=load_context(result['legacy'],symbol='BTCUSDC',
        account_ref=value['binding']['account_ref_sha256'],as_of_ms=value['binding']['recorded_at_ms'])
    assert original['canonical_wallet']=='4999.6801' and original['initial_equity']=='5000'
    assert sum(map(Decimal,original['capacity_consumed'].values()))==Decimal('239.8')


@pytest.mark.parametrize('damage',['reservation','wire','recovery','oms','drop','target_market',
    'target_account','target_early','target_chain','duplicate_id','legacy_wire_append','unresolved','extra_oms',
    'bool_oms_time','target_recovery_symbol'])
def test_no_prefix_loss_or_post_seal_legacy_mutation_can_be_hidden(book,damage):
    state, _, _, _, _=book;value=candidate(book);mixed=append_target(state)
    if damage=='reservation':mixed['reservations'][0]['approval_id']='changed'
    elif damage=='wire':mixed['wire'][0]['at_ms']+=1
    elif damage=='recovery':mixed['recovery'][0]['observed_status']='CANCELED'
    elif damage=='oms':mixed['oms'][0]['status']='CANCELED'
    elif damage=='drop':mixed['reservations']=mixed['reservations'][1:]
    elif damage=='target_market':mixed['reservations'][-1]['payload']['orders'][0]['symbol']='BTCUSDC'
    elif damage=='target_account':mixed['reservations'][-1]['payload']['context']['account_ref_sha256']='a'*64
    elif damage=='target_early':mixed['reservations'][-1]['payload']['reserved_at_ms']=NOW
    elif damage=='target_chain':mixed['reservations'][-1]['prev_hash']='a'*64
    elif damage=='duplicate_id':mixed['reservations'][-1]['payload']['orders'][0]['newClientOrderId']=mixed['oms'][0]['client_order_id']
    elif damage=='legacy_wire_append':mixed['wire'].append(deepcopy(mixed['wire'][-1]))
    elif damage=='unresolved':mixed['recovery'][-1]['state']='PENDING_QUERY'
    elif damage=='extra_oms':mixed['oms'].append(dict(mixed['oms'][-1],client_order_id='unowned'))
    elif damage=='bool_oms_time':mixed['oms'][-1]['submitted_at']=False
    else:mixed['recovery'][-1]['instrument_id']='BTCUSDC'
    with pytest.raises(ValueError):split(mixed,value)


@pytest.mark.parametrize('damage',['binding_digest','review_digest','context_digest','future','source','kill','quantity'])
def test_candidate_cannot_rebind_old_book_or_declared_deployment_inputs(book,damage):
    state, _, _, _, _=book;value=candidate(book)
    if damage=='binding_digest':value['binding_sha256']='a'*64
    elif damage=='review_digest':value['review']['reviewed_at_ms']-=1
    elif damage=='context_digest':value['binding']['legacy_context_sha256']='a'*64
    elif damage=='future':value['binding']['recorded_at_ms']=NOW+500000
    elif damage=='source':value['binding']['source_commit']='HEAD'
    elif damage=='kill':value['binding']['kill_switch_sha256']=''
    else:value['review']['legacy_book']['quantity']='1'
    if damage!='binding_digest':
        value['binding']['review_sha256']=_digest(value['review'])
        value['binding_sha256']=_digest(value['binding'])
    with pytest.raises(ValueError):split(state,value)


@pytest.mark.parametrize('amount',['abc','NaN','-1','0'])
def test_rehashed_candidate_still_rejects_invalid_gold_collateral(book,amount):
    state,_,_,_,_=book;value=candidate(book)
    value['binding']['gold_initial_wallet']=value['review']['gold_observation']['wallet']=amount
    value['binding']['review_sha256']=_digest(value['review']);value['binding_sha256']=_digest(value['binding'])
    with pytest.raises(ValueError):split(state,value)


def test_rebuild_calls_the_original_ledger_for_each_market_without_summing_cash(book,monkeypatch):
    from live.execution import demo_position_context as original
    from live.execution.demo_market_partition import rebuild_contexts
    state, context, _, _, _=book;value=candidate(book);mixed=append_target(state)
    replay=original._load_context_unpartitioned;calls=[]
    def load(state,*,symbol,account_ref,as_of_ms):
        calls.append(symbol)
        if symbol=='BTCUSDC':return replay(state,symbol=symbol,account_ref=account_ref,as_of_ms=as_of_ms)
        assert len(state['reservations'])==1 and state['reservations'][0]['payload']['orders'][0]['symbol']=='XAUUSDT'
        return dict(schema='DEMO_CANONICAL_POSITION_CONTEXT_V1',symbol=symbol,account_ref_sha256=account_ref,
                    initial_equity='4200',canonical_wallet='4199',quantity='0')
    monkeypatch.setattr(original,'_load_context_unpartitioned',load)
    result=rebuild_contexts(state=mixed,candidate=value,as_of_ms=NOW+140000)
    assert calls==['BTCUSDC','XAUUSDT']
    assert result['legacy']==context and result['target']['canonical_wallet']=='4199'
    assert result['collateral_conversion']=='NOT_PERFORMED' and result['runtime_authorization'] is False


def test_empty_target_has_no_manufactured_context_or_baseline(book):
    from live.execution.demo_market_partition import rebuild_contexts
    state, context, _, _, _=book
    result=rebuild_contexts(state=state,candidate=candidate(book),as_of_ms=NOW+140000)
    assert result['status']=='NO_TARGET_EXECUTION_HISTORY' and result['target'] is None
    assert result['legacy']==context and result['runtime_authorization'] is False
