"""Native old BTC prefix and original Scope enrollment, with isolated custody.

No current Gold grant, new baseline, cash report or empty replacement ledger is
created. The caller's fixed-file read is real under the original UID fixture.
"""
from copy import deepcopy
import json

import pytest

from tests.tools.test_gold_scope_pilot import (
    book as book, installed as installed, first as first, materials as materials,
    scope_inputs as scope_inputs, day_materials as day_materials, enrollment_case as enrollment_case,
)
from tests.live.test_demo_market_partition_runtime import shift
from tests.tools.test_gold_bar_opening_origin import opening_case as opening_case
from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _state


@pytest.fixture
def idle_case(enrollment_case):
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from live.execution import demo_market_partition_runtime as partition
    args, request, at = enrollment_case
    config = enrollment.enroll_requested(**args, now_ms=at)
    root = args['root']; area = root/'gold-pilot'
    # Past both the original 24h pilot and the installed Scope key. Historical
    # verification must not invoke the execution-only current-key gate.
    now = request['budget_scope']['valid_until_ms']+1
    current = _state(**{k:args[k] for k in ('dispatch','recovery','oms')})
    record, _ = partition._load(role='executor', as_of_ms=now)
    values = dict(request=request, configuration=config,
        receipt=json.loads((area/'enrollment.json').read_bytes()),
        intent=json.loads((area/'enrollment-intent.json').read_bytes()), state=current,
        origin=shift(args['origin'],now-at), partition_record=record, source_commit='c'*40, now_ms=now)
    assert current['reservations'] and all(o['symbol']=='BTCUSDC' for r in current['reservations'] for o in r['payload']['orders'])
    return values, root


def validate(values):
    from tools.gold_pilot_idle_closure import validate_idle
    return validate_idle(**values)


def test_native_old_btc_history_can_prove_idle_after_scope_expiry(idle_case,monkeypatch):
    from live.execution import demo_market_partition_runtime as partition
    values, root = idle_case
    before = deepcopy(values)
    files = {p:p.read_bytes() for p in (root/'gold-pilot').iterdir() if p.is_file()}
    monkeypatch.setattr(partition,'_new_key',lambda *a,**kw:pytest.fail('idle observation renewed authority'))
    result = validate(values)
    assert result['schema']=='GOLD_PILOT_IDLE_EVIDENCE_V1'
    assert result['mode']=='NO_EXECUTION_SINCE_ENROLLMENT'
    assert result['execution_state_sha256']==values['intent']['prepared']['execution_state_sha256']
    assert result['enrollment_intent_sha256']==_digest(values['intent'])
    assert result['enrollment_receipt_sha256']==_digest(values['receipt'])
    assert result['origin_sha256']==_digest(values['origin'])
    assert result['position_flat'] is True and result['observed_equity']=='4200'
    assert result['submission_enabled'] is result['economic_authority'] is False
    assert values==before and all(p.read_bytes()==raw for p,raw in files.items())
    assert not (root/'gold-pilot/stopped.json').exists()


@pytest.mark.parametrize('part',['reservations','recovery','oms','wire'])
@pytest.mark.parametrize('mutation',['delete','append'])
def test_every_original_execution_component_must_be_unchanged(idle_case,part,mutation):
    values,_=idle_case;values=deepcopy(values)
    assert values['state'][part]
    if mutation=='delete':values['state'][part].pop()
    else:values['state'][part].append(deepcopy(values['state'][part][-1]))
    with pytest.raises(ValueError):validate(values)


@pytest.mark.parametrize('damage',['missing_intent','receipt','request','source','configuration','partition_missing',
    'partition_replaced','origin_account','origin_source','stale_origin','before_end','gold_wallet','btc_wallet','positions'])
def test_idle_proof_rejects_missing_or_changed_originals(idle_case,damage):
    values,_=idle_case;values=deepcopy(values)
    if damage=='missing_intent':values['intent']=None
    elif damage=='receipt':values['receipt']['execution_state_sha256']='f'*64
    elif damage=='request':values['request']['duration_seconds']-=1
    elif damage=='source':values['source_commit']='d'*40
    elif damage=='configuration':values['configuration']['initial_equity']='4201'
    elif damage=='partition_missing':values['partition_record']=None
    elif damage=='partition_replaced':values['partition_record']['candidate_sha256']='f'*64
    elif damage=='origin_account':values['origin']['account_ref_sha256']='f'*64
    elif damage=='origin_source':values['origin']['source_sha256']='f'*64
    elif damage=='stale_origin':values['now_ms']+=120000
    elif damage=='before_end':
        values['now_ms']=values['configuration']['ends_at_ms']-1
        values['origin']=shift(values['origin'], values['now_ms']-values['origin']['completed_at_ms'])
    elif damage in ('gold_wallet','btc_wallet'):
        for row in values['origin']['reads']:
            if row['kind'].startswith('account'):
                asset=next(a for a in row['data']['assets'] if a['asset']==('USDT' if damage=='gold_wallet' else 'USDC'))
                for name in ('walletBalance','crossWalletBalance','availableBalance','marginBalance'):asset[name]='4201'
    else:
        for row in values['origin']['reads']:
            if row['kind']=='positions':row['data']=[{'symbol':'XAUUSDT','positionAmt':'0.1'}]
    with pytest.raises(ValueError):validate(values)


def test_helper_never_accepts_a_caller_assertion_of_empty_target(idle_case):
    values,_=idle_case;values=deepcopy(values)
    values['state']['position_context']=None
    with pytest.raises(ValueError):validate(values)


@pytest.mark.parametrize('legacy_without_intent',[False,True])
def test_native_empty_book_uses_original_completed_enrollment(opening_case,legacy_without_intent):
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from tests.tools.test_gold_pilot_capacity import write
    from tests.tools.test_gold_strategy_entry import origin_at
    f=opening_case
    request=dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1',source_commit='c'*40,
        account_ref_sha256=f.account,duration_seconds=86400,max_loss_fraction='0.05',submission_enabled=False)
    write(f.root/'gold-pilot-request.json',request)
    config=enrollment.enroll_requested(root=f.root,source_commit='c'*40,origin=origin_at(f.clock[0]),
        now_ms=f.clock[0],**dict(zip(('dispatch','recovery','oms'),f.native)))
    area=f.root/'gold-pilot'
    intent=json.loads((area/'enrollment-intent.json').read_bytes())
    receipt=json.loads((area/'enrollment.json').read_bytes())
    if legacy_without_intent:
        # The old receipt is structurally valid but has no durable original
        # preparation. It must not be interpreted as proof of no execution.
        receipt=enrollment._receipt(config,request,receipt['execution_state_sha256'])
        intent=None
        assert enrollment.completed_configuration_records(configuration=config,receipt=receipt,
            intent=intent,request=request)==config
    at=config['ends_at_ms']+1
    args=dict(request=request,configuration=config,receipt=receipt,intent=intent,state=_state(*f.native),
        origin=origin_at(at),partition_record=None,source_commit='c'*40,now_ms=at)
    if legacy_without_intent:
        with pytest.raises(ValueError,match='ORIGINAL_INTENT_REQUIRED'):validate(args)
    else:
        result=validate(args)
        assert result['market_partition_sha256'] is None and result['observed_equity']=='5000'
        assert result['execution_state_sha256']==receipt['execution_state_sha256']
