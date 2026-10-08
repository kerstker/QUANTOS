"""The public collector, existing API model and full backup consume one archive."""
from copy import deepcopy
import json
import pytest
from services.research import demo_market_inputs as model,demo_observation_store as store
from tools import binance_demo_market_inputs as collector,demo_namespace_checkpoint as checkpoint
from tests.tools.test_binance_demo_market_inputs import capture
from tests.tools.test_demo_namespace_checkpoint import COMMIT,SECRET,private_fixture_files


def source(tmp_path):
    from services.research.demo_user_stream import UserStreamJournal
    root=tmp_path/'source';root.mkdir()
    with UserStreamJournal(root/'binance_demo_user_stream.db',account_ref_sha256='a'*64) as journal:
        journal.record('SESSION_START',at_ms=1800000000000,source_sha256='b'*64)
    return root


def test_public_cli_archive_retains_original_report_and_view_contract(tmp_path,monkeypatch,capsys):
    raw,_=capture();monkeypatch.setattr(collector,'collect_bounded',lambda **kw:deepcopy(raw))
    directory=tmp_path/'BTCUSDC'
    assert collector.main(['--symbol','BTCUSDC','--output-dir',str(directory),'--archive-observations'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='CAPTURED'
    assert model.load_latest(directory)==raw
    assert model.display(model.load_latest(directory))==model.display(raw)
    assert len(list((directory/'archive').glob('*.sqlite3')))==1
    from services.console.api.binance_demo_views import build_market_inputs_snapshot
    before={p.relative_to(directory):p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    assert build_market_inputs_snapshot(directory=directory,as_of_ms=raw['completed_at_ms']+1)=={'status':'AVAILABLE','report':model.display(raw)}
    assert before=={p.relative_to(directory):p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    with pytest.raises(SystemExit):
        collector.main(['--symbol','BTCUSDC','--output-dir',str(tmp_path/'other'),'--archive-observations','--protected-network'])
    assert not (tmp_path/'other').exists()


def test_archive_backups_keep_frozen_head_and_new_concurrent_records_without_rolling_back_source(tmp_path,monkeypatch):
    root=source(tmp_path);directory=root/'binance_demo_market_inputs/BTCUSDC'
    first,_=capture();second=deepcopy(first)
    # A failed later attempt is meaningful history, not a fabricated market sample.
    second.update(observed_at_ms=first['observed_at_ms']+100,completed_at_ms=first['completed_at_ms']+100,
        reads=[],status='FAILED',failure={'kind':'worker','reason':'REQUEST_FAILED'})
    model.save(directory,first,archive=True)
    original=checkpoint.snapshot_file;appended=False
    def copy(*args,**kwargs):
        nonlocal appended
        result=original(*args,**kwargs)
        if not appended:
            appended=True;model.save(directory,second,archive=True)
        return result
    monkeypatch.setattr(checkpoint,'snapshot_file',copy)
    archive=tmp_path/'capture.enc'
    result=checkpoint.create_checkpoint(root,archive,namespace='research',source_commit=COMMIT,
        passphrase=SECRET,observation_prefix=True)
    restored=tmp_path/'restored'
    checked=checkpoint.restore_checkpoint(archive,restored,passphrase=SECRET,expected_sha256=result['sha256'])
    assert model.load_latest(restored/'binance_demo_market_inputs/BTCUSDC')==first
    assert model.load_latest(directory)==second
    summary=checked['state']['market_observation_archives']
    assert summary['segments']==1 and summary['records']==1
    assert summary['quotes']=={'BTCUSDC':{'segments':1,'records':1}}
    assert len(summary['inventory_sha256'])==64
    assert not checked['source_verified'] and not checked['production_restore_performed']


def test_missing_archive_head_row_is_not_accepted_as_a_restorable_backup(tmp_path):
    root=source(tmp_path);directory=root/'binance_demo_market_inputs/BTCUSDC'
    raw,_=capture();model.save(directory,raw,archive=True)
    pointer=json.loads((directory/'latest.json').read_text());pointer['sha256']='f'*64
    (directory/'latest.json').write_text(json.dumps(pointer))
    with pytest.raises(ValueError):
        checkpoint.create_checkpoint(root,tmp_path/'capture.zip',namespace='research',source_commit=COMMIT,
            passphrase=None,capture_only=True,observation_prefix=True)
    assert not (tmp_path/'capture.zip').exists()


def test_backup_rejects_a_market_archive_filed_under_the_other_quote(tmp_path):
    root=source(tmp_path);directory=root/'binance_demo_market_inputs/BTCUSDC'
    raw,_=capture()
    raw.update(symbol='BTCUSDT',reads=[],status='FAILED',failure={'kind':'worker','reason':'REQUEST_FAILED'})
    model.save(directory,raw,archive=True)
    with pytest.raises(ValueError,match='ARCHIVE_QUOTE'):
        checkpoint.create_checkpoint(root,tmp_path/'bad.zip',namespace='research',source_commit=COMMIT,
            passphrase=None,capture_only=True,observation_prefix=True)
