"""A displayed research workspace must never silently select another state root."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.console.api.ai_factory import router
from services.research.ai_factory_store import FactoryStore


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    current, archive = tmp_path / 'current', tmp_path / 'archive'
    for root, attempt in ((current, 1), (archive, 2)):
        with FactoryStore(root / 'data/cache/ai_factory/factory.db') as store:
            store.create({'attempt': attempt, 'mode': 'baseline', 'budget': 3, 'objective': root.name})
    config = tmp_path / 'workspaces.json'
    config.write_text(json.dumps({'version': 1, 'workspaces': [
        {'id': 'frozen', 'label': '冻结研究', 'root': str(archive)},
    ]}))
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(current))
    monkeypatch.setenv('QUANTOS_RESEARCH_WORKSPACES_PATH', str(config))
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), current, archive, config


def test_explicit_read_only_workspace_reads_its_own_history_without_mutation(workspaces):
    client, current, archive, _ = workspaces
    files = {p: p.read_bytes() for root in (current, archive) for p in root.rglob('*') if p.is_file()}
    data = client.get('/api/v1/console/ai-factory?workspace=frozen').json()
    assert data['selected']['attempt'] == 2
    assert data['workspace'] == {'id': 'frozen', 'label': '冻结研究', 'writable': False}
    assert str(archive) not in json.dumps(data['workspaces'])
    assert all(p.read_bytes() == content for p, content in files.items())
    assert data['authoritative'] is False and data['read_only'] is True
    current_data = client.get('/api/v1/console/ai-factory').json()
    assert current_data['selected']['attempt'] == 1
    assert current_data['workspace']['writable'] is True


@pytest.mark.parametrize('endpoint', ['ai-factory', 'ai-factory-lifecycle'])
def test_unknown_workspace_never_falls_back_to_current(workspaces, endpoint):
    client, *_ = workspaces
    for name in ('missing', '../../current', '/srv/quantos/state'):
        assert client.get(f'/api/v1/console/{endpoint}', params={'workspace': name}).status_code == 404


def test_lifecycle_uses_the_same_workspace_contract(workspaces):
    client, *_ = workspaces
    data = client.get('/api/v1/console/ai-factory-lifecycle?workspace=frozen').json()
    assert data['workspace']['id'] == 'frozen' and data['workspace']['writable'] is False
    assert data['scheduler_enabled'] is False


@pytest.mark.parametrize('bad', [
    {'version': 2, 'workspaces': []},
    {'version': 1, 'workspaces': [{'id': 'current', 'label': 'override', 'root': '/tmp'}]},
    {'version': 1, 'workspaces': [{'id': 'bad', 'label': 'bad', 'root': 'relative'}]},
])
def test_invalid_registry_is_visible_and_cannot_select_a_fallback(workspaces, bad):
    client, _, _, config = workspaces
    config.write_text(json.dumps(bad))
    assert client.get('/api/v1/console/ai-factory').status_code == 503


def test_unavailable_registered_workspace_is_not_an_empty_factory(workspaces):
    client, _, _, config = workspaces
    data = json.loads(config.read_text())
    data['workspaces'][0]['root'] += '-missing'
    config.write_text(json.dumps(data))
    assert client.get('/api/v1/console/ai-factory?workspace=frozen').status_code == 503
    assert client.get('/api/v1/console/ai-factory').status_code == 200


@pytest.mark.parametrize('endpoint,body', [
    ('factory', {'action': 'create', 'attempt': 3}),
    ('factory-campaign', {'action': 'create', 'campaign_id': 3}),
    ('profitability-input', {'attempt': 3, 'kind': 'basis', 'content': '{}'}),
])
def test_runner_rejects_non_current_workspace_before_any_write(tmp_path, monkeypatch, endpoint, body):
    from services.research_runner import main
    root = tmp_path / 'untouched'
    monkeypatch.setattr(main, '_STATE_ROOT', root)
    client = TestClient(main.app)
    response = client.post('/' + endpoint, json={**body, 'workspace': 'frozen'}, headers={
        'X-Runner-Token': main._RUNNER_TOKEN, 'X-Operator': 'test',
    })
    assert response.status_code == 422
    assert not root.exists()


def test_explicit_current_workspace_keeps_existing_research_actions(tmp_path, monkeypatch):
    from services.research_runner import main
    monkeypatch.setattr(main, '_STATE_ROOT', tmp_path)
    monkeypatch.setattr(main._queue, 'submit', lambda *args, **kwargs: 'test-run')
    response = TestClient(main.app).post('/factory', json={
        'workspace': 'current', 'action': 'create', 'attempt': 1,
    }, headers={'X-Runner-Token': main._RUNNER_TOKEN, 'X-Operator': 'test'})
    assert response.status_code == 200


@pytest.mark.parametrize('damage', [None, 'missing', 'invalid'])
def test_registered_bar_study_is_visible_before_factory_database(workspaces, damage):
    from tests.research.test_gold_bar_program_status import fixture
    client, _, archive, config = workspaces
    study = archive.parent / 'bar-study'
    directory = fixture(study)
    if damage == 'missing':
        (directory / 'plan.json').unlink()
    elif damage == 'invalid':
        (directory / 'plan.json').write_text('{}')
    registered = json.loads(config.read_text())
    registered['workspaces'][0]['root'] = str(study)
    config.write_text(json.dumps(registered))
    before = {str(p.relative_to(study)): p.read_bytes() for p in study.rglob('*') if p.is_file()}
    response = client.get('/api/v1/console/ai-factory-lifecycle?workspace=frozen')
    if damage:
        assert response.status_code == 503
    else:
        assert response.status_code == 200
        data = response.json()
        assert data['workspace']['writable'] is False
        assert data['scheduler_enabled'] is False
        row = data['gold_bar_programs']['items'][0]
        assert row['recorded_stage'] == 'COLLECTING' and row['liveness'] == 'UNKNOWN'
        assert row['submission_enabled'] is False
    assert {str(p.relative_to(study)): p.read_bytes() for p in study.rglob('*') if p.is_file()} == before
    assert not (study / 'data/cache/ai_factory/factory.db').exists()
