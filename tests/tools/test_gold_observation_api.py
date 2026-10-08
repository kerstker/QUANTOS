from fastapi.testclient import TestClient
from tools.gold_observation_api import app


def test_projection_service_has_only_saved_read_surface(tmp_path,monkeypatch):
    monkeypatch.setenv('QUANTOS_GOLD_OBSERVATION_ROOT',str(tmp_path))
    with TestClient(app) as client:
        response=client.get('/api/v1/console/gold-observation')
        assert response.status_code==200
        data=response.json()
        assert data['monitor_status']=='NOT_CONNECTED'
        assert data['submission_enabled'] is False
        assert client.post('/api/v1/console/gold-observation').status_code==405
        assert client.get('/docs').status_code==404
        assert client.post('/run').status_code==404
