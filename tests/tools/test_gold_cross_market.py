"""Offline three-source input must preserve PIT and failure evidence."""
import json
from dataclasses import asdict
import pytest
from tests.research.test_gold_cross_market import quotes
from tools.gold_cross_market import main


def document():
    rows = [[dict(asdict(q), bid=str(q.bid), ask=str(q.ask)) for q in group] for group in quotes()]
    return dict(schema='GOLD_CROSS_MARKET_INPUT_V1', external_contract='GCZ2026',
        decision_ms=1020, max_age_ms=100, max_skew_ms=20, external_session_open=True,
        local=rows[0], external=rows[1], fx=rows[2])


def run(tmp_path, capsys, d):
    path=tmp_path/'input.json'
    raw=json.dumps(d).encode(); path.write_bytes(raw)
    code=main(['--input',str(path)])
    assert path.read_bytes()==raw
    assert list(tmp_path.iterdir())==[path]
    return code,json.loads(capsys.readouterr().out)


def test_three_source_cli_returns_diagnostic_and_input_binding(tmp_path,capsys):
    code,r=run(tmp_path,capsys,document())
    assert code==0 and r['status']=='ALIGNED_QUOTES_ONLY'
    assert len(r['input_sha256'])==64
    assert r['source_hashes']==['a'*64,'b'*64,'c'*64]
    assert r['causal_driver'] is None and r['trade_intent'] is None
    assert r['external_session_evidence']=='OPERATOR_DECLARED_NOT_VERIFIED'


@pytest.mark.parametrize('case,status', [('missing','INPUT_MISSING'),('stale','INPUT_INVALID'),('closed','EXTERNAL_SESSION_CLOSED')])
def test_unusable_inputs_are_explicit_non_success(tmp_path,capsys,case,status):
    d=document()
    if case=='missing':d['fx']=[]
    if case=='stale':d['decision_ms']=2000
    if case=='closed':d['external_session_open']=False
    code,r=run(tmp_path,capsys,d)
    assert code==2 and r['status']==status
    assert len(r['input_sha256'])==64


@pytest.mark.parametrize('case', ['float','extra','nan','duplicate','nested'])
def test_malformed_input_fails_without_echo(tmp_path,capsys,case):
    d=document()
    if case=='float':d['local'][0]['bid']=4000.0
    if case=='extra':d['secret']='private-value'
    if case=='nan':d['local'][0]['bid']='NaN'
    path=tmp_path/'input.json'
    path.write_text('{"schema":1,"schema":2}' if case=='duplicate' else '['*2000+']'*2000 if case=='nested' else json.dumps(d))
    assert main(['--input',str(path)])==2
    r=json.loads(capsys.readouterr().out)
    assert r=={'status':'REJECTED','reason':'INVALID_CROSS_MARKET_INPUT'}
