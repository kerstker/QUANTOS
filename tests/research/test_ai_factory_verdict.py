import pytest
from tools.btc_factory_research import seal


def test_verdict_metrics_are_bound_to_exact_tool_results_and_missing_values_stay_unknown():
    from services.research.ai_factory_verdict import research_verdict
    original=seal({'model_hash':'a'*64,'outer':{'net_pnl':-12.,'fees':'.3'},'authoritative':False})
    portfolio=seal({'model_hash':'b'*64,'plan_hash':'c'*64,'authoritative':False,'economic_eligible':False,
        'decision':'STOP_NO_ACTIVE_PORTFOLIO_EVIDENCE','comparisons':[{'fee_bps':4,'slippage_bps':5,
            'portfolio_net_pnl':0.,'baseline_net_pnl':-12.,'net_pnl_delta':12.,'portfolio_fills':0}]})
    value=research_verdict(original,portfolio)
    assert value['decision']=='STOP_NO_ACTIVE_PORTFOLIO_EVIDENCE'
    assert value['economic_eligible'] is False and value['scope']=='OUTER_EXPLANATION'
    fact=next(f for f in value['facts'] if f['id']=='primary.net_pnl')
    assert fact['value']==-12. and fact['evidence_hash']==original['result_hash'] and fact['pointer']=='/outer/net_pnl'
    unknown=next(f for f in value['facts'] if f['id']=='actual_venue_costs')
    assert unknown['value'] is None and unknown['status']=='UNKNOWN'
    with pytest.raises(ValueError,match='evidence'):
        research_verdict({**original,'outer':{'net_pnl':100}},portfolio)


def test_missing_portfolio_is_not_inferred_from_single_model_profit():
    from services.research.ai_factory_verdict import research_verdict
    original=seal({'model_hash':'a'*64,'outer':{'net_pnl':100},'authoritative':False})
    value=research_verdict(original,None)
    assert value['decision']=='PORTFOLIO_EVIDENCE_MISSING'
    assert value['profitability_established'] is False


def test_verdict_publication_and_episode_recover_without_overwriting(tmp_path,monkeypatch):
    import numpy as np
    from services.research.ai_factory import HOUR
    from services.research.ai_factory_store import FactoryStore,digest
    from services.research.ai_factory_episodes import ResearchEpisodes
    from tools.ai_quant_factory import execute
    from tools.ai_factory_verdict import run_verdict
    from tools.ai_factory_checkpoint import create_checkpoint,restore_checkpoint
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(tmp_path))
    base=tmp_path/'data/cache/ai_factory';t=np.arange(600,dtype=np.int64)*HOUR
    with FactoryStore(base/'factory.db') as store:
        store.create({'attempt':1,'mode':'baseline','objective':'BTC','budget':3})
        execute(store,1,(t,np.ones((600,5)),np.zeros(600)),
            lambda *a:{'score':0.,'net_pnl':0.,'drawdown':0.,'fills':0},
            {'input_hash':digest('data')},base)
    first=run_verdict({'attempt':1});before=(base/'1/research-verdict.json').read_bytes()
    assert run_verdict({'attempt':1})==first
    assert (base/'1/research-verdict.json').read_bytes()==before
    with ResearchEpisodes(base/'1/research-episodes.db',read_only=True) as episodes:
        assert episodes.verify()['episodes']==4
    create_checkpoint(tmp_path,tmp_path/'archive.zip');restore_checkpoint(tmp_path/'archive.zip',tmp_path/'restored')
    with ResearchEpisodes(tmp_path/'restored/1/research-episodes.db',read_only=True) as episodes:
        assert episodes.verify()['episodes']==4
    from tools.ai_factory_research_records import verify_records
    with FactoryStore(base/'factory.db') as store:originals=store.trials(1)
    originals[0]={**originals[0],'metrics':{'score':999}}
    with pytest.raises(ValueError,match='trial evidence conflict'):verify_records(base/'1',originals)
