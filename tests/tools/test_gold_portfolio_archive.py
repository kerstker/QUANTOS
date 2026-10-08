import base64
import json
import pytest
from tools import gold_bar_experiment as experiment
from tools.gold_bar_review import review_request, retained_request
from tests.tools.test_gold_bar_observed_funding import funding_inputs


def arguments(tmp_path, monkeypatch):
    args = funding_inputs(tmp_path, monkeypatch)
    journal = args.pop('journal')
    args['journal_files'] = {n:base64.b64encode((journal/n).read_bytes()).decode() for n in experiment.JOURNAL_FILES}
    args['portfolio_plan'] = dict(quantities={n:'0.1' for n in ('channel','trend','reversion')}, max_notional='100')
    monkeypatch.setattr(experiment,'source_identity',lambda:args['release_identity'])
    return args


def test_portfolio_is_fourth_registered_trial_and_reproduces(tmp_path, monkeypatch):
    args=arguments(tmp_path,monkeypatch)
    destination=tmp_path/'experiment'
    saved=experiment.archive(args,destination)
    assert len(saved['trial_ids'])==4
    checked=experiment.verify(destination)
    assert checked['result_hash']==saved['result_hash']
    review=review_request(destination)
    assert review==retained_request(destination)
    assert review['candidates'][-1]['candidate']=='net_portfolio'
    report=json.loads((destination/'report.json').read_text())
    assert report['portfolio']['simulation']['reconciliation']['residual']=='0'
    assert report['portfolio']['submission_enabled'] is False
    assert experiment.resume_archive(args,destination)['trial_ids']==saved['trial_ids']
    modified=dict(args,portfolio_plan=dict(args['portfolio_plan'],max_notional='200'))
    with pytest.raises(ValueError,match='INPUT_CHANGED'):
        experiment.resume_archive(modified,destination)
