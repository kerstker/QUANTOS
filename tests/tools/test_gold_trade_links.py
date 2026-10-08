import json

from services.research.gold_trade_links import report_digest
from tests.research.test_gold_trade_links import inputs
from tests.tools.test_gold_signal_replay import archive
from tools.gold_signal_replay import archive_replay
from tools.gold_trade_links import main


def files(root):
    f, _, annotations = inputs()
    result = archive_replay(json.dumps(archive()).encode(), root/'replay')
    annotations['replay_sha256'] = report_digest(result)
    (root/'fills.json').write_text(json.dumps(f))
    (root/'annotations.json').write_text(json.dumps(annotations))
    return ['--fills',str(root/'fills.json'),'--replay-dir',str(root/'replay'),
            '--annotations',str(root/'annotations.json')]


def test_cli_verifies_replay_before_linking(tmp_path,capsys):
    args = files(tmp_path)
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['trades'][0]['status'] == 'DECLARED_LINK'
    assert result['replay_verification'] == 'VERIFIED_LOCAL_REPLAY'
    assert result['strategy_pnl'] is None
    assert result['review_counts']['declared_entry_fills'] == 1
    assert result['signals_without_declared_entry'] == []
    assert result['trades'][0]['position_effect'] == 'UNKNOWN'


def test_tampered_replay_cannot_acquire_a_trade_association(tmp_path,capsys):
    args = files(tmp_path)
    (tmp_path/'replay/report.json').write_text('{}')
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out) == {
        'status':'REJECTED','reason':'INVALID_OR_UNVERIFIABLE_LINK_INPUT'}
