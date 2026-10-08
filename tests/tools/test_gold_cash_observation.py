import json
from tests.research.test_gold_cash_observation import document
from tests.research.test_gold_trade_observation import document as fills
from tools.gold_cash_observation import main


def test_cli_crosschecks_without_mutating_inputs(tmp_path,capsys):
    cash=tmp_path/'cash.json';fill=tmp_path/'fills.json'
    cash.write_text(json.dumps(document()));fill.write_text(json.dumps(fills()))
    before={p:p.read_bytes() for p in tmp_path.iterdir()}
    assert main(['--cash',str(cash),'--fills',str(fill)])==0
    r=json.loads(capsys.readouterr().out)
    assert r['crosscheck']['commission_checks']['USDT']['status']=='MATCHING_DECLARED_TOTALS'
    assert r['crosscheck']['net_pnl'] is None
    assert len(r['cash_input_sha256'])==len(r['fills_input_sha256'])==64
    assert {p:p.read_bytes() for p in tmp_path.iterdir()}==before


def test_cash_only_and_empty_are_not_zero_pnl(tmp_path,capsys):
    d=document();d['events']=[]
    cash=tmp_path/'cash.json';cash.write_text(json.dumps(d))
    assert main(['--cash',str(cash)])==0
    r=json.loads(capsys.readouterr().out)
    assert r['cash']['status']=='NO_CASH_FACTS'
    assert r['cash']['net_pnl'] is None and r['crosscheck'] is None


def test_mismatch_stays_visible_and_returns_non_success(tmp_path,capsys):
    d=document();d['events'][0]['amount']='-1'
    cash=tmp_path/'cash.json';fill=tmp_path/'fills.json'
    cash.write_text(json.dumps(d));fill.write_text(json.dumps(fills()))
    assert main(['--cash',str(cash),'--fills',str(fill)])==2
    r=json.loads(capsys.readouterr().out)
    assert r['crosscheck']['commission_checks']['USDT']['status']=='MISMATCH'


def test_invalid_input_is_redacted(tmp_path,capsys):
    cash=tmp_path/'cash.json';cash.write_text('{"private":"secret","private":2}')
    assert main(['--cash',str(cash)])==2
    assert json.loads(capsys.readouterr().out)=={'status':'REJECTED','reason':'INVALID_CASH_OR_FILL_INPUT'}
