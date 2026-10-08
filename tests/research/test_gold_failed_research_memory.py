import hashlib
import json

import pytest

from services.research.ai_factory_store import FactoryStore
from tests.research.test_gold_factory_campaign import policy
from tests.research.test_ai_factory_provider import LOCAL, Transport
from tests.tools.test_gold_cost_replay import inputs
from tools.ai_factory_provider import propose
from tools.gold_cost_inputs import dump_inputs


@pytest.mark.parametrize('damage', [False, True])
@pytest.mark.parametrize('failure_stage', ['proposal', 'cost'])
def test_failed_experiment_is_frozen_and_verified_before_next_model(tmp_path, monkeypatch, damage, failure_stage):
    from tools import gold_ai_research, gold_cost_research
    from tools.gold_factory_research import run_session
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    arguments = inputs()
    monkeypatch.setattr(gold_ai_research, 'source_identity', lambda: arguments['release_identity'])
    monkeypatch.setattr(gold_cost_research, 'source_identity', lambda: arguments['release_identity'])
    raw = dump_inputs(arguments)
    base = tmp_path/'data/cache/ai_factory'
    (base/'gold-inputs').mkdir(parents=True)
    (base/'gold-inputs/1.json').write_bytes(raw)
    memories = []
    def choose(objective, remaining, history, **kwargs):
        memories.append(kwargs.get('research_memory'))
        if failure_stage == 'proposal' and len(memories) == 1:
            raise ValueError('synthetic proposal failure')
        return propose(objective, remaining, history, **kwargs, environ=LOCAL,
                       transport=Transport({'candidate_id': remaining[0], 'reason': 'next bounded experiment'}))
    monkeypatch.setattr(gold_ai_research, 'propose', choose)
    with FactoryStore(base/'factory.db') as store:
        store.create_campaign(policy(raw), operator='test', now=10**15)
        store.reserve_campaign(1, now=10**15)
    original_archive = gold_ai_research.archive_experiment
    def fail(*args, **kwargs):
        raise ValueError('synthetic cost experiment failure')
    monkeypatch.setattr(gold_ai_research, 'archive_experiment', fail)
    expected = 'research role failed; receipt retained' if failure_stage == 'proposal' else 'synthetic cost experiment failure'
    with pytest.raises(ValueError, match=expected):
        run_session({'attempt': 1})
    monkeypatch.setattr(gold_ai_research, 'archive_experiment', original_archive)
    with FactoryStore(base/'factory.db') as store:
        store.finish_campaign_round(1, 0, 'FAILED', now=10**15+1)
        store.reserve_campaign(1, now=10**15+3600001)
        config = store.get(2)['config']
        assert (0 not in config['remaining_candidates']) == (failure_stage == 'cost')
        memory = config.get('research_memory')
        assert memory is not None
        assert memory['schema'] == 'GOLD_RESEARCH_MEMORY_V2'
        assert memory['records'][0]['outcome'] == 'RESEARCH_FAILED'
        assert memory['records'][0]['candidate_id'] == (0 if failure_stage == 'cost' else None)
        assert 'trial_id' not in memory['records'][0]
    original = (base/'gold/results/1/input.json').read_bytes()
    if damage:
        (base/'gold/results/1/input.json').write_text('{}')
        with pytest.raises(ValueError, match='memory source mismatch'):
            run_session({'attempt': 2})
        assert len(memories) == 1
        assert not (base/'gold/results/2').exists()
    else:
        result = run_session({'attempt': 2})
        assert result['status'] == 'COMPLETED'
        assert memories == [None, memory]
        request = json.loads((base/'gold/results/2/request.json').read_bytes())
        assert request['research_memory'] == memory
        assert hashlib.sha256((base/'gold/results/1/input.json').read_bytes()).digest() == hashlib.sha256(original).digest()
