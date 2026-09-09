import json
import pytest
from services.research.ai_factory_store import FactoryStore
from tools.ai_factory_checkpoint import create_checkpoint,restore_checkpoint


def test_checkpoint_roundtrip_and_tamper_rejection(tmp_path):
    root=tmp_path/'state';base=root/'data/cache/ai_factory'
    with FactoryStore(base/'factory.db') as s:s.create({'attempt':1,'mode':'baseline','objective':'BTC','budget':3})
    report=create_checkpoint(root,tmp_path/'backup.zip')
    assert report['files']>=1 and report['credential_paths_included'] is False
    restored=tmp_path/'restore'
    restore_checkpoint(tmp_path/'backup.zip',restored)
    assert FactoryStore.snapshot(restored/'factory.db')['selected']['state']=='READY'
    with pytest.raises(ValueError):restore_checkpoint(tmp_path/'backup.zip',restored)
    import zipfile
    with zipfile.ZipFile(tmp_path/'backup.zip','a') as z:z.writestr('../escape','bad')
    with pytest.raises(ValueError):restore_checkpoint(tmp_path/'backup.zip',tmp_path/'bad')


def test_checkpoint_rejects_symlink_artifacts(tmp_path):
    root=tmp_path/'state';base=root/'data/cache/ai_factory';base.mkdir(parents=True)
    (base/'bad.json').symlink_to('/etc/hostname')
    with pytest.raises(ValueError):create_checkpoint(root,tmp_path/'backup.zip')


def test_checkpoint_restores_campaign_outbox_and_rejects_broken_binding(tmp_path):
    root=tmp_path/'state';base=root/'data/cache/ai_factory'
    config={'campaign_id':1,'objective':'BTC','mode':'baseline','budget':3,'max_cycles':2,'interval_hours':1,'source_ids':['aqr']}
    with FactoryStore(base/'factory.db') as s:
        s.create_campaign(config,operator='test',now=0);s.reserve_campaign(1,now=0)
    create_checkpoint(root,tmp_path/'campaign.zip');restore_checkpoint(tmp_path/'campaign.zip',tmp_path/'restored')
    with FactoryStore(tmp_path/'restored/factory.db') as s:
        s.verify_campaigns();assert s.reserve_campaign(1,now=1)['attempt']==1
    with FactoryStore(base/'factory.db') as s:
        s.c.execute('UPDATE campaign_rounds SET attempt=999');s.c.commit()
    with pytest.raises(ValueError):create_checkpoint(root,tmp_path/'broken.zip')


def test_checkpoint_includes_profitability_evidence_and_ledger(tmp_path):
    from services.research.trial_ledger import TrialLedger
    root=tmp_path/'state';cache=root/'data/cache'
    with FactoryStore(cache/'ai_factory/factory.db') as store:
        store.create({'attempt':1,'mode':'baseline','objective':'BTC','budget':3})
    evidence=cache/'profitability/1/p6';evidence.mkdir(parents=True)
    (evidence/'status.json').write_text('{"status":"WAITING"}')
    with TrialLedger(str(evidence/'trials.db')) as ledger:ledger.verify()
    report=create_checkpoint(root,tmp_path/'full.zip')
    restored=tmp_path/'restored'
    result=restore_checkpoint(tmp_path/'full.zip',restored)
    assert report['layout']==result['layout']=='CACHE_ROOT_V2'
    assert (restored/'profitability/1/p6/status.json').read_bytes()==(evidence/'status.json').read_bytes()
    assert FactoryStore.snapshot(restored/'ai_factory/factory.db')['selected']['state']=='READY'
    with TrialLedger(str(restored/'profitability/1/p6/trials.db'),create=False) as ledger:ledger.verify()
