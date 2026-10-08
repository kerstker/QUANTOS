from tools import ai_factory_daemon as module


def test_resident_without_active_models_does_not_invent_observations(tmp_path):
    r=module.tick(tmp_path,now_ms=1700000000000)
    assert r['attempts']==[] and r['checkpoint']['status']=='NO_ACTIVE_MODELS'
    assert r['authoritative'] is False


def test_resident_retains_model_errors(monkeypatch,tmp_path):
    monkeypatch.setattr(module,'advance_factory',lambda *a,**k:[{'attempt':1,'status':'ERROR'}])
    r=module.tick(tmp_path,now_ms=1700000000000)
    assert r['status']=='ERROR' and r['checkpoint']['status']=='RETRY_REQUIRED'
