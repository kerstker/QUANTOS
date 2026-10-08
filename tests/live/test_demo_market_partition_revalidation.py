"""Fresh days validate the same sealed collateral and original native book."""
from copy import deepcopy

import pytest

from tests.live.test_demo_market_partition import book as book, candidate, append_target, NOW


def observations(values,delta=86400000):
    def shift(value):
        if isinstance(value,dict):return {k:shift(v) for k,v in value.items()}
        if isinstance(value,list):return [shift(v) for v in value]
        return value+delta if type(value) is int and value>=NOW else value
    return {name:shift(values[name]) for name in ('legacy_origin','gold_origin')}


def fresh_observations(values,at):
    """Independent synthetic captures finish before the advancing fixture clock."""
    result={}
    for name,offset in (('legacy_origin',2000),('gold_origin',1000)):
        delta=at-offset-values[name]['observed_at_ms']
        result[name]=observations(values,delta=delta)[name]
    return result


def inspect(book,**overrides):
    from live.execution.demo_market_partition import revalidate_pre_activation
    state,_,values,_,_=book
    args=dict(state=state,candidate=candidate(book),registry=values['registry'],
        source_commit='c'*40,kill_switch_sha256='d'*64,now_ms=values['now_ms']+86400000,
        **observations(values))
    args.update(overrides)
    return revalidate_pre_activation(**args)


def test_next_day_replays_original_cut_and_preserves_original_cash_and_loss(book):
    from live.execution.demo_batch_risk import _digest
    state,context,values,stores,events=book;sealed=candidate(book)
    before=deepcopy(state),events.row_count(),deepcopy(sealed)
    result=inspect(book)
    assert result['status']=='ORIGINAL_MARKET_PARTITION_REVALIDATED'
    assert result['candidate_sha256']==_digest(sealed)
    assert result['original_cut_at_ms']==sealed['binding']['recorded_at_ms']
    assert result['observed_day_id']>context['day_id']
    assert result['full_state_sha256']==sealed['binding']['legacy_state_sha256']
    assert result['legacy_context_sha256']==_digest(context)
    assert result['legacy_wallet']=='4999.6801' and result['gold_initial_wallet']=='4200'
    assert result['installed'] is result['approval_granted'] is result['submission_enabled'] is False
    assert result['economic_authority'] is result['kill_switch_verified'] is result['source_verified'] is False
    assert before==(state,events.row_count(),sealed)
    from live.execution.demo_pre_submit import _state
    assert _state(stores['dispatch'],stores['recovery'],stores['oms'])==state


@pytest.mark.parametrize('damage',['old_oms','old_wire','target','stop','source','registry','legacy_wallet',
    'gold_wallet','held','different_inventory','different_capture_source','sequence','stale_day','future'])
def test_cross_day_does_not_hide_changed_history_wallet_or_observation(book,damage):
    state,_,values,_,_=book;origins=observations(values);args=dict(origins)
    if damage=='old_oms':
        altered=deepcopy(state);altered['oms'][0]['quantity']='0.004';args['state']=altered
    elif damage=='old_wire':
        altered=deepcopy(state);altered['wire'][0]['at_ms']+=1;args['state']=altered
    elif damage=='target':args['state']=append_target(state)
    elif damage=='stop':args['kill_switch_sha256']='e'*64
    elif damage=='source':args['source_commit']='e'*40
    elif damage=='registry':
        reg=deepcopy(values['registry']);reg['deployment_id']='changed';args['registry']=reg
    elif damage in ('legacy_wallet','gold_wallet','different_inventory'):
        for name,origin in origins.items():
            for row in origin['reads']:
                if row['kind'].startswith('account'):
                    if damage=='different_inventory' and name=='gold_origin':row['data']['assets'].pop()
                    for asset in row['data']['assets']:
                        if asset['asset']==('USDC' if damage=='legacy_wallet' else 'USDT'):
                            for field in ('walletBalance','crossWalletBalance','availableBalance','marginBalance'):
                                asset[field]='4300'
    elif damage=='held':
        for row in origins['gold_origin']['reads']:
            if row['kind']=='positions':row['data']=[dict(symbol='BTCUSDC',positionAmt='0.002')]
    elif damage=='different_capture_source':origins['gold_origin']['source_sha256']='e'*64
    elif damage=='sequence':origins['legacy_origin']['completed_at_ms']=origins['gold_origin']['observed_at_ms']+1
    elif damage=='stale_day':args.update(legacy_origin=values['legacy_origin'],gold_origin=values['gold_origin'])
    else:args['now_ms']=values['now_ms']
    with pytest.raises(ValueError):inspect(book,**args)
