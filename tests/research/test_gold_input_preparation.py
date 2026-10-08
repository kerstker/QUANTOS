import json
import hashlib
import pytest

from services.console.api.gold_input_catalog import catalog
from services.research.run_registry.registry import RunRegistry
from services.research.run_registry.store import ResearchRunStore
from services.research_runner.runner import JobQueue
from tests.tools.test_gold_public_cost_inputs import request
from tools.gold_cost_inputs import dump_inputs, load_inputs
from tools.gold_public_cost_inputs import assemble_inputs


def prepare(tmp_path, monkeypatch):
    from tools import gold_input_preparation
    req = request()
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    monkeypatch.setattr(gold_input_preparation, 'source_identity', lambda: req['release_identity'])
    root = tmp_path/'data/cache/ai_factory/gold-preparation/1'
    root.mkdir(parents=True)
    (root/'quotes.json').write_text(json.dumps(req['capture']))
    (root/'signals.json').write_text(json.dumps(req['document']))
    (root/'template.json').write_bytes(dump_inputs(assemble_inputs(**req)))
    return root


def test_queue_publishes_selectable_snapshot_and_replay_is_idempotent(tmp_path, monkeypatch):
    prepare(tmp_path, monkeypatch)
    registry = RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')))
    queue = JobQueue(registry)
    first = queue.submit('gold_prepare_input', {'attempt': 1}, 1, operator='test')
    queue.join()
    assert registry.get(first).status == 'done', registry.get(first).error
    item = catalog(tmp_path)['items'][0]
    assert item['input_id'] == 1
    path = tmp_path/'data/cache/ai_factory/gold-inputs/1.json'
    raw = path.read_bytes()
    assert load_inputs(raw)['market_identity']['schema'] == 'GOLD_PUBLIC_COST_ASSEMBLY_V1'
    second = queue.submit('gold_prepare_input', {'attempt': 1}, 1, operator='test')
    queue.join()
    assert registry.get(second).status == 'done'
    assert path.read_bytes() == raw
    assert registry.get(first).metrics['input_sha256'] == item['input_sha256']
    # Continue from published bytes through the original scheduler and worker.
    from services.research.ai_factory_store import FactoryStore
    from services.research_runner.factory_campaign import campaign_tick
    from tests.research.test_gold_factory_campaign import policy
    from tests.research.test_ai_factory_provider import LOCAL, Transport
    from tools import gold_ai_research, gold_cost_research
    from tools.ai_factory_provider import propose
    release = load_inputs(raw)['release_identity']
    monkeypatch.setattr(gold_ai_research, 'source_identity', lambda: release)
    monkeypatch.setattr(gold_cost_research, 'source_identity', lambda: release)
    monkeypatch.setattr(gold_ai_research, 'propose', lambda objective, remaining, history, **kwargs:
        propose(objective, remaining, history, **kwargs, environ=LOCAL,
                transport=Transport({'candidate_id': remaining[0], 'reason': 'test published capture'})))
    base = tmp_path/'data/cache/ai_factory'
    with FactoryStore(base/'factory.db') as store:
        store.create_campaign(policy(raw), operator='test', now=0)
    assert campaign_tick(tmp_path, queue, registry, now=0)['status'] == 'SUBMITTED'
    queue.join()
    campaign_tick(tmp_path, queue, registry, now=1)
    from services.console.api.ai_factory_lifecycle import lifecycle_payload
    view = lifecycle_payload(tmp_path)
    assert view['gold_results'][0]['report_status'] == 'AVAILABLE'
    assert view['gold_results'][0]['trial_id']



def test_changed_request_cannot_replace_published_input(tmp_path, monkeypatch):
    from tools.gold_input_preparation import run_prepare
    root = prepare(tmp_path, monkeypatch)
    run_prepare({'attempt': 1})
    path = tmp_path/'data/cache/ai_factory/gold-inputs/1.json'
    original = path.read_bytes()
    signal = json.loads((root/'signals.json').read_text())
    signal['rule']['sell_threshold'] = '11'
    (root/'signals.json').write_text(json.dumps(signal))
    import pytest
    with pytest.raises(ValueError):
        run_prepare({'attempt': 1})
    assert path.read_bytes() == original


def test_incomplete_window_retains_bound_diagnostic_without_publishing(tmp_path, monkeypatch):
    from tools.gold_input_preparation import run_prepare
    root=prepare(tmp_path,monkeypatch)
    signal=json.loads((root/'signals.json').read_bytes())
    signal['packets']=signal['packets'][:1]
    (root/'signals.json').write_text(json.dumps(signal))
    before={name:(root/name).read_bytes() for name in ('quotes.json','signals.json','template.json')}
    for _ in range(2):
        with pytest.raises(ValueError,match='GOLD_SIGNAL_INPUT_INCOMPLETE'):
            run_prepare({'attempt':1})
        report=json.loads((root/'signal-validation.json').read_bytes())
        assert report['status']=='INPUT_INCOMPLETE'
        assert report['reason']=='MISSING_WINDOW_COVERAGE'
        assert report['failed_window_start_ms']==signal['start_ms']
        assert report['scheduled_decision_ms']==signal['decision_times_ms'][0]
        assert report['source_sha256']=={name:hashlib.sha256(raw).hexdigest() for name,raw in before.items()}
        assert report['authoritative'] is False
    assert not (root.parent.parent/'gold-inputs/1.json').exists()
    assert before=={name:(root/name).read_bytes() for name in before}
