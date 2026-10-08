"""Real ZIP recovery checks at the resident and pipeline publication boundaries."""
import pytest
from zipfile import BadZipFile

from services.research.ai_factory_store import FactoryStore
from tools import ai_factory_checkpoint as checkpoints
from tools import ai_factory_daemon as daemon
from tools import ai_factory_pipeline as pipeline


@pytest.fixture
def state(tmp_path,monkeypatch):
    with FactoryStore(tmp_path/'data/cache/ai_factory/factory.db') as store:
        store.create({'attempt':1,'mode':'baseline','objective':'BTC','budget':3})
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(tmp_path))
    monkeypatch.setattr(daemon,'advance_factory',lambda *a,**kw:[{'attempt':1,'status':'WAITING_WINDOW'}])
    return tmp_path


def test_resident_rejects_corrupt_daily_archive_and_preserves_latest(state):
    folder=state/'data/cache/ai_factory_checkpoints';folder.mkdir()
    archive=folder/'daily-20231114.zip';archive.write_bytes(b'interrupted archive')
    latest=state/'data/cache/ai_factory_checkpoint.zip'
    checkpoints.create_checkpoint(state,latest);before=latest.read_bytes()
    report=daemon.tick(state,now_ms=1700000000000)
    assert report['status']=='DEGRADED'
    assert report['checkpoint']['status']=='RETRY_REQUIRED'
    assert latest.read_bytes()==before and archive.read_bytes()==b'interrupted archive'


def test_resident_repairs_corrupt_download_from_verified_daily_archive(state):
    first=daemon.tick(state,now_ms=1700000000000)
    assert first['checkpoint']['status']=='VERIFIED_RESEARCH_CHECKPOINT'
    latest=state/'data/cache/ai_factory_checkpoint.zip';expected=latest.read_bytes()
    latest.write_bytes(b'interrupted download copy')
    again=daemon.tick(state,now_ms=1700000001000)
    assert again['status']=='RUNNING' and latest.read_bytes()==expected
    checkpoints.restore_checkpoint(latest,state/'restored')


def test_pipeline_rejects_corrupt_existing_archive_without_replacing_download(state):
    folder=state/'data/cache/ai_factory_checkpoints';folder.mkdir()
    (folder/'attempt-1.zip').write_bytes(b'incomplete immutable archive')
    latest=state/'data/cache/ai_factory_checkpoint.zip'
    checkpoints.create_checkpoint(state,latest);before=latest.read_bytes()
    with pytest.raises(BadZipFile):pipeline.checkpoint({'attempt':1})
    assert latest.read_bytes()==before


def test_failed_archive_sync_does_not_publish_partial_destination(state,monkeypatch):
    destination=state/'backup.zip'
    def failed_sync(fd):raise OSError('simulated disk sync failure')
    monkeypatch.setattr(checkpoints.os,'fsync',failed_sync)
    with pytest.raises(OSError,match='disk sync'):checkpoints.create_checkpoint(state,destination)
    assert not destination.exists()


def test_publication_failure_preserves_download_and_cleans_staging(state,monkeypatch):
    archive=state/'backup.zip';checkpoints.create_checkpoint(state,archive)
    latest=state/'download.zip';latest.write_bytes(b'prior copy retained')
    def fail_replace(*args):raise OSError('simulated replacement failure')
    monkeypatch.setattr(checkpoints.os,'replace',fail_replace)
    with pytest.raises(OSError,match='replacement failure'):
        checkpoints.publish_checkpoint(archive,latest)
    assert latest.read_bytes()==b'prior copy retained'
    assert not list(state.glob('.checkpoint-*'))


def test_new_archive_never_overwrites_concurrent_winner(state,monkeypatch):
    destination=state/'backup.zip';link=checkpoints.os.link
    def competing_link(source,target):
        target.write_bytes(b'concurrent archive retained')
        link(source,target)
    monkeypatch.setattr(checkpoints.os,'link',competing_link)
    with pytest.raises(FileExistsError):checkpoints.create_checkpoint(state,destination)
    assert destination.read_bytes()==b'concurrent archive retained'
    assert not list(state.glob('.checkpoint-*'))
