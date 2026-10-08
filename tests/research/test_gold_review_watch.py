from services.research.gold_review_watch import observation
from tests.research.test_gold_cost_receipt import configured, NOW


def test_changed_receipt_registry_and_expiry_change_observation(tmp_path,monkeypatch):
    dest,registry=configured(tmp_path,monkeypatch)
    first=observation(dest,registry,now=NOW)
    assert observation(dest,registry,now=NOW+1000)==first
    assert observation(dest,registry,now=NOW+86400001)!=first
    original=registry.read_bytes();registry.write_bytes(b'{}')
    assert observation(dest,registry,now=NOW)!=first
    registry.write_bytes(original)
    (dest/'independent-envelope.json').unlink()
    assert observation(dest,registry,now=NOW)!=first


def test_scheduler_rechecks_late_receipt_and_expiry_without_research_replay(tmp_path,monkeypatch):
    import shutil
    from types import SimpleNamespace
    from services.research import gold_review_watch as watch
    from services.research.ai_factory_store import FactoryStore
    from services.research.run_registry.registry import RunRegistry
    from services.research.run_registry.store import ResearchRunStore
    from services.research_runner.runner import JobQueue
    from services.research_runner.gold_review import review_tick
    from services.console.api import gold_factory_results as view
    from tools import gold_factory_review as worker
    from tools.gold_cost_research import read_completed_experiment
    original,public_registry=configured(tmp_path,monkeypatch)
    base=tmp_path/'data/cache/ai_factory';dest=base/'gold/results/1/trial'
    shutil.copytree(original,dest)
    summary=read_completed_experiment(dest)
    with FactoryStore(base/'factory.db') as store:
        store.create({'attempt':1,'market':'XAUUSDT','mode':'ai','budget':1,'objective':'receipt watch',
            'input_id':1,'input_sha256':'a'*64,'remaining_candidates':list(range(6))})
        store.update(1,'COMPLETED',{**summary,'candidate_id':0})
    monkeypatch.setattr(watch,'REGISTRY',public_registry)
    clock=[NOW]
    monkeypatch.setattr(worker,'time',SimpleNamespace(time_ns=lambda:clock[0]*1000000))
    monkeypatch.setattr(view,'time',SimpleNamespace(time_ns=lambda:clock[0]*1000000))
    envelope=(dest/'independent-envelope.json').read_bytes();(dest/'independent-envelope.json').unlink()
    retained={name:(dest/name).read_bytes() for name in ('report.json','manifest.json','trials.db')}
    registry=RunRegistry(ResearchRunStore(str(tmp_path/'watch-runs.db')));queue=JobQueue(registry)
    first=review_tick(tmp_path,queue,registry,now=clock[0]);queue.join()
    assert registry.get(first['run_id']).metrics['verification_status']=='INDEPENDENT_AUTHORITY_UNCONFIGURED'
    assert review_tick(tmp_path,queue,registry,now=clock[0]+1)['status']=='NO_REVIEW_CHANGES'
    (dest/'independent-envelope.json').write_bytes(envelope)
    rows=FactoryStore.snapshot(base/'factory.db')['sessions']
    assert view.results(tmp_path,rows)[0]['latest_review']['status']=='PENDING_RECHECK'
    second=review_tick(tmp_path,queue,registry,now=clock[0]);queue.join()
    assert registry.get(second['run_id']).metrics['verification_status']=='VERIFIED_SIGNATURE_ENGINEERING_ONLY'
    assert view.results(tmp_path,rows)[0]['latest_review']['current'] is True
    clock[0]+=86400001
    assert view.results(tmp_path,rows)[0]['latest_review']['status']=='PENDING_RECHECK'
    third=review_tick(tmp_path,queue,registry,now=clock[0]);queue.join()
    assert registry.get(third['run_id']).metrics['verification_status']=='REJECTED'
    assert view.results(tmp_path,rows)[0]['latest_review']['status']=='REJECTED'
    assert all((dest/name).read_bytes()==raw for name,raw in retained.items())
