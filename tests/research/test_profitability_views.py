from services.console.api.profitability_views import profitability_payload
from tools.profitability_study import write_new


def test_missing_and_failed_studies_visible_without_false_completion(tmp_path):
    assert profitability_payload(tmp_path,1) is None
    p=tmp_path/'data/cache/profitability/1/p0';p.mkdir(parents=True)
    assert profitability_payload(tmp_path,1)['failures'][0]['reason']=='INCOMPLETE_ATTEMPT'
    write_new(p/'failure.json',{'reason':'INPUT_GAP'})
    assert profitability_payload(tmp_path,1)['failures']==[{'stage':'p0','reason':'INPUT_GAP'}]


def test_receipt_hash_and_economic_boundary_are_enforced(tmp_path):
    p=tmp_path/'data/cache/profitability/1/p0';p.mkdir(parents=True)
    write_new(p/'result.json',{'economic_eligible':True})
    result=profitability_payload(tmp_path,1)
    assert result['p0'] is None and result['failures'][0]['reason']=='UNREADABLE_STUDY_RECEIPT'


def test_console_projection_does_not_claim_original_hash_or_repeat_large_trace(tmp_path):
    p=tmp_path/'data/cache/profitability/1/p0';p.mkdir(parents=True)
    write_new(p/'result.json',{'economic_eligible':False,'metrics':{'net_pnl':-1,'decision_trace':[{'large':'trace'}]}})
    result=profitability_payload(tmp_path,1)['p0']
    assert result['metrics']=={'net_pnl':-1}
    assert 'result_hash' not in result and len(result['artifact_hash'])==64
