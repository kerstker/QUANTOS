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


def account_source():
    from tests.execution.test_passive_account_environment import fixture,BASE
    raw,environment=fixture()
    return {'tape':raw,'actions':[{'timestamp':BASE+1060,'kind':'ORDER','order_id':'fixed-account',
        'side':'BUY','quantity':'0.1','price':'99'}],
        'account':{'environment':environment,'initial_collateral':'1000','fee_rate':'0.001','max_order_notional':'100'}}


def test_account_profiles_publish_canonical_cost_and_equity_without_profit_selection(tmp_path):
    path=tmp_path/'input.json';path.write_text(json.dumps(account_source()))
    result=study(path,tmp_path/'run')
    assert result['status']=='QUEUE_ACCOUNT_REPLAYS_COMPLETED'
    assert result['ledger_applied'] is True and result['selected_profile'] is None
    first=result['trials'][0]['account']
    assert first['reconciliation']['fee']=='0.0099'
    assert first['reconciliation']['ending_equity']=='1000.0901'
    assert first['reconciliation']['residual']=='0'
    assert first['actual_venue_fees'] is None
    assert first['equity_history'] and first['ending_state']['positions']
    assert result['trials'][2]['orders'][0]['status']=='SENT'
    assert result['trials'][2]['account']['reconciliation']['fee']=='0'
    assert result['account_environment']['venue_margin_tiers_verified'] is False
    from tools.btc_factory_research import verified
    from services.execution.backtest_authority import exact_identity_hash
    for profile in (0,25,100):
        artifact=verified(tmp_path/f'run/profile-{profile}.json')
        artifact.pop('result_hash')
        engine_hash=artifact.pop('engine_result_hash')
        assert exact_identity_hash(artifact,name='passive_account_result')==engine_hash
        assert next(t for t in result['trials'] if t['profile_ms']==profile)['account']['result_hash']==engine_hash


def test_optional_account_reaches_versioned_workflow_and_missing_environment_is_rejected(tmp_path,monkeypatch):
    database=tmp_path/'data/cache/ai_factory/factory.db'
    with FactoryStore(database) as store:
        store.create({'attempt':1,'mode':'baseline','budget':3,'objective':'BTC'});store.update(1,'COMPLETED',{})
        store.add_profitability_input(1,'passive',account_source(),'test')
        bad=account_source();del bad['account']['environment']['before']['funding']
        with pytest.raises(ValueError):store.add_profitability_input(1,'passive',bad,'test')
    monkeypatch.setattr(workflow,'runtime_state_root',lambda:tmp_path)
    result=workflow.run_input_version({'attempt':1,'input_kind':4,'revision':1})
    assert result['p5_queue']['ledger_applied'] is True
    assert profitability_payload(tmp_path,1)['p5_queue']['trials'][0]['account']['reconciliation']['residual']=='0'


@pytest.mark.parametrize('normal',['0','1'])
def test_rpi_volume_cannot_deplete_visible_queue_or_manufacture_passive_fills(tmp_path,normal):
    source=account_source()
    for row in source['tape']['messages']:
        packet=json.loads(row['raw_utf8'])
        if packet['stream'].endswith('aggTrade'):
            packet['data']['nq']=normal
            row['raw_utf8']=json.dumps(packet)
    path=tmp_path/'input.json';path.write_text(json.dumps(source))
    result=study(path,tmp_path/'run')
    assert result['ledger_applied'] is True
    assert all(t['fill_count']==0 for t in result['trials'])
    assert all(t['account']['reconciliation']['fee']=='0' for t in result['trials'])
