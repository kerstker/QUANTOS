import pytest
from services.research.profitability_trajectory import compare_trajectories


def trajectory(filled='0.4',at=20):
    return {'action_hash':'a'*64,'symbol':'BTCUSDC','orders':[{'id':'one','quantity':'1','side':'BUY',
       'events':[{'at_ms':10,'state':'ACK','filled':'0'}, {'at_ms':at,'state':'CANCELLED','filled':filled}]}]}


def test_partial_fill_and_cancellation_mismatch_is_quantified_not_profit_tuned():
    report=compare_trajectories(trajectory(),trajectory('0.6',25))
    assert report['quantity_absolute_error']=='0.2'
    assert report['state_time_absolute_error_ms']==5
    assert not report['venue_receipts_verified'] and report['economic_eligible'] is False


def test_different_actions_and_decreasing_cumulative_fills_rejected():
    with pytest.raises(ValueError):compare_trajectories(trajectory(),{**trajectory(),'action_hash':'b'*64})
    bad=trajectory();bad['orders'][0]['events'][0]['filled']='0.8'
    with pytest.raises(ValueError):compare_trajectories(bad,trajectory())


def test_missing_trajectory_has_failure_receipt_without_simulated_real_fills(tmp_path):
    from tools.profitability_trajectory_import import import_trajectories
    from tools.btc_factory_research import verified
    with pytest.raises(ValueError):import_trajectories(tmp_path/'missing.json',tmp_path/'out')
    report=verified(tmp_path/'out/failure.json')
    assert report['stage']=='P5' and report['input_sha256'] is None
    assert not report['economic_eligible']
