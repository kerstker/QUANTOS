import subprocess
import sys
from dataclasses import replace

from services.research.ai_factory_store import FactoryStore
from services.research.run_registry.registry import RunRegistry
from services.research.run_registry.store import ResearchRunStore
from services.research_runner.runner import JobQueue
from services.research_runner.run_kinds import RUN_KINDS
from services.research_runner.factory_campaign import campaign_tick
from tests.research.test_gold_factory_campaign import policy
from tests.tools.test_gold_cost_replay import inputs
from tools.gold_cost_inputs import dump_inputs


def prepare(tmp_path, monkeypatch):
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    raw = dump_inputs(inputs())
    base = tmp_path/'data/cache/ai_factory'
    (base/'gold-inputs').mkdir(parents=True)
    (base/'gold-inputs/1.json').write_bytes(raw)
    with FactoryStore(base/'factory.db') as store:
        store.create_campaign(policy(raw), operator='test', now=0)
    return base


def test_terminal_queue_error_cannot_release_unreconciled_gold_budget(tmp_path, monkeypatch):
    base = prepare(tmp_path, monkeypatch)
    def failed_process(params):
        with FactoryStore(base/'factory.db') as store:
            store.update(params['attempt'], 'RUNNING')
        raise RuntimeError('simulate worker loss before workflow terminal write')
    monkeypatch.setitem(RUN_KINDS, 'gold_factory_research',
                        replace(RUN_KINDS['gold_factory_research'], runner=failed_process))
    registry = RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')))
    queue = JobQueue(registry)
    campaign_tick(tmp_path, queue, registry, now=0)
    queue.join()
    result = campaign_tick(tmp_path, queue, registry, now=1)
    assert result['status'] == 'SUBMITTED_GOLD_RECOVERY'
    queue.join()
    result = campaign_tick(tmp_path, queue, registry, now=2)
    assert result['status'] == 'WAITING_GOLD_RECONCILIATION'
    campaign_tick(tmp_path, queue, registry, now=3600001)
    with FactoryStore(base/'factory.db') as store:
        assert [r['state'] for r in store.campaign_snapshot()['rounds']] == ['RESERVED']


def test_actual_process_exit_preserves_choice_and_resumes_without_model(tmp_path, monkeypatch):
    base = prepare(tmp_path, monkeypatch)
    with FactoryStore(base/'factory.db') as store:
        store.reserve_campaign(1, now=0)
    script = '''
import os
from tools import gold_ai_research, gold_factory_research
from tools.ai_factory_provider import propose
from tests.research.test_ai_factory_provider import LOCAL, Transport
from tests.tools.test_gold_cost_replay import inputs
gold_ai_research.source_identity = lambda: inputs()['release_identity']
gold_ai_research.propose = lambda *a, **k: propose(*a, **k, environ=LOCAL,
    transport=Transport({'candidate_id': 2, 'reason': 'crash test'}))
gold_ai_research.archive_experiment = lambda *a, **k: os._exit(77)
gold_factory_research.run_session({'attempt': 1})
'''
    child = subprocess.run([sys.executable, '-c', script], capture_output=True, timeout=20)
    assert child.returncode == 77, child.stderr.decode()[-2000:]
    with FactoryStore(base/'factory.db') as store:
        assert store.get(1)['state'] == 'RUNNING'
        assert store.trials(1) == []
    from tools import gold_ai_research, gold_cost_research
    monkeypatch.setattr(gold_ai_research, 'source_identity', lambda: inputs()['release_identity'])
    monkeypatch.setattr(gold_cost_research, 'source_identity', lambda: inputs()['release_identity'])
    def forbidden(*a, **k):
        raise AssertionError('must not call model twice')
    monkeypatch.setattr(gold_ai_research, 'propose', forbidden)
    def lost_worker(params):
        raise RuntimeError('the separately tested worker exited with code 77')
    monkeypatch.setitem(RUN_KINDS, 'gold_factory_research',
                        replace(RUN_KINDS['gold_factory_research'], runner=lost_worker))
    registry = RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')))
    queue = JobQueue(registry)
    campaign_tick(tmp_path, queue, registry, now=0)
    queue.join()
    recovery = campaign_tick(tmp_path, queue, registry, now=1)
    assert recovery['status'] == 'SUBMITTED_GOLD_RECOVERY'
    queue.join()
    run = registry.get(recovery['run_id'])
    assert run.status == 'done', run.error
    assert run.metrics['candidate_id'] == 2
    campaign_tick(tmp_path, queue, registry, now=2)
    with FactoryStore(base/'factory.db') as store:
        assert store.trials(1)[0]['candidate_id'] == 2
        assert store.campaign_snapshot()['rounds'][0]['state'] == 'COMPLETED'
        # The child and recovery persist real wall-clock timestamps. The next
        # round must follow those observations as well as the synthetic cooldown.
        first = store.get(1)
        next_at = max(3600002, first['updated'] + 1)
        second = store.reserve_campaign(1, now=next_at)
        config = store.get(second['attempt'])['config']
        assert 2 not in config['remaining_candidates']
        memory = config['research_memory']
        assert memory['as_of_ms'] == next_at
        assert memory['records'][0]['trial_id'] == first['result']['trial_id']
        assert memory['records'][0]['candidate_id'] == 2
        assert memory['records'][0]['known_at_ms'] <= next_at


def test_pause_prevents_automatic_recovery_dispatch(tmp_path, monkeypatch):
    base = prepare(tmp_path, monkeypatch)
    def lost(params):
        with FactoryStore(base/'factory.db') as store:
            store.update(params['attempt'], 'RUNNING')
        raise RuntimeError('lost worker')
    monkeypatch.setitem(RUN_KINDS, 'gold_factory_research',
                        replace(RUN_KINDS['gold_factory_research'], runner=lost))
    registry = RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')))
    queue = JobQueue(registry)
    campaign_tick(tmp_path, queue, registry, now=0)
    queue.join()
    with FactoryStore(base/'factory.db') as store:
        store.control_campaign(1, 'pause', operator='test')
    assert campaign_tick(tmp_path, queue, registry, now=1)['status'] == 'WAITING_CAMPAIGN_CONTROL'
    assert len(registry.list()) == 1
