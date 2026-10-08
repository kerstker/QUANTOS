"""Prefix-only fitting is frozen before whole-window execution can fail."""
from copy import deepcopy
import json
import pytest

from services.research.ai_factory_store import digest,FactoryStore
from services.research.passive_execution_study import prepare_input,prepare_account,run_profile
from services.research.passive_receipt_calibration import queue_receipts
from services.console.api.profitability_views import profitability_payload
from tests.research.test_passive_execution_study import account_source
from tests.execution.test_passive_account_environment import BASE
from tests.data.test_binance_execution_tape import depth,trade
from tools.passive_execution_study import study
from tools.btc_factory_research import verified


def source():
    data=account_source();raw=data['tape'];raw['ended_at_ms']=BASE+1300
    packets=[('market',1225,'aggTrade',trade(aggregate=202,first=406,last=406,at=BASE+1220,quantity='0.001')),
        ('public',1255,'depth@100ms',depth(first=106,last=107,previous=105,at=BASE+1250)),
        ('market',1265,'markPrice@1s',{'e':'markPriceUpdate','s':'BTCUSDC','E':BASE+1260,'T':BASE+120000,'p':'100','i':'100','r':'0.0001'})]
    for route,received,stream,payload in packets:
        raw['messages'].append({'sequence':len(raw['messages']),'route':route,'received_at_ms':BASE+received,
            'raw_utf8':json.dumps({'stream':'btcusdc@'+stream,'data':payload})})
        raw['connections'][route]['messages']+=1
    for row in raw['connections'].values():row['closed_at_ms']=BASE+1300
    tape,actions=prepare_input(data);account=prepare_account(data,tape)
    observed={'action_hash':digest(data['actions']),'symbol':tape.symbol,
        'coverage':{'start_ms':tape.start_ms,'end_ms':tape.end_ms,'gaps':[]}}
    result=run_profile(tape,actions,25,account=account,release_identity={'fixture':'synthetic-receipts'})
    observed=queue_receipts(result['queue'],actions,observed)
    observed['provenance']['kind']='SYNTHETIC_TEST_FIXTURE'
    data['calibration']={'schema':'PASSIVE_RECEIPT_CALIBRATION_V1','cutoff_ms':BASE+1120,'observed':observed}
    return data


def run(data,output):
    path=output.with_suffix('.json');path.write_text(json.dumps(data));return study(path,output)


def test_selects_by_prefix_receipt_error_and_reports_heldout_without_venue_authority(tmp_path):
    result=run(source(),tmp_path/'study')
    calibration=result['calibration']
    assert calibration['selected_profile_ms']==25
    assert result['selected_profile']==25
    assert calibration['heldout']['paired_orders']==1
    assert calibration['heldout']['quantity_path_error_btc_ms']=='0'
    assert result['queue_model_calibrated'] is False and result['venue_receipts_verified'] is False
    assert calibration['observed_provenance']['kind']=='SYNTHETIC_TEST_FIXTURE'
    assert result['simulations_attempted']==6
    assert result['trials_attempted']==6 and result['full_window_trials_attempted']==3
    selection=verified(tmp_path/'study/calibration/selection.json')
    assert selection['selected_profile_ms']==25
    assert len(list((tmp_path/'study/calibration').glob('fit-*.started.json')))==3


def test_future_receipts_change_heldout_error_but_never_the_fitted_profile(tmp_path):
    data=source();baseline=run(data,tmp_path/'first')
    modified=deepcopy(data)
    modified['calibration']['observed']['orders'][0]['events'][-1]['received_ms']+=10
    changed=run(modified,tmp_path/'second')
    assert changed['calibration']['selected_profile_ms']==baseline['calibration']['selected_profile_ms']==25
    assert [t['fit'] for t in changed['calibration']['trials']]==[t['fit'] for t in baseline['calibration']['trials']]
    assert changed['calibration']['heldout']['receive_time_absolute_error_ms']==10


def test_tail_replay_failure_cannot_remove_the_winning_prefix_or_choose_another_profile(tmp_path,monkeypatch):
    from tools import passive_execution_study as module
    from tools import passive_receipt_study as fitting
    actual=module.run_profile;calls=[]
    def wrapper(tape,actions,profile,**kwargs):
        calls.append((tape.end_ms,profile))
        if tape.end_ms>BASE+1120:
            assert (tmp_path/'study/calibration/selection.json').exists()
            if profile==25:raise ValueError('fixture tail failure')
        return actual(tape,actions,profile,**kwargs)
    monkeypatch.setattr(module,'run_profile',wrapper);monkeypatch.setattr(fitting,'run_profile',wrapper)
    result=run(source(),tmp_path/'study')
    assert calls[:3]==[(BASE+1120,p) for p in (0,25,100)]
    assert result['selected_profile']==25
    assert result['calibration']['status']=='HELDOUT_REPLAY_FAILED'
    assert result['calibration']['heldout'] is None


def test_missing_receipts_and_declared_gap_do_not_create_a_zero_error_pass(tmp_path):
    data=source();data['calibration']['observed']['orders']=[]
    result=run(data,tmp_path/'empty')
    assert result['selected_profile'] is None
    assert result['calibration']['status']=='INSUFFICIENT_PREFIX_RECEIPTS'
    data=source();data['calibration']['observed']['coverage']['gaps']=[{'start_ms':BASE+1080,'end_ms':BASE+1090}]
    result=run(data,tmp_path/'gap')
    assert result['selected_profile'] is None
    assert result['calibration']['status']=='INCOMPLETE_PREFIX_RECEIPT_COVERAGE'


def test_imported_calibration_uses_existing_input_version_and_console_workflow(tmp_path,monkeypatch):
    from tools import profitability_input_workflow as workflow
    database=tmp_path/'data/cache/ai_factory/factory.db'
    with FactoryStore(database) as store:
        store.create({'attempt':1,'mode':'baseline','budget':3,'objective':'BTC'});store.update(1,'COMPLETED',{})
        store.add_profitability_input(1,'passive',source(),'test')
    monkeypatch.setattr(workflow,'runtime_state_root',lambda:tmp_path)
    result=workflow.run_input_version({'attempt':1,'input_kind':4,'revision':1})
    assert result['p5_queue']['calibration']['selected_profile_ms']==25
    view=profitability_payload(tmp_path,1)['p5_queue']
    assert view['calibration']['heldout']['paired_orders']==1
    assert view['calibration']['heldout']['rows'][0]['path']


def test_equal_prefix_scores_leave_parameters_unidentified(tmp_path):
    data=source();data['actions'].append({**data['actions'][0],'order_id':'second'})
    observed=data['calibration']['observed'];first=deepcopy(observed['orders'][0])
    first['events']=first['events'][:1]
    second=deepcopy(first);second['order_id']='second';second['events'][0]['receipt_id']='second-ack'
    first['events'][0]['venue_ms']=first['events'][0]['received_ms']=BASE+1060
    observed['orders']=[first,second];observed['action_hash']=digest(data['actions'])
    result=run(data,tmp_path/'equal')
    assert result['selected_profile'] is None
    assert result['calibration']['status']=='AMBIGUOUS_PREFIX_FIT'
    assert result['calibration']['equally_best_profiles_ms']==[0,25]


def test_a_prefix_failure_is_not_silently_removed_from_parameter_selection(tmp_path,monkeypatch):
    from tools import passive_receipt_study as fitting
    actual=fitting.run_profile
    def fail(tape,actions,profile,**kwargs):
        if profile==25:raise ValueError('prefix fixture failure')
        return actual(tape,actions,profile,**kwargs)
    monkeypatch.setattr(fitting,'run_profile',fail)
    result=run(source(),tmp_path/'failure')
    assert result['selected_profile'] is None
    assert result['calibration']['status']=='INCOMPLETE_PREFIX_FITS'
    assert result['status']=='QUEUE_RECEIPT_DIAGNOSTICS_WITH_FAILURES'
    assert len(list((tmp_path/'failure/calibration').glob('fit-*.started.json')))==3
