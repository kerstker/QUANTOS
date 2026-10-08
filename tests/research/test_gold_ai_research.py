import json

import pytest

from tests.tools.test_gold_cost_replay import inputs
from tests.research.test_ai_factory_provider import LOCAL, Transport
from tools.ai_factory_provider import propose
from tools.gold_cost_inputs import dump_inputs


def setup(tmp_path, monkeypatch):
    from tools import gold_ai_research as tool, gold_cost_research
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    args = inputs()
    monkeypatch.setattr(gold_cost_research, 'source_identity', lambda: args['release_identity'])
    monkeypatch.setattr(tool, 'source_identity', lambda: args['release_identity'])
    request = tmp_path/'data/cache/gold_ai_research/requests/1'
    request.mkdir(parents=True)
    (request/'input.json').write_bytes(dump_inputs(args))
    (request/'request.json').write_text(json.dumps({'objective': 'Test gold absorption after costs'}))
    calls = []
    def proposal(*args, **kwargs):
        calls.append(1)
        return propose(*args, **kwargs, environ=LOCAL,
                       transport=Transport({'candidate_id': 0, 'reason': 'test hypothesis'}))
    monkeypatch.setattr(tool, 'propose', proposal)
    return tool, calls, tmp_path/'data/cache/gold_ai_research/results/1'


def test_proposal_is_bound_to_formal_trial_and_review(tmp_path, monkeypatch):
    tool, calls, dest = setup(tmp_path, monkeypatch)
    result = tool.run_research({'attempt': 1})
    assert result['outcome'] == 'FLAT_TERMINAL'
    assert result['authoritative'] is False and len(calls) == 1
    manifest = json.loads((dest/'trial/manifest.json').read_bytes())
    review = json.loads((dest/'trial/independent-request.json').read_bytes())
    assert manifest['trial_id'] == result['trial_id'] == review['trial_id']
    assert review['files']['research-origin.json'] == manifest['research_origin_sha256']
    original = (dest/'trial/trials.db').read_bytes()
    with pytest.raises(FileExistsError):
        tool.run_research({'attempt': 1})
    assert len(calls) == 1 and (dest/'trial/trials.db').read_bytes() == original


def test_model_failure_is_retained_without_starting_a_cost_trial(tmp_path, monkeypatch):
    tool, _, dest = setup(tmp_path, monkeypatch)
    def fail(*args, **kwargs):
        raise ValueError('model unavailable')
    monkeypatch.setattr(tool, 'propose', fail)
    with pytest.raises(ValueError):
        tool.run_research({'attempt': 1})
    assert (dest/'research/roles/0.started.json').is_file()
    assert json.loads((dest/'result.json').read_bytes())['status'] == 'FAILED'
    assert not (dest/'trial').exists()


def test_interruption_keeps_consumed_model_reservation(tmp_path, monkeypatch):
    tool, _, dest = setup(tmp_path, monkeypatch)
    def stop(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(tool, 'propose', stop)
    with pytest.raises(KeyboardInterrupt):
        tool.run_research({'attempt': 1})
    assert (dest/'research/roles/0.started.json').is_file()
    assert not (dest/'result.json').exists()
    with pytest.raises(FileExistsError):
        tool.run_research({'attempt': 1})


def test_review_rejects_changed_origin(tmp_path, monkeypatch):
    from tools.gold_cost_review import review_request
    tool, _, dest = setup(tmp_path, monkeypatch)
    tool.run_research({'attempt': 1})
    path = dest/'trial/research-origin.json'
    origin = json.loads(path.read_bytes())
    origin['proposal']['candidate_id'] = 5
    path.write_text(json.dumps(origin))
    with pytest.raises(ValueError):
        review_request(dest/'trial')


def test_actual_queue_links_model_choice_and_trial(tmp_path, monkeypatch):
    from services.research.run_registry.store import ResearchRunStore
    from services.research.run_registry.registry import RunRegistry
    from services.research_runner.runner import JobQueue
    _, calls, dest = setup(tmp_path, monkeypatch)
    registry = RunRegistry(ResearchRunStore(str(tmp_path/'ai-runs.db')))
    queue = JobQueue(registry)
    uid = queue.submit('gold_ai_research', {'attempt': 1}, 1, invocation_id='gold-ai-one')
    queue.join()
    run = registry.get(uid)
    assert run.status == 'done', run.error
    assert run.metrics['candidate_id'] == 0
    assert run.metrics['residual'] == '0'
    assert run.verdict == 'TECHNICAL_VALIDATION_ONLY'
    assert run.metrics['trial_id'] == json.loads((dest/'trial/manifest.json').read_bytes())['trial_id']
    assert queue.submit('gold_ai_research', {'attempt': 1}, 2, invocation_id='gold-ai-one') == uid
    assert len(calls) == 1


@pytest.mark.parametrize('change', ['base-cost', 'base-packet', 'candidate', 'catalog'])
def test_rehashed_origin_cannot_relabel_selected_input(tmp_path, monkeypatch, change):
    import hashlib
    from quantos.local_artifacts import with_result_hash
    from tools.gold_ai_origin import encode
    from tools.gold_cost_inputs import load_inputs
    from tools.gold_cost_review import review_request
    from dataclasses import replace
    from decimal import Decimal
    tool, _, dest = setup(tmp_path, monkeypatch)
    tool.run_research({'attempt': 1})
    path = dest/'trial/research-origin.json'
    origin = json.loads(path.read_bytes())
    if change.startswith('base-'):
        base = load_inputs(encode(origin['base_input']))
        if change == 'base-cost':
            base['costs'] = replace(base['costs'], fee_rate=Decimal('.002'))
        else:
            base['document']['packets'].pop()
        origin['base_input'] = json.loads(dump_inputs(base))
    else:
        proposal = origin['proposal']
        if change == 'candidate':
            proposal['candidate_id'] = 5
            proposal['roles'][0]['candidate_id'] = 5
        else:
            proposal['roles'][0]['candidate_catalog'] = 'HOURLY_MODELS'
        origin['proposal'] = with_result_hash({k: v for k, v in proposal.items() if k != 'result_hash'})
    raw = encode(origin)
    path.write_bytes(raw)
    manifest_path = dest/'trial/manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['research_origin_sha256'] = hashlib.sha256(raw).hexdigest()
    manifest_path.write_bytes(encode(manifest))
    with pytest.raises(ValueError, match='GOLD_AI_'):
        review_request(dest/'trial')
