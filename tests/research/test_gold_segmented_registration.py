"""One original registered producer, full segmented input and readonly recovery."""
import base64
import copy
import hashlib
import json
import time

import pytest

from tests.tools.test_gold_segmented_replay import funding_arguments, segmented_arguments
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments


def request(tmp_path, monkeypatch, technical_arguments, *, portfolio=False, declared=False, funding=False,
            long_declared=False):
    from tools import gold_bar_experiment, gold_bar_research
    if funding:
        args = funding_arguments(tmp_path, monkeypatch)
    else:
        args, _, _ = segmented_arguments(tmp_path, monkeypatch)
    completed = args['continuation_segments'][-1]['capture']['exchange_after']['received_at']+1000
    monkeypatch.setattr(time, 'time_ns', lambda: completed*1_000_000)
    source = copy.deepcopy(technical_arguments['release_identity'])
    for module in (gold_bar_experiment, gold_bar_research):
        monkeypatch.setattr(module, 'source_identity', lambda: source)
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    root = tmp_path/'data/cache/gold_bar_research/requests/1'
    (root/'journal').mkdir(parents=True)
    for name, raw in args['journal_files'].items():
        (root/'journal'/name).write_bytes(base64.b64decode(raw))
    (root/'quotes.json').write_text(json.dumps(args['capture']))
    raw = json.dumps(args['continuation_segments']).encode()
    (root/'segments.json').write_bytes(raw)
    plan = dict(quantity='0.1', initial_collateral='1000', fee_rate='0.001',
        release_identity=source, register_original_trials=True,
        segmented_input=dict(schema=args['input_schema'], segments_sha256=hashlib.sha256(raw).hexdigest()))
    if funding:
        raw = json.dumps(args['observed_funding_records']).encode()
        (root/'funding.json').write_bytes(raw)
        plan.update(settlement_window_ms=1000,
            observed_funding=dict(settlement_window_ms=1000, records_sha256=hashlib.sha256(raw).hexdigest()))
    if portfolio:
        plan['portfolio_plan'] = dict(quantities={n:'0.1' for n in ('channel','trend','reversion')},
                                      max_notional='100')
    if declared:
        from quantos.contracts.gold_forward_evaluation_design import declare
        message = json.loads(base64.b64decode(args['journal_files']['messages.jsonl']))
        close = json.loads(message['raw'])['data']['k']['T']+1
        configuration = {n:plan[n] for n in ('quantity','initial_collateral','fee_rate',
            'portfolio_plan','settlement_window_ms') if n in plan}
        plan['forward_design'] = declare(program_id=1, attempt=1, source_identity=source,
            configuration=configuration, first_close_ms=close, window_bars=2,
            declared_at_ms=close-1_100_000, selected_candidate='channel', proposal_sha256='a'*64,
            long_window=long_declared)
    (root/'plan.json').write_text(json.dumps(plan))
    return root


@pytest.mark.parametrize(('portfolio','declared','funding'), [
    (False,False,False), (True,False,False), (False,True,False), (True,True,False), (True,True,True)])
def test_actual_queue_original_archive_population_and_recovery_keep_segmented_account(
        tmp_path, monkeypatch, technical_arguments, portfolio, declared, funding):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from tools import registered_research_job as job, registered_research_run as native
    from tests.research.test_registered_research_job import queued
    from tools import registered_gold_collection as bridge
    root = request(tmp_path, monkeypatch, technical_arguments, portfolio=portfolio,
                   declared=declared, funding=funding)
    original = native._native_run
    calls = []
    def observed(**kwargs):
        producer = kwargs['execute']
        def execute():
            calls.append(1)
            return producer()
        return original(**{**kwargs, 'execute':execute})
    monkeypatch.setattr(native, '_native_run', observed)
    registry, queue = queued(tmp_path)
    uid = queue.submit('gold_bar_research', {'attempt':1}, 1, invocation_id='segmented-gold')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
    assert calls == [1]
    project, database = job.workspace_paths()
    cut = ProjectResearchEvidenceReader(project, registry_database=database)()
    assert cut['attempt_count'] == (4 if portfolio else 3)
    assert {row['status'] for row in cut['attempts']} == {'PASSED'}
    directory = project/'runs'/bridge.invocation_id(1)
    report = json.loads((directory/'archive/report.json').read_bytes())
    assert report['dataset_identity']['input_segment_count'] == 2
    assert len(report['trials'][0]['simulation']['orders']) == 2
    if funding:
        assert report['trials'][0]['simulation']['reconciliation']['funding'] == '-0.3'
    mirror = root.parent.parent/'results/1'
    for name in ('input.json','report.json','manifest.json','trials.db'):
        assert (directory/'archive'/name).read_bytes() == (mirror/name).read_bytes()
    input_before = (directory/'archive/input.json').read_bytes()
    database_before = database.read_bytes()
    uid = queue.submit('gold_bar_recover', {'attempt':1}, 2, invocation_id='segmented-gold-recover')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
    assert calls == [1] and database.read_bytes() == database_before
    assert (directory/'archive/input.json').read_bytes() == input_before
    assert json.loads(registry.get(uid).metrics['gold_bar_report_json'])['economic_eligible'] is False


def test_fixed_request_segment_digest_and_configuration_cannot_be_replaced(
        tmp_path, monkeypatch, technical_arguments):
    from tools.gold_bar_research import read_arguments
    root = request(tmp_path, monkeypatch, technical_arguments)
    (root/'segments.json').write_bytes((root/'segments.json').read_bytes()+b' ')
    with pytest.raises(ValueError, match='GOLD_SEGMENT_REQUEST_CHANGED'):
        read_arguments(root, original_source=technical_arguments['release_identity'])
