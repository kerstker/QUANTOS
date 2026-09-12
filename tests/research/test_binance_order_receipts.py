"""Offline native receipts bind fixed actions without inventing paid costs."""
from copy import deepcopy
from decimal import Decimal as D
import hashlib
import json
from types import SimpleNamespace

import pytest

from services.execution.passive_queue import Action
from services.research.passive_receipt_calibration import validate_calibration
from services.research.binance_order_receipts import normalize_order_receipts

HASH = 'a' * 64
ACTIONS = (Action(100, 'ORDER', 'fixed-buy', 'BUY', D('1'), D('99')),)
TAPE = SimpleNamespace(symbol='BTCUSDC', start_ms=100, end_ms=300)


def message(at, status, last='0', total='0', *, trade_id=0, fee=None, asset='USDC'):
    order = {'s':'BTCUSDC', 'c':'client-1', 'S':'BUY', 'o':'LIMIT', 'f':'GTX',
        'q':'1', 'p':'99', 'x':'TRADE' if last != '0' else ('NEW' if status == 'NEW' else 'CANCELED'),
        'X':status, 'i':123, 'l':last, 'z':total, 'L':'99' if last != '0' else '0',
        'T':at, 't':trade_id, 'm':True, 'R':False, 'ps':'BOTH'}
    if fee is not None:
        order.update(n=fee, N=asset)
    return {'e':'ORDER_TRADE_UPDATE', 'E':at+1, 'T':at, 'o':order}


def receipt(payload, received):
    raw = json.dumps(payload, separators=(',', ':'))
    return {'received_at_ms':received, 'raw_utf8':raw,
        'response_sha256':hashlib.sha256(raw.encode()).hexdigest()}


def native_spec():
    return {'schema':'PASSIVE_BINANCE_ORDER_RECEIPTS_V1', 'cutoff_ms':150,
        'raw_observed':{'schema':'BINANCE_USDM_ORDER_RECEIPTS_V1', 'symbol':'BTCUSDC',
            'account_ref_sha256':'b'*64, 'action_hash':HASH, 'provenance':'SYNTHETIC_TEST_FIXTURE',
            'coverage':{'start_ms':100, 'end_ms':300, 'gaps':[]},
            'bindings':[{'order_id':'fixed-buy', 'venue_order_id':'123', 'client_order_id':'client-1'}],
            'messages':[receipt(message(101, 'NEW'), 111),
                receipt(message(121, 'PARTIALLY_FILLED', '0.4', '0.4', trade_id=7, fee='0.00792'), 131),
                receipt(message(151, 'FILLED', '0.6', '1', trade_id=8, fee='0.01188'), 171)]}}


def parse(value=None, **kwargs):
    return validate_calibration(value or native_spec(), TAPE, ACTIONS, HASH, **kwargs)


def edit_message(spec, index, change):
    record=spec['raw_observed']['messages'][index]
    payload=json.loads(record['raw_utf8']);change(payload)
    spec['raw_observed']['messages'][index]=receipt(payload, record['received_at_ms'])


def v2_spec(tif='IOC',reduce_only=False):
    spec=native_spec();raw=spec['raw_observed'];raw['schema']='BINANCE_USDM_ORDER_RECEIPTS_V2'
    raw['bindings'][0].update(time_in_force=tif,reduce_only=reduce_only)
    for i in range(len(raw['messages'])):
        edit_message(spec,i,lambda p:p['o'].update(f=tif,m=tif=='GTX',R=reduce_only))
    return spec


def test_v2_expiry_reason_is_retained_without_reinterpreting_v1_history():
    source=v2_spec()
    for i in range(3):edit_message(source,i,lambda p:p['o'].update(er='0'))
    raw=source['raw_observed']
    _,evidence=normalize_order_receipts(raw,ACTIONS,'BTCUSDC',HASH)
    assert evidence['trades_count']==2 and raw['messages'][0]['raw_utf8'].endswith('"er":"0"}}')
    old=native_spec();edit_message(old,0,lambda p:p['o'].update(er='0'))
    with pytest.raises(ValueError):parse(old)


def test_v2_ioc_observations_bind_options_and_keep_reported_taker_costs():
    spec=v2_spec();raw=spec['raw_observed'];original=deepcopy(raw)
    _,evidence=normalize_order_receipts(raw,ACTIONS,'BTCUSDC',HASH)
    assert raw==original and evidence['schema']=='BINANCE_USDM_ORDER_RECEIPTS_V2'
    assert evidence['reported_quote_commission']=='0.0198'
    assert {r['liquidity_role'] for r in evidence['trade_rows']}=={'TAKER'}
    assert evidence['order_options']==[{'order_id':'fixed-buy','time_in_force':'IOC','reduce_only':False}]
    with pytest.raises(ValueError,match='GTX'):parse(spec)


def test_v2_reduce_only_flags_must_match_external_fixed_order_scope():
    spec=v2_spec('GTX',True)
    with pytest.raises(ValueError):normalize_order_receipts(spec['raw_observed'],ACTIONS,'BTCUSDC',HASH)
    _,evidence=normalize_order_receipts(spec['raw_observed'],ACTIONS,'BTCUSDC',HASH,reduce_only_ids=frozenset({'fixed-buy'}))
    assert evidence['order_options'][0]['reduce_only'] is True


@pytest.mark.parametrize('change',['missing_option','unknown_tif','different_tif','ioc_maker','legacy_options'])
def test_v2_order_option_errors_do_not_relax_v1_or_maker_assumptions(change):
    spec=v2_spec();raw=spec['raw_observed']
    if change=='missing_option':del raw['bindings'][0]['reduce_only']
    elif change=='unknown_tif':raw['bindings'][0]['time_in_force']='GTC'
    elif change=='different_tif':raw['bindings'][0]['time_in_force']='GTX'
    elif change=='ioc_maker':edit_message(spec,1,lambda p:p['o'].update(m=True))
    else:raw['schema']='BINANCE_USDM_ORDER_RECEIPTS_V1'
    with pytest.raises(ValueError):normalize_order_receipts(raw,ACTIONS,'BTCUSDC',HASH)


def test_native_partial_fills_use_receive_time_and_exact_observed_commission():
    original=native_spec(); frozen=deepcopy(original); parsed=parse(original)
    assert original==frozen
    events=parsed['observed']['orders'][0]['events']
    assert [e['state'] for e in events]==['ACK','PARTIALLY_FILLED','FILLED']
    assert events[-1]['venue_ms']==151 and events[-1]['received_ms']==171
    evidence=parsed['native_observations']
    assert evidence['reported_commission_by_asset']=={'USDC':'0.0198'}
    assert evidence['trades_count']==2 and evidence['missing_commission_trades']==0
    assert evidence['reported_filled_quantity']=='1'
    assert evidence['source_verified'] is False and evidence['actual_paid_fees'] is None
    assert evidence['trade_rows'][-1]['event_ms']==152
    assert evidence['trade_rows'][-1]['transaction_ms']==151


def test_repeated_transport_does_not_double_count_fills_or_fees():
    spec=native_spec();duplicate=deepcopy(spec['raw_observed']['messages'][1]);duplicate['received_at_ms']=200
    spec['raw_observed']['messages'].append(duplicate)
    evidence=parse(spec)['native_observations']
    assert evidence['messages_count']==4 and evidence['duplicate_messages']==1
    assert evidence['trades_count']==2 and evidence['reported_commission_by_asset']=={'USDC':'0.0198'}


def test_repeated_trade_with_new_envelope_is_retained_once():
    spec=native_spec();duplicate=deepcopy(spec['raw_observed']['messages'][1])
    payload=json.loads(duplicate['raw_utf8']);payload['E']=201;payload['T']=200
    spec['raw_observed']['messages'].append(receipt(payload, 211))
    assert parse(spec)['native_observations']['duplicate_messages']==1


def test_missing_commission_remains_unknown_and_other_asset_is_not_converted():
    spec=native_spec()
    edit_message(spec, 1, lambda p: (p['o'].pop('n'), p['o'].pop('N')))
    edit_message(spec, 2, lambda p: p['o'].update(n='0.000001', N='BNB'))
    evidence=parse(spec)['native_observations']
    assert evidence['reported_commission_by_asset']=={'BNB':'0.000001'}
    assert evidence['missing_commission_trades']==1
    assert evidence['all_commissions_in_quote'] is False
    assert evidence['reported_quote_commission'] is None
    assert evidence['actual_paid_fees'] is None


def test_cancel_after_partial_retains_fill_and_zero_extra_fees():
    spec=native_spec()
    spec['raw_observed']['messages'][-1]=receipt(message(151,'CANCELED',total='0.4'),171)
    parsed=parse(spec)
    assert parsed['observed']['orders'][0]['events'][-1]['state']=='CANCELLED'
    assert parsed['native_observations']['reported_filled_quantity']=='0.4'
    assert parsed['native_observations']['reported_quote_commission']=='0.00792'


@pytest.mark.parametrize('change', ['hash','symbol','client','order','intent','hedge','maker','cumulative',
    'duplicate_trade_conflict','unsupported_event','credential','duplicate_json','float_quantity','backwards_receive',
    'future_event','nontrade_fee','source_authority','missing_binding','reduce_only','nested_ignored_field'])
def test_unbound_ambiguous_or_unsupported_native_receipts_are_rejected(change):
    spec=native_spec();raw=spec['raw_observed']
    edits={
        'symbol':lambda p:p['o'].update(s='BTCUSDT'),
        'client':lambda p:p['o'].update(c='different'),
        'order':lambda p:p['o'].update(i=321),
        'intent':lambda p:p['o'].update(q='2'),
        'hedge':lambda p:p['o'].update(ps='LONG'),
        'maker':lambda p:p['o'].update(m='true'),
        'cumulative':lambda p:p['o'].update(l='0.5'),
        'unsupported_event':lambda p:p.update(e='ACCOUNT_UPDATE'),
        'credential':lambda p:p.update(listenKey='forbidden-fixture'),
        'float_quantity':lambda p:p['o'].update(l=0.4),
        'future_event':lambda p:p.update(E=200),
        'reduce_only':lambda p:p['o'].update(R=True),
        'nested_ignored_field':lambda p:p['o'].update(ap={'headers':'forbidden-fixture'}),
    }
    if change in edits:edit_message(spec,1,edits[change])
    elif change=='hash':raw['messages'][1]['response_sha256']='c'*64
    elif change=='backwards_receive':raw['messages'][1]['received_at_ms']=110
    elif change=='source_authority':raw['provenance']='VERIFIED_VENUE'
    elif change=='missing_binding':raw['bindings']=[]
    elif change=='nontrade_fee':edit_message(spec,0,lambda p:p['o'].update(n='1',N='USDC'))
    elif change=='duplicate_trade_conflict':
        payload=json.loads(raw['messages'][1]['raw_utf8']);payload['o']['n']='0.1'
        raw['messages'].append(receipt(payload,200))
    else:
        record=raw['messages'][1];record['raw_utf8']=record['raw_utf8'].replace('"n":','"n":"0","n":')
        record['response_sha256']=hashlib.sha256(record['raw_utf8'].encode()).hexdigest()
    with pytest.raises(ValueError):parse(spec)


def test_reduce_only_flag_must_match_the_frozen_action_metadata():
    spec=native_spec()
    for i in range(3):edit_message(spec,i,lambda p:p['o'].update(R=True))
    parsed=parse(spec,reduce_only_ids=frozenset({'fixed-buy'}))
    assert parsed['native_observations']['trades_count']==2


def test_native_input_changes_source_identity_without_rewriting_old_data():
    original=native_spec();changed=deepcopy(original)
    edit_message(changed,2,lambda p:p['o'].update(n='0.02'))
    a,b=parse(original),parse(changed)
    assert a['observed']['provenance']['declared_source_sha256']!=b['observed']['provenance']['declared_source_sha256']
    assert [{k:v for k,v in e.items() if k!='receipt_id'} for e in a['observed']['orders'][0]['events']]==[
        {k:v for k,v in e.items() if k!='receipt_id'} for e in b['observed']['orders'][0]['events']]


def test_account_conditions_and_native_receipts_cannot_name_different_accounts():
    with pytest.raises(ValueError,match='receipt account differs'):
        parse(account_ref_sha256='d'*64)


def test_native_identifiers_larger_than_javascript_integer_range_remain_exact_strings():
    spec=native_spec();identifier='9007199254740993'
    spec['raw_observed']['bindings'][0]['venue_order_id']=identifier
    for index in range(3):edit_message(spec,index,lambda p:p['o'].update(i=int(identifier),t=int(identifier)+index))
    evidence=parse(spec)['native_observations']
    assert evidence['trade_rows'][0]['venue_order_id']==identifier
    assert evidence['trade_rows'][0]['trade_id']=='9007199254740994'


def native_workflow_source():
    from tests.research.test_passive_calibration_study import source
    data=source();legacy=data['calibration'];observed=legacy['observed'];messages=[];bindings=[]
    for number,row in enumerate(observed['orders']):
        filled=D('0');binding={'order_id':row['order_id'],'venue_order_id':str(123+number),'client_order_id':f'client-{number}'}
        bindings.append(binding)
        for index,event in enumerate(row['events']):
            total=D(event['cumulative_filled']);last=total-filled;filled=total
            native_state={'ACK':'NEW','PARTIALLY_FILLED':'PARTIALLY_FILLED','FILLED':'FILLED','CANCELLED':'CANCELED'}[event['state']]
            payload=message(event['venue_ms'],native_state,str(last),str(total),trade_id=index,
                fee=str(last*D(row['limit_price'])*D('0.0002')) if last else None)
            payload['E']=event['venue_ms']
            payload['o'].update(c=binding['client_order_id'],i=int(binding['venue_order_id']),q=row['quantity'],p=row['limit_price'],S=row['side'])
            messages.append(receipt(payload,event['received_ms']))
    raw=native_spec()['raw_observed'];raw.update(symbol=observed['symbol'],action_hash=observed['action_hash'],
        coverage=observed['coverage'],bindings=bindings,messages=sorted(messages,key=lambda r:r['received_at_ms']))
    data['calibration']={'schema':'PASSIVE_BINANCE_ORDER_RECEIPTS_V1','cutoff_ms':legacy['cutoff_ms'],'raw_observed':raw}
    return data


def test_native_workflow_preserves_selection_and_separates_observed_from_modeled_fees(tmp_path):
    from tools.passive_execution_study import study
    from tools.btc_factory_research import verified
    data=native_workflow_source();path=tmp_path/'native.json';path.write_text(json.dumps(data))
    before=path.read_bytes();result=study(path,tmp_path/'study');evidence=result['calibration']['native_observations']
    assert result['selected_profile']==25 and result['simulations_attempted']==6
    assert evidence['reported_quote_commission']=='0.00198'
    compared=next(x for x in evidence['fee_comparisons'] if x['profile_ms']==25)
    assert compared['simulated_fee']=='0.0099'
    assert compared['reported_minus_simulated_fee']=='-0.00792'
    assert result['trials'][1]['account']['reconciliation']['fee']=='0.0099'
    assert result['trials'][1]['account']['actual_venue_fees'] is None
    assert path.read_bytes()==before
    assert verified(tmp_path/'study/native-observations.json')['trades_count']==1
    assert evidence['source_verified'] is False and evidence['actual_paid_fees'] is None


def test_native_fee_change_does_not_choose_a_better_delay(tmp_path):
    from tools.passive_execution_study import study
    data=native_workflow_source();changed=deepcopy(data)
    for i,record in enumerate(changed['calibration']['raw_observed']['messages']):
        payload=json.loads(record['raw_utf8'])
        if payload['o']['x']=='TRADE':edit_message(changed['calibration'],i,lambda p:p['o'].update(n='100'))
    results=[]
    for index,value in enumerate((data,changed)):
        path=tmp_path/f'input-{index}.json';path.write_text(json.dumps(value));results.append(study(path,tmp_path/f'study-{index}'))
    assert results[0]['selected_profile']==results[1]['selected_profile']==25
    assert [x['fit'] for x in results[0]['calibration']['trials']]==[x['fit'] for x in results[1]['calibration']['trials']]
    assert [x['account']['result_hash'] for x in results[0]['trials']]==[x['account']['result_hash'] for x in results[1]['trials']]


def test_stp_expiration_preserves_native_reason_and_partial_quantity():
    spec=native_spec();payload=message(151,'EXPIRED_IN_MATCH',total='0.4');payload['o']['x']='EXPIRED'
    spec['raw_observed']['messages'][-1]=receipt(payload,171)
    result=parse(spec)
    assert result['observed']['orders'][0]['events'][-1]['state']=='EXPIRED'
    assert result['native_observations']['message_rows'][-1]['native_state']=='EXPIRED_IN_MATCH'
    assert result['native_observations']['reported_filled_quantity']=='0.4'


@pytest.mark.parametrize('change',['gap','other_asset','missing_commission','short_window','unobserved_order'])
def test_incomplete_native_fee_coverage_has_no_quote_difference(change):
    from services.research.binance_order_receipts import native_fee_comparisons
    evidence=parse()['native_observations']
    if change=='gap':evidence['coverage']['gaps']=[{'start_ms':110,'end_ms':115}]
    elif change in ('other_asset','missing_commission'):evidence['reported_quote_commission']=None
    elif change=='short_window':evidence['coverage']['end_ms']=250
    else:evidence['unobserved_order_ids']=['missing']
    rows=native_fee_comparisons(evidence,{25:{'reconciliation':{'fee':'0.099'}}},TAPE,(0,25,100))
    assert all(not row['comparison_available'] and row['reported_minus_simulated_fee'] is None for row in rows)
    assert rows[1]['simulated_fee']=='0.099'


def test_signed_reported_commission_is_not_silently_clamped_or_deducted_twice():
    spec=native_spec();edit_message(spec,1,lambda p:p['o'].update(n='-0.00792'))
    assert parse(spec)['native_observations']['reported_quote_commission']=='0.00396'
