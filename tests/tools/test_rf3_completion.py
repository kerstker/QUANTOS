from services.research.rf3_daily_forward import make_plan, DAY_MS, DEADLINE_MS
from tools.rf3_completion import readiness


def plan():return make_plan(1790000000000,'a'*64,'b'*64)


def test_never_evaluates_before_last_account_minute_matures():
 p=plan();at=p['slots'][0]+14*DAY_MS+DEADLINE_MS+60001
 report={'identity_matches':True,'published':list(range(14)),'gaps':[]}
 assert readiness(p,report,at-1)['status']=='WAITING_MATURITY'
 assert readiness(p,report,at)['status']=='READY'


def test_missing_forecast_or_identity_drift_blocks_even_after_maturity():
 p=plan();at=p['slots'][0]+15*DAY_MS
 assert readiness(p,{'identity_matches':True,'published':[0],'gaps':[1]},at)['status']=='BLOCKED_MISSING_PUBLICATIONS'
 assert readiness(p,{'identity_matches':False,'published':list(range(14)),'gaps':[]},at)['status']=='BLOCKED_IDENTITY_DRIFT'


def test_claim_prevents_duplicate_capture_after_interruption(tmp_path,monkeypatch):
 import json
 from tools import rf3_completion as worker
 p=plan();root=tmp_path/'observation';root.mkdir();out=tmp_path/'evaluation';out.mkdir()
 (out/'claim.json').write_text(json.dumps({'plan_hash':p['plan_hash']}))
 monkeypatch.setattr(worker,'_load_plan',lambda _:p)
 monkeypatch.setattr(worker,'observation_status',lambda _:{'identity_matches':True,'published':list(range(14)),'gaps':[]})
 def forbidden(*a,**k):raise AssertionError('must not duplicate capture or evaluation')
 r=worker.advance(root,out,clock=lambda:p['slots'][0]+15*DAY_MS,capture=forbidden,evaluate=forbidden)
 assert r['status']=='BLOCKED_CLAIM_REQUIRES_REVIEW'


def test_waiting_never_claims_or_fetches(tmp_path,monkeypatch):
 from tools import rf3_completion as worker
 p=plan();root=tmp_path/'observation';root.mkdir();out=tmp_path/'evaluation'
 monkeypatch.setattr(worker,'_load_plan',lambda _:p)
 monkeypatch.setattr(worker,'observation_status',lambda _:{'identity_matches':True,'published':[],'gaps':[]})
 def forbidden(*a,**k):raise AssertionError('premature network')
 r=worker.advance(root,out,clock=lambda:p['slots'][0],capture=forbidden)
 assert r['status']=='WAITING_MATURITY' and not (out/'claim.json').exists()


def test_real_archives_evaluate_once_and_tampering_cannot_reuse_completion(captured_window,monkeypatch):
 import shutil
 from tools import rf3_completion as worker
 state,market,out=captured_window
 p=worker._load_plan(state)
 at=p['slots'][0]+15*DAY_MS
 monkeypatch.setattr(worker,'observation_status',lambda root:__import__('tools.rf3_daily_forward',fromlist=['status']).status(root,clock=lambda:at))
 calls=[]
 def capture(destination,**kwargs):
  calls.append(kwargs)
  shutil.copytree(market,destination)
 result=worker.advance(state,out,clock=lambda:at,capture=capture)
 assert result['status']=='COMPLETED_TECHNICAL_ONLY'
 assert worker.advance(state,out,clock=lambda:at,capture=capture)['status']=='COMPLETED_TECHNICAL_ONLY'
 assert len(calls)==1
 (out/'evaluation/summary.json').write_text('{}')
 import pytest
 with pytest.raises(ValueError,match='changed'):worker.advance(state,out,clock=lambda:at,capture=capture)


from tests.tools.test_rf3_account_acceptance import collected_window
from tests.tools.test_rf3_cli_acceptance import captured_window
