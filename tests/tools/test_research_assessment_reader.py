from tools import research_assessment_reader as reader


def test_forward_failure_does_not_hide_rf2_or_create_positive_evidence(monkeypatch):
 monkeypatch.delenv('QUANTOS_RF2_EVIDENCE_ROOT',raising=False)
 monkeypatch.setenv('QUANTOS_RF3_OBSERVATION_ROOT','/tmp/fictional-observer')
 def broken(*a,**kw):raise ValueError('private path details')
 monkeypatch.setattr(reader,'forward_status',broken)
 r=reader.read_assessment()
 assert r['forward_observation']=={'status':'DATA_BLOCKED'}
 assert all(d['verdict']=='UNKNOWN' for d in r['dimensions'])
 assert 'private path' not in str(r)


def test_observation_gaps_never_promote_research_dimension(monkeypatch):
 monkeypatch.delenv('QUANTOS_RF2_EVIDENCE_ROOT',raising=False)
 monkeypatch.setenv('QUANTOS_RF3_OBSERVATION_ROOT','/tmp/fictional-observer')
 monkeypatch.setattr(reader,'forward_status',lambda *a,**kw:dict(plan_hash='a'*64,identity_matches=True,
 observed_ms=1000,published=[0],gaps=[1],pending=list(range(2,14)),slots=[]))
 r=reader.read_assessment()
 assert r['forward_observation']['status']=='BLOCKED'
 assert r['overall']['verdict']=='RESEARCH_EVIDENCE_INCOMPLETE'
 assert next(d for d in r['dimensions'] if d['id']=='forward_and_testnet_evidence')['verdict']=='UNKNOWN'
