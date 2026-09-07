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
