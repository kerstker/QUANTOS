import hashlib
import json

from tests.research.test_gold_trade_windows import tape
from tools.gold_signal_replay import main


def archive():
    return {'schema':'GOLD_SIGNAL_REPLAY_INPUT_V1','start_ms':1000,
        'rule':{'window_ms':1000,'max_age_ms':100,'sell_threshold':'10','confirm_windows':2},
        'zone':{'lower':'99','upper':'100','registered_at_ms':500,'expires_at_ms':10000},
        'decision_times_ms':[2010,3010],'packets':tape()}


def test_cli_replays_and_preserves_all_decisions_without_fills(tmp_path,capsys):
    path=tmp_path/'input.json'; raw=json.dumps(archive()).encode(); path.write_bytes(raw)
    assert main(['--input',str(path)])==0
    result=json.loads(capsys.readouterr().out)
    assert result['input_sha256']==hashlib.sha256(raw).hexdigest()
    assert [r['decision']['state'] for r in result['records']]==['CONFIRMING','SIGNALLED']
    assert result['records'][1]['decision']['entry_price'] is None
    assert result['net_pnl'] is None and result['authoritative'] is False
    assert path.read_bytes()==raw


def test_missing_window_produces_explicit_failure_and_keeps_prefix(tmp_path,capsys):
    d=archive(); d['packets']=d['packets'][:4]
    path=tmp_path/'input.json'; path.write_text(json.dumps(d))
    assert main(['--input',str(path)])==2
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='INPUT_INCOMPLETE'
    assert len(result['records'])==1 and result['failed_window_start_ms']==2000


def test_unknown_schema_or_fields_cannot_be_silently_interpreted(tmp_path,capsys):
    d=archive(); d['comex_confirmation']=True
    path=tmp_path/'input.json'; path.write_text(json.dumps(d))
    assert main(['--input',str(path)])==2
    assert json.loads(capsys.readouterr().out)['status']=='REJECTED'
