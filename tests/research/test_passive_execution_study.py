import json
import pytest

from tests.research.test_passive_execution_input import tape
from services.research.ai_factory_store import FactoryStore
from services.console.api.profitability_views import profitability_payload
from tools.passive_execution_study import study
from tools import profitability_input_workflow as workflow


def source():
    return {'tape':tape(),'actions':[{'timestamp':1060,'kind':'ORDER','order_id':'fixed-a',
        'side':'BUY','quantity':'0.01','price':'99'}]}


def test_all_profiles_are_preregistered_and_none_selected_by_fill_count(tmp_path):
    data=source();path=tmp_path/'input.json';path.write_text(json.dumps(data));output=tmp_path/'run'
    result=study(path,output)
    assert result['status']=='QUEUE_DIAGNOSTICS_COMPLETED'
    assert result['trials_attempted']==3
    assert [r['profile_ms'] for r in result['trials']]==[0,25,100]
    assert result['selected_profile'] is None and result['ledger_applied'] is False
    assert len(list(output.glob('*.started.json')))==3
    assert result['trials'][2]['censored_orders']==1
    with pytest.raises(FileExistsError):study(path,output)


def test_failure_keeps_input_identity_and_registered_plan(tmp_path):
    data=source();data['actions'][0]['timestamp']=1050
    path=tmp_path/'input.json';path.write_text(json.dumps(data));output=tmp_path/'run'
    with pytest.raises(ValueError):study(path,output)
    assert (output/'preregistration.json').exists() and (output/'failure.json').exists()


def test_registered_queue_input_reaches_workflow_and_console_without_p0(tmp_path,monkeypatch):
    database=tmp_path/'data/cache/ai_factory/factory.db'
    with FactoryStore(database) as store:
        store.create({'attempt':1,'mode':'baseline','budget':3,'objective':'BTC'});store.update(1,'COMPLETED',{})
        first=store.add_profitability_input(1,'passive',source(),'test')
        assert store.add_profitability_input(1,'passive',source(),'test')==first
    monkeypatch.setattr(workflow,'runtime_state_root',lambda:tmp_path)
    result=workflow.run_input_version({'attempt':1,'input_kind':4,'revision':1})
    assert result['p5_queue']['status']=='QUEUE_DIAGNOSTICS_COMPLETED'
    view=profitability_payload(tmp_path,1)
    assert view['p5_queue']['trials_attempted']==3
    assert view['p5_queue']['selected_profile'] is None
    assert view['inputs'][0]['result_status']=='QUEUE_DIAGNOSTICS_COMPLETED'
    assert workflow.run_input_version({'attempt':1,'input_kind':4,'revision':1})['p5_queue']['result_hash']


def test_data_only_input_does_not_invent_orders_or_three_trials(tmp_path):
    data=source();data['actions']=[];path=tmp_path/'input.json';path.write_text(json.dumps(data))
    result=study(path,tmp_path/'run')
    assert result['status']=='PUBLIC_INPUT_READY_NO_FIXED_ACTIONS'
    assert result['trials']==[] and result['trials_attempted']==0
