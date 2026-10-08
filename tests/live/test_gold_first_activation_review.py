"""A pending first enrollment rechecks the exact registry and native old book."""
from copy import deepcopy
from decimal import Decimal

import pytest

from live.execution import demo_market_partition_runtime as runtime
from live.execution.demo_batch_risk import _digest
from services.validation.demo_submission import registry_sha256
from tests.live.test_demo_market_partition import book as book, NOW
from tests.live.test_demo_market_partition_runtime import installed as installed
from tests.live.test_gold_first_enrollment_review import first as first


@pytest.mark.parametrize('published', [False, True])
def test_pending_first_gold_review_accepts_only_legacy_or_exact_staged_registry(first, published):
    value=first;expected=deepcopy(value['registered'])
    original=runtime.first_gold_enrollment_review(state=value['state'],origin=value['origin'],as_of_ms=NOW+140000)
    if published:value['registry']=deepcopy(expected)
    before=deepcopy(value['state']),value['events'].row_count(),deepcopy(value['registry'])
    report=runtime.first_gold_activation_review(state=value['state'],origin=value['origin'],
        expected_registry=expected,as_of_ms=NOW+140000)
    assert report['schema']=='DEMO_FIRST_GOLD_ACTIVATION_REVIEW_V1'
    assert report['registry_state']==('EXACT_STAGED_REGISTRY' if published else 'LEGACY_PREIMAGE')
    assert report['expected_registry_sha256']==registry_sha256(expected)
    assert report['observed_registry_sha256']==registry_sha256(value['registry'])
    assert report['legacy_registry_sha256']==registry_sha256(value['legacy'])
    assert report['legacy_state_sha256']==_digest(before[0]) and report['gold_position_context'] is None
    assert report['observed_usdt_wallet']=='4200'
    risk=report['legacy_risk']
    assert risk['canonical_wallet']=='4999.6801' and risk['initial_equity']=='5000'
    assert sum(map(Decimal,risk['capacity_consumed'].values()))==Decimal('239.8')
    assert all(report[name] is False for name in ('configuration_activation_verified',
        'approval_granted','source_verified','submission_enabled','economic_authority'))
    assert before==(value['state'],value['events'].row_count(),value['registry'])
    assert runtime.validate_first_gold_activation_review(report,origin=value['origin'],
        legacy_registry=value['legacy'],expected_registry=expected,observed_registry=value['registry'],
        original_review=original,source_commit='c'*40,as_of_ms=NOW+140000)==report


@pytest.mark.parametrize('damage',['extra_key','old_key','base','different_new_key','expired','early',
    'activation','wallet','state','source'])
def test_pending_review_cannot_hide_new_authority_or_changed_original_inputs(first,monkeypatch,damage):
    value=first;expected=deepcopy(value['registered']);origin=deepcopy(value['origin']);state=deepcopy(value['state'])
    if damage=='extra_key':expected['keys'].append(deepcopy(expected['keys'][-1]))
    elif damage=='old_key':expected['keys'][0]['status']='REVOKED'
    elif damage=='base':expected['deployment_sha256']='e'*64
    elif damage=='different_new_key':
        value['registry']=deepcopy(expected);value['registry']['keys'][-1]['key_id']='another-isolated-key'
    elif damage=='expired':expected['keys'][-1]['valid_until_ms']=NOW+139999
    elif damage=='early':expected['keys'][-1]['valid_from_ms']=NOW+129999
    elif damage=='activation':value['activation'].unlink()
    elif damage=='wallet':
        for read in origin['reads']:
            if read['kind'].startswith('account'):
                asset=next(a for a in read['data']['assets'] if a['asset']=='USDC')
                for name in ('walletBalance','crossWalletBalance','availableBalance','marginBalance'):asset[name]='5000'
    elif damage=='state':state['reservations'][0]['record_hash']='d'*64
    else:monkeypatch.setattr(runtime,'executing_source_commit',lambda:'d'*40)
    with pytest.raises(ValueError):runtime.first_gold_activation_review(state=state,origin=origin,
        expected_registry=expected,as_of_ms=NOW+140000)


@pytest.mark.parametrize('damage',['legacy_context','day_checkpoint','day_open_equity','numeric_permission'])
def test_activation_receiver_rejects_changed_same_day_risk_and_false_permission(first,damage):
    value=first;expected=deepcopy(value['registered'])
    original=runtime.first_gold_enrollment_review(state=value['state'],origin=value['origin'],as_of_ms=NOW+140000)
    report=runtime.first_gold_activation_review(state=value['state'],origin=value['origin'],
        expected_registry=expected,as_of_ms=NOW+140000)
    if damage=='legacy_context':report['legacy_context_sha256']='e'*64
    elif damage=='day_checkpoint':report['legacy_risk']['day_checkpoint_sha256']='e'*64
    elif damage=='day_open_equity':report['legacy_risk']['day_open_equity']='5000'
    else:report['approval_granted']=0
    with pytest.raises(ValueError):
        runtime.validate_first_gold_activation_review(report,origin=value['origin'],
            legacy_registry=value['legacy'],expected_registry=expected,observed_registry=value['registry'],
            original_review=original,source_commit='c'*40,as_of_ms=NOW+140000)
