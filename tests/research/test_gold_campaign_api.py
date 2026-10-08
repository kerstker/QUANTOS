"""The existing campaign endpoint must reach the gold scheduler, not BTC defaults."""
from fastapi.testclient import TestClient
import pytest

from services.research_runner import main
from services.research.ai_factory_store import FactoryStore
from tests.research.test_gold_factory_campaign import policy


def test_gold_campaign_creation_and_feedback_wait_use_existing_store(tmp_path, monkeypatch):
    monkeypatch.setattr(main, '_STATE_ROOT', tmp_path)
    client = TestClient(main.app)
    headers = {'X-Runner-Token': main._RUNNER_TOKEN, 'X-Operator': 'isolated-test'}
    config = dict(policy(b'base'), execution_feedback_mode='after_first_round')
    response = client.post('/factory-campaign', headers=headers, json=dict(config, action='create'))
    assert response.status_code == 200, response.text
    assert response.json()['campaigns'][0]['config'] == config
    with FactoryStore(tmp_path / 'data/cache/ai_factory/factory.db') as store:
        first = store.reserve_campaign(1, now=10**15)
        assert store.get(first['attempt'])['config']['market'] == 'XAUUSDT'
        store.record_trial(first['attempt'], 0, {'candidate_id': 0, 'status': 'PASSED', 'trial_id': 'a' * 64})
        store.update(first['attempt'], 'COMPLETED')
        store.finish_campaign_round(1, 0, 'COMPLETED', now=10**15)
        assert store.reserve_campaign(1, now=10**15 + 3600001) is None
    for action in ('pause', 'resume', 'stop'):
        result = client.post('/factory-campaign', headers=headers, json={'action': action, 'campaign_id': 1})
        assert result.status_code == 200, result.text


@pytest.mark.parametrize('change', [{'trigger': 'drift'}, {'source_ids': ['aqr']},
    {'research_protocol': 'THREE_ROLES_V1'}, {'max_cycles': 7}, {'budget': 3}, {'input_sha256': None}])
def test_gold_endpoint_rejects_mixed_btc_controls(tmp_path, monkeypatch, change):
    monkeypatch.setattr(main, '_STATE_ROOT', tmp_path)
    result = TestClient(main.app).post('/factory-campaign',
        headers={'X-Runner-Token': main._RUNNER_TOKEN, 'X-Operator': 'isolated-test'},
        json=dict(policy(b'base'), action='create', **change))
    assert result.status_code in (409, 422)
