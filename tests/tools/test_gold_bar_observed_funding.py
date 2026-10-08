import base64
from copy import deepcopy
from decimal import Decimal as D
import hashlib
import json

import pytest

from tests.tools.test_gold_bar_observed_replay import continuous_inputs
from tests.execution.test_perpetual_public_replay import START, sealed
from tools.gold_bar_observed_replay import compare_observed
from tools import gold_bar_experiment as experiment
from tools import gold_bar_research as research


def funding_inputs(tmp_path, monkeypatch):
    journal, capture, _ = continuous_inputs(tmp_path, monkeypatch)
    nominal = START + 10000
    capture['bootstrap']['funding']['payload'][0]['fundingTime'] = nominal - 14_400_000
    for sample in capture['samples']:
        mark = sample['mark']['payload']
        mark['nextFundingTime'] = nominal if mark['time'] < nominal else nominal + 14_400_000
    record = deepcopy(capture['bootstrap']['funding'])
    record.update(requested_at=START + 24000, received_at=START + 25000,
        payload=[dict(symbol='XAUUSDT', fundingTime=nominal + 2, fundingRate='0.01', markPrice='100')])
    record = sealed({'record': record})['record']
    return dict(journal=journal, capture=sealed(capture), release_identity={'fixture': True},
        quantity=D('.1'), initial_collateral=D('1000'), fee_rate=D('.001'),
        observed_funding_records=[record], settlement_window_ms=1000)


def test_three_candidates_share_receipt_bound_original_accounting(tmp_path, monkeypatch):
    args = funding_inputs(tmp_path, monkeypatch)
    result = compare_observed(**args)
    trial = result['trials'][0]
    assert trial['simulation']['reconciliation']['funding'] == '-0.1'
    assert trial['simulation']['reconciliation']['residual'] == '0'
    assert trial['outcome'] == 'FLAT_TERMINAL'
    assert 'FORECAST_TIMING_BOUND_UNVERIFIED' in trial['simulation']['authority_blockers']
    assert result['economic_eligible'] is False
    assert result['dataset_identity']['quote_manifest']['observed_funding']['settlement']['available_at_ms'] == START + 25000
    archived = dict(args)
    journal = archived.pop('journal')
    archived['journal_files'] = {n: base64.b64encode((journal/n).read_bytes()).decode() for n in experiment.JOURNAL_FILES}
    monkeypatch.setattr(experiment, 'source_identity', lambda: args['release_identity'])
    destination = tmp_path/'archive'
    saved = experiment.archive(archived, destination)
    assert experiment.verify(destination)['result_hash'] == saved['result_hash'] == result['result_hash']


def queue_request(tmp_path, monkeypatch):
    args = funding_inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(research, 'runtime_state_root', lambda: tmp_path)
    monkeypatch.setattr(research, 'source_identity', lambda: args['release_identity'])
    request = tmp_path/'data/cache/gold_bar_research/requests/1'
    request.mkdir(parents=True)
    import shutil
    shutil.copytree(args['journal'], request/'journal')
    (request/'quotes.json').write_text(json.dumps(args['capture']))
    raw = json.dumps(args['observed_funding_records']).encode()
    (request/'funding.json').write_bytes(raw)
    plan = {n: str(args[n]) for n in ('quantity', 'initial_collateral', 'fee_rate')}
    plan.update(release_identity=args['release_identity'], observed_funding=dict(
        settlement_window_ms=1000, records_sha256=hashlib.sha256(raw).hexdigest()))
    (request/'plan.json').write_text(json.dumps(plan))
    return request


def test_guarded_queue_freezes_receipts_and_rejects_changed_file(tmp_path, monkeypatch):
    request = queue_request(tmp_path, monkeypatch)
    arguments, _ = research._arguments({'attempt': 1})
    assert arguments['settlement_window_ms'] == 1000
    result = experiment._execute(arguments)
    assert result['trials'][0]['simulation']['reconciliation']['funding'] == '-0.1'
    (request/'funding.json').write_text('[]')
    with pytest.raises(ValueError, match='FUNDING_RECEIPT_BINDING'):
        research._arguments({'attempt': 1})



def test_program_receipt_transfer_preserves_exact_request_binding(tmp_path, monkeypatch):
    from tools.gold_bar_program import _research_plan
    args = funding_inputs(tmp_path, monkeypatch)
    document = dict(args['capture'], funding_observations=args['observed_funding_records'])
    plan = {k: str(args[k]) for k in ('quantity', 'initial_collateral', 'fee_rate')}
    request = tmp_path/'request'
    request.mkdir()
    prepared = _research_plan(request, plan, args['release_identity'], document, 1000)
    raw = (request/'funding.json').read_bytes()
    assert prepared['observed_funding']['records_sha256'] == hashlib.sha256(raw).hexdigest()
    assert json.loads(raw) == args['observed_funding_records']
    # An un-crossed window keeps the old strict path, not a zero funding event.
    assert 'observed_funding' not in _research_plan(request, plan, args['release_identity'],
        dict(funding_observations=[]), 1000)
