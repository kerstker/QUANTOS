from services.console.api.gold_factory_results import results
from tests.tools.test_gold_cost_replay import inputs
from tools.gold_cost_research import archive_experiment
from tools.gold_cost_inputs import dump_inputs


def test_projects_original_cost_report_and_refuses_changed_report(tmp_path, monkeypatch):
    from tools import gold_cost_research
    args = inputs()
    monkeypatch.setattr(gold_cost_research, 'source_identity', lambda: args['release_identity'])
    destination = tmp_path/'data/cache/ai_factory/gold/results/1/trial'
    summary = archive_experiment(dump_inputs(args), destination, attempt=1)
    session = {'attempt': 1, 'config': {'market': 'XAUUSDT'}, 'state': 'COMPLETED',
               'result': {**summary, 'candidate_id': 0}}
    item = results(tmp_path, [session])[0]
    assert item['report_status'] == 'AVAILABLE'
    assert item['costs'] == summary['reconciliation']
    assert item['outcome'] == summary['outcome']
    assert item['recorded_verification_status'] == summary['verification_status']
    assert item['economic_eligible'] is False
    destination.joinpath('report.json').write_text('{}')
    damaged = results(tmp_path, [session])[0]
    assert damaged['report_status'] == 'UNAVAILABLE'
    assert damaged['costs'] is None and damaged['outcome'] is None


def test_failed_and_pending_rounds_are_visible_without_invented_metrics(tmp_path):
    rows = [{'attempt': n, 'config': {'market': 'XAUUSDT'}, 'state': state, 'result': {}}
            for n, state in enumerate(('FAILED', 'RUNNING'), 1)]
    projected = results(tmp_path, rows)
    assert [row['state'] for row in projected] == ['FAILED', 'RUNNING']
    assert all(row['costs'] is None and row['candidate'] is None for row in projected)
